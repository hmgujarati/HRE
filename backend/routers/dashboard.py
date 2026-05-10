"""Dashboard router — admin counts + public landing-page stats."""
from fastapi import APIRouter, Depends

from core import db, get_current_user

router = APIRouter()


@router.get("/dashboard/stats")
async def dashboard_stats(_: dict = Depends(get_current_user)):
    materials = await db.materials.find({}, {"_id": 0}).to_list(100)
    mat_map = {m["id"]: m["material_name"] for m in materials}
    total_families = await db.product_families.count_documents({})
    total_variants = await db.product_variants.count_documents({})
    active_variants = await db.product_variants.count_documents({"active": True})
    total_categories = await db.categories.count_documents({})

    material_counts = {}
    for mid, mname in mat_map.items():
        cnt = await db.product_variants.count_documents({"material_id": mid})
        material_counts[mname] = cnt

    recent_families = await db.product_families.find({}, {"_id": 0}).sort("created_at", -1).limit(5).to_list(5)
    recent_price_changes = await db.price_history.find({}, {"_id": 0}).sort("changed_at", -1).limit(8).to_list(8)

    return {
        "total_families": total_families,
        "total_variants": total_variants,
        "active_variants": active_variants,
        "total_categories": total_categories,
        "material_counts": material_counts,
        "recent_families": recent_families,
        "recent_price_changes": recent_price_changes,
    }


@router.get("/public/stats")
async def public_stats():
    """Lightweight, unauthenticated counts for the login splash."""
    materials = await db.materials.count_documents({"active": True})
    families = await db.product_families.count_documents({"active": True})
    variants = await db.product_variants.count_documents({"active": True})
    return {"materials": materials, "families": families, "variants": variants}
