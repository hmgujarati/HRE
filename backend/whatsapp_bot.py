"""WhatsApp Chatbot — inbound message handler + state machine.

Wired into server.py via:
  - `/api/webhooks/bizchat/inbound` — receives customer messages
  - `_bot_dispatch(payload)` — main entrypoint

State persists in MongoDB collection `chatbot_sessions`:
  { phone_norm, state, ctx: {customer_info, line_items, current_family_id,
                              current_variant_id, qty, ...},
    last_msg_at, expires_at }

Outbound sends use BizChatAPI's `/contact/send-message` (text) and
`/contact/send-interactive-message` (button + list).
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SESSION_TTL_MINUTES = 30
ABOUT_HRE_URL = "https://hrexporter.com/about-hr-exporter/"

# State machine values
ST_WELCOME = "welcome"
ST_ASK_NAME = "ask_name"
ST_ASK_EMAIL = "ask_email"
ST_ASK_COMPANY = "ask_company"
ST_BROWSE_FAMILY = "browse_family"
ST_PICK_VARIANT = "pick_variant"
ST_ASK_QTY = "ask_qty"
ST_ADD_MORE = "add_more"
ST_CONFIRM = "confirm"
ST_FINALIZED = "finalized"
ST_HUMAN_HANDOFF = "human_handoff"


def _now_dt():
    return datetime.now(timezone.utc)


def _norm_phone_local(p: str) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit())


# ─────────────────────── Outbound send helpers ───────────────────────

async def _bizchat_post(wa: dict, path: str, payload: dict) -> dict:
    base = (wa.get("api_base_url") or "").rstrip("/")
    vendor = wa.get("vendor_uid")
    token = wa.get("token")
    if not (base and vendor and token):
        raise RuntimeError("BizChat is not configured")
    url = f"{base}/{vendor}/contact/{path}"
    if wa.get("from_phone_number_id"):
        payload.setdefault("from_phone_number_id", wa["from_phone_number_id"])
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, params={"token": token}, json=payload)
    body: Any
    try:
        body = r.json()
    except Exception:
        body = r.text
    if r.status_code >= 400 or (isinstance(body, dict) and body.get("result") == "error"):
        logger.error(f"[bot-out] {path} failed status={r.status_code} body={body}")
        raise RuntimeError(f"BizChat send failed: {body}")
    return body if isinstance(body, dict) else {"raw": body}


async def send_text(wa: dict, phone: str, text: str) -> dict:
    return await _bizchat_post(wa, "send-message", {
        "phone_number": phone,
        "message_body": text,
    })


async def send_buttons(wa: dict, phone: str, body_text: str, buttons: List[str],
                        header_text: Optional[str] = None,
                        footer_text: Optional[str] = None) -> dict:
    """Up to 3 buttons (BizChat / Meta limit)."""
    payload: Dict[str, Any] = {
        "phone_number": phone,
        "interactive_type": "button",
        "body_text": body_text,
        "buttons": {str(i + 1): label for i, label in enumerate(buttons[:3])},
    }
    if header_text:
        payload["header_type"] = "text"
        payload["header_text"] = header_text
    if footer_text:
        payload["footer_text"] = footer_text
    return await _bizchat_post(wa, "send-interactive-message", payload)


async def send_list(wa: dict, phone: str, body_text: str, button_text: str,
                     sections: List[Dict[str, Any]],
                     header_text: Optional[str] = None,
                     footer_text: Optional[str] = None) -> dict:
    """sections = [{"title": str, "id": str, "rows": [{"id": str, "title": str, "description": str}]}]
    Combined max 10 rows across all sections."""
    sections_obj: Dict[str, Any] = {}
    for i, sec in enumerate(sections, start=1):
        rows_obj: Dict[str, Any] = {}
        for j, row in enumerate(sec.get("rows", []), start=1):
            rows_obj[f"row_{j}"] = {
                "id": row["id"],
                "row_id": row["id"],
                "title": (row.get("title") or "")[:24],  # WA hard limit
                "description": (row.get("description") or "Tap to select")[:72],
            }
        sections_obj[f"section_{i}"] = {
            "title": (sec.get("title") or "")[:24],
            "id": sec.get("id") or f"sec_{i}",
            "rows": rows_obj,
        }
    payload: Dict[str, Any] = {
        "phone_number": phone,
        "interactive_type": "list",
        "body_text": body_text,
        "list_data": {"button_text": (button_text or "Choose")[:20], "sections": sections_obj},
    }
    if header_text:
        payload["header_type"] = "text"
        payload["header_text"] = header_text[:60]
    if footer_text:
        payload["footer_text"] = footer_text[:60]
    return await _bizchat_post(wa, "send-interactive-message", payload)


# ─────────────────────── Inbound parsing ───────────────────────

def parse_inbound(payload: dict) -> Optional[Dict[str, Any]]:
    """Extract from a BizChat inbound webhook payload:
      - phone (raw + normalized)
      - text (free-form text content, OR interactive selection title)
      - selection_id (when user clicked a button/list row, the id we set on the row)
      - wamid (incoming message id, for dedup)
    Permissive — handles the common Meta payload shape and a few BizChat variants.
    """
    if not isinstance(payload, dict):
        return None
    # Walk down common wrappers but capture `from` along the way
    body = payload
    from_phone = None
    for key in ("data", "payload", "event"):
        from_phone = (body.get("from") or body.get("phone_number")
                      or body.get("contact_phone")
                      or (body.get("contact") or {}).get("phone")
                      or from_phone)
        if isinstance(body.get(key), dict):
            body = body[key]
    # Final pass on the innermost
    from_phone = (body.get("from") or body.get("phone_number")
                  or body.get("contact_phone")
                  or (body.get("contact") or {}).get("phone")
                  or from_phone)
    if not from_phone:
        return None
    # Message id
    wamid = body.get("wamid") or body.get("id") or body.get("message_id") or payload.get("wamid")
    # Text + selection
    text: Optional[str] = None
    selection_id: Optional[str] = None
    msg = body.get("message") if isinstance(body.get("message"), dict) else body
    msg_type = (msg.get("type") or msg.get("message_type") or "").lower()
    if "text" in msg and isinstance(msg["text"], dict):
        text = (msg["text"].get("body") or "").strip()
    elif msg.get("body"):
        text = str(msg["body"]).strip()
    elif msg_type == "interactive" or "interactive" in msg:
        inter = msg.get("interactive") or msg
        # Button reply
        if isinstance(inter.get("button_reply"), dict):
            selection_id = inter["button_reply"].get("id")
            text = inter["button_reply"].get("title")
        # List reply
        elif isinstance(inter.get("list_reply"), dict):
            selection_id = inter["list_reply"].get("id")
            text = inter["list_reply"].get("title")
    elif msg_type == "button":
        # "type": "button" (Meta legacy) — payload is in message.button.payload
        btn = msg.get("button") or {}
        text = btn.get("text")
        selection_id = btn.get("payload")
    return {
        "phone": str(from_phone),
        "phone_norm": _norm_phone_local(str(from_phone)),
        "text": (text or "").strip(),
        "selection_id": (selection_id or "").strip(),
        "wamid": wamid,
    }


# ─────────────────────── Session helpers ───────────────────────

async def _find_contact_by_phone(db, phone_norm: str) -> Optional[dict]:
    """Tolerant contact lookup — tries phone_norm directly, then with/without
    the '91' country-code prefix (Indian numbers are stored either way)."""
    candidates = {phone_norm}
    if phone_norm.startswith("91") and len(phone_norm) == 12:
        candidates.add(phone_norm[2:])
    elif len(phone_norm) == 10:
        candidates.add("91" + phone_norm)
    return await db.contacts.find_one({"phone_norm": {"$in": list(candidates)}}, {"_id": 0})


async def _load_session(db, phone_norm: str) -> Optional[dict]:
    s = await db.chatbot_sessions.find_one({"phone_norm": phone_norm}, {"_id": 0})
    if not s:
        return None
    last = s.get("last_msg_at")
    if last:
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if _now_dt() - dt > timedelta(minutes=SESSION_TTL_MINUTES):
                return None
        except Exception:
            pass
    return s



    s = await db.chatbot_sessions.find_one({"phone_norm": phone_norm}, {"_id": 0})
    if not s:
        return None
    # Expire if older than TTL
    last = s.get("last_msg_at")
    if last:
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if _now_dt() - dt > timedelta(minutes=SESSION_TTL_MINUTES):
                return None
        except Exception:
            pass
    return s


async def _save_session(db, phone_norm: str, state: str, ctx: dict, transcript_entry: Optional[dict] = None):
    update = {
        "$set": {
            "phone_norm": phone_norm,
            "state": state,
            "ctx": ctx,
            "last_msg_at": _now_dt().isoformat(),
        },
        "$setOnInsert": {"started_at": _now_dt().isoformat()},
    }
    if transcript_entry:
        update["$push"] = {"transcript": transcript_entry}
    await db.chatbot_sessions.update_one({"phone_norm": phone_norm}, update, upsert=True)


# ─────────────────────── State handlers ───────────────────────

MAIN_MENU_BUTTONS = ["Get a Quote", "Talk to Sales", "About HRE"]
TRIGGER_HANDOFF = {"talk to sales", "talk to human", "talk to agent", "human", "agent",
                    "complaint", "refund", "problem", "urgent", "sales"}


async def _safe_send(coro, phone: str, label: str) -> bool:
    """Run an outbound send; log + swallow BizChat errors so the state machine
    keeps progressing. Returns True on success, False on failure."""
    try:
        await coro
        return True
    except Exception as e:
        logger.warning(f"[bot-out] {label} to {phone} failed: {e}")
        return False


async def _send_main_menu(wa: dict, phone: str):
    await _safe_send(send_buttons(
        wa, phone,
        body_text="Hi! I'm the HRE Quotation Assistant 🛠\n\nWhat would you like to do?",
        buttons=MAIN_MENU_BUTTONS,
        header_text="HRE Exporter",
        footer_text="ISO 9001:2015 Certified",
    ), phone, "main_menu")


async def _hand_off(wa: dict, phone: str, admin_phone: str):
    msg = (
        "Sure — connecting you to our sales team.\n\n"
        f"Please call or WhatsApp our admin directly: *{admin_phone}*\n\n"
        "We typically respond within business hours (Mon–Sat, 10am–7pm IST)."
    )
    await _safe_send(send_text(wa, phone, msg), phone, "handoff")


def _matches_handoff(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(k in t for k in TRIGGER_HANDOFF)


async def _send_family_list(wa: dict, db, phone: str):
    families = await db.product_families.find({}, {"_id": 0, "id": 1, "name": 1, "material_id": 1}).limit(10).to_list(10)
    if not families:
        await _safe_send(send_text(wa, phone, "Our catalog is being updated. Please contact our sales team for a quote."), phone, "no_families")
        return False
    rows = [{"id": f"fam:{f['id']}", "title": (f.get("name") or "Family")[:24], "description": ""} for f in families]
    await _safe_send(send_list(
        wa, phone,
        body_text="Please select the product family you're looking for:",
        button_text="View Products",
        sections=[{"title": "Product Families", "id": "families", "rows": rows}],
        header_text="HRE Catalog",
    ), phone, "family_list")
    return True


async def _send_variant_list(wa: dict, db, phone: str, family_id: str):
    variants = await db.product_variants.find({"family_id": family_id}, {"_id": 0, "id": 1, "name": 1, "code": 1, "price": 1}).limit(10).to_list(10)
    if not variants:
        await _safe_send(send_text(wa, phone, "No variants available for this family. Try a different one — type 'menu' to start over."), phone, "no_variants")
        return False
    rows = []
    for v in variants:
        title = (v.get("name") or v.get("code") or "Variant")[:24]
        price = float(v.get("price") or 0)
        desc = f"₹{price:,.0f}/unit · code {v.get('code', '')}"[:72]
        rows.append({"id": f"var:{v['id']}", "title": title, "description": desc})
    await _safe_send(send_list(
        wa, phone,
        body_text="Pick the variant you'd like to quote:",
        button_text="Pick Variant",
        sections=[{"title": "Available Variants", "id": "variants", "rows": rows}],
        header_text="Variants",
    ), phone, "variant_list")
    return True


async def _finalize_and_create_quote(db, wa: dict, sm: dict, sess: dict, settings: dict, builder_fn):
    """Calls builder_fn(line_items, contact) → returns the saved quote dict.
    builder_fn is provided by server.py to keep this file decoupled from the
    quote/PDF/dispatch internals."""
    ctx = sess.get("ctx") or {}
    line_items = ctx.get("line_items") or []
    customer = ctx.get("customer") or {}
    return await builder_fn(line_items=line_items, customer=customer, source="whatsapp_bot")


# ─────────────────────── Main dispatcher ───────────────────────

async def dispatch(*, db, wa: dict, sm: dict, settings_doc: dict, msg: Dict[str, Any], builder_fn) -> Dict[str, Any]:
    """Process a single inbound message. `builder_fn` is an async function from
    server.py that creates the quotation + PDF + dispatch when called with
    (line_items=[{variant_id, qty}], customer={name, email, company, phone},
    source=str). Returns dict with `state`, `customer_phone`, `actions_taken`."""
    phone = msg["phone"]
    phone_norm = msg["phone_norm"]
    text = msg.get("text") or ""
    sel = msg.get("selection_id") or ""
    text_lower = text.strip().lower()

    actions: List[str] = []

    # Global handoff trigger — works in any state
    admin_phone = (wa.get("admin_notify_phone") or "").strip() or None
    if _matches_handoff(text_lower):
        if admin_phone:
            await _hand_off(wa, phone, admin_phone)
            actions.append("handoff")
        else:
            await _safe_send(send_text(wa, phone, "Our team will reach out shortly. Thanks for your patience!"), phone, "step_1")
        # Mark session as handed-off
        await _save_session(db, phone_norm, ST_HUMAN_HANDOFF,
                            {"reason": "keyword_trigger"},
                            {"in": text, "at": _now_dt().isoformat(), "out": "handoff"})
        return {"state": ST_HUMAN_HANDOFF, "customer_phone": phone, "actions_taken": actions}

    # "menu" / "restart" → reset
    if text_lower in {"menu", "start", "hi", "hello", "hey", "restart"}:
        await _send_main_menu(wa, phone)
        await _save_session(db, phone_norm, ST_WELCOME, {},
                            {"in": text, "at": _now_dt().isoformat(), "out": "main_menu"})
        actions.append("welcome_menu")
        return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}

    # Load existing session
    sess = await _load_session(db, phone_norm)
    state = (sess or {}).get("state") or ST_WELCOME
    ctx: Dict[str, Any] = (sess or {}).get("ctx") or {}

    # First-time / expired → main menu
    if state == ST_WELCOME and not sess:
        await _send_main_menu(wa, phone)
        await _save_session(db, phone_norm, ST_WELCOME, {},
                            {"in": text, "at": _now_dt().isoformat(), "out": "main_menu"})
        actions.append("welcome_menu")
        return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}

    # State: WELCOME — expecting button reply or text answer
    if state == ST_WELCOME:
        # Button replies will set sel + text="Get a Quote" etc
        choice = text_lower
        if "quote" in choice or sel == "1":
            # Look up contact (tolerant to phone country-code differences)
            contact = await _find_contact_by_phone(db, phone_norm)
            if contact:
                ctx["customer"] = {"name": contact.get("name"), "email": contact.get("email"),
                                   "company": contact.get("company"), "phone": phone, "contact_id": contact.get("id")}
                ctx["line_items"] = []
                await _safe_send(send_text(wa, phone, f"Welcome back, {contact['name']}! Let's build your quote."), phone, "step_2")
                if await _send_family_list(wa, db, phone):
                    await _save_session(db, phone_norm, ST_BROWSE_FAMILY, ctx,
                                        {"in": text, "at": _now_dt().isoformat(), "out": "family_list"})
                    actions.append("family_list_known_customer")
                    return {"state": ST_BROWSE_FAMILY, "customer_phone": phone, "actions_taken": actions}
            else:
                ctx["customer"] = {"phone": phone}
                ctx["line_items"] = []
                await _safe_send(send_text(wa, phone, "Great! Let me set up a quote for you. What's your full name?"), phone, "step_3")
                await _save_session(db, phone_norm, ST_ASK_NAME, ctx,
                                    {"in": text, "at": _now_dt().isoformat(), "out": "ask_name"})
                actions.append("ask_name_new_customer")
                return {"state": ST_ASK_NAME, "customer_phone": phone, "actions_taken": actions}
        elif "sales" in choice or sel == "2":
            if admin_phone:
                await _hand_off(wa, phone, admin_phone)
                actions.append("handoff")
            await _save_session(db, phone_norm, ST_HUMAN_HANDOFF, {},
                                {"in": text, "at": _now_dt().isoformat(), "out": "handoff"})
            return {"state": ST_HUMAN_HANDOFF, "customer_phone": phone, "actions_taken": actions}
        elif "about" in choice or sel == "3":
            await _safe_send(send_text(wa, phone,
                            f"Learn more about HRE Exporter here:\n\n{ABOUT_HRE_URL}\n\n"
                            "Type 'menu' anytime to start over."), phone, "step_4")
            await _save_session(db, phone_norm, ST_WELCOME, {},
                                {"in": text, "at": _now_dt().isoformat(), "out": "about_hre"})
            actions.append("about_hre")
            return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}
        else:
            # Unknown reply — re-show menu
            await _send_main_menu(wa, phone)
            actions.append("menu_repeat")
            return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}

    # State: ASK_NAME → ASK_EMAIL → ASK_COMPANY → BROWSE_FAMILY
    if state == ST_ASK_NAME:
        ctx.setdefault("customer", {})["name"] = text.strip().title()
        await _safe_send(send_text(wa, phone, "Thanks! Your email address?"), phone, "step_5")
        await _save_session(db, phone_norm, ST_ASK_EMAIL, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "ask_email"})
        return {"state": ST_ASK_EMAIL, "customer_phone": phone, "actions_taken": ["captured_name"]}

    if state == ST_ASK_EMAIL:
        if "@" not in text or "." not in text:
            await _safe_send(send_text(wa, phone, "That doesn't look like an email. Please type your email (e.g. you@company.com)."), phone, "step_6")
            return {"state": ST_ASK_EMAIL, "customer_phone": phone, "actions_taken": ["bad_email"]}
        ctx["customer"]["email"] = text.strip()
        await _safe_send(send_text(wa, phone, "And your company name?"), phone, "step_7")
        await _save_session(db, phone_norm, ST_ASK_COMPANY, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "ask_company"})
        return {"state": ST_ASK_COMPANY, "customer_phone": phone, "actions_taken": ["captured_email"]}

    if state == ST_ASK_COMPANY:
        ctx["customer"]["company"] = text.strip()
        # Persist new contact
        await db.contacts.insert_one({
            "id": __import__("uuid").uuid4().hex,
            "name": ctx["customer"]["name"],
            "email": ctx["customer"]["email"],
            "company": ctx["customer"]["company"],
            "phone": phone,
            "phone_norm": phone_norm,
            "created_at": _now_dt().isoformat(),
            "source": "whatsapp_bot",
        })
        await _safe_send(send_text(wa, phone, f"Got it, {ctx['customer']['name']}. Let's pick the products you need."), phone, "step_8")
        if await _send_family_list(wa, db, phone):
            await _save_session(db, phone_norm, ST_BROWSE_FAMILY, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "family_list"})
            return {"state": ST_BROWSE_FAMILY, "customer_phone": phone, "actions_taken": ["captured_company", "family_list"]}
        return {"state": ST_BROWSE_FAMILY, "customer_phone": phone, "actions_taken": ["captured_company"]}

    # State: BROWSE_FAMILY — expects sel="fam:<id>"
    if state == ST_BROWSE_FAMILY:
        if sel.startswith("fam:"):
            family_id = sel[4:]
            ctx["current_family_id"] = family_id
            if await _send_variant_list(wa, db, phone, family_id):
                await _save_session(db, phone_norm, ST_PICK_VARIANT, ctx,
                                    {"in": text, "at": _now_dt().isoformat(), "out": "variant_list"})
                return {"state": ST_PICK_VARIANT, "customer_phone": phone, "actions_taken": ["picked_family"]}
            return {"state": ST_BROWSE_FAMILY, "customer_phone": phone, "actions_taken": ["empty_family"]}
        else:
            await _safe_send(send_text(wa, phone, "Please pick a family from the list above. (Or type 'menu' to start over.)"), phone, "step_9")
            return {"state": ST_BROWSE_FAMILY, "customer_phone": phone, "actions_taken": ["expecting_family"]}

    # State: PICK_VARIANT — expects sel="var:<id>"
    if state == ST_PICK_VARIANT:
        if sel.startswith("var:"):
            variant_id = sel[4:]
            ctx["current_variant_id"] = variant_id
            v = await db.product_variants.find_one({"id": variant_id}, {"_id": 0, "name": 1, "price": 1})
            ctx["current_variant_name"] = v.get("name") if v else "variant"
            ctx["current_variant_price"] = float(v.get("price") or 0) if v else 0
            await _safe_send(send_text(wa, phone,
                            f"How many units of *{ctx['current_variant_name']}* "
                            f"(₹{ctx['current_variant_price']:,.0f}/unit) do you need?\n\n"
                            "Reply with just the number, e.g. 100"), phone, "step_10")
            await _save_session(db, phone_norm, ST_ASK_QTY, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "ask_qty"})
            return {"state": ST_ASK_QTY, "customer_phone": phone, "actions_taken": ["picked_variant"]}
        else:
            await _safe_send(send_text(wa, phone, "Please pick a variant from the list above. (Or type 'menu' to start over.)"), phone, "step_11")
            return {"state": ST_PICK_VARIANT, "customer_phone": phone, "actions_taken": ["expecting_variant"]}

    # State: ASK_QTY — expect a number
    if state == ST_ASK_QTY:
        try:
            qty = int("".join(ch for ch in text if ch.isdigit()))
            if qty <= 0: raise ValueError
        except Exception:
            await _safe_send(send_text(wa, phone, "Please reply with a valid quantity (e.g. 100)."), phone, "step_12")
            return {"state": ST_ASK_QTY, "customer_phone": phone, "actions_taken": ["bad_qty"]}
        ctx.setdefault("line_items", []).append({
            "variant_id": ctx["current_variant_id"],
            "variant_name": ctx["current_variant_name"],
            "unit_price": ctx["current_variant_price"],
            "qty": qty,
        })
        # Ask add-more or finish
        await _safe_send(send_buttons(
            wa, phone,
            body_text=f"Added: *{qty} × {ctx['current_variant_name']}* (₹{qty * ctx['current_variant_price']:,.0f})\n\nAdd another item?",
            buttons=["Add another", "Finish quote", "Cancel"],
        ), phone, "step_13")
        await _save_session(db, phone_norm, ST_ADD_MORE, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "add_more"})
        return {"state": ST_ADD_MORE, "customer_phone": phone, "actions_taken": ["added_line_item"]}

    # State: ADD_MORE — Add/Finish/Cancel
    if state == ST_ADD_MORE:
        choice = text_lower
        if "add" in choice or sel == "1":
            ctx.pop("current_family_id", None)
            ctx.pop("current_variant_id", None)
            if await _send_family_list(wa, db, phone):
                await _save_session(db, phone_norm, ST_BROWSE_FAMILY, ctx,
                                    {"in": text, "at": _now_dt().isoformat(), "out": "family_list"})
            return {"state": ST_BROWSE_FAMILY, "customer_phone": phone, "actions_taken": ["add_more"]}
        elif "cancel" in choice or sel == "3":
            await _safe_send(send_text(wa, phone, "Quote cancelled. Type 'menu' anytime to start over."), phone, "step_14")
            await db.chatbot_sessions.delete_one({"phone_norm": phone_norm})
            return {"state": "cancelled", "customer_phone": phone, "actions_taken": ["cancelled"]}
        elif "finish" in choice or sel == "2":
            # Finalize quote
            try:
                quote = await builder_fn(
                    line_items=ctx.get("line_items") or [],
                    customer=ctx.get("customer") or {},
                    source="whatsapp_bot",
                )
                qno = quote.get("quote_number")
                grand = float(quote.get("grand_total") or 0)
                await send_text(
                    wa, phone,
                    f"✅ Done!\n\nQuotation *{qno}* generated.\nTotal: *₹{grand:,.2f}*\n\n"
                    "PDF is on its way to your WhatsApp + email. You can also view all your quotes anytime at hrexporter.com/my-quotes.\n\n"
                    "Type 'menu' to start a new request."
                )
                await _save_session(db, phone_norm, ST_FINALIZED,
                                    {"quote_id": quote.get("id"), "quote_number": qno},
                                    {"in": text, "at": _now_dt().isoformat(), "out": f"quote_finalized:{qno}"})
                return {"state": ST_FINALIZED, "customer_phone": phone, "quote_id": quote.get("id"), "actions_taken": ["finalized"]}
            except Exception as e:
                logger.exception("[bot] failed to finalize quote")
                await _safe_send(send_text(wa, phone,
                                "Sorry, I hit a snag generating your quote. Our team has been notified and will reach out shortly."), phone, "step_15")
                return {"state": ST_ADD_MORE, "customer_phone": phone, "actions_taken": ["finalize_failed"], "error": str(e)}
        else:
            await _safe_send(send_buttons(
                wa, phone,
                body_text="Please pick one:",
                buttons=["Add another", "Finish quote", "Cancel"],
            ), phone, "step_16")
            return {"state": ST_ADD_MORE, "customer_phone": phone, "actions_taken": ["expecting_addmore"]}

    if state == ST_HUMAN_HANDOFF or state == ST_FINALIZED:
        # User typed something after handoff/finalize — silently restart menu
        await _send_main_menu(wa, phone)
        await _save_session(db, phone_norm, ST_WELCOME, {},
                            {"in": text, "at": _now_dt().isoformat(), "out": "main_menu"})
        return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": ["restart_after_terminal"]}

    # Fallback
    await _send_main_menu(wa, phone)
    return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": ["fallback"]}
