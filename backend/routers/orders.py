"""Orders router — full lifecycle: create-from-quote, list/get, advance, document
uploads (PO/proforma/dispatch/LR), generate proforma + tax invoice PDFs,
production updates, expected completion, refire notification, raw-material status, delete."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from core import PUBLIC_BASE_URL, UPLOAD_DIR, db, get_current_user, now_iso, require_role
from services.dispatch import (
    AUTO_NOTIFY_STAGES, ORDER_STAGES, STAGE_ORDER, STAGE_REQUIRED_DOCS,
    STAGE_TO_LABEL, _now_dt, mint_order_from_quote, missing_required_docs,
    next_invoice_number, next_order_number, next_pi_number, order_auto_notify,
    notify_production_update, persist_order_notification, save_order_doc,
    timeline_event,
)

router = APIRouter()


class OrderAdvanceIn(BaseModel):
    stage: str
    note: Optional[str] = ""


class ProductionUpdateIn(BaseModel):
    note: str


class ExpectedCompletionIn(BaseModel):
    date: Optional[str] = None  # ISO date "YYYY-MM-DD" or null to clear


class RawMaterialStatusIn(BaseModel):
    status: str  # available | procuring | procured
    note: Optional[str] = ""


@router.post("/orders/from-quote/{qid}")
async def create_order_from_quote(
    qid: str,
    data: Optional[Dict[str, Any]] = None,
    user: dict = Depends(require_role("admin", "manager")),
):
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    if quote.get("status") not in ("approved", "sent"):
        raise HTTPException(status_code=400, detail="Quote must be approved (or at least sent) before converting to order")
    # Per business rule (2026-05-11): multiple orders may be created from the
    # same quote — each gets its own sequential HRE/ORD/... number.
    po_number = (data or {}).get("po_number", "") if data else ""
    order = mint_order_from_quote(quote, user["email"], po_number=po_number)
    order["order_number"] = await next_order_number()
    await db.orders.insert_one(order.copy())
    return {k: v for k, v in order.items() if k != "_id"}


@router.get("/orders")
async def list_orders(
    stage: Optional[str] = None,
    q: Optional[str] = None,
    _: dict = Depends(require_role("admin", "manager")),
):
    query: Dict[str, Any] = {}
    if stage:
        query["stage"] = stage
    if q:
        rx = re.compile(re.escape(q), re.IGNORECASE)
        query["$or"] = [
            {"order_number": rx}, {"contact_name": rx},
            {"contact_company": rx}, {"quote_number": rx}, {"po_number": rx},
        ]
    return await db.orders.find(query, {"_id": 0}).sort("created_at", -1).limit(200).to_list(length=200)


@router.get("/orders/{oid}")
async def get_order(oid: str, _: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders/{oid}/advance")
async def advance_order_stage(oid: str, data: OrderAdvanceIn,
                                user: dict = Depends(require_role("admin", "manager"))):
    if data.stage not in STAGE_TO_LABEL:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{data.stage}'")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    missing = missing_required_docs(order, data.stage)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot move to {STAGE_TO_LABEL[data.stage]} — missing required document(s): "
                   f"{', '.join(missing)}. Please upload all required files first.",
        )
    new_stage = data.stage
    label = STAGE_TO_LABEL[new_stage]
    ev = timeline_event("stage", f"Stage → {label}", user["email"], stage=new_stage, note=data.note or "")
    update_set: Dict[str, Any] = {"stage": new_stage, "updated_at": now_iso()}
    if new_stage == "dispatched":
        update_set["dispatch.dispatched_at"] = now_iso()
    await db.orders.update_one({"id": oid}, {"$set": update_set, "$push": {"timeline": ev}})
    order_after = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await order_auto_notify(order_after, new_stage)
    if notify:
        notify["stage"] = new_stage
        notify["at"] = now_iso()
        await persist_order_notification(oid, notify)
    return await db.orders.find_one({"id": oid}, {"_id": 0})


@router.put("/orders/{oid}/expected-completion")
async def set_expected_completion(oid: str, data: ExpectedCompletionIn,
                                    user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_date = (data.date or "").strip() or None
    if new_date:
        try:
            datetime.strptime(new_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be in YYYY-MM-DD format")
    note = f"Expected completion {'set to ' + new_date if new_date else 'cleared'}"
    ev = timeline_event("note", note, user["email"])
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"expected_completion_date": new_date, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    return await db.orders.find_one({"id": oid}, {"_id": 0})


@router.post("/orders/{oid}/production-update")
async def add_production_update(oid: str, data: ProductionUpdateIn,
                                  user: dict = Depends(require_role("admin", "manager"))):
    if not data.note.strip():
        raise HTTPException(status_code=400, detail="Note is required")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    note = data.note.strip()
    import uuid as _uuid
    entry = {"id": str(_uuid.uuid4()), "note": note, "at": now_iso(), "by": user["email"]}
    ev = timeline_event("production_update", note, user["email"])
    new_stage = ("in_production" if order.get("stage") in ("order_placed", "raw_material_check", "procuring_raw_material")
                 else order["stage"])
    await db.orders.update_one(
        {"id": oid},
        {"$push": {"production_updates": entry, "timeline": ev},
         "$set": {"updated_at": now_iso(), "stage": new_stage}},
    )
    fresh = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await notify_production_update(fresh, note)
    if notify:
        await persist_order_notification(oid, notify)
        fresh = await db.orders.find_one({"id": oid}, {"_id": 0})
    return fresh


@router.post("/orders/{oid}/raw-material")
async def set_raw_material_status(oid: str, data: RawMaterialStatusIn,
                                    user: dict = Depends(require_role("admin", "manager"))):
    if data.status not in ("available", "procuring", "procured"):
        raise HTTPException(status_code=400, detail="status must be available | procuring | procured")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_stage = order["stage"]
    if data.status == "available":
        new_stage = "in_production"
    elif data.status == "procuring":
        new_stage = "procuring_raw_material"
    elif data.status == "procured":
        new_stage = "in_production"
    label = {"available": "Raw material available", "procuring": "Procuring raw material",
             "procured": "Raw material procured"}[data.status]
    ev = timeline_event("raw_material", label + (f" — {data.note}" if data.note else ""), user["email"])
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"raw_material_status": data.status, "stage": new_stage, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    if new_stage == "in_production" and order["stage"] != "in_production":
        notify = await order_auto_notify(updated, "in_production")
        if notify:
            notify["stage"] = "in_production"
            notify["at"] = now_iso()
            await persist_order_notification(oid, notify)
            updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@router.post("/orders/{oid}/upload-po")
async def upload_po(oid: str, file: UploadFile = File(...), po_number: str = "",
                     user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await save_order_doc(oid, "po", file, user["email"], {"po_number": po_number})
    ev = timeline_event("document", f"PO uploaded{(': ' + po_number) if po_number else ''}",
                          user["email"], doc_key="po")
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"documents.po": doc,
                  "po_number": po_number or order.get("po_number", ""),
                  "po_received_at": now_iso(),
                  "stage": "po_received" if order["stage"] == "pending_po" else order["stage"],
                  "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    return await db.orders.find_one({"id": oid}, {"_id": 0})


def _render_pdf_for_order(doc_src: dict, out_path, title: str):
    from quote_pdf import render_quote_pdf
    logo = UPLOAD_DIR.parent.parent / "frontend" / "public" / "hre-logo-light-bg.png"
    logo_url = logo.as_uri() if logo.exists() else None
    render_quote_pdf(doc_src, out_path, logo_url, title)


@router.post("/orders/{oid}/proforma/generate")
async def generate_proforma(oid: str, user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    pi_no = order.get("proforma", {}).get("number") or await next_pi_number()
    out_dir = UPLOAD_DIR / "orders" / oid
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", pi_no)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    out = out_dir / f"proforma_{safe}_{ts}.pdf"
    doc_src = {
        **order,
        "quote_number": pi_no,
        "created_at": now_iso(),
        "valid_until": (_now_dt() + timedelta(days=15)).date().isoformat(),
        "notes": order.get("notes") or "",
        "terms": "PAYMENT: 50% advance, 50% before dispatch.\nDelivery: 15-20 working days post advance.\nPrices are ex-works unless specified.",
    }
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _render_pdf_for_order, doc_src, out, "PROFORMA INVOICE")
    public_url = (f"{PUBLIC_BASE_URL}/api/uploads/orders/{oid}/{out.name}"
                  if PUBLIC_BASE_URL else f"/api/uploads/orders/{oid}/{out.name}")
    proforma = {
        "number": pi_no, "filename": out.name, "url": public_url,
        "generated_at": now_iso(), "generated_by": user["email"], "source": "generated",
    }
    ev = timeline_event("proforma", f"Proforma Invoice {pi_no} generated", user["email"])
    new_stage = ("proforma_issued"
                 if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("proforma_issued")
                 else order["stage"])
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"proforma": proforma, "stage": new_stage, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await order_auto_notify(updated, "proforma_issued")
    if notify:
        notify["stage"] = "proforma_issued"
        notify["at"] = now_iso()
        await persist_order_notification(oid, notify)
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@router.post("/orders/{oid}/proforma/upload")
async def upload_proforma(oid: str, file: UploadFile = File(...), pi_number: str = "",
                            user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    pi_no = pi_number or (order.get("proforma", {}).get("number")) or await next_pi_number()
    doc = await save_order_doc(oid, "proforma", file, user["email"], {"number": pi_no, "source": "uploaded"})
    proforma = {
        "number": pi_no, "filename": doc["filename"], "url": doc["url"],
        "generated_at": now_iso(), "generated_by": user["email"], "source": "uploaded",
    }
    ev = timeline_event("proforma", f"Proforma Invoice {pi_no} uploaded", user["email"])
    new_stage = ("proforma_issued"
                 if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("proforma_issued")
                 else order["stage"])
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"proforma": proforma, "stage": new_stage, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await order_auto_notify(updated, "proforma_issued")
    if notify:
        notify["stage"] = "proforma_issued"
        notify["at"] = now_iso()
        await persist_order_notification(oid, notify)
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@router.post("/orders/{oid}/invoice/generate")
async def generate_invoice(oid: str, user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    existing_inv = (order.get("documents") or {}).get("invoice") or {}
    inv_no = existing_inv.get("number") or await next_invoice_number()
    out_dir = UPLOAD_DIR / "orders" / oid
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", inv_no)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    out = out_dir / f"invoice_{safe}_{ts}.pdf"
    doc_src = {
        **order, "quote_number": inv_no, "created_at": now_iso(), "valid_until": None,
        "notes": order.get("notes") or "",
        "terms": "PAYMENT: As per Proforma Invoice and PO terms.\nPrices are inclusive of taxes as applicable.\nGoods once dispatched will not be taken back.",
    }
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _render_pdf_for_order, doc_src, out, "TAX INVOICE")
    public_url = (f"{PUBLIC_BASE_URL}/api/uploads/orders/{oid}/{out.name}"
                  if PUBLIC_BASE_URL else f"/api/uploads/orders/{oid}/{out.name}")
    invoice = {
        "filename": out.name, "original_name": out.name, "url": public_url,
        "uploaded_at": now_iso(), "uploaded_by": user["email"],
        "number": inv_no, "source": "generated",
    }
    ev = timeline_event("document", f"Tax Invoice {inv_no} generated", user["email"], doc_key="invoice")
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"documents.invoice": invoice, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    return await db.orders.find_one({"id": oid}, {"_id": 0})


@router.post("/orders/{oid}/upload-dispatch")
async def upload_dispatch_docs(
    oid: str,
    invoice: Optional[UploadFile] = File(None),
    eway_bill: Optional[UploadFile] = File(None),
    invoice_number: str = "",
    eway_bill_number: str = "",
    transporter_name: str = "",
    user: dict = Depends(require_role("admin", "manager")),
):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    existing_docs = order.get("documents") or {}
    will_have_invoice = (invoice is not None) or bool((existing_docs.get("invoice") or {}).get("filename"))
    will_have_eway = (eway_bill is not None) or bool((existing_docs.get("eway_bill") or {}).get("filename"))
    if not (will_have_invoice and will_have_eway):
        missing = []
        if not will_have_invoice: missing.append("Tax Invoice")
        if not will_have_eway: missing.append("E-way Bill")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot mark Dispatched — missing required document(s): {', '.join(missing)}. "
                   f"Please attach all dispatch documents before proceeding.",
        )
    set_ops: Dict[str, Any] = {"updated_at": now_iso()}
    events: List[dict] = []
    if invoice is not None:
        doc = await save_order_doc(oid, "invoice", invoice, user["email"], {"number": invoice_number})
        set_ops["documents.invoice"] = doc
        events.append(timeline_event("document",
                                       f"Invoice{(' ' + invoice_number) if invoice_number else ''} uploaded",
                                       user["email"], doc_key="invoice"))
    if eway_bill is not None:
        doc = await save_order_doc(oid, "eway_bill", eway_bill, user["email"], {"number": eway_bill_number})
        set_ops["documents.eway_bill"] = doc
        events.append(timeline_event("document",
                                       f"E-way Bill{(' ' + eway_bill_number) if eway_bill_number else ''} uploaded",
                                       user["email"], doc_key="eway_bill"))
    if transporter_name:
        set_ops["dispatch.transporter_name"] = transporter_name
    set_ops["dispatch.dispatched_at"] = now_iso()
    if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("dispatched"):
        set_ops["stage"] = "dispatched"
        events.append(timeline_event("stage", "Stage → Dispatched", user["email"], stage="dispatched"))
    update_doc: Dict[str, Any] = {"$set": set_ops}
    if events:
        update_doc["$push"] = {"timeline": {"$each": events}}
    await db.orders.update_one({"id": oid}, update_doc)
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})

    # Merge invoice + e-way-bill into a single PDF so it can ship as ONE
    # WhatsApp template attachment (Meta's 24-hour session policy means
    # outside-window non-template follow-ups silently fail).
    final_docs = updated.get("documents") or {}
    inv_doc = final_docs.get("invoice") or {}
    eway_doc = final_docs.get("eway_bill") or {}
    try:
        from services.dispatch import merge_pdfs_for_dispatch
        from core import UPLOAD_DIR as _UD
        paths = []
        if inv_doc.get("filename"):
            paths.append(_UD / "orders" / oid / inv_doc["filename"])
        if eway_doc.get("filename"):
            paths.append(_UD / "orders" / oid / eway_doc["filename"])
        bundle = merge_pdfs_for_dispatch(oid, paths)
        if bundle:
            await db.orders.update_one(
                {"id": oid},
                {"$set": {"documents.dispatch_bundle": bundle, "updated_at": now_iso()}},
            )
            updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    except Exception:
        # Non-fatal — fall back to sending the two attachments separately.
        pass

    notify = await order_auto_notify(updated, "dispatched")
    if notify:
        notify["stage"] = "dispatched"
        notify["at"] = now_iso()
        await persist_order_notification(oid, notify)
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@router.post("/orders/{oid}/upload-lr")
async def upload_lr(oid: str, file: UploadFile = File(...), lr_number: str = "",
                     user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await save_order_doc(oid, "lr", file, user["email"], {"number": lr_number})
    ev = timeline_event("document",
                          f"LR Copy{(' ' + lr_number) if lr_number else ''} uploaded",
                          user["email"], doc_key="lr")
    new_stage = ("lr_received"
                 if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("lr_received")
                 else order["stage"])
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"documents.lr": doc, "dispatch.lr_number": lr_number,
                  "stage": new_stage, "updated_at": now_iso()},
         "$push": {"timeline": ev}},
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await order_auto_notify(updated, "lr_received")
    if notify:
        notify["stage"] = "lr_received"
        notify["at"] = now_iso()
        await persist_order_notification(oid, notify)
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@router.post("/orders/{oid}/refire-notification")
async def refire_order_notification(oid: str, user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    notifs = order.get("notifications") or []
    if not notifs:
        raise HTTPException(status_code=400, detail="No notifications have been fired yet for this order. Advance the stage or post a production update first.")
    last = notifs[-1]
    kind = last.get("kind") or ("stage" if last.get("stage") else None)
    if kind == "production_update":
        notify = await notify_production_update(order, last.get("note") or "(repeat)")
    else:
        stage = last.get("stage") or order.get("stage")
        notify = await order_auto_notify(order, stage)
        if notify:
            notify["stage"] = stage
    if not notify:
        raise HTTPException(status_code=400, detail="Nothing to send — channels (WhatsApp + Email) are not configured. Enable them in Settings first.")
    notify["at"] = now_iso()
    notify["refire_of"] = last.get("at")
    await persist_order_notification(oid, notify)
    return await db.orders.find_one({"id": oid}, {"_id": 0})


@router.delete("/orders/{oid}")
async def delete_order(oid: str, _: dict = Depends(require_role("admin"))):
    res = await db.orders.delete_one({"id": oid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True}
