from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import re
import uuid
import shutil
import asyncio
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

# Shared infra (db handle, JWT helpers, auth deps, common Pydantic models) lives
# in `core.py` so per-domain routers in `routers/` can import them without
# circular-importing this module.
from core import (
    db, client, JWT_SECRET, JWT_ALGO, JWT_EXP_HOURS,
    now_iso, hash_password, verify_password, create_token,
    get_current_user, require_role, calc_final_price,
    LoginIn, UserOut, MaterialIn, CategoryIn,
    ProductFamilyIn, ProductVariantIn, BulkDiscountIn,
    PUBLIC_BASE_URL, SELLER_INFO_EMAIL, OTP_TTL_SECONDS, OTP_MAX_ATTEMPTS,
    SESSION_TTL_DAYS, DEV_OTP_PASSTHROUGH, SETTINGS_DOC_ID,
)
# Phase C: shared integration + dispatch helpers — alias-imported so legacy
# callers throughout this file keep their underscored names.
from services.integrations import (
    DEFAULT_INTEGRATIONS,
    get_integrations as _get_integrations,
    normalise_phone as _normalise_phone,
    send_whatsapp_template as _send_whatsapp_template,
    send_whatsapp_text as _send_whatsapp_text,
    send_whatsapp_document as _send_whatsapp_document,
    send_smtp_email as _send_smtp_email,
    fetch_whatsapp_templates as _fetch_whatsapp_templates,
    get_whatsapp_message_status as _get_whatsapp_message_status,
    hash_otp as _hash_otp,
    send_otp_whatsapp as _send_otp_whatsapp,
    send_otp_email as _send_otp_email,
    otp_delivery_label as _otp_delivery_label,
)
from services.dispatch import (
    generate_quote_pdf as _generate_quote_pdf,
    dispatch_finalised_quote as _dispatch_finalised_quote,
    _now_dt,
    _now_ist,
    STAGE_ORDER, STAGE_TO_LABEL,
    mint_order_from_quote as _mint_order_from_quote,
    next_order_number as _next_order_number,
    next_pi_number as _next_pi_number,
    timeline_event as _timeline_event,
    order_auto_notify as _order_auto_notify,
    persist_order_notification as _persist_order_notification,
    save_order_doc as _save_order_doc,
)
# Phase C Tier 2 — contacts helpers (alias-imported so legacy callers stay valid)
from services.contacts import (
    norm_phone as _norm_phone,
    norm_email as _norm_email,
    find_contact_match as _find_contact_match,
)
# Phase C Tier 2 — quotation helpers (used by _bot_finalize_quote + public OTP flow)
from services.quote_helpers import (
    fy_label as _fy_label,
    next_quote_number as _next_quote_number,
    compute_quote_totals as _compute_quote_totals,
)


# ---------- Setup ----------
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

mongo_url = os.environ['MONGO_URL']

app = FastAPI(title="HRE Exporter CRM API")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ---------- Helpers + models live in core.py (shared with routers/) ----------


# ---------- Auth Routes ----------
# Auth, materials, categories, dashboard endpoints have been moved to routers/.
# See bottom of file for `api.include_router(...)` calls that mount them.



# ---------- Dashboard ----------
# ---------- Public (unauthenticated) ----------
@api.get("/")
async def root():
    return {"service": "HRE Exporter CRM API", "status": "ok"}


# `/dashboard/stats` and `/public/stats` moved to routers/dashboard.py



# ---------- Public Catalogue + Self-Serve Quote (Wave A) ----------
import secrets
import hashlib
import httpx











# ────────────────────────── WhatsApp Inbound Bot ──────────────────────────
from whatsapp_bot import dispatch as bot_dispatch, parse_inbound as bot_parse_inbound


async def _bot_finalize_quote(*, line_items: List[Dict[str, Any]], customer: Dict[str, Any], source: str) -> dict:
    """Build a finalized *Quotation* from bot-collected line items and dispatch
    it (PDF + WA + Email). NO order or Proforma Invoice is created here —
    those happen later in the admin → approved → customer-uploads-PO →
    admin-generates-PI lifecycle.
    Returns: {quote_id, quote_number, grand_total, contact_email, contact_phone}."""
    if not line_items:
        raise HTTPException(status_code=400, detail="No items to quote")
    phone = customer.get("phone") or ""
    # IMPORTANT: must use the same last-10-digits normalisation as the rest of
    # the system (services/contacts.norm_phone) — else /my-quotes can't match
    # the contact by phone after OTP login. (Bug: 2026-05-11.)
    phone_norm = _norm_phone(phone)
    contact = await db.contacts.find_one({"phone_norm": phone_norm}, {"_id": 0})
    if not contact:
        contact = {
            "id": str(uuid.uuid4()),
            "name": customer.get("name") or "Bot Customer",
            "email": customer.get("email") or "",
            "company": customer.get("company") or "",
            "phone": phone,
            "phone_norm": phone_norm,
            "state": customer.get("state") or "",
            "created_at": now_iso(),
            "source": "whatsapp_bot",
        }
        await db.contacts.insert_one(contact.copy())

    # Hydrate to the proper QuoteIn line schema so _compute_quote_totals + PDF render work.
    enriched_lines: List[Dict[str, Any]] = []
    for li in line_items:
        v = await db.product_variants.find_one({"id": li["variant_id"]}, {"_id": 0})
        if not v:
            continue
        fam = await db.product_families.find_one({"id": v.get("product_family_id")}, {"_id": 0, "family_name": 1}) if v.get("product_family_id") else None
        unit_price = float(li.get("unit_price") or v.get("final_price") or 0)
        qty = float(li.get("qty") or 1)
        enriched_lines.append({
            "product_variant_id": v["id"],
            "product_code": v.get("product_code") or "",
            "family_name": (fam or {}).get("family_name", ""),
            "description": v.get("product_name") or "",
            "cable_size": v.get("cable_size") or "",
            "hole_size": v.get("hole_size") or "",
            "dimensions": v.get("dimensions") or {},
            "hsn_code": v.get("hsn_code") or "85369090",
            "quantity": qty,
            "unit": v.get("unit") or "NOS",
            "base_price": unit_price,
            "discount_percentage": 0.0,
            "gst_percentage": float(v.get("gst_percentage") or 18.0),
        })
    if not enriched_lines:
        raise HTTPException(status_code=400, detail="No valid variants found in bot cart")
    totals = _compute_quote_totals(enriched_lines)
    qno = await _next_quote_number()
    quote = {
        "id": str(uuid.uuid4()),
        "quote_number": qno,
        "version": 1,
        "parent_quote_id": None,
        "status": "sent",
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
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=15)).date().isoformat(),
        "notes": f"Auto-generated via WhatsApp bot on {_now_ist().strftime('%d-%m-%Y %H:%M IST')}",
        "terms": "PAYMENT: 50% advance, 50% before dispatch.\nDelivery: 15-20 working days post advance.\nPrices are ex-works unless specified.",
        "line_items": enriched_lines,
        **totals,
        "created_by": "whatsapp_bot",
        "source": source,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "sent_at": now_iso(),
        "approved_at": None,
        "rejected_at": None,
        "archived": False,
        "dispatch_log": [],
    }
    await db.quotations.insert_one(quote.copy())

    # Ship the QUOTATION PDF via WA + Email (no order, no PI — those happen
    # only after admin approval and customer PO upload).
    try:
        await _dispatch_finalised_quote(quote)
    except Exception:
        logger.exception(f"[bot-finalize] quote dispatch failed for {qno}")

    return {
        "quote_id": quote["id"],
        "quote_number": qno,
        "grand_total": float(quote.get("grand_total") or 0),
        "contact_email": contact.get("email"),
        "contact_phone": contact.get("phone"),
    }


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
    if not (data.company or "").strip():
        raise HTTPException(status_code=400, detail="Company name is required")
    if not (data.state or "").strip():
        raise HTTPException(status_code=400, detail="State is required (used for GST: CGST+SGST within Gujarat, IGST otherwise)")
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


@api.get("/public/me")
async def public_me(token: str):
    """Resolve the public OTP session token to the customer's contact profile.
    Used by the public quote builder to skip the 'enter your details' step for
    already-logged-in customers."""
    sess = await _resolve_public_session(token)
    phone_norm = sess.get("phone_norm") or ""
    contact = await _find_contact_match(sess.get("phone") or "", "") if phone_norm == "" else \
              await db.contacts.find_one({"phone_norm": phone_norm}, {"_id": 0})
    if not contact:
        return {"contact": None}
    return {
        "contact": {
            "id": contact.get("id"),
            "name": contact.get("name") or "",
            "company": contact.get("company") or "",
            "phone": contact.get("phone") or "",
            "email": contact.get("email") or "",
            "gst_number": contact.get("gst_number") or "",
            "state": contact.get("state") or "",
            "billing_address": contact.get("billing_address") or "",
            "shipping_address": contact.get("shipping_address") or "",
        }
    }


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


@api.post("/public/me/quote/create")
async def public_me_create_quote(payload: FinalisePayload, token: str):
    """Logged-in customer (with a valid public session) creates a quote directly
    using their existing contact profile — bypasses the OTP/details form.
    Mirrors `/public/quote-requests/{rid}/finalise` but reads name/state/etc.
    from the contact record."""
    sess = await _resolve_public_session(token)
    phone_norm = sess.get("phone_norm") or ""
    contact = await db.contacts.find_one({"phone_norm": phone_norm}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="No customer profile found for this session — please request an OTP again.")
    # Enforce the same mandatory-field rule we apply at signup.
    if not (contact.get("company") or "").strip():
        raise HTTPException(status_code=400, detail="Your profile is missing a company name — please contact support.")
    if not (contact.get("state") or "").strip():
        raise HTTPException(status_code=400, detail="Your profile is missing state — please contact support.")
    if not payload.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

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
        "archived": False,
    }
    await db.quotations.insert_one(quote.copy())
    delivery = await _dispatch_finalised_quote(quote)
    logger.info(f"[Self-Service Quote (logged-in)] {qnum} created for {contact.get('phone')} delivery={delivery}")
    return {
        "id": quote["id"],
        "quote_number": qnum,
        "grand_total": quote["grand_total"],
        "delivery": delivery,
    }


def _public_order_summary(order: dict) -> dict:
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
        "expected_completion_date": order.get("expected_completion_date") or "",
        "updated_at": order.get("updated_at"),
    }


@api.get("/public/my-quotes")
async def public_my_quotes(token: str):
    sess = await _resolve_public_session(token)
    pn = sess["phone_norm"]
    # Tolerant lookup: accept any contact whose phone_norm matches the canonical
    # last-10 digits OR the legacy country-code-prefixed form (covers data created
    # before the 2026-05-11 phone-norm fix migration).
    contacts = await db.contacts.find({"phone_norm": {"$in": [pn, f"91{pn}"]}}, {"_id": 0}).to_list(50)
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
    pn = sess["phone_norm"]
    if not contact or contact.get("phone_norm") not in (pn, f"91{pn}"):
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
                _send_smtp_email,
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
                "field_4": _now_ist().strftime("%d-%m-%Y %H:%M IST"),
            }
            if has_file and po_url:
                extra["header_document"] = po_url
                extra["header_document_name"] = (order.get("documents") or {}).get("po", {}).get("filename") or "po.pdf"
            await _send_whatsapp_template(
                wa, admin_phone,
                template_name=tpl_name,
                template_language=wa.get("po_received_admin_template_language") or wa.get("quote_template_language") or "en",
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
    if quote.get("status") != "approved":
        raise HTTPException(status_code=400, detail="This quote is not yet approved. Please wait for our team to approve it before uploading a PO.")

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
    # Start the email retry worker
    asyncio.create_task(_email_retry_worker())


# ─────────────────── Email retry queue ───────────────────

EMAIL_RETRY_BACKOFF_SECONDS = [30, 120, 600]  # 30s → 2m → 10m
EMAIL_RETRY_MAX_ATTEMPTS = len(EMAIL_RETRY_BACKOFF_SECONDS)


async def _enqueue_email_retry(*, order_id: str, notification_id: str, payload: dict, last_error: str):
    """Insert a row into email_retry_queue for the worker to pick up later."""
    next_retry_at = (_now_dt() + timedelta(seconds=EMAIL_RETRY_BACKOFF_SECONDS[0])).isoformat()
    await db.email_retry_queue.insert_one({
        "id": str(uuid.uuid4()),
        "order_id": order_id,
        "notification_id": notification_id,
        "attempt": 1,  # current attempt count (after first inline failure)
        "next_retry_at": next_retry_at,
        "payload": payload,  # {to_email, subject, body_text, body_html, attach_paths}
        "last_error": last_error,
        "status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })
    logger.info(f"[retry-queue] enqueued order={order_id} notif={notification_id[:8]} next={next_retry_at}")


async def _email_retry_worker():
    """Background loop that picks due rows from email_retry_queue and re-attempts SMTP send.
    On success, updates the original notification.email_status. On final failure (>= 3 attempts),
    marks queue row as 'failed' and leaves the notification's email_error in place."""
    logger.info("[retry-worker] starting")
    while True:
        try:
            await _process_retry_batch()
        except Exception:
            logger.exception("[retry-worker] tick failed")
        await asyncio.sleep(30)


async def _process_retry_batch():
    now_str = now_iso()
    cur = db.email_retry_queue.find(
        {"status": "pending", "next_retry_at": {"$lte": now_str}},
        {"_id": 0},
    ).sort("next_retry_at", 1).limit(20)
    rows = await cur.to_list(length=20)
    if not rows:
        return
    settings = await _get_integrations()
    sm = settings["smtp"]
    smtp_ok = sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email")
    for row in rows:
        rid = row["id"]
        if not smtp_ok:
            # SMTP not configured — push next retry far out so we don't spin
            await db.email_retry_queue.update_one(
                {"id": rid},
                {"$set": {"next_retry_at": (_now_dt() + timedelta(minutes=10)).isoformat(), "last_error": "SMTP not configured", "updated_at": now_iso()}},
            )
            continue
        payload = row.get("payload") or {}
        attempt = int(row.get("attempt", 1))
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                _send_smtp_email,
                sm,
                payload.get("to_email"),
                payload.get("subject"),
                payload.get("body_text"),
                payload.get("attach_paths"),
                payload.get("body_html"),
            )
            # Success — flip the queue row + original notification
            await db.email_retry_queue.update_one(
                {"id": rid},
                {"$set": {"status": "sent", "sent_at": now_iso(), "updated_at": now_iso()}},
            )
            await db.orders.update_one(
                {"id": row["order_id"], "notifications.id": row["notification_id"]},
                {"$set": {
                    "notifications.$.email": True,
                    "notifications.$.email_status": "sent",
                    "notifications.$.email_status_updated_at": now_iso(),
                    "notifications.$.email_error": None,
                    "notifications.$.email_retry_attempt": attempt + 1,
                }},
            )
            logger.info(f"[retry-worker] success order={row['order_id']} notif={row['notification_id'][:8]} on attempt {attempt + 1}")
        except Exception as e:
            err = str(e)[:300]
            new_attempt = attempt + 1
            if new_attempt > EMAIL_RETRY_MAX_ATTEMPTS:
                # Final failure
                await db.email_retry_queue.update_one(
                    {"id": rid},
                    {"$set": {"status": "failed", "attempt": new_attempt, "last_error": err, "updated_at": now_iso()}},
                )
                await db.orders.update_one(
                    {"id": row["order_id"], "notifications.id": row["notification_id"]},
                    {"$set": {
                        "notifications.$.email_error": err,
                        "notifications.$.email_retry_attempt": new_attempt,
                        "notifications.$.email_retry_exhausted": True,
                    }},
                )
                logger.warning(f"[retry-worker] EXHAUSTED order={row['order_id']} notif={row['notification_id'][:8]}")
            else:
                # Schedule next attempt
                delay = EMAIL_RETRY_BACKOFF_SECONDS[min(new_attempt - 1, len(EMAIL_RETRY_BACKOFF_SECONDS) - 1)]
                next_at = (_now_dt() + timedelta(seconds=delay)).isoformat()
                await db.email_retry_queue.update_one(
                    {"id": rid},
                    {"$set": {"attempt": new_attempt, "next_retry_at": next_at, "last_error": err, "updated_at": now_iso()}},
                )
                # Surface attempt count + next-retry on the notification too
                await db.orders.update_one(
                    {"id": row["order_id"], "notifications.id": row["notification_id"]},
                    {"$set": {
                        "notifications.$.email_retry_attempt": new_attempt,
                        "notifications.$.email_retry_next_at": next_at,
                        "notifications.$.email_error": err,
                    }},
                )
                logger.info(f"[retry-worker] retry scheduled order={row['order_id']} notif={row['notification_id'][:8]} attempt={new_attempt} next={next_at}")


@app.on_event("shutdown")
async def shutdown():
    client.close()


@api.get("/")
async def root():
    return {"service": "HRE Exporter CRM API", "status": "ok"}



# ---------- Mount per-domain routers (Phase A — auth/materials/categories/dashboard) ----------
from routers import auth as _auth_router  # noqa: E402
from routers import materials as _materials_router  # noqa: E402
from routers import categories as _categories_router  # noqa: E402
from routers import dashboard as _dashboard_router  # noqa: E402
# Phase B — families/variants/pricing
from routers import families as _families_router  # noqa: E402
from routers import variants as _variants_router  # noqa: E402
from routers import pricing as _pricing_router  # noqa: E402
# Phase C (Tier 1) — settings + webhooks
from routers import settings as _settings_router  # noqa: E402
from routers import webhooks as _webhooks_router  # noqa: E402
# Phase C (Tier 2) — contacts, quotations
from routers import contacts as _contacts_router  # noqa: E402
from routers import quotations as _quotations_router  # noqa: E402
# Phase C (Tier 2) — orders
from routers import orders as _orders_router  # noqa: E402

api.include_router(_auth_router.router)
api.include_router(_materials_router.router)
api.include_router(_categories_router.router)
api.include_router(_dashboard_router.router)
api.include_router(_families_router.router)
api.include_router(_variants_router.router)
api.include_router(_pricing_router.router)
api.include_router(_settings_router.router)
api.include_router(_webhooks_router.router)
api.include_router(_contacts_router.router)
api.include_router(_quotations_router.router)
api.include_router(_orders_router.router)


# Mount the API router AFTER all routes are registered
app.include_router(api)
