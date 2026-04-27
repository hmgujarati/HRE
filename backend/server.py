from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import uuid
import shutil
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Any, Dict

import bcrypt
import jwt
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr, ConfigDict


# ---------- Setup ----------
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ['JWT_SECRET']
JWT_ALGO = "HS256"
JWT_EXP_HOURS = 24

app = FastAPI(title="HRE Exporter CRM API")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ---------- Helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_role(*roles):
    async def _checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _checker


def calc_final_price(base_price: float, discount: float, manual_override: bool, manual_price: Optional[float]) -> float:
    if manual_override and manual_price is not None:
        return round(float(manual_price), 2)
    return round(float(base_price) - (float(base_price) * float(discount) / 100.0), 2)


# ---------- Models ----------
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    email: EmailStr
    mobile: Optional[str] = None
    role: str
    active: bool = True


class MaterialIn(BaseModel):
    material_name: str
    description: Optional[str] = ""
    active: bool = True


class CategoryIn(BaseModel):
    category_name: str
    material_id: str
    parent_category_id: Optional[str] = None
    description: Optional[str] = ""
    active: bool = True


class ProductFamilyIn(BaseModel):
    family_name: str
    short_name: Optional[str] = ""
    material_id: str
    category_id: str
    subcategory_id: Optional[str] = None
    product_type: Optional[str] = ""
    catalogue_title: Optional[str] = ""
    material_description: Optional[str] = ""
    specification_description: Optional[str] = ""
    finish_description: Optional[str] = ""
    insulation_colour_coding: Optional[str] = ""
    standard_reference: Optional[str] = ""
    description: Optional[str] = ""
    active: bool = True


class ProductVariantIn(BaseModel):
    product_family_id: str
    product_code: str
    product_name: Optional[str] = ""
    material_id: str
    category_id: str
    subcategory_id: Optional[str] = None
    cable_size: Optional[str] = ""
    hole_size: Optional[str] = ""
    size: Optional[str] = ""
    unit: str = "NOS"
    hsn_code: str = "85369090"
    gst_percentage: float = 18.0
    base_price: float = 0.0
    discount_percentage: float = 0.0
    manual_price_override: bool = False
    manual_price: Optional[float] = None
    minimum_order_quantity: int = 1
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = ""
    active: bool = True


class BulkDiscountIn(BaseModel):
    discount_percentage: float
    target_id: str  # material_id, category_id, or product_family_id
    change_reason: Optional[str] = "Bulk discount update"


# ---------- Auth Routes ----------
@api.post("/auth/login")
async def login(payload: LoginIn):
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not user.get("active", True):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(user["id"], user["email"], user["role"])
    return {
        "token": token,
        "user": {
            "id": user["id"], "name": user["name"], "email": user["email"],
            "mobile": user.get("mobile"), "role": user["role"], "active": user.get("active", True),
        },
    }


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@api.post("/auth/logout")
async def logout(_: dict = Depends(get_current_user)):
    return {"ok": True}


# ---------- Materials ----------
@api.get("/materials")
async def list_materials(_: dict = Depends(get_current_user)):
    items = await db.materials.find({}, {"_id": 0}).sort("material_name", 1).to_list(1000)
    return items


@api.post("/materials")
async def create_material(data: MaterialIn, user: dict = Depends(require_role("admin"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.materials.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.put("/materials/{mid}")
async def update_material(mid: str, data: MaterialIn, user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["updated_at"] = now_iso()
    res = await db.materials.update_one({"id": mid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Material not found")
    item = await db.materials.find_one({"id": mid}, {"_id": 0})
    return item


@api.delete("/materials/{mid}")
async def delete_material(mid: str, user: dict = Depends(require_role("admin"))):
    res = await db.materials.delete_one({"id": mid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Material not found")
    return {"ok": True}


# ---------- Categories ----------
@api.get("/categories")
async def list_categories(_: dict = Depends(get_current_user)):
    items = await db.categories.find({}, {"_id": 0}).sort("category_name", 1).to_list(2000)
    return items


@api.post("/categories")
async def create_category(data: CategoryIn, user: dict = Depends(require_role("admin"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.categories.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.put("/categories/{cid}")
async def update_category(cid: str, data: CategoryIn, user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["updated_at"] = now_iso()
    res = await db.categories.update_one({"id": cid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    item = await db.categories.find_one({"id": cid}, {"_id": 0})
    return item


@api.delete("/categories/{cid}")
async def delete_category(cid: str, user: dict = Depends(require_role("admin"))):
    res = await db.categories.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"ok": True}


# ---------- Product Families ----------
@api.get("/product-families")
async def list_families(_: dict = Depends(get_current_user)):
    items = await db.product_families.find({}, {"_id": 0}).sort("family_name", 1).to_list(1000)
    return items


@api.get("/product-families/{fid}")
async def get_family(fid: str, _: dict = Depends(get_current_user)):
    item = await db.product_families.find_one({"id": fid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Family not found")
    return item


@api.post("/product-families")
async def create_family(data: ProductFamilyIn, user: dict = Depends(require_role("admin", "manager"))):
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


@api.put("/product-families/{fid}")
async def update_family(fid: str, data: ProductFamilyIn, user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["updated_at"] = now_iso()
    res = await db.product_families.update_one({"id": fid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Family not found")
    item = await db.product_families.find_one({"id": fid}, {"_id": 0})
    return item


@api.delete("/product-families/{fid}")
async def delete_family(fid: str, user: dict = Depends(require_role("admin"))):
    res = await db.product_families.delete_one({"id": fid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Family not found")
    return {"ok": True}


async def _save_upload_for_family(fid: str, file: UploadFile, field: str) -> str:
    family = await db.product_families.find_one({"id": fid}, {"_id": 0})
    if not family:
        raise HTTPException(status_code=404, detail="Family not found")
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg").lower()
    if ext not in {"jpg", "jpeg", "png", "webp"}:
        raise HTTPException(status_code=400, detail="Only JPG/PNG/WebP allowed")
    fname = f"{fid}_{field}_{uuid.uuid4().hex}.{ext}"
    path = UPLOAD_DIR / fname
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    rel = f"/uploads/{fname}"
    await db.product_families.update_one({"id": fid}, {"$set": {field: rel, "updated_at": now_iso()}})
    return rel


@api.post("/product-families/{fid}/upload-image")
async def upload_main_image(fid: str, file: UploadFile = File(...), user: dict = Depends(require_role("admin", "manager"))):
    url = await _save_upload_for_family(fid, file, "main_product_image")
    return {"url": url}


@api.post("/product-families/{fid}/upload-dimension-drawing")
async def upload_dim_drawing(fid: str, file: UploadFile = File(...), user: dict = Depends(require_role("admin", "manager"))):
    url = await _save_upload_for_family(fid, file, "dimension_drawing_image")
    return {"url": url}


@api.post("/product-families/{fid}/upload-catalogue-reference")
async def upload_cat_ref(fid: str, file: UploadFile = File(...), user: dict = Depends(require_role("admin", "manager"))):
    url = await _save_upload_for_family(fid, file, "catalogue_reference_image")
    return {"url": url}


# ---------- Product Variants ----------
async def _record_price_history(variant_before, variant_after: dict, changed_by: str, reason: str):
    if variant_before is None:
        return
    fields = ["base_price", "discount_percentage", "manual_price", "manual_price_override", "final_price"]
    # for non-empty before dict, only record if something changed
    if variant_before and not any(variant_before.get(f) != variant_after.get(f) for f in fields):
        return
    entry = {
        "id": str(uuid.uuid4()),
        "product_variant_id": variant_after["id"],
        "product_family_id": variant_after.get("product_family_id"),
        "changed_by": changed_by,
        "old_base_price": variant_before.get("base_price"),
        "new_base_price": variant_after.get("base_price"),
        "old_discount_percentage": variant_before.get("discount_percentage"),
        "new_discount_percentage": variant_after.get("discount_percentage"),
        "old_manual_price": variant_before.get("manual_price"),
        "new_manual_price": variant_after.get("manual_price"),
        "old_manual_price_override": variant_before.get("manual_price_override"),
        "new_manual_price_override": variant_after.get("manual_price_override"),
        "old_final_price": variant_before.get("final_price"),
        "new_final_price": variant_after.get("final_price"),
        "change_reason": reason,
        "changed_at": now_iso(),
    }
    await db.price_history.insert_one(entry.copy())


@api.get("/product-variants")
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
    items = await db.product_variants.find(query, {"_id": 0}).sort("product_code", 1).to_list(5000)
    return items


@api.get("/product-variants/{vid}")
async def get_variant(vid: str, _: dict = Depends(get_current_user)):
    item = await db.product_variants.find_one({"id": vid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Variant not found")
    return item


@api.post("/product-variants")
async def create_variant(data: ProductVariantIn, user: dict = Depends(require_role("admin", "manager"))):
    doc = data.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["final_price"] = calc_final_price(doc["base_price"], doc["discount_percentage"], doc["manual_price_override"], doc.get("manual_price"))
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    await db.product_variants.insert_one(doc.copy())
    doc.pop("_id", None)
    # Initial price history
    await _record_price_history({}, doc, user["email"], "Variant created")
    return doc


@api.put("/product-variants/{vid}")
async def update_variant(vid: str, data: ProductVariantIn, user: dict = Depends(require_role("admin", "manager"))):
    before = await db.product_variants.find_one({"id": vid}, {"_id": 0})
    if not before:
        raise HTTPException(status_code=404, detail="Variant not found")
    upd = data.model_dump()
    upd["final_price"] = calc_final_price(upd["base_price"], upd["discount_percentage"], upd["manual_price_override"], upd.get("manual_price"))
    upd["updated_at"] = now_iso()
    await db.product_variants.update_one({"id": vid}, {"$set": upd})
    after = await db.product_variants.find_one({"id": vid}, {"_id": 0})
    await _record_price_history(before, after, user["email"], "Variant updated")
    return after


@api.delete("/product-variants/{vid}")
async def delete_variant(vid: str, user: dict = Depends(require_role("admin"))):
    res = await db.product_variants.delete_one({"id": vid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"ok": True}


@api.get("/product-variants/{vid}/price-history")
async def variant_price_history(vid: str, _: dict = Depends(get_current_user)):
    items = await db.price_history.find({"product_variant_id": vid}, {"_id": 0}).sort("changed_at", -1).to_list(1000)
    return items


@api.get("/price-history")
async def all_price_history(limit: int = 100, _: dict = Depends(get_current_user)):
    items = await db.price_history.find({}, {"_id": 0}).sort("changed_at", -1).to_list(limit)
    return items


# ---------- Bulk Discount ----------
async def _apply_bulk_discount(query: Dict[str, Any], discount: float, user_email: str, reason: str) -> int:
    variants = await db.product_variants.find(query, {"_id": 0}).to_list(10000)
    count = 0
    for v in variants:
        before = dict(v)
        new_doc = dict(v)
        new_doc["discount_percentage"] = float(discount)
        new_doc["final_price"] = calc_final_price(
            new_doc["base_price"], new_doc["discount_percentage"],
            new_doc.get("manual_price_override", False), new_doc.get("manual_price"),
        )
        new_doc["updated_at"] = now_iso()
        await db.product_variants.update_one({"id": v["id"]}, {"$set": {
            "discount_percentage": new_doc["discount_percentage"],
            "final_price": new_doc["final_price"],
            "updated_at": new_doc["updated_at"],
        }})
        await _record_price_history(before, new_doc, user_email, reason)
        count += 1
    return count


@api.post("/pricing/bulk-discount/material")
async def bulk_discount_material(data: BulkDiscountIn, user: dict = Depends(require_role("admin", "manager"))):
    count = await _apply_bulk_discount({"material_id": data.target_id}, data.discount_percentage, user["email"], data.change_reason or "Bulk discount by material")
    return {"updated_count": count}


@api.post("/pricing/bulk-discount/category")
async def bulk_discount_category(data: BulkDiscountIn, user: dict = Depends(require_role("admin", "manager"))):
    count = await _apply_bulk_discount({"$or": [{"category_id": data.target_id}, {"subcategory_id": data.target_id}]}, data.discount_percentage, user["email"], data.change_reason or "Bulk discount by category")
    return {"updated_count": count}


@api.post("/pricing/bulk-discount/product-family")
async def bulk_discount_family(data: BulkDiscountIn, user: dict = Depends(require_role("admin", "manager"))):
    count = await _apply_bulk_discount({"product_family_id": data.target_id}, data.discount_percentage, user["email"], data.change_reason or "Bulk discount by family")
    return {"updated_count": count}


@api.post("/pricing/bulk-discount/preview")
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


# ---------- Dashboard ----------
@api.get("/dashboard/stats")
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


# ---------- Mount ----------
app.include_router(api)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Seed ----------
async def seed_data():
    await db.users.create_index("email", unique=True)
    await db.materials.create_index("material_name", unique=True)
    await db.product_variants.create_index("product_code")
    await db.price_history.create_index("product_variant_id")

    admin_email = os.environ["ADMIN_EMAIL"].lower()
    admin_password = os.environ["ADMIN_PASSWORD"]
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()), "name": "HRE Admin", "email": admin_email,
            "mobile": "", "password_hash": hash_password(admin_password),
            "role": "admin", "active": True,
            "created_at": now_iso(), "updated_at": now_iso(),
        })
        logger.info(f"Seeded admin user: {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password), "updated_at": now_iso()}})
        logger.info("Admin password updated from env")

    # Materials
    mat_seed = [("Copper", "High purity electrolytic copper"), ("Aluminium", "Aluminium alloy")]
    mat_ids = {}
    for name, desc in mat_seed:
        m = await db.materials.find_one({"material_name": name})
        if not m:
            m = {"id": str(uuid.uuid4()), "material_name": name, "description": desc, "active": True,
                 "created_at": now_iso(), "updated_at": now_iso()}
            await db.materials.insert_one(m.copy())
        mat_ids[name] = m["id"]

    # Categories (nested)
    async def upsert_cat(name, mat_id, parent_id=None):
        existing = await db.categories.find_one({"category_name": name, "material_id": mat_id, "parent_category_id": parent_id})
        if existing:
            return existing["id"]
        doc = {"id": str(uuid.uuid4()), "category_name": name, "material_id": mat_id,
               "parent_category_id": parent_id, "description": "", "active": True,
               "created_at": now_iso(), "updated_at": now_iso()}
        await db.categories.insert_one(doc.copy())
        return doc["id"]

    cu = mat_ids["Copper"]; al = mat_ids["Aluminium"]
    cu_sheet = await upsert_cat("Sheet Metal Lug", cu)
    cu_ring = await upsert_cat("Ring Type Lug", cu, cu_sheet)
    cu_pin = await upsert_cat("Pin Type Lug", cu, cu_sheet)
    await upsert_cat("Fork Type Lug", cu, cu_sheet)
    await upsert_cat("U Type Lug", cu, cu_sheet)
    cu_tub = await upsert_cat("Tubular Lug", cu)
    await upsert_cat("Copper Lug", cu, cu_tub)
    await upsert_cat("Inline Connectors", cu, cu_tub)
    await upsert_cat("Tubular Lug", al)
    await upsert_cat("Inline Connectors", al)
    await upsert_cat("Forged Lug", al)

    # Product families
    async def upsert_family(name, mat_id, cat_id, sub_id, **kwargs):
        existing = await db.product_families.find_one({"family_name": name})
        if existing:
            return existing["id"]
        doc = {"id": str(uuid.uuid4()), "family_name": name, "material_id": mat_id,
               "category_id": cat_id, "subcategory_id": sub_id,
               "main_product_image": None, "dimension_drawing_image": None,
               "catalogue_reference_image": None, "active": True,
               "created_at": now_iso(), "updated_at": now_iso(), **kwargs}
        # set defaults
        for k in ["short_name", "product_type", "catalogue_title", "material_description",
                  "specification_description", "finish_description", "insulation_colour_coding",
                  "standard_reference", "description"]:
            doc.setdefault(k, "")
        await db.product_families.insert_one(doc.copy())
        return doc["id"]

    fam1 = await upsert_family(
        "Crimping Type Tinned Copper Ring Type Cable Terminal Ends",
        cu, cu_sheet, cu_ring,
        short_name="Ring Type Lug",
        product_type="Ring Type",
        catalogue_title="Crimping Type Tinned Copper Ring Type Cable Terminal Ends",
        material_description="Copper Strip / Tape to IS-1897",
        specification_description="E.C. Grade 99.25% IACS",
        finish_description="Electro Tinned to BS 1872 (1984)",
        standard_reference="IS-1897 / BS 1872 (1984)",
        description="Sheet metal copper ring type lug used for terminating cables onto bolted connections.",
    )
    fam2 = await upsert_family(
        "Crimping Type Insulated Tinned Copper Ring Type Terminals",
        cu, cu_sheet, cu_ring,
        short_name="Insulated Ring Type",
        product_type="Insulated Ring",
        catalogue_title="Crimping Type Insulated Tinned Copper Ring Type Terminals",
        material_description="Copper Strip / Tape to IS-1897",
        specification_description="E.C. Grade 99.25% IACS",
        finish_description="Electro Tinned to BS 1872 (1984)",
        insulation_colour_coding="1.5 = Red, 2.5 = Blue, 4-6 = Yellow",
        standard_reference="IS-1897 / BS 1872 (1984)",
        description="Insulated ring type terminal with PVC sleeve, colour coded for cable size.",
    )
    fam3 = await upsert_family(
        "Crimping Type Tinned Copper Pin Type Cable Terminal Ends",
        cu, cu_sheet, cu_pin,
        short_name="Pin Type Lug",
        product_type="Pin Type",
        catalogue_title="Crimping Type Tinned Copper Pin Type Cable Terminal Ends",
        material_description="Copper Strip / Tape to IS-1897",
        specification_description="E.C. Grade 99.25% IACS",
        finish_description="Electro Tinned to BS 1872 (1984)",
        standard_reference="IS-1897 / BS 1872 (1984)",
        description="Pin type lug used for terminating cables onto pin type terminal blocks.",
    )

    # Variants
    async def upsert_variant(code, fam_id, mat_id, cat_id, sub_id, cable, hole, dims, base_price):
        existing = await db.product_variants.find_one({"product_code": code})
        if existing:
            return
        doc = {
            "id": str(uuid.uuid4()), "product_family_id": fam_id, "product_code": code,
            "product_name": "", "material_id": mat_id, "category_id": cat_id,
            "subcategory_id": sub_id, "cable_size": cable, "hole_size": hole,
            "size": "", "unit": "NOS", "hsn_code": "85369090", "gst_percentage": 18.0,
            "base_price": base_price, "discount_percentage": 0.0,
            "manual_price_override": False, "manual_price": None,
            "minimum_order_quantity": 100, "dimensions": dims, "notes": "", "active": True,
            "final_price": calc_final_price(base_price, 0.0, False, None),
            "created_at": now_iso(), "updated_at": now_iso(),
        }
        await db.product_variants.insert_one(doc.copy())

    ring_dims = {"A": "1.6", "C": "3.2", "D": "6.8", "F": "0.8", "B": "5", "K": "1", "H": "3.6", "L1": "9.6", "J": "13"}
    await upsert_variant("RI-7153", fam1, cu, cu_sheet, cu_ring, "1.5 mm²", "3.2", ring_dims, 4.50)
    await upsert_variant("RI-7048", fam1, cu, cu_sheet, cu_ring, "1.5 mm²", "3.7", ring_dims, 4.75)
    await upsert_variant("RI-7049", fam1, cu, cu_sheet, cu_ring, "1.5 mm²", "4.2", ring_dims, 5.00)

    ins_dims = {"A": "1.6", "C": "3.2", "D": "6.8", "F": "0.8", "B": "5", "K": "1", "H": "3.6", "J": "13", "J1": "10", "L3": "14.6", "C1": "4.8"}
    await upsert_variant("RII-7057", fam2, cu, cu_sheet, cu_ring, "1.5 mm²", "3.2", ins_dims, 6.20)
    await upsert_variant("RII-7058", fam2, cu, cu_sheet, cu_ring, "1.5 mm²", "3.7", ins_dims, 6.50)
    await upsert_variant("RII-7059", fam2, cu, cu_sheet, cu_ring, "1.5 mm²", "4.2", ins_dims, 6.80)

    pin1 = {"A": "1.6", "C": "3.2", "D": "1.9", "F": "0.8", "B": "5", "G+H": "10", "J": "17", "TYPE": "I"}
    pin2 = {"A": "2.3", "C": "3.9", "D": "1.9", "F": "0.8", "B": "5", "G+H": "10", "J": "17", "TYPE": "I"}
    pin3 = {"A": "2.3", "C": "3.9", "D": "3.1", "F": "0.8", "B": "5", "G+H": "10", "J": "17", "TYPE": "II"}
    await upsert_variant("PT-9", fam3, cu, cu_sheet, cu_pin, "1.5 mm²", "-", pin1, 5.50)
    await upsert_variant("PT-1", fam3, cu, cu_sheet, cu_pin, "2.5 mm²", "-", pin2, 6.00)
    await upsert_variant("PT-2", fam3, cu, cu_sheet, cu_pin, "2.5 mm²", "-", pin3, 6.25)


@app.on_event("startup")
async def on_startup():
    try:
        await seed_data()
    except Exception as e:
        logger.exception(f"Seed failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    client.close()


@api.get("/")
async def root():
    return {"service": "HRE Exporter CRM API", "status": "ok"}
