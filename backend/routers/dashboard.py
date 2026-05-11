"""Dashboard router — admin counts + public landing-page stats + hot leads + demo seed."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from core import db, get_current_user, now_iso, require_role
from services.contacts import norm_email, norm_phone
from services.quote_helpers import compute_quote_totals, next_quote_number

router = APIRouter()


@router.get("/dashboard/stats")
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


@router.get("/public/stats")
async def public_stats():
    """Lightweight, unauthenticated counts for the login splash."""
    materials = await db.materials.count_documents({"active": True})
    families = await db.product_families.count_documents({"active": True})
    variants = await db.product_variants.count_documents({"active": True})
    return {"materials": materials, "families": families, "variants": variants}


# ─────────────────── Hot Leads ───────────────────

@router.get("/dashboard/hot-leads")
async def hot_leads(_: dict = Depends(get_current_user)):
    """Quotes the customer has READ (WhatsApp or Email tracking pixel) on a
    non-archived `sent`/`draft` quote that hasn't been approved/rejected yet.

    Returned rows include the latest READ timestamp + the channel so the
    sales team can prioritise outreach.
    """
    # Pull all non-terminal, non-archived quotes with a dispatch_log present
    cursor = db.quotations.find(
        {
            "archived": {"$ne": True},
            "status": {"$in": ["sent", "draft"]},
            "dispatch_log": {"$exists": True, "$ne": []},
        },
        {"_id": 0},
    ).sort("updated_at", -1)
    quotes = await cursor.to_list(500)

    hot: List[Dict[str, Any]] = []
    for q in quotes:
        read_entries = [
            e for e in (q.get("dispatch_log") or []) if e.get("status") == "read"
        ]
        if not read_entries:
            continue
        # Latest read timestamp (status_updated_at preferred, else sent_at)
        def _ts(e: Dict[str, Any]) -> str:
            return e.get("status_updated_at") or e.get("sent_at") or ""

        latest = max(read_entries, key=_ts)
        hot.append({
            "id": q.get("id"),
            "quote_number": q.get("quote_number"),
            "contact_name": q.get("contact_name"),
            "contact_company": q.get("contact_company"),
            "contact_phone": q.get("contact_phone"),
            "grand_total": q.get("grand_total", 0),
            "status": q.get("status"),
            "read_channel": latest.get("channel"),
            "read_at": _ts(latest),
            "sent_at": q.get("sent_at"),
        })

    # Sort by read_at desc (most recent read first)
    hot.sort(key=lambda x: x.get("read_at") or "", reverse=True)
    return {"hot_leads": hot[:25], "total": len(hot)}


# ─────────────────── Seed Demo Data ───────────────────

@router.post("/dashboard/seed-demo-data")
async def seed_demo_data(_: dict = Depends(require_role("admin"))):
    """Admin-only: seed 3 sample contacts + 1 sample quote so a freshly-wiped
    DB has data for demos. Idempotent: if the demo contacts already exist
    (matched by phone_norm), reuses them and only creates a new quote.
    """
    # Need at least one product variant to build a quote line
    variant = await db.product_variants.find_one(
        {"active": True}, {"_id": 0}, sort=[("created_at", -1)],
    )
    if not variant:
        raise HTTPException(
            status_code=400,
            detail="Cannot seed demo data — no active product variants in catalogue.",
        )

    demo_specs = [
        {
            "name": "Demo · Rajesh Kumar",
            "company": "Bharat Cables Pvt Ltd",
            "phone": "+91 9876543210",
            "email": "rajesh.demo@bharatcables.test",
            "state": "Gujarat",
            "source": "manual",
        },
        {
            "name": "Demo · Priya Sharma",
            "company": "ElectroWorks India",
            "phone": "+91 9123456780",
            "email": "priya.demo@electroworks.test",
            "state": "Maharashtra",
            "source": "expo",
        },
        {
            "name": "Demo · Anil Singh",
            "company": "PowerGrid Components",
            "phone": "+91 9988776655",
            "email": "anil.demo@powergrid.test",
            "state": "Delhi",
            "source": "whatsapp_bot",
        },
    ]

    contacts_seeded: List[Dict[str, Any]] = []
    for spec in demo_specs:
        p_norm = norm_phone(spec["phone"])
        e_norm = norm_email(spec["email"])
        existing = await db.contacts.find_one({"phone_norm": p_norm}, {"_id": 0})
        if existing:
            contacts_seeded.append(existing)
            continue
        doc = {
            "id": str(uuid.uuid4()),
            "name": spec["name"],
            "company": spec["company"],
            "phone": spec["phone"],
            "phone_norm": p_norm,
            "email": spec["email"],
            "email_norm": e_norm,
            "state": spec["state"],
            "gst_number": "",
            "billing_address": "",
            "shipping_address": "",
            "tags": ["demo"],
            "source": spec["source"],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        await db.contacts.insert_one(doc.copy())
        doc.pop("_id", None)
        contacts_seeded.append(doc)

    # Build a sample quote for the first demo contact
    primary = contacts_seeded[0]
    line = {
        "product_variant_id": variant.get("id"),
        "product_code": variant.get("product_code", ""),
        "family_name": variant.get("family_name", ""),
        "description": variant.get("description", ""),
        "cable_size": variant.get("cable_size", ""),
        "hole_size": variant.get("hole_size", ""),
        "dimensions": variant.get("dimensions", {}) or {},
        "hsn_code": variant.get("hsn_code", "85369090"),
        "quantity": 100,
        "unit": "NOS",
        "base_price": float(variant.get("final_price") or variant.get("base_price") or 10.0),
        "discount_percentage": 0.0,
        "gst_percentage": 18.0,
    }
    line_items = [line]
    totals = compute_quote_totals(line_items)
    qnum = await next_quote_number()
    quote_doc = {
        "id": str(uuid.uuid4()),
        "quote_number": qnum,
        "version": 1,
        "parent_quote_id": None,
        "status": "sent",
        "contact_id": primary["id"],
        "contact_name": primary.get("name", ""),
        "contact_company": primary.get("company", ""),
        "contact_email": primary.get("email", ""),
        "contact_phone": primary.get("phone", ""),
        "contact_gst": primary.get("gst_number", ""),
        "billing_address": primary.get("billing_address", ""),
        "shipping_address": primary.get("shipping_address", ""),
        "place_of_supply": primary.get("state", ""),
        "currency": "INR",
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat(),
        "notes": "Demo quote — generated via Seed Demo Data.",
        "terms": "50% advance, balance before dispatch.",
        "line_items": line_items,
        **totals,
        "created_by": "system@demo",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "sent_at": now_iso(),
        "approved_at": None,
        "rejected_at": None,
        "archived": False,
        "dispatch_log": [
            {
                "id": str(uuid.uuid4()),
                "channel": "email",
                "template": None,
                "to": primary.get("email", ""),
                "wamid": None,
                "log_uid": None,
                "pdf_file": None,
                "pdf_url": None,
                "sent_at": now_iso(),
                "status": "read",
                "status_updated_at": now_iso(),
            }
        ],
    }
    await db.quotations.insert_one(quote_doc.copy())

    return {
        "ok": True,
        "contacts_created": [c["id"] for c in contacts_seeded],
        "quote_id": quote_doc["id"],
        "quote_number": qnum,
    }
