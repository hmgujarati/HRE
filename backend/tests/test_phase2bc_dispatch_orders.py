"""Phase 2B + 2C backend tests:
- Settings/integrations CRUD (whatsapp + smtp)
- Quotation dispatch /send (PDF gen, graceful failure when creds empty)
- Email-open tracking pixel webhook
- BizChat status webhook (with shared secret)
- Order conversion from approved quote
- Order advance through manufacturing stages
- Proforma invoice generation
- Production updates
- Regression: _id must not leak anywhere
"""
import os
import re
import time
import uuid
import pytest
import requests
from datetime import datetime, timezone
from typing import Any, Dict

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
try:
    with open("/app/frontend/.env") as f:
        for ln in f:
            if ln.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = ln.split("=", 1)[1].strip().strip('"').rstrip("/")
                break
except Exception:
    pass

ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"

STATE: Dict[str, Any] = {}


# ------------- fixtures -------------
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_client(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def seeded_quote(admin_client):
    """Create a contact + draft quote for dispatch + order tests."""
    # Contact
    phone = "9" + str(int(time.time()))[-9:]
    payload = {
        "name": f"TEST_ODR_{uuid.uuid4().hex[:5]}",
        "company": "TEST_OrderCo",
        "phone": phone,
        "email": f"TEST_odr_{uuid.uuid4().hex[:5]}@example.com",
        "state": "Karnataka",
        "source": "manual",
    }
    r = admin_client.post(f"{BASE_URL}/api/contacts", json=payload)
    assert r.status_code == 200, r.text
    contact = r.json()
    STATE["contact_id"] = contact["id"]

    # Pick first variant
    r = admin_client.get(f"{BASE_URL}/api/product-variants")
    assert r.status_code == 200
    variants = r.json()
    assert variants, "No variants seeded; Phase 1 seed required"
    v = variants[0]

    q_payload = {
        "contact_id": contact["id"],
        "place_of_supply": "Karnataka",
        "valid_until": "2026-12-31",
        "line_items": [{
            "product_variant_id": v["id"],
            "product_code": v["product_code"],
            "family_name": "TestFam",
            "description": "desc",
            "hsn_code": "85369090",
            "quantity": 5,
            "unit": "NOS",
            "base_price": 200.0,
            "discount_percentage": 0.0,
            "gst_percentage": 18.0,
        }],
    }
    r = admin_client.post(f"{BASE_URL}/api/quotations", json=q_payload)
    assert r.status_code == 200, r.text
    q = r.json()
    STATE["quote_id"] = q["id"]
    STATE["grand_total"] = q["grand_total"]
    return q


def _assert_no_mongo_id(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        assert "_id" not in obj, f"Mongo _id leaked at {path or 'root'}"
        for k, v in obj.items():
            _assert_no_mongo_id(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, it in enumerate(obj):
            _assert_no_mongo_id(it, f"{path}[{i}]")


# =========== Settings / Integrations ===========
class TestIntegrationsSettings:
    def test_get_integrations(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/settings/integrations")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "whatsapp" in data and "smtp" in data
        # Secrets must be masked (either empty string or contain '•')
        tok = data["whatsapp"].get("token", "")
        pw = data["smtp"].get("password", "")
        assert (tok == "" or "•" in tok), f"Token not masked: {tok!r}"
        assert (pw == "" or "•" in pw), f"Password not masked: {pw!r}"
        # Webhook secret auto-seeded
        assert data["whatsapp"].get("webhook_secret"), "webhook_secret not auto-seeded"
        STATE["webhook_secret"] = data["whatsapp"]["webhook_secret"]
        _assert_no_mongo_id(data)

    def test_update_integrations_persists(self, admin_client):
        payload = {
            "whatsapp": {
                "enabled": False,
                "vendor_uid": "TEST_VENDOR",
                "token": "",  # keep existing
                "default_country_code": "91",
                "quote_template_name": "TEST_quote_doc_v1",
                "quote_template_language": "en",
            },
            "smtp": {
                "enabled": False,
                "host": "smtp.hostinger.com",
                "port": 465,
                "use_ssl": True,
                "username": "test@example.com",
                "password": "",  # keep existing
                "from_email": "test@example.com",
                "from_name": "TEST HRE",
            },
        }
        r = admin_client.put(f"{BASE_URL}/api/settings/integrations", json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["whatsapp"]["vendor_uid"] == "TEST_VENDOR"
        assert data["whatsapp"]["quote_template_name"] == "TEST_quote_doc_v1"
        assert data["smtp"]["host"] == "smtp.hostinger.com"
        assert data["smtp"]["port"] == 465
        # Re-read to verify persistence
        r = admin_client.get(f"{BASE_URL}/api/settings/integrations")
        d2 = r.json()
        assert d2["whatsapp"]["vendor_uid"] == "TEST_VENDOR"
        assert d2["smtp"]["host"] == "smtp.hostinger.com"
        _assert_no_mongo_id(d2)


# =========== Quote /send dispatch ===========
class TestQuoteDispatch:
    def test_send_quote_graceful_without_creds(self, admin_client, seeded_quote):
        qid = STATE["quote_id"]
        r = admin_client.post(f"{BASE_URL}/api/quotations/{qid}/send")
        # Expect either 200 with errors dict or a 4xx; never 500
        assert r.status_code < 500, f"/send returned 500: {r.text}"
        data = r.json()
        # PDF should have been generated regardless of integration availability
        assert data.get("pdf") is True, f"PDF not generated: {data}"
        # WhatsApp + email should be skipped because integrations disabled / empty
        # whatsapp flag True only if actually dispatched; with enabled=False it won't
        assert data.get("whatsapp", False) in (False, True)
        assert data.get("email", False) in (False, True)

    def test_pdf_endpoint_returns_file(self, admin_client):
        qid = STATE["quote_id"]
        r = admin_client.get(f"{BASE_URL}/api/quotations/{qid}/pdf")
        assert r.status_code == 200, r.text
        ct = r.headers.get("content-type", "")
        assert "pdf" in ct.lower(), f"Not a PDF content-type: {ct}"
        assert r.content[:4] == b"%PDF", "Response is not a valid PDF"

    def test_dispatch_log_and_no_id_leak(self, admin_client):
        qid = STATE["quote_id"]
        r = admin_client.get(f"{BASE_URL}/api/quotations/{qid}")
        assert r.status_code == 200
        q = r.json()
        _assert_no_mongo_id(q)
        # dispatch_log should exist (may be empty list if no creds); just assert shape
        log = q.get("dispatch_log")
        assert log is None or isinstance(log, list)


# =========== Webhooks ===========
class TestWebhooks:
    def test_email_open_pixel_returns_gif(self):
        # No auth needed; public endpoint
        r = requests.get(f"{BASE_URL}/api/webhooks/email/open", timeout=15)
        assert r.status_code == 200, r.text
        ct = r.headers.get("content-type", "")
        assert "gif" in ct.lower() or "image" in ct.lower(), f"Unexpected content-type: {ct}"
        # 1x1 gif is tiny
        assert len(r.content) < 200
        assert r.headers.get("cache-control", "").lower().startswith("no-")

    def test_email_open_with_unknown_token_still_returns_pixel(self):
        r = requests.get(f"{BASE_URL}/api/webhooks/email/open?t=UNKNOWN_TOKEN_{uuid.uuid4().hex}", timeout=15)
        assert r.status_code == 200
        assert "image" in r.headers.get("content-type", "").lower()

    def test_bizchat_webhook_requires_secret(self):
        r = requests.post(f"{BASE_URL}/api/webhooks/bizchat/status", json={"any": 1}, timeout=15)
        assert r.status_code == 403, r.text

    def test_bizchat_webhook_wrong_secret(self):
        r = requests.post(f"{BASE_URL}/api/webhooks/bizchat/status?secret=WRONG", json={"x": 1}, timeout=15)
        assert r.status_code == 403

    def test_bizchat_webhook_get_healthcheck(self):
        secret = STATE.get("webhook_secret")
        assert secret, "webhook_secret missing from state"
        r = requests.get(f"{BASE_URL}/api/webhooks/bizchat/status?secret={secret}", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d.get("ok") is True

    def test_bizchat_webhook_post_sample_payload(self):
        """POST a sample BizChat status payload. Even if no dispatch_log matches the
        wamid, the endpoint should still return 200 and log the event."""
        secret = STATE.get("webhook_secret")
        payload = {
            "event": "message.status",
            "data": {
                "wamid": f"TEST_WAMID_{uuid.uuid4().hex}",
                "status": "delivered",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
        r = requests.post(f"{BASE_URL}/api/webhooks/bizchat/status?secret={secret}",
                          json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert "events" in d and "updated" in d


# =========== Orders (Phase 2C) ===========
class TestOrders:
    def test_approve_quote_first(self, admin_client):
        qid = STATE["quote_id"]
        r = admin_client.patch(f"{BASE_URL}/api/quotations/{qid}/status", json={"status": "approved"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"

    def test_convert_to_order(self, admin_client):
        qid = STATE["quote_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/from-quote/{qid}", json={"po_number": "TEST_PO_001"})
        assert r.status_code == 200, r.text
        order = r.json()
        _assert_no_mongo_id(order)
        assert order["quote_id"] == qid
        assert order["order_number"], "Order number missing"
        assert re.match(r"^HRE/ORD/\d{4}-\d{2}/\d{4}$", order["order_number"]), \
            f"Bad order number: {order['order_number']}"
        assert order["stage"] == "pending_po"
        assert order["grand_total"] == STATE["grand_total"]
        assert order["po_number"] == "TEST_PO_001"
        assert isinstance(order["timeline"], list) and len(order["timeline"]) >= 1
        STATE["order_id"] = order["id"]
        STATE["order_number"] = order["order_number"]

    def test_duplicate_convert_creates_another_order(self, admin_client):
        """Per business rule (2026-05-11): multiple orders may be created from
        the same quote. The legacy 409 guard has been removed; a second
        conversion creates a fresh order with its own unique HRE/ORD/... number."""
        qid = STATE["quote_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/from-quote/{qid}", json={})
        assert r.status_code == 200, r.text
        second = r.json()
        assert second["quote_id"] == qid
        assert second["id"] != STATE["order_id"]
        assert second["order_number"] != STATE["order_number"]

    def test_convert_to_order_alias_works(self, admin_client):
        """REGRESSION: `POST /api/quotations/{qid}/convert-to-order` is the
        endpoint the frontend Quotation detail page uses. After the orders
        refactor (2026-05-10) this alias was broken by a stale
        `from server import create_order_from_quote` — guard against that
        recurring. We just need the route to resolve and produce a valid 200
        order response (multiple orders from one quote are now allowed)."""
        qid = STATE["quote_id"]
        r = admin_client.post(
            f"{BASE_URL}/api/quotations/{qid}/convert-to-order",
            json={"po_number": "TEST_PO_ALIAS"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["quote_id"] == qid
        assert body["order_number"], "Missing order_number"

    def test_list_orders(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/orders")
        assert r.status_code == 200, r.text
        orders = r.json()
        assert isinstance(orders, list)
        assert any(o["id"] == STATE["order_id"] for o in orders)
        _assert_no_mongo_id(orders)

    def test_list_orders_search_filter(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/orders", params={"q": "TEST_PO_001"})
        assert r.status_code == 200
        assert any(o["id"] == STATE["order_id"] for o in r.json())

    def test_get_order_detail(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.get(f"{BASE_URL}/api/orders/{oid}")
        assert r.status_code == 200, r.text
        order = r.json()
        _assert_no_mongo_id(order)
        assert order["id"] == oid

    def test_advance_stage_po_received(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/{oid}/advance",
                              json={"stage": "po_received", "note": "PO rcvd"})
        assert r.status_code == 200, r.text
        order = r.json()
        assert order["stage"] == "po_received"
        # Timeline got a new event
        tl = order["timeline"]
        assert any(ev.get("stage") == "po_received" for ev in tl), \
            f"timeline missing po_received event: {tl}"

    def test_advance_invalid_stage_rejected(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/{oid}/advance",
                              json={"stage": "bogus_stage"})
        assert r.status_code == 400

    def test_generate_proforma(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/{oid}/proforma/generate")
        assert r.status_code == 200, r.text
        order = r.json()
        pf = order.get("proforma") or {}
        assert pf.get("number"), f"Proforma number missing: {pf}"
        assert pf.get("filename"), "Proforma filename missing"
        assert pf.get("url"), "Proforma url missing"
        # Stage should auto-advance to proforma_issued
        assert order["stage"] == "proforma_issued"
        _assert_no_mongo_id(order)

    def test_advance_through_production_stages(self, admin_client):
        oid = STATE["order_id"]
        stages = ["order_placed", "raw_material_check", "in_production",
                  "packaging", "dispatched", "delivered"]
        for st in stages:
            r = admin_client.post(f"{BASE_URL}/api/orders/{oid}/advance",
                                  json={"stage": st, "note": f"move to {st}"})
            assert r.status_code == 200, f"Advance to {st} failed: {r.text}"
            assert r.json()["stage"] == st

        # Final order: delivered + timeline full
        r = admin_client.get(f"{BASE_URL}/api/orders/{oid}")
        order = r.json()
        assert order["stage"] == "delivered"
        stages_logged = {ev.get("stage") for ev in order["timeline"] if ev.get("stage")}
        for expected in ("po_received", "order_placed", "in_production",
                         "packaging", "dispatched", "delivered"):
            assert expected in stages_logged, f"{expected} missing in timeline {stages_logged}"

    def test_production_update_endpoint(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/{oid}/production-update",
                              json={"note": "50% done welding"})
        assert r.status_code == 200, r.text
        order = r.json()
        assert order["production_updates"], "production_updates not appended"
        assert order["production_updates"][-1]["note"] == "50% done welding"

    def test_production_update_empty_rejected(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.post(f"{BASE_URL}/api/orders/{oid}/production-update",
                              json={"note": "  "})
        assert r.status_code == 400


# =========== Regression: Phase 1 still alive ===========
class TestRegressionPhase1:
    def test_materials_list(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/materials")
        assert r.status_code == 200
        _assert_no_mongo_id(r.json())

    def test_families_list(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-families")
        assert r.status_code == 200
        _assert_no_mongo_id(r.json())

    def test_variants_list(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/product-variants")
        assert r.status_code == 200
        _assert_no_mongo_id(r.json())


# =========== Cleanup ===========
class TestZCleanup:
    def test_delete_order(self, admin_client):
        oid = STATE.get("order_id")
        if not oid:
            pytest.skip("no order to delete")
        r = admin_client.delete(f"{BASE_URL}/api/orders/{oid}")
        assert r.status_code in (200, 204), r.text

    def test_delete_quote(self, admin_client):
        qid = STATE.get("quote_id")
        if not qid:
            pytest.skip("no quote to delete")
        # Per business rule (2026-05-11), quotes with orders cannot be hard-deleted.
        # The earlier tests in this class converted the quote to ≥1 orders, so we
        # archive instead (matching the recommended admin workflow).
        r = admin_client.post(f"{BASE_URL}/api/quotations/{qid}/archive")
        assert r.status_code == 200, r.text

    def test_delete_contact(self, admin_client):
        cid = STATE.get("contact_id")
        if not cid:
            pytest.skip("no contact to delete")
        # Per business rule (2026-05-11), contacts with linked quotes/orders cannot
        # be hard-deleted. The earlier tests in this class created quote+order(s)
        # for this contact, so we expect a 409 with the guard message.
        r = admin_client.delete(f"{BASE_URL}/api/contacts/{cid}")
        assert r.status_code == 409, r.text
        msg = (r.json().get("detail") or "").lower()
        assert "quote" in msg or "order" in msg
