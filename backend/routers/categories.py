"""Categories router — CRUD on nested category/sub-category tree."""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from core import CategoryIn, db, get_current_user, now_iso, require_role

router = APIRouter()


@router.get("/categories")
async def list_categories(_: dict = Depends(get_current_user)):
    items = await db.categories.find({}, {"_id": 0}).sort("category_name", 1).to_list(2000)
    return items


@router.post("/categories")
async def create_category(data: CategoryIn, user: dict = Depends(require_role("admin"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.categories.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.put("/categories/{cid}")
async def update_category(cid: str, data: CategoryIn,
                          user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["updated_at"] = now_iso()
    res = await db.categories.update_one({"id": cid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    item = await db.categories.find_one({"id": cid}, {"_id": 0})
    return item


@router.delete("/categories/{cid}")
async def delete_category(cid: str, user: dict = Depends(require_role("admin"))):
    res = await db.categories.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"ok": True}
