"""Quotations router — full CRUD + revisions + dispatch + delivery refresh + PDF + stats + convert-to-order."""
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import db, get_current_user, now_iso, require_role
from services.dispatch import dispatch_finalised_quote, generate_quote_pdf
from services.integrations import get_integrations, get_whatsapp_message_status
from services.quote_helpers import compute_quote_totals, fy_label, next_quote_number

router = APIRouter()


# ─────────────────── Pydantic models ───────────────────

class QuoteLineIn(BaseModel):
    product_variant_id: Optional[str] = None
    product_code: str
    family_name: Optional[str] = ""
    description: Optional[str] = ""
    cable_size: Optional[str] = ""
    hole_size: Optional[str] = ""
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    hsn_code: Optional[str] = "85369090"
    quantity: float = 1
    unit: Optional[str] = "NOS"
    base_price: float = 0.0
    discount_percentage: float = 0.0
    gst_percentage: float = 18.0


class QuoteIn(BaseModel):
    contact_id: str
    place_of_supply: Optional[str] = ""
    valid_until: Optional[str] = None  # ISO date
    notes: Optional[str] = ""
    terms: Optional[str] = ""
    line_items: List[QuoteLineIn] = Field(default_factory=list)


# ─────────────────── Routes ───────────────────

@router.get("/quotations")
async def list_quotations(
    status_filter: Optional[str] = None,
    contact_id: Optional[str] = None,
    q: Optional[str] = None,
    _: dict = Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if status_filter:
        query["status"] = status_filter
    if contact_id:
        query["contact_id"] = contact_id
    if q:
        query["$or"] = [
            {"quote_number": {"$regex": q, "$options": "i"}},
            {"contact_name": {"$regex": q, "$options": "i"}},
            {"contact_company": {"$regex": q, "$options": "i"}},
        ]
    return await db.quotations.find(query, {"_id": 0}).sort("created_at", -1).to_list(2000)


@router.get("/quotations/next-number")
async def quote_next_number(_: dict = Depends(get_current_user)):
    fy = fy_label(datetime.now(timezone.utc))
    counter = await db.counters.find_one({"_id": f"quote_seq_{fy}"}, {"_id": 0})
    nxt = (counter.get("seq", 0) + 1) if counter else 1
    return {"preview": f"HRE/QT/{fy}/{nxt:04d}"}


@router.get("/quotations/{qid}")
async def get_quotation(qid: str, _: dict = Depends(get_current_user)):
    item = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return item


@router.post("/quotations")
async def create_quotation(data: QuoteIn,
                            user: dict = Depends(require_role("admin", "manager"))):
    contact = await db.contacts.find_one({"id": data.contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    line_items = [li.model_dump() for li in data.line_items]
    totals = compute_quote_totals(line_items)
    qnum = await next_quote_number()
    doc = {
        "id": str(uuid.uuid4()),
        "quote_number": qnum,
        "version": 1,
        "parent_quote_id": None,
        "status": "draft",
        "contact_id": contact["id"],
        "contact_name": contact.get("name", ""),
        "contact_company": contact.get("company", ""),
        "contact_email": contact.get("email", ""),
        "contact_phone": contact.get("phone", ""),
        "contact_gst": contact.get("gst_number", ""),
        "billing_address": contact.get("billing_address", ""),
        "shipping_address": contact.get("shipping_address", ""),
        "place_of_supply": data.place_of_supply or contact.get("state", ""),
        "currency": "INR",
        "valid_until": data.valid_until,
        "notes": data.notes or "",
        "terms": data.terms or "",
        "line_items": line_items,
        **totals,
        "created_by": user["email"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "sent_at": None,
        "approved_at": None,
        "rejected_at": None,
    }
    await db.quotations.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.put("/quotations/{qid}")
async def update_quotation(qid: str, data: QuoteIn,
                            user: dict = Depends(require_role("admin", "manager"))):
    existing = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Quotation not found")
    if existing.get("status") in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot edit {existing['status']} quote — use Revise instead")
    contact = await db.contacts.find_one({"id": data.contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    line_items = [li.model_dump() for li in data.line_items]
    totals = compute_quote_totals(line_items)
    upd = {
        "contact_id": contact["id"],
        "contact_name": contact.get("name", ""),
        "contact_company": contact.get("company", ""),
        "contact_email": contact.get("email", ""),
        "contact_phone": contact.get("phone", ""),
        "contact_gst": contact.get("gst_number", ""),
        "billing_address": contact.get("billing_address", ""),
        "shipping_address": contact.get("shipping_address", ""),
        "place_of_supply": data.place_of_supply or contact.get("state", ""),
        "valid_until": data.valid_until,
        "notes": data.notes or "",
        "terms": data.terms or "",
        "line_items": line_items,
        **totals,
        "updated_at": now_iso(),
    }
    await db.quotations.update_one({"id": qid}, {"$set": upd})
    return await db.quotations.find_one({"id": qid}, {"_id": 0})


@router.patch("/quotations/{qid}/status")
async def change_quote_status(qid: str, payload: dict,
                                user: dict = Depends(require_role("admin", "manager"))):
    new_status = payload.get("status")
    if new_status not in ("draft", "sent", "approved", "rejected", "expired"):
        raise HTTPException(status_code=400, detail="Invalid status")
    existing = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Quotation not found")
    upd: Dict[str, Any] = {"status": new_status, "updated_at": now_iso()}
    if new_status == "sent":
        upd["sent_at"] = now_iso()
    elif new_status == "approved":
        upd["approved_at"] = now_iso()
    elif new_status == "rejected":
        upd["rejected_at"] = now_iso()
    await db.quotations.update_one({"id": qid}, {"$set": upd})
    return await db.quotations.find_one({"id": qid}, {"_id": 0})


@router.post("/quotations/{qid}/revise")
async def revise_quotation(qid: str,
                            user: dict = Depends(require_role("admin", "manager"))):
    """Mark current as revised, clone it as a new draft v(N+1)."""
    src = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not src:
        raise HTTPException(status_code=404, detail="Quotation not found")
    if src.get("status") == "revised":
        raise HTTPException(status_code=400, detail="This quote has already been revised — open the latest revision instead")
    new_doc = {**src}
    new_doc["id"] = str(uuid.uuid4())
    new_doc["version"] = (src.get("version") or 1) + 1
    new_doc["parent_quote_id"] = src["id"]
    base_number = re.sub(r"-R\d+$", "", src["quote_number"])
    new_doc["quote_number"] = f"{base_number}-R{new_doc['version']}"
    new_doc["status"] = "draft"
    new_doc["created_by"] = user["email"]
    new_doc["created_at"] = now_iso()
    new_doc["updated_at"] = now_iso()
    new_doc["sent_at"] = None
    new_doc["approved_at"] = None
    new_doc["rejected_at"] = None
    await db.quotations.insert_one(new_doc.copy())
    await db.quotations.update_one({"id": src["id"]}, {"$set": {"status": "revised", "updated_at": now_iso()}})
    new_doc.pop("_id", None)
    return new_doc


@router.delete("/quotations/{qid}")
async def delete_quotation(qid: str, _: dict = Depends(require_role("admin"))):
    res = await db.quotations.delete_one({"id": qid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return {"ok": True}


@router.post("/quotations/{qid}/send")
async def send_quotation_dispatch(qid: str, _: dict = Depends(require_role("admin", "manager"))):
    """Manually generate the PDF and dispatch to customer via WhatsApp + Email."""
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")
    delivery = await dispatch_finalised_quote(quote)
    pdf_ok = bool(delivery.get("pdf"))
    channel_ok = bool(delivery.get("whatsapp") or delivery.get("email"))
    if pdf_ok or channel_ok:
        await db.quotations.update_one(
            {"id": qid},
            {"$set": {"sent_at": now_iso(),
                      "status": "sent" if quote.get("status") == "draft" else quote.get("status")}},
        )
    return delivery


@router.post("/quotations/{qid}/refresh-delivery")
async def refresh_quotation_delivery(qid: str,
                                      _: dict = Depends(require_role("admin", "manager"))):
    """Poll BizChat message-status for every WhatsApp dispatch log entry that isn't
    in a terminal state yet and update the stored status in-place."""
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")
    cur = await get_integrations()
    wa = cur["whatsapp"]
    log = list(quote.get("dispatch_log") or [])
    TERMINAL = {"read", "failed"}
    updates = 0
    for entry in log:
        if entry.get("channel") != "whatsapp":
            continue
        if entry.get("status") in TERMINAL:
            continue
        wamid = entry.get("wamid")
        if not wamid:
            continue
        try:
            data = await get_whatsapp_message_status(wa, wamid)
            new_status = (data or {}).get("status")
            if new_status and new_status != entry.get("status"):
                entry["status"] = new_status
                entry["status_updated_at"] = (data or {}).get("updated_at") or now_iso()
                updates += 1
        except HTTPException as e:
            entry["status_error"] = str(e.detail)
        except Exception as e:
            entry["status_error"] = str(e)
    if updates or any("status_error" in e for e in log):
        await db.quotations.update_one({"id": qid}, {"$set": {"dispatch_log": log}})
    updated = await db.quotations.find_one({"id": qid}, {"_id": 0})
    return {"updates": updates, "dispatch_log": (updated or {}).get("dispatch_log", [])}


@router.get("/quotations/{qid}/pdf")
async def get_quotation_pdf(qid: str, _: dict = Depends(require_role("admin", "manager"))):
    """Render (or re-render) the quotation PDF and return its public path."""
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")
    pdf = await generate_quote_pdf(quote)
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@router.get("/dashboard/quote-stats")
async def quote_stats(_: dict = Depends(get_current_user)):
    statuses = ["draft", "sent", "approved", "rejected", "revised", "expired"]
    counts = {}
    for s in statuses:
        counts[s] = await db.quotations.count_documents({"status": s})
    pipeline_total = await db.quotations.aggregate([
        {"$match": {"status": {"$in": ["draft", "sent"]}}},
        {"$group": {"_id": None, "value": {"$sum": "$grand_total"}}},
    ]).to_list(1)
    won_total = await db.quotations.aggregate([
        {"$match": {"status": "approved"}},
        {"$group": {"_id": None, "value": {"$sum": "$grand_total"}}},
    ]).to_list(1)
    return {
        "counts": counts,
        "pipeline_value": (pipeline_total[0]["value"] if pipeline_total else 0),
        "won_value": (won_total[0]["value"] if won_total else 0),
        "total_contacts": await db.contacts.count_documents({}),
    }


@router.post("/quotations/{qid}/convert-to-order")
async def quote_convert_to_order(
    qid: str,
    data: Optional[Dict[str, Any]] = None,
    user: dict = Depends(require_role("admin", "manager")),
):
    """Convenience alias used by the Quotation detail page — delegates to the
    canonical order-minting handler now in routers/orders.py."""
    from routers.orders import create_order_from_quote
    return await create_order_from_quote(qid, data, user)
