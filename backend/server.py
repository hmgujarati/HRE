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
    """Build a finalized Quotation from bot-collected line items, auto-convert to
    an Order, generate the Proforma Invoice PDF, and dispatch it via WA + Email.
    Returns: {quote_id, quote_number, order_id, order_number, proforma{number, url},
              grand_total, contact_email, contact_phone}."""
    if not line_items:
        raise HTTPException(status_code=400, detail="No items to quote")
    phone = customer.get("phone") or ""
    phone_norm = "".join(ch for ch in phone if ch.isdigit())
    contact = await db.contacts.find_one({"phone_norm": phone_norm}, {"_id": 0})
    if not contact:
        contact = {
            "id": str(uuid.uuid4()),
            "name": customer.get("name") or "Bot Customer",
            "email": customer.get("email") or "",
            "company": customer.get("company") or "",
            "phone": phone,
            "phone_norm": phone_norm,
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
        "notes": f"Auto-generated via WhatsApp bot on {datetime.now().strftime('%d-%m-%Y %H:%M')}",
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
        "dispatch_log": [],
    }
    await db.quotations.insert_one(quote.copy())

    # Mint an Order from the quote (pending_po) and immediately move to proforma_issued.
    order = _mint_order_from_quote(quote, "whatsapp_bot", po_number="")
    order["order_number"] = await _next_order_number()
    order["timeline"] = [
        _timeline_event("created", "Order auto-created from WhatsApp bot quote",
                        "whatsapp_bot", quote_number=qno),
    ]
    await db.orders.insert_one(order.copy())

    # Generate the Proforma Invoice PDF (mirrors /api/orders/{oid}/proforma/generate).
    from quote_pdf import render_quote_pdf
    pi_no = await _next_pi_number()
    out_dir = UPLOAD_DIR / "orders" / order["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", pi_no)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    out = out_dir / f"proforma_{safe}_{ts}.pdf"
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
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: render_quote_pdf(doc_src, out, logo_url, "PROFORMA INVOICE"))
        public_url = f"{PUBLIC_BASE_URL}/api/uploads/orders/{order['id']}/{out.name}" if PUBLIC_BASE_URL else f"/api/uploads/orders/{order['id']}/{out.name}"
        proforma = {
            "number": pi_no,
            "filename": out.name,
            "url": public_url,
            "generated_at": now_iso(),
            "generated_by": "whatsapp_bot",
            "source": "generated",
        }
        ev = _timeline_event("proforma", f"Proforma Invoice {pi_no} generated", "whatsapp_bot")
        await db.orders.update_one(
            {"id": order["id"]},
            {"$set": {"proforma": proforma, "stage": "proforma_issued", "updated_at": now_iso()},
             "$push": {"timeline": ev}},
        )
    except Exception:
        logger.exception(f"[bot-finalize] proforma PDF generation failed for {pi_no}")
        # We continue — at least quote + order exist; admin can regenerate.

    # Send the Proforma to the customer via WhatsApp + Email (auto-notify).
    updated = await db.orders.find_one({"id": order["id"]}, {"_id": 0})
    if updated and updated.get("stage") == "proforma_issued":
        try:
            notify = await _order_auto_notify(updated, "proforma_issued")
            if notify:
                notify["stage"] = "proforma_issued"
                notify["at"] = now_iso()
                await _persist_order_notification(order["id"], notify)
        except Exception:
            logger.exception(f"[bot-finalize] proforma auto-notify failed for {pi_no}")

    fresh = await db.orders.find_one({"id": order["id"]}, {"_id": 0}) or order
    return {
        "quote_id": quote["id"],
        "quote_number": qno,
        "order_id": order["id"],
        "order_number": order["order_number"],
        "proforma": fresh.get("proforma") or {"number": pi_no},
        "grand_total": float(quote.get("grand_total") or 0),
        "contact_email": contact.get("email"),
        "contact_phone": contact.get("phone"),
    }










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
        "expected_completion_date": order.get("expected_completion_date") or "",
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
                "field_4": datetime.now().strftime("%d-%m-%Y %H:%M"),
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
# Label used INSIDE WhatsApp/Email template body variables.
# Meta-approved template bodies often already include connector words like
# "is now in {{3}}" — passing the full label "In Production" would render
# "is now in In Production". So strip redundant connectors here.
STAGE_TEMPLATE_LABEL = {
    "pending_po": "Awaiting PO",
    "po_received": "PO Received",
    "proforma_issued": "Proforma Invoice Issued",
    "order_placed": "Order Placed with Factory",
    "raw_material_check": "Raw Material Check",
    "procuring_raw_material": "Procuring Raw Material",
    "in_production": "Production",  # template body has "in {{3}}"
    "packaging": "Packaging",
    "dispatched": "Dispatched",
    "lr_received": "LR Received",
    "delivered": "Delivered",
}
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


async def _next_invoice_number() -> str:
    year = _now_dt().year
    fy_start = year if _now_dt().month >= 4 else year - 1
    prefix = f"HRE/INV/{fy_start}-{(fy_start+1) % 100:02d}/"
    last = await db.orders.find({"documents.invoice.number": {"$regex": f"^{re.escape(prefix)}"}}, {"_id": 0, "documents": 1}).sort("documents.invoice.number", -1).to_list(length=1)
    seq = 1
    if last:
        try:
            seq = int(last[0]["documents"]["invoice"]["number"].split("/")[-1]) + 1
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
    """Fire WhatsApp + Email customer notifications for the given stage.

    WhatsApp: uses the per-stage template + per-stage language. On `dispatched`,
    also sends a follow-up `send-media-message` carrying the second document
    (so customer receives BOTH tax invoice AND e-way bill).

    Email: branded HTML body listing the stage update, with all relevant
    documents attached as actual files (tax invoice, e-way bill, LR copy, PI).
    """
    settings = await _get_integrations()
    wa = settings["whatsapp"]
    sm = settings["smtp"]
    tpl_key = AUTO_NOTIFY_STAGES.get(stage)
    if not tpl_key:
        return None
    tpl_name = wa.get(tpl_key)
    tpl_lang_key = f"{tpl_key}_language"
    tpl_lang = wa.get(tpl_lang_key) or wa.get("quote_template_language") or "en"
    phone = order.get("contact_phone") or ""
    email = (order.get("contact_email") or "").strip()
    # Live fallback if frozen contact info is empty
    if (not phone or not email) and order.get("contact_id"):
        live = await db.contacts.find_one({"id": order["contact_id"]}, {"_id": 0, "phone": 1, "email": 1})
        if live:
            phone = phone or live.get("phone") or ""
            email = email or (live.get("email") or "").strip()
    customer = order.get("contact_name") or order.get("contact_company") or "Customer"
    ord_no = order.get("order_number") or ""
    stage_label = STAGE_TO_LABEL.get(stage, stage)  # Used in email/audit copy
    stage_template_label = STAGE_TEMPLATE_LABEL.get(stage, stage_label)  # Used in WA/email field_3
    # Build the timestamp string — append expected completion date if admin has set one,
    # so it flows into the EXISTING approved templates' {{4}} variable without re-approval.
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M")
    eta = (order.get("expected_completion_date") or "").strip()
    eta_pretty = ""
    if eta:
        try:
            eta_pretty = datetime.strptime(eta, "%Y-%m-%d").strftime("%d-%b-%Y")
        except Exception:
            eta_pretty = eta
    field_4_value = f"{timestamp}  ·  Expected completion: {eta_pretty}" if eta_pretty else timestamp
    docs = order.get("documents") or {}

    # Build the list of attachments for THIS stage
    attachments: List[dict] = []  # [{url, filename, label, path}]

    def add_doc(meta: Optional[dict], label: str):
        if not meta:
            return
        url = (meta or {}).get("url")
        fn = (meta or {}).get("filename")
        if not (url and fn):
            return
        # Resolve a local path so we can attach to email
        local_path = UPLOAD_DIR / "orders" / order["id"] / fn
        attachments.append({
            "url": url,
            "filename": fn,
            "label": label,
            "path": local_path if local_path.exists() else None,
        })

    if stage == "proforma_issued":
        pi = order.get("proforma") or {}
        if pi.get("url") and pi.get("filename"):
            local_path = UPLOAD_DIR / "orders" / order["id"] / pi["filename"]
            attachments.append({
                "url": pi["url"], "filename": pi["filename"],
                "label": "Proforma Invoice",
                "path": local_path if local_path.exists() else None,
            })
    elif stage == "dispatched":
        add_doc(docs.get("invoice"), "Tax Invoice")
        add_doc(docs.get("eway_bill"), "E-way Bill")
    elif stage == "lr_received":
        add_doc(docs.get("lr"), "LR Copy")
    # in_production / packaging — no attachments

    primary = attachments[0] if attachments else None
    secondary = attachments[1] if len(attachments) > 1 else None

    result: Dict[str, Any] = {
        "template": tpl_name,
        "stage": stage,
        "whatsapp": False,
        "email": False,
    }
    # Pre-mint an email open token so we can both inject it into the HTML AND
    # persist it on the notification record for webhook lookup later.
    open_token = secrets.token_urlsafe(24)

    # ---- WhatsApp ----
    if tpl_name and phone and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token"):
        try:
            extra: Dict[str, Any] = {
                "field_2": ord_no,
                "field_3": stage_template_label,
                "field_4": field_4_value,
            }
            if primary:
                extra["header_document"] = primary["url"]
                extra["header_document_name"] = primary["filename"]
            body = await _send_whatsapp_template(
                wa, phone,
                template_name=tpl_name,
                template_language=tpl_lang,
                field_1=customer,
                extra=extra,
            )
            data = body.get("data") if isinstance(body, dict) else {}
            result["whatsapp"] = True
            result["wamid"] = data.get("wamid")
            result["status"] = data.get("status") or "sent"
            result["whatsapp_status"] = "sent"
            # Follow-up: ship the second document via send-media-message
            if secondary:
                try:
                    await _send_whatsapp_document(
                        wa, phone,
                        media_url=secondary["url"],
                        file_name=secondary["filename"],
                        caption=f"{secondary['label']} — Order {ord_no}",
                    )
                    result["whatsapp_secondary"] = True
                except Exception as e:
                    logger.warning(f"[Order notify] secondary WA doc failed: {e}")
                    result["whatsapp_secondary_error"] = str(e)
        except HTTPException as e:
            logger.error(f"[Order notify] stage={stage} WA failed: {e.detail}")
            result["whatsapp_error"] = str(e.detail)
        except Exception as e:
            logger.exception(f"[Order notify] stage={stage} WA unexpected error")
            result["whatsapp_error"] = str(e)

    # ---- Email ----
    if email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        try:
            subject = f"Order Update — {ord_no} · {stage_label}"
            attach_list_html = ""
            if attachments:
                items = "".join(f"<li>{a['label']} — <span style='color:#71717a'>{a['filename']}</span></li>" for a in attachments)
                attach_list_html = f"<p style='margin:18px 0 4px;color:#1A1A1A;font-weight:bold;font-size:13px;'>Attached:</p><ul style='margin:0;padding-left:18px;color:#3f3f46;font-size:13px;line-height:1.7;'>{items}</ul>"
            eta_block_html = ""
            if eta_pretty:
                eta_block_html = f"<div style='margin-top:10px;background:#1A1A1A;color:#FBAE17;padding:10px 14px;font-family:Arial,sans-serif;font-size:12px;font-weight:bold;letter-spacing:0.5px;'>EXPECTED COMPLETION · {eta_pretty}</div>"
            body_text = (
                f"Hello {customer},\n\n"
                f"Your order {ord_no} has moved to: {stage_label}.\n"
                f"Updated: {timestamp}\n"
                + (f"Expected completion: {eta_pretty}\n" if eta_pretty else "")
                + "\n"
                + ("Documents attached:\n" + "\n".join(f"  - {a['label']}" for a in attachments) + "\n\n" if attachments else "")
                + "Track your order live in our customer portal.\n\nTeam HRExporter\nAn ISO 9001:2015 Certified Company"
            )
            body_html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f5f5;padding:32px 16px;margin:0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e4e4e7;">
<tr><td style="background:#1A1A1A;color:#FBAE17;padding:18px 24px;font-weight:800;letter-spacing:2px;font-size:11px;text-transform:uppercase;">HRE Exporter — Order Update</td></tr>
<tr><td style="padding:28px 24px;">
<p style="margin:0 0 6px;color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">Order {ord_no}</p>
<h2 style="margin:0 0 16px;color:#1A1A1A;font-size:22px;font-weight:900;">{stage_label}</h2>
<p style="margin:0 0 18px;color:#3f3f46;font-size:14px;line-height:1.6;">Hello {customer},<br/>Your order has moved to <b>{stage_label}</b>.</p>
<div style="background:#FBAE17;color:#1A1A1A;padding:10px 14px;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;">UPDATED · {timestamp}</div>
{eta_block_html}
{attach_list_html}
</td></tr>
<tr><td style="background:#fafafa;color:#a1a1aa;padding:14px 24px;font-size:11px;text-align:center;border-top:1px solid #e4e4e7;">An ISO 9001:2015 Certified Company &middot; info@hrexporter.com</td></tr>
</table>
<img src="{PUBLIC_BASE_URL}/api/webhooks/email/open?t={open_token}" width="1" height="1" alt="" style="display:none" />
</body></html>"""
            # Attach actual files (skip ones whose local path doesn't exist)
            attach_paths = [a["path"] for a in attachments if a.get("path")]
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _send_smtp_email, sm, email, subject, body_text, attach_paths, body_html)
            result["email"] = True
            result["email_open_token"] = open_token
            result["email_status"] = "sent"
        except Exception as e:
            err = str(e)[:300]
            logger.exception(f"[Order notify] stage={stage} email failed; queuing retry")
            result["email_error"] = err
            result["email_open_token"] = open_token
            result["email_retry_attempt"] = 1
            result["_retry_payload"] = {
                "to_email": email,
                "subject": subject,
                "body_text": body_text,
                "body_html": body_html,
                "attach_paths": [str(p) for p in attach_paths] if attach_paths else None,
            }

    # If nothing actually went out, return None to keep notifications log clean
    if not (result["whatsapp"] or result["email"] or result.get("whatsapp_error") or result.get("email_error")):
        return None
    return result


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


# Doc requirements per stage — used to gate generic /advance and surface
# missing-doc errors with a friendly message
STAGE_REQUIRED_DOCS: Dict[str, List[tuple]] = {
    "proforma_issued": [("proforma", "Proforma Invoice")],
    "dispatched": [("documents.invoice", "Tax Invoice"), ("documents.eway_bill", "E-way Bill")],
    "lr_received": [("documents.lr", "LR Copy")],
}


def _missing_required_docs(order: dict, stage: str) -> List[str]:
    """Return human-readable labels of missing required docs for the given stage."""
    needed = STAGE_REQUIRED_DOCS.get(stage) or []
    missing: List[str] = []
    for path, label in needed:
        parts = path.split(".")
        node: Any = order
        for p in parts:
            node = (node or {}).get(p) if isinstance(node, dict) else None
        if not (node and isinstance(node, dict) and node.get("filename")):
            missing.append(label)
    return missing


@api.post("/orders/{oid}/advance")
async def advance_order_stage(oid: str, data: OrderAdvanceIn, user: dict = Depends(require_role("admin", "manager"))):
    if data.stage not in STAGE_TO_LABEL:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{data.stage}'")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    # Guard: required docs must be present BEFORE moving the stage
    missing = _missing_required_docs(order, data.stage)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot move to {STAGE_TO_LABEL[data.stage]} — missing required document(s): {', '.join(missing)}. "
                   f"Please upload all required files first.",
        )
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
        await _persist_order_notification(oid, notify)
    updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


class ProductionUpdateIn(BaseModel):
    note: str


class ExpectedCompletionIn(BaseModel):
    date: Optional[str] = None  # ISO date string "YYYY-MM-DD" or null to clear


@api.put("/orders/{oid}/expected-completion")
async def set_expected_completion(oid: str, data: ExpectedCompletionIn, user: dict = Depends(require_role("admin", "manager"))):
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_date = (data.date or "").strip() or None
    # Light validation — accept "YYYY-MM-DD"
    if new_date:
        try:
            datetime.strptime(new_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be in YYYY-MM-DD format")
    note = f"Expected completion {'set to ' + new_date if new_date else 'cleared'}"
    ev = _timeline_event("note", note, user["email"])
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"expected_completion_date": new_date, "updated_at": now_iso()}, "$push": {"timeline": ev}},
    )
    return await db.orders.find_one({"id": oid}, {"_id": 0})


async def _notify_production_update(order: dict, note: str) -> Optional[dict]:
    """Fire WA template (if configured) + branded email for an ad-hoc production update note.
    The template body must accept body vars: {{1}}=customer, {{2}}=order#, {{3}}=note, {{4}}=timestamp."""
    settings = await _get_integrations()
    wa = settings["whatsapp"]
    sm = settings["smtp"]
    phone = order.get("contact_phone") or ""
    email = (order.get("contact_email") or "").strip()
    if (not phone or not email) and order.get("contact_id"):
        live = await db.contacts.find_one({"id": order["contact_id"]}, {"_id": 0, "phone": 1, "email": 1})
        if live:
            phone = phone or live.get("phone") or ""
            email = email or (live.get("email") or "").strip()
    customer = order.get("contact_name") or order.get("contact_company") or "Customer"
    ord_no = order.get("order_number") or ""
    timestamp_raw = datetime.now().strftime("%d-%m-%Y %H:%M")
    eta = (order.get("expected_completion_date") or "").strip()
    eta_pretty = ""
    if eta:
        try:
            eta_pretty = datetime.strptime(eta, "%Y-%m-%d").strftime("%d-%b-%Y")
        except Exception:
            eta_pretty = eta
    timestamp = f"{timestamp_raw}  ·  Expected completion: {eta_pretty}" if eta_pretty else timestamp_raw
    result: Dict[str, Any] = {"kind": "production_update", "note": note, "whatsapp": False, "email": False, "at": now_iso()}
    open_token = secrets.token_urlsafe(24)

    # ---- WhatsApp ----
    # Use the dedicated production-update template if configured; otherwise fall
    # back to the existing approved 'order_production_template' (the same one
    # that fires when stage moves to In Production). Passing the note text as
    # {{3}} reuses the approved body without needing fresh Meta approval.
    tpl_name = (wa.get("order_production_update_template") or wa.get("order_production_template") or "").strip()
    tpl_lang = (
        wa.get("order_production_update_template_language")
        if wa.get("order_production_update_template")
        else wa.get("order_production_template_language")
    ) or "en"
    if tpl_name and phone and wa.get("enabled") and wa.get("vendor_uid") and wa.get("token"):
        try:
            body = await _send_whatsapp_template(
                wa, phone,
                template_name=tpl_name,
                template_language=tpl_lang,
                field_1=customer,
                extra={"field_2": ord_no, "field_3": note, "field_4": timestamp},
            )
            data = body.get("data") if isinstance(body, dict) else {}
            result["whatsapp"] = True
            result["wamid"] = data.get("wamid")
            result["template"] = tpl_name
            result["whatsapp_status"] = "sent"
        except HTTPException as e:
            logger.error(f"[Prod update notify] WA failed: {e.detail}")
            result["whatsapp_error"] = str(e.detail)
        except Exception as e:
            logger.exception("[Prod update notify] WA unexpected error")
            result["whatsapp_error"] = str(e)

    # ---- Email (always send if SMTP enabled & we have an email) ----
    if email and sm.get("enabled") and sm.get("host") and sm.get("username") and sm.get("password") and sm.get("from_email"):
        try:
            subject = f"Production Update — {ord_no}"
            eta_block_html = ""
            if eta_pretty:
                eta_block_html = f"<div style='margin-top:10px;background:#1A1A1A;color:#FBAE17;padding:10px 14px;font-family:Arial,sans-serif;font-size:12px;font-weight:bold;letter-spacing:0.5px;'>EXPECTED COMPLETION · {eta_pretty}</div>"
            body_text = (
                f"Hello {customer},\n\nProduction update on your order {ord_no}:\n\n"
                f"\"{note}\"\n\nUpdated: {timestamp_raw}\n"
                + (f"Expected completion: {eta_pretty}\n" if eta_pretty else "")
                + "\nTrack your order live in our customer portal.\n\n"
                "Team HRExporter\nAn ISO 9001:2015 Certified Company"
            )
            body_html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f5f5;padding:32px 16px;margin:0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e4e4e7;">
<tr><td style="background:#1A1A1A;color:#FBAE17;padding:18px 24px;font-weight:800;letter-spacing:2px;font-size:11px;text-transform:uppercase;">HRE Exporter — Production Update</td></tr>
<tr><td style="padding:28px 24px;">
<p style="margin:0 0 6px;color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">Order {ord_no}</p>
<h2 style="margin:0 0 16px;color:#1A1A1A;font-size:20px;font-weight:900;">Production Update</h2>
<p style="margin:0 0 18px;color:#3f3f46;font-size:14px;line-height:1.6;">Hello {customer}, here's the latest update from our production floor:</p>
<blockquote style="margin:0 0 18px;padding:14px 16px;background:#fafafa;border-left:4px solid #FBAE17;color:#1A1A1A;font-size:15px;line-height:1.55;font-style:italic;">{note}</blockquote>
<div style="background:#FBAE17;color:#1A1A1A;padding:10px 14px;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;">UPDATED · {timestamp_raw}</div>
{eta_block_html}
</td></tr>
<tr><td style="background:#fafafa;color:#a1a1aa;padding:14px 24px;font-size:11px;text-align:center;border-top:1px solid #e4e4e7;">An ISO 9001:2015 Certified Company &middot; info@hrexporter.com</td></tr>
</table>
<img src="{PUBLIC_BASE_URL}/api/webhooks/email/open?t={open_token}" width="1" height="1" alt="" style="display:none" />
</body></html>"""
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _send_smtp_email, sm, email, subject, body_text, None, body_html)
            result["email"] = True
            result["email_open_token"] = open_token
            result["email_status"] = "sent"
        except Exception as e:
            err = str(e)[:300]
            logger.exception("[Prod update notify] email failed; queuing retry")
            result["email_error"] = err
            result["email_open_token"] = open_token
            result["email_retry_attempt"] = 1
            result["_retry_payload"] = {
                "to_email": email,
                "subject": subject,
                "body_text": body_text,
                "body_html": body_html,
                "attach_paths": None,
            }

    if not (result["whatsapp"] or result["email"] or result.get("whatsapp_error") or result.get("email_error")):
        return None
    return result


@api.post("/orders/{oid}/production-update")
async def add_production_update(oid: str, data: ProductionUpdateIn, user: dict = Depends(require_role("admin", "manager"))):
    if not data.note.strip():
        raise HTTPException(status_code=400, detail="Note is required")
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    note = data.note.strip()
    entry = {
        "id": str(uuid.uuid4()),
        "note": note,
        "at": now_iso(),
        "by": user["email"],
    }
    ev = _timeline_event("production_update", note, user["email"])
    await db.orders.update_one(
        {"id": oid},
        {
            "$push": {"production_updates": entry, "timeline": ev},
            "$set": {"updated_at": now_iso(), "stage": "in_production" if order.get("stage") in ("order_placed", "raw_material_check", "procuring_raw_material") else order["stage"]},
        },
    )
    fresh = await db.orders.find_one({"id": oid}, {"_id": 0})
    notify = await _notify_production_update(fresh, note)
    if notify:
        await _persist_order_notification(oid, notify)
        fresh = await db.orders.find_one({"id": oid}, {"_id": 0})
    return fresh


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
            await _persist_order_notification(oid, notify)
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
        await _persist_order_notification(oid, notify)
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


@api.post("/orders/{oid}/invoice/generate")
async def generate_invoice(oid: str, user: dict = Depends(require_role("admin", "manager"))):
    """Auto-generate the Tax Invoice PDF from the order's line items.
    Saves into documents.invoice with a fresh HRE/INV/{FY}/{NNNN} number unless one exists."""
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    from quote_pdf import render_quote_pdf
    existing_inv = (order.get("documents") or {}).get("invoice") or {}
    inv_no = existing_inv.get("number") or await _next_invoice_number()
    out_dir = UPLOAD_DIR / "orders" / oid
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", inv_no)
    ts = _now_dt().strftime("%Y%m%d%H%M%S")
    out = out_dir / f"invoice_{safe}_{ts}.pdf"
    doc_src = {
        **order,
        "quote_number": inv_no,
        "created_at": now_iso(),
        "valid_until": None,
        "notes": order.get("notes") or "",
        "terms": "PAYMENT: As per Proforma Invoice and PO terms.\nPrices are inclusive of taxes as applicable.\nGoods once dispatched will not be taken back.",
    }
    logo = UPLOAD_DIR.parent.parent / "frontend" / "public" / "hre-logo-light-bg.png"
    logo_url = logo.as_uri() if logo.exists() else None
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: render_quote_pdf(doc_src, out, logo_url, "TAX INVOICE"))
    public_url = f"{PUBLIC_BASE_URL}/api/uploads/orders/{oid}/{out.name}" if PUBLIC_BASE_URL else f"/api/uploads/orders/{oid}/{out.name}"
    invoice = {
        "filename": out.name,
        "original_name": out.name,
        "url": public_url,
        "uploaded_at": now_iso(),
        "uploaded_by": user["email"],
        "number": inv_no,
        "source": "generated",
    }
    ev = _timeline_event("document", f"Tax Invoice {inv_no} generated", user["email"], doc_key="invoice")
    await db.orders.update_one(
        {"id": oid},
        {"$set": {"documents.invoice": invoice, "updated_at": now_iso()}, "$push": {"timeline": ev}},
    )
    return await db.orders.find_one({"id": oid}, {"_id": 0})


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
        await _persist_order_notification(oid, notify)
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

    # Determine final state of the two required docs after this upload
    existing_docs = order.get("documents") or {}
    will_have_invoice = (invoice is not None) or bool((existing_docs.get("invoice") or {}).get("filename"))
    will_have_eway = (eway_bill is not None) or bool((existing_docs.get("eway_bill") or {}).get("filename"))
    if not (will_have_invoice and will_have_eway):
        missing = []
        if not will_have_invoice: missing.append("Tax Invoice")
        if not will_have_eway: missing.append("E-way Bill")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot mark Dispatched — missing required document(s): {', '.join(missing)}. "
                   f"Please attach all dispatch documents before proceeding.",
        )

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
        await _persist_order_notification(oid, notify)
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
        await _persist_order_notification(oid, notify)
        updated = await db.orders.find_one({"id": oid}, {"_id": 0})
    return updated


async def _persist_order_notification(order_id: str, notify: dict) -> dict:
    """Stamp a fresh uuid `id` onto the notification, push to orders.notifications,
    and (if email failed inline) enqueue a retry. Returns the updated entry."""
    if not notify:
        return notify
    notify = dict(notify)
    notify.setdefault("id", str(uuid.uuid4()))
    notify.setdefault("at", now_iso())
    retry_payload = notify.pop("_retry_payload", None)
    await db.orders.update_one({"id": order_id}, {"$push": {"notifications": notify}})
    if retry_payload and notify.get("email_error"):
        await _enqueue_email_retry(
            order_id=order_id,
            notification_id=notify["id"],
            payload=retry_payload,
            last_error=notify["email_error"],
        )
    return notify



@api.post("/orders/{oid}/refire-notification")
async def refire_order_notification(oid: str, user: dict = Depends(require_role("admin", "manager"))):
    """Re-fire the most recent customer notification (stage advance OR production
    update) for this order. Useful when WA/email failed earlier or the customer
    asks for a re-send. Does NOT advance the stage."""
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    notifs = order.get("notifications") or []
    if not notifs:
        raise HTTPException(status_code=400, detail="No notifications have been fired yet for this order. Advance the stage or post a production update first.")
    last = notifs[-1]
    kind = last.get("kind") or ("stage" if last.get("stage") else None)
    if kind == "production_update":
        notify = await _notify_production_update(order, last.get("note") or "(repeat)")
    else:
        # Treat as a stage notification — re-derive stage from notification log
        stage = last.get("stage") or order.get("stage")
        notify = await _order_auto_notify(order, stage)
        if notify:
            notify["stage"] = stage
    if not notify:
        raise HTTPException(status_code=400, detail="Nothing to send — channels (WhatsApp + Email) are not configured. Enable them in Settings first.")
    notify["at"] = now_iso()
    notify["refire_of"] = last.get("at")
    await _persist_order_notification(oid, notify)
    return await db.orders.find_one({"id": oid}, {"_id": 0})


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


# Mount the API router AFTER all routes are registered
app.include_router(api)
