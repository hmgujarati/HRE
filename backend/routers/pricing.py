"""Pricing router — bulk discount endpoints + Excel price import.

Read endpoints are guarded with `get_current_user`; mutations require admin/manager.
"""
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core import (
    BulkDiscountIn, calc_final_price, db, get_current_user, now_iso, require_role,
)
from services.pricing import (
    apply_bulk_discount, classify_header, is_number, norm_code,
    parse_variant_workbook, record_price_history,
)

router = APIRouter()


@router.post("/pricing/bulk-discount/material")
async def bulk_discount_material(data: BulkDiscountIn,
                                  user: dict = Depends(require_role("admin", "manager"))):
    count = await apply_bulk_discount(
        {"material_id": data.target_id}, data.discount_percentage, user["email"],
        data.change_reason or "Bulk discount by material",
    )
    return {"updated_count": count}


@router.post("/pricing/bulk-discount/category")
async def bulk_discount_category(data: BulkDiscountIn,
                                  user: dict = Depends(require_role("admin", "manager"))):
    count = await apply_bulk_discount(
        {"$or": [{"category_id": data.target_id}, {"subcategory_id": data.target_id}]},
        data.discount_percentage, user["email"],
        data.change_reason or "Bulk discount by category",
    )
    return {"updated_count": count}


@router.post("/pricing/bulk-discount/product-family")
async def bulk_discount_family(data: BulkDiscountIn,
                                user: dict = Depends(require_role("admin", "manager"))):
    count = await apply_bulk_discount(
        {"product_family_id": data.target_id}, data.discount_percentage, user["email"],
        data.change_reason or "Bulk discount by family",
    )
    return {"updated_count": count}


@router.post("/pricing/bulk-discount/preview")
async def bulk_discount_preview(data: dict, _: dict = Depends(get_current_user)):
    scope = data.get("scope")
    target_id = data.get("target_id")
    if scope == "material":
        q = {"material_id": target_id}
    elif scope == "category":
        q = {"$or": [{"category_id": target_id}, {"subcategory_id": target_id}]}
    elif scope == "product_family":
        q = {"product_family_id": target_id}
    else:
        raise HTTPException(status_code=400, detail="Invalid scope")
    count = await db.product_variants.count_documents(q)
    return {"count": count}


@router.post("/pricing/upload-prices-excel")
async def upload_prices_excel(
    file: UploadFile = File(...),
    user: dict = Depends(require_role("admin", "manager")),
):
    """Bulk update base_price by Product Code from an Excel sheet.
    Columns auto-detected: Product Code (required), Price/Rate/MRP/HRE (required, numeric).
    Matches variants by normalised product code (ignores spaces/dashes/case)."""
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
        raise HTTPException(status_code=400, detail="Could not find a 'Product Code' column")

    price_idx = None
    for i, r in enumerate(col_roles):
        if r == "price":
            price_idx = i
    if price_idx is None:
        for i in range(len(headers) - 1, -1, -1):
            if col_roles[i] in ("code", "cable", "hole"):
                continue
            vals = [r[i] for r in data_rows if i < len(r) and r[i] is not None and str(r[i]).strip()]
            if vals and all(is_number(v) for v in vals):
                price_idx = i
                break
    if price_idx is None:
        raise HTTPException(
            status_code=400,
            detail="Could not detect a price column. Add a column header like 'Price', 'Rate', 'MRP' or 'HRE'.",
        )

    code_idx = col_roles.index("code")
    updated = 0
    not_found = 0
    skipped = 0
    errors: List[str] = []

    for row_idx, row in enumerate(data_rows, start=1):
        try:
            raw_code = row[code_idx] if code_idx < len(row) else None
            raw_price = row[price_idx] if price_idx < len(row) else None
            if raw_code is None or raw_price is None:
                skipped += 1
                continue
            code = " ".join(str(raw_code).strip().split())
            if not code:
                skipped += 1
                continue
            try:
                new_base = float(str(raw_price).strip().replace(",", ""))
            except Exception:
                skipped += 1
                continue
            code_key = norm_code(code)

            existing = None
            async for v in db.product_variants.find({}, {"_id": 0}):
                if norm_code(v.get("product_code", "")) == code_key:
                    existing = v
                    break
            if not existing:
                not_found += 1
                continue

            before = dict(existing)
            new_final = calc_final_price(
                new_base,
                existing.get("discount_percentage", 0.0),
                existing.get("manual_price_override", False),
                existing.get("manual_price"),
            )
            await db.product_variants.update_one(
                {"id": existing["id"]},
                {"$set": {"base_price": new_base, "final_price": new_final, "updated_at": now_iso()}},
            )
            after = dict(existing)
            after["base_price"] = new_base
            after["final_price"] = new_final
            await record_price_history(before, after, user["email"], "Price imported from Excel")
            updated += 1
        except Exception as e:
            errors.append(f"Row {row_idx}: {e}")
            skipped += 1

    return {
        "updated": updated,
        "not_found": not_found,
        "skipped": skipped,
        "headers_detected": headers,
        "price_column": headers[price_idx] if price_idx is not None else None,
        "errors": errors[:20],
    }
