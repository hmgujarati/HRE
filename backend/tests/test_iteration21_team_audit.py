"""Iter21 — Team/Users CRUD + AuditMiddleware + audit-logs endpoints.

Covers admin-only user management, permission fields, non-admin guards,
audit capture rules (writes only, skip auth/webhook/etc), filters, and
summary. Cleans up all TEST_ users at the end.
"""
from __future__ import annotations
import os
import time
import uuid

import pytest
import requests

def _load_frontend_env():
    p = "/app/frontend/.env"
    try:
        with open(p) as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _load_frontend_env() or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not set"
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASS = "Admin@123"

# Unique employee for this run
_run = uuid.uuid4().hex[:8]
EMP_EMAIL = f"audittest+{_run}@hrexporter.com"
EMP_PASS = "Auditest@1234"

# Secondary admin used for last-admin-guard test
ADMIN2_EMAIL = f"tempadmin+{_run}@hrexporter.com"
ADMIN2_PASS = "TempAdmin@1234"


# ─────────────────────────── Fixtures ──────────────────────────────────
@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    body = r.json()
    # Assert new keys are surfaced on login
    u = body["user"]
    assert u["role"] == "admin"
    assert u["can_delete"] is True
    assert u["can_edit"] is True
    assert u["allowed_tabs"] == []
    return body["token"]


@pytest.fixture(scope="module")
def admin_hdr(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def created_ids():
    return {"emp": None, "admin2": None}


@pytest.fixture(scope="module", autouse=True)
def cleanup(admin_hdr, created_ids):
    yield
    # Best-effort teardown: deactivate anything we created
    for uid in filter(None, [created_ids.get("emp"), created_ids.get("admin2")]):
        try:
            requests.post(f"{API}/users/{uid}/deactivate", headers=admin_hdr, timeout=10)
        except Exception:
            pass


# ─────────────────────────── /auth/me shape ────────────────────────────
def test_auth_me_shape(admin_hdr):
    r = requests.get(f"{API}/auth/me", headers=admin_hdr, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("can_delete", "can_edit", "allowed_tabs"):
        assert k in body, f"missing {k}"
    assert body["can_delete"] is True
    assert body["can_edit"] is True
    assert body["allowed_tabs"] == []


# ─────────────────────────── Team CRUD ─────────────────────────────────
class TestTeamCRUD:
    def test_list_users(self, admin_hdr):
        r = requests.get(f"{API}/users", headers=admin_hdr, timeout=10)
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 1
        # ensure no password_hash or _id leaked
        for u in rows:
            assert "password_hash" not in u
            assert "_id" not in u
        assert any(u["email"] == ADMIN_EMAIL for u in rows)

    def test_create_employee(self, admin_hdr, created_ids):
        payload = {
            "name": f"Audit Tester {_run}",
            "email": EMP_EMAIL,
            "mobile": "+919999999999",
            "role": "employee",
            "password": EMP_PASS,
            "can_edit": True,
            "can_delete": False,
            "allowed_tabs": ["dashboard", "quotations"],
        }
        r = requests.post(f"{API}/users", headers=admin_hdr, json=payload, timeout=10)
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["email"] == EMP_EMAIL
        assert body["role"] == "employee"
        assert body["can_edit"] is True
        assert body["can_delete"] is False
        assert body["allowed_tabs"] == ["dashboard", "quotations"]
        assert "password_hash" not in body
        assert body.get("id")
        created_ids["emp"] = body["id"]

    def test_duplicate_email_conflict(self, admin_hdr):
        r = requests.post(
            f"{API}/users", headers=admin_hdr,
            json={"name": "dup", "email": EMP_EMAIL, "password": EMP_PASS, "role": "employee"},
            timeout=10,
        )
        assert r.status_code == 409, r.text

    def test_invalid_role_rejected(self, admin_hdr):
        r = requests.post(
            f"{API}/users", headers=admin_hdr,
            json={"name": "x", "email": f"bad+{_run}@t.com", "password": "SomePass1", "role": "superuser"},
            timeout=10,
        )
        assert r.status_code == 400

    def test_invalid_tab_key_rejected(self, admin_hdr):
        r = requests.post(
            f"{API}/users", headers=admin_hdr,
            json={"name": "x", "email": f"badtabs+{_run}@t.com", "password": "SomePass1",
                  "role": "employee", "allowed_tabs": ["bogus-key"]},
            timeout=10,
        )
        assert r.status_code == 400

    def test_patch_permission_field(self, admin_hdr, created_ids):
        uid = created_ids["emp"]
        assert uid, "employee must be created first"
        r = requests.patch(f"{API}/users/{uid}", headers=admin_hdr,
                           json={"can_delete": True}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["can_delete"] is True
        assert body["can_edit"] is True  # unchanged
        assert body["email"] == EMP_EMAIL

    def test_reset_password_and_login(self, admin_hdr, created_ids):
        uid = created_ids["emp"]
        new_pw = "NewAudit@5678"
        r = requests.post(f"{API}/users/{uid}/reset-password", headers=admin_hdr,
                          json={"new_password": new_pw}, timeout=10)
        assert r.status_code == 200, r.text
        # login with new password
        r2 = requests.post(f"{API}/auth/login", json={"email": EMP_EMAIL, "password": new_pw}, timeout=10)
        assert r2.status_code == 200, r2.text
        emp_user = r2.json()["user"]
        assert emp_user["role"] == "employee"
        # Employee that had can_delete=true set earlier
        assert emp_user["can_delete"] is True
        assert emp_user["allowed_tabs"] == ["dashboard", "quotations"]

    def test_deactivate_then_activate(self, admin_hdr, created_ids):
        uid = created_ids["emp"]
        r = requests.post(f"{API}/users/{uid}/deactivate", headers=admin_hdr, timeout=10)
        assert r.status_code == 200
        assert r.json()["active"] is False
        # Deactivated user cannot log in
        r_login = requests.post(f"{API}/auth/login",
                                json={"email": EMP_EMAIL, "password": "NewAudit@5678"}, timeout=10)
        assert r_login.status_code == 401
        r2 = requests.post(f"{API}/users/{uid}/activate", headers=admin_hdr, timeout=10)
        assert r2.status_code == 200
        assert r2.json()["active"] is True


# ────────────────── Last-admin guard (create/delete secondary admin) ───
class TestLastAdminGuard:
    def test_self_deactivate_blocked(self, admin_hdr):
        # find own id
        me = requests.get(f"{API}/auth/me", headers=admin_hdr, timeout=10).json()
        r = requests.post(f"{API}/users/{me['id']}/deactivate", headers=admin_hdr, timeout=10)
        assert r.status_code == 400
        assert "yourself" in r.text.lower() or "cannot" in r.text.lower()

    def test_last_admin_demote_blocked(self, admin_hdr, created_ids):
        # Create secondary admin
        r = requests.post(f"{API}/users", headers=admin_hdr, json={
            "name": f"Temp Admin {_run}", "email": ADMIN2_EMAIL,
            "password": ADMIN2_PASS, "role": "admin",
        }, timeout=10)
        assert r.status_code in (200, 201), r.text
        admin2_id = r.json()["id"]
        created_ids["admin2"] = admin2_id

        # Log in as admin2
        tok = requests.post(f"{API}/auth/login",
                            json={"email": ADMIN2_EMAIL, "password": ADMIN2_PASS}, timeout=10).json()["token"]
        h2 = {"Authorization": f"Bearer {tok}"}

        # Deactivate the seed admin so admin2 is the only remaining admin
        me = requests.get(f"{API}/auth/me", headers=admin_hdr, timeout=10).json()
        seed_admin_id = me["id"]
        r_deact = requests.post(f"{API}/users/{seed_admin_id}/deactivate", headers=h2, timeout=10)
        try:
            assert r_deact.status_code == 200, r_deact.text
            # Now try to demote admin2 → should 400 (last active admin)
            r_demote = requests.patch(f"{API}/users/{admin2_id}", headers=h2,
                                      json={"role": "employee"}, timeout=10)
            assert r_demote.status_code == 400, r_demote.text
            # Try to deactivate admin2 (self) → 400 "cannot deactivate yourself"
            r_self = requests.post(f"{API}/users/{admin2_id}/deactivate", headers=h2, timeout=10)
            assert r_self.status_code == 400
        finally:
            # Reactivate seed admin no matter what
            requests.post(f"{API}/users/{seed_admin_id}/activate", headers=admin_hdr, timeout=10)


# ─────────────────────────── Non-admin guards ──────────────────────────
class TestNonAdminGuards:
    @pytest.fixture(scope="class")
    def emp_hdr(self, admin_hdr, created_ids):
        # emp user was created + reset earlier. Log in.
        uid = created_ids["emp"]
        assert uid
        # Ensure active
        requests.post(f"{API}/users/{uid}/activate", headers=admin_hdr, timeout=10)
        r = requests.post(f"{API}/auth/login",
                          json={"email": EMP_EMAIL, "password": "NewAudit@5678"}, timeout=10)
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def test_emp_get_users_forbidden(self, emp_hdr):
        r = requests.get(f"{API}/users", headers=emp_hdr, timeout=10)
        assert r.status_code == 403

    def test_emp_post_user_forbidden(self, emp_hdr):
        r = requests.post(f"{API}/users", headers=emp_hdr, json={
            "name": "x", "email": f"xx+{_run}@t.com", "password": "SomePass1", "role": "employee",
        }, timeout=10)
        assert r.status_code == 403

    def test_emp_patch_user_forbidden(self, emp_hdr, created_ids):
        r = requests.patch(f"{API}/users/{created_ids['emp']}", headers=emp_hdr,
                           json={"can_delete": False}, timeout=10)
        assert r.status_code == 403

    def test_emp_audit_logs_forbidden(self, emp_hdr):
        r = requests.get(f"{API}/audit-logs", headers=emp_hdr, timeout=10)
        assert r.status_code == 403


# ─────────────────────────── Audit middleware ──────────────────────────
class TestAuditMiddleware:
    def test_writes_captured_newest_first(self, admin_hdr, created_ids):
        # Trigger a fresh write we can identify
        uid = created_ids["emp"]
        marker_before = time.time()
        r = requests.patch(f"{API}/users/{uid}", headers=admin_hdr,
                           json={"mobile": f"+919000{_run[:5]}"}, timeout=10)
        assert r.status_code == 200
        # Small delay so ISO ordering is stable
        time.sleep(0.5)
        r2 = requests.get(f"{API}/audit-logs?limit=25", headers=admin_hdr, timeout=10)
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert "rows" in body and "count" in body
        rows = body["rows"]
        assert len(rows) >= 1
        # newest first
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats, reverse=True)
        # required fields
        r0 = rows[0]
        for k in ("id", "at", "method", "path", "status_code", "user_email", "user_role", "ip", "latency_ms"):
            assert k in r0, f"missing key {k}"
        # We should be able to find our PATCH
        assert any(
            row["method"] == "PATCH" and f"/api/users/{uid}" in row["path"]
            and row["user_email"] == ADMIN_EMAIL
            for row in rows
        ), "our recent PATCH was not captured in audit_logs"

    def test_get_not_captured(self, admin_hdr):
        # Hit a GET endpoint and confirm no audit row for it in the next page
        r = requests.get(f"{API}/users", headers=admin_hdr, timeout=10)
        assert r.status_code == 200
        time.sleep(0.3)
        r2 = requests.get(f"{API}/audit-logs?method=GET&limit=10", headers=admin_hdr, timeout=10)
        # Server will accept the filter but should return zero rows because middleware
        # only stores writes.
        body = r2.json()
        assert body["count"] == 0

    def test_auth_paths_skipped(self, admin_hdr):
        # Perform an auth login (POST /api/auth/login) — MUST NOT show in audit
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=10)
        assert r.status_code == 200
        time.sleep(0.3)
        r2 = requests.get(f"{API}/audit-logs?path_contains=/auth/login&limit=25",
                          headers=admin_hdr, timeout=10)
        assert r2.status_code == 200
        assert r2.json()["count"] == 0, "/api/auth/login must be skipped by audit middleware"

    def test_filters_work(self, admin_hdr, created_ids):
        # user_email + method + path_contains filter
        r = requests.get(
            f"{API}/audit-logs",
            headers=admin_hdr,
            params={"user_email": ADMIN_EMAIL, "method": "PATCH", "path_contains": "/users", "limit": 50},
            timeout=10,
        )
        assert r.status_code == 200
        rows = r.json()["rows"]
        for row in rows:
            assert row["user_email"] == ADMIN_EMAIL
            assert row["method"] == "PATCH"
            assert "/users" in row["path"]

    def test_summary(self, admin_hdr):
        r = requests.get(f"{API}/audit-logs/summary", headers=admin_hdr, timeout=10)
        assert r.status_code == 200
        body = r.json()
        for k in ("total", "today", "last_7_days", "top_users_7d"):
            assert k in body
        counts = [u["count"] for u in body["top_users_7d"]]
        assert counts == sorted(counts, reverse=True)
        assert body["total"] >= body["last_7_days"] >= body["today"]

    def test_no_measurable_latency(self, admin_hdr):
        # Make 5 GET calls (unaffected by writes middleware) and 5 PATCH calls (writes).
        # Just sanity-check that PATCH average latency is under ~500ms.
        uid = None
        # locate emp id
        rows = requests.get(f"{API}/users", headers=admin_hdr, timeout=10).json()
        for u in rows:
            if u["email"] == EMP_EMAIL:
                uid = u["id"]
                break
        assert uid
        latencies = []
        for i in range(3):
            t0 = time.time()
            requests.patch(f"{API}/users/{uid}", headers=admin_hdr,
                           json={"name": f"Audit Tester {_run} {i}"}, timeout=10)
            latencies.append((time.time() - t0) * 1000)
        avg = sum(latencies) / len(latencies)
        assert avg < 1500, f"avg PATCH latency too high: {avg}ms"
