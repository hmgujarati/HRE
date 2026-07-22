"""Iteration 10 — verify TEST-MODE outbound phone/email override redirects ALL
WhatsApp + SMTP outbound traffic to the env-configured test recipient.

Mocks httpx.AsyncClient.post so we never actually call BizChat, and asserts
that the JSON payload `phone_number` (after normalization) equals the
RESTRICT_OUTBOUND_TO_PHONE env value regardless of the original target.
"""
from __future__ import annotations
import os
import sys
import asyncio
import pytest
import requests

# Ensure backend root is importable for unit-level patches.
sys.path.insert(0, "/app/backend")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    "https://quotation-system-20.preview.emergentagent.com"

ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"
TEST_PHONE = "+918200663263"


# ───────── API-level: test_mode object is surfaced ─────────

@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_settings_integrations_exposes_test_mode(admin_token):
    r = requests.get(f"{BASE_URL}/api/settings/integrations",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert "test_mode" in data, "test_mode object missing from response"
    tm = data["test_mode"]
    # Value is env-driven — accept either the test override or empty (live mode).
    assert tm.get("restrict_outbound_phone") in (TEST_PHONE, "")
    assert "restrict_outbound_email" in tm  # may be empty string


# ───────── Unit-level: _redirect_phone / _redirect_email helpers ─────────

def test_redirect_phone_overrides_when_env_set(monkeypatch):
    monkeypatch.setenv("RESTRICT_OUTBOUND_TO_PHONE", TEST_PHONE)
    from services import integrations as integ
    assert integ._redirect_phone("9999999999") == TEST_PHONE
    assert integ._redirect_phone("+911234567890") == TEST_PHONE


def test_redirect_phone_noop_when_env_unset(monkeypatch):
    monkeypatch.delenv("RESTRICT_OUTBOUND_TO_PHONE", raising=False)
    from services import integrations as integ
    assert integ._redirect_phone("9999999999") == "9999999999"


def test_redirect_email_noop_when_env_unset(monkeypatch):
    monkeypatch.delenv("RESTRICT_OUTBOUND_TO_EMAIL", raising=False)
    from services import integrations as integ
    assert integ._redirect_email("foo@bar.com") == "foo@bar.com"


def test_redirect_email_overrides_when_env_set(monkeypatch):
    monkeypatch.setenv("RESTRICT_OUTBOUND_TO_EMAIL", "qa@hre.test")
    from services import integrations as integ
    assert integ._redirect_email("real@customer.com") == "qa@hre.test"


# ───────── send_whatsapp_template uses override (no real HTTP) ─────────

class _FakeResp:
    status_code = 200
    headers = {"content-type": "application/json"}
    def json(self): return {"result": "success", "data": {"wamid": "FAKE-WAMID-1"}}
    @property
    def text(self): return "ok"


class _FakeClient:
    captured = {}
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, params=None, json=None):
        _FakeClient.captured = {"url": url, "params": params, "json": json}
        return _FakeResp()
    async def get(self, url, params=None):
        _FakeClient.captured = {"url": url, "params": params}
        return _FakeResp()


def test_send_whatsapp_template_redirects_to_test_phone(monkeypatch):
    monkeypatch.setenv("RESTRICT_OUTBOUND_TO_PHONE", TEST_PHONE)
    from services import integrations as integ
    monkeypatch.setattr(integ.httpx, "AsyncClient", _FakeClient)
    wa = {
        "enabled": True, "vendor_uid": "v1", "token": "t1",
        "api_base_url": "https://bizchatapi.in/api",
        "from_phone_number_id": "p1",
        "default_country_code": "91",
    }
    asyncio.run(integ.send_whatsapp_template(
        wa, "9000000000", template_name="otp_v1",
        template_language="en", field_1="999111", button_0="999111"))
    captured = _FakeClient.captured["json"]
    # Payload phone must be the normalized test phone (digits only, +91 prefix kept as 91)
    assert captured["phone_number"].endswith("8200663263"), captured
    # And must NOT contain the original target
    assert "9000000000" not in captured["phone_number"], captured


def test_send_whatsapp_text_redirects_to_test_phone(monkeypatch):
    monkeypatch.setenv("RESTRICT_OUTBOUND_TO_PHONE", TEST_PHONE)
    from services import integrations as integ
    monkeypatch.setattr(integ.httpx, "AsyncClient", _FakeClient)
    wa = {"enabled": True, "vendor_uid": "v1", "token": "t1",
          "api_base_url": "https://bizchatapi.in/api",
          "from_phone_number_id": "p1", "default_country_code": "91"}
    asyncio.run(integ.send_whatsapp_text(wa, "9000000000", "hello"))
    cap = _FakeClient.captured["json"]
    assert cap["phone_number"].endswith("8200663263")


def test_send_whatsapp_document_redirects_to_test_phone(monkeypatch):
    monkeypatch.setenv("RESTRICT_OUTBOUND_TO_PHONE", TEST_PHONE)
    from services import integrations as integ
    monkeypatch.setattr(integ.httpx, "AsyncClient", _FakeClient)
    wa = {"enabled": True, "vendor_uid": "v1", "token": "t1",
          "api_base_url": "https://bizchatapi.in/api",
          "from_phone_number_id": "p1", "default_country_code": "91"}
    asyncio.run(integ.send_whatsapp_document(
        wa, "9000000000", media_url="https://x.test/f.pdf",
        file_name="f.pdf", caption="hi"))
    cap = _FakeClient.captured["json"]
    assert cap["phone_number"].endswith("8200663263")


# ───────── whatsapp_bot._bizchat_post override ─────────

def test_bizchat_post_redirects_payload_phone(monkeypatch):
    monkeypatch.setenv("RESTRICT_OUTBOUND_TO_PHONE", TEST_PHONE)
    import whatsapp_bot as bot
    monkeypatch.setattr(bot.httpx, "AsyncClient", _FakeClient)
    wa = {"api_base_url": "https://bizchatapi.in/api",
          "vendor_uid": "v1", "token": "t1", "from_phone_number_id": "p1"}
    asyncio.run(bot._bizchat_post(
        wa, "send-message",
        {"phone_number": "9000000000", "message_body": "hi"}))
    cap = _FakeClient.captured["json"]
    assert cap["phone_number"] == TEST_PHONE, cap
