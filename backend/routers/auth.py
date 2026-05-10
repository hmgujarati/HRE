"""Auth router — login, /me, logout."""
from fastapi import APIRouter, Depends, HTTPException

from core import LoginIn, db, verify_password, create_token, get_current_user

router = APIRouter()


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
