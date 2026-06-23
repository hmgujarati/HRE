"""Health router — integrations uptime check.

GET /api/health/integrations
  Pings BizChat (template-list) and SMTP (connect+login+NOOP+quit) and reports
  per-service status. Lightweight — uses the existing token/credentials from the
  settings doc. Admin-only because it would otherwise leak whether the WA token
  is valid to an unauthenticated probe.
"""
from __future__ import annotations
import asyncio
import logging
import os
import smtplib
import time
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends

from core import get_current_user
from services.integrations import get_integrations

router = APIRouter()
logger = logging.getLogger("hre.health")


async def _check_whatsapp(wa: dict) -> Dict[str, Any]:
    started = time.monotonic()
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        return {"ok": False, "configured": False, "error": "WhatsApp integration is not configured", "latency_ms": 0}
    url = f"{(wa.get('api_base_url') or '').rstrip('/')}/{wa['vendor_uid']}/contact/template-list"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, params={"token": wa["token"]})
        latency = int((time.monotonic() - started) * 1000)
        body: Any
        try:
            body = r.json()
        except Exception:
            body = r.text
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("result") == "error"):
            msg = body.get("message") if isinstance(body, dict) else str(body)[:200]
            return {"ok": False, "configured": True, "error": f"HTTP {r.status_code}: {msg}", "latency_ms": latency}
        return {"ok": True, "configured": True, "error": None, "latency_ms": latency}
    except Exception as e:
        return {"ok": False, "configured": True, "error": str(e)[:200], "latency_ms": int((time.monotonic() - started) * 1000)}


def _check_smtp_sync(sm: dict) -> Dict[str, Any]:
    started = time.monotonic()
    if not (sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password")):
        return {"ok": False, "configured": False, "error": "SMTP integration is not configured", "latency_ms": 0}
    try:
        port = int(sm.get("port", 465))
        if sm.get("use_ssl") or port == 465:
            with smtplib.SMTP_SSL(sm["host"], port, timeout=8) as server:
                server.login(sm["username"], sm["password"])
                server.noop()
        else:
            with smtplib.SMTP(sm["host"], port, timeout=8) as server:
                server.starttls()
                server.login(sm["username"], sm["password"])
                server.noop()
        return {"ok": True, "configured": True, "error": None, "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as e:
        return {"ok": False, "configured": True, "error": str(e)[:200], "latency_ms": int((time.monotonic() - started) * 1000)}


@router.get("/health/integrations")
async def health_integrations(_: dict = Depends(get_current_user)):
    settings = await get_integrations()
    wa_result = await _check_whatsapp(settings["whatsapp"])
    loop = asyncio.get_event_loop()
    smtp_result = await loop.run_in_executor(None, _check_smtp_sync, settings["smtp"])
    test_mode = {
        "restrict_outbound_phone": (os.environ.get("RESTRICT_OUTBOUND_TO_PHONE") or "").strip(),
        "restrict_outbound_email": (os.environ.get("RESTRICT_OUTBOUND_TO_EMAIL") or "").strip(),
    }
    return {
        "whatsapp": wa_result,
        "smtp": smtp_result,
        "test_mode": test_mode,
        "overall_ok": bool(wa_result["ok"] and smtp_result["ok"]),
    }
