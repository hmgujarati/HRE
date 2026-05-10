"""Integrations service — WhatsApp (BizChatAPI) + SMTP + OTP delivery helpers.

Stateless functions called from `services/dispatch.py`, `routers/settings.py`,
`routers/webhooks.py`, and the public OTP endpoints in `server.py`.
"""

from __future__ import annotations
import hashlib
import logging
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from core import OTP_TTL_SECONDS, SETTINGS_DOC_ID, db, now_iso

logger = logging.getLogger("hre.integrations")


DEFAULT_INTEGRATIONS = {
    "id": SETTINGS_DOC_ID,
    "whatsapp": {
        "enabled": False,
        "api_base_url": "https://bizchatapi.in/api",
        "vendor_uid": "",
        "token": "",
        "from_phone_number_id": "",
        "otp_template_name": "",
        "otp_template_language": "en",
        "default_country_code": "91",
        "quote_template_name": "",
        "quote_template_language": "en",
        "webhook_secret": "",
        "order_pi_template": "",
        "order_pi_template_language": "en",
        "order_production_template": "",
        "order_production_template_language": "en",
        "order_packaging_template": "",
        "order_packaging_template_language": "en",
        "order_dispatched_template": "",
        "order_dispatched_template_language": "en",
        "order_lr_template": "",
        "order_lr_template_language": "en",
        "order_production_update_template": "",
        "order_production_update_template_language": "en",
        "admin_notify_phone": "",
        "po_received_admin_template": "",
        "po_received_admin_template_language": "en",
    },
    "smtp": {
        "enabled": False,
        "host": "smtp.hostinger.com",
        "port": 465,
        "use_ssl": True,
        "username": "",
        "password": "",
        "from_email": "",
        "from_name": "HRE Exporter",
        "admin_notify_email": "",
    },
}


# ─────────────────── Settings access ───────────────────

async def get_integrations() -> dict:
    doc = await db.settings.find_one({"id": SETTINGS_DOC_ID}, {"_id": 0})
    if not doc:
        return DEFAULT_INTEGRATIONS.copy()
    out = {**DEFAULT_INTEGRATIONS, **doc}
    out["whatsapp"] = {**DEFAULT_INTEGRATIONS["whatsapp"], **(doc.get("whatsapp") or {})}
    out["smtp"] = {**DEFAULT_INTEGRATIONS["smtp"], **(doc.get("smtp") or {})}
    if not out["whatsapp"].get("webhook_secret"):
        out["whatsapp"]["webhook_secret"] = secrets.token_urlsafe(24)
        await db.settings.update_one(
            {"id": SETTINGS_DOC_ID},
            {"$set": {"whatsapp.webhook_secret": out["whatsapp"]["webhook_secret"], "id": SETTINGS_DOC_ID}},
            upsert=True,
        )
    return out


def mask_secret(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val)
    if len(s) <= 6:
        return "•" * len(s)
    return s[:3] + "•" * max(4, len(s) - 6) + s[-3:]


def public_integrations(d: dict) -> dict:
    out = {**d}
    out["whatsapp"] = {**d["whatsapp"], "token": mask_secret(d["whatsapp"].get("token"))}
    out["smtp"] = {**d["smtp"], "password": mask_secret(d["smtp"].get("password"))}
    return out


# ─────────────────── Phone helpers ───────────────────

def normalise_phone(phone: str, default_cc: str = "91") -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if len(digits) == 10:
        digits = (default_cc or "91") + digits
    if digits.startswith("0"):
        digits = digits.lstrip("0")
        if len(digits) == 10:
            digits = (default_cc or "91") + digits
    return digits


# ─────────────────── BizChat WhatsApp ───────────────────

async def send_whatsapp_template(
    wa: dict, phone: str, template_name: str, template_language: str,
    field_1: Optional[str] = None, button_0: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and template_name):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/send-template-message"
    payload: Dict[str, Any] = {
        "from_phone_number_id": wa.get("from_phone_number_id") or "",
        "phone_number": normalise_phone(phone, wa.get("default_country_code") or "91"),
        "template_name": template_name,
        "template_language": template_language or "en",
    }
    if field_1 is not None:
        payload["field_1"] = str(field_1)
    if button_0 is not None:
        payload["button_0"] = str(button_0)
    if extra:
        payload.update(extra)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, params={"token": wa["token"]}, json=payload)
        body: Any
        try:
            body = r.json()
        except Exception:
            body = r.text
        if r.status_code >= 400:
            logger.error(f"[WA] template send failed status={r.status_code} body={body}")
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        if isinstance(body, dict) and body.get("result") == "error":
            detail = body.get("message") or "Unknown BizChat error"
            logger.error(f"[WA] template send error body={body}")
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        if isinstance(body, dict):
            data = body.get("data") or {}
            if not (data.get("wamid") or data.get("log_uid")):
                detail = body.get("message") or body.get("error") or "BizChat accepted the request but did not return a message ID"
                logger.error(f"[WA] template send returned no wamid body={body}")
                raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        logger.info(f"[WA] template={template_name} → {payload['phone_number']} ok")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        logger.exception("[WA] HTTP error")
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def get_whatsapp_message_status(wa: dict, wamid: str) -> dict:
    if not (wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=400, detail="WhatsApp credentials missing")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/message-status"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params={"wamid": wamid, "token": wa["token"]})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("result") == "error"):
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"Status fetch failed: {detail}")
        return body.get("data") or {}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def send_whatsapp_text(wa: dict, phone: str, message: str) -> dict:
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/send-message"
    payload = {
        "from_phone_number_id": wa.get("from_phone_number_id") or "",
        "phone_number": normalise_phone(phone, wa.get("default_country_code") or "91"),
        "message_body": message,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, params={"token": wa["token"]}, json=payload)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code >= 400:
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def fetch_whatsapp_templates(wa: dict) -> Any:
    if not (wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=400, detail="Save Vendor UID and Token first")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/template-list"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, params={"token": wa["token"]})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code >= 400:
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"Template list fetch failed: {detail}")
        return body
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def send_whatsapp_document(
    wa: dict, phone: str, media_url: str, file_name: str,
    caption: Optional[str] = None,
) -> dict:
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/send-media-message"
    payload = {
        "from_phone_number_id": wa.get("from_phone_number_id") or "",
        "phone_number": normalise_phone(phone, wa.get("default_country_code") or "91"),
        "media_type": "document",
        "media_url": media_url,
        "file_name": file_name,
        "caption": caption or "",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, params={"token": wa["token"]}, json=payload)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code >= 400:
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"WhatsApp document send failed: {detail}")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


# ─────────────────── SMTP ───────────────────

def send_smtp_email(
    sm: dict, to_email: str, subject: str, body_text: str,
    attachments: Optional[List[Any]] = None, body_html: Optional[str] = None,
) -> None:
    """Synchronous SMTP send (call via run_in_executor from async)."""
    import smtplib
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr

    outer = MIMEMultipart("mixed")
    outer["Subject"] = subject
    outer["From"] = formataddr((sm.get("from_name") or "HRE Exporter", sm["from_email"]))
    outer["To"] = to_email

    if body_html:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body_text, "plain", "utf-8"))
        alt.attach(MIMEText(body_html, "html", "utf-8"))
        outer.attach(alt)
    else:
        outer.attach(MIMEText(body_text, "plain", "utf-8"))

    for path in (attachments or []):
        if not path or not path.exists():
            continue
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        outer.attach(part)
    if sm.get("use_ssl") or int(sm.get("port", 465)) == 465:
        with smtplib.SMTP_SSL(sm["host"], int(sm.get("port", 465)), timeout=30) as server:
            server.login(sm["username"], sm["password"])
            server.sendmail(sm["from_email"], [to_email], outer.as_string())
    else:
        with smtplib.SMTP(sm["host"], int(sm.get("port", 587)), timeout=30) as server:
            server.starttls()
            server.login(sm["username"], sm["password"])
            server.sendmail(sm["from_email"], [to_email], outer.as_string())


# ─────────────────── OTP delivery ───────────────────

def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def send_otp_whatsapp(wa: dict, phone: str, code: str) -> Tuple[bool, Optional[str]]:
    if not (phone and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and wa.get("otp_template_name")):
        return False, None
    try:
        await send_whatsapp_template(
            wa, phone,
            template_name=wa["otp_template_name"],
            template_language=wa.get("otp_template_language") or "en",
            field_1=code,
            button_0=code,
        )
        return True, None
    except HTTPException as e:
        logger.error(f"[OTP-WA] send failed: {e.detail}")
        return False, str(e.detail)
    except Exception as e:
        logger.exception("[OTP-WA] unexpected error")
        return False, str(e)


async def send_otp_email(sm: dict, to_email: str, code: str) -> Tuple[bool, Optional[str]]:
    if not (to_email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email")):
        return False, None
    subject = f"Your HRE Exporter verification code is {code}"
    body_text = (
        f"Your HRE Exporter verification code is: {code}\n\n"
        f"This code expires in {OTP_TTL_SECONDS // 60} minutes.\n\n"
        "If you didn't request this, please ignore this email.\n\n"
        "— HRExporter\nAn ISO 9001:2015 Certified Company"
    )
    body_html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f5f5;padding:32px 16px;margin:0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e4e4e7;">
<tr><td style="background:#1A1A1A;color:#FBAE17;padding:18px 24px;font-weight:800;letter-spacing:2px;font-size:11px;text-transform:uppercase;">HRE Exporter — Verification</td></tr>
<tr><td style="padding:32px 24px;">
<p style="margin:0 0 16px;color:#1A1A1A;font-size:14px;">Hello,</p>
<p style="margin:0 0 24px;color:#3f3f46;font-size:14px;line-height:1.55;">Use the code below to verify your phone number and continue with your quote on HRExporter.</p>
<div style="background:#FBAE17;color:#1A1A1A;font-weight:900;font-size:34px;letter-spacing:10px;text-align:center;padding:18px 12px;font-family:'Courier New',monospace;border:2px solid #1A1A1A;">{code}</div>
<p style="margin:24px 0 0;color:#71717a;font-size:12px;">This code expires in <b>{OTP_TTL_SECONDS // 60} minutes</b>. If you didn't request this, you can safely ignore this email.</p>
</td></tr>
<tr><td style="background:#fafafa;color:#a1a1aa;padding:14px 24px;font-size:11px;text-align:center;border-top:1px solid #e4e4e7;">An ISO 9001:2015 Certified Company &middot; info@hrexporter.com</td></tr>
</table></body></html>"""
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, send_smtp_email, sm, to_email, subject, body_text, None, body_html)
        return True, None
    except Exception as e:
        logger.exception("[OTP-Email] send failed")
        return False, str(e)


def otp_delivery_label(wa_ok: bool, email_ok: bool) -> str:
    if wa_ok and email_ok:
        return "whatsapp+email"
    if wa_ok:
        return "whatsapp"
    if email_ok:
        return "email"
    return "dev"
