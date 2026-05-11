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


def test_dim_helpers_pick_distinguishing_keys_and_format():
    """Verify the dimension-formatting helpers used to disambiguate variants
    that share cable+hole but differ on physical dims (the user-reported bug)."""
    importlib.reload(wb)
    variants = [
        {"product_code": "RI-7018", "final_price": 5.30,
         "dimensions": {"A": "3.5", "C": "5.5", "D": "12", "F": "1", "B": "6", "K": "2", "H": "6", "L1": "14", "J": "20"}},
        {"product_code": "RI-7020", "final_price": 7.44,
         "dimensions": {"A": "3.5", "C": "5.5", "D": "14", "F": "1", "B": "6", "K": "2", "H": "10.5", "L1": "18.5", "J": "25.5"}},
        {"product_code": "RI-7116", "final_price": 10.37,
         "dimensions": {"A": "3.5", "C": "5.5", "D": "16", "F": "1", "B": "6", "K": "3", "H": "13", "L1": "22", "J": "30"}},
    ]
    # A, C, F, B are equal across these three; L1, D, H, J, K differ.
    keys = wb._pick_distinguishing_keys(variants, max_keys=3)
    # All keys must come from the set of dims that ACTUALLY differ across the variants
    differing = {"L1", "D", "H", "J", "K"}
    assert keys, "expected non-empty distinguishing key list"
    assert all(k in differing for k in keys), f"got non-differing keys: {keys}"
    # L1 and D must be present (top-priority distinguishers)
    assert "L1" in keys and "D" in keys, keys
    desc = "Rs{:,.2f} . {}".format(variants[0]["final_price"], wb._format_dim_row(variants[0]["dimensions"], keys))
    assert len(desc) <= 72, desc
    assert "L1=14" in desc and "D=12" in desc
    txt = wb._build_comparison_text(variants, cable_target=6.0, hole_target=8.0)
    assert "RI-7018" in txt and "RI-7020" in txt and "RI-7116" in txt
    assert "L1=14" in txt and "L1=18.5" in txt
    assert "dimension drawing" in txt.lower()


def test_pick_family_sends_product_and_dimension_images():
    """When a customer picks a product family, the bot pushes two images
    (main_product_image + dimension_drawing_image) before asking for cable size."""
    importlib.reload(wb)
    db = _db()
    captured = []

    async def fake_post(_wa, path, payload):
        captured.append((path, payload))
        return {"data": {"wamid": f"fake-{len(captured)}"}}

    async def go():
        wb.PUBLIC_BASE_URL = wb.PUBLIC_BASE_URL or "https://test.local"
        fam_id = "test-fam-images-1234"
        await db.product_families.delete_many({"id": fam_id})
        await db.product_families.insert_one({
            "id": fam_id, "active": True, "family_name": "Test Lug",
            "material_id": "test-mat-images", "category_id": "cat-x",
            "main_product_image": "/api/uploads/test_main.png",
            "dimension_drawing_image": "/api/uploads/test_dim.png",
        })
        phone = "+919000111222"
        phone_norm = "919000111222"
        await db.chatbot_sessions.delete_many({"phone_norm": phone_norm})
        await db.chatbot_sessions.update_one(
            {"phone_norm": phone_norm},
            {"$set": {"phone_norm": phone_norm, "state": wb.ST_PICK_FAMILY,
                      "ctx": {"customer": {"name": "T", "email": "t@t.com",
                                            "company": "T", "phone": phone},
                              "line_items": [], "current_material_id": "test-mat-images",
                              "current_material_name": "Copper"},
                      "last_msg_at": wb._now_dt().isoformat()}},
            upsert=True,
        )

        with patch.object(wb, "_bizchat_post", new=fake_post):
            r = await wb.dispatch(
                db=db, wa=WA, sm={}, settings_doc={},
                msg={"phone": phone, "phone_norm": phone_norm,
                     "text": "Test Lug", "selection_id": f"fam:{fam_id}",
                     "wamid": "in-img-test"},
                builder_fn=None,
            )

        assert r["state"] == "ask_cable", r
        media_calls = [p for path, p in captured
                       if path == "send-media-message" and p.get("media_type") == "image"]
        assert len(media_calls) == 2, captured
        urls = [m["media_url"] for m in media_calls]
        assert any("test_main.png" in u for u in urls), urls
        assert any("test_dim.png" in u for u in urls), urls
        dim_msg = next(m for m in media_calls if "test_dim.png" in m["media_url"])
        assert "Dimension Reference" in dim_msg["caption"]

        await db.product_families.delete_one({"id": fam_id})
        await db.chatbot_sessions.delete_one({"phone_norm": phone_norm})

    _run(go())

def test_remove_from_cart_flow():
    """REVIEW_CART → Remove item → list reply → item removed → cart re-rendered."""
    importlib.reload(wb)
    db = _db()
    captured = []

    async def fake_post(_wa, path, payload):
        captured.append((path, payload))
        return {"data": {"wamid": f"fake-{len(captured)}"}}

    async def go():
        phone = "+919111222333"
        phone_norm = "919111222333"
        await db.chatbot_sessions.delete_many({"phone_norm": phone_norm})
        # Plant a session at REVIEW_CART with 2 line items.
        ctx = {
            "customer": {"name": "T", "email": "t@t.com", "company": "T", "phone": phone},
            "line_items": [
                {"variant_id": "v-aaa", "variant_code": "RI-1", "variant_name": "RI-1",
                 "unit_price": 10.0, "qty": 5},
                {"variant_id": "v-bbb", "variant_code": "RI-2", "variant_name": "RI-2",
                 "unit_price": 20.0, "qty": 3},
            ],
        }
        await db.chatbot_sessions.update_one(
            {"phone_norm": phone_norm},
            {"$set": {"phone_norm": phone_norm, "state": wb.ST_REVIEW_CART,
                      "ctx": ctx, "last_msg_at": wb._now_dt().isoformat()}},
            upsert=True,
        )

        # Step 1: tap "Remove item" (sel="2")
        with patch.object(wb, "_bizchat_post", new=fake_post):
            r = await wb.dispatch(
                db=db, wa=WA, sm={}, settings_doc={},
                msg={"phone": phone, "phone_norm": phone_norm,
                     "text": "Remove item", "selection_id": "2", "wamid": "in-rm-1"},
                builder_fn=None,
            )
        assert r["state"] == wb.ST_REMOVE_ITEM, r
        # The bot should have sent a list of cart items with `rm:<idx>` row ids
        list_msg = next((p for path, p in reversed(captured)
                         if path == "send-interactive-message"
                         and p.get("interactive_type") == "list"), None)
        assert list_msg, "no remove-item list captured"
        rows = list_msg["list_data"]["sections"]["section_1"]["rows"]
        row_ids = [r["id"] for r in rows.values()]
        assert "rm:0" in row_ids and "rm:1" in row_ids

        # Step 2: tap item at index 0 (RI-1) for removal
        captured.clear()
        with patch.object(wb, "_bizchat_post", new=fake_post):
            r = await wb.dispatch(
                db=db, wa=WA, sm={}, settings_doc={},
                msg={"phone": phone, "phone_norm": phone_norm,
                     "text": "1. RI-1", "selection_id": "rm:0", "wamid": "in-rm-2"},
                builder_fn=None,
            )
        assert r["state"] == wb.ST_REVIEW_CART, r
        assert "removed_item" in r["actions_taken"]
        # Session ctx should now have only 1 line item, and it should be RI-2
        sess = await db.chatbot_sessions.find_one({"phone_norm": phone_norm}, {"_id": 0})
        assert len(sess["ctx"]["line_items"]) == 1
        assert sess["ctx"]["line_items"][0]["variant_code"] == "RI-2"

        # Step 3: remove the LAST item → cart becomes empty → AFTER_ITEM with "Add another / Cancel"
        captured.clear()
        with patch.object(wb, "_bizchat_post", new=fake_post):
            # Open remove list again
            await wb.dispatch(
                db=db, wa=WA, sm={}, settings_doc={},
                msg={"phone": phone, "phone_norm": phone_norm,
                     "text": "Remove item", "selection_id": "2", "wamid": "in-rm-3"},
                builder_fn=None,
            )
            r = await wb.dispatch(
                db=db, wa=WA, sm={}, settings_doc={},
                msg={"phone": phone, "phone_norm": phone_norm,
                     "text": "1. RI-2", "selection_id": "rm:0", "wamid": "in-rm-4"},
                builder_fn=None,
            )
        assert r["state"] == wb.ST_AFTER_ITEM, r
        assert "removed_last_item" in r["actions_taken"]
        sess = await db.chatbot_sessions.find_one({"phone_norm": phone_norm}, {"_id": 0})
        assert sess["ctx"]["line_items"] == []

        await db.chatbot_sessions.delete_one({"phone_norm": phone_norm})

    _run(go())

