"""Auth router — login, /me, logout, change-password."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import LoginIn, db, hash_password, verify_password, create_token, get_current_user, now_iso

router = APIRouter()


class ChangePasswordIn(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


@router.post("/auth/login")
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


@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/auth/logout")
async def logout(_: dict = Depends(get_current_user)):
    return {"ok": True}


@router.post("/auth/change-password")
async def change_password(payload: ChangePasswordIn, user: dict = Depends(get_current_user)):
    """Authenticated user changes their own password. Verifies the current
    password against the stored bcrypt hash before accepting a new one."""
    record = await db.users.find_one({"id": user["id"]})
    if not record:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(payload.current_password, record["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="New password must be different from the current one")
    new_hash = hash_password(payload.new_password)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": new_hash, "updated_at": now_iso()}},
    )
    return {"ok": True}
