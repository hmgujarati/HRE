"""Universal Update — admin-driven WhatsApp + Email notification using the
approved Meta template (`hr_templet_delivery` with PDF / `hr_product_dispatch`
text-only). The template body is:

    Hello {{1}}
    Update from H R Exporter!

    {{2}}
    {{3}}
    {{4}}
    {{5}}
    {{6}}

    Thank you for choosing H R Exporter!

Variable 1 is the customer's name (auto-filled from contact). Variables 2-6
are the 5 message body lines, supplied by the admin (preset or free-text).
Empty body lines are replaced with "—" so Meta doesn't reject the send.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import PUBLIC_BASE_URL, UPLOAD_DIR, db, now_iso
from services.integrations import (
    get_integrations, send_smtp_email, send_whatsapp_template,
)

logger = logging.getLogger(__name__)

EMPTY_PLACEHOLDER = "—"


# ─────────────────── Preset library (7 ready-to-send messages) ───────────────────
# Each preset has 5 lines for variables {{2}}–{{6}}. Tokens enclosed in {{…}}
# are filled in from the order/line context at send time.
PRESETS: List[Dict[str, Any]] = [
    {
        "id": "pi_issued",
        "label": "Proforma Invoice issued",
        "needs_attachment": True,  # surfaces a hint in the UI
        "lines": [
            "Reference: Order {{order_number}}",
            "Proforma Invoice has been issued for your review",
            "Invoice value: ₹{{grand_total}} (inclusive of GST)",
            "Kindly remit the agreed advance to confirm production",
            "The Proforma Invoice is attached to this message",
        ],
    },
    {
        "id": "item_in_production",
        "label": "Item now in production",
        "lines": [
            "Reference: Order {{order_number}}",
            "Item: {{product_code}} × {{quantity}}",
            "Status: Production has commenced",
            "Expected dispatch date: {{expected_dispatch_date}}",
            "We will notify you as production progresses",
        ],
    },
    {
        "id": "item_ready",
        "label": "Item ready for dispatch",
        "lines": [
            "Reference: Order {{order_number}}",
            "Item: {{product_code}} × {{quantity}}",
            "Status: Manufacturing complete and ready for dispatch",
            "We are consolidating with other ready items",
            "The Tax Invoice will follow once the shipment is finalised",
        ],
    },
    {
        "id": "shipment_dispatched",
        "label": "Shipment dispatched",
        "needs_attachment": True,
        "lines": [
            "Reference: Order {{order_number}}",
            "Your shipment has been dispatched today",
            "Items in this shipment: {{product_code}}",
            "Transporter: {{transporter}} · LR No: {{lr_number}}",
            "Tax Invoice, E-Way Bill and LR Copy are attached",
        ],
    },
    {
        "id": "shipment_delivered",
        "label": "Shipment delivered",
        "lines": [
            "Reference: Order {{order_number}}",
            "Your shipment has been delivered",
            "Items delivered: {{product_code}}",
            "Kindly verify the consignment and revert in case of any discrepancy",
            "We appreciate your business and look forward to a continued partnership",
        ],
    },
    {
        "id": "schedule_revision",
        "label": "Schedule revision",
        "lines": [
            "Reference: Order {{order_number}}",
            "Item: {{product_code}} × {{quantity}}",
            "Revised dispatch schedule: {{expected_dispatch_date}}",
            "Reason: Lead-time extended (we will share specifics on request)",
            "We sincerely regret the inconvenience and appreciate your understanding",
        ],
    },
    {
        "id": "custom",
        "label": "Custom message",
        "lines": ["", "", "", "", ""],
    },
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}


# ─────────────────── Attachment resolution ───────────────────

ATTACH_CHOICES = {
    "none": (None, None),
    "proforma": ("proforma", "Proforma_Invoice"),
    "tax_invoice": ("documents.invoice", "Tax_Invoice"),
    "eway": ("documents.eway_bill", "E-Way_Bill"),
    "lr": ("documents.lr", "LR_Copy"),
}


def _resolve_attachment(order: Dict[str, Any], choice: str) -> Optional[Dict[str, Any]]:
    """Return {url, filename, local_path?} for the picked attachment, or None
    for 'none' / a missing document."""
    if not choice or choice == "none":
        return None
    spec = ATTACH_CHOICES.get(choice)
    if not spec:
        return None
    path, default_name = spec
    if not path:
        return None
    node: Any = order
    for p in path.split("."):
        node = (node or {}).get(p) if isinstance(node, dict) else None
    if not (node and isinstance(node, dict) and node.get("url")):
        return None
    url = node["url"]
    if url.startswith("/") and PUBLIC_BASE_URL:
        url = f"{PUBLIC_BASE_URL.rstrip('/')}{url}"
    order_no = (order.get("order_number") or "order").replace("/", "_")
    out: Dict[str, Any] = {"url": url, "filename": f"{default_name}_{order_no}.pdf"}
    # For SMTP we need a real file on disk — derive from the relative url path.
    rel = node["url"]
    if rel.startswith("/api/uploads/"):
        local = UPLOAD_DIR / rel.replace("/api/uploads/", "", 1)
        if local.exists():
            out["local_path"] = local
    return out


# ─────────────────── Send helper ───────────────────

def _sanitise(lines: List[str]) -> List[str]:
    """Replace empty lines with a single dash so Meta accepts every var."""
    out: List[str] = []
    for v in lines:
        s = (v or "").strip()
        out.append(s if s else EMPTY_PLACEHOLDER)
    return out


async def send_universal_update(
    order: Dict[str, Any],
    body_lines: List[str],
    attach_choice: str = "none",
    preset_id: Optional[str] = None,
    also_email: bool = True,
) -> Dict[str, Any]:
    """Send the universal update via WhatsApp (+ optional email mirror).
    Returns a dict with `whatsapp` + `email` deliveries and any errors. Persists
    nothing — the caller is responsible for writing to the order's
    notifications log.
    """
    settings = await get_integrations()
    wa = settings["whatsapp"]
    sm = settings["smtp"]
    uu = settings.get("universal_update") or {}
    template_lang = uu.get("template_language") or "en_US"
    template_doc = uu.get("template_doc") or ""
    template_text = uu.get("template_text") or ""

    contact_name = (order.get("contact_name") or "").strip() or "Valued customer"
    contact_phone = order.get("contact_phone") or ""
    contact_email = order.get("contact_email") or ""

    if len(body_lines) < 5:
        body_lines = list(body_lines) + [""] * (5 - len(body_lines))
    body_lines = _sanitise(body_lines[:5])

    attachment = _resolve_attachment(order, attach_choice)
    template_name = (template_doc if attachment else template_text) or ""

    result: Dict[str, Any] = {
        "whatsapp": {"sent": False, "wamid": None, "log_uid": None, "error": None,
                      "template": template_name, "attached": bool(attachment)},
        "email":    {"sent": False, "error": None},
        "preset_id": preset_id,
        "vars": [contact_name] + body_lines,
        "at": now_iso(),
    }

    # ── WhatsApp ──
    if not template_name:
        result["whatsapp"]["error"] = "Universal update template name is not set in Settings."
    elif not contact_phone:
        result["whatsapp"]["error"] = "Contact has no phone number."
    else:
        extra: Dict[str, Any] = {
            "field_2": body_lines[0],
            "field_3": body_lines[1],
            "field_4": body_lines[2],
            "field_5": body_lines[3],
            "field_6": body_lines[4],
        }
        if attachment:
            extra["header_document"] = attachment["url"]
            extra["header_document_name"] = attachment["filename"]
        try:
            body = await send_whatsapp_template(
                wa, contact_phone,
                template_name=template_name,
                template_language=template_lang,
                field_1=contact_name,
                extra=extra,
            )
            data = body.get("data") if isinstance(body, dict) else None
            result["whatsapp"]["sent"] = True
            result["whatsapp"]["wamid"] = (data or {}).get("wamid")
            result["whatsapp"]["log_uid"] = (data or {}).get("log_uid")
            result["whatsapp"]["status"] = (data or {}).get("status") or "sent"
        except Exception as e:  # HTTPException or otherwise
            logger.exception("[UniversalUpdate] WhatsApp send failed")
            result["whatsapp"]["error"] = getattr(e, "detail", None) or str(e)

    # ── Email mirror ──
    if also_email and contact_email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        try:
            subject = f"Update on Order {order.get('order_number') or ''}".strip(" -:")
            text_lines = [f"Hello {contact_name}", "Update from H R Exporter!", ""]
            text_lines += [ln for ln in body_lines]
            text_lines += ["", "Thank you for choosing H R Exporter!"]
            text_body = "\n".join(text_lines)
            attach_paths: List[Path] = []
            if attachment and attachment.get("local_path"):
                attach_paths.append(attachment["local_path"])
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, send_smtp_email,
                sm, contact_email, subject, text_body, attach_paths or None, None,
            )
            result["email"]["sent"] = True
        except Exception as e:
            logger.exception("[UniversalUpdate] Email send failed")
            result["email"]["error"] = str(e)

    return result


async def log_universal_update(oid: str, result: Dict[str, Any], user_email: str) -> None:
    """Append a `notifications` entry on the order so it shows in the audit log."""
    entry = {
        "kind": "universal_update",
        "preset_id": result.get("preset_id"),
        "by": user_email,
        "at": result.get("at"),
        "whatsapp": result["whatsapp"],
        "email": result["email"],
        "vars": result["vars"],
    }
    await db.orders.update_one({"id": oid}, {"$push": {"notifications": entry}})
