"""WhatsApp Chatbot — inbound message handler + state machine.

Flow:
  WELCOME → ASK_NAME → ASK_EMAIL → ASK_COMPANY → PICK_MATERIAL → PICK_FAMILY
        → ASK_CABLE → ASK_HOLE → PICK_VARIANT → ASK_QTY → AFTER_ITEM
        (Add another → loop back to PICK_MATERIAL)
        (Review cart → REVIEW_CART → confirm → FINALIZED)

Wired into server.py via:
  - `/api/webhooks/bizchat/inbound` and `/api/webhooks/bizchat/status` (consolidated)
  - `_bot_finalize_quote(line_items, customer)` builds the Quotation, auto-converts
    to an Order, generates the Proforma Invoice PDF, and ships it on WA + Email.

Persists state to MongoDB collection `chatbot_sessions`:
  { phone_norm, state, ctx: { customer, line_items[], current_*}, last_msg_at }
"""

from __future__ import annotations
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

SESSION_TTL_MINUTES = 30
ABOUT_HRE_URL = "https://hrexporter.com/about-hr-exporter/"

# State machine values
ST_WELCOME = "welcome"
ST_ASK_NAME = "ask_name"
ST_ASK_EMAIL = "ask_email"
ST_ASK_COMPANY = "ask_company"
ST_PICK_MATERIAL = "pick_material"
ST_PICK_FAMILY = "pick_family"
ST_ASK_CABLE = "ask_cable"
ST_ASK_HOLE = "ask_hole"
ST_PICK_VARIANT = "pick_variant"
ST_ASK_QTY = "ask_qty"
ST_AFTER_ITEM = "after_item"
ST_REVIEW_CART = "review_cart"
ST_FINALIZED = "finalized"
ST_HUMAN_HANDOFF = "human_handoff"


def _now_dt():
    return datetime.now(timezone.utc)


def _norm_phone_local(p: str) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit())


# ─────────────────────── Numeric helpers ───────────────────────

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_size_range(s: Any) -> Optional[Tuple[float, float]]:
    """Parse a size string like '4-6 mm²', '1.5', '5 mm' → (min, max)."""
    if s is None:
        return None
    nums = _NUM_RE.findall(str(s))
    if not nums:
        return None
    arr = [float(n) for n in nums if n.replace(".", "").isdigit()]
    if not arr:
        return None
    return (min(arr), max(arr))


def range_distance(target: float, rng: Optional[Tuple[float, float]]) -> float:
    if rng is None:
        return float("inf")
    lo, hi = rng
    if lo <= target <= hi:
        return 0.0
    return min(abs(target - lo), abs(target - hi))


def parse_first_number(text: str) -> Optional[float]:
    """Extract the first number from text. Returns None if no number found."""
    if not text:
        return None
    m = _NUM_RE.search(str(text))
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


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


async def _safe_send(coro, phone: str, label: str) -> bool:
    try:
        await coro
        return True
    except Exception as e:
        logger.warning(f"[bot-out] {label} to {phone} failed: {e}")
        return False


# ─────────────────────── Inbound parsing ───────────────────────

def parse_inbound(payload: dict) -> Optional[Dict[str, Any]]:
    """Extract phone/text/selection_id/wamid from a BizChat inbound webhook payload."""
    if not isinstance(payload, dict):
        return None

    contact_obj = payload.get("contact")
    msg_obj = payload.get("message")
    if isinstance(contact_obj, dict) and isinstance(msg_obj, dict):
        from_phone = contact_obj.get("phone_number") or contact_obj.get("phone")
        if from_phone:
            wamid = msg_obj.get("whatsapp_message_id") or msg_obj.get("wamid") or msg_obj.get("id")
            text = (msg_obj.get("body") or "").strip()
            selection_id = ""
            inter = msg_obj.get("interactive")
            if isinstance(inter, dict):
                if isinstance(inter.get("button_reply"), dict):
                    selection_id = inter["button_reply"].get("id") or ""
                    text = inter["button_reply"].get("title") or text
                elif isinstance(inter.get("list_reply"), dict):
                    selection_id = inter["list_reply"].get("id") or ""
                    text = inter["list_reply"].get("title") or text
            return {
                "phone": str(from_phone),
                "phone_norm": _norm_phone_local(str(from_phone)),
                "text": (text or "").strip(),
                "selection_id": (selection_id or "").strip(),
                "wamid": wamid,
            }

    # Generic Meta-style fallback
    body = payload
    from_phone = None
    for key in ("data", "payload", "event"):
        from_phone = (body.get("from") or body.get("phone_number")
                      or body.get("contact_phone")
                      or (body.get("contact") or {}).get("phone")
                      or from_phone)
        if isinstance(body.get(key), dict):
            body = body[key]
    from_phone = (body.get("from") or body.get("phone_number")
                  or body.get("contact_phone")
                  or (body.get("contact") or {}).get("phone")
                  or from_phone)
    if not from_phone:
        return None
    wamid = body.get("wamid") or body.get("id") or body.get("message_id") or payload.get("wamid")
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
        if isinstance(inter.get("button_reply"), dict):
            selection_id = inter["button_reply"].get("id")
            text = inter["button_reply"].get("title")
        elif isinstance(inter.get("list_reply"), dict):
            selection_id = inter["list_reply"].get("id")
            text = inter["list_reply"].get("title")
    elif msg_type == "button":
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
    """Tolerant phone match — handles 91-prefix variations."""
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


# ─────────────────────── Catalogue helpers ───────────────────────

MAIN_MENU_BUTTONS = ["Get a Quote", "Talk to Sales", "About HRE"]
TRIGGER_HANDOFF = {"talk to sales", "talk to human", "talk to agent", "human", "agent",
                    "complaint", "refund", "problem", "urgent", "sales"}


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


async def _send_material_buttons(wa: dict, db, phone: str):
    """Fetches active materials and shows them as buttons (max 3)."""
    mats = await db.materials.find({"active": True}, {"_id": 0, "id": 1, "material_name": 1}).to_list(10)
    if not mats:
        await _safe_send(send_text(wa, phone, "Our catalog is being updated. Please contact our sales team for a quote."), phone, "no_materials")
        return False, []
    # Buttons pattern: BizChat sends back `id` of the button (which matches our index '1','2','3'),
    # so we'll resolve the choice from session ctx (we'll save mapping there).
    labels = [m["material_name"] for m in mats[:3]]
    await _safe_send(send_buttons(
        wa, phone,
        body_text="Which material are you looking for?",
        buttons=labels,
        header_text="Step 1 of 3",
    ), phone, "pick_material")
    return True, mats[:3]


async def _send_family_list(wa: dict, db, phone: str, material_id: str):
    families = await db.product_families.find(
        {"active": True, "material_id": material_id},
        {"_id": 0, "id": 1, "family_name": 1, "short_name": 1, "product_type": 1},
    ).sort("family_name", 1).limit(10).to_list(10)
    if not families:
        await _safe_send(send_text(wa, phone,
            "No product families found for this material. Type 'menu' to start over or 'sales' to talk to our team."),
            phone, "no_families")
        return False
    rows = []
    for f in families:
        title = (f.get("short_name") or f.get("family_name") or "Family").strip()
        desc = (f.get("product_type") or f.get("family_name") or "").strip()
        rows.append({"id": f"fam:{f['id']}", "title": title[:24], "description": desc[:72] or "Tap to select"})
    await _safe_send(send_list(
        wa, phone,
        body_text="Pick the product family you need:",
        button_text="View Families",
        sections=[{"title": "Product Families", "id": "families", "rows": rows}],
        header_text="Step 2 of 3",
    ), phone, "family_list")
    return True


async def _send_variant_matches(wa: dict, db, phone: str, family_id: str,
                                 cable_target: float, hole_target: Optional[float]):
    """Fetch all active variants in family, score by numeric distance, send top 5 as a list."""
    variants = await db.product_variants.find(
        {"product_family_id": family_id, "active": True},
        {"_id": 0, "id": 1, "product_code": 1, "product_name": 1, "final_price": 1,
         "cable_size": 1, "hole_size": 1, "size": 1},
    ).to_list(2000)
    if not variants:
        await _safe_send(send_text(wa, phone, "No variants available for this family. Type 'menu' to start over."),
                         phone, "no_variants")
        return False, []
    scored = []
    for v in variants:
        cable_rng = parse_size_range(v.get("cable_size"))
        hole_rng = parse_size_range(v.get("hole_size"))
        sc = range_distance(cable_target, cable_rng)
        if hole_target is not None:
            sc = (sc + range_distance(hole_target, hole_rng)) / 2
        scored.append((sc, v))
    scored.sort(key=lambda t: t[0])
    top = [v for _, v in scored[:5]]
    rows = []
    for v in top:
        title = (v.get("product_code") or v.get("product_name") or "Variant").strip()
        price = float(v.get("final_price") or 0)
        cs = (v.get("cable_size") or "").strip()
        hs = (v.get("hole_size") or "").strip()
        bits = [b for b in [cs, (f"⌀{hs}" if hs else "")] if b]
        size_label = " · ".join(bits)
        if price > 0:
            desc = (f"₹{price:,.2f}/unit" + (f" · {size_label}" if size_label else "")).strip()
        else:
            desc = size_label or "Tap to select"
        rows.append({"id": f"var:{v['id']}", "title": title[:24], "description": desc[:72]})
    await _safe_send(send_list(
        wa, phone,
        body_text=f"Top {len(top)} closest matches for cable={cable_target}"
                  + (f", hole={hole_target}" if hole_target is not None else "")
                  + ". Pick one:",
        button_text="Pick Variant",
        sections=[{"title": "Closest matches", "id": "matches", "rows": rows}],
        header_text="Step 3 of 3",
    ), phone, "variant_matches")
    return True, top


def _cart_summary_text(line_items: List[dict]) -> str:
    if not line_items:
        return "Your cart is empty."
    lines = []
    total = 0.0
    for i, li in enumerate(line_items, 1):
        lt = float(li.get("unit_price", 0)) * int(li.get("qty", 0))
        total += lt
        nm = li.get("variant_name") or li.get("variant_code") or "Item"
        lines.append(f"{i}. {nm} × {li['qty']} = ₹{lt:,.2f}")
    lines.append(f"\n*Total: ₹{total:,.2f}*  _(GST extra)_")
    return "\n".join(lines)


# ─────────────────────── Main dispatcher ───────────────────────

async def dispatch(*, db, wa: dict, sm: dict, settings_doc: dict, msg: Dict[str, Any], builder_fn) -> Dict[str, Any]:
    """Process a single inbound message. `builder_fn` is provided by server.py and
    creates the quotation + order + proforma when called with
    (line_items=[{variant_id, variant_code, variant_name, unit_price, qty}],
     customer={name, email, company, phone}, source=str).
    Returns dict with `state`, `customer_phone`, `actions_taken`."""
    phone = msg["phone"]
    phone_norm = msg["phone_norm"]
    text = msg.get("text") or ""
    sel = msg.get("selection_id") or ""
    text_lower = text.strip().lower()

    actions: List[str] = []
    admin_phone = (wa.get("admin_notify_phone") or "").strip() or None

    # Global handoff trigger
    if _matches_handoff(text_lower):
        if admin_phone:
            await _hand_off(wa, phone, admin_phone)
            actions.append("handoff")
        else:
            await _safe_send(send_text(wa, phone, "Our team will reach out shortly. Thanks for your patience!"),
                             phone, "handoff_no_admin")
        await _save_session(db, phone_norm, ST_HUMAN_HANDOFF,
                            {"reason": "keyword_trigger"},
                            {"in": text, "at": _now_dt().isoformat(), "out": "handoff"})
        return {"state": ST_HUMAN_HANDOFF, "customer_phone": phone, "actions_taken": actions}

    # "menu"/"restart" → reset
    if text_lower in {"menu", "start", "hi", "hello", "hey", "restart"}:
        await _send_main_menu(wa, phone)
        await _save_session(db, phone_norm, ST_WELCOME, {},
                            {"in": text, "at": _now_dt().isoformat(), "out": "main_menu"})
        actions.append("welcome_menu")
        return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}

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

    # Helper: kick off material picker (used in multiple branches)
    async def _enter_material_picker(ctx_obj):
        ok, mats = await _send_material_buttons(wa, db, phone)
        if ok:
            ctx_obj["material_choices"] = [{"id": m["id"], "name": m["material_name"]} for m in mats]
            await _save_session(db, phone_norm, ST_PICK_MATERIAL, ctx_obj,
                                {"in": text, "at": _now_dt().isoformat(), "out": "pick_material"})
            return ST_PICK_MATERIAL
        return state

    # ─── State: WELCOME — main menu reply ───
    if state == ST_WELCOME:
        choice = text_lower
        if "quote" in choice or sel == "1":
            contact = await _find_contact_by_phone(db, phone_norm)
            if contact:
                ctx["customer"] = {"name": contact.get("name"), "email": contact.get("email"),
                                   "company": contact.get("company"), "phone": phone,
                                   "contact_id": contact.get("id"),
                                   "billing_address": contact.get("billing_address") or "",
                                   "shipping_address": contact.get("shipping_address") or "",
                                   "gst_number": contact.get("gst_number") or "",
                                   "state": contact.get("state") or ""}
                ctx.setdefault("line_items", [])
                await _safe_send(send_text(wa, phone, f"Welcome back, {contact['name']}! Let's build your quote."),
                                 phone, "returning_welcome")
                new_state = await _enter_material_picker(ctx)
                actions.append("returning_customer_to_material")
                return {"state": new_state, "customer_phone": phone, "actions_taken": actions}
            else:
                ctx["customer"] = {"phone": phone}
                ctx["line_items"] = []
                await _safe_send(send_text(wa, phone, "Great! Let me set up a quote for you. What's your full name?"),
                                 phone, "ask_name")
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
                            "Type 'menu' anytime to start over."), phone, "about_hre")
            await _save_session(db, phone_norm, ST_WELCOME, {},
                                {"in": text, "at": _now_dt().isoformat(), "out": "about_hre"})
            actions.append("about_hre")
            return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}
        else:
            await _send_main_menu(wa, phone)
            actions.append("menu_repeat")
            return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": actions}

    # ─── States: ASK_NAME → ASK_EMAIL → ASK_COMPANY ───
    if state == ST_ASK_NAME:
        if not text.strip() or len(text.strip()) < 2:
            await _safe_send(send_text(wa, phone, "Please type your full name."), phone, "bad_name")
            return {"state": ST_ASK_NAME, "customer_phone": phone, "actions_taken": ["bad_name"]}
        ctx.setdefault("customer", {})["name"] = text.strip().title()
        await _safe_send(send_text(wa, phone, "Thanks! Your email address?"), phone, "ask_email")
        await _save_session(db, phone_norm, ST_ASK_EMAIL, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "ask_email"})
        return {"state": ST_ASK_EMAIL, "customer_phone": phone, "actions_taken": ["captured_name"]}

    if state == ST_ASK_EMAIL:
        if "@" not in text or "." not in text:
            await _safe_send(send_text(wa, phone, "That doesn't look like an email. Please type your email (e.g. you@company.com)."),
                             phone, "bad_email")
            return {"state": ST_ASK_EMAIL, "customer_phone": phone, "actions_taken": ["bad_email"]}
        ctx["customer"]["email"] = text.strip()
        await _safe_send(send_text(wa, phone, "And your company name?"), phone, "ask_company")
        await _save_session(db, phone_norm, ST_ASK_COMPANY, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "ask_company"})
        return {"state": ST_ASK_COMPANY, "customer_phone": phone, "actions_taken": ["captured_email"]}

    if state == ST_ASK_COMPANY:
        ctx["customer"]["company"] = text.strip() or "—"
        # Persist new contact (idempotent against repeat sessions)
        existing = await _find_contact_by_phone(db, phone_norm)
        if not existing:
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
            ctx["customer"]["contact_id"] = ctx["customer"].get("contact_id")
        await _safe_send(send_text(wa, phone, f"Got it, {ctx['customer']['name']}. Let's pick the products you need."),
                         phone, "captured_company")
        new_state = await _enter_material_picker(ctx)
        return {"state": new_state, "customer_phone": phone, "actions_taken": ["captured_company"]}

    # ─── State: PICK_MATERIAL — button reply ───
    if state == ST_PICK_MATERIAL:
        # The button reply title is the material name; selection_id is "1"/"2"/"3"
        chosen = None
        choices = ctx.get("material_choices") or []
        # Match by selection_id index first (button id = "1"/"2"/"3")
        if sel and sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(choices):
                chosen = choices[idx]
        # Fallback: match by name (case-insensitive)
        if not chosen:
            for m in choices:
                if m["name"].lower() == text.strip().lower():
                    chosen = m
                    break
        if not chosen:
            await _safe_send(send_text(wa, phone, "Please tap one of the buttons above (or type 'menu' to start over)."),
                             phone, "expecting_material")
            return {"state": ST_PICK_MATERIAL, "customer_phone": phone, "actions_taken": ["expecting_material"]}
        ctx["current_material_id"] = chosen["id"]
        ctx["current_material_name"] = chosen["name"]
        if await _send_family_list(wa, db, phone, chosen["id"]):
            await _save_session(db, phone_norm, ST_PICK_FAMILY, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "family_list"})
            return {"state": ST_PICK_FAMILY, "customer_phone": phone, "actions_taken": ["picked_material"]}
        return {"state": ST_PICK_MATERIAL, "customer_phone": phone, "actions_taken": ["material_no_families"]}

    # ─── State: PICK_FAMILY — list reply ───
    if state == ST_PICK_FAMILY:
        if sel.startswith("fam:"):
            family_id = sel[4:]
            fam = await db.product_families.find_one({"id": family_id}, {"_id": 0, "id": 1, "family_name": 1})
            if not fam:
                await _safe_send(send_text(wa, phone, "That family isn't available. Pick another from the list above."),
                                 phone, "family_not_found")
                return {"state": ST_PICK_FAMILY, "customer_phone": phone, "actions_taken": ["family_not_found"]}
            ctx["current_family_id"] = family_id
            ctx["current_family_name"] = fam.get("family_name") or "Family"
            await _safe_send(send_text(wa, phone,
                            f"Great — *{fam['family_name']}*.\n\nWhat *cable size* do you need? Reply with a *number only* (e.g. 4, 6, 1.5, 16)."),
                             phone, "ask_cable")
            await _save_session(db, phone_norm, ST_ASK_CABLE, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "ask_cable"})
            return {"state": ST_ASK_CABLE, "customer_phone": phone, "actions_taken": ["picked_family"]}
        else:
            await _safe_send(send_text(wa, phone, "Please tap one of the families from the list above (or type 'menu' to start over)."),
                             phone, "expecting_family")
            return {"state": ST_PICK_FAMILY, "customer_phone": phone, "actions_taken": ["expecting_family"]}

    # ─── State: ASK_CABLE — numeric only ───
    if state == ST_ASK_CABLE:
        n = parse_first_number(text)
        if n is None or n <= 0:
            await _safe_send(send_text(wa, phone,
                            "I need a *number*. Please reply with the cable size in mm² (e.g. 4 or 1.5)."),
                             phone, "bad_cable")
            return {"state": ST_ASK_CABLE, "customer_phone": phone, "actions_taken": ["bad_cable"]}
        ctx["current_cable_size"] = n
        await _safe_send(send_text(wa, phone,
                        f"Cable size: *{n} mm²*.\n\nNow the *hole size* in mm — reply with a *number only* (e.g. 6, 8, 10).\nType *skip* if you don't have a hole-size requirement."),
                         phone, "ask_hole")
        await _save_session(db, phone_norm, ST_ASK_HOLE, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "ask_hole"})
        return {"state": ST_ASK_HOLE, "customer_phone": phone, "actions_taken": ["captured_cable"]}

    # ─── State: ASK_HOLE — numeric or 'skip' ───
    if state == ST_ASK_HOLE:
        if text_lower in {"skip", "no", "none", "n/a", "na", "-"}:
            ctx["current_hole_size"] = None
        else:
            n = parse_first_number(text)
            if n is None or n <= 0:
                await _safe_send(send_text(wa, phone,
                                "I need a *number* (or type 'skip'). Please reply with the hole size in mm (e.g. 6 or 8)."),
                                 phone, "bad_hole")
                return {"state": ST_ASK_HOLE, "customer_phone": phone, "actions_taken": ["bad_hole"]}
            ctx["current_hole_size"] = n
        ok, top = await _send_variant_matches(
            wa, db, phone, ctx["current_family_id"],
            cable_target=ctx["current_cable_size"],
            hole_target=ctx.get("current_hole_size"),
        )
        if ok:
            await _save_session(db, phone_norm, ST_PICK_VARIANT, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "variant_matches"})
            return {"state": ST_PICK_VARIANT, "customer_phone": phone, "actions_taken": ["captured_hole"]}
        return {"state": ST_ASK_HOLE, "customer_phone": phone, "actions_taken": ["no_matches"]}

    # ─── State: PICK_VARIANT — list reply ───
    if state == ST_PICK_VARIANT:
        if sel.startswith("var:"):
            variant_id = sel[4:]
            v = await db.product_variants.find_one(
                {"id": variant_id},
                {"_id": 0, "id": 1, "product_name": 1, "product_code": 1, "final_price": 1,
                 "minimum_order_quantity": 1, "cable_size": 1, "hole_size": 1},
            )
            if not v:
                await _safe_send(send_text(wa, phone, "That variant isn't available. Pick another from the list above."),
                                 phone, "variant_not_found")
                return {"state": ST_PICK_VARIANT, "customer_phone": phone, "actions_taken": ["variant_not_found"]}
            ctx["current_variant_id"] = variant_id
            ctx["current_variant_code"] = v.get("product_code") or ""
            ctx["current_variant_name"] = (v.get("product_code") or v.get("product_name") or "variant")
            ctx["current_variant_price"] = float(v.get("final_price") or 0)
            ctx["current_variant_moq"] = int(v.get("minimum_order_quantity") or 1)
            await _safe_send(send_text(wa, phone,
                            f"How many units of *{ctx['current_variant_name']}* "
                            f"(₹{ctx['current_variant_price']:,.2f}/unit, MOQ {ctx['current_variant_moq']}) do you need?\n\n"
                            "Reply with a *number only*, e.g. 100"),
                             phone, "ask_qty")
            await _save_session(db, phone_norm, ST_ASK_QTY, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "ask_qty"})
            return {"state": ST_ASK_QTY, "customer_phone": phone, "actions_taken": ["picked_variant"]}
        else:
            await _safe_send(send_text(wa, phone, "Please pick a variant from the list above (or type 'menu' to start over)."),
                             phone, "expecting_variant")
            return {"state": ST_PICK_VARIANT, "customer_phone": phone, "actions_taken": ["expecting_variant"]}

    # ─── State: ASK_QTY — numeric only ───
    if state == ST_ASK_QTY:
        n = parse_first_number(text)
        if n is None or n <= 0:
            await _safe_send(send_text(wa, phone, "I need a *number*. Please reply with the quantity (e.g. 100)."),
                             phone, "bad_qty")
            return {"state": ST_ASK_QTY, "customer_phone": phone, "actions_taken": ["bad_qty"]}
        qty = int(n)
        moq = int(ctx.get("current_variant_moq") or 1)
        if qty < moq:
            await _safe_send(send_text(wa, phone,
                f"Minimum order quantity for *{ctx['current_variant_name']}* is *{moq}* units. Please reply with a number ≥ {moq}."),
                phone, "below_moq")
            return {"state": ST_ASK_QTY, "customer_phone": phone, "actions_taken": ["below_moq"]}
        ctx.setdefault("line_items", []).append({
            "variant_id": ctx["current_variant_id"],
            "variant_code": ctx["current_variant_code"],
            "variant_name": ctx["current_variant_name"],
            "unit_price": ctx["current_variant_price"],
            "qty": qty,
        })
        line_total = qty * ctx["current_variant_price"]
        n_items = len(ctx["line_items"])
        await _safe_send(send_buttons(
            wa, phone,
            body_text=f"✅ Added: *{qty} × {ctx['current_variant_name']}* (₹{line_total:,.2f})\n\nCart: *{n_items} item(s)*\n\nWhat next?",
            buttons=["Add another", "Review cart", "Cancel"],
        ), phone, "after_item")
        await _save_session(db, phone_norm, ST_AFTER_ITEM, ctx,
                            {"in": text, "at": _now_dt().isoformat(), "out": "after_item"})
        return {"state": ST_AFTER_ITEM, "customer_phone": phone, "actions_taken": ["added_line_item"]}

    # ─── State: AFTER_ITEM — Add another / Review cart / Cancel ───
    if state == ST_AFTER_ITEM:
        choice = text_lower
        if "add" in choice or sel == "1":
            # Re-ask metal each time (user choice 1b)
            for k in ("current_material_id", "current_material_name", "current_family_id",
                      "current_family_name", "current_cable_size", "current_hole_size",
                      "current_variant_id", "current_variant_code", "current_variant_name",
                      "current_variant_price", "current_variant_moq"):
                ctx.pop(k, None)
            new_state = await _enter_material_picker(ctx)
            return {"state": new_state, "customer_phone": phone, "actions_taken": ["add_more"]}
        elif "review" in choice or sel == "2":
            summary = _cart_summary_text(ctx.get("line_items") or [])
            await _safe_send(send_buttons(
                wa, phone,
                body_text=f"*Review your cart:*\n\n{summary}\n\nReady to receive your Proforma Invoice?",
                buttons=["Confirm & Send", "Cancel"],
                header_text="Cart Review",
            ), phone, "review_cart")
            await _save_session(db, phone_norm, ST_REVIEW_CART, ctx,
                                {"in": text, "at": _now_dt().isoformat(), "out": "review_cart"})
            return {"state": ST_REVIEW_CART, "customer_phone": phone, "actions_taken": ["review_cart"]}
        elif "cancel" in choice or sel == "3":
            await _safe_send(send_text(wa, phone, "Quote cancelled. Type 'menu' anytime to start over."),
                             phone, "cancelled")
            await db.chatbot_sessions.delete_one({"phone_norm": phone_norm})
            return {"state": "cancelled", "customer_phone": phone, "actions_taken": ["cancelled"]}
        else:
            await _safe_send(send_buttons(
                wa, phone,
                body_text="Please pick one:",
                buttons=["Add another", "Review cart", "Cancel"],
            ), phone, "after_item_repeat")
            return {"state": ST_AFTER_ITEM, "customer_phone": phone, "actions_taken": ["expecting_addmore"]}

    # ─── State: REVIEW_CART — Confirm / Cancel ───
    if state == ST_REVIEW_CART:
        choice = text_lower
        if "confirm" in choice or "send" in choice or sel == "1":
            line_items = ctx.get("line_items") or []
            if not line_items:
                await _safe_send(send_text(wa, phone, "Your cart is empty. Type 'menu' to start over."), phone, "empty_cart")
                await db.chatbot_sessions.delete_one({"phone_norm": phone_norm})
                return {"state": "cancelled", "customer_phone": phone, "actions_taken": ["empty_cart"]}
            try:
                result = await builder_fn(
                    line_items=line_items,
                    customer=ctx.get("customer") or {},
                    source="whatsapp_bot",
                )
                pi_no = (result.get("proforma") or {}).get("number") or result.get("quote_number")
                grand = float(result.get("grand_total") or 0)
                onum = result.get("order_number") or ""
                await send_text(
                    wa, phone,
                    f"✅ Done!\n\n*Proforma Invoice {pi_no}* generated.\n"
                    f"Order: {onum}\n"
                    f"Total: *₹{grand:,.2f}* _(GST included)_\n\n"
                    "PDF is on its way to your WhatsApp + email.\n"
                    "Track your order anytime at hrexporter.com/my-quotes.\n\n"
                    "Type 'menu' to start a new request."
                )
                await _save_session(db, phone_norm, ST_FINALIZED,
                                    {"quote_id": result.get("quote_id"), "order_id": result.get("order_id"),
                                     "proforma_number": pi_no},
                                    {"in": text, "at": _now_dt().isoformat(), "out": f"proforma:{pi_no}"})
                return {"state": ST_FINALIZED, "customer_phone": phone, "quote_id": result.get("quote_id"),
                        "order_id": result.get("order_id"), "actions_taken": ["finalized"]}
            except Exception as e:
                logger.exception("[bot] failed to finalize → proforma")
                await _safe_send(send_text(wa, phone,
                                "Sorry, I hit a snag generating your Proforma Invoice. Our team has been notified and will reach out shortly."),
                                 phone, "finalize_failed")
                return {"state": ST_REVIEW_CART, "customer_phone": phone, "actions_taken": ["finalize_failed"], "error": str(e)}
        elif "cancel" in choice or sel == "2":
            await _safe_send(send_text(wa, phone, "Order cancelled. Type 'menu' anytime to start over."),
                             phone, "cancelled")
            await db.chatbot_sessions.delete_one({"phone_norm": phone_norm})
            return {"state": "cancelled", "customer_phone": phone, "actions_taken": ["cancelled"]}
        else:
            await _safe_send(send_buttons(
                wa, phone,
                body_text="Please pick one:",
                buttons=["Confirm & Send", "Cancel"],
            ), phone, "review_repeat")
            return {"state": ST_REVIEW_CART, "customer_phone": phone, "actions_taken": ["expecting_confirm"]}

    if state == ST_HUMAN_HANDOFF or state == ST_FINALIZED:
        # Restart flow on next message
        await _send_main_menu(wa, phone)
        await _save_session(db, phone_norm, ST_WELCOME, {},
                            {"in": text, "at": _now_dt().isoformat(), "out": "main_menu"})
        return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": ["restart_after_terminal"]}

    # Fallback
    await _send_main_menu(wa, phone)
    return {"state": ST_WELCOME, "customer_phone": phone, "actions_taken": ["fallback"]}
