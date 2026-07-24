"""Audit log router — read-only feed of who-did-what.

GET /api/audit-logs
  Filters: ?user_email=&method=&path_contains=&since=<iso>&until=<iso>&limit=100
  Returns newest first.

Admin-only. The write side lives in services.audit.AuditMiddleware.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Query

from core import db, require_role

router = APIRouter()


@router.get("/audit-logs")
async def list_audit_logs(
    user_email: Optional[str] = None,
    method: Optional[str] = None,
    path_contains: Optional[str] = None,
    entity_id: Optional[str] = None,
    since: Optional[str] = Query(None, description="ISO 8601 timestamp — inclusive lower bound"),
    until: Optional[str] = Query(None, description="ISO 8601 timestamp — inclusive upper bound"),
    limit: int = Query(100, ge=1, le=500),
    _: dict = Depends(require_role("admin")),
):
    q: dict = {}
    if user_email:
        q["user_email"] = user_email.lower().strip()
    if method:
        q["method"] = method.upper().strip()
    if entity_id:
        q["entity_id"] = entity_id.strip()
    if path_contains:
        # Case-insensitive regex — MongoDB will use a scan (audit_logs is small enough)
        q["path"] = {"$regex": path_contains, "$options": "i"}
    if since or until:
        rng: dict = {}
        if since:
            rng["$gte"] = since
        if until:
            rng["$lte"] = until
        q["at"] = rng
    rows = await db.audit_logs.find(q, {"_id": 0}).sort("at", -1).limit(limit).to_list(limit)
    return {"rows": rows, "count": len(rows)}


@router.get("/audit-logs/summary")
async def audit_summary(_: dict = Depends(require_role("admin"))):
    """Quick counters for the Activity page header — today, last 7 days, per-user."""
    from datetime import datetime, timedelta, timezone
    today_iso = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()
    week_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    total = await db.audit_logs.count_documents({})
    today = await db.audit_logs.count_documents({"at": {"$gte": today_iso}})
    week = await db.audit_logs.count_documents({"at": {"$gte": week_iso}})
    # Top 5 users by action count in last 7 days
    pipeline = [
        {"$match": {"at": {"$gte": week_iso}}},
        {"$group": {"_id": "$user_email", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5},
    ]
    top_users = [{"user_email": r["_id"], "count": r["count"]} async for r in db.audit_logs.aggregate(pipeline)]
    return {"total": total, "today": today, "last_7_days": week, "top_users_7d": top_users}
