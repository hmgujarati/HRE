"""Quotation helpers — FY label, next-quote-number sequencer, line-item totals.

Stateless. Used by `routers/quotations.py` AND by `_bot_finalize_quote`
(server.py) AND by the public OTP-driven quote flow (server.py).
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List

from core import db


def fy_label(d: datetime) -> str:
    """Indian FY: April → March. e.g. Apr 2026 → '2026-27'."""
    if d.month >= 4:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"


async def next_quote_number() -> str:
    fy = fy_label(datetime.now(timezone.utc))
    counter = await db.counters.find_one_and_update(
        {"_id": f"quote_seq_{fy}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = counter["seq"] if counter else 1
    return f"HRE/QT/{fy}/{seq:04d}"


def compute_quote_totals(line_items: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute per-line gross/discount/taxable/gst/total + roll-up summary.
    Mutates each `line_items` entry in place with computed fields."""
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
