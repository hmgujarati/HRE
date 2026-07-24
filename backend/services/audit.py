"""Audit service — one persistent record per write API call.

Design:
- FastAPI middleware wraps every /api/* request. On POST/PUT/PATCH/DELETE it
  captures user (from JWT), method, path, response status_code, latency,
  client IP, and an optional entity_id parsed from the path.
- Read requests (GET) are NOT logged — reduces noise + storage.
- Auth + webhook paths are skipped (no user context, or too chatty).
- The audit_logs collection is capped by index rotation (older than 90 days
  can be trimmed by a scheduled task; not implemented here).

The middleware does NOT block on write failures — audit is best-effort.
"""
from __future__ import annotations
import logging
import re
import time
import uuid
from typing import Optional

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from core import JWT_ALGO, JWT_SECRET, db, now_iso

logger = logging.getLogger("hre.audit")

# Skip these paths — too chatty, no user context, or already logged elsewhere.
_SKIP_PREFIXES = (
    "/api/auth/",
    "/api/webhooks/",
    "/api/uploads/",
    "/api/public/",       # public tracker / OTP — no admin user
    "/api/health/",
    "/api/settings/whatsapp/webhook-events",
)

# Only these HTTP methods are audit-worthy (writes only).
_LOGGED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _parse_entity_id(path: str) -> Optional[str]:
    """Best-effort extraction of the target entity id / order-number from the
    path. Returns the last path segment if it looks like a UUID / snowflake /
    order-number. Never raises."""
    segs = [s for s in path.split("/") if s]
    if not segs:
        return None
    last = segs[-1]
    if re.fullmatch(r"[0-9a-fA-F-]{8,}", last):
        return last
    # HRE-style numbers like HRE/QT/2026-27/0009 arrive URL-decoded here
    if "HRE" in path:
        m = re.search(r"HRE/[A-Z]+/\d{4}-\d{2}/\d+", path)
        if m:
            return m.group(0)
    return None


def _user_from_token(request: Request) -> Optional[dict]:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return {"id": payload.get("sub"), "email": payload.get("email"), "role": payload.get("role")}
    except Exception:
        return None


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.monotonic()
        response: Response = await call_next(request)
        try:
            path = request.url.path
            method = request.method
            if method not in _LOGGED_METHODS:
                return response
            if any(path.startswith(p) for p in _SKIP_PREFIXES):
                return response
            if not path.startswith("/api/"):
                return response
            user = _user_from_token(request)
            entry = {
                "id": str(uuid.uuid4()),
                "at": now_iso(),
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "entity_id": _parse_entity_id(path),
                "user_id": (user or {}).get("id"),
                "user_email": (user or {}).get("email") or "anonymous",
                "user_role": (user or {}).get("role"),
                "ip": (request.client.host if request.client else None),
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
            await db.audit_logs.insert_one(entry)
        except Exception:
            logger.exception("[audit] failed to log request")
        return response
