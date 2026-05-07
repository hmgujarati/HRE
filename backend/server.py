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
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, Form, status
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


@api.post("/quotations/{qid}/send")
async def send_quotation_dispatch(qid: str, _: dict = Depends(require_role("admin", "manager"))):
    """Manually generate the PDF and dispatch to customer via WhatsApp + Email."""
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")
    delivery = await _dispatch_finalised_quote(quote)
    # PDF was always generated; mark as 'sent' even when no channel was configured —
    # admin will share the link manually. Otherwise the customer cannot submit a PO.
    pdf_ok = bool(delivery.get("pdf"))
    channel_ok = bool(delivery.get("whatsapp") or delivery.get("email"))
    if pdf_ok or channel_ok:
        await db.quotations.update_one(
            {"id": qid},
            {"$set": {"sent_at": now_iso(), "status": "sent" if quote.get("status") == "draft" else quote.get("status")}},
        )
    return delivery


@api.post("/quotations/{qid}/refresh-delivery")
async def refresh_quotation_delivery(qid: str, _: dict = Depends(require_role("admin", "manager"))):
    """Poll BizChat message-status for every WhatsApp dispatch log entry that isn't
    in a terminal state yet and update the stored status in-place."""
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")
    cur = await _get_integrations()
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
            data = await _get_whatsapp_message_status(wa, wamid)
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
    # Return updated quote for the UI to re-render
    updated = await db.quotations.find_one({"id": qid}, {"_id": 0})
    return {"updates": updates, "dispatch_log": (updated or {}).get("dispatch_log", [])}


@api.get("/quotations/{qid}/pdf")
async def get_quotation_pdf(qid: str, _: dict = Depends(require_role("admin", "manager"))):
    """Render (or re-render) the quotation PDF and return its public path."""
    from fastapi.responses import FileResponse
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")
    pdf = await _generate_quote_pdf(quote)
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


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
        # Quote dispatch (Wave B-2)
        "quote_template_name": "",
        "quote_template_language": "en",
        # Webhook receiver (Wave B-3)
        "webhook_secret": "",
        # Order tracking auto-notify templates (Phase 2C)
        "order_pi_template": "",
        "order_production_template": "",
        "order_packaging_template": "",
        "order_dispatched_template": "",
        "order_lr_template": "",
        # Internal admin alert when customer submits a PO (Phase 2C-customer)
        "admin_notify_phone": "",
        "po_received_admin_template": "",
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
        # Internal admin alert email (falls back to from_email if blank)
        "admin_notify_email": "",
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
    # Auto-generate a webhook secret once so the admin can register the URL
    if not out["whatsapp"].get("webhook_secret"):
        out["whatsapp"]["webhook_secret"] = secrets.token_urlsafe(24)
        await db.settings.update_one(
            {"id": SETTINGS_DOC_ID},
            {"$set": {"whatsapp.webhook_secret": out["whatsapp"]["webhook_secret"], "id": SETTINGS_DOC_ID}},
            upsert=True,
        )
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
    """Send a WhatsApp template via BizChatAPI. Raises HTTPException on failure.
    Returns BizChat response body (includes data.wamid, data.status, data.log_uid)."""
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
        # Treat "result": "error" in body as failure too
        if isinstance(body, dict) and body.get("result") == "error":
            detail = body.get("message") or "Unknown BizChat error"
            logger.error(f"[WA] template send error body={body}")
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        # Strong success signal: BizChat sets data.wamid only when the message is
        # actually accepted/queued. Some misconfigurations return 200 OK with an
        # empty/missing wamid — treat that as failure so callers don't get a false
        # 'delivery: whatsapp' confirmation.
        if isinstance(body, dict):
            data = body.get("data") or {}
            if not (data.get("wamid") or data.get("log_uid")):
                detail = body.get("message") or body.get("error") or "BizChat accepted the request but did not return a message ID"
                logger.error(f"[WA] template send returned no wamid body={body}")
                raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {detail}")
        logger.info(f"[WA] template={template_name} → {payload['phone_number']} ok")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        logger.exception("[WA] HTTP error")
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def _get_whatsapp_message_status(wa: dict, wamid: str) -> dict:
    """Poll BizChat message-status endpoint. Returns {status, created_at, updated_at}."""
    if not (wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=400, detail="WhatsApp credentials missing")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/message-status"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params={"wamid": wamid, "token": wa["token"]})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("result") == "error"):
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"Status fetch failed: {detail}")
        return body.get("data") or {}
    except httpx.HTTPError as e:
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


async def _fetch_whatsapp_templates(wa: dict) -> Any:
    if not (wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=400, detail="Save Vendor UID and Token first")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/template-list"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, params={"token": wa["token"]})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code >= 400:
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"Template list fetch failed: {detail}")
        return body
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


async def _send_whatsapp_document(
    wa: dict,
    phone: str,
    media_url: str,
    file_name: str,
    caption: Optional[str] = None,
) -> dict:
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    url = f"{wa['api_base_url'].rstrip('/')}/{wa['vendor_uid']}/contact/send-media-message"
    payload = {
        "from_phone_number_id": wa.get("from_phone_number_id") or "",
        "phone_number": _normalise_phone(phone, wa.get("default_country_code") or "91"),
        "media_type": "document",
        "media_url": media_url,
        "file_name": file_name,
        "caption": caption or "",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, params={"token": wa["token"]}, json=payload)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code >= 400:
            detail = body.get("message") if isinstance(body, dict) else str(body)
            raise HTTPException(status_code=502, detail=f"WhatsApp document send failed: {detail}")
        return body if isinstance(body, dict) else {"raw": body}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp HTTP error: {e}")


def _send_smtp_email(
    sm: dict,
    to_email: str,
    subject: str,
    body_text: str,
    attachments: Optional[List[Path]] = None,
    body_html: Optional[str] = None,
) -> None:
    """Synchronous SMTP send (called from async via run_in_executor).
    If body_html is provided, sends a multipart/alternative with both text + html,
    so tracking pixels inside HTML still render in HTML-capable clients."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    from email.utils import formataddr

    outer = MIMEMultipart("mixed")
    outer["Subject"] = subject
    outer["From"] = formataddr((sm.get("from_name") or "HRE Exporter", sm["from_email"]))
    outer["To"] = to_email

    if body_html:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body_text, "plain", "utf-8"))
        alt.attach(MIMEText(body_html, "html", "utf-8"))
        outer.attach(alt)
    else:
        outer.attach(MIMEText(body_text, "plain", "utf-8"))

    for path in (attachments or []):
        if not path or not path.exists():
            continue
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        outer.attach(part)
    if sm.get("use_ssl") or int(sm.get("port", 465)) == 465:
        with smtplib.SMTP_SSL(sm["host"], int(sm.get("port", 465)), timeout=30) as server:
            server.login(sm["username"], sm["password"])
            server.sendmail(sm["from_email"], [to_email], outer.as_string())
    else:
        with smtplib.SMTP(sm["host"], int(sm.get("port", 587)), timeout=30) as server:
            server.starttls()
            server.login(sm["username"], sm["password"])
            server.sendmail(sm["from_email"], [to_email], outer.as_string())


async def _generate_quote_pdf(quote: dict, unique: bool = False) -> Path:
    """Render the quote to a PDF saved under uploads/quotes/.
    When `unique=True`, a timestamp suffix is appended so caches (WhatsApp/Meta
    media cache keyed on URL) always fetch a fresh copy."""
    from quote_pdf import render_quote_pdf
    out_dir = UPLOAD_DIR / "quotes"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", quote.get("quote_number") or quote["id"])
    if unique:
        ts = _now_dt().strftime("%Y%m%d%H%M%S")
        out = out_dir / f"{safe_name}_{ts}.pdf"
    else:
        out = out_dir / f"{safe_name}.pdf"
    logo = UPLOAD_DIR.parent.parent / "frontend" / "public" / "hre-logo-light-bg.png"
    logo_url = logo.as_uri() if logo.exists() else None
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, render_quote_pdf, quote, out, logo_url)
    return out


async def _dispatch_finalised_quote(quote: dict) -> dict:
    """Generate PDF, then deliver via WhatsApp template + SMTP if configured.
    Returns delivery telemetry. Never raises — failures are logged + returned.
    Also appends a dispatch log entry to the quotation document."""
    cur = await _get_integrations()
    wa = cur["whatsapp"]
    sm = cur["smtp"]
    delivery = {"pdf": False, "whatsapp": False, "email": False, "errors": {}}
    log_entries: List[Dict[str, Any]] = []

    # Pull fresh contact snapshot for email/phone — quote stores a snapshot at creation.
    contact_email = (quote.get("contact_email") or "").strip()
    contact_phone = (quote.get("contact_phone") or "").strip()
    contact_name = quote.get("contact_name") or ""
    contact_company = quote.get("contact_company") or ""
    if quote.get("contact_id"):
        live = await db.contacts.find_one({"id": quote["contact_id"]}, {"_id": 0})
        if live:
            contact_email = contact_email or (live.get("email") or "").strip()
            contact_phone = contact_phone or (live.get("phone") or "").strip()
            contact_name = contact_name or live.get("name") or ""
            contact_company = contact_company or live.get("company") or ""

    try:
        pdf_path = await _generate_quote_pdf(quote, unique=True)
        delivery["pdf"] = True
        delivery["pdf_path"] = pdf_path.name
    except Exception as e:
        logger.exception("[Quote PDF] generation failed")
        delivery["errors"]["pdf"] = str(e)
        return delivery

    public_pdf_url = f"{PUBLIC_BASE_URL}/api/uploads/quotes/{pdf_path.name}" if PUBLIC_BASE_URL else None
    dispatched_at = now_iso()

    # WhatsApp document template
    if wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and wa.get("quote_template_name"):
        wa_entry: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "channel": "whatsapp",
            "template": wa["quote_template_name"],
            "to": _normalise_phone(contact_phone, wa.get("default_country_code") or "91") if contact_phone else "",
            "pdf_file": pdf_path.name,
            "pdf_url": public_pdf_url,
            "sent_at": dispatched_at,
            "status": "pending",
        }
        if not contact_phone:
            wa_entry["status"] = "failed"
            wa_entry["error"] = "no contact phone"
            delivery["errors"]["whatsapp"] = "no contact phone"
        elif not public_pdf_url:
            wa_entry["status"] = "failed"
            wa_entry["error"] = "PUBLIC_BASE_URL not configured"
            delivery["errors"]["whatsapp"] = "PUBLIC_BASE_URL not configured for media URL"
        else:
            try:
                customer_name = contact_name or contact_company or "Customer"
                grand_inr = float(quote.get("grand_total") or 0)
                grand_str = f"Total: ₹{grand_inr:,.2f}"
                line_count = len(quote.get("line_items") or [])
                valid_iso = (quote.get("valid_until") or "").strip()
                if valid_iso:
                    try:
                        d = datetime.fromisoformat(valid_iso)
                        valid_str = f"Valid till {d.strftime('%d-%m-%Y')} · {line_count} item{'s' if line_count != 1 else ''}"
                    except Exception:
                        valid_str = f"{line_count} item{'s' if line_count != 1 else ''} · validity 30 days"
                else:
                    valid_str = f"{line_count} item{'s' if line_count != 1 else ''} · validity 30 days"
                body = await _send_whatsapp_template(
                    wa,
                    contact_phone,
                    template_name=wa["quote_template_name"],
                    template_language=wa.get("quote_template_language") or "en",
                    field_1=customer_name,
                    extra={
                        "field_2": quote.get("quote_number", ""),
                        "field_3": grand_str,
                        "field_4": valid_str,
                        "header_document": public_pdf_url,
                        "header_document_name": f"{quote.get('quote_number') or 'quotation'}.pdf",
                    },
                )
                data = body.get("data") if isinstance(body, dict) else None
                wa_entry["wamid"] = (data or {}).get("wamid")
                wa_entry["log_uid"] = (data or {}).get("log_uid")
                wa_entry["status"] = (data or {}).get("status", "sent") or "sent"
                delivery["whatsapp"] = True
            except HTTPException as e:
                wa_entry["status"] = "failed"
                wa_entry["error"] = str(e.detail)
                delivery["errors"]["whatsapp"] = str(e.detail)
            except Exception as e:
                logger.exception("[Quote WA] dispatch failed")
                wa_entry["status"] = "failed"
                wa_entry["error"] = str(e)
                delivery["errors"]["whatsapp"] = str(e)
        log_entries.append(wa_entry)

    # SMTP email
    if sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        email_entry: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "channel": "email",
            "to": contact_email,
            "pdf_file": pdf_path.name,
            "sent_at": dispatched_at,
            "status": "pending",
        }
        if contact_email:
            try:
                open_token = secrets.token_urlsafe(24)
                email_entry["open_token"] = open_token
                pixel_url = (
                    f"{PUBLIC_BASE_URL}/api/webhooks/email/open?t={open_token}"
                    if PUBLIC_BASE_URL else ""
                )
                subject = f"Quotation {quote.get('quote_number')} from HRE Exporter"
                grand_txt = f"₹{float(quote.get('grand_total') or 0):,.2f}"
                body_txt = (
                    f"Dear {contact_name or 'Sir/Madam'},\n\n"
                    f"Please find attached the quotation {quote.get('quote_number')} for your inquiry.\n\n"
                    f"Grand Total: {grand_txt}\n\n"
                    f"For any queries, contact us at {SELLER_INFO_EMAIL}.\n\n"
                    f"Regards,\nHRE Exporter Team"
                )
                body_html = f"""<!DOCTYPE html>
<html><body style="font-family: Arial, Helvetica, sans-serif; color: #1A1A1A; background: #f5f5f5; margin:0; padding:24px;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width: 580px; margin: 0 auto; background:#fff; border:1px solid #e5e5e5;">
    <tr><td style="padding: 24px 28px; border-bottom: 3px solid #FBAE17;">
      <div style="font-size: 10px; letter-spacing: 3px; text-transform: uppercase; color:#FBAE17; font-weight:bold;">HREXPORTER · Quotation</div>
      <h1 style="margin: 6px 0 0; font-size: 22px; letter-spacing: -0.5px;">{quote.get('quote_number','')}</h1>
    </td></tr>
    <tr><td style="padding: 24px 28px; font-size: 14px; line-height: 1.6;">
      <p style="margin:0 0 14px;">Dear <strong>{contact_name or 'Sir/Madam'}</strong>,</p>
      <p style="margin:0 0 14px;">Please find the attached quotation for your recent inquiry. You can open it on any phone or desktop — it carries our full GST breakdown, bank details and terms.</p>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#FBAE17; margin: 14px 0;">
        <tr><td style="padding: 14px 18px;">
          <div style="font-size: 10px; letter-spacing: 2px; text-transform: uppercase; font-weight: bold;">Grand Total</div>
          <div style="font-size: 24px; font-weight: 900; font-family: 'Courier New', monospace;">{grand_txt}</div>
        </td></tr>
      </table>
      <p style="margin:0 0 6px;">If you have any questions, reply to this email or WhatsApp us — we'd love to help finalize this order for you.</p>
      <p style="margin: 14px 0 0; color: #666; font-size: 12px;">Regards,<br/><strong>HRE Exporter Team</strong><br/><a href="mailto:{SELLER_INFO_EMAIL}" style="color:#1A1A1A;">{SELLER_INFO_EMAIL}</a></p>
    </td></tr>
    <tr><td style="padding: 14px 28px; font-size: 10px; color:#999; border-top:1px solid #eee;">
      This quotation is confidential and intended for {contact_email}. If you received this by mistake, please delete it.
    </td></tr>
  </table>
  {f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:block; border:0;" />' if pixel_url else ''}
</body></html>"""
                import asyncio
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, _send_smtp_email, sm, contact_email, subject, body_txt, [pdf_path], body_html,
                )
                email_entry["status"] = "sent"
                delivery["email"] = True
            except Exception as e:
                logger.exception("[Quote SMTP] dispatch failed")
                email_entry["status"] = "failed"
                email_entry["error"] = str(e)
                delivery["errors"]["email"] = str(e)
        else:
            email_entry["status"] = "failed"
            email_entry["error"] = "no contact email"
            delivery["errors"]["email"] = "no contact email"
        log_entries.append(email_entry)

    if log_entries:
        try:
            await db.quotations.update_one(
                {"id": quote["id"]},
                {
                    "$push": {"dispatch_log": {"$each": log_entries}},
                    "$set": {"last_dispatched_at": dispatched_at},
                },
            )
        except Exception:
            logger.exception("[Dispatch] failed to persist dispatch log")

    delivery["log_entries"] = log_entries
    return delivery


PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
SELLER_INFO_EMAIL = "info@hrexporter.com"


class WhatsAppSettingsIn(BaseModel):
    enabled: bool = False
    api_base_url: str = "https://bizchatapi.in/api"
    vendor_uid: str = ""
    token: Optional[str] = None  # null = keep existing
    from_phone_number_id: str = ""
    otp_template_name: str = ""
    otp_template_language: str = "en"
    default_country_code: str = "91"
    quote_template_name: str = ""
    quote_template_language: str = "en"
    order_pi_template: str = ""
    order_production_template: str = ""
    order_packaging_template: str = ""
    order_dispatched_template: str = ""
    order_lr_template: str = ""
    admin_notify_phone: str = ""
    po_received_admin_template: str = ""
    webhook_secret_rotate: Optional[bool] = False  # non-persisted: trigger fresh secret


class SmtpSettingsIn(BaseModel):
    enabled: bool = False
    host: str = "smtp.hostinger.com"
    port: int = 465
    use_ssl: bool = True
    username: str = ""
    password: Optional[str] = None  # null = keep existing
    from_email: str = ""
    from_name: str = "HRE Exporter"
    admin_notify_email: str = ""


class IntegrationsIn(BaseModel):
    whatsapp: Optional[WhatsAppSettingsIn] = None
    smtp: Optional[SmtpSettingsIn] = None


@api.get("/settings/integrations")
async def get_integrations(_: dict = Depends(require_role("admin", "manager"))):
    cur = await _get_integrations()
    resp = _public_integrations(cur)
    # Expose the ready-to-register webhook URL for the BizChat admin console
    if PUBLIC_BASE_URL and cur["whatsapp"].get("webhook_secret"):
        resp["whatsapp"]["webhook_url"] = (
            f"{PUBLIC_BASE_URL}/api/webhooks/bizchat/status?secret={cur['whatsapp']['webhook_secret']}"
        )
    else:
        resp["whatsapp"]["webhook_url"] = ""
    return resp


@api.put("/settings/integrations")
async def update_integrations(data: IntegrationsIn, _: dict = Depends(require_role("admin"))):
    cur = await _get_integrations()
    if data.whatsapp is not None:
        wa_in = data.whatsapp.model_dump()
        # Handle secret rotation
        rotate = wa_in.pop("webhook_secret_rotate", False)
        if wa_in.get("token") in (None, ""):
            wa_in["token"] = cur["whatsapp"].get("token", "")
        cur["whatsapp"] = {**cur["whatsapp"], **wa_in}
        if rotate:
            cur["whatsapp"]["webhook_secret"] = secrets.token_urlsafe(24)
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
    resp = _public_integrations(refreshed)
    if PUBLIC_BASE_URL and refreshed["whatsapp"].get("webhook_secret"):
        resp["whatsapp"]["webhook_url"] = (
            f"{PUBLIC_BASE_URL}/api/webhooks/bizchat/status?secret={refreshed['whatsapp']['webhook_secret']}"
        )
    return resp


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


@api.get("/settings/whatsapp/templates")
async def list_whatsapp_templates(_: dict = Depends(require_role("admin", "manager"))):
    """Proxy to BizChatAPI template-list. Returns the raw response."""
    cur = await _get_integrations()
    wa = cur["whatsapp"]
    body = await _fetch_whatsapp_templates(wa)
    return body


# ---------- Webhook receiver for BizChat status events ----------
def _extract_status_events(payload: Any) -> List[Dict[str, Any]]:
    """Return a list of {wamid, status, timestamp?} dicts from a webhook payload.
    Handles multiple shapes: {wamid, status}, {data: {wamid, status}}, Meta
    native {entry: [{changes: [{value: {statuses: [{id, status, timestamp}]}}]}]},
    {statuses: [...]}, {event, payload: {...}} etc."""
    out: List[Dict[str, Any]] = []

    def add(wamid, status, timestamp=None):
        if wamid and status:
            out.append({
                "wamid": str(wamid),
                "status": str(status).lower(),
                "timestamp": timestamp,
            })

    if not payload:
        return out
    if isinstance(payload, list):
        for p in payload:
            out.extend(_extract_status_events(p))
        return out
    if not isinstance(payload, dict):
        return out

    if payload.get("wamid") and payload.get("status"):
        add(payload.get("wamid"), payload.get("status"), payload.get("updated_at") or payload.get("timestamp"))

    data = payload.get("data")
    if isinstance(data, dict) and data.get("wamid") and data.get("status"):
        add(data.get("wamid"), data.get("status"), data.get("updated_at") or data.get("timestamp"))
    if isinstance(data, list):
        for d in data:
            if isinstance(d, dict) and d.get("wamid") and d.get("status"):
                add(d.get("wamid"), d.get("status"), d.get("updated_at") or d.get("timestamp"))

    for s in (payload.get("statuses") or []):
        if isinstance(s, dict):
            add(s.get("id") or s.get("wamid"), s.get("status"), s.get("timestamp"))

    for entry in (payload.get("entry") or []):
        for ch in (entry.get("changes") or []):
            val = ch.get("value") or {}
            for s in (val.get("statuses") or []):
                add(s.get("id") or s.get("wamid"), s.get("status"), s.get("timestamp"))

    # Shape F: BizChat native — {message: {whatsapp_message_id, status}, ...}
    msg = payload.get("message")
    if isinstance(msg, dict):
        wid = msg.get("whatsapp_message_id") or msg.get("wamid") or msg.get("id")
        st = msg.get("status")
        if wid and st:
            add(wid, st, msg.get("updated_at") or msg.get("timestamp"))

    # Shape G: BizChat may nest the Meta envelope under `whatsapp_webhook_payload`
    inner_meta = payload.get("whatsapp_webhook_payload")
    if isinstance(inner_meta, dict):
        out.extend(_extract_status_events(inner_meta))

    inner = payload.get("payload")
    if isinstance(inner, dict):
        out.extend(_extract_status_events(inner))

    return out


@api.api_route("/webhooks/bizchat/status", methods=["GET", "POST"])
async def bizchat_status_webhook(request: Request, secret: Optional[str] = None):
    """Public webhook endpoint for BizChat push events. Requires matching
    `?secret=...` query param. Finds the quotation containing the wamid and
    updates the corresponding dispatch_log entry's status. GET returns a
    health-check JSON so BizChat's 'verify webhook' feature can succeed."""
    cur = await _get_integrations()
    expected = cur["whatsapp"].get("webhook_secret")
    if not expected or not secret or secret != expected:
        raise HTTPException(status_code=403, detail="invalid secret")

    if request.method == "GET":
        return {"ok": True, "service": "hre-crm", "webhook": "bizchat-status"}

    try:
        raw: Any = await request.json()
    except Exception:
        body_bytes = await request.body()
        raw = {"raw": body_bytes.decode("utf-8", errors="replace")}

    events = _extract_status_events(raw)

    await db.webhook_events.insert_one({
        "id": str(uuid.uuid4()),
        "source": "bizchat",
        "kind": "message.status",
        "received_at": now_iso(),
        "parsed_events": len(events),
        "payload": raw,
    })

    STATUS_RANK = {"accepted": 1, "sent": 2, "delivered": 3, "read": 4, "failed": 5, "pending": 0}
    updated = 0
    for ev in events:
        wamid = ev["wamid"]
        new_status = ev["status"]
        ts = ev.get("timestamp") or now_iso()
        # Only upgrade status, never downgrade (late "sent" mustn't overwrite "read")
        quote = await db.quotations.find_one({"dispatch_log.wamid": wamid}, {"_id": 0, "dispatch_log": 1})
        if not quote:
            continue
        cur_entry = next((e for e in quote.get("dispatch_log", []) if e.get("wamid") == wamid), None)
        if not cur_entry:
            continue
        if STATUS_RANK.get(new_status, 0) <= STATUS_RANK.get(cur_entry.get("status", "pending"), 0):
            continue
        res = await db.quotations.update_one(
            {"dispatch_log.wamid": wamid},
            {"$set": {
                "dispatch_log.$.status": new_status,
                "dispatch_log.$.status_updated_at": ts,
            }},
        )
        if res.modified_count:
            updated += 1
            logger.info(f"[WA WEBHOOK] wamid={wamid[:30]}… status={new_status}")
    return {"ok": True, "events": len(events), "updated": updated}


@api.get("/settings/whatsapp/webhook-events")
async def recent_webhook_events(_: dict = Depends(require_role("admin", "manager"))):
    """Last 20 webhook events — for debugging registration + shape verification."""
    cur = db.webhook_events.find({}, {"_id": 0}).sort("received_at", -1).limit(20)
    return await cur.to_list(length=20)


# 1×1 transparent GIF served by the email-open endpoint
_OPEN_PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


@api.api_route("/webhooks/email/open", methods=["GET", "HEAD"])
async def email_open_tracking(t: Optional[str] = None):
    """Invisible tracking pixel. When a recipient's email client loads this
    image, mark the corresponding dispatch_log entry as `read`."""
    from fastapi.responses import Response
    if t:
        try:
            quote = await db.quotations.find_one(
                {"dispatch_log.open_token": t},
                {"_id": 0, "id": 1, "dispatch_log": 1},
            )
            if quote:
                # Only upgrade sent → read, not downgrade/overwrite failed/read
                entry = next((e for e in quote.get("dispatch_log", []) if e.get("open_token") == t), None)
                if entry and entry.get("status") == "sent":
                    await db.quotations.update_one(
                        {"id": quote["id"], "dispatch_log.open_token": t},
                        {"$set": {
                            "dispatch_log.$.status": "read",
                            "dispatch_log.$.status_updated_at": now_iso(),
                        }},
                    )
                    logger.info(f"[EMAIL OPEN] quote={quote['id']} token={t[:10]}… → read")
        except Exception:
            logger.exception("[EMAIL OPEN] failed to process")
    # Always return the pixel + no-cache headers so every open is logged
    return Response(
        content=_OPEN_PIXEL_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )




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


async def _send_otp_whatsapp(wa: dict, phone: str, code: str) -> tuple[bool, Optional[str]]:
    if not (phone and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token") and wa.get("otp_template_name")):
        return False, None
    try:
        await _send_whatsapp_template(
            wa, phone,
            template_name=wa["otp_template_name"],
            template_language=wa.get("otp_template_language") or "en",
            field_1=code,
            button_0=code,
        )
        return True, None
    except HTTPException as e:
        logger.error(f"[OTP-WA] send failed: {e.detail}")
        return False, str(e.detail)
    except Exception as e:
        logger.exception("[OTP-WA] unexpected error")
        return False, str(e)


async def _send_otp_email(sm: dict, to_email: str, code: str) -> tuple[bool, Optional[str]]:
    if not (to_email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email")):
        return False, None
    subject = f"Your HRE Exporter verification code is {code}"
    body_text = (
        f"Your HRE Exporter verification code is: {code}\n\n"
        f"This code expires in {OTP_TTL_SECONDS // 60} minutes.\n\n"
        "If you didn't request this, please ignore this email.\n\n"
        "— HRExporter\nAn ISO 9001:2015 Certified Company"
    )
    body_html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f5f5;padding:32px 16px;margin:0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e4e4e7;">
<tr><td style="background:#1A1A1A;color:#FBAE17;padding:18px 24px;font-weight:800;letter-spacing:2px;font-size:11px;text-transform:uppercase;">HRE Exporter — Verification</td></tr>
<tr><td style="padding:32px 24px;">
<p style="margin:0 0 16px;color:#1A1A1A;font-size:14px;">Hello,</p>
<p style="margin:0 0 24px;color:#3f3f46;font-size:14px;line-height:1.55;">Use the code below to verify your phone number and continue with your quote on HRExporter.</p>
<div style="background:#FBAE17;color:#1A1A1A;font-weight:900;font-size:34px;letter-spacing:10px;text-align:center;padding:18px 12px;font-family:'Courier New',monospace;border:2px solid #1A1A1A;">{code}</div>
<p style="margin:24px 0 0;color:#71717a;font-size:12px;">This code expires in <b>{OTP_TTL_SECONDS // 60} minutes</b>. If you didn't request this, you can safely ignore this email.</p>
</td></tr>
<tr><td style="background:#fafafa;color:#a1a1aa;padding:14px 24px;font-size:11px;text-align:center;border-top:1px solid #e4e4e7;">An ISO 9001:2015 Certified Company &middot; info@hrexporter.com</td></tr>
</table></body></html>"""
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_email_sync, sm, to_email, subject, body_text, None, body_html)
        return True, None
    except Exception as e:
        logger.exception("[OTP-Email] send failed")
        return False, str(e)


def _otp_delivery_label(wa_ok: bool, email_ok: bool) -> str:
    if wa_ok and email_ok: return "whatsapp+email"
    if wa_ok: return "whatsapp"
    if email_ok: return "email"
    return "dev"


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
    email: EmailStr  # required for quote PDF dispatch
    gst_number: Optional[str] = ""
    state: Optional[str] = ""
    billing_address: Optional[str] = ""
    shipping_address: Optional[str] = ""


@api.post("/public/quote-requests/start")
async def public_qr_start(data: QuoteRequestStart):
    if not data.phone or len(_norm_phone(data.phone)) < 10:
        raise HTTPException(status_code=400, detail="Valid 10-digit phone number required")
    if not data.email:
        raise HTTPException(status_code=400, detail="Email is required so we can email you the quote PDF")
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
    wa_ok, wa_err = await _send_otp_whatsapp(cur["whatsapp"], qr.get("phone") or "", code)
    email_ok, email_err = await _send_otp_email(cur["smtp"], qr.get("email") or "", code)

    delivery = _otp_delivery_label(wa_ok, email_ok)
    logger.info(f"[OTP] phone={qr.get('phone')} email={qr.get('email')} code={code} delivery={delivery} (request_id={rid})")
    resp: Dict[str, Any] = {"ok": True, "expires_in": OTP_TTL_SECONDS, "delivery": delivery}
    if wa_err: resp["whatsapp_error"] = wa_err
    if email_err: resp["email_error"] = email_err
    if DEV_OTP_PASSTHROUGH and not (wa_ok or email_ok):
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
    delivery = await _dispatch_finalised_quote(quote)
    logger.info(f"[Self-Service Quote] {qnum} created for {contact.get('phone')} delivery={delivery}")
    return {
        "id": quote["id"],
        "quote_number": qnum,
        "grand_total": quote["grand_total"],
        "delivery": delivery,
    }


def _public_order_summary(order: dict) -> dict:
    """Return a customer-safe order tracking snapshot (no internal user emails, no production notes)."""
    if not order:
        return None
    stage = order.get("stage") or "pending_po"
    # Public-facing milestone list (collapses internal-only stages)
    public_stages = [
        ("po_received", "Order Confirmed"),
        ("proforma_issued", "Proforma Invoice Issued"),
        ("in_production", "In Production"),
        ("packaging", "Packaging"),
        ("dispatched", "Dispatched"),
        ("delivered", "Delivered"),
    ]
    # Build done-flags from timeline kinds (each stage transition writes a `stage_advanced` event w/ to=stage)
    timeline = order.get("timeline") or []
    stage_at = {}
    for ev in timeline:
        to_stage = ev.get("to") or ev.get("stage")
        if to_stage and to_stage not in stage_at:
            stage_at[to_stage] = ev.get("at")
    # Order index of the current stage
    try:
        cur_idx = STAGE_ORDER.index(stage)
    except ValueError:
        cur_idx = 0
    milestones = []
    for key, label in public_stages:
        try:
            key_idx = STAGE_ORDER.index(key)
        except ValueError:
            key_idx = -1
        done = key_idx >= 0 and key_idx <= cur_idx
        milestones.append({
            "key": key,
            "label": label,
            "done": done,
            "at": stage_at.get(key),
        })
    proforma = order.get("proforma") or {}
    docs = order.get("documents") or {}
    return {
        "order_number": order.get("order_number"),
        "stage": stage,
        "stage_label": STAGE_TO_LABEL.get(stage, stage),
        "stage_index": cur_idx,
        "total_stages": len(STAGE_ORDER),
        "milestones": milestones,
        "po_number": order.get("po_number") or "",
        "proforma_number": proforma.get("number") or "",
        "proforma_url": proforma.get("url") or "",
        "lr_number": (order.get("dispatch") or {}).get("lr_number") or "",
        "transporter_name": (order.get("dispatch") or {}).get("transporter_name") or "",
        "dispatched_at": (order.get("dispatch") or {}).get("dispatched_at"),
        "invoice_url": (docs.get("invoice") or {}).get("url") or "",
        "lr_url": (docs.get("lr") or {}).get("url") or "",
        "po_submitted_by_customer": bool((docs.get("po") or {}).get("submitted_by_customer")),
        "po_submitted_at": (docs.get("po") or {}).get("uploaded_at") if (docs.get("po") or {}).get("submitted_by_customer") else None,
        "po_url": (docs.get("po") or {}).get("url") or "",
        "po_instructions": (docs.get("po") or {}).get("customer_instructions") or "",
        "updated_at": order.get("updated_at"),
    }


@api.get("/public/my-quotes")
async def public_my_quotes(token: str):
    sess = await _resolve_public_session(token)
    contacts = await db.contacts.find({"phone_norm": sess["phone_norm"]}, {"_id": 0}).to_list(50)
    cids = [c["id"] for c in contacts]
    if not cids:
        return []
    items = await db.quotations.find({"contact_id": {"$in": cids}}, {"_id": 0}).sort("created_at", -1).to_list(500)
    qids = [q["id"] for q in items]
    orders_by_qid = {}
    if qids:
        async for o in db.orders.find({"quote_id": {"$in": qids}}, {"_id": 0}):
            orders_by_qid[o["quote_id"]] = o
    for q in items:
        o = orders_by_qid.get(q["id"])
        q["order"] = _public_order_summary(o) if o else None
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


# ----- Customer-side PO submission -----
async def _notify_admin_po_received(order: dict, quote: dict, contact: dict, has_file: bool, instructions: str):
    """Fire email + WhatsApp to the admin telling them a PO has been submitted."""
    settings = await _get_integrations()
    sm = settings["smtp"]
    wa = settings["whatsapp"]
    customer = order.get("contact_company") or order.get("contact_name") or "a customer"
    quote_no = quote.get("quote_number") or ""
    order_no = order.get("order_number") or ""
    po_url = (order.get("documents") or {}).get("po", {}).get("url") or ""
    body_text_lines = [
        f"Hello Admin,",
        "",
        f"{customer} has just submitted a Purchase Order against quote {quote_no}.",
        f"Internal order ref: {order_no}",
        f"Customer phone: {contact.get('phone') or contact.get('phone_norm') or ''}",
        f"Customer email: {contact.get('email') or ''}",
        "",
        f"PO file attached: {'Yes — ' + po_url if has_file and po_url else 'No (instructions only)'}",
    ]
    if instructions:
        body_text_lines += ["", "Customer instructions / message:", "-" * 40, instructions, "-" * 40]
    body_text_lines += [
        "",
        f"Please review the PO in the Orders module and click 'Confirm PO' to advance the order.",
        "",
        "— HRExporter system",
    ]
    body_text = "\n".join(body_text_lines)

    # Email
    email_ok = False
    email_err = None
    notify_to = (sm.get("admin_notify_email") or sm.get("from_email") or "").strip()
    if notify_to and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                _send_email_sync,
                sm,
                notify_to,
                f"[HRE] PO received — {customer} ({quote_no})",
                body_text,
                None,
                None,
            )
            email_ok = True
        except Exception as e:
            email_err = str(e)
            logger.exception("[customer-po-notify] email failed")

    # WhatsApp
    wa_ok = False
    wa_err = None
    admin_phone = (wa.get("admin_notify_phone") or "").strip()
    tpl_name = (wa.get("po_received_admin_template") or "").strip()
    if admin_phone and tpl_name and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token"):
        try:
            extra: Dict[str, Any] = {
                "field_2": quote_no,
                "field_3": order_no or "(new)",
                "field_4": datetime.now().strftime("%d-%m-%Y %H:%M"),
            }
            if has_file and po_url:
                extra["header_document"] = po_url
                extra["header_document_name"] = (order.get("documents") or {}).get("po", {}).get("filename") or "po.pdf"
            await _send_whatsapp_template(
                wa, admin_phone,
                template_name=tpl_name,
                template_language=wa.get("quote_template_language") or "en",
                field_1=customer,
                extra=extra,
            )
            wa_ok = True
        except Exception as e:
            wa_err = str(e)
            logger.exception("[customer-po-notify] whatsapp failed")

    return {"email": email_ok, "email_error": email_err, "whatsapp": wa_ok, "whatsapp_error": wa_err}


@api.post("/public/quote/{qid}/submit-po")
async def public_submit_po(
    qid: str,
    token: str = Form(...),
    instructions: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    """Customer-side PO submission. Either a PDF or instructions text (or both) is required.
    Creates a draft order in `pending_po` if none exists; otherwise attaches PO + instructions.
    Never auto-advances the stage — admin must click 'Confirm PO'."""
    sess = await _resolve_public_session(token)
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    contact = await db.contacts.find_one({"id": quote.get("contact_id")}, {"_id": 0})
    if not contact or contact.get("phone_norm") != sess["phone_norm"]:
        raise HTTPException(status_code=403, detail="This quote does not belong to your phone")
    if quote.get("status") not in ("approved", "sent"):
        raise HTTPException(status_code=400, detail="Quote is not yet ready to receive a PO. Please ask our team to send the quote first.")

    instructions = (instructions or "").strip()
    has_file = bool(file and (file.filename or "").strip())
    if not has_file and not instructions:
        raise HTTPException(status_code=400, detail="Please attach a PO PDF or type your instructions before submitting.")

    # File hygiene: PDF/image-only + 25 MB cap
    MAX_BYTES = 25 * 1024 * 1024
    if has_file:
        ct = (file.content_type or "").lower()
        ext = (file.filename.rsplit(".", 1)[-1] if "." in (file.filename or "") else "").lower()
        allowed_ct = {"application/pdf", "image/png", "image/jpeg", "image/jpg", "image/webp"}
        allowed_ext = {"pdf", "png", "jpg", "jpeg", "webp"}
        if ct not in allowed_ct and ext not in allowed_ext:
            raise HTTPException(status_code=400, detail="Only PDF or image files are accepted.")
        # Peek size — UploadFile.spool_max_size is small, so read into memory check
        # We'll let _save_order_doc read the bytes; pre-check via seek if available
        try:
            file.file.seek(0, 2)
            size = file.file.tell()
            file.file.seek(0)
        except Exception:
            size = 0
        if size and size > MAX_BYTES:
            raise HTTPException(status_code=413, detail="File too large. Max 25 MB.")

    # Ensure an order exists (create in pending_po if not)
    order = await db.orders.find_one({"quote_id": qid}, {"_id": 0})
    created_now = False
    if not order:
        order = _mint_order_from_quote(quote, contact.get("email") or "customer@portal", po_number="")
        order["order_number"] = await _next_order_number()
        # Mark how it was created
        order["timeline"] = [
            _timeline_event("created", "Order auto-created from customer PO submission",
                            contact.get("email") or "customer@portal", quote_number=quote.get("quote_number")),
        ]
        await db.orders.insert_one(order)
        created_now = True

    oid = order["id"]
    # Save the PO file (if provided)
    po_doc = None
    if has_file:
        po_doc = await _save_order_doc(
            oid, "po", file,
            user_email=contact.get("email") or "customer@portal",
            extra={"submitted_by_customer": True, "customer_instructions": instructions},
        )

    # Build update
    update_set: Dict[str, Any] = {
        "po_received_at": now_iso(),
        "updated_at": now_iso(),
    }
    if po_doc:
        update_set["documents.po"] = po_doc
    else:
        # Instructions-only PO — store as a synthetic doc record (no file)
        update_set["documents.po"] = {
            "filename": "",
            "original_name": "",
            "url": "",
            "uploaded_at": now_iso(),
            "uploaded_by": contact.get("email") or "customer@portal",
            "submitted_by_customer": True,
            "customer_instructions": instructions,
            "po_number": "",
        }

    ev = _timeline_event(
        "customer_po",
        "Customer submitted PO" + (" (PDF attached)" if has_file else " (instructions only)"),
        contact.get("email") or "customer@portal",
        has_file=has_file,
        instructions=instructions[:500],
    )
    await db.orders.update_one(
        {"id": oid},
        {"$set": update_set, "$push": {"timeline": ev}},
    )

    # Refresh order for notification
    fresh = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _notify_admin_po_received(fresh, quote, contact, has_file, instructions)

    return {
        "ok": True,
        "order_number": fresh.get("order_number"),
        "stage": fresh.get("stage"),
        "stage_label": STAGE_TO_LABEL.get(fresh.get("stage"), fresh.get("stage")),
        "had_existing_order": not created_now,
        "po_attached": has_file,
        "admin_notified": notify,
    }


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
    # Look up the customer's email by phone (so OTP can also go to their email)
    contact = await db.contacts.find_one({"phone_norm": pn}, {"_id": 0, "email": 1})
    contact_email = (contact or {}).get("email") or ""
    await db.quote_requests.insert_one({
        "id": rid,
        "name": "", "company": "", "phone": data.phone, "phone_norm": pn,
        "email": contact_email, "gst_number": "", "state": "",
        "billing_address": "", "shipping_address": "",
        "verified": False, "session_token": None, "session_expires_at": None,
        "otp_hash": _hash_otp(code), "otp_expires_at": expires.isoformat(),
        "otp_attempts": 0, "created_at": now_iso(), "kind": "login",
    })
    cur = await _get_integrations()
    wa_ok, wa_err = await _send_otp_whatsapp(cur["whatsapp"], data.phone, code)
    email_ok, email_err = await _send_otp_email(cur["smtp"], contact_email, code)
    delivery = _otp_delivery_label(wa_ok, email_ok)
    logger.info(f"[OTP-LOGIN] phone={data.phone} email={contact_email} code={code} delivery={delivery} (request_id={rid})")
    resp: Dict[str, Any] = {"request_id": rid, "expires_in": OTP_TTL_SECONDS, "delivery": delivery}
    if contact_email:
        # Surface a masked hint to the UI so the user knows where to look
        local, _, dom = contact_email.partition("@")
        masked = (local[:2] + "•" * max(1, len(local) - 2)) + "@" + dom if dom else contact_email
        resp["email_hint"] = masked
    if wa_err: resp["whatsapp_error"] = wa_err
    if email_err: resp["email_error"] = email_err
    if DEV_OTP_PASSTHROUGH and not (wa_ok or email_ok):
        resp["dev_otp"] = code
    return resp


# ---------- Mount ----------
# (Moved to end of file after all routes are defined)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ Order Tracking (Phase 2C) ============

ORDER_STAGES = [
    ("pending_po", "Awaiting Purchase Order"),
    ("po_received", "PO Received"),
    ("proforma_issued", "Proforma Invoice Issued"),
    ("order_placed", "Order Placed with Factory"),
    ("raw_material_check", "Raw Material Check"),
    ("procuring_raw_material", "Procuring Raw Material"),
    ("in_production", "In Production"),
    ("packaging", "Packaging"),
    ("dispatched", "Dispatched"),
    ("lr_received", "LR Received"),
    ("delivered", "Delivered"),
]
STAGE_TO_LABEL = dict(ORDER_STAGES)
STAGE_ORDER = [s for s, _ in ORDER_STAGES]
# Stages that trigger an auto-WhatsApp (template settings key + default field_2 text)
AUTO_NOTIFY_STAGES = {
    "proforma_issued": "order_pi_template",
    "in_production": "order_production_template",
    "packaging": "order_packaging_template",
    "dispatched": "order_dispatched_template",
    "lr_received": "order_lr_template",
}


async def _next_order_number() -> str:
    year = _now_dt().year
    fy_start = year if _now_dt().month >= 4 else year - 1
    prefix = f"HRE/ORD/{fy_start}-{(fy_start+1) % 100:02d}/"
    last = await db.orders.find({"order_number": {"$regex": f"^{re.escape(prefix)}"}}, {"_id": 0, "order_number": 1}).sort("order_number", -1).to_list(length=1)
    seq = 1
    if last:
        try:
            seq = int(last[0]["order_number"].split("/")[-1]) + 1
        except Exception:
            pass
    return f"{prefix}{seq:04d}"


async def _next_pi_number() -> str:
    year = _now_dt().year
    fy_start = year if _now_dt().month >= 4 else year - 1
    prefix = f"HRE/PI/{fy_start}-{(fy_start+1) % 100:02d}/"
    last = await db.orders.find({"proforma.number": {"$regex": f"^{re.escape(prefix)}"}}, {"_id": 0, "proforma": 1}).sort("proforma.number", -1).to_list(length=1)
    seq = 1
    if last and last[0].get("proforma", {}).get("number"):
        try:
            seq = int(last[0]["proforma"]["number"].split("/")[-1]) + 1
        except Exception:
            pass
    return f"{prefix}{seq:04d}"


def _timeline_event(kind: str, label: str, user_email: str, **extra) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "label": label,
        "at": now_iso(),
        "by": user_email,
        **extra,
    }


async def _order_auto_notify(order: dict, stage: str):
    """Fire a WhatsApp template if the stage has a configured template name."""
    settings = await _get_integrations()
    wa = settings["whatsapp"]
    tpl_key = AUTO_NOTIFY_STAGES.get(stage)
    if not tpl_key:
        return None
    tpl_name = wa.get(tpl_key)
    if not tpl_name:
        return None
    if not (wa.get("enabled") and wa.get("vendor_uid") and wa.get("token")):
        return None
    phone = order.get("contact_phone") or ""
    if not phone:
        return None
    customer = order.get("contact_name") or order.get("contact_company") or "Customer"
    ord_no = order.get("order_number") or ""
    stage_label = STAGE_TO_LABEL.get(stage, stage)
    # Extra attachment URL (e.g., LR copy, invoice etc.)
    latest_doc_url = None
    latest_doc_name = None
    # Pick the newest attachment relevant to this stage
    docs = order.get("documents") or {}
    if stage == "dispatched":
        inv = docs.get("invoice"); eway = docs.get("eway_bill")
        latest_doc_url = (inv or {}).get("url") or (eway or {}).get("url")
        latest_doc_name = (inv or {}).get("filename") or (eway or {}).get("filename")
    elif stage == "lr_received":
        lr = docs.get("lr")
        latest_doc_url = (lr or {}).get("url")
        latest_doc_name = (lr or {}).get("filename")
    elif stage == "proforma_issued":
        pi = (order.get("proforma") or {})
        latest_doc_url = pi.get("url")
        latest_doc_name = pi.get("filename")
    try:
        extra: Dict[str, Any] = {
            "field_2": ord_no,
            "field_3": stage_label,
            "field_4": datetime.now().strftime("%d-%m-%Y %H:%M"),
        }
        if latest_doc_url:
            extra["header_document"] = latest_doc_url
            extra["header_document_name"] = latest_doc_name or "document.pdf"
        body = await _send_whatsapp_template(
            wa, phone,
            template_name=tpl_name,
            template_language=wa.get("quote_template_language") or "en",
            field_1=customer,
            extra=extra,
        )
        data = body.get("data") if isinstance(body, dict) else {}
        return {
            "wamid": data.get("wamid"),
            "template": tpl_name,
            "status": data.get("status") or "sent",
        }
    except Exception as e:
        logger.exception(f"[Order notify] stage={stage} failed")
        return {"error": str(e), "template": tpl_name, "status": "failed"}


def _mint_order_from_quote(quote: dict, user_email: str, po_number: Optional[str] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "order_number": "",  # filled by caller
        "quote_id": quote["id"],
        "quote_number": quote.get("quote_number"),
        "contact_id": quote.get("contact_id"),
        "contact_name": quote.get("contact_name"),
        "contact_company": quote.get("contact_company"),
        "contact_phone": quote.get("contact_phone"),
        "contact_email": quote.get("contact_email"),
        "contact_gst": quote.get("contact_gst"),
        "billing_address": quote.get("billing_address"),
        "shipping_address": quote.get("shipping_address"),
        "place_of_supply": quote.get("place_of_supply"),
        "line_items": quote.get("line_items") or [],
        "taxable_value": quote.get("taxable_value"),
        "total_gst": quote.get("total_gst"),
        "total_discount": quote.get("total_discount"),
        "grand_total": quote.get("grand_total"),
        "currency": quote.get("currency", "INR"),
        "stage": "pending_po",
        "po_number": po_number or "",
        "po_received_at": None,
        "documents": {},  # {po, proforma, invoice, eway_bill, lr}  each: {filename, url, uploaded_at, uploaded_by}
        "proforma": {},   # {number, filename, url, generated_at}
        "raw_material_status": "",  # available | procuring | procured
        "production_updates": [],   # {id, note, at, by}
        "dispatch": {},   # {transporter_name, lr_number, dispatched_at}
        "timeline": [
            _timeline_event("created", "Order created from approved quote", user_email, quote_number=quote.get("quote_number")),
        ],
        "notifications": [],  # list of WA auto-notify results
        "created_by": user_email,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


@api.post("/orders/from-quote/{qid}")
async def create_order_from_quote(
    qid: str,
    data: Optional[Dict[str, Any]] = None,
    user: dict = Depends(require_role("admin", "manager")),
):
    quote = await db.quotations.find_one({"id": qid}, {"_id": 0})
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    if quote.get("status") not in ("approved", "sent"):
        raise HTTPException(status_code=400, detail="Quote must be approved (or at least sent) before converting to order")
    existing = await db.orders.find_one({"quote_id": qid}, {"_id": 0, "id": 1, "order_number": 1})
    if existing:
        raise HTTPException(status_code=409, detail=f"Order {existing['order_number']} already exists for this quote")
    po_number = (data or {}).get("po_number", "") if data else ""
    order = _mint_order_from_quote(quote, user["email"], po_number=po_number)
    order["order_number"] = await _next_order_number()
    await db.orders.insert_one(order.copy())
    return {k: v for k, v in order.items() if k != "_id"}


# Convenience alias used by the Quotation detail page
@api.post("/quotations/{qid}/convert-to-order")
async def quote_convert_to_order(
    qid: str,
    data: Optional[Dict[str, Any]] = None,
    user: dict = Depends(require_role("admin", "manager")),
):
    return await create_order_from_quote(qid, data, user)


@api.get("/orders")
async def list_orders(
    stage: Optional[str] = None,
    q: Optional[str] = None,
    _: dict = Depends(require_role("admin", "manager")),
):
    query: Dict[str, Any] = {}
    if stage:
        query["stage"] = stage
    if q:
        rx = re.compile(re.escape(q), re.IGNORECASE)
        query["$or"] = [
            {"order_number": rx},
            {"contact_name": rx},
            {"contact_company": rx},
            {"quote_number": rx},
            {"po_number": rx},
        ]
    cur = db.orders.find(query, {"_id": 0}).sort("created_at", -1).limit(200)
    return await cur.to_list(length=200)


@api.get("/orders/{oid}")
async def get_order(oid: str, _: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


class OrderAdvanceIn(BaseModel):
    stage: str
    note: Optional[str] = ""


@api.post("/orders/{oid}/advance")
async def advance_order_stage(oid: str, data: OrderAdvanceIn, user: dict = Depends(require_role("admin", "manager"))):
    if data.stage not in STAGE_TO_LABEL:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{data.stage}'")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_stage = data.stage
    label = STAGE_TO_LABEL[new_stage]
    ev = _timeline_event("stage", f"Stage → {label}", user["email"], stage=new_stage, note=data.note or "")
    update_set: Dict[str, Any] = {
        "stage": new_stage,
        "updated_at": now_iso(),
    }
    if new_stage == "dispatched":
        update_set["dispatch.dispatched_at"] = now_iso()
    await db.orders.update_one(
        {"id": oid},
        {"$set": update_set, "$push": {"timeline": ev}},
    )
    # Auto-notify
    order_after = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _order_auto_notify(order_after, new_stage)
    if notify:
        notify["stage"] = new_stage
        notify["at"] = now_iso()
        await db.orders.update_one({"id": oid}, {"$push": {"notifications": notify}})
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


class ProductionUpdateIn(BaseModel):
    note: str


@api.post("/orders/{oid}/production-update")
async def add_production_update(oid: str, data: ProductionUpdateIn, user: dict = Depends(require_role("admin", "manager"))):
    if not data.note.strip():
        raise HTTPException(status_code=400, detail="Note is required")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    entry = {
        "id": str(uuid.uuid4()),
        "note": data.note.strip(),
        "at": now_iso(),
        "by": user["email"],
    }
    ev = _timeline_event("production_update", data.note.strip(), user["email"])
    await db.orders.update_one(
        {"id": oid},
        {
            "$push": {"production_updates": entry, "timeline": ev},
            "$set": {"updated_at": now_iso(), "stage": "in_production" if order.get("stage") in ("order_placed", "raw_material_check", "procuring_raw_material") else order["stage"]},
        },
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


class RawMaterialStatusIn(BaseModel):
    status: str  # available | procuring | procured
    note: Optional[str] = ""


@api.post("/orders/{oid}/raw-material")
async def set_raw_material_status(oid: str, data: RawMaterialStatusIn, user: dict = Depends(require_role("admin", "manager"))):
    if data.status not in ("available", "procuring", "procured"):
        raise HTTPException(status_code=400, detail="status must be available | procuring | procured")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_stage = order["stage"]
    if data.status == "available":
        new_stage = "in_production"
    elif data.status == "procuring":
        new_stage = "procuring_raw_material"
    elif data.status == "procured":
        new_stage = "in_production"
    label = {"available": "Raw material available", "procuring": "Procuring raw material", "procured": "Raw material procured"}[data.status]
    ev = _timeline_event("raw_material", label + (f" — {data.note}" if data.note else ""), user["email"])
    await db.orders.update_one(
        {"id": oid},
        {
            "$set": {"raw_material_status": data.status, "stage": new_stage, "updated_at": now_iso()},
            "$push": {"timeline": ev},
        },
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    # If we transitioned into in_production, auto-notify
    if new_stage == "in_production" and order["stage"] != "in_production":
        notify = await _order_auto_notify(updated, "in_production")
        if notify:
            notify["stage"] = "in_production"
            notify["at"] = now_iso()
            await db.orders.update_one({"id": oid}, {"$push": {"notifications": notify}})
            updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


async def _save_order_doc(oid: str, doc_key: str, file: UploadFile, user_email: str, extra: Optional[dict] = None) -> dict:
    """Persist an uploaded file under /uploads/orders/{oid}/ and record it."""
    out_dir = UPLOAD_DIR / "orders" / oid
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", (file.filename or doc_key).rsplit(".", 1)[0])
    ext = (file.filename.rsplit(".", 1)[-1] if (file.filename and "." in file.filename) else "bin").lower()
    safe_name = f"{doc_key}_{safe_stem}_{ts}.{ext}"
    out = out_dir / safe_name
    content = await file.read()
    out.write_bytes(content)
    public_url = f"{PUBLIC_BASE_URL}/api/uploads/orders/{oid}/{safe_name}" if PUBLIC_BASE_URL else f"/api/uploads/orders/{oid}/{safe_name}"
    doc = {
        "filename": safe_name,
        "original_name": file.filename or "",
        "url": public_url,
        "uploaded_at": now_iso(),
        "uploaded_by": user_email,
        **(extra or {}),
    }
    return doc


@api.post("/orders/{oid}/upload-po")
async def upload_po(
    oid: str,
    file: UploadFile = File(...),
    po_number: str = "",
    user: dict = Depends(require_role("admin", "manager")),
):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await _save_order_doc(oid, "po", file, user["email"], {"po_number": po_number})
    ev = _timeline_event("document", f"PO uploaded{(': '+po_number) if po_number else ''}", user["email"], doc_key="po")
    await db.orders.update_one(
        {"id": oid},
        {
            "$set": {
                "documents.po": doc,
                "po_number": po_number or order.get("po_number", ""),
                "po_received_at": now_iso(),
                "stage": "po_received" if order["stage"] == "pending_po" else order["stage"],
                "updated_at": now_iso(),
            },
            "$push": {"timeline": ev},
        },
    )
    return await db.orders.find_one({"id": oid}, {"_id": 0})


@api.post("/orders/{oid}/proforma/generate")
async def generate_proforma(oid: str, user: dict = Depends(require_role("admin", "manager"))):
    """Auto-generate the Proforma Invoice PDF from the order's line items."""
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    from quote_pdf import render_quote_pdf
    pi_no = order.get("proforma", {}).get("number") or await _next_pi_number()
    out_dir = UPLOAD_DIR / "orders" / oid
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", pi_no)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    out = out_dir / f"proforma_{safe}_{ts}.pdf"
    # Build a quote-shaped dict for the renderer but with PI-specific labels
    doc_src = {
        **order,
        "quote_number": pi_no,
        "created_at": now_iso(),
        "valid_until": (_now_dt() + timedelta(days=15)).date().isoformat(),
        "notes": order.get("notes") or "",
        "terms": "PAYMENT: 50% advance, 50% before dispatch.\nDelivery: 15-20 working days post advance.\nPrices are ex-works unless specified.",
    }
    logo = UPLOAD_DIR.parent.parent / "frontend" / "public" / "hre-logo-light-bg.png"
    logo_url = logo.as_uri() if logo.exists() else None
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: render_quote_pdf(doc_src, out, logo_url, "PROFORMA INVOICE"))
    public_url = f"{PUBLIC_BASE_URL}/api/uploads/orders/{oid}/{out.name}" if PUBLIC_BASE_URL else f"/api/uploads/orders/{oid}/{out.name}"
    proforma = {
        "number": pi_no,
        "filename": out.name,
        "url": public_url,
        "generated_at": now_iso(),
        "generated_by": user["email"],
        "source": "generated",
    }
    ev = _timeline_event("proforma", f"Proforma Invoice {pi_no} generated", user["email"])
    await db.orders.update_one(
        {"id": oid},
        {
            "$set": {
                "proforma": proforma,
                "stage": "proforma_issued" if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("proforma_issued") else order["stage"],
                "updated_at": now_iso(),
            },
            "$push": {"timeline": ev},
        },
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _order_auto_notify(updated, "proforma_issued")
    if notify:
        notify["stage"] = "proforma_issued"
        notify["at"] = now_iso()
        await db.orders.update_one({"id": oid}, {"$push": {"notifications": notify}})
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@api.post("/orders/{oid}/proforma/upload")
async def upload_proforma(
    oid: str,
    file: UploadFile = File(...),
    pi_number: str = "",
    user: dict = Depends(require_role("admin", "manager")),
):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    pi_no = pi_number or (order.get("proforma", {}).get("number")) or await _next_pi_number()
    doc = await _save_order_doc(oid, "proforma", file, user["email"], {"number": pi_no, "source": "uploaded"})
    proforma = {
        "number": pi_no,
        "filename": doc["filename"],
        "url": doc["url"],
        "generated_at": now_iso(),
        "generated_by": user["email"],
        "source": "uploaded",
    }
    ev = _timeline_event("proforma", f"Proforma Invoice {pi_no} uploaded", user["email"])
    await db.orders.update_one(
        {"id": oid},
        {
            "$set": {
                "proforma": proforma,
                "stage": "proforma_issued" if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("proforma_issued") else order["stage"],
                "updated_at": now_iso(),
            },
            "$push": {"timeline": ev},
        },
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _order_auto_notify(updated, "proforma_issued")
    if notify:
        notify["stage"] = "proforma_issued"
        notify["at"] = now_iso()
        await db.orders.update_one({"id": oid}, {"$push": {"notifications": notify}})
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@api.post("/orders/{oid}/upload-dispatch")
async def upload_dispatch_docs(
    oid: str,
    invoice: Optional[UploadFile] = File(None),
    eway_bill: Optional[UploadFile] = File(None),
    invoice_number: str = "",
    eway_bill_number: str = "",
    transporter_name: str = "",
    user: dict = Depends(require_role("admin", "manager")),
):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    set_ops: Dict[str, Any] = {"updated_at": now_iso()}
    events: List[dict] = []
    if invoice is not None:
        doc = await _save_order_doc(oid, "invoice", invoice, user["email"], {"number": invoice_number})
        set_ops["documents.invoice"] = doc
        events.append(_timeline_event("document", f"Invoice{(' '+invoice_number) if invoice_number else ''} uploaded", user["email"], doc_key="invoice"))
    if eway_bill is not None:
        doc = await _save_order_doc(oid, "eway_bill", eway_bill, user["email"], {"number": eway_bill_number})
        set_ops["documents.eway_bill"] = doc
        events.append(_timeline_event("document", f"E-way Bill{(' '+eway_bill_number) if eway_bill_number else ''} uploaded", user["email"], doc_key="eway_bill"))
    if transporter_name:
        set_ops["dispatch.transporter_name"] = transporter_name
    # Transition to dispatched
    set_ops["dispatch.dispatched_at"] = now_iso()
    if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("dispatched"):
        set_ops["stage"] = "dispatched"
        events.append(_timeline_event("stage", "Stage → Dispatched", user["email"], stage="dispatched"))
    update_doc: Dict[str, Any] = {"$set": set_ops}
    if events:
        update_doc["$push"] = {"timeline": {"$each": events}}
    await db.orders.update_one({"id": oid}, update_doc)
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _order_auto_notify(updated, "dispatched")
    if notify:
        notify["stage"] = "dispatched"
        notify["at"] = now_iso()
        await db.orders.update_one({"id": oid}, {"$push": {"notifications": notify}})
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@api.post("/orders/{oid}/upload-lr")
async def upload_lr(
    oid: str,
    file: UploadFile = File(...),
    lr_number: str = "",
    user: dict = Depends(require_role("admin", "manager")),
):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await _save_order_doc(oid, "lr", file, user["email"], {"number": lr_number})
    ev = _timeline_event("document", f"LR Copy{(' '+lr_number) if lr_number else ''} uploaded", user["email"], doc_key="lr")
    await db.orders.update_one(
        {"id": oid},
        {
            "$set": {
                "documents.lr": doc,
                "dispatch.lr_number": lr_number,
                "stage": "lr_received" if STAGE_ORDER.index(order["stage"]) < STAGE_ORDER.index("lr_received") else order["stage"],
                "updated_at": now_iso(),
            },
            "$push": {"timeline": ev},
        },
    )
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _order_auto_notify(updated, "lr_received")
    if notify:
        notify["stage"] = "lr_received"
        notify["at"] = now_iso()
        await db.orders.update_one({"id": oid}, {"$push": {"notifications": notify}})
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@api.delete("/orders/{oid}")
async def delete_order(oid: str, _: dict = Depends(require_role("admin"))):
    res = await db.orders.delete_one({"id": oid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True}



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



# Mount the API router AFTER all routes are registered
app.include_router(api)
