"""Product Families router — CRUD, image uploads, and Excel-based bulk variant import."""
import shutil
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core import (
    UPLOAD_DIR, ProductFamilyIn, db, get_current_user, now_iso, require_role,
)
from services.pricing import (
    classify_header, norm_code, parse_variant_workbook, record_price_history,
)

router = APIRouter()


# ─────────────────── CRUD ───────────────────

@router.get("/product-families")
async def list_families(_: dict = Depends(get_current_user)):
    return await db.product_families.find({}, {"_id": 0}).sort("family_name", 1).to_list(1000)


@router.get("/product-families/{fid}")
async def get_family(fid: str, _: dict = Depends(get_current_user)):
    item = await db.product_families.find_one({"id": fid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Family not found")
    return item


@router.post("/product-families")
async def create_family(data: ProductFamilyIn,
                        user: dict = Depends(require_role("admin", "manager"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["main_product_image"] = None
    doc["dimension_drawing_image"] = None
    doc["catalogue_reference_image"] = None
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.product_families.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.put("/product-families/{fid}")
async def update_family(fid: str, data: ProductFamilyIn,
                        user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["updated_at"] = now_iso()
    res = await db.product_families.update_one({"id": fid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Family not found")
    return await db.product_families.find_one({"id": fid}, {"_id": 0})


@router.delete("/product-families/{fid}")
async def delete_family(fid: str, user: dict = Depends(require_role("admin"))):
    res = await db.product_families.delete_one({"id": fid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Family not found")
    return {"ok": True}


# ─────────────────── Image uploads ───────────────────

async def _save_upload_for_family(fid: str, file: UploadFile, field: str) -> str:
    family = await db.product_families.find_one({"id": fid}, {"_id": 0})
    if not family:
        raise HTTPException(status_code=404, detail="Family not found")
    ext = (file.filename.rsplit(".", 1)[-1] if "." in (file.filename or "") else "jpg").lower()
    if ext not in {"jpg", "jpeg", "png", "webp"}:
        raise HTTPException(status_code=400, detail="Only JPG/PNG/WebP allowed")
    fname = f"{fid}_{field}_{uuid.uuid4().hex}.{ext}"
    path = UPLOAD_DIR / fname
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    rel = f"/api/uploads/{fname}"
    await db.product_families.update_one({"id": fid}, {"$set": {field: rel, "updated_at": now_iso()}})
    return rel


@router.post("/product-families/{fid}/upload-image")
async def upload_main_image(fid: str, file: UploadFile = File(...),
                             user: dict = Depends(require_role("admin", "manager"))):
    return {"url": await _save_upload_for_family(fid, file, "main_product_image")}


@router.post("/product-families/{fid}/upload-dimension-drawing")
async def upload_dim_drawing(fid: str, file: UploadFile = File(...),
                              user: dict = Depends(require_role("admin", "manager"))):
    return {"url": await _save_upload_for_family(fid, file, "dimension_drawing_image")}


@router.post("/product-families/{fid}/upload-catalogue-reference")
async def upload_cat_ref(fid: str, file: UploadFile = File(...),
                          user: dict = Depends(require_role("admin", "manager"))):
    return {"url": await _save_upload_for_family(fid, file, "catalogue_reference_image")}


# ─────────────────── Excel variant bulk upload ───────────────────

@router.post("/product-families/{fid}/upload-variants-excel")
async def upload_variants_excel(
    fid: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_role("admin", "manager")),
):
    family = await db.product_families.find_one({"id": fid}, {"_id": 0})
    if not family:
        raise HTTPException(status_code=404, detail="Family not found")
    fname_lower = (file.filename or "").lower()
    if not (fname_lower.endswith(".xlsx") or fname_lower.endswith(".xlsm")):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file")

    content = await file.read()
    try:
        headers, data_rows = parse_variant_workbook(content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {e}")

    col_roles = [classify_header(h) for h in headers]
    if "code" not in col_roles:
        raise HTTPException(status_code=400,
                            detail="Could not find a 'Product Code' / 'Prod. Code' column in the spreadsheet")

    created = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    for row_idx, row in enumerate(data_rows, start=1):
        try:
            cable, hole, code = "", "", ""
            dims: Dict[str, str] = {}
            for ci, role in enumerate(col_roles):
                val = row[ci] if ci < len(row) else None
                if val is None:
                    continue
                sval = str(val).strip()
                if not sval:
                    continue
                if role == "cable":
                    cable = sval
                elif role == "hole":
                    hole = sval
                elif role == "code":
                    code = sval
                elif role.startswith("dim:"):
                    key = role.split(":", 1)[1]
                    dims[key] = sval
            if not code:
                skipped += 1
                continue
            code_clean = " ".join(code.split())
            code_key = norm_code(code_clean)
            cable_clean = (cable or "").strip()
            if cable_clean and not any(u in cable_clean.lower() for u in ["mm", "sq", "²"]):
                cable_disp = f"{cable_clean} mm²"
            else:
                cable_disp = cable_clean

            existing: Dict[str, Any] | None = None
            async for v in db.product_variants.find({}, {"_id": 0}):
                if norm_code(v.get("product_code", "")) == code_key:
                    existing = v
                    break

            if existing:
                upd = {
                    "product_code": code_clean,
                    "cable_size": cable_disp or existing.get("cable_size", ""),
                    "hole_size": hole or existing.get("hole_size", ""),
                    "dimensions": dims or existing.get("dimensions", {}),
                    "product_family_id": fid,
                    "material_id": family["material_id"],
                    "category_id": family["category_id"],
                    "subcategory_id": family.get("subcategory_id"),
                    "updated_at": now_iso(),
                }
                await db.product_variants.update_one({"id": existing["id"]}, {"$set": upd})
                updated += 1
            else:
                doc = {
                    "id": str(uuid.uuid4()),
                    "product_family_id": fid,
                    "product_code": code_clean,
                    "product_name": "",
                    "material_id": family["material_id"],
                    "category_id": family["category_id"],
                    "subcategory_id": family.get("subcategory_id"),
                    "cable_size": cable_disp,
                    "hole_size": hole,
                    "size": "",
                    "unit": "NOS",
                    "hsn_code": "85369090",
                    "gst_percentage": 18.0,
                    "base_price": 0.0,
                    "discount_percentage": 0.0,
                    "manual_price_override": False,
                    "manual_price": None,
                    "minimum_order_quantity": 100,
                    "dimensions": dims,
                    "notes": "",
                    "active": True,
                    "final_price": 0.0,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                await db.product_variants.insert_one(doc.copy())
                await record_price_history({}, doc, user["email"], "Variant imported from Excel")
                created += 1
        except Exception as e:
            errors.append(f"Row {row_idx}: {e}")
            skipped += 1

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "headers_detected": headers,
        "errors": errors[:20],
    }
