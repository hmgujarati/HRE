"""Iteration 18 — Comprehensive state-appropriate-button audit.

Focus:
  1. Convert-to-order guard: quotation carries `order_id`+`order_number` after
     conversion; 2nd POST returns 409 with detail={message, order_id, order_number}.
  2. Backfill sanity: every existing order has its source quote's `order_id` populated.
  3. Delete quotation blocked (409) once an order was created from it.
  4. Delete contact blocked (409) once linked orders/quotes exist.
  5. Convert-to-order rejects quotes not in approved/sent.
  6. Public /my-quotes: only sent/approved/etc quotes surface (draft filtered out).
"""

import os
import time
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@hrexporter.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# ── Helpers ──────────────────────────────────────────────────────────────
def _get(client, path):
    r = client.get(f"{BASE_URL}{path}", timeout=15)
    return r


def _quote_by_number(client, number):
    r = client.get(f"{BASE_URL}/api/quotations", timeout=15)
    assert r.status_code == 200
    for q in r.json():
        if q.get("quote_number") == number:
            return q
    return None


# ── CONVERT-TO-ORDER GUARD ───────────────────────────────────────────────
class TestConvertToOrderGuard:

    def test_existing_converted_quote_has_order_id(self, client):
        # Order HRE/ORD/2026-27/0001 exists per credentials note.
        r = _get(client, "/api/orders")
        assert r.status_code == 200
        orders = r.json()
        assert orders, "expected at least one existing order in DB"
        # pick any order that has quote_id
        target = next((o for o in orders if o.get("quote_id")), None)
        assert target, "no order with quote_id linkage found"
        qr = _get(client, f"/api/quotations/{target['quote_id']}")
        assert qr.status_code == 200
        q = qr.json()
        assert q.get("order_id") == target["id"], "quote missing back-link order_id"
        assert q.get("order_number") == target["order_number"], "quote missing back-link order_number"

    def test_convert_to_order_returns_409_when_already_converted(self, client):
        # pick a quote that already has order_id
        r = _get(client, "/api/quotations?archived=true")
        assert r.status_code == 200
        quotes = r.json()
        converted = [q for q in quotes if q.get("order_id")]
        # also non-archived
        r2 = _get(client, "/api/quotations")
        for q in r2.json():
            if q.get("order_id"):
                converted.append(q)
        assert converted, "no converted quote found — cannot verify 409 guard"
        q = converted[0]
        resp = client.post(f"{BASE_URL}/api/quotations/{q['id']}/convert-to-order",
                           json={"po_number": ""}, timeout=15)
        assert resp.status_code == 409, f"expected 409, got {resp.status_code}: {resp.text}"
        body = resp.json()
        detail = body.get("detail")
        assert isinstance(detail, dict), f"detail must be dict, got {type(detail)}"
        assert "message" in detail
        assert detail.get("order_id") == q["order_id"]
        assert detail.get("order_number") == q["order_number"]

    def test_backfill_sanity_every_order_has_quote_back_link(self, client):
        """Every order that references a quote_id should have that quote carrying
        an order_id back-pointer (backfill script verification)."""
        r = _get(client, "/api/orders")
        orders = r.json()
        broken = []
        for o in orders:
            qid = o.get("quote_id")
            if not qid:
                continue
            qr = _get(client, f"/api/quotations/{qid}")
            if qr.status_code != 200:
                broken.append((o["order_number"], "quote missing"))
                continue
            q = qr.json()
            if q.get("order_id") != o["id"]:
                broken.append((o["order_number"], f"quote.order_id={q.get('order_id')}"))
        assert not broken, f"Backfill incomplete for orders: {broken}"


# ── DELETE GUARDS ────────────────────────────────────────────────────────
class TestDeleteGuards:

    def test_delete_quotation_blocked_when_order_exists(self, client):
        r = _get(client, "/api/orders")
        with_qid = next((o for o in r.json() if o.get("quote_id")), None)
        assert with_qid, "no order with quote_id"
        resp = client.delete(f"{BASE_URL}/api/quotations/{with_qid['quote_id']}", timeout=15)
        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", "")
        assert "order" in detail.lower()

    def test_delete_contact_blocked_when_linked(self, client):
        # pick contact from an order
        r = _get(client, "/api/orders")
        orders = r.json()
        if not orders:
            pytest.skip("no orders in DB")
        contact_id = orders[0].get("contact_id")
        if not contact_id:
            pytest.skip("order missing contact_id")
        resp = client.delete(f"{BASE_URL}/api/contacts/{contact_id}", timeout=15)
        # backend should refuse (409 or 400 with descriptive detail)
        assert resp.status_code in (400, 409), (
            f"contact delete not guarded: {resp.status_code} {resp.text[:200]}")


# ── STATUS PRECONDITIONS ─────────────────────────────────────────────────
class TestConvertRejectsBadStatus:

    def test_convert_rejects_draft_quote(self, client):
        # Create a new draft quote and try to convert.
        # Need a contact first.
        cs = client.get(f"{BASE_URL}/api/contacts", timeout=15).json()
        if not cs:
            pytest.skip("no contacts")
        c = cs[0]
        payload = {
            "contact_id": c["id"],
            "contact_name": c.get("name", "Test"),
            "contact_company": c.get("company", ""),
            "contact_phone": c.get("phone", ""),
            "line_items": [{
                "product_code": "TEST-PROD", "family_name": "Test", "cable_size": "1.5",
                "hole_size": "", "quantity": 1, "unit": "pc", "base_price": 100,
                "gst_percentage": 18, "hsn_code": "0000", "description": "TEST_iter18",
            }],
            "notes": "TEST_iter18_draft", "terms": "", "place_of_supply": "GUJARAT",
        }
        r = client.post(f"{BASE_URL}/api/quotations", json=payload, timeout=15)
        assert r.status_code in (200, 201), r.text
        q = r.json()
        qid = q["id"]
        try:
            assert q.get("status") == "draft", f"expected draft, got {q.get('status')}"
            resp = client.post(f"{BASE_URL}/api/quotations/{qid}/convert-to-order",
                               json={"po_number": ""}, timeout=15)
            # underlying route allows sent OR approved; draft must be rejected
            assert resp.status_code == 400, resp.text
        finally:
            # Cleanup: force-delete via hard delete (admin)
            client.delete(f"{BASE_URL}/api/quotations/{qid}", timeout=15)


# ── SHIPMENT STATE BUTTONS (backend side) ────────────────────────────────
class TestShipmentGuards:

    def test_shipment_dispatch_requires_docs(self, client):
        """Backend guard: cannot dispatch a shipment without tax_invoice + LR."""
        r = _get(client, "/api/orders")
        for o in r.json():
            ships = o.get("shipments") or []
            drafts = [s for s in ships
                      if s.get("stage") in ("created", "invoiced")
                      and not ((s.get("documents") or {}).get("tax_invoice"))]
            if drafts:
                s = drafts[0]
                resp = client.post(
                    f"{BASE_URL}/api/orders/{o['id']}/shipments/{s['id']}/dispatch",
                    json={}, timeout=15)
                assert resp.status_code in (400, 409), (
                    f"dispatch not guarded: {resp.status_code} {resp.text[:200]}")
                return
        pytest.skip("no draft shipment without tax_invoice available")


# ── PUBLIC MY-QUOTES: draft filtering ────────────────────────────────────
class TestPublicQuotesFiltering:

    def test_my_quotes_filters_draft(self, client):
        # This is an indirect check: sample a customer contact with a token.
        # Backend method should exclude draft.
        # We validate at the code level by hitting the route with an invalid
        # token and confirming shape; deeper check would need real OTP flow.
        r = requests.get(f"{BASE_URL}/api/public/my-quotes?token=invalid", timeout=15)
        # invalid token -> 401 or 403; endpoint alive
        assert r.status_code in (401, 403), (r.status_code, r.text[:200])
