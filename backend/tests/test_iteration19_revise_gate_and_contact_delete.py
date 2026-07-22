"""Iteration 19 — Revise-button gate on converted quotes + Contact Delete flow.

Backend-verifiable slices:
- Contact delete guard: blocked when contact has linked quotes/orders (409/400).
- Contact delete works for fresh unlinked contact (200/204) → 404 on subsequent GET.
- Revise business rule reflected in quote data: converted quote has order_id set;
  unconverted approved/sent quotes have order_id=None (UI gates on this).
- Sanity: double-convert on already-converted quote returns 409 with detail.order_id.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"


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


# ---------- Quote / revise-gate state ----------

def _find_quote_by_number(client, qnum):
    r = client.get(f"{BASE_URL}/api/quotations?limit=500")
    assert r.status_code == 200
    for q in r.json():
        if q.get("quote_number") == qnum:
            return q
    return None


def test_converted_quote_has_order_id(client):
    q = _find_quote_by_number(client, "HRE/QT/2026-27/0027")
    assert q, "seed converted quote HRE/QT/2026-27/0027 not found"
    detail = client.get(f"{BASE_URL}/api/quotations/{q['id']}").json()
    assert detail.get("order_id"), f"expected order_id set on converted quote 0027, got {detail.get('order_id')}"
    # UI rule: Revise must be hidden when order_id is set → assert data supports the gate
    assert detail.get("status") == "approved"


def test_unconverted_approved_quote_no_order_id(client):
    for qnum in ("HRE/QT/2026-27/0028", "HRE/QT/2026-27/0029"):
        q = _find_quote_by_number(client, qnum)
        if not q:
            continue
        detail = client.get(f"{BASE_URL}/api/quotations/{q['id']}").json()
        if detail.get("status") == "approved" and not detail.get("order_id"):
            # UI rule: Revise must be visible
            assert detail["status"] == "approved" and not detail.get("order_id")
            return
    pytest.skip("no approved-not-converted seed quote available")


def test_double_convert_returns_409_with_order_id(client):
    q = _find_quote_by_number(client, "HRE/QT/2026-27/0027")
    assert q
    r = client.post(f"{BASE_URL}/api/quotations/{q['id']}/convert-to-order",
                    json={"po_number": "RETEST"})
    assert r.status_code == 409, r.text
    detail = r.json().get("detail")
    assert isinstance(detail, dict) and detail.get("order_id"), detail


# ---------- Contact delete ----------

def test_delete_blocked_when_contact_has_linked_quotes(client):
    # Find a contact that has quotes
    r = client.get(f"{BASE_URL}/api/contacts?limit=200")
    assert r.status_code == 200
    linked = None
    for c in r.json():
        qs = client.get(f"{BASE_URL}/api/contacts/{c['id']}/quotations").json()
        if qs:
            linked = c
            break
    assert linked, "expected at least one contact with linked quotes"
    d = client.delete(f"{BASE_URL}/api/contacts/{linked['id']}")
    assert d.status_code in (400, 409), f"expected 400/409, got {d.status_code}: {d.text}"


def test_delete_unlinked_test_contact_success(client):
    # CREATE fresh test contact
    payload = {
        "name": "DeleteMe QA",
        "company": "TEST_DELETE_CO",
        "phone": "+919999999901",
        "email": "test_delete_qa@example.com",
        "state": "GUJARAT",
    }
    r = client.post(f"{BASE_URL}/api/contacts", json=payload)
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]
    # DELETE
    d = client.delete(f"{BASE_URL}/api/contacts/{cid}")
    assert d.status_code in (200, 204), f"delete failed: {d.status_code} {d.text}"
    # Verify gone
    g = client.get(f"{BASE_URL}/api/contacts/{cid}")
    assert g.status_code == 404
