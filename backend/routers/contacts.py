"""Contacts router — CRM-style CRUD + quotation list per contact."""
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import db, get_current_user, now_iso, require_role
from services.contacts import find_contact_match, norm_email, norm_phone

router = APIRouter()


class ContactIn(BaseModel):
    name: str
    company: Optional[str] = ""
    phone: Optional[str] = ""
    email: Optional[str] = ""
    gst_number: Optional[str] = ""
    billing_address: Optional[str] = ""
    shipping_address: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = "India"
    source: Optional[str] = "manual"  # manual / expo / quotation / whatsapp
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = ""


@router.get("/contacts")
async def list_contacts(q: Optional[str] = None, source: Optional[str] = None,
                        _: dict = Depends(get_current_user)):
    query: Dict[str, Any] = {}
    if source:
        query["source"] = source
    if q:
        rx = re.escape(q)
        query["$or"] = [
            {"name": {"$regex": rx, "$options": "i"}},
            {"company": {"$regex": rx, "$options": "i"}},
            {"phone": {"$regex": rx, "$options": "i"}},
            {"phone_norm": {"$regex": norm_phone(q) or rx}},
            {"email": {"$regex": rx, "$options": "i"}},
        ]
    return await db.contacts.find(query, {"_id": 0}).sort("created_at", -1).to_list(2000)


@router.get("/contacts/{cid}")
async def get_contact(cid: str, _: dict = Depends(get_current_user)):
    item = await db.contacts.find_one({"id": cid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Contact not found")
    return item


@router.post("/contacts")
async def create_contact(data: ContactIn,
                         user: dict = Depends(require_role("admin", "manager"))):
    if not (data.company or "").strip():
        raise HTTPException(status_code=400, detail="Company name is required")
    if not (data.state or "").strip():
        raise HTTPException(status_code=400, detail="State is required (used for GST: CGST+SGST within Gujarat, IGST otherwise)")
    doc = data.model_dump()
    doc["phone_norm"] = norm_phone(doc.get("phone"))
    doc["email_norm"] = norm_email(doc.get("email"))
    # Smart upsert: if phone or email already present, update existing
    existing = await find_contact_match(doc.get("phone", ""), doc.get("email", ""))
    if existing:
        upd = {k: v for k, v in doc.items() if v not in (None, "", []) or k in {"tags"}}
        upd["updated_at"] = now_iso()
        await db.contacts.update_one({"id": existing["id"]}, {"$set": upd})
        return await db.contacts.find_one({"id": existing["id"]}, {"_id": 0})
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    doc["created_by"] = user["email"]
    await db.contacts.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.put("/contacts/{cid}")
async def update_contact(cid: str, data: ContactIn,
                         user: dict = Depends(require_role("admin", "manager"))):
    if not (data.company or "").strip():
        raise HTTPException(status_code=400, detail="Company name is required")
    if not (data.state or "").strip():
        raise HTTPException(status_code=400, detail="State is required (used for GST: CGST+SGST within Gujarat, IGST otherwise)")
    upd = data.model_dump()
    upd["phone_norm"] = norm_phone(upd.get("phone"))
    upd["email_norm"] = norm_email(upd.get("email"))
    upd["updated_at"] = now_iso()
    res = await db.contacts.update_one({"id": cid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    return await db.contacts.find_one({"id": cid}, {"_id": 0})


@router.delete("/contacts/{cid}")
async def delete_contact(cid: str, _: dict = Depends(require_role("admin"))):
    res = await db.contacts.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"ok": True}


@router.get("/contacts/{cid}/quotations")
async def contact_quotations(cid: str, _: dict = Depends(get_current_user)):
    return await db.quotations.find({"contact_id": cid}, {"_id": 0}).sort("created_at", -1).to_list(500)
