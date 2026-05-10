"""Webhooks router — BizChat status (consolidated with inbound bot routing) +
BizChat dedicated inbound + email pixel/open tracking."""

from __future__ import annotations
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from core import db, now_iso
from services.integrations import get_integrations

# Bot dispatcher — already a separable module
from whatsapp_bot import dispatch as bot_dispatch, parse_inbound as bot_parse_inbound

logger = logging.getLogger("hre.webhooks")

router = APIRouter()


# 1×1 transparent GIF served by the email-open endpoint
_OPEN_PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


def _extract_status_events(payload: Any) -> List[Dict[str, Any]]:
    """Walk multiple BizChat / Meta envelope shapes and yield {wamid, status, timestamp}."""
    out: List[Dict[str, Any]] = []

    def add(wamid, status, timestamp=None):
        if wamid and status:
            out.append({"wamid": str(wamid), "status": str(status).lower(), "timestamp": timestamp})

    if not payload:
        return out
    if isinstance(payload, list):
        for p in payload:
            out.extend(_extract_status_events(p))
        return out
    if not isinstance(payload, dict):
        return out

    if payload.get("wamid") and payload.get("status"):
        add(payload.get("wamid"), payload.get("status"), payload.get("updated_at") or payload.get("timestamp"))

    data = payload.get("data")
    if isinstance(data, dict) and data.get("wamid") and data.get("status"):
        add(data.get("wamid"), data.get("status"), data.get("updated_at") or data.get("timestamp"))
    if isinstance(data, list):
        for d in data:
            if isinstance(d, dict) and d.get("wamid") and d.get("status"):
                add(d.get("wamid"), d.get("status"), d.get("updated_at") or d.get("timestamp"))

    for s in (payload.get("statuses") or []):
        if isinstance(s, dict):
            add(s.get("id") or s.get("wamid"), s.get("status"), s.get("timestamp"))

    for entry in (payload.get("entry") or []):
        for ch in (entry.get("changes") or []):
            val = ch.get("value") or {}
            for s in (val.get("statuses") or []):
                add(s.get("id") or s.get("wamid"), s.get("status"), s.get("timestamp"))

    msg = payload.get("message")
    if isinstance(msg, dict):
        wid = msg.get("whatsapp_message_id") or msg.get("wamid") or msg.get("id")
        st = msg.get("status")
        if wid and st:
            add(wid, st, msg.get("updated_at") or msg.get("timestamp"))

    inner_meta = payload.get("whatsapp_webhook_payload")
    if isinstance(inner_meta, dict):
        out.extend(_extract_status_events(inner_meta))

    inner = payload.get("payload")
    if isinstance(inner, dict):
        out.extend(_extract_status_events(inner))

    return out


@router.api_route("/webhooks/bizchat/status", methods=["GET", "POST"])
async def bizchat_status_webhook(request: Request, secret: Optional[str] = None):
    """Public webhook endpoint for BizChat push events. Requires `?secret=...`
    matching settings. GET → health check (BizChat 'Verify Webhook' button).
    POST → routes inbound customer messages through `whatsapp_bot.dispatch` and
    routes status events through quotation/order dispatch_log updates."""
    cur = await get_integrations()
    expected = cur["whatsapp"].get("webhook_secret")
    if not expected or not secret or secret != expected:
        raise HTTPException(status_code=403, detail="invalid secret")

    if request.method == "GET":
        return {"ok": True, "service": "hre-crm", "webhook": "bizchat-status"}

    try:
        raw: Any = await request.json()
    except Exception:
        body_bytes = await request.body()
        raw = {"raw": body_bytes.decode("utf-8", errors="replace")}

    events = _extract_status_events(raw)

    await db.webhook_events.insert_one({
        "id": str(uuid.uuid4()),
        "source": "bizchat",
        "kind": "message.status",
        "received_at": now_iso(),
        "parsed_events": len(events),
        "payload": raw,
    })

    # Inbound customer message routing
    body_obj = raw if isinstance(raw, dict) else {}
    msg_obj = body_obj.get("message") or {}
    looks_inbound = (
        isinstance(msg_obj, dict)
        and (msg_obj.get("is_new_message") is True)
        and (msg_obj.get("body") or msg_obj.get("interactive") or msg_obj.get("type") == "interactive")
    )
    if looks_inbound:
        try:
            msg = bot_parse_inbound(raw)
            if msg and msg.get("phone"):
                # Late import to break circular dep with server.py
                from server import _bot_finalize_quote
                bot_result = await bot_dispatch(
                    db=db, wa=cur["whatsapp"], sm=cur["smtp"], settings_doc=cur,
                    msg=msg, builder_fn=_bot_finalize_quote,
                )
                logger.info(f"[bot] handled inbound from {msg['phone']} → {bot_result.get('state')}")
                return {"ok": True, "kind": "inbound", "bot_state": bot_result.get("state")}
        except Exception:
            logger.exception("[bot] dispatch failed on status-webhook inbound")
        return {"ok": True, "kind": "inbound", "bot_state": "error"}

    # Status events: update dispatch_log on quotations / notifications on orders
    STATUS_RANK = {"accepted": 1, "sent": 2, "delivered": 3, "read": 4, "failed": 5, "pending": 0}
    updated = 0
    for ev in events:
        wamid = ev["wamid"]
        new_status = ev["status"]
        ts = ev.get("timestamp") or now_iso()
        quote = await db.quotations.find_one({"dispatch_log.wamid": wamid}, {"_id": 0, "dispatch_log": 1, "id": 1})
        if quote:
            cur_entry = next((e for e in quote.get("dispatch_log", []) if e.get("wamid") == wamid), None)
            if cur_entry and STATUS_RANK.get(new_status, 0) > STATUS_RANK.get(cur_entry.get("status", "pending"), 0):
                res = await db.quotations.update_one(
                    {"dispatch_log.wamid": wamid},
                    {"$set": {
                        "dispatch_log.$.status": new_status,
                        "dispatch_log.$.status_updated_at": ts,
                    }},
                )
                if res.modified_count:
                    updated += 1
                    logger.info(f"[WA WEBHOOK] quote wamid={wamid[:30]}… status={new_status}")
            continue
        order = await db.orders.find_one({"notifications.wamid": wamid}, {"_id": 0, "id": 1, "notifications": 1})
        if order:
            cur_entry = next((n for n in order.get("notifications", []) if n.get("wamid") == wamid), None)
            if cur_entry and STATUS_RANK.get(new_status, 0) > STATUS_RANK.get(cur_entry.get("whatsapp_status", "pending"), 0):
                res = await db.orders.update_one(
                    {"notifications.wamid": wamid},
                    {"$set": {
                        "notifications.$.whatsapp_status": new_status,
                        "notifications.$.whatsapp_status_updated_at": ts,
                    }},
                )
                if res.modified_count:
                    updated += 1
                    logger.info(f"[WA WEBHOOK] order wamid={wamid[:30]}… status={new_status}")
    return {"ok": True, "events": len(events), "updated": updated}


@router.post("/webhooks/bizchat/inbound")
async def bizchat_inbound_webhook(request: Request):
    """Dedicated inbound endpoint (legacy/shim). Routes through the same bot."""
    raw = (await request.json()
           if request.headers.get("content-type", "").startswith("application/json")
           else {"raw": (await request.body()).decode("utf-8", errors="replace")})
    try:
        await db.webhook_events.insert_one({
            "id": str(uuid.uuid4()),
            "kind": "inbound",
            "received_at": now_iso(),
            "payload": raw,
        })
    except Exception:
        pass
    msg = bot_parse_inbound(raw)
    if not msg or not msg.get("phone"):
        logger.warning(f"[bot-in] could not parse inbound: {raw}")
        return {"ok": True, "parsed": False}
    settings = await get_integrations()
    wa = settings["whatsapp"]
    sm = settings["smtp"]
    if not (wa.get("vendor_uid") and wa.get("token")):
        logger.error("[bot-in] WhatsApp not configured; ignoring inbound")
        return {"ok": True, "parsed": True, "skipped": "wa_not_configured"}
    try:
        from server import _bot_finalize_quote
        result = await bot_dispatch(
            db=db, wa=wa, sm=sm, settings_doc=settings,
            msg=msg, builder_fn=_bot_finalize_quote,
        )
        return {"ok": True, "parsed": True, "result": result}
    except Exception as e:
        logger.exception("[bot-in] dispatch failed")
        return {"ok": True, "parsed": True, "error": str(e)}


@router.api_route("/webhooks/email/open", methods=["GET", "HEAD"])
async def email_open_tracking(t: Optional[str] = None):
    """Invisible 1×1 GIF pixel. Loading it marks the matching dispatch_log /
    order-notification entry as `read`."""
    if t:
        try:
            quote = await db.quotations.find_one(
                {"dispatch_log.open_token": t},
                {"_id": 0, "id": 1, "dispatch_log": 1},
            )
            if quote:
                entry = next((e for e in quote.get("dispatch_log", []) if e.get("open_token") == t), None)
                if entry and entry.get("status") == "sent":
                    await db.quotations.update_one(
                        {"id": quote["id"], "dispatch_log.open_token": t},
                        {"$set": {
                            "dispatch_log.$.status": "read",
                            "dispatch_log.$.status_updated_at": now_iso(),
                        }},
                    )
                    logger.info(f"[EMAIL OPEN] quote={quote['id']} token={t[:10]}… → read")
            else:
                order = await db.orders.find_one(
                    {"notifications.email_open_token": t},
                    {"_id": 0, "id": 1, "notifications": 1},
                )
                if order:
                    entry = next((n for n in order.get("notifications", []) if n.get("email_open_token") == t), None)
                    if entry and entry.get("email_status") in ("sent", None):
                        await db.orders.update_one(
                            {"id": order["id"], "notifications.email_open_token": t},
                            {"$set": {
                                "notifications.$.email_status": "read",
                                "notifications.$.email_status_updated_at": now_iso(),
                            }},
                        )
                        logger.info(f"[EMAIL OPEN] order={order['id']} token={t[:10]}… → read")
        except Exception:
            logger.exception("[EMAIL OPEN] failed to process")
    return Response(
        content=_OPEN_PIXEL_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
