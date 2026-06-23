"""Iteration 11 — Password length guard tests (bcrypt 72-byte crash fix).

Covers:
- POST /api/auth/login with 200-char password → 422 (Pydantic max_length=128).
- POST /api/auth/login with 128-char (max allowed) wrong password → 401 (not 500).
- POST /api/auth/login with empty password → 422 (min_length=1).
- POST /api/auth/login with correct admin creds → 200 + token (no regression).
- POST /api/auth/change-password with 200-char new_password → 422.
- POST /api/auth/change-password with 5-char new_password → 422 (min_length=8).
- POST /api/auth/change-password rotate + login + rotate back → 200.
- Supervisor still reports backend RUNNING after the long-password barrage.
"""
import os
import subprocess

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fall back to local supervisor port for completeness
    BASE_URL = "http://localhost:8001"

ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    body = r.json()
    assert "token" in body and isinstance(body["token"], str) and body["token"]
    return body["token"]


# ----- /api/auth/login -----

def test_login_200char_password_returns_422():
    pw = "A" * 200
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": pw},
        timeout=15,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


def test_login_128char_password_returns_401():
    # 128 chars is allowed by Pydantic but it's the wrong password → 401, not 500.
    pw = "B" * 128
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": pw},
        timeout=15,
    )
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"
    body = r.json()
    assert "detail" in body
    assert "invalid" in body["detail"].lower()


def test_login_empty_password_returns_422():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ""},
        timeout=15,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


def test_login_correct_admin_credentials_returns_200(admin_token):
    # Implicitly tested via fixture, but assert again for clarity.
    assert admin_token


def test_long_password_does_not_crash_backend():
    # Hammer the endpoint with several oversize payloads — backend must stay up.
    for size in (200, 500, 1024, 5000):
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": "X" * size},
            timeout=15,
        )
        assert r.status_code == 422, f"size={size} expected 422, got {r.status_code}"
    # Now verify good login still works → backend healthy.
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200


def test_supervisor_backend_running():
    try:
        out = subprocess.check_output(
            ["sudo", "supervisorctl", "status", "backend"],
            stderr=subprocess.STDOUT, timeout=10,
        ).decode()
        assert "RUNNING" in out, f"backend not RUNNING: {out}"
    except FileNotFoundError:
        pytest.skip("supervisorctl unavailable")


# ----- /api/auth/change-password -----

def test_change_password_200char_new_returns_422(admin_token):
    r = requests.post(
        f"{BASE_URL}/api/auth/change-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"current_password": ADMIN_PASSWORD, "new_password": "Z" * 200},
        timeout=15,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


def test_change_password_5char_new_returns_422(admin_token):
    r = requests.post(
        f"{BASE_URL}/api/auth/change-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"current_password": ADMIN_PASSWORD, "new_password": "abcde"},
        timeout=15,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


def test_change_password_rotate_and_rotate_back(admin_token):
    """Rotate admin pw → login with new pw → rotate back → login with old pw.
    Critical: MUST end with Admin@123 so test_credentials.md stays valid.
    """
    new_pw = "NewPass@2026"

    # 1) Rotate to new pw
    r = requests.post(
        f"{BASE_URL}/api/auth/change-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"current_password": ADMIN_PASSWORD, "new_password": new_pw},
        timeout=15,
    )
    assert r.status_code == 200, f"rotate forward failed: {r.status_code} {r.text}"

    try:
        # 2) Login with new pw works
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": new_pw},
            timeout=15,
        )
        assert r.status_code == 200, f"login with new pw failed: {r.status_code} {r.text}"
        new_token = r.json()["token"]

        # 3) Login with OLD pw must now fail
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        assert r.status_code == 401

        # 4) Rotate back to Admin@123 using the new token
        r = requests.post(
            f"{BASE_URL}/api/auth/change-password",
            headers={"Authorization": f"Bearer {new_token}"},
            json={"current_password": new_pw, "new_password": ADMIN_PASSWORD},
            timeout=15,
        )
        assert r.status_code == 200, f"rotate back failed: {r.status_code} {r.text}"
    finally:
        # Safety net — if anything above blew up, force rotate back.
        check = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        if check.status_code != 200:
            # try the new pw and rotate back
            r2 = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": new_pw},
                timeout=15,
            )
            if r2.status_code == 200:
                tok = r2.json()["token"]
                requests.post(
                    f"{BASE_URL}/api/auth/change-password",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={"current_password": new_pw, "new_password": ADMIN_PASSWORD},
                    timeout=15,
                )

    # 5) Final assertion — Admin@123 must be the live pw
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, "CRITICAL: admin pw not restored to Admin@123"
