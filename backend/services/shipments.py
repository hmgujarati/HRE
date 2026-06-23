"""Shipments — Phase 3.

A shipment groups one or more line items from an order that physically go out
together with one Tax Invoice + one E-Way Bill + one LR Copy. Each shipment
has its own stage (`created → invoiced → dispatched → delivered`). When a
shipment is dispatched/delivered the picked lines on the parent order auto-
flip their `qty_status` accordingly. The order's high-level `stage` is
recomputed from the union of all shipments + lines.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from core import PUBLIC_BASE_URL, UPLOAD_DIR, db, now_iso
from services.dispatch import _now_dt, timeline_event

logger = logging.getLogger(__name__)

SHIPMENT_STAGES = ["created", "invoiced", "dispatched", "delivered"]
SHIP_DOC_KEYS = {"tax_invoice", "eway_bill", "lr_copy"}
SHIP_DOC_REQUIRED_FOR_DISPATCH = {"tax_invoice", "eway_bill"}  # LR optional at dispatch


# ─────────────────── Shipment construction ───────────────────

def _next_shipment_number(order: Dict[str, Any]) -> str:
    """`HRE/ORD/2026-27/0042-S1` style — sequence index per order."""
    existing = order.get("shipments") or []
    return f"{order['order_number']}-S{len(existing) + 1}"


def make_shipment_draft(order: Dict[str, Any], line_indexes: List[int],
                        user_email: str,
                        transporter_name: str = "",
                        lr_number: str = "",
                        expected_delivery_date: Optional[str] = None) -> Dict[str, Any]:
    items = order.get("line_items") or []
    if not line_indexes:
        raise HTTPException(status_code=400, detail="Pick at least one line item")
    # De-duplicate + bounds-check
    line_indexes = sorted(set(line_indexes))
    for idx in line_indexes:
        if idx < 0 or idx >= len(items):
            raise HTTPException(status_code=400, detail=f"Line index {idx} out of range")
    # No double-booking: a line cannot belong to two non-deleted shipments
    already_in = set()
    for s in (order.get("shipments") or []):
        if s.get("stage") in {"dispatched", "delivered", "invoiced", "created"}:
            for li in (s.get("line_indexes") or []):
                already_in.add(li)
    clash = [i for i in line_indexes if i in already_in]
    if clash:
        codes = ", ".join((items[i].get("product_code") or f"line {i}") for i in clash)
        raise HTTPException(status_code=400, detail=f"These items are already in another shipment: {codes}")
    return {
        "id": uuid.uuid4().hex,
        "shipment_number": _next_shipment_number(order),
        "stage": "created",
        "line_indexes": line_indexes,
        "transporter_name": transporter_name,
        "lr_number": lr_number,
        "invoice_number": "",  # admin types this manually
        "expected_delivery_date": expected_delivery_date or None,
        "dispatched_at": None,
        "delivered_at": None,
        "documents": {},       # populated by upload endpoint
        "timeline": [
            {"kind": "created", "at": now_iso(), "by": user_email,
             "summary": f"Shipment drafted with {len(line_indexes)} line(s)"},
        ],
        "created_at": now_iso(),
        "created_by": user_email,
        "updated_at": now_iso(),
    }


# ─────────────────── File uploads ───────────────────

async def save_shipment_doc(oid: str, sid: str, doc_key: str,
                             file: UploadFile, user_email: str) -> Dict[str, Any]:
    """Persist under /uploads/orders/{oid}/shipments/{sid}/{doc_key}_..."""
    if doc_key not in SHIP_DOC_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown doc key '{doc_key}'")
    out_dir = UPLOAD_DIR / "orders" / oid / "shipments" / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", (file.filename or doc_key).rsplit(".", 1)[0])
    ext = (file.filename.rsplit(".", 1)[-1] if (file.filename and "." in file.filename) else "bin").lower()
    safe_name = f"{doc_key}_{safe_stem}_{ts}.{ext}"
    out = out_dir / safe_name
    content = await file.read()
    out.write_bytes(content)
    rel_url = f"/api/uploads/orders/{oid}/shipments/{sid}/{safe_name}"
    public_url = f"{PUBLIC_BASE_URL.rstrip('/')}{rel_url}" if PUBLIC_BASE_URL else rel_url
    return {
        "filename": safe_name,
        "original_name": file.filename or "",
        "url": public_url,
        "uploaded_at": now_iso(),
        "uploaded_by": user_email,
    }


# ─────────────────── Order rollup ───────────────────

def shipment_summary(order: Dict[str, Any]) -> Dict[str, Any]:
    """Derived summary used by the order detail + customer views. Never written
    to the order doc — recomputed on read. Keeps existing `order.stage` logic
    untouched while still letting the UI show 'Shipment 2 of 3 dispatched'."""
    shipments = order.get("shipments") or []
    items = order.get("line_items") or []
    total = len(shipments)
    dispatched = sum(1 for s in shipments if s.get("stage") in {"dispatched", "delivered"})
    delivered = sum(1 for s in shipments if s.get("stage") == "delivered")
    in_some = set()
    for s in shipments:
        for li in (s.get("line_indexes") or []):
            in_some.add(li)
    lines_not_in_shipment = max(len(items) - len(in_some), 0)
    if total == 0:
        label = "No shipments yet"
    elif delivered == total and lines_not_in_shipment == 0:
        label = "All shipments delivered"
    elif dispatched == total and lines_not_in_shipment == 0:
        label = "All shipments dispatched"
    elif dispatched > 0:
        label = f"{dispatched} of {total} dispatched · {lines_not_in_shipment} item(s) still pending"
    else:
        label = f"{total} shipment(s) being prepared"
    return {
        "total": total,
        "dispatched": dispatched,
        "delivered": delivered,
        "lines_not_in_shipment": lines_not_in_shipment,
        "label": label,
    }


def apply_shipment_stage_to_lines(items: List[Dict[str, Any]],
                                   line_indexes: List[int],
                                   new_qty_status: str) -> List[Dict[str, Any]]:
    """Flip qty_status on the picked lines (in-place safe; returns new list)."""
    out = list(items)
    ts = now_iso()
    for i in line_indexes:
        if 0 <= i < len(out):
            li = dict(out[i])
            li["qty_status"] = new_qty_status
            li["status_updated_at"] = ts
            out[i] = li
    return out
