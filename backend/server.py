from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import re
import uuid
import shutil
import logging
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Any, Dict

import bcrypt
import jwt
import openpyxl
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
    rel = f"/api/uploads/{fname}"
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


# ---------- Excel/CSV Variant Bulk Upload ----------
def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace(".", "").replace(" ", "")


def _norm_code(s: Any) -> str:
    """Normalise product code for matching: uppercase, remove all whitespace."""
    return "".join(str(s or "").upper().split())


def _is_number(v: Any) -> bool:
    if v is None:
        return False
    try:
        float(str(v).strip().replace(",", ""))
        return True
    except Exception:
        return False


def _parse_variant_workbook(content: bytes) -> tuple[list[str], list[list[Any]]]:
    """Return (header_keys, data_rows). Tolerates 1/2/3-row merged headers
    (e.g. row 0 has 'CABLE SIZE / HOLE E / DIMENSIONS / PROD. CODE' with merged cells,
    row 1 is empty due to vertical merges, row 2 carries dimension sub-keys A/B/C/D...).
    """
    wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    ws = wb.active

    # Unmerge so each cell holds its own value (top-left value already there)
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if not rows:
        raise HTTPException(status_code=400, detail="Empty workbook")

    n_cols = max(len(r) for r in rows)
    rows = [list(r) + [None] * (n_cols - len(r)) for r in rows]

    # Find data-start: first row with a value that looks like a product code
    # (contains a digit AND alphabetic — e.g. RI-7153, PT-9, RII-7057).
    def looks_like_code(v):
        if v is None:
            return False
        s = str(v).strip()
        return bool(s) and any(ch.isdigit() for ch in s) and any(ch.isalpha() for ch in s)

    def looks_like_number(v):
        if v is None:
            return False
        try:
            float(str(v).strip())
            return True
        except Exception:
            return False

    data_start = None
    for i, row in enumerate(rows):
        # at least one cell is a code AND at least 3 cells are numeric → data row
        if any(looks_like_code(c) for c in row) and sum(1 for c in row if looks_like_number(c)) >= 3:
            data_start = i
            break
    if data_start is None or data_start == 0:
        raise HTTPException(status_code=400, detail="Could not locate data rows in spreadsheet")

    # Build per-column header by collecting non-empty values from rows 0..data_start-1.
    # Skip generic group labels like 'DIMENSIONS'. Prefer the most specific (last) value.
    GENERIC = {"dimensions", "dimension", "specs", "specification"}
    headers: list[str] = []
    for ci in range(n_cols):
        chosen = ""
        for ri in range(data_start):
            v = rows[ri][ci]
            if v is None:
                continue
            sv = str(v).strip()
            if not sv:
                continue
            if sv.lower() in GENERIC:
                continue
            chosen = sv
        headers.append(chosen)

    data = rows[data_start:]
    data = [r for r in data if any(c is not None and str(c).strip() != "" for c in r)]
    return headers, data


def _classify_header(h: str) -> str:
    """Return 'cable'|'hole'|'code'|'price'|'dim:<key>'|'skip'."""
    n = _norm(h)
    if not n:
        return "skip"
    if "prod" in n or "code" in n:
        return "code"
    if "cable" in n or n == "mm2" or ("size" in n and "hole" not in n):
        return "cable"
    if "hole" in n or n == "e":
        return "hole"
    if any(k in n for k in ["price", "rate", "mrp", "cost", "amount", "hre"]):
        return "price"
    # treat as dimension key, preserve original label (strip whitespace)
    return f"dim:{h.strip()}"


@api.post("/product-families/{fid}/upload-variants-excel")
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
        headers, data_rows = _parse_variant_workbook(content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {e}")

    # Map column index → role
    col_roles = [_classify_header(h) for h in headers]
    if "code" not in col_roles:
        raise HTTPException(status_code=400, detail="Could not find a 'Product Code' / 'Prod. Code' column in the spreadsheet")

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

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
                # 'skip' ignored
            if not code:
                skipped += 1
                continue
            # Collapse multiple spaces, then store; match by normalised (no-space, upper) form
            code_clean = " ".join(code.split())
            code_key = _norm_code(code_clean)
            # Normalise cable size: append mm² if no unit suffix present
            cable_clean = (cable or "").strip()
            if cable_clean and not any(u in cable_clean.lower() for u in ["mm", "sq", "²"]):
                cable_disp = f"{cable_clean} mm²"
            else:
                cable_disp = cable_clean

            # Find existing variant by normalised product code (handles 'RI-7153' vs 'RI - 7153')
            existing = None
            async for v in db.product_variants.find({}, {"_id": 0}):
                if _norm_code(v.get("product_code", "")) == code_key:
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
                await _record_price_history({}, doc, user["email"], "Variant imported from Excel")
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


@api.post("/pricing/upload-prices-excel")
async def upload_prices_excel(
    file: UploadFile = File(...),
    user: dict = Depends(require_role("admin", "manager")),
):
    """Bulk update base_price by Product Code from an Excel sheet.
    Columns auto-detected: Product Code (required), Price/Rate/MRP/HRE (required, numeric).
    Matches variants by normalised product code (ignores spaces/dashes case).
    """
    fname_lower = (file.filename or "").lower()
    if not (fname_lower.endswith(".xlsx") or fname_lower.endswith(".xlsm")):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file")
    content = await file.read()
    try:
        headers, data_rows = _parse_variant_workbook(content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {e}")

    col_roles = [_classify_header(h) for h in headers]
    if "code" not in col_roles:
        raise HTTPException(status_code=400, detail="Could not find a 'Product Code' column")

    # Locate the price column. Prefer explicit 'price' classification; else the
    # rightmost numeric-only unmapped column.
    price_idx = None
    for i, r in enumerate(col_roles):
        if r == "price":
            price_idx = i
    if price_idx is None:
        # Fallback: any column where all non-empty values parse as float
        for i in range(len(headers) - 1, -1, -1):
            if col_roles[i] in ("code", "cable", "hole"):
                continue
            vals = [r[i] for r in data_rows if i < len(r) and r[i] is not None and str(r[i]).strip()]
            if vals and all(_is_number(v) for v in vals):
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
    errors: list[str] = []

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
            code_key = _norm_code(code)

            existing = None
            async for v in db.product_variants.find({}, {"_id": 0}):
                if _norm_code(v.get("product_code", "")) == code_key:
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
            await _record_price_history(before, after, user["email"], "Price imported from Excel")
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


# ---------- Public (unauthenticated) ----------
@api.get("/")
async def root():
    return {"service": "HRE Exporter CRM API", "status": "ok"}


@api.get("/public/stats")
async def public_stats():
    """Lightweight, unauthenticated counts for the login splash."""
    materials = await db.materials.count_documents({"active": True})
    families = await db.product_families.count_documents({"active": True})
    variants = await db.product_variants.count_documents({"active": True})
    return {"materials": materials, "families": families, "variants": variants}


# ---------- Contacts (CRM) ----------
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


def _norm_phone(s: Optional[str]) -> str:
    if not s:
        return ""
    return "".join(ch for ch in s if ch.isdigit())[-10:]


def _norm_email(s: Optional[str]) -> str:
    return (s or "").strip().lower()


async def _find_contact_match(phone: str, email: str) -> Optional[dict]:
    p = _norm_phone(phone)
    e = _norm_email(email)
    if e:
        c = await db.contacts.find_one({"email_norm": e}, {"_id": 0})
        if c:
            return c
    if p:
        c = await db.contacts.find_one({"phone_norm": p}, {"_id": 0})
        if c:
            return c
    return None


@api.get("/contacts")
async def list_contacts(q: Optional[str] = None, source: Optional[str] = None, _: dict = Depends(get_current_user)):
    query: Dict[str, Any] = {}
    if source:
        query["source"] = source
    if q:
        rx = re.escape(q)
        query["$or"] = [
            {"name": {"$regex": rx, "$options": "i"}},
            {"company": {"$regex": rx, "$options": "i"}},
            {"phone": {"$regex": rx, "$options": "i"}},
            {"phone_norm": {"$regex": _norm_phone(q) or rx}},
            {"email": {"$regex": rx, "$options": "i"}},
        ]
    items = await db.contacts.find(query, {"_id": 0}).sort("created_at", -1).to_list(2000)
    return items


@api.get("/contacts/{cid}")
async def get_contact(cid: str, _: dict = Depends(get_current_user)):
    item = await db.contacts.find_one({"id": cid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Contact not found")
    return item


@api.post("/contacts")
async def create_contact(data: ContactIn, user: dict = Depends(require_role("admin", "manager"))):
    doc = data.model_dump()
    doc["phone_norm"] = _norm_phone(doc.get("phone"))
    doc["email_norm"] = _norm_email(doc.get("email"))
    # Smart upsert: if phone or email already present, update existing
    existing = await _find_contact_match(doc.get("phone", ""), doc.get("email", ""))
    if existing:
        upd = {k: v for k, v in doc.items() if v not in (None, "", []) or k in {"tags"}}
        upd["updated_at"] = now_iso()
        await db.contacts.update_one({"id": existing["id"]}, {"$set": upd})
        item = await db.contacts.find_one({"id": existing["id"]}, {"_id": 0})
        return item
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    doc["created_by"] = user["email"]
    await db.contacts.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.put("/contacts/{cid}")
async def update_contact(cid: str, data: ContactIn, user: dict = Depends(require_role("admin", "manager"))):
    upd = data.model_dump()
    upd["phone_norm"] = _norm_phone(upd.get("phone"))
    upd["email_norm"] = _norm_email(upd.get("email"))
    upd["updated_at"] = now_iso()
    res = await db.contacts.update_one({"id": cid}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    item = await db.contacts.find_one({"id": cid}, {"_id": 0})
    return item


@api.delete("/contacts/{cid}")
async def delete_contact(cid: str, _: dict = Depends(require_role("admin"))):
    res = await db.contacts.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"ok": True}


@api.get("/contacts/{cid}/quotations")
async def contact_quotations(cid: str, _: dict = Depends(get_current_user)):
    items = await db.quotations.find({"contact_id": cid}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items


# ---------- Quotations ----------
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


def _fy_label(d: datetime) -> str:
    """Indian FY: April → March. e.g. Apr 2026 → '2026-27'."""
    if d.month >= 4:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"


async def _next_quote_number() -> str:
    fy = _fy_label(datetime.now(timezone.utc))
    counter = await db.counters.find_one_and_update(
        {"_id": f"quote_seq_{fy}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = counter["seq"] if counter else 1
    return f"HRE/QT/{fy}/{seq:04d}"


def _compute_quote_totals(line_items: List[Dict[str, Any]]) -> Dict[str, float]:
    subtotal = 0.0
    total_discount = 0.0
    total_gst = 0.0
    for li in line_items:
        qty = float(li.get("quantity") or 0)
        base = float(li.get("base_price") or 0)
        disc_pct = float(li.get("discount_percentage") or 0)
        gst_pct = float(li.get("gst_percentage") or 0)
        line_gross = qty * base
        line_disc = round(line_gross * disc_pct / 100.0, 2)
        line_taxable = round(line_gross - line_disc, 2)
        line_gst = round(line_taxable * gst_pct / 100.0, 2)
        line_total = round(line_taxable + line_gst, 2)
        li["line_gross"] = round(line_gross, 2)
        li["discount_amount"] = line_disc
        li["taxable_value"] = line_taxable
        li["gst_amount"] = line_gst
        li["line_total"] = line_total
        subtotal += line_gross
        total_discount += line_disc
        total_gst += line_gst
    grand_total = round(subtotal - total_discount + total_gst, 2)
    return {
        "subtotal": round(subtotal, 2),
        "total_discount": round(total_discount, 2),
        "taxable_value": round(subtotal - total_discount, 2),
        "total_gst": round(total_gst, 2),
        "grand_total": grand_total,
    }


@api.get("/quotations")
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
    items = await db.quotations.find(query, {"_id": 0}).sort("created_at", -1).to_list(2000)
    return items


@api.get("/quotations/next-number")
async def quote_next_number(_: dict = Depends(get_current_user)):
    fy = _fy_label(datetime.now(timezone.utc))
    counter = await db.counters.find_one({"_id": f"quote_seq_{fy}"}, {"_id": 0})
    nxt = (counter.get("seq", 0) + 1) if counter else 1
    return {"preview": f"HRE/QT/{fy}/{nxt:04d}"}


@api.get("/quotations/{qid}")
async def get_quotation(qid: str, _: dict = Depends(get_current_user)):
    item = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return item


@api.post("/quotations")
async def create_quotation(data: QuoteIn, user: dict = Depends(require_role("admin", "manager"))):
    contact = await db.contacts.find_one({"id": data.contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    line_items = [li.model_dump() for li in data.line_items]
    totals = _compute_quote_totals(line_items)
    qnum = await _next_quote_number()
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


@api.put("/quotations/{qid}")
async def update_quotation(qid: str, data: QuoteIn, user: dict = Depends(require_role("admin", "manager"))):
    existing = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Quotation not found")
    if existing.get("status") in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot edit {existing['status']} quote — use Revise instead")
    contact = await db.contacts.find_one({"id": data.contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    line_items = [li.model_dump() for li in data.line_items]
    totals = _compute_quote_totals(line_items)
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
    item = await db.quotations.find_one({"id": qid}, {"_id": 0})
    return item


@api.patch("/quotations/{qid}/status")
async def change_quote_status(qid: str, payload: dict, user: dict = Depends(require_role("admin", "manager"))):
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
    item = await db.quotations.find_one({"id": qid}, {"_id": 0})
    return item


@api.post("/quotations/{qid}/revise")
async def revise_quotation(qid: str, user: dict = Depends(require_role("admin", "manager"))):
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
    # Strip any prior -R\d+ suffix before appending current revision marker
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
    # Mark source as revised
    await db.quotations.update_one({"id": src["id"]}, {"$set": {"status": "revised", "updated_at": now_iso()}})
    new_doc.pop("_id", None)
    return new_doc


@api.delete("/quotations/{qid}")
async def delete_quotation(qid: str, _: dict = Depends(require_role("admin"))):
    res = await db.quotations.delete_one({"id": qid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return {"ok": True}


@api.get("/dashboard/quote-stats")
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


# ---------- Public Catalogue + Self-Serve Quote (Wave A) ----------
import secrets
import hashlib
import httpx

OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
SESSION_TTL_DAYS = 30
DEV_OTP_PASSTHROUGH = os.environ.get("DEV_OTP_PASSTHROUGH", "true").lower() == "true"
SETTINGS_DOC_ID = "integrations"


# ---------- Integration Settings (WhatsApp BizChatAPI + SMTP) ----------
DEFAULT_INTEGRATIONS = {
    "id": SETTINGS_DOC_ID,
    "whatsapp": {
        "enabled": False,
        "api_base_url": "https://bizchatapi.in/api",
        "vendor_uid": "",
        "token": "",
        "from_phone_number_id": "",
        "otp_template_name": "",
        "otp_template_language": "en",
        "default_country_code": "91",
    },
    "smtp": {
        "enabled": False,
        "host": "smtp.hostinger.com",
        "port": 465,
        "use_ssl": True,
        "username": "",
        "password": "",
        "from_email": "",
        "from_name": "HRE Exporter",
    },
}


async def _get_integrations() -> dict:
    doc = await db.settings.find_one({"id": SETTINGS_DOC_ID}, {"_id": 0})
    if not doc:
        return DEFAULT_INTEGRATIONS.copy()
    # Merge with defaults to surface new keys after upgrades
    out = {**DEFAULT_INTEGRATIONS, **doc}
    out["whatsapp"] = {**DEFAULT_INTEGRATIONS["whatsapp"], **(doc.get("whatsapp") or {})}
    out["smtp"] = {**DEFAULT_INTEGRATIONS["smtp"], **(doc.get("smtp") or {})}
    return out


def _mask_secret(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val)
    if len(s) <= 6:
        return "•" * len(s)
    return s[:3] + "•" * max(4, len(s) - 6) + s[-3:]


def _public_integrations(d: dict) -> dict:
    out = {**d}
    out["whatsapp"] = {**d["whatsapp"], "token": _mask_secret(d["whatsapp"].get("token"))}
    out["smtp"] = {**d["smtp"], "password": _mask_secret(d["smtp"].get("password"))}
    return out


def _normalise_phone(phone: str, default_cc: str = "91") -> str:
    """Strip non-digits; if 10 digits, prepend default country code."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if len(digits) == 10:
        digits = (default_cc or "91") + digits
    if digits.startswith("0"):
        digits = digits.lstrip("0")
        if len(digits) == 10:
            digits = (default_cc or "91") + digits
    return digits


async def _send_whatsapp_template(
    wa: dict,
    phone: str,
    template_name: str,
    template_language: str,
    field_1: Optional[str] = None,
    button_0: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Send a WhatsApp template via BizChatAPI. Raises HTTPException on failure."""
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and template_name):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/send-template-message"
    payload: Dict[str, Any] = {
        "from_phone_number_id": wa.get("from_phone_number_id") or "",
        "phone_number": _normalise_phone(phone, wa.get("default_country_code") or "91"),
        "template_name": template_name,
        "template_language": template_language or "en",
    }
    if field_1 is not None:
        payload["field_1"] = str(field_1)
    if button_0 is not None:
        payload["button_0"] = str(button_0)
    if extra:
        payload.update(extra)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, params={"token": wa["token"]}, json=payload)
        body: Any
        try:
            body = r.json()
        except Exception:
            body = r.text
        if r.status_code >= 400:
            logger.error(f"[WA] template send failed status={r.status_code} body={body}")
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        logger.info(f"[WA] template={template_name} → {payload['phone_number']} ok")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        logger.exception("[WA] HTTP error")
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def _send_whatsapp_text(wa: dict, phone: str, message: str) -> dict:
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/send-message"
    payload = {
        "from_phone_number_id": wa.get("from_phone_number_id") or "",
        "phone_number": _normalise_phone(phone, wa.get("default_country_code") or "91"),
        "message_body": message,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, params={"token": wa["token"]}, json=payload)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code >= 400:
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


class WhatsAppSettingsIn(BaseModel):
    enabled: bool = False
    api_base_url: str = "https://bizchatapi.in/api"
    vendor_uid: str = ""
    token: Optional[str] = None  # null = keep existing
    from_phone_number_id: str = ""
    otp_template_name: str = ""
    otp_template_language: str = "en"
    default_country_code: str = "91"


class SmtpSettingsIn(BaseModel):
    enabled: bool = False
    host: str = "smtp.hostinger.com"
    port: int = 465
    use_ssl: bool = True
    username: str = ""
    password: Optional[str] = None  # null = keep existing
    from_email: str = ""
    from_name: str = "HRE Exporter"


class IntegrationsIn(BaseModel):
    whatsapp: Optional[WhatsAppSettingsIn] = None
    smtp: Optional[SmtpSettingsIn] = None


@api.get("/settings/integrations")
async def get_integrations(_: dict = Depends(require_role("admin", "manager"))):
    cur = await _get_integrations()
    return _public_integrations(cur)


@api.put("/settings/integrations")
async def update_integrations(data: IntegrationsIn, _: dict = Depends(require_role("admin"))):
    cur = await _get_integrations()
    if data.whatsapp is not None:
        wa_in = data.whatsapp.model_dump()
        if wa_in.get("token") in (None, ""):
            wa_in["token"] = cur["whatsapp"].get("token", "")
        cur["whatsapp"] = {**cur["whatsapp"], **wa_in}
    if data.smtp is not None:
        sm_in = data.smtp.model_dump()
        if sm_in.get("password") in (None, ""):
            sm_in["password"] = cur["smtp"].get("password", "")
        cur["smtp"] = {**cur["smtp"], **sm_in}
    cur["id"] = SETTINGS_DOC_ID
    cur["updated_at"] = now_iso()
    await db.settings.update_one(
        {"id": SETTINGS_DOC_ID},
        {"$set": cur},
        upsert=True,
    )
    refreshed = await _get_integrations()
    return _public_integrations(refreshed)


class WhatsAppTestIn(BaseModel):
    phone: str
    mode: str = "template"  # "template" | "text"
    message: Optional[str] = None  # for text mode
    sample_otp: Optional[str] = "123456"  # for template mode


@api.post("/settings/whatsapp/test")
async def test_whatsapp_send(data: WhatsAppTestIn, _: dict = Depends(require_role("admin", "manager"))):
    cur = await _get_integrations()
    wa = cur["whatsapp"]
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=400, detail="Save & enable WhatsApp settings first")
    if data.mode == "text":
        if not data.message:
            raise HTTPException(status_code=400, detail="Message required for text mode")
        body = await _send_whatsapp_text(wa, data.phone, data.message)
        return {"ok": True, "mode": "text", "response": body}
    if not wa.get("otp_template_name"):
        raise HTTPException(status_code=400, detail="OTP template name not set")
    body = await _send_whatsapp_template(
        wa, data.phone,
        template_name=wa["otp_template_name"],
        template_language=wa.get("otp_template_language") or "en",
        field_1=data.sample_otp or "123456",
        button_0=data.sample_otp or "123456",
    )
    return {"ok": True, "mode": "template", "response": body}


class SmtpTestIn(BaseModel):
    to_email: EmailStr
    subject: str = "HRE Exporter SMTP test"
    body: str = "If you can read this, your Hostinger SMTP credentials are wired correctly."


@api.post("/settings/smtp/test")
async def test_smtp_send(data: SmtpTestIn, _: dict = Depends(require_role("admin", "manager"))):
    cur = await _get_integrations()
    sm = cur["smtp"]
    if not (sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email")):
        raise HTTPException(status_code=400, detail="Save & enable SMTP settings first")
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.utils import formataddr
        msg = MIMEText(data.body, "plain", "utf-8")
        msg["Subject"] = data.subject
        msg["From"] = formataddr((sm.get("from_name") or "HRE Exporter", sm["from_email"]))
        msg["To"] = data.to_email
        if sm.get("use_ssl") or int(sm.get("port", 465)) == 465:
            with smtplib.SMTP_SSL(sm["host"], int(sm.get("port", 465)), timeout=20) as server:
                server.login(sm["username"], sm["password"])
                server.sendmail(sm["from_email"], [data.to_email], msg.as_string())
        else:
            with smtplib.SMTP(sm["host"], int(sm.get("port", 587)), timeout=20) as server:
                server.starttls()
                server.login(sm["username"], sm["password"])
                server.sendmail(sm["from_email"], [data.to_email], msg.as_string())
        return {"ok": True}
    except Exception as e:
        logger.exception("[SMTP] test failed")
        raise HTTPException(status_code=502, detail=f"SMTP send failed: {e}")


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _strip_pricing_fields(obj: dict) -> dict:
    out = dict(obj)
    for f in ("base_price", "discount_percentage", "final_price", "manual_price", "manual_price_override"):
        out.pop(f, None)
    return out


@api.get("/public/catalogue")
async def public_catalogue():
    fams = await db.product_families.find({"active": True}, {"_id": 0}).sort("family_name", 1).to_list(500)
    mats = await db.materials.find({"active": True}, {"_id": 0}).to_list(50)
    cats = await db.categories.find({"active": True}, {"_id": 0}).to_list(500)
    return {"families": fams, "materials": mats, "categories": cats}


@api.get("/public/family/{fid}")
async def public_family(fid: str):
    fam = await db.product_families.find_one({"id": fid, "active": True}, {"_id": 0})
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    variants = await db.product_variants.find({"product_family_id": fid, "active": True}, {"_id": 0}).to_list(2000)
    return {"family": fam, "variants": [_strip_pricing_fields(v) for v in variants]}


class QuoteRequestStart(BaseModel):
    name: str
    company: Optional[str] = ""
    phone: str
    email: Optional[str] = ""
    gst_number: Optional[str] = ""
    state: Optional[str] = ""
    billing_address: Optional[str] = ""
    shipping_address: Optional[str] = ""


@api.post("/public/quote-requests/start")
async def public_qr_start(data: QuoteRequestStart):
    if not data.phone or len(_norm_phone(data.phone)) < 10:
        raise HTTPException(status_code=400, detail="Valid 10-digit phone number required")
    doc = {
        "id": str(uuid.uuid4()),
        "name": data.name.strip(),
        "company": (data.company or "").strip(),
        "phone": data.phone.strip(),
        "phone_norm": _norm_phone(data.phone),
        "email": _norm_email(data.email),
        "gst_number": data.gst_number or "",
        "state": data.state or "",
        "billing_address": data.billing_address or "",
        "shipping_address": data.shipping_address or "",
        "verified": False,
        "session_token": None,
        "session_expires_at": None,
        "otp_hash": None,
        "otp_expires_at": None,
        "otp_attempts": 0,
        "created_at": now_iso(),
    }
    await db.quote_requests.insert_one(doc.copy())
    return {"request_id": doc["id"]}


@api.post("/public/quote-requests/{rid}/send-otp")
async def public_qr_send_otp(rid: str):
    qr = await db.quote_requests.find_one({"id": rid}, {"_id": 0})
    if not qr:
        raise HTTPException(status_code=404, detail="Request not found")
    code = f"{secrets.randbelow(900000) + 100000}"
    expires = _now_dt() + timedelta(seconds=OTP_TTL_SECONDS)
    await db.quote_requests.update_one(
        {"id": rid},
        {"$set": {
            "otp_hash": _hash_otp(code),
            "otp_expires_at": expires.isoformat(),
            "otp_attempts": 0,
        }},
    )
    cur = await _get_integrations()
    wa = cur["whatsapp"]
    delivery = "dev"
    delivery_error: Optional[str] = None
    if wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and wa.get("otp_template_name"):
        try:
            await _send_whatsapp_template(
                wa,
                qr["phone"],
                template_name=wa["otp_template_name"],
                template_language=wa.get("otp_template_language") or "en",
                field_1=code,
                button_0=code,  # for COPY_CODE / URL button OTP templates
            )
            delivery = "whatsapp"
        except HTTPException as e:
            delivery_error = str(e.detail)
            logger.error(f"[OTP] WhatsApp send failed for {rid}: {delivery_error}")
        except Exception as e:
            delivery_error = str(e)
            logger.exception(f"[OTP] unexpected WhatsApp error for {rid}")

    logger.info(f"[OTP] phone={qr['phone']} code={code} delivery={delivery} (request_id={rid})")
    resp: Dict[str, Any] = {"ok": True, "expires_in": OTP_TTL_SECONDS, "delivery": delivery}
    if delivery_error:
        resp["delivery_error"] = delivery_error
    if DEV_OTP_PASSTHROUGH and delivery != "whatsapp":
        resp["dev_otp"] = code
    return resp


class OtpVerify(BaseModel):
    code: str


@api.post("/public/quote-requests/{rid}/verify-otp")
async def public_qr_verify_otp(rid: str, data: OtpVerify):
    qr = await db.quote_requests.find_one({"id": rid}, {"_id": 0})
    if not qr or not qr.get("otp_hash"):
        raise HTTPException(status_code=404, detail="Request not found or no OTP issued")
    if qr.get("otp_attempts", 0) >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed attempts — request a new OTP")
    if qr.get("otp_expires_at") and datetime.fromisoformat(qr["otp_expires_at"]) < _now_dt():
        raise HTTPException(status_code=400, detail="OTP expired — request a new one")
    if _hash_otp((data.code or "").strip()) != qr["otp_hash"]:
        await db.quote_requests.update_one({"id": rid}, {"$inc": {"otp_attempts": 1}})
        raise HTTPException(status_code=400, detail="Incorrect OTP")
    token = secrets.token_urlsafe(32)
    sess_exp = _now_dt() + timedelta(days=SESSION_TTL_DAYS)
    await db.quote_requests.update_one(
        {"id": rid},
        {"$set": {
            "verified": True,
            "session_token": token,
            "session_expires_at": sess_exp.isoformat(),
            "otp_hash": None,
            "verified_at": now_iso(),
        }},
    )
    await db.public_sessions.insert_one({
        "token": token,
        "phone_norm": qr["phone_norm"],
        "request_id": rid,
        "expires_at": sess_exp.isoformat(),
        "created_at": now_iso(),
    })
    return {"token": token, "expires_in_days": SESSION_TTL_DAYS}


async def _resolve_public_session(token: Optional[str]) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Verification token required")
    sess = await db.public_sessions.find_one({"token": token}, {"_id": 0})
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if datetime.fromisoformat(sess["expires_at"]) < _now_dt():
        raise HTTPException(status_code=401, detail="Session expired")
    return sess


@api.get("/public/variants")
async def public_variants(token: str, q: Optional[str] = None):
    """After OTP verification: list active variants WITH prices for the cart."""
    await _resolve_public_session(token)
    query: Dict[str, Any] = {"active": True}
    if q:
        rx = re.escape(q)
        query["$or"] = [
            {"product_code": {"$regex": rx, "$options": "i"}},
            {"product_name": {"$regex": rx, "$options": "i"}},
        ]
    items = await db.product_variants.find(query, {"_id": 0}).sort("product_code", 1).to_list(5000)
    return items


class CartLine(BaseModel):
    product_variant_id: str
    quantity: float


class FinalisePayload(BaseModel):
    items: List[CartLine]
    notes: Optional[str] = ""


@api.post("/public/quote-requests/{rid}/finalise")
async def public_qr_finalise(rid: str, payload: FinalisePayload, token: str):
    sess = await _resolve_public_session(token)
    if sess.get("request_id") != rid:
        raise HTTPException(status_code=403, detail="Session does not match this request")
    qr = await db.quote_requests.find_one({"id": rid}, {"_id": 0})
    if not qr or not qr.get("verified"):
        raise HTTPException(status_code=400, detail="Phone number not verified")
    if not payload.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    existing = await _find_contact_match(qr.get("phone", ""), qr.get("email", ""))
    if existing:
        contact_id = existing["id"]
        await db.contacts.update_one(
            {"id": contact_id},
            {"$set": {
                "name": qr["name"] or existing.get("name"),
                "company": qr.get("company") or existing.get("company"),
                "email": qr.get("email") or existing.get("email"),
                "email_norm": _norm_email(qr.get("email")),
                "phone": qr.get("phone") or existing.get("phone"),
                "phone_norm": qr["phone_norm"],
                "gst_number": qr.get("gst_number") or existing.get("gst_number"),
                "state": qr.get("state") or existing.get("state"),
                "billing_address": qr.get("billing_address") or existing.get("billing_address"),
                "shipping_address": qr.get("shipping_address") or existing.get("shipping_address"),
                "source": "public",
                "updated_at": now_iso(),
            }},
        )
        contact = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
    else:
        contact = {
            "id": str(uuid.uuid4()),
            "name": qr["name"], "company": qr.get("company", ""),
            "phone": qr.get("phone", ""), "phone_norm": qr["phone_norm"],
            "email": qr.get("email", ""), "email_norm": _norm_email(qr.get("email")),
            "gst_number": qr.get("gst_number", ""),
            "state": qr.get("state", ""),
            "country": "India",
            "billing_address": qr.get("billing_address", ""),
            "shipping_address": qr.get("shipping_address", ""),
            "source": "public",
            "tags": [], "notes": "",
            "created_by": "self-service",
            "created_at": now_iso(), "updated_at": now_iso(),
        }
        await db.contacts.insert_one(contact.copy())
        contact.pop("_id", None)

    line_items: List[Dict[str, Any]] = []
    for ci in payload.items:
        v = await db.product_variants.find_one({"id": ci.product_variant_id, "active": True}, {"_id": 0})
        if not v:
            continue
        fam = await db.product_families.find_one({"id": v["product_family_id"]}, {"_id": 0})
        line_items.append({
            "product_variant_id": v["id"],
            "product_code": v["product_code"],
            "family_name": (fam or {}).get("family_name", ""),
            "description": "",
            "cable_size": v.get("cable_size", ""),
            "hole_size": v.get("hole_size", ""),
            "dimensions": v.get("dimensions", {}),
            "hsn_code": v.get("hsn_code", "85369090"),
            "quantity": float(ci.quantity or 0),
            "unit": v.get("unit", "NOS"),
            "base_price": float(v.get("final_price") or v.get("base_price") or 0),
            "discount_percentage": 0.0,
            "gst_percentage": float(v.get("gst_percentage") or 18),
        })
    if not line_items:
        raise HTTPException(status_code=400, detail="No valid variants in cart")

    totals = _compute_quote_totals(line_items)
    qnum = await _next_quote_number()
    quote = {
        "id": str(uuid.uuid4()),
        "quote_number": qnum,
        "version": 1,
        "parent_quote_id": None,
        "status": "sent",
        "self_service": True,
        "contact_id": contact["id"],
        "contact_name": contact.get("name", ""),
        "contact_company": contact.get("company", ""),
        "contact_email": contact.get("email", ""),
        "contact_phone": contact.get("phone", ""),
        "contact_gst": contact.get("gst_number", ""),
        "billing_address": contact.get("billing_address", ""),
        "shipping_address": contact.get("shipping_address", ""),
        "place_of_supply": contact.get("state", ""),
        "currency": "INR",
        "valid_until": (_now_dt() + timedelta(days=30)).date().isoformat(),
        "notes": payload.notes or "",
        "terms": "Prices are exclusive of freight unless specified.\nValidity: 30 days.\nPayment: 50% advance, 50% before dispatch.",
        "line_items": line_items,
        **totals,
        "created_by": f"self-service ({contact.get('phone', '')})",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "sent_at": now_iso(),
        "approved_at": None, "rejected_at": None,
    }
    await db.quotations.insert_one(quote.copy())
    # TODO Wave B: dispatch Hostinger SMTP email + WhatsApp here.
    logger.info(f"[Self-Service Quote] {qnum} created for {contact.get('phone')}")
    return {"id": quote["id"], "quote_number": qnum, "grand_total": quote["grand_total"]}


@api.get("/public/my-quotes")
async def public_my_quotes(token: str):
    sess = await _resolve_public_session(token)
    contacts = await db.contacts.find({"phone_norm": sess["phone_norm"]}, {"_id": 0}).to_list(50)
    cids = [c["id"] for c in contacts]
    if not cids:
        return []
    items = await db.quotations.find({"contact_id": {"$in": cids}}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items


@api.get("/public/quote/{qid}")
async def public_quote_view(qid: str, token: str):
    sess = await _resolve_public_session(token)
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    contact = await db.contacts.find_one({"id": quote["contact_id"]}, {"_id": 0})
    if not contact or contact.get("phone_norm") != sess["phone_norm"]:
        raise HTTPException(status_code=403, detail="This quote does not belong to your phone")
    return quote


class PhoneOnlyOtp(BaseModel):
    phone: str


@api.post("/public/my-quotes/login/start")
async def public_login_start(data: PhoneOnlyOtp):
    pn = _norm_phone(data.phone)
    if len(pn) < 10:
        raise HTTPException(status_code=400, detail="Valid 10-digit phone number required")
    rid = str(uuid.uuid4())
    code = f"{secrets.randbelow(900000) + 100000}"
    expires = _now_dt() + timedelta(seconds=OTP_TTL_SECONDS)
    await db.quote_requests.insert_one({
        "id": rid,
        "name": "", "company": "", "phone": data.phone, "phone_norm": pn,
        "email": "", "gst_number": "", "state": "",
        "billing_address": "", "shipping_address": "",
        "verified": False, "session_token": None, "session_expires_at": None,
        "otp_hash": _hash_otp(code), "otp_expires_at": expires.isoformat(),
        "otp_attempts": 0, "created_at": now_iso(), "kind": "login",
    })
    logger.info(f"[OTP-LOGIN] phone={data.phone} code={code} (request_id={rid})")
    resp = {"request_id": rid, "expires_in": OTP_TTL_SECONDS}
    if DEV_OTP_PASSTHROUGH:
        resp["dev_otp"] = code
    return resp


# ---------- Mount ----------
app.include_router(api)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

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

    # Catalogue seed runs ONCE on initial install. Once user manages catalogue,
    # we never recreate deleted records. Toggle the system_meta flag to re-seed.
    meta = await db.system_meta.find_one({"key": "catalogue_seeded"})
    if meta and meta.get("value") is True:
        return
    logger.info("Seeding initial catalogue (first-run only)…")

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

    # Mark catalogue as seeded so subsequent restarts skip the catalogue seed.
    await db.system_meta.update_one(
        {"key": "catalogue_seeded"},
        {"$set": {"key": "catalogue_seeded", "value": True, "seeded_at": now_iso()}},
        upsert=True,
    )
    logger.info("Catalogue seed complete · marked as seeded")


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
