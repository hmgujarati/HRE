"""Dispatch service — Quote PDF generation + customer dispatch via WA + Email.

`dispatch_finalised_quote(quote)` is the single entry point for sending a
finalised Quotation to the customer: renders the PDF, ships it via the
WhatsApp document template (with PDF as `header_document`), then via SMTP
(branded HTML body + tracking pixel + PDF attachment), persists the dispatch
log onto the Quotation document, and returns delivery telemetry.

Order-stage notifications (`_order_auto_notify`) and proforma/tax-invoice
generation remain in `server.py` for now (entangled with order timeline +
auto-notify stages) — extract in Phase C.5.
"""

from __future__ import annotations
import asyncio
import logging
import re
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

from core import (
    PUBLIC_BASE_URL, SELLER_INFO_EMAIL, UPLOAD_DIR, db, now_iso,
)
from services.integrations import (
    get_integrations, normalise_phone, send_smtp_email, send_whatsapp_template,
)

logger = logging.getLogger("hre.dispatch")


def _now_dt() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


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
