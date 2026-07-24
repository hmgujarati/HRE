"""Team router — admin-only user CRUD.

Endpoints:
  GET  /api/users                 → list all users (admin only)
  POST /api/users                 → create a new employee/manager
  PATCH /api/users/{uid}          → edit name/mobile/role/permissions/allowed_tabs
  POST /api/users/{uid}/reset-password → set a new password (admin)
  POST /api/users/{uid}/deactivate → soft-disable (active=False)
  POST /api/users/{uid}/activate   → re-enable
"""
from __future__ import annotations
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from core import db, get_current_user, hash_password, now_iso, require_role

router = APIRouter()

# The set of nav tabs the admin can grant to a user. Keep this in sync
# with the Sidebar items on the frontend.
ALLOWED_TAB_KEYS = [
    "dashboard", "quotations", "orders", "contacts",
    "pricing-chart", "product-families", "materials",
    "categories", "products", "price-history",
    "team", "activity", "settings",
]

VALID_ROLES = ("admin", "manager", "employee")


class UserCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    mobile: Optional[str] = ""
    role: str = "employee"
    password: str = Field(..., min_length=8, max_length=128)
    can_delete: bool = False
    can_edit: bool = True
    allowed_tabs: List[str] = Field(default_factory=list)  # empty = all tabs allowed


class UserUpdateIn(BaseModel):
    name: Optional[str] = None
    mobile: Optional[str] = None
    role: Optional[str] = None
    can_delete: Optional[bool] = None
    can_edit: Optional[bool] = None
    allowed_tabs: Optional[List[str]] = None


class PasswordResetIn(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


def _sanitize(u: dict) -> dict:
    """Strip password hash + Mongo _id from a user doc before returning."""
    out = {k: v for k, v in u.items() if k not in ("_id", "password_hash")}
    out.setdefault("can_delete", u.get("role") == "admin")
    out.setdefault("can_edit", u.get("role") in ("admin", "manager"))
    out.setdefault("allowed_tabs", [])
    return out


@router.get("/users")
async def list_users(_: dict = Depends(require_role("admin"))):
    rows = await db.users.find({}, {"_id": 0, "password_hash": 0}).sort("created_at", 1).to_list(500)
    return [_sanitize(u) for u in rows]


@router.post("/users")
async def create_user(data: UserCreateIn, _: dict = Depends(require_role("admin"))):
    if data.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {VALID_ROLES}")
    for t in data.allowed_tabs:
        if t not in ALLOWED_TAB_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown tab key '{t}'")
    email = data.email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="A user with that email already exists")
    user = {
        "id": str(uuid.uuid4()),
        "name": data.name.strip(),
        "email": email,
        "mobile": (data.mobile or "").strip(),
        "role": data.role,
        "active": True,
        "password_hash": hash_password(data.password),
        "can_delete": data.can_delete or data.role == "admin",
        "can_edit": data.can_edit or data.role in ("admin", "manager"),
        "allowed_tabs": data.allowed_tabs,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.users.insert_one(user.copy())
    return _sanitize(user)


@router.patch("/users/{uid}")
async def update_user(uid: str, data: UserUpdateIn, actor: dict = Depends(require_role("admin"))):
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    updates: dict = {}
    if data.name is not None:
        updates["name"] = data.name.strip()
    if data.mobile is not None:
        updates["mobile"] = data.mobile.strip()
    if data.role is not None:
        if data.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"role must be one of {VALID_ROLES}")
        # Protect against removing the last admin.
        if target.get("role") == "admin" and data.role != "admin":
            other_admins = await db.users.count_documents({"role": "admin", "active": True, "id": {"$ne": uid}})
            if other_admins == 0:
                raise HTTPException(status_code=400, detail="Cannot demote the last active admin")
        updates["role"] = data.role
    if data.can_delete is not None:
        updates["can_delete"] = bool(data.can_delete)
    if data.can_edit is not None:
        updates["can_edit"] = bool(data.can_edit)
    if data.allowed_tabs is not None:
        for t in data.allowed_tabs:
            if t not in ALLOWED_TAB_KEYS:
                raise HTTPException(status_code=400, detail=f"Unknown tab key '{t}'")
        updates["allowed_tabs"] = data.allowed_tabs
    if updates:
        updates["updated_at"] = now_iso()
        await db.users.update_one({"id": uid}, {"$set": updates})
    fresh = await db.users.find_one({"id": uid}, {"_id": 0, "password_hash": 0})
    return _sanitize(fresh)


@router.post("/users/{uid}/reset-password")
async def reset_user_password(uid: str, data: PasswordResetIn, _: dict = Depends(require_role("admin"))):
    target = await db.users.find_one({"id": uid}, {"_id": 0, "id": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.update_one(
        {"id": uid},
        {"$set": {"password_hash": hash_password(data.new_password), "updated_at": now_iso()}},
    )
    return {"ok": True}


@router.post("/users/{uid}/deactivate")
async def deactivate_user(uid: str, actor: dict = Depends(require_role("admin"))):
    if actor["id"] == uid:
        raise HTTPException(status_code=400, detail="You cannot deactivate yourself")
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "admin":
        other_admins = await db.users.count_documents({"role": "admin", "active": True, "id": {"$ne": uid}})
        if other_admins == 0:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last active admin")
    await db.users.update_one({"id": uid}, {"$set": {"active": False, "updated_at": now_iso()}})
    return {"ok": True, "active": False}


@router.post("/users/{uid}/activate")
async def activate_user(uid: str, _: dict = Depends(require_role("admin"))):
    target = await db.users.find_one({"id": uid}, {"_id": 0, "id": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.update_one({"id": uid}, {"$set": {"active": True, "updated_at": now_iso()}})
    return {"ok": True, "active": True}


# ─────────────────── Constants exposed to the frontend ───────────────────
@router.get("/users/meta")
async def users_meta(_: dict = Depends(get_current_user)):
    """Reference lists so the frontend can render checkbox groups etc."""
    return {"roles": list(VALID_ROLES), "allowed_tab_keys": ALLOWED_TAB_KEYS}
