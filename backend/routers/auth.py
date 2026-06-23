"""Auth router — login (+ brute-force lockout), /me, logout, change-password."""
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import LoginIn, db, hash_password, verify_password, create_token, get_current_user, now_iso

router = APIRouter()


# ─────────────────── Brute-force lockout (in-memory) ───────────────────
# 5 failed attempts within the window → 15-minute cooldown per email.
# In-memory by design: low traffic admin app, single-replica preview env.
# For multi-replica production, swap this for a Redis-backed counter.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes
ATTEMPT_WINDOW_SECONDS = 15 * 60  # window in which failures are counted

# email_lower → list of failed-attempt timestamps (monotonic seconds)
_failed_attempts: Dict[str, List[float]] = defaultdict(list)
# email_lower → unlock-after timestamp (monotonic seconds)
_lockouts: Dict[str, float] = {}


def _email_key(email: str) -> str:
    return (email or "").strip().lower()


def _check_lockout(email: str) -> Tuple[bool, int]:
    """Returns (is_locked, seconds_remaining)."""
    key = _email_key(email)
    if not key:
        return False, 0
    unlock_at = _lockouts.get(key)
    if unlock_at is None:
        return False, 0
    now = time.monotonic()
    if now >= unlock_at:
        _lockouts.pop(key, None)
        _failed_attempts.pop(key, None)
        return False, 0
    return True, int(unlock_at - now)


def _record_failure(email: str) -> None:
    key = _email_key(email)
    if not key:
        return
    now = time.monotonic()
    cutoff = now - ATTEMPT_WINDOW_SECONDS
    bucket = [t for t in _failed_attempts[key] if t >= cutoff]
    bucket.append(now)
    _failed_attempts[key] = bucket
    if len(bucket) >= MAX_FAILED_ATTEMPTS:
        _lockouts[key] = now + LOCKOUT_SECONDS


def _record_success(email: str) -> None:
    key = _email_key(email)
    _failed_attempts.pop(key, None)
    _lockouts.pop(key, None)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/auth/login")
async def login(payload: LoginIn):
    locked, secs = _check_lockout(payload.email)
    if locked:
        mins = max(1, secs // 60)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {mins} minute{'s' if mins != 1 else ''}.",
        )
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not user.get("active", True):
        _record_failure(payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user["password_hash"]):
        _record_failure(payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _record_success(payload.email)
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
    # Clear any lockout state for this user — they've proven identity.
    _record_success(record["email"])
    return {"ok": True}
