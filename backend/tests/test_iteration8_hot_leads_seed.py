"""Iteration 8: Hot Leads dashboard widget + Seed Demo Data admin endpoint tests."""
import os
import pytest
import requests
from pathlib import Path

# Load REACT_APP_BACKEND_URL from frontend/.env
def _load_backend_url() -> str:
    env_file = Path("/app/frontend/.env")
    for line in env_file.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL"):
            return line.split("=", 1)[1].strip().rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL not found")

BASE_URL = _load_backend_url()
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def manager_headers(admin_headers):
    """Create a manager user via admin /users endpoint and login as them."""
    payload = {
        "email": "TEST_manager_hotleads@hrexporter.com",
        "password": "Mgr@1234",
        "full_name": "TEST Manager Hot Leads",
        "role": "manager",
    }
    # Try create; if exists, ignore
    r = requests.post(f"{BASE_URL}/api/users", json=payload, headers=admin_headers, timeout=15)
    if r.status_code not in (200, 201, 400, 409):
        pytest.skip(f"Could not create manager: {r.status_code} {r.text}")
    login = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": payload["email"], "password": payload["password"]},
                          timeout=15)
    if login.status_code != 200:
        pytest.skip(f"Manager login failed: {login.status_code} {login.text}")
    tok = login.json()["token"]
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ─────────────────── /api/dashboard/hot-leads ───────────────────

class TestHotLeads:
    def test_unauthenticated_returns_401(self):
        r = requests.get(f"{BASE_URL}/api/dashboard/hot-leads", timeout=15)
        assert r.status_code in (401, 403), f"Expected 401/403 got {r.status_code}: {r.text}"

    def test_hot_leads_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/dashboard/hot-leads", headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "hot_leads" in data and "total" in data
        assert isinstance(data["hot_leads"], list)
        assert isinstance(data["total"], int)

    def test_hot_leads_field_contract(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/dashboard/hot-leads", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        leads = r.json()["hot_leads"]
        if not leads:
            pytest.skip("No hot leads in DB to validate fields")
        required = {"id", "quote_number", "contact_name", "grand_total",
                    "status", "read_channel", "read_at", "sent_at"}
        for lead in leads:
            missing = required - set(lead.keys())
            assert not missing, f"Lead {lead.get('id')} missing keys: {missing}"
            assert lead["status"] in ("sent", "draft"), f"Bad status: {lead['status']}"
            assert lead["read_channel"] in ("whatsapp", "email"), f"Bad channel: {lead['read_channel']}"

    def test_excludes_approved_rejected_archived(self, admin_headers):
        """Direct DB-truth check: scan all returned ids, ensure none are approved/rejected/archived."""
        r = requests.get(f"{BASE_URL}/api/dashboard/hot-leads", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        for lead in r.json()["hot_leads"]:
            assert lead["status"] in ("sent", "draft")

    def test_total_matches_list_when_under_25(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/dashboard/hot-leads", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        # cap is 25 in response list; total reflects full count
        assert len(data["hot_leads"]) <= 25
        if data["total"] <= 25:
            assert len(data["hot_leads"]) == data["total"]


# ─────────────────── /api/dashboard/seed-demo-data ───────────────────

class TestSeedDemoData:
    def test_seed_unauth_returns_401(self):
        r = requests.post(f"{BASE_URL}/api/dashboard/seed-demo-data", timeout=15)
        assert r.status_code in (401, 403)

    def test_seed_manager_forbidden(self, manager_headers):
        r = requests.post(f"{BASE_URL}/api/dashboard/seed-demo-data",
                          headers=manager_headers, timeout=15)
        assert r.status_code == 403, f"Expected 403 got {r.status_code}: {r.text}"

    def test_seed_admin_succeeds(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/dashboard/seed-demo-data",
                          headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert "contacts_created" in data
        assert isinstance(data["contacts_created"], list)
        assert len(data["contacts_created"]) == 3
        assert "quote_id" in data and data["quote_id"]
        assert "quote_number" in data and data["quote_number"]

    def test_seed_is_idempotent_for_contacts(self, admin_headers):
        """Re-call: contacts_created should reuse the same 3 ids; quote_id changes."""
        r1 = requests.post(f"{BASE_URL}/api/dashboard/seed-demo-data",
                           headers=admin_headers, timeout=20)
        r2 = requests.post(f"{BASE_URL}/api/dashboard/seed-demo-data",
                           headers=admin_headers, timeout=20)
        assert r1.status_code == 200 and r2.status_code == 200
        d1, d2 = r1.json(), r2.json()
        assert sorted(d1["contacts_created"]) == sorted(d2["contacts_created"]), (
            f"Contacts not idempotent: {d1['contacts_created']} vs {d2['contacts_created']}"
        )
        assert d1["quote_id"] != d2["quote_id"], "Quote should be fresh each call"

    def test_seeded_quote_appears_in_hot_leads(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/dashboard/seed-demo-data",
                          headers=admin_headers, timeout=20)
        assert r.status_code == 200
        qid = r.json()["quote_id"]
        hl = requests.get(f"{BASE_URL}/api/dashboard/hot-leads",
                          headers=admin_headers, timeout=15)
        assert hl.status_code == 200
        ids = [lead["id"] for lead in hl.json()["hot_leads"]]
        assert qid in ids, f"Seeded quote {qid} not in hot_leads ids {ids[:5]}..."
        # find the seeded one and verify channel is email
        seeded = next(l for l in hl.json()["hot_leads"] if l["id"] == qid)
        assert seeded["read_channel"] == "email"
        assert seeded["status"] == "sent"
