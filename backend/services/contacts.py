"""Contacts service — phone/email normalisation + smart-match upsert helpers.

Used by `routers/contacts.py` and by public quote-request endpoints in `server.py`.
"""

from __future__ import annotations
from typing import Optional

from core import db


def norm_phone(s: Optional[str]) -> str:
    if not s:
        return ""
    return "".join(ch for ch in s if ch.isdigit())[-10:]


def norm_email(s: Optional[str]) -> str:
    return (s or "").strip().lower()


async def find_contact_match(phone: str, email: str) -> Optional[dict]:
    """Look up an existing contact by email_norm first, then phone_norm.
    Returns the full contact doc (without `_id`) or None."""
    p = norm_phone(phone)
    e = norm_email(email)
    if e:
        c = await db.contacts.find_one({"email_norm": e}, {"_id": 0})
        if c:
            return c
    if p:
        c = await db.contacts.find_one({"phone_norm": p}, {"_id": 0})
        if c:
            return c
    return None
