"""Dispatch service — Quote PDF generation + customer dispatch via WA + Email.

`dispatch_finalised_quote(quote)` is the single entry point for sending a
finalised Quotation to the customer: renders the PDF, ships it via the
WhatsApp document template (with PDF as `header_document`), then via SMTP
(branded HTML body + tracking pixel + PDF attachment), persists the dispatch
log onto the Quotation document, and returns delivery telemetry.

Order pipeline helpers (`order_auto_notify`, `notify_production_update`,
`persist_order_notification`, `mint_order_from_quote`, document save +
required-docs guards, FY-prefixed sequencers for ORD/PI/INV numbers) live
here too so the orders router can stay pure HTTP code.
"""

from __future__ import annotations
import asyncio
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from core import (
    PUBLIC_BASE_URL, SELLER_INFO_EMAIL, UPLOAD_DIR, db, now_iso,
)
from services.integrations import (
    get_integrations, normalise_phone, send_smtp_email, send_whatsapp_document,
    send_whatsapp_template,
)

logger = logging.getLogger("hre.dispatch")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────── Order pipeline constants ───────────────────

ORDER_STAGES = [
    ("pending_po", "Awaiting Purchase Order"),
    ("po_received", "PO Received"),
    ("proforma_issued", "Proforma Invoice Issued"),
    ("order_placed", "Order Placed with Factory"),
    ("raw_material_check", "Raw Material Check"),
    ("procuring_raw_material", "Procuring Raw Material"),
    ("in_production", "In Production"),
    ("packaging", "Packaging"),
    ("dispatched", "Dispatched"),
    ("lr_received", "LR Received"),
    ("delivered", "Delivered"),
]
STAGE_TO_LABEL = dict(ORDER_STAGES)
STAGE_ORDER = [s for s, _ in ORDER_STAGES]
STAGE_TEMPLATE_LABEL = {
    "pending_po": "Awaiting PO",
    "po_received": "PO Received",
    "proforma_issued": "Proforma Invoice Issued",
    "order_placed": "Order Placed with Factory",
    "raw_material_check": "Raw Material Check",
    "procuring_raw_material": "Procuring Raw Material",
    "in_production": "Production",
    "packaging": "Packaging",
    "dispatched": "Dispatched",
    "lr_received": "LR Received",
    "delivered": "Delivered",
}
AUTO_NOTIFY_STAGES = {
    "proforma_issued": "order_pi_template",
    "in_production": "order_production_template",
    "packaging": "order_packaging_template",
    "dispatched": "order_dispatched_template",
    "lr_received": "order_lr_template",
}
STAGE_REQUIRED_DOCS: Dict[str, List[tuple]] = {
    "proforma_issued": [("proforma", "Proforma Invoice")],
    "dispatched": [("documents.invoice", "Tax Invoice"), ("documents.eway_bill", "E-way Bill")],
    "lr_received": [("documents.lr", "LR Copy")],
}


# ─────────────────── FY-prefixed sequencers ───────────────────

async def _next_doc_number(prefix: str, query_field_path: str) -> str:
    year = _now_dt().year
    fy_start = year if _now_dt().month >= 4 else year - 1
    full_prefix = f"{prefix}/{fy_start}-{(fy_start + 1) % 100:02d}/"
    q = {query_field_path: {"$regex": f"^{re.escape(full_prefix)}"}}
    last = await db.orders.find(q, {"_id": 0}).sort(query_field_path, -1).to_list(length=1)
    seq = 1
    if last:
        try:
            # walk dotted path
            v: Any = last[0]
            for part in query_field_path.split("."):
                v = (v or {}).get(part) if isinstance(v, dict) else None
            seq = int(str(v).split("/")[-1]) + 1
        except Exception:
            pass
    return f"{full_prefix}{seq:04d}"


async def next_order_number() -> str:
    return await _next_doc_number("HRE/ORD", "order_number")


async def next_pi_number() -> str:
    return await _next_doc_number("HRE/PI", "proforma.number")


async def next_invoice_number() -> str:
    return await _next_doc_number("HRE/INV", "documents.invoice.number")


# ─────────────────── Timeline + order minting ───────────────────

def timeline_event(kind: str, label: str, user_email: str, **extra) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "label": label,
        "at": now_iso(),
        "by": user_email,
        **extra,
    }


def mint_order_from_quote(quote: dict, user_email: str, po_number: Optional[str] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "order_number": "",
        "quote_id": quote["id"],
        "quote_number": quote.get("quote_number"),
        "contact_id": quote.get("contact_id"),
        "contact_name": quote.get("contact_name"),
        "contact_company": quote.get("contact_company"),
        "contact_phone": quote.get("contact_phone"),
        "contact_email": quote.get("contact_email"),
        "contact_gst": quote.get("contact_gst"),
        "billing_address": quote.get("billing_address"),
        "shipping_address": quote.get("shipping_address"),
        "place_of_supply": quote.get("place_of_supply"),
        "line_items": quote.get("line_items") or [],
        "taxable_value": quote.get("taxable_value"),
        "total_gst": quote.get("total_gst"),
        "total_discount": quote.get("total_discount"),
        "grand_total": quote.get("grand_total"),
        "currency": quote.get("currency", "INR"),
        "stage": "pending_po",
        "po_number": po_number or "",
        "po_received_at": None,
        "documents": {},
        "proforma": {},
        "raw_material_status": "",
        "production_updates": [],
        "dispatch": {},
        "timeline": [
            timeline_event("created", "Order created from approved quote", user_email,
                            quote_number=quote.get("quote_number")),
        ],
        "notifications": [],
        "created_by": user_email,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def missing_required_docs(order: dict, stage: str) -> List[str]:
    needed = STAGE_REQUIRED_DOCS.get(stage) or []
    missing: List[str] = []
    for path, label in needed:
        node: Any = order
        for p in path.split("."):
            node = (node or {}).get(p) if isinstance(node, dict) else None
        if not (node and isinstance(node, dict) and node.get("filename")):
            missing.append(label)
    return missing


async def save_order_doc(oid: str, doc_key: str, file: UploadFile,
                          user_email: str, extra: Optional[dict] = None) -> dict:
    """Persist an uploaded file under /uploads/orders/{oid}/ and record metadata."""
    out_dir = UPLOAD_DIR / "orders" / oid
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", (file.filename or doc_key).rsplit(".", 1)[0])
    ext = (file.filename.rsplit(".", 1)[-1] if (file.filename and "." in file.filename) else "bin").lower()
    safe_name = f"{doc_key}_{safe_stem}_{ts}.{ext}"
    out = out_dir / safe_name
    content = await file.read()
    out.write_bytes(content)
    public_url = (f"{PUBLIC_BASE_URL}/api/uploads/orders/{oid}/{safe_name}"
                  if PUBLIC_BASE_URL else f"/api/uploads/orders/{oid}/{safe_name}")
    return {
        "filename": safe_name,
        "original_name": file.filename or "",
        "url": public_url,
        "uploaded_at": now_iso(),
        "uploaded_by": user_email,
        **(extra or {}),
    }


async def generate_quote_pdf(quote: dict, unique: bool = False) -> Path:
    """Render the quote to a PDF saved under uploads/quotes/.
    `unique=True` adds a timestamp suffix so WhatsApp/Meta media cache
    (keyed on URL) always fetches a fresh copy."""
    from quote_pdf import render_quote_pdf
    out_dir = UPLOAD_DIR / "quotes"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", quote.get("quote_number") or quote["id"])
    if unique:
        ts = _now_dt().strftime("%Y%m%d%H%M%S")
        out = out_dir / f"{safe_name}_{ts}.pdf"
    else:
        out = out_dir / f"{safe_name}.pdf"
    logo = UPLOAD_DIR.parent.parent / "frontend" / "public" / "hre-logo-light-bg.png"
    logo_url = logo.as_uri() if logo.exists() else None
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, render_quote_pdf, quote, out, logo_url)
    return out


async def dispatch_finalised_quote(quote: dict) -> dict:
    """Generate PDF → ship via WA + SMTP → persist dispatch_log on quote doc.
    Never raises — failures are captured in `delivery.errors[]` and returned."""
    cur = await get_integrations()
    wa = cur["whatsapp"]
    sm = cur["smtp"]
    delivery: Dict[str, Any] = {"pdf": False, "whatsapp": False, "email": False, "errors": {}}
    log_entries: List[Dict[str, Any]] = []

    contact_email = (quote.get("contact_email") or "").strip()
    contact_phone = (quote.get("contact_phone") or "").strip()
    contact_name = quote.get("contact_name") or ""
    contact_company = quote.get("contact_company") or ""
    if quote.get("contact_id"):
        live = await db.contacts.find_one({"id": quote["contact_id"]}, {"_id": 0})
        if live:
            contact_email = contact_email or (live.get("email") or "").strip()
            contact_phone = contact_phone or (live.get("phone") or "").strip()
            contact_name = contact_name or live.get("name") or ""
            contact_company = contact_company or live.get("company") or ""

    try:
        pdf_path = await generate_quote_pdf(quote, unique=True)
        delivery["pdf"] = True
        delivery["pdf_path"] = pdf_path.name
    except Exception as e:
        logger.exception("[Quote PDF] generation failed")
        delivery["errors"]["pdf"] = str(e)
        return delivery

    public_pdf_url = f"{PUBLIC_BASE_URL}/api/uploads/quotes/{pdf_path.name}" if PUBLIC_BASE_URL else None
    dispatched_at = now_iso()

    # WhatsApp document template
    if wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and wa.get("quote_template_name"):
        wa_entry: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "channel": "whatsapp",
            "template": wa["quote_template_name"],
            "to": normalise_phone(contact_phone, wa.get("default_country_code") or "91") if contact_phone else "",
            "pdf_file": pdf_path.name,
            "pdf_url": public_pdf_url,
            "sent_at": dispatched_at,
            "status": "pending",
        }
        if not contact_phone:
            wa_entry["status"] = "failed"
            wa_entry["error"] = "no contact phone"
            delivery["errors"]["whatsapp"] = "no contact phone"
        elif not public_pdf_url:
            wa_entry["status"] = "failed"
            wa_entry["error"] = "PUBLIC_BASE_URL not configured"
            delivery["errors"]["whatsapp"] = "PUBLIC_BASE_URL not configured for media URL"
        else:
            try:
                customer_name = contact_name or contact_company or "Customer"
                grand_inr = float(quote.get("grand_total") or 0)
                grand_str = f"Total: ₹{grand_inr:,.2f}"
                line_count = len(quote.get("line_items") or [])
                valid_iso = (quote.get("valid_until") or "").strip()
                if valid_iso:
                    try:
                        d = datetime.fromisoformat(valid_iso)
                        valid_str = f"Valid till {d.strftime('%d-%m-%Y')} · {line_count} item{'s' if line_count != 1 else ''}"
                    except Exception:
                        valid_str = f"{line_count} item{'s' if line_count != 1 else ''} · validity 30 days"
                else:
                    valid_str = f"{line_count} item{'s' if line_count != 1 else ''} · validity 30 days"
                body = await send_whatsapp_template(
                    wa, contact_phone,
                    template_name=wa["quote_template_name"],
                    template_language=wa.get("quote_template_language") or "en",
                    field_1=customer_name,
                    extra={
                        "field_2": quote.get("quote_number", ""),
                        "field_3": grand_str,
                        "field_4": valid_str,
                        "header_document": public_pdf_url,
                        "header_document_name": f"{quote.get('quote_number') or 'quotation'}.pdf",
                    },
                )
                data = body.get("data") if isinstance(body, dict) else None
                wa_entry["wamid"] = (data or {}).get("wamid")
                wa_entry["log_uid"] = (data or {}).get("log_uid")
                wa_entry["status"] = (data or {}).get("status", "sent") or "sent"
                delivery["whatsapp"] = True
            except HTTPException as e:
                wa_entry["status"] = "failed"
                wa_entry["error"] = str(e.detail)
                delivery["errors"]["whatsapp"] = str(e.detail)
            except Exception as e:
                logger.exception("[Quote WA] dispatch failed")
                wa_entry["status"] = "failed"
                wa_entry["error"] = str(e)
                delivery["errors"]["whatsapp"] = str(e)
        log_entries.append(wa_entry)

    # SMTP email
    if sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        email_entry: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "channel": "email",
            "to": contact_email,
            "pdf_file": pdf_path.name,
            "sent_at": dispatched_at,
            "status": "pending",
        }
        if contact_email:
            try:
                open_token = secrets.token_urlsafe(24)
                email_entry["open_token"] = open_token
                pixel_url = (
                    f"{PUBLIC_BASE_URL}/api/webhooks/email/open?t={open_token}"
                    if PUBLIC_BASE_URL else ""
                )
                subject = f"Quotation {quote.get('quote_number')} from HRE Exporter"
                grand_txt = f"₹{float(quote.get('grand_total') or 0):,.2f}"
                body_txt = (
                    f"Dear {contact_name or 'Sir/Madam'},\n\n"
                    f"Please find attached the quotation {quote.get('quote_number')} for your inquiry.\n\n"
                    f"Grand Total: {grand_txt}\n\n"
                    f"For any queries, contact us at {SELLER_INFO_EMAIL}.\n\n"
                    f"Regards,\nHRE Exporter Team"
                )
                body_html = f"""<!DOCTYPE html>
<html><body style="font-family: Arial, Helvetica, sans-serif; color: #1A1A1A; background: #f5f5f5; margin:0; padding:24px;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width: 580px; margin: 0 auto; background:#fff; border:1px solid #e5e5e5;">
    <tr><td style="padding: 24px 28px; border-bottom: 3px solid #FBAE17;">
      <div style="font-size: 10px; letter-spacing: 3px; text-transform: uppercase; color:#FBAE17; font-weight:bold;">HREXPORTER · Quotation</div>
      <h1 style="margin: 6px 0 0; font-size: 22px; letter-spacing: -0.5px;">{quote.get('quote_number','')}</h1>
    </td></tr>
    <tr><td style="padding: 24px 28px; font-size: 14px; line-height: 1.6;">
      <p style="margin:0 0 14px;">Dear <strong>{contact_name or 'Sir/Madam'}</strong>,</p>
      <p style="margin:0 0 14px;">Please find the attached quotation for your recent inquiry. You can open it on any phone or desktop — it carries our full GST breakdown, bank details and terms.</p>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#FBAE17; margin: 14px 0;">
        <tr><td style="padding: 14px 18px;">
          <div style="font-size: 10px; letter-spacing: 2px; text-transform: uppercase; font-weight: bold;">Grand Total</div>
          <div style="font-size: 24px; font-weight: 900; font-family: 'Courier New', monospace;">{grand_txt}</div>
        </td></tr>
      </table>
      <p style="margin:0 0 6px;">If you have any questions, reply to this email or WhatsApp us — we'd love to help finalize this order for you.</p>
      <p style="margin: 14px 0 0; color: #666; font-size: 12px;">Regards,<br/><strong>HRE Exporter Team</strong><br/><a href="mailto:{SELLER_INFO_EMAIL}" style="color:#1A1A1A;">{SELLER_INFO_EMAIL}</a></p>
    </td></tr>
    <tr><td style="padding: 14px 28px; font-size: 10px; color:#999; border-top:1px solid #eee;">
      This quotation is confidential and intended for {contact_email}. If you received this by mistake, please delete it.
    </td></tr>
  </table>
  {f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:block; border:0;" />' if pixel_url else ''}
</body></html>"""
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, send_smtp_email, sm, contact_email, subject, body_txt, [pdf_path], body_html,
                )
                email_entry["status"] = "sent"
                delivery["email"] = True
            except Exception as e:
                logger.exception("[Quote SMTP] dispatch failed")
                email_entry["status"] = "failed"
                email_entry["error"] = str(e)
                delivery["errors"]["email"] = str(e)
        else:
            email_entry["status"] = "failed"
            email_entry["error"] = "no contact email"
            delivery["errors"]["email"] = "no contact email"
        log_entries.append(email_entry)

    if log_entries:
        try:
            await db.quotations.update_one(
                {"id": quote["id"]},
                {
                    "$push": {"dispatch_log": {"$each": log_entries}},
                    "$set": {"last_dispatched_at": dispatched_at},
                },
            )
        except Exception:
            logger.exception("[Dispatch] failed to persist dispatch log")

    delivery["log_entries"] = log_entries
    return delivery


# ─────────────────── Order stage auto-notify ───────────────────

async def order_auto_notify(order: dict, stage: str) -> Optional[dict]:
    """Fire WhatsApp + Email notifications for an order stage transition.
    On `dispatched`, ships a follow-up media message with the second document.
    Returns a notification dict (with `_retry_payload` if email failed) or None."""
    settings = await get_integrations()
    wa = settings["whatsapp"]
    sm = settings["smtp"]
    tpl_key = AUTO_NOTIFY_STAGES.get(stage)
    if not tpl_key:
        return None
    tpl_name = wa.get(tpl_key)
    tpl_lang = wa.get(f"{tpl_key}_language") or wa.get("quote_template_language") or "en"
    phone = order.get("contact_phone") or ""
    email = (order.get("contact_email") or "").strip()
    if (not phone or not email) and order.get("contact_id"):
        live = await db.contacts.find_one({"id": order["contact_id"]}, {"_id": 0, "phone": 1, "email": 1})
        if live:
            phone = phone or live.get("phone") or ""
            email = email or (live.get("email") or "").strip()
    customer = order.get("contact_name") or order.get("contact_company") or "Customer"
    ord_no = order.get("order_number") or ""
    stage_label = STAGE_TO_LABEL.get(stage, stage)
    stage_template_label = STAGE_TEMPLATE_LABEL.get(stage, stage_label)
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M")
    eta = (order.get("expected_completion_date") or "").strip()
    eta_pretty = ""
    if eta:
        try:
            eta_pretty = datetime.strptime(eta, "%Y-%m-%d").strftime("%d-%b-%Y")
        except Exception:
            eta_pretty = eta
    field_4_value = f"{timestamp}  ·  Expected completion: {eta_pretty}" if eta_pretty else timestamp
    docs = order.get("documents") or {}
    attachments: List[dict] = []

    def add_doc(meta: Optional[dict], label: str):
        if not meta:
            return
        url = (meta or {}).get("url")
        fn = (meta or {}).get("filename")
        if not (url and fn):
            return
        local_path = UPLOAD_DIR / "orders" / order["id"] / fn
        attachments.append({
            "url": url, "filename": fn, "label": label,
            "path": local_path if local_path.exists() else None,
        })

    if stage == "proforma_issued":
        pi = order.get("proforma") or {}
        if pi.get("url") and pi.get("filename"):
            local_path = UPLOAD_DIR / "orders" / order["id"] / pi["filename"]
            attachments.append({
                "url": pi["url"], "filename": pi["filename"], "label": "Proforma Invoice",
                "path": local_path if local_path.exists() else None,
            })
    elif stage == "dispatched":
        add_doc(docs.get("invoice"), "Tax Invoice")
        add_doc(docs.get("eway_bill"), "E-way Bill")
    elif stage == "lr_received":
        add_doc(docs.get("lr"), "LR Copy")

    primary = attachments[0] if attachments else None
    secondary = attachments[1] if len(attachments) > 1 else None
    result: Dict[str, Any] = {"template": tpl_name, "stage": stage, "whatsapp": False, "email": False}
    open_token = secrets.token_urlsafe(24)

    if tpl_name and phone and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token"):
        try:
            extra: Dict[str, Any] = {"field_2": ord_no, "field_3": stage_template_label, "field_4": field_4_value}
            if primary:
                extra["header_document"] = primary["url"]
                extra["header_document_name"] = primary["filename"]
            body = await send_whatsapp_template(
                wa, phone, template_name=tpl_name, template_language=tpl_lang,
                field_1=customer, extra=extra,
            )
            data = body.get("data") if isinstance(body, dict) else {}
            result["whatsapp"] = True
            result["wamid"] = data.get("wamid")
            result["status"] = data.get("status") or "sent"
            result["whatsapp_status"] = "sent"
            if secondary:
                try:
                    await send_whatsapp_document(
                        wa, phone, media_url=secondary["url"],
                        file_name=secondary["filename"],
                        caption=f"{secondary['label']} — Order {ord_no}",
                    )
                    result["whatsapp_secondary"] = True
                except Exception as e:
                    logger.warning(f"[Order notify] secondary WA doc failed: {e}")
                    result["whatsapp_secondary_error"] = str(e)
        except HTTPException as e:
            logger.error(f"[Order notify] stage={stage} WA failed: {e.detail}")
            result["whatsapp_error"] = str(e.detail)
        except Exception as e:
            logger.exception(f"[Order notify] stage={stage} WA unexpected error")
            result["whatsapp_error"] = str(e)

    if email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        attach_paths = [a["path"] for a in attachments if a.get("path")]
        try:
            subject = f"Order Update — {ord_no} · {stage_label}"
            attach_list_html = ""
            if attachments:
                items = "".join(f"<li>{a['label']} — <span style='color:#71717a'>{a['filename']}</span></li>" for a in attachments)
                attach_list_html = f"<p style='margin:18px 0 4px;color:#1A1A1A;font-weight:bold;font-size:13px;'>Attached:</p><ul style='margin:0;padding-left:18px;color:#3f3f46;font-size:13px;line-height:1.7;'>{items}</ul>"
            eta_block_html = ""
            if eta_pretty:
                eta_block_html = f"<div style='margin-top:10px;background:#1A1A1A;color:#FBAE17;padding:10px 14px;font-family:Arial,sans-serif;font-size:12px;font-weight:bold;letter-spacing:0.5px;'>EXPECTED COMPLETION · {eta_pretty}</div>"
            body_text = (
                f"Hello {customer},\n\nYour order {ord_no} has moved to: {stage_label}.\nUpdated: {timestamp}\n"
                + (f"Expected completion: {eta_pretty}\n" if eta_pretty else "") + "\n"
                + ("Documents attached:\n" + "\n".join(f"  - {a['label']}" for a in attachments) + "\n\n" if attachments else "")
                + "Track your order live in our customer portal.\n\nTeam HRExporter\nAn ISO 9001:2015 Certified Company"
            )
            body_html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f5f5;padding:32px 16px;margin:0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e4e4e7;">
<tr><td style="background:#1A1A1A;color:#FBAE17;padding:18px 24px;font-weight:800;letter-spacing:2px;font-size:11px;text-transform:uppercase;">HRE Exporter — Order Update</td></tr>
<tr><td style="padding:28px 24px;">
<p style="margin:0 0 6px;color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">Order {ord_no}</p>
<h2 style="margin:0 0 16px;color:#1A1A1A;font-size:22px;font-weight:900;">{stage_label}</h2>
<p style="margin:0 0 18px;color:#3f3f46;font-size:14px;line-height:1.6;">Hello {customer},<br/>Your order has moved to <b>{stage_label}</b>.</p>
<div style="background:#FBAE17;color:#1A1A1A;padding:10px 14px;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;">UPDATED · {timestamp}</div>
{eta_block_html}{attach_list_html}
</td></tr>
<tr><td style="background:#fafafa;color:#a1a1aa;padding:14px 24px;font-size:11px;text-align:center;border-top:1px solid #e4e4e7;">An ISO 9001:2015 Certified Company &middot; {SELLER_INFO_EMAIL}</td></tr>
</table>
<img src="{PUBLIC_BASE_URL}/api/webhooks/email/open?t={open_token}" width="1" height="1" alt="" style="display:none" />
</body></html>"""
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, send_smtp_email, sm, email, subject, body_text, attach_paths, body_html)
            result["email"] = True
            result["email_open_token"] = open_token
            result["email_status"] = "sent"
        except Exception as e:
            err = str(e)[:300]
            logger.exception(f"[Order notify] stage={stage} email failed; queuing retry")
            result["email_error"] = err
            result["email_open_token"] = open_token
            result["email_retry_attempt"] = 1
            result["_retry_payload"] = {
                "to_email": email, "subject": subject,
                "body_text": body_text, "body_html": body_html,
                "attach_paths": [str(p) for p in attach_paths] if attach_paths else None,
            }
    if not (result["whatsapp"] or result["email"] or result.get("whatsapp_error") or result.get("email_error")):
        return None
    return result


async def notify_production_update(order: dict, note: str) -> Optional[dict]:
    """Fire WA template (if configured) + branded email for an ad-hoc production update note."""
    settings = await get_integrations()
    wa = settings["whatsapp"]
    sm = settings["smtp"]
    phone = order.get("contact_phone") or ""
    email = (order.get("contact_email") or "").strip()
    if (not phone or not email) and order.get("contact_id"):
        live = await db.contacts.find_one({"id": order["contact_id"]}, {"_id": 0, "phone": 1, "email": 1})
        if live:
            phone = phone or live.get("phone") or ""
            email = email or (live.get("email") or "").strip()
    customer = order.get("contact_name") or order.get("contact_company") or "Customer"
    ord_no = order.get("order_number") or ""
    timestamp_raw = datetime.now().strftime("%d-%m-%Y %H:%M")
    eta = (order.get("expected_completion_date") or "").strip()
    eta_pretty = ""
    if eta:
        try:
            eta_pretty = datetime.strptime(eta, "%Y-%m-%d").strftime("%d-%b-%Y")
        except Exception:
            eta_pretty = eta
    timestamp = f"{timestamp_raw}  ·  Expected completion: {eta_pretty}" if eta_pretty else timestamp_raw
    result: Dict[str, Any] = {"kind": "production_update", "note": note, "whatsapp": False, "email": False, "at": now_iso()}
    open_token = secrets.token_urlsafe(24)
    tpl_name = (wa.get("order_production_update_template") or wa.get("order_production_template") or "").strip()
    tpl_lang = (
        wa.get("order_production_update_template_language") if wa.get("order_production_update_template")
        else wa.get("order_production_template_language")
    ) or "en"
    if tpl_name and phone and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token"):
        try:
            body = await send_whatsapp_template(
                wa, phone, template_name=tpl_name, template_language=tpl_lang,
                field_1=customer, extra={"field_2": ord_no, "field_3": note, "field_4": timestamp},
            )
            data = body.get("data") if isinstance(body, dict) else {}
            result["whatsapp"] = True
            result["wamid"] = data.get("wamid")
            result["template"] = tpl_name
            result["whatsapp_status"] = "sent"
        except HTTPException as e:
            logger.error(f"[Prod update notify] WA failed: {e.detail}")
            result["whatsapp_error"] = str(e.detail)
        except Exception as e:
            logger.exception("[Prod update notify] WA unexpected error")
            result["whatsapp_error"] = str(e)
    if email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        try:
            subject = f"Production Update — {ord_no}"
            eta_block_html = ""
            if eta_pretty:
                eta_block_html = f"<div style='margin-top:10px;background:#1A1A1A;color:#FBAE17;padding:10px 14px;font-family:Arial,sans-serif;font-size:12px;font-weight:bold;letter-spacing:0.5px;'>EXPECTED COMPLETION · {eta_pretty}</div>"
            body_text = (
                f"Hello {customer},\n\nProduction update on your order {ord_no}:\n\n"
                f"\"{note}\"\n\nUpdated: {timestamp_raw}\n"
                + (f"Expected completion: {eta_pretty}\n" if eta_pretty else "")
                + "\nTrack your order live in our customer portal.\n\nTeam HRExporter\nAn ISO 9001:2015 Certified Company"
            )
            body_html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f5f5;padding:32px 16px;margin:0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e4e4e7;">
<tr><td style="background:#1A1A1A;color:#FBAE17;padding:18px 24px;font-weight:800;letter-spacing:2px;font-size:11px;text-transform:uppercase;">HRE Exporter — Production Update</td></tr>
<tr><td style="padding:28px 24px;">
<p style="margin:0 0 6px;color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">Order {ord_no}</p>
<h2 style="margin:0 0 16px;color:#1A1A1A;font-size:20px;font-weight:900;">Production Update</h2>
<p style="margin:0 0 18px;color:#3f3f46;font-size:14px;line-height:1.6;">Hello {customer}, here's the latest update from our production floor:</p>
<blockquote style="margin:0 0 18px;padding:14px 16px;background:#fafafa;border-left:4px solid #FBAE17;color:#1A1A1A;font-size:15px;line-height:1.55;font-style:italic;">{note}</blockquote>
<div style="background:#FBAE17;color:#1A1A1A;padding:10px 14px;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;">UPDATED · {timestamp_raw}</div>
{eta_block_html}
</td></tr>
<tr><td style="background:#fafafa;color:#a1a1aa;padding:14px 24px;font-size:11px;text-align:center;border-top:1px solid #e4e4e7;">An ISO 9001:2015 Certified Company &middot; {SELLER_INFO_EMAIL}</td></tr>
</table>
<img src="{PUBLIC_BASE_URL}/api/webhooks/email/open?t={open_token}" width="1" height="1" alt="" style="display:none" />
</body></html>"""
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, send_smtp_email, sm, email, subject, body_text, None, body_html)
            result["email"] = True
            result["email_open_token"] = open_token
            result["email_status"] = "sent"
        except Exception as e:
            err = str(e)[:300]
            logger.exception("[Prod update notify] email failed; queuing retry")
            result["email_error"] = err
            result["email_open_token"] = open_token
            result["email_retry_attempt"] = 1
            result["_retry_payload"] = {
                "to_email": email, "subject": subject,
                "body_text": body_text, "body_html": body_html, "attach_paths": None,
            }
    if not (result["whatsapp"] or result["email"] or result.get("whatsapp_error") or result.get("email_error")):
        return None
    return result


async def persist_order_notification(order_id: str, notify: dict) -> dict:
    """Push a notification onto orders.notifications + (if email failed) enqueue a retry.
    Late-imports `_enqueue_email_retry` from server.py to keep email-retry queue working
    until that worker is also moved into a service module."""
    if not notify:
        return notify
    notify = dict(notify)
    notify.setdefault("id", str(uuid.uuid4()))
    notify.setdefault("at", now_iso())
    retry_payload = notify.pop("_retry_payload", None)
    await db.orders.update_one({"id": order_id}, {"$push": {"notifications": notify}})
    if retry_payload and notify.get("email_error"):
        try:
            from server import _enqueue_email_retry  # late import: server.py is fully loaded by now
            await _enqueue_email_retry(
                order_id=order_id,
                notification_id=notify["id"],
                payload=retry_payload,
                last_error=notify["email_error"],
            )
        except Exception:
            logger.exception("[Notify] failed to enqueue email retry")
    return notify
