"""Phase 2A backend tests: Contacts (CRM) + Quotations module."""
import os
import re
import uuid
import time
import pytest
import requests
from datetime import datetime, timezone
from typing import Dict, Any

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


def _expected_fy() -> str:
    d = datetime.now(timezone.utc)
    if d.month >= 4:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_client(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def employee_token():
    """Create or update TEST_employee user directly in MongoDB and return JWT."""
    try:
        import pymongo, bcrypt
        mongo_url = "mongodb://localhost:27017"
        db_name = "test_database"
        with open("/app/backend/.env") as f:
            for ln in f:
                if ln.startswith("MONGO_URL="):
                    mongo_url = ln.split("=", 1)[1].strip().strip('"')
                if ln.startswith("DB_NAME="):
                    db_name = ln.split("=", 1)[1].strip().strip('"')
        cli = pymongo.MongoClient(mongo_url)
        db = cli[db_name]
        email = "test_employee@hrexporter.com"  # server lowercases email on login
        pw = "EmpPass@123"
        pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        if not db.users.find_one({"email": email}):
            db.users.insert_one({
                "id": str(uuid.uuid4()), "name": "TEST Employee", "email": email,
                "mobile": "", "password_hash": pw_hash, "role": "employee", "active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            db.users.update_one({"email": email}, {"$set": {
                "password_hash": pw_hash, "role": "employee", "active": True,
            }})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=20)
        assert r.status_code == 200, r.text
        return r.json()["token"]
    except Exception as e:
        pytest.skip(f"Cannot create employee user: {e}")


# ---------- Contacts ----------
class TestContacts:
    def test_create_contact(self, admin_client):
        payload = {
            "name": "TEST_Alice", "company": "TEST_AcmeCorp",
            "phone": "+91 98765 43210", "email": "TEST_alice@example.com",
            "gst_number": "29ABCDE1234F1Z5",
            "billing_address": "B Addr", "shipping_address": "S Addr",
            "state": "Karnataka", "source": "manual",
            "tags": ["vip", "b2b"], "notes": "first",
        }
        r = admin_client.post(f"{BASE_URL}/api/contacts", json=payload)
        assert r.status_code == 200, r.text
        c = r.json()
        assert c["name"] == "TEST_Alice"
        assert c["company"] == "TEST_AcmeCorp"
        assert "id" in c and len(c["id"]) > 0
        assert c["tags"] == ["vip", "b2b"]
        assert "_id" not in c
        STATE["contact_id"] = c["id"]
        # Persistence check
        r2 = admin_client.get(f"{BASE_URL}/api/contacts/{c['id']}")
        assert r2.status_code == 200
        assert r2.json()["email"] == "TEST_alice@example.com"

    def test_upsert_by_email(self, admin_client):
        # Same email different name/phone -> should update existing, not create new
        payload = {
            "name": "TEST_Alice Updated", "company": "TEST_AcmeCorp2",
            "phone": "9999988888", "email": "TEST_alice@example.com",
            "source": "expo", "tags": ["vvip"], "state": "MH",
        }
        r = admin_client.post(f"{BASE_URL}/api/contacts", json=payload)
        assert r.status_code == 200, r.text
        c = r.json()
        assert c["id"] == STATE["contact_id"], "Should upsert same contact by email"
        assert c["name"] == "TEST_Alice Updated"
        assert c["company"] == "TEST_AcmeCorp2"
        assert c["source"] == "expo"

    def test_upsert_by_phone(self, admin_client):
        # Different email but same last-10-digits phone -> upsert
        payload = {
            "name": "TEST_Alice PhoneMatch", "company": "X",
            "phone": "0091 99999-88888",  # normalised last 10 digits same as 9999988888
            "email": "TEST_alice_other@example.com",
            "state": "Karnataka",
            "source": "whatsapp",
        }
        r = admin_client.post(f"{BASE_URL}/api/contacts", json=payload)
        assert r.status_code == 200, r.text
        c = r.json()
        assert c["id"] == STATE["contact_id"], (
            f"Should upsert by phone but got new id. existing={STATE['contact_id']} returned={c['id']}"
        )

    def test_search_q(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/contacts", params={"q": "TEST_Alice"})
        assert r.status_code == 200
        items = r.json()
        assert any(c["id"] == STATE["contact_id"] for c in items)

    def test_search_q_email(self, admin_client):
        # email field stored is TEST_alice_other@example.com (last upsert by phone changed email)
        r = admin_client.get(f"{BASE_URL}/api/contacts", params={"q": "alice_other"})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()]
        assert STATE["contact_id"] in ids

    def test_filter_source(self, admin_client):
        # After test_upsert_by_phone, current source is 'whatsapp'
        r = admin_client.get(f"{BASE_URL}/api/contacts", params={"source": "whatsapp"})
        assert r.status_code == 200
        items = r.json()
        assert all(c["source"] == "whatsapp" for c in items)
        assert any(c["id"] == STATE["contact_id"] for c in items)

    def test_get_contact_by_id(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/contacts/{STATE['contact_id']}")
        assert r.status_code == 200
        c = r.json()
        assert c["id"] == STATE["contact_id"]

    def test_get_contact_404(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/contacts/does-not-exist-xyz")
        assert r.status_code == 404

    def test_update_contact(self, admin_client):
        payload = {
            "name": "TEST_Alice Final", "company": "TEST_AcmeCorp2",
            "phone": "9999988888", "email": "TEST_alice@example.com",
            "state": "Goa", "source": "expo", "tags": ["vip"],
        }
        r = admin_client.put(f"{BASE_URL}/api/contacts/{STATE['contact_id']}", json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "TEST_Alice Final"
        # Persistence
        r2 = admin_client.get(f"{BASE_URL}/api/contacts/{STATE['contact_id']}")
        assert r2.json()["state"] == "Goa"

    def test_employee_cannot_delete_contact(self, employee_token):
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {employee_token}", "Content-Type": "application/json"})
        r = s.delete(f"{BASE_URL}/api/contacts/{STATE['contact_id']}")
        assert r.status_code == 403

    def test_employee_can_create_contact(self, employee_token):
        # role spec: admin+manager can create. So employee should also be 403
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {employee_token}", "Content-Type": "application/json"})
        r = s.post(f"{BASE_URL}/api/contacts", json={"name": "TEST_emp_should_fail", "phone": "1234567890"})
        assert r.status_code == 403


# ---------- Quotations ----------
class TestQuoteNumbering:
    def test_next_number_preview_format(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/quotations/next-number")
        assert r.status_code == 200, r.text
        prev = r.json()["preview"]
        fy = _expected_fy()
        assert re.match(rf"^HRE/QT/{re.escape(fy)}/\d{{4}}$", prev), f"Bad format: {prev}"
        STATE["expected_fy"] = fy

    def test_create_quote_uses_preview_and_increments(self, admin_client):
        # snapshot preview, then create -> quote_number should match preview, next preview increments
        r = admin_client.get(f"{BASE_URL}/api/quotations/next-number")
        before_preview = r.json()["preview"]

        # Need a contact + a variant for line items
        contact_id = STATE["contact_id"]
        r = admin_client.get(f"{BASE_URL}/api/product-variants")
        variants = r.json()
        v = variants[0]

        payload = {
            "contact_id": contact_id,
            "place_of_supply": "Karnataka",
            "valid_until": "2026-12-31",
            "notes": "test quote",
            "terms": "30 days",
            "line_items": [{
                "product_variant_id": v["id"],
                "product_code": v["product_code"],
                "family_name": "TestFam",
                "description": "desc",
                "cable_size": v.get("cable_size", ""),
                "hole_size": v.get("hole_size", ""),
                "dimensions": {"A": "1.6"},
                "hsn_code": "85369090",
                "quantity": 10,
                "unit": "NOS",
                "base_price": 100.0,
                "discount_percentage": 10.0,
                "gst_percentage": 18.0,
            }, {
                "product_variant_id": None,
                "product_code": "MISC-1",
                "quantity": 2,
                "unit": "NOS",
                "base_price": 50.0,
                "discount_percentage": 0.0,
                "gst_percentage": 18.0,
            }],
        }
        r = admin_client.post(f"{BASE_URL}/api/quotations", json=payload)
        assert r.status_code == 200, r.text
        q = r.json()
        assert q["quote_number"] == before_preview, f"Expected {before_preview} got {q['quote_number']}"
        assert q["status"] == "draft"
        assert q["version"] == 1
        assert q["parent_quote_id"] is None
        # Contact snapshot
        assert q["contact_id"] == contact_id
        assert q["contact_name"]  # snapshot present
        # Totals validation:
        # Line1: gross 1000, disc 100, taxable 900, gst 162, total 1062
        # Line2: gross 100, disc 0, taxable 100, gst 18, total 118
        # subtotal=1100, total_discount=100, taxable_value=1000, total_gst=180, grand_total=1180
        assert q["subtotal"] == 1100.0
        assert q["total_discount"] == 100.0
        assert q["taxable_value"] == 1000.0
        assert q["total_gst"] == 180.0
        assert q["grand_total"] == 1180.0
        # Per line computed
        li0 = q["line_items"][0]
        assert li0["line_gross"] == 1000.0
        assert li0["discount_amount"] == 100.0
        assert li0["taxable_value"] == 900.0
        assert li0["gst_amount"] == 162.0
        assert li0["line_total"] == 1062.0
        STATE["quote_id"] = q["id"]
        STATE["quote_number"] = q["quote_number"]

        # Next preview should increment
        r = admin_client.get(f"{BASE_URL}/api/quotations/next-number")
        next_preview = r.json()["preview"]
        # extract numeric tails
        cur_tail = int(before_preview.split("/")[-1])
        next_tail = int(next_preview.split("/")[-1])
        assert next_tail == cur_tail + 1

    def test_quote_number_increments_per_fy(self, admin_client):
        # Create a second quote -> number = previous +1
        contact_id = STATE["contact_id"]
        payload = {
            "contact_id": contact_id,
            "line_items": [{
                "product_code": "X", "quantity": 1, "base_price": 200,
                "discount_percentage": 0, "gst_percentage": 18,
            }],
        }
        prev_tail = int(STATE["quote_number"].split("/")[-1])
        r = admin_client.post(f"{BASE_URL}/api/quotations", json=payload)
        assert r.status_code == 200, r.text
        new_tail = int(r.json()["quote_number"].split("/")[-1])
        assert new_tail == prev_tail + 1
        STATE["quote2_id"] = r.json()["id"]


class TestQuotationsCRUD:
    def test_list_quotations(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/quotations")
        assert r.status_code == 200
        ids = [q["id"] for q in r.json()]
        assert STATE["quote_id"] in ids

    def test_list_filter_status(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/quotations", params={"status_filter": "draft"})
        assert r.status_code == 200
        assert all(q["status"] == "draft" for q in r.json())

    def test_list_filter_contact(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/quotations", params={"contact_id": STATE["contact_id"]})
        assert r.status_code == 200
        assert all(q["contact_id"] == STATE["contact_id"] for q in r.json())

    def test_list_search_q(self, admin_client):
        # search by quote_number substring
        r = admin_client.get(f"{BASE_URL}/api/quotations", params={"q": STATE["quote_number"][-4:]})
        assert r.status_code == 200
        assert any(q["id"] == STATE["quote_id"] for q in r.json())

    def test_get_quote_by_id(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/quotations/{STATE['quote_id']}")
        assert r.status_code == 200
        q = r.json()
        assert q["id"] == STATE["quote_id"]
        assert "line_items" in q and len(q["line_items"]) == 2

    def test_get_quote_404(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/quotations/non-existent")
        assert r.status_code == 404

    def test_update_draft_recalculates(self, admin_client):
        payload = {
            "contact_id": STATE["contact_id"],
            "place_of_supply": "Goa",
            "line_items": [{
                "product_code": "Y", "quantity": 4, "base_price": 250.0,
                "discount_percentage": 20.0, "gst_percentage": 18.0,
            }],
        }
        r = admin_client.put(f"{BASE_URL}/api/quotations/{STATE['quote_id']}", json=payload)
        assert r.status_code == 200, r.text
        q = r.json()
        # gross=1000, disc=200, taxable=800, gst=144, total=944
        assert q["subtotal"] == 1000.0
        assert q["total_discount"] == 200.0
        assert q["taxable_value"] == 800.0
        assert q["total_gst"] == 144.0
        assert q["grand_total"] == 944.0


class TestQuoteStatusFlow:
    def test_change_status_sent_records_timestamp(self, admin_client):
        r = admin_client.patch(f"{BASE_URL}/api/quotations/{STATE['quote_id']}/status",
                               json={"status": "sent"})
        assert r.status_code == 200, r.text
        q = r.json()
        assert q["status"] == "sent"
        assert q.get("sent_at") is not None

    def test_change_status_approved_records_timestamp(self, admin_client):
        r = admin_client.patch(f"{BASE_URL}/api/quotations/{STATE['quote_id']}/status",
                               json={"status": "approved"})
        assert r.status_code == 200
        q = r.json()
        assert q["status"] == "approved"
        assert q.get("approved_at") is not None

    def test_update_approved_returns_400(self, admin_client):
        payload = {
            "contact_id": STATE["contact_id"],
            "line_items": [{"product_code": "Z", "quantity": 1, "base_price": 1.0,
                            "discount_percentage": 0, "gst_percentage": 18}],
        }
        r = admin_client.put(f"{BASE_URL}/api/quotations/{STATE['quote_id']}", json=payload)
        assert r.status_code == 400, f"Expected 400 got {r.status_code}: {r.text}"

    def test_invalid_status_returns_400(self, admin_client):
        r = admin_client.patch(f"{BASE_URL}/api/quotations/{STATE['quote2_id']}/status",
                               json={"status": "bogus"})
        assert r.status_code == 400

    def test_change_status_rejected_records_timestamp(self, admin_client):
        # use 2nd quote for rejected
        r = admin_client.patch(f"{BASE_URL}/api/quotations/{STATE['quote2_id']}/status",
                               json={"status": "rejected"})
        assert r.status_code == 200
        q = r.json()
        assert q["status"] == "rejected"
        assert q.get("rejected_at") is not None

    def test_update_rejected_returns_400(self, admin_client):
        payload = {
            "contact_id": STATE["contact_id"],
            "line_items": [{"product_code": "Z", "quantity": 1, "base_price": 1.0,
                            "discount_percentage": 0, "gst_percentage": 18}],
        }
        r = admin_client.put(f"{BASE_URL}/api/quotations/{STATE['quote2_id']}", json=payload)
        assert r.status_code == 400


class TestQuoteRevise:
    def test_revise_creates_v2(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/quotations/{STATE['quote_id']}/revise")
        assert r.status_code == 200, r.text
        nv = r.json()
        assert nv["version"] == 2
        assert nv["parent_quote_id"] == STATE["quote_id"]
        assert nv["status"] == "draft"
        assert nv["quote_number"].endswith("-R2"), f"Got {nv['quote_number']}"
        STATE["revision_id"] = nv["id"]
        # source quote should be marked revised
        r = admin_client.get(f"{BASE_URL}/api/quotations/{STATE['quote_id']}")
        assert r.json()["status"] == "revised"


class TestContactQuotations:
    def test_contact_quotations_history(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/contacts/{STATE['contact_id']}/quotations")
        assert r.status_code == 200
        items = r.json()
        ids = [q["id"] for q in items]
        # original, second, and revision should be present
        assert STATE["quote_id"] in ids
        assert STATE["quote2_id"] in ids
        assert STATE["revision_id"] in ids


class TestQuoteDashboard:
    def test_dashboard_quote_stats(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/dashboard/quote-stats")
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["counts", "pipeline_value", "won_value", "total_contacts"]:
            assert k in d, f"missing {k}"
        for st in ["draft", "sent", "approved", "rejected", "revised", "expired"]:
            assert st in d["counts"]
        # We approved quote_id (now revised), rejected quote2_id, draft revision_id
        assert d["counts"]["revised"] >= 1
        assert d["counts"]["rejected"] >= 1
        assert d["counts"]["draft"] >= 1
        assert d["total_contacts"] >= 1
        assert isinstance(d["pipeline_value"], (int, float))
        assert isinstance(d["won_value"], (int, float))


# ---------- Role enforcement ----------
class TestRoleEnforcement:
    def test_employee_cannot_create_quote(self, employee_token):
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {employee_token}", "Content-Type": "application/json"})
        r = s.post(f"{BASE_URL}/api/quotations",
                   json={"contact_id": STATE["contact_id"], "line_items": []})
        assert r.status_code == 403

    def test_employee_cannot_delete_quote(self, employee_token):
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {employee_token}", "Content-Type": "application/json"})
        r = s.delete(f"{BASE_URL}/api/quotations/{STATE['quote_id']}")
        assert r.status_code == 403


# ---------- Cleanup (admin delete) ----------
class TestZCleanup:
    def test_admin_delete_quote(self, admin_client):
        for key in ("revision_id", "quote2_id", "quote_id"):
            qid = STATE.get(key)
            if not qid:
                continue
            r = admin_client.delete(f"{BASE_URL}/api/quotations/{qid}")
            assert r.status_code == 200, f"{key}: {r.status_code} {r.text}"
            r = admin_client.get(f"{BASE_URL}/api/quotations/{qid}")
            assert r.status_code == 404

    def test_admin_delete_contact(self, admin_client):
        cid = STATE.get("contact_id")
        if cid:
            r = admin_client.delete(f"{BASE_URL}/api/contacts/{cid}")
            assert r.status_code == 200
            r = admin_client.get(f"{BASE_URL}/api/contacts/{cid}")
            assert r.status_code == 404
