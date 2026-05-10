"""Product Variants router — CRUD + price history."""
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core import (
    ProductVariantIn, calc_final_price, db, get_current_user, now_iso, require_role,
)
from services.pricing import record_price_history

router = APIRouter()


@router.get("/product-variants")
async def list_variants(
    material_id: Optional[str] = None,
    category_id: Optional[str] = None,
    product_family_id: Optional[str] = None,
    active: Optional[bool] = None,
    q: Optional[str] = None,
    _: dict = Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if material_id:
        query["material_id"] = material_id
    if category_id:
        query["category_id"] = category_id
    if product_family_id:
        query["product_family_id"] = product_family_id
    if active is not None:
        query["active"] = active
    if q:
        query["$or"] = [
            {"product_code": {"$regex": q, "$options": "i"}},
            {"product_name": {"$regex": q, "$options": "i"}},
        ]
    return await db.product_variants.find(query, {"_id": 0}).sort("product_code", 1).to_list(5000)


@router.get("/product-variants/{vid}")
async def get_variant(vid: str, _: dict = Depends(get_current_user)):
    item = await db.product_variants.find_one({"id": vid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Variant not found")
    return item


@router.post("/product-variants")
async def create_variant(data: ProductVariantIn,
                         user: dict = Depends(require_role("admin", "manager"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["final_price"] = calc_final_price(
        doc["base_price"], doc["discount_percentage"],
        doc["manual_price_override"], doc.get("manual_price"),
    )
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.product_variants.insert_one(doc.copy())
    doc.pop("_id", None)
    await record_price_history({}, doc, user["email"], "Variant created")
    return doc


@router.put("/product-variants/{vid}")
async def update_variant(vid: str, data: ProductVariantIn,
                         user: dict = Depends(require_role("admin", "manager"))):
    before = await db.product_variants.find_one({"id": vid}, {"_id": 0})
    if not before:
        raise HTTPException(status_code=404, detail="Variant not found")
    upd = data.model_dump()
    upd["final_price"] = calc_final_price(
        upd["base_price"], upd["discount_percentage"],
        upd["manual_price_override"], upd.get("manual_price"),
    )
    upd["updated_at"] = now_iso()
    await db.product_variants.update_one({"id": vid}, {"$set": upd})
    after = await db.product_variants.find_one({"id": vid}, {"_id": 0})
    await record_price_history(before, after, user["email"], "Variant updated")
    return after


@router.delete("/product-variants/{vid}")
async def delete_variant(vid: str, user: dict = Depends(require_role("admin"))):
    res = await db.product_variants.delete_one({"id": vid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"ok": True}


@router.get("/product-variants/{vid}/price-history")
async def variant_price_history(vid: str, _: dict = Depends(get_current_user)):
    return await db.price_history.find(
        {"product_variant_id": vid}, {"_id": 0}
    ).sort("changed_at", -1).to_list(1000)


@router.get("/price-history")
async def all_price_history(limit: int = 100, _: dict = Depends(get_current_user)):
    return await db.price_history.find({}, {"_id": 0}).sort("changed_at", -1).to_list(limit)
