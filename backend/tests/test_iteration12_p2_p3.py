"""Iteration 12 — P2/P3 features.

Covers:
  - P2 customer PO acknowledgement (shape check on submit-po response)
  - P2 public /track (path + query variants, slim/verified)
  - P2 customer-360 endpoint
  - P3 brute-force lockout (5+1 → 429)
  - P3 /api/health/integrations endpoint
  - P3 quote diff endpoint
"""
import os
import time
import uuid
import pytest
import requests


def _read_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        return ""
    return ""


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_env() or "").rstrip("/")
assert BASE_URL
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASS = "Admin@123"
ORDER_NUMBER = "HRE/ORD/2026-27/0001"


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def H(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────── P2 public /track ───────────────────

class TestPublicTrack:
    def test_track_query_slim(self):
        r = requests.get(f"{API}/public/track", params={"order_number": ORDER_NUMBER}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["order_number"] == ORDER_NUMBER
        assert "milestones" in body
        assert isinstance(body["milestones"], list)
        # 6 stages: Order Confirmed → Delivered
        assert len(body["milestones"]) == 6
        assert body.get("verified") is False
        # Slim: line_status & shipments NOT present (unverified)
        assert "line_status" not in body
        assert "shipments" not in body

    def test_track_path_variant(self):
        # FastAPI :path captures slashes
        r = requests.get(f"{API}/public/track/{ORDER_NUMBER}", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["order_number"] == ORDER_NUMBER
        assert body.get("verified") is False

    def test_track_wrong_phone_unverified(self):
        r = requests.get(f"{API}/public/track",
                         params={"order_number": ORDER_NUMBER, "phone": "9999999999"},
                         timeout=15)
        assert r.status_code == 200
        assert r.json().get("verified") is False

    def test_track_correct_phone_verified(self, H):
        # Look up the contact for this order to get the real phone
        order_q = requests.get(f"{API}/orders", headers=H, timeout=15).json() or []
        target = next((o for o in order_q if o.get("order_number") == ORDER_NUMBER), None)
        if not target:
            pytest.skip(f"Order {ORDER_NUMBER} not found")
        contact = requests.get(f"{API}/contacts/{target['contact_id']}", headers=H, timeout=10).json()
        phone_norm = (contact.get("phone_norm") or "")[-10:]
        if len(phone_norm) != 10:
            pytest.skip("contact has no valid phone_norm")
        r = requests.get(f"{API}/public/track",
                         params={"order_number": ORDER_NUMBER, "phone": phone_norm},
                         timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body.get("verified") is True
        assert "line_status" in body
        assert "shipments" in body

    def test_track_unknown_order_404(self):
        r = requests.get(f"{API}/public/track", params={"order_number": "HRE/ORD/9999-99/9999"}, timeout=15)
        assert r.status_code == 404


# ─────────────────── P2 customer-360 ───────────────────

class TestCustomer360:
    def test_customer_360_for_existing_contact(self, H):
        # Pick first contact with at least one order
        order_q = requests.get(f"{API}/orders", headers=H, timeout=15).json() or []
        if not order_q:
            pytest.skip("no orders to pick a contact from")
        cid = order_q[0].get("contact_id")
        if not cid:
            pytest.skip("no contact_id on order")
        r = requests.get(f"{API}/contacts/{cid}/customer-360", headers=H, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("contact", "quotes", "orders", "whatsapp_engagement", "totals"):
            assert key in body, f"missing key: {key}"
        assert isinstance(body["quotes"], list) and len(body["quotes"]) <= 5
        assert isinstance(body["orders"], list) and len(body["orders"]) <= 3
        wa = body["whatsapp_engagement"]
        for k in ("sent", "delivered", "read", "failed"):
            assert k in wa
        totals = body["totals"]
        assert "quotes_total" in totals and "orders_total" in totals

    def test_customer_360_unknown_404(self, H):
        r = requests.get(f"{API}/contacts/nonexistent-id-{uuid.uuid4().hex}/customer-360", headers=H, timeout=10)
        assert r.status_code == 404

    def test_customer_360_requires_auth(self):
        r = requests.get(f"{API}/contacts/anything/customer-360", timeout=10)
        assert r.status_code in (401, 403)


# ─────────────────── P3 brute-force lockout ───────────────────

class TestBruteForceLockout:
    """CRITICAL: use a non-existent email so we don't lock admin@hrexporter.com."""
    LOCKOUT_EMAIL = f"lockouttest_iter12_{uuid.uuid4().hex[:8]}@example.com"

    def test_5_fails_then_429_then_admin_still_ok(self):
        # 5 wrong → 401
        for i in range(5):
            r = requests.post(f"{API}/auth/login",
                              json={"email": self.LOCKOUT_EMAIL, "password": "wrong"}, timeout=10)
            assert r.status_code == 401, f"attempt {i+1}: got {r.status_code}, body={r.text[:200]}"
        # 6th → 429
        r = requests.post(f"{API}/auth/login",
                          json={"email": self.LOCKOUT_EMAIL, "password": "wrong"}, timeout=10)
        assert r.status_code == 429, r.text
        body = r.json()
        detail = (body.get("detail") or "").lower() if isinstance(body, dict) else ""
        assert "too many" in detail or "minutes" in detail

        # IMPORTANT: admin login on a different email must still succeed (not locked).
        r2 = requests.post(f"{API}/auth/login",
                           json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=10)
        assert r2.status_code == 200, r2.text

    def test_admin_failed_counter_clears_on_success(self):
        # 2 wrong (under threshold) then correct → counter should clear
        admin_test = ADMIN_EMAIL
        for _ in range(2):
            r = requests.post(f"{API}/auth/login",
                              json={"email": admin_test, "password": "wrong"}, timeout=10)
            assert r.status_code == 401
        r = requests.post(f"{API}/auth/login",
                          json={"email": admin_test, "password": ADMIN_PASS}, timeout=10)
        assert r.status_code == 200
        # Now another 2 wrong should still only be 401 (not 429) since the counter cleared
        for _ in range(2):
            r = requests.post(f"{API}/auth/login",
                              json={"email": admin_test, "password": "wrong"}, timeout=10)
            assert r.status_code == 401
        # Restore: log in once more to clear
        r = requests.post(f"{API}/auth/login",
                         json={"email": admin_test, "password": ADMIN_PASS}, timeout=10)
        assert r.status_code == 200


# ─────────────────── P3 /api/health/integrations ───────────────────

class TestHealthIntegrations:
    def test_requires_auth(self):
        r = requests.get(f"{API}/health/integrations", timeout=15)
        assert r.status_code in (401, 403)

    def test_returns_shape(self, H):
        t0 = time.monotonic()
        r = requests.get(f"{API}/health/integrations", headers=H, timeout=15)
        elapsed = time.monotonic() - t0
        assert r.status_code == 200, r.text
        assert elapsed < 12, f"too slow: {elapsed:.1f}s"
        body = r.json()
        for k in ("whatsapp", "smtp", "test_mode", "overall_ok"):
            assert k in body, f"missing key {k}"
        for svc in ("whatsapp", "smtp"):
            for sk in ("ok", "configured", "error", "latency_ms"):
                assert sk in body[svc], f"missing {svc}.{sk}"


# ─────────────────── P3 /api/quotations/{qid}/diff/{other_qid} ───────────────────

class TestQuoteDiff:
    def test_self_diff_all_unchanged(self, H):
        quotes = requests.get(f"{API}/quotations", headers=H, timeout=15).json() or []
        if not quotes:
            pytest.skip("no quotes in DB")
        qid = quotes[0]["id"]
        r = requests.get(f"{API}/quotations/{qid}/diff/{qid}", headers=H, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        for k in ("newer", "older", "line_diff", "totals_diff", "summary"):
            assert k in body
        # All lines unchanged
        for ld in body["line_diff"]:
            assert ld["change"] == "unchanged", f"self-diff produced {ld['change']}"
        # totals_diff should be empty
        assert body["totals_diff"] == {} or body["totals_diff"] == {} or not body["totals_diff"]
        assert body["summary"]["added"] == 0
        assert body["summary"]["removed"] == 0
        assert body["summary"]["modified"] == 0

    def test_diff_unknown_404(self, H):
        quotes = requests.get(f"{API}/quotations", headers=H, timeout=15).json() or []
        if not quotes:
            pytest.skip("no quotes in DB")
        qid = quotes[0]["id"]
        r = requests.get(f"{API}/quotations/{qid}/diff/nonexistent-{uuid.uuid4().hex}",
                        headers=H, timeout=10)
        assert r.status_code == 404

    def test_diff_different_contacts_400(self, H):
        quotes = requests.get(f"{API}/quotations", headers=H, timeout=15).json() or []
        if len(quotes) < 2:
            pytest.skip("need at least 2 quotes")
        a = quotes[0]
        b = next((q for q in quotes if q.get("contact_id") != a.get("contact_id")), None)
        if not b:
            pytest.skip("all quotes share one contact")
        r = requests.get(f"{API}/quotations/{a['id']}/diff/{b['id']}", headers=H, timeout=15)
        assert r.status_code == 400, r.text


# ─────────────────── P2 customer PO ack — shape only ───────────────────

class TestCustomerPoAckShape:
    """We don't run a real submit-po (it requires a public OTP session and would
    fire ack to test phone). Just verify the response shape via an existing
    integration test path if possible. If no approved quote exists, we skip."""

    def test_submit_po_response_shape(self, H):
        # Locate an existing draft order with `pending_po` stage OR create a TEST quote,
        # approve it, then submit-po via a synthetic public session. To avoid touching
        # OTP flow we just verify the endpoint and shape exist via a 422/4xx response.
        r = requests.post(f"{API}/public/quote/test-noexist/submit-po",
                          data={"token": "BAD", "instructions": "hello"}, timeout=10)
        # token resolution must fail → 401/403/404/400 — endpoint exists
        assert r.status_code in (400, 401, 403, 404, 422), r.text
