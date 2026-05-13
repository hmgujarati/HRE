"""Settings router — read/update integration settings + WhatsApp test/templates/sync + SMTP test."""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from core import PUBLIC_BASE_URL, SETTINGS_DOC_ID, db, now_iso, require_role
from services.integrations import (
    fetch_whatsapp_templates, get_integrations, public_integrations,
    send_smtp_email, send_whatsapp_template, send_whatsapp_text,
)
import secrets

router = APIRouter()


# ─────────────────── Pydantic models ───────────────────

class WhatsAppSettingsIn(BaseModel):
    enabled: bool = False
    api_base_url: str = "https://bizchatapi.in/api"
    vendor_uid: str = ""
    token: Optional[str] = None  # null = keep existing
    from_phone_number_id: str = ""
    otp_template_name: str = ""
    otp_template_language: str = "en"
    default_country_code: str = "91"
    quote_template_name: str = ""
    quote_template_language: str = "en"
    order_pi_template: str = ""
    order_pi_template_language: str = "en"
    order_production_template: str = ""
    order_production_template_language: str = "en"
    order_packaging_template: str = ""
    order_packaging_template_language: str = "en"
    order_dispatched_template: str = ""
    order_dispatched_template_language: str = "en"
    order_lr_template: str = ""
    order_lr_template_language: str = "en"
    order_production_update_template: str = ""
    order_production_update_template_language: str = "en"
    admin_notify_phone: str = ""
    po_received_admin_template: str = ""
    po_received_admin_template_language: str = "en"
    webhook_secret_rotate: Optional[bool] = False  # non-persisted: trigger fresh secret


class SmtpSettingsIn(BaseModel):
    enabled: bool = False
    host: str = "smtp.hostinger.com"
    port: int = 465
    use_ssl: bool = True
    username: str = ""
    password: Optional[str] = None  # null = keep existing
    from_email: str = ""
    from_name: str = "HRE Exporter"
    admin_notify_email: str = ""


class IntegrationsIn(BaseModel):
    whatsapp: Optional[WhatsAppSettingsIn] = None
    smtp: Optional[SmtpSettingsIn] = None


class WhatsAppTestIn(BaseModel):
    phone: str
    mode: str = "template"  # "template" | "text"
    message: Optional[str] = None
    sample_otp: Optional[str] = "123456"


class SmtpTestIn(BaseModel):
    to_email: EmailStr
    subject: str = "HRE Exporter SMTP test"
    body: str = "If you can read this, your Hostinger SMTP credentials are wired correctly."


# ─────────────────── Routes ───────────────────

def _with_webhook_url(resp: dict, secret: str, request: Optional[Request] = None) -> dict:
    """Build the public webhook URL. Prefers PUBLIC_BASE_URL env, falls back to
    the incoming request's host so the panel renders even on live servers
    where the env var hasn't been baked in. Always sets a value when a secret
    exists so the UI never hides the section."""
    base = PUBLIC_BASE_URL
    if not base and request is not None:
        # X-Forwarded headers from the Kubernetes/Emergent ingress let us
        # reconstruct the external HTTPS URL the customer sees.
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        if host:
            base = f"{proto}://{host}"
    if base and secret:
        resp["whatsapp"]["webhook_url"] = f"{base.rstrip('/')}/api/webhooks/bizchat/status?secret={secret}"
    else:
        resp["whatsapp"]["webhook_url"] = ""
    return resp


@router.get("/settings/integrations")
async def get_integrations_endpoint(request: Request, _: dict = Depends(require_role("admin", "manager"))):
    cur = await get_integrations()
    return _with_webhook_url(public_integrations(cur), cur["whatsapp"].get("webhook_secret", ""), request)


@router.put("/settings/integrations")
async def update_integrations(data: IntegrationsIn, request: Request, _: dict = Depends(require_role("admin"))):
    cur = await get_integrations()
    if data.whatsapp is not None:
        wa_in = data.whatsapp.model_dump()
        rotate = wa_in.pop("webhook_secret_rotate", False)
        if wa_in.get("token") in (None, ""):
            wa_in["token"] = cur["whatsapp"].get("token", "")
        cur["whatsapp"] = {**cur["whatsapp"], **wa_in}
        if rotate:
            cur["whatsapp"]["webhook_secret"] = secrets.token_urlsafe(24)
    if data.smtp is not None:
        sm_in = data.smtp.model_dump()
        if sm_in.get("password") in (None, ""):
            sm_in["password"] = cur["smtp"].get("password", "")
        cur["smtp"] = {**cur["smtp"], **sm_in}
    cur["id"] = SETTINGS_DOC_ID
    cur["updated_at"] = now_iso()
    await db.settings.update_one({"id": SETTINGS_DOC_ID}, {"$set": cur}, upsert=True)
    refreshed = await get_integrations()
    return _with_webhook_url(public_integrations(refreshed), refreshed["whatsapp"].get("webhook_secret", ""), request)


@router.post("/settings/whatsapp/test")
async def test_whatsapp_send(data: WhatsAppTestIn, _: dict = Depends(require_role("admin", "manager"))):
    cur = await get_integrations()
    wa = cur["whatsapp"]
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=400, detail="Save & enable WhatsApp settings first")
    if data.mode == "text":
        if not data.message:
            raise HTTPException(status_code=400, detail="Message required for text mode")
        body = await send_whatsapp_text(wa, data.phone, data.message)
        return {"ok": True, "mode": "text", "response": body}
    if not wa.get("otp_template_name"):
        raise HTTPException(status_code=400, detail="OTP template name not set")
    body = await send_whatsapp_template(
        wa, data.phone,
        template_name=wa["otp_template_name"],
        template_language=wa.get("otp_template_language") or "en",
        field_1=data.sample_otp or "123456",
        button_0=data.sample_otp or "123456",
    )
    return {"ok": True, "mode": "template", "response": body}


@router.get("/settings/whatsapp/templates")
async def list_whatsapp_templates(_: dict = Depends(require_role("admin", "manager"))):
    cur = await get_integrations()
    return await fetch_whatsapp_templates(cur["whatsapp"])


@router.post("/settings/whatsapp/sync-template-languages")
async def sync_whatsapp_template_languages(_: dict = Depends(require_role("admin", "manager"))):
    cur = await get_integrations()
    wa = dict(cur["whatsapp"] or {})
    body = await fetch_whatsapp_templates(wa)
    items: List = []
    if isinstance(body, dict):
        items = (((body.get("data") or {}).get("templateList") or {}).get("data") or [])
        if not items:
            items = body.get("data") if isinstance(body.get("data"), list) else []
        if not items:
            items = body.get("templates") or []
    elif isinstance(body, list):
        items = body
    name_to_lang: Dict[str, str] = {}
    for t in items:
        if not isinstance(t, dict):
            continue
        if t.get("status") and t.get("status") != "APPROVED":
            continue
        name = (t.get("template_name") or t.get("name") or "").strip()
        lang = (t.get("language") or t.get("template_language") or "").strip()
        if name and lang and name not in name_to_lang:
            name_to_lang[name] = lang
    pairs = [
        ("otp_template_name", "otp_template_language"),
        ("quote_template_name", "quote_template_language"),
        ("order_pi_template", "order_pi_template_language"),
        ("order_production_template", "order_production_template_language"),
        ("order_packaging_template", "order_packaging_template_language"),
        ("order_dispatched_template", "order_dispatched_template_language"),
        ("order_lr_template", "order_lr_template_language"),
        ("order_production_update_template", "order_production_update_template_language"),
        ("po_received_admin_template", "po_received_admin_template_language"),
    ]
    updated: Dict[str, str] = {}
    skipped: List[str] = []
    for name_key, lang_key in pairs:
        tpl = (wa.get(name_key) or "").strip()
        if not tpl:
            continue
        new_lang = name_to_lang.get(tpl)
        if not new_lang:
            skipped.append(f"{tpl} (not found in BizChat)")
            continue
        old_lang = (wa.get(lang_key) or "").strip()
        if new_lang != old_lang:
            wa[lang_key] = new_lang
            updated[lang_key] = new_lang
    if updated:
        await db.settings.update_one(
            {"id": SETTINGS_DOC_ID},
            {"$set": {f"whatsapp.{k}": v for k, v in updated.items()} | {"updated_at": now_iso()}},
        )
    return {"updated": updated, "skipped": skipped, "templates_found": len(name_to_lang)}


@router.get("/settings/whatsapp/webhook-events")
async def recent_webhook_events(_: dict = Depends(require_role("admin", "manager"))):
    """Last 20 webhook events — for debugging registration + shape verification."""
    cur = db.webhook_events.find({}, {"_id": 0}).sort("received_at", -1).limit(20)
    return await cur.to_list(length=20)


@router.post("/settings/smtp/test")
async def test_smtp_send(data: SmtpTestIn, _: dict = Depends(require_role("admin", "manager"))):
    cur = await get_integrations()
    sm = cur["smtp"]
    if not (sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email")):
        raise HTTPException(status_code=400, detail="Save & enable SMTP settings first")
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, send_smtp_email, sm, data.to_email, data.subject, data.body, None, None,
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SMTP send failed: {e}")
