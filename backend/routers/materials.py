"""Materials router — CRUD on the catalogue's top-level material taxonomy."""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from core import MaterialIn, db, get_current_user, now_iso, require_role

router = APIRouter()


@router.get("/materials")
async def list_materials(_: dict = Depends(get_current_user)):
    items = await db.materials.find({}, {"_id": 0}).sort("material_name", 1).to_list(1000)
    return items


@router.post("/materials")
async def create_material(data: MaterialIn, user: dict = Depends(require_role("admin"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.materials.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.put("/materials/{mid}")
async def update_material(mid: str, data: MaterialIn,
                          user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["updated_at"] = now_iso()
    res = await db.materials.update_one({"id": mid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Material not found")
    item = await db.materials.find_one({"id": mid}, {"_id": 0})
    return item


@router.delete("/materials/{mid}")
async def delete_material(mid: str, user: dict = Depends(require_role("admin"))):
    res = await db.materials.delete_one({"id": mid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Material not found")
    return {"ok": True}
