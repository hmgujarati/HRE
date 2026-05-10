"""Regression tests for the WhatsApp Chatbot (whatsapp_bot.py + _bot_finalize_quote).

Covers the new flow: WELCOME → NAME/EMAIL/COMPANY → PICK_MATERIAL → PICK_FAMILY
                  → ASK_CABLE → ASK_HOLE → PICK_VARIANT → ASK_QTY → AFTER_ITEM
                  → REVIEW_CART → confirm → FINALIZED (with proforma generated).

Locks down the schema field names: family_name, product_code, product_family_id,
final_price, cable_size, hole_size — the bug that caused families to display
as 'Family / Tap to select' on customer phones.
"""
import asyncio
import os
import sys
import importlib
import pytest
from unittest.mock import patch
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

import whatsapp_bot as wb  # noqa: E402
import server as srv  # noqa: E402


PHONE = "+918888777666"
PHONE_NORM = "918888777666"

WA = {
    "api_base_url": "https://x", "vendor_uid": "v", "token": "t",
    "from_phone_number_id": "p", "admin_notify_phone": "+919999999999",
    "default_country_code": "91",
}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


async def _cleanup(db, phone, phone_norm):
    await db.chatbot_sessions.delete_many({"phone_norm": phone_norm})
    await db.contacts.delete_many({"phone_norm": phone_norm})
    await db.quotations.delete_many({"contact_phone": phone})
    await db.orders.delete_many({"contact_phone": phone})


def test_size_parser():
    """Lock down the smart-match size parser."""
    importlib.reload(wb)
    assert wb.parse_size_range("4-6 mm²") == (4.0, 6.0)
    assert wb.parse_size_range("1.5") == (1.5, 1.5)
    assert wb.parse_size_range("") is None
    assert wb.parse_size_range(None) is None
    assert wb.range_distance(5, (4.0, 6.0)) == 0
    assert wb.range_distance(8, (4.0, 6.0)) == 2
    assert wb.range_distance(5, None) == float("inf")
    assert wb.parse_first_number("abc") is None
    assert wb.parse_first_number("100") == 100.0
    assert wb.parse_first_number("approx 4.5 mm") == 4.5


def test_parse_inbound_real_bizchat_list_reply():
    """REGRESSION: Live BizChat envelope nests interactive.list_reply inside
    `whatsapp_webhook_payload.entry[].changes[].value.messages[]`. The top-level
    message.body only carries the row TITLE, not the row id — so parser must
    walk the nested envelope to recover `fam:<uuid>` selections."""
    importlib.reload(wb)
    payload = {
        "contact": {"phone_number": "918200663263"},
        "message": {"is_new_message": True, "body": "CRIMPING TYPE TINNED COP"},
        "whatsapp_webhook_payload": {"entry": [{"changes": [{"value": {"messages": [{
            "from": "918200663263", "id": "wamid.x", "type": "interactive",
            "interactive": {"type": "list_reply",
                "list_reply": {"id": "fam:2c0d529d", "title": "CRIMPING TYPE TINNED COP"}}
        }]}}]}]},
    }
    r = wb.parse_inbound(payload)
    assert r["selection_id"] == "fam:2c0d529d", r
    assert r["phone_norm"] == "918200663263"

    # button_reply variant
    payload2 = {
        "contact": {"phone_number": "918200663263"},
        "message": {"is_new_message": True, "body": "Copper"},
        "whatsapp_webhook_payload": {"entry": [{"changes": [{"value": {"messages": [{
            "from": "918200663263", "id": "wamid.y", "type": "interactive",
            "interactive": {"type": "button_reply",
                "button_reply": {"id": "1", "title": "Copper"}}
        }]}}]}]},
    }
    r2 = wb.parse_inbound(payload2)
    assert r2["selection_id"] == "1" and r2["text"] == "Copper"

    # Backwards-compat: old data-envelope shape still works
    old = {"data": {"from": "918980004416", "message": {"type": "interactive",
            "interactive": {"button_reply": {"id": "1", "title": "Get a Quote"}}}}}
    r3 = wb.parse_inbound(old)
    assert r3["selection_id"] == "1" and r3["text"] == "Get a Quote"


def test_handoff_keyword_short_circuits():
    importlib.reload(wb)
    db = _db()
    captured = []

    async def fake_post(wa_, path, payload):
        captured.append((path, payload))
        return {"data": {"wamid": "fake"}}

    async def go():
        await db.chatbot_sessions.delete_many({"phone_norm": "917777777777"})
        with patch.object(wb, "_bizchat_post", new=fake_post):
            return await wb.dispatch(
                db=db, wa=WA, sm={}, settings_doc={},
                msg={"phone": "+917777777777", "phone_norm": "917777777777",
                     "text": "talk to sales", "selection_id": "", "wamid": "h1"},
                builder_fn=None,
            )

    r = _run(go())
    assert r["state"] == "human_handoff"


def test_full_flow_to_proforma():
    """End-to-end: build cart and confirm → quote + order + proforma created."""
    importlib.reload(wb)
    importlib.reload(srv)
    db = _db()
    captured = []

    async def fake_post(wa_, path, payload):
        captured.append((path, payload))
        return {"data": {"wamid": f"fake-wamid-{len(captured)}"}}

    async def go():
        await _cleanup(db, PHONE, PHONE_NORM)
        builder = srv._bot_finalize_quote

        async def step(text, sel=""):
            with patch.object(wb, "_bizchat_post", new=fake_post):
                return await wb.dispatch(
                    db=db, wa=WA, sm={}, settings_doc={},
                    msg={"phone": PHONE, "phone_norm": PHONE_NORM, "text": text,
                         "selection_id": sel, "wamid": f"in-{text[:6]}"},
                    builder_fn=builder,
                )

        r = await step("hi"); assert r["state"] == "welcome"
        r = await step("Get a Quote", "1"); assert r["state"] == "ask_name"
        r = await step("Test Bot Pytest"); assert r["state"] == "ask_email"
        r = await step("pytest@x.com"); assert r["state"] == "ask_company"
        r = await step("ACME Pytest"); assert r["state"] == "pick_material"

        btn_msg = next((p for path, p in reversed(captured)
                        if path == "send-interactive-message" and p.get("interactive_type") == "button"), None)
        assert btn_msg, "No button message captured"
        assert "Copper" in list(btn_msg["buttons"].values()), btn_msg["buttons"]

        r = await step("Copper", "1"); assert r["state"] == "pick_family"

        list_msg = next((p for path, p in reversed(captured)
                         if path == "send-interactive-message" and p.get("interactive_type") == "list"), None)
        rows = list_msg["list_data"]["sections"]["section_1"]["rows"]
        first_row = list(rows.values())[0]
        assert first_row["title"] != "Family", "Family list still showing 'Family' fallback (regression)"
        fam_id = first_row["id"][4:]

        r = await step(first_row["title"], f"fam:{fam_id}"); assert r["state"] == "ask_cable"
        r = await step("abc"); assert r["actions_taken"] == ["bad_cable"]
        r = await step("4"); assert r["state"] == "ask_hole"
        r = await step("skip"); assert r["state"] == "pick_variant"

        list_msg = next((p for path, p in reversed(captured)
                         if path == "send-interactive-message" and p.get("interactive_type") == "list"), None)
        var_rows = list_msg["list_data"]["sections"]["section_1"]["rows"]
        assert len(var_rows) >= 1
        first_var = list(var_rows.values())[0]
        assert first_var["title"] not in ("Variant", "variant")
        assert "₹" in first_var["description"]
        var_id = first_var["id"][4:]

        r = await step(first_var["title"], f"var:{var_id}"); assert r["state"] == "ask_qty"
        r = await step("xyz"); assert r["actions_taken"] == ["bad_qty"]
        r = await step("100"); assert r["state"] == "after_item"
        r = await step("Review cart", "2"); assert r["state"] == "review_cart"
        r = await step("Confirm & Send", "1"); assert r["state"] == "finalized"

        quote = await db.quotations.find_one({"id": r["quote_id"]}, {"_id": 0})
        assert quote and quote["status"] == "sent"
        assert quote["quote_number"].startswith("HRE/QT/")
        assert len(quote["line_items"]) == 1
        assert quote["grand_total"] > 0

        order = await db.orders.find_one({"id": r["order_id"]}, {"_id": 0})
        assert order and order["stage"] == "proforma_issued"
        assert order["order_number"].startswith("HRE/ORD/")
        assert order["proforma"]["number"].startswith("HRE/PI/")
        assert order["proforma"]["filename"].endswith(".pdf")

        await _cleanup(db, PHONE, PHONE_NORM)

    _run(go())
