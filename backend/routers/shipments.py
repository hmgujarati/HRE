"""Shipments router — Phase 3 of the order workflow."""
from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core import db, now_iso, require_role
from services.dispatch import normalize_line_items, timeline_event
from services.shipments import (
    SHIP_DOC_KEYS, SHIP_DOC_REQUIRED_FOR_DISPATCH, SHIPMENT_STAGES,
    apply_shipment_stage_to_lines, make_shipment_draft, save_shipment_doc,
)
from services.universal_update import auto_send_preset

router = APIRouter()


class ShipmentCreateIn(BaseModel):
    line_indexes: List[int] = Field(..., min_length=1)
    transporter_name: str = ""
    lr_number: str = ""
    expected_delivery_date: Optional[str] = None  # YYYY-MM-DD


class ShipmentPatchIn(BaseModel):
    transporter_name: Optional[str] = None
    lr_number: Optional[str] = None
    invoice_number: Optional[str] = None
    expected_delivery_date: Optional[str] = None


def _ymd(d: Optional[str]) -> Optional[str]:
    if d is None or d == "":
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    return d


async def _load_order(oid: str) -> dict:
    o = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    o["line_items"] = normalize_line_items(o.get("line_items") or [])
    o["shipments"] = o.get("shipments") or []
    return o


def _find_shipment(order: dict, sid: str) -> dict:
    for s in order.get("shipments") or []:
        if s["id"] == sid:
            return s
    raise HTTPException(status_code=404, detail="Shipment not found")


# ─────────────────── Endpoints ───────────────────

@router.post("/orders/{oid}/shipments")
async def create_shipment(oid: str, data: ShipmentCreateIn,
                          user: dict = Depends(require_role("admin", "manager"))):
    order = await _load_order(oid)
    draft = make_shipment_draft(
        order=order,
        line_indexes=data.line_indexes,
        user_email=user["email"],
        transporter_name=data.transporter_name.strip(),
        lr_number=data.lr_number.strip(),
        expected_delivery_date=_ymd(data.expected_delivery_date),
    )
    summary = f"{draft['shipment_number']}: created with {len(draft['line_indexes'])} line(s)"
    ev = timeline_event("shipment_created", summary, user["email"], shipment_id=draft["id"])
    await db.orders.update_one(
        {"id": oid},
        {"$push": {"shipments": draft, "timeline": ev}, "$set": {"updated_at": now_iso()}},
    )
    return await _load_order(oid)


@router.patch("/orders/{oid}/shipments/{sid}")
async def patch_shipment(oid: str, sid: str, data: ShipmentPatchIn,
                         user: dict = Depends(require_role("admin", "manager"))):
    order = await _load_order(oid)
    s = _find_shipment(order, sid)
    if s["stage"] not in {"created", "invoiced"}:
        raise HTTPException(status_code=400, detail="Shipment fields can only be edited before dispatch")
    incoming = data.model_dump(exclude_unset=True)
    if "expected_delivery_date" in incoming:
        incoming["expected_delivery_date"] = _ymd(incoming["expected_delivery_date"])
    s.update({k: v for k, v in incoming.items() if v is not None or k == "expected_delivery_date"})
    s["updated_at"] = now_iso()
    await db.orders.update_one(
        {"id": oid, "shipments.id": sid},
        {"$set": {"shipments.$": s, "updated_at": now_iso()}},
    )
    return await _load_order(oid)


@router.post("/orders/{oid}/shipments/{sid}/upload")
async def upload_shipment_doc(oid: str, sid: str,
                               doc_key: str = Form(...),
                               file: UploadFile = File(...),
                               user: dict = Depends(require_role("admin", "manager"))):
    if doc_key not in SHIP_DOC_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown doc key '{doc_key}'")
    order = await _load_order(oid)
    s = _find_shipment(order, sid)
    saved = await save_shipment_doc(oid, sid, doc_key, file, user["email"])
    s["documents"] = {**(s.get("documents") or {}), doc_key: saved}
    if doc_key == "tax_invoice" and s["stage"] == "created":
        s["stage"] = "invoiced"
    s["updated_at"] = now_iso()
    ev = timeline_event("shipment_doc_uploaded", f"{s['shipment_number']}: {doc_key} uploaded",
                        user["email"], shipment_id=sid, doc_key=doc_key)
    await db.orders.update_one(
        {"id": oid, "shipments.id": sid},
        {"$set": {"shipments.$": s, "updated_at": now_iso()}, "$push": {"timeline": ev}},
    )
    return await _load_order(oid)


class DispatchIn(BaseModel):
    transporter_name: Optional[str] = None
    lr_number: Optional[str] = None
    invoice_number: Optional[str] = None
    expected_delivery_date: Optional[str] = None
    dispatched_on: Optional[str] = None  # YYYY-MM-DD; defaults to today


@router.post("/orders/{oid}/shipments/{sid}/dispatch")
async def dispatch_shipment(oid: str, sid: str, data: DispatchIn,
                             user: dict = Depends(require_role("admin", "manager"))):
    order = await _load_order(oid)
    s = _find_shipment(order, sid)
    if s["stage"] in {"dispatched", "delivered"}:
        raise HTTPException(status_code=400, detail="Shipment is already dispatched")
    # Apply any last-minute field updates from the dispatch form
    incoming = data.model_dump(exclude_unset=True)
    for k in ("transporter_name", "lr_number", "invoice_number"):
        if incoming.get(k):
            s[k] = incoming[k].strip() if isinstance(incoming[k], str) else incoming[k]
    if "expected_delivery_date" in incoming:
        s["expected_delivery_date"] = _ymd(incoming["expected_delivery_date"])
    # Required-docs gate (tax invoice + e-way bill must be uploaded)
    docs = s.get("documents") or {}
    missing = [k for k in SHIP_DOC_REQUIRED_FOR_DISPATCH if not (docs.get(k) and docs[k].get("url"))]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot dispatch: missing documents — {', '.join(missing)}",
        )
    s["stage"] = "dispatched"
    s["dispatched_at"] = (_ymd(incoming.get("dispatched_on")) or now_iso())
    s["updated_at"] = now_iso()
    # Flip picked lines on the parent order to `shipped`
    items = apply_shipment_stage_to_lines(order["line_items"], s["line_indexes"], "shipped")
    ev = timeline_event("shipment_dispatched", f"{s['shipment_number']}: dispatched",
                        user["email"], shipment_id=sid)
    # Update the shipment + line_items in one pass
    await db.orders.update_one(
        {"id": oid, "shipments.id": sid},
        {"$set": {"shipments.$": s, "line_items": items, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    # ─── Auto-notify customer that the shipment is on its way ───
    fresh = await _load_order(oid)
    fresh_shipment = _find_shipment(fresh, sid)
    await auto_send_preset(
        oid, "shipment_dispatched", fresh, shipment=fresh_shipment,
        triggered_by=f"shipment_dispatched:{sid}",
    )
    return await _load_order(oid)


class DeliverIn(BaseModel):
    delivered_on: Optional[str] = None  # YYYY-MM-DD


@router.post("/orders/{oid}/shipments/{sid}/deliver")
async def deliver_shipment(oid: str, sid: str, data: DeliverIn,
                            user: dict = Depends(require_role("admin", "manager"))):
    order = await _load_order(oid)
    s = _find_shipment(order, sid)
    if s["stage"] != "dispatched":
        raise HTTPException(status_code=400, detail="Only dispatched shipments can be marked delivered")
    s["stage"] = "delivered"
    s["delivered_at"] = (_ymd((data.model_dump() or {}).get("delivered_on")) or now_iso())
    s["updated_at"] = now_iso()
    items = apply_shipment_stage_to_lines(order["line_items"], s["line_indexes"], "delivered")
    ev = timeline_event("shipment_delivered", f"{s['shipment_number']}: delivered",
                        user["email"], shipment_id=sid)
    await db.orders.update_one(
        {"id": oid, "shipments.id": sid},
        {"$set": {"shipments.$": s, "line_items": items, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    # ─── Auto-notify customer that the shipment is delivered ───
    fresh = await _load_order(oid)
    fresh_shipment = _find_shipment(fresh, sid)
    await auto_send_preset(
        oid, "shipment_delivered", fresh, shipment=fresh_shipment,
        triggered_by=f"shipment_delivered:{sid}",
    )
    return await _load_order(oid)


@router.delete("/orders/{oid}/shipments/{sid}")
async def delete_shipment(oid: str, sid: str,
                           user: dict = Depends(require_role("admin", "manager"))):
    order = await _load_order(oid)
    s = _find_shipment(order, sid)
    if s["stage"] not in {"created", "invoiced"}:
        raise HTTPException(status_code=400, detail="Only draft / invoiced shipments can be deleted")
    ev = timeline_event("shipment_deleted", f"{s['shipment_number']}: deleted",
                        user["email"], shipment_id=sid)
    await db.orders.update_one(
        {"id": oid},
        {"$pull": {"shipments": {"id": sid}}, "$push": {"timeline": ev},
         "$set": {"updated_at": now_iso()}},
    )
    return {"ok": True}
