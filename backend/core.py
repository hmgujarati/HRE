"""Shared infrastructure for HRE CRM backend.

Lives independently of `server.py` to break the circular-import problem when
moving endpoints into per-domain routers under `routers/`.

Holds: MongoDB client + db handle, JWT settings + token helpers, FastAPI auth
dependencies (`get_current_user`, `require_role`), tiny stateless utilities,
and the Pydantic models used by the auth/material/category/dashboard routers.

Anything that imports `server.py` should import shared resources from here
instead, otherwise the import order will deadlock.
"""

from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Any, Dict

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import HTTPException, Depends, Request
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, ConfigDict, Field


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Frequently referenced env / shared constants
PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
SELLER_INFO_EMAIL: str = "info@hrexporter.com"

# OTP / public-quote-portal constants
OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
SESSION_TTL_DAYS = 30
DEV_OTP_PASSTHROUGH: bool = os.environ.get("DEV_OTP_PASSTHROUGH", "true").lower() == "true"
SETTINGS_DOC_ID = "integrations"

# ---------- MongoDB ----------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

# ---------- JWT ----------
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGO = "HS256"
JWT_EXP_HOURS = 24


# ---------- Stateless helpers ----------
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


def calc_final_price(base_price: float, discount: float,
                     manual_override: bool, manual_price: Optional[float]) -> float:
    if manual_override and manual_price is not None:
        return round(float(manual_price), 2)
    return round(float(base_price) - (float(base_price) * float(discount) / 100.0), 2)


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


# ---------- Pydantic models (auth + catalogue scaffolding) ----------
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
    target_id: str
    change_reason: Optional[str] = "Bulk discount update"


__all__ = [
    "ROOT_DIR", "UPLOAD_DIR", "PUBLIC_BASE_URL", "SELLER_INFO_EMAIL",
    "OTP_TTL_SECONDS", "OTP_MAX_ATTEMPTS", "SESSION_TTL_DAYS",
    "DEV_OTP_PASSTHROUGH", "SETTINGS_DOC_ID",
    "client", "db",
    "JWT_SECRET", "JWT_ALGO", "JWT_EXP_HOURS",
    "now_iso", "hash_password", "verify_password", "create_token",
    "calc_final_price", "get_current_user", "require_role",
    "LoginIn", "UserOut", "MaterialIn", "CategoryIn",
    "ProductFamilyIn", "ProductVariantIn", "BulkDiscountIn",
]
