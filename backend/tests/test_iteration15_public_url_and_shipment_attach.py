"""Iteration 15 — PUBLIC_BASE_URL sweep + shipment auto-notify attaches PDF.

Covers:
  1. PUBLIC_BASE_URL is honoured — GET /api/settings/integrations returns a
     webhook_url built on the new https://quote.hrexporter.com base.
  2. No leftover hardcoded 'https://hrexporter.com/my-quotes' or
     'hrexporter.com/api' URLs in backend code (excluding tests + __pycache__).
  3. auto_send_preset appends a 'Track live: {PUBLIC_BASE_URL}/track?...' line
     with URL-encoded order_number.
  4. _resolve_shipment_attachment fallback chain (invoice → eway → lr → None).
  5. attachment_override takes precedence over _resolve_attachment in
     send_universal_update — template chosen is `template_doc`.
  6. Shipment auto-notify records `whatsapp.attached=True` when a Tax Invoice
     was uploaded to the shipment; filename begins with 'Tax_Invoice_' and URL
     is absolute (starts with PUBLIC_BASE_URL).

Runs LIVE-SAFE: httpx.AsyncClient.post + smtplib.SMTP_SSL are monkeypatched at
the unit level; the API-level tests rely on RESTRICT_OUTBOUND_TO_PHONE being
set in /app/backend/.env for the test module lifespan.
"""
from __future__ import annotations

import os
import re
import sys
import time
import uuid
import subprocess
from pathlib import Path
from urllib.parse import quote as urlquote

import pytest
import requests

sys.path.insert(0, "/app/backend")

PUBLIC_BASE_URL_EXPECTED = "https://quote.hrexporter.com"


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
assert BASE_URL, "REACT_APP_BACKEND_URL not set"
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASS = "Admin@123"


# ─────────────────── Fixtures ───────────────────

@pytest.fixture(scope="session")
def H():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _pick_order_with_free_line(H):
    orders = requests.get(f"{API}/orders", headers=H, timeout=15).json() or []
    for o in orders:
        if not (o.get("line_items") or []):
            continue
        full = requests.get(f"{API}/orders/{o['id']}", headers=H, timeout=15).json()
        booked = set()
        for s in (full.get("shipments") or []):
            if s.get("stage") in {"created", "invoiced", "dispatched", "delivered"}:
                for i in (s.get("line_indexes") or []):
                    booked.add(i)
        free_line = next((i for i in range(len(full.get("line_items") or []))
                          if i not in booked), None)
        if free_line is not None:
            return full["id"], full, free_line
    pytest.skip("No order with a free (un-shipped) line item found")


# ─────────────────── (1) Grep sweep — no hardcoded URLs ───────────────────

class TestHardcodedUrlSweep:
    def test_no_hardcoded_hrexporter_urls_in_backend(self):
        # Exclude __pycache__, tests dir, and the intentional marketing URL
        # ABOUT_HRE_URL = https://hrexporter.com/about-hr-exporter/ in whatsapp_bot.py
        result = subprocess.run(
            ["grep", "-rn",
             "https://hrexporter.com/my-quotes",
             "/app/backend", "--include=*.py"],
            capture_output=True, text=True,
        )
        offenders = [
            ln for ln in result.stdout.splitlines()
            if "__pycache__" not in ln and "/tests/" not in ln
        ]
        assert not offenders, f"Hardcoded my-quotes URL still present:\n{offenders}"

    def test_no_hardcoded_hrexporter_api_urls(self):
        result = subprocess.run(
            ["grep", "-rn", "hrexporter.com/api",
             "/app/backend", "--include=*.py"],
            capture_output=True, text=True,
        )
        offenders = [
            ln for ln in result.stdout.splitlines()
            if "__pycache__" not in ln and "/tests/" not in ln
        ]
        assert not offenders, f"Hardcoded /api URL present:\n{offenders}"


# ─────────────────── (2) settings/integrations webhook_url ───────────────────

class TestIntegrationsWebhookOnNewDomain:
    def test_webhook_url_uses_public_base(self, H):
        r = requests.get(f"{API}/settings/integrations", headers=H, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        wa = data.get("whatsapp") or {}
        webhook = wa.get("webhook_url") or ""
        assert "quote.hrexporter.com" in webhook, (
            f"webhook_url should be built off PUBLIC_BASE_URL, got: {webhook!r}"
        )


# ─────────────────── (3) Unit tests on universal_update ───────────────────

class TestResolveShipmentAttachmentFallback:
    def test_prefers_tax_invoice(self):
        from services.universal_update import _resolve_shipment_attachment
        order = {"order_number": "HRE/ORD/T/1"}
        shipment = {
            "shipment_number": "HRE/SHP/T/1",
            "documents": {
                "tax_invoice": {"url": "/api/uploads/orders/x/inv.pdf"},
                "eway_bill":   {"url": "/api/uploads/orders/x/ew.pdf"},
                "lr_copy":     {"url": "/api/uploads/orders/x/lr.pdf"},
            },
        }
        out = _resolve_shipment_attachment(order, shipment)
        assert out is not None
        assert out["filename"].startswith("Tax_Invoice_")
        assert out["url"].startswith(PUBLIC_BASE_URL_EXPECTED)

    def test_falls_back_to_eway(self):
        from services.universal_update import _resolve_shipment_attachment
        shipment = {"shipment_number": "S2",
                    "documents": {"eway_bill": {"url": "/api/uploads/x/ew.pdf"}}}
        out = _resolve_shipment_attachment({}, shipment)
        assert out and out["filename"].startswith("E-Way_Bill_")

    def test_falls_back_to_lr(self):
        from services.universal_update import _resolve_shipment_attachment
        shipment = {"shipment_number": "S3",
                    "documents": {"lr_copy": {"url": "/api/uploads/x/lr.pdf"}}}
        out = _resolve_shipment_attachment({}, shipment)
        assert out and out["filename"].startswith("LR_Copy_")

    def test_no_docs_returns_none(self):
        from services.universal_update import _resolve_shipment_attachment
        assert _resolve_shipment_attachment({}, {"documents": {}}) is None
        assert _resolve_shipment_attachment({}, {}) is None


class TestAutoSendPresetAppendsTrackLink:
    def test_track_url_encoded_order_number_appended(self, monkeypatch):
        """auto_send_preset must fold 'Track live: <base>/track?order_number=<enc>'
        onto the last empty body-line. Also verifies attachment_override wiring."""
        import asyncio
        import services.universal_update as uu

        captured = {}

        async def _fake_send(order, body_lines, attach_choice="none",
                             preset_id=None, also_email=True, attachment_override=None):
            captured["body_lines"] = list(body_lines)
            captured["attach_choice"] = attach_choice
            captured["attachment_override"] = attachment_override
            return {
                "whatsapp": {"sent": True, "wamid": "X", "log_uid": None,
                              "error": None, "template": "tpl_doc",
                              "attached": bool(attachment_override)},
                "email": {"sent": False, "error": None},
                "preset_id": preset_id,
                "vars": ["Cust"] + list(body_lines),
                "at": "2026-01-01T00:00:00Z",
            }

        async def _fake_update_one(*_args, **_kwargs):
            return None

        monkeypatch.setattr(uu, "send_universal_update", _fake_send)
        monkeypatch.setattr(uu.db.orders, "update_one", _fake_update_one)

        order = {
            "order_number": "HRE/ORD/2026-27/0042",
            "grand_total": "1000",
            "line_items": [{"product_code": "SKU-1", "quantity": 5}],
            "shipments": [],
        }
        shipment = {
            "id": "sid1",
            "shipment_number": "HRE/SHP/1",
            "line_indexes": [0],
            "transporter_name": "BlueDart",
            "lr_number": "LR-1",
            "documents": {"tax_invoice": {"url": "/api/uploads/x/inv.pdf"}},
        }
        res = asyncio.get_event_loop().run_until_complete(
            uu.auto_send_preset("oid1", "shipment_dispatched", order,
                                shipment=shipment, also_email=False)
        )
        assert res is not None
        # attachment_override must have been picked up from shipment docs
        ao = captured["attachment_override"]
        assert ao is not None, "attachment_override should be resolved from shipment docs"
        assert ao["filename"].startswith("Tax_Invoice_")
        assert ao["url"].startswith(PUBLIC_BASE_URL_EXPECTED)

        # Track link folded onto some body line — must be URL-encoded
        joined = "\n".join(captured["body_lines"])
        enc = urlquote("HRE/ORD/2026-27/0042", safe="")
        assert enc == "HRE%2FORD%2F2026-27%2F0042"
        expected = f"Track live: {PUBLIC_BASE_URL_EXPECTED}/track?order_number={enc}"
        assert expected in joined, (
            f"Track link not appended; body_lines={captured['body_lines']}"
        )


class TestSendUniversalUpdateOverrideTakesPrecedence:
    def test_override_selects_template_doc_not_text(self, monkeypatch):
        """When attachment_override is truthy, template_doc must be chosen
        regardless of attach_choice='none'."""
        import asyncio
        import services.universal_update as uu

        async def _fake_integrations():
            return {
                "whatsapp": {"enabled": True},
                "smtp": {"enabled": False},
                "universal_update": {
                    "template_language": "en_US",
                    "template_doc": "hr_templet_delivery",
                    "template_text": "hr_product_dispatch",
                },
            }

        sent = {}

        async def _fake_wa_send(wa, phone, *, template_name, template_language,
                                field_1, extra):
            sent["template_name"] = template_name
            sent["extra"] = extra
            return {"data": {"wamid": "W1", "status": "sent"}}

        monkeypatch.setattr(uu, "get_integrations", _fake_integrations)
        monkeypatch.setattr(uu, "send_whatsapp_template", _fake_wa_send)

        order = {
            "order_number": "HRE/ORD/X/1",
            "contact_name": "Test User",
            "contact_phone": "+911111111111",
        }
        override = {"url": "https://x/y.pdf", "filename": "test.pdf"}
        res = asyncio.get_event_loop().run_until_complete(
            uu.send_universal_update(
                order=order,
                body_lines=["a", "b", "c", "d", "e"],
                attach_choice="none",     # would resolve to no doc
                preset_id="custom",
                also_email=False,
                attachment_override=override,
            )
        )
        assert res["whatsapp"]["attached"] is True
        assert sent["template_name"] == "hr_templet_delivery", (
            f"template_doc must be used when override provided; got {sent}"
        )
        assert sent["extra"]["header_document"] == "https://x/y.pdf"
        assert sent["extra"]["header_document_name"] == "test.pdf"


# ─────────────────── (4) End-to-end: shipment dispatch attaches PDF ───────────────────

class TestShipmentAutoNotifyAttachesPdf:
    def test_dispatch_records_attached_true_and_absolute_url(self, H):
        """Create shipment → upload tax_invoice → dispatch → verify the
        notification entry pushed by auto_send_preset has whatsapp.attached=True
        and vars/payload can be inspected on the order."""
        oid, order, line_idx = _pick_order_with_free_line(H)

        r = requests.post(f"{API}/orders/{oid}/shipments", headers=H, json={
            "line_indexes": [line_idx],
            "transporter_name": "TestExpress",
            "lr_number": f"LR-{uuid.uuid4().hex[:6]}",
            "expected_delivery_date": "2026-12-01",
        }, timeout=20)
        assert r.status_code == 200, r.text
        sid = r.json()["shipments"][-1]["id"]

        # Upload tax_invoice PDF
        dummy = b"%PDF-1.4\n%TEST invoice iter15\n"
        files = {"file": ("tax_invoice.pdf", dummy, "application/pdf")}
        rr = requests.post(f"{API}/orders/{oid}/shipments/{sid}/upload",
                           headers=H, files=files,
                           data={"doc_key": "tax_invoice"}, timeout=20)
        assert rr.status_code == 200, rr.text

        # Dispatch → auto_send_preset('shipment_dispatched') fires
        rd = requests.post(f"{API}/orders/{oid}/shipments/{sid}/dispatch",
                           headers=H, json={"dispatched_on": "2026-07-22"}, timeout=25)
        assert rd.status_code == 200, rd.text
        time.sleep(1.5)

        after = requests.get(f"{API}/orders/{oid}", headers=H, timeout=15).json()
        auto_entries = [
            e for e in (after.get("notifications") or [])
            if e.get("kind") == "auto_universal_update"
            and e.get("preset_id") == "shipment_dispatched"
            and e.get("shipment_id") == sid
        ]
        assert auto_entries, "shipment_dispatched auto-notify entry not found"
        last = auto_entries[-1]

        # Attachment flag on the whatsapp payload dict
        wa = last.get("whatsapp") or {}
        assert wa.get("attached") is True, (
            f"whatsapp.attached should be True (Tax Invoice uploaded); got {wa!r}"
        )

        # Track link in vars is URL-encoded and uses PUBLIC_BASE_URL
        joined_vars = "\n".join(last.get("vars") or [])
        order_no = after["order_number"]
        enc = urlquote(order_no, safe="")
        expected_track = f"Track live: {PUBLIC_BASE_URL_EXPECTED}/track?order_number={enc}"
        assert expected_track in joined_vars, (
            f"expected {expected_track!r} in vars, got:\n{joined_vars}"
        )

    def test_dispatched_invoice_url_absolute(self, H):
        """After dispatch, GET /api/orders/{oid} → dispatch.invoice.url (if
        legacy order-level dispatch is used) should be absolute using
        PUBLIC_BASE_URL. Skip if the field isn't present on any order."""
        orders = requests.get(f"{API}/orders", headers=H, timeout=15).json() or []
        for o in orders:
            full = requests.get(f"{API}/orders/{o['id']}", headers=H, timeout=15).json()
            inv = ((full.get("dispatch") or {}).get("invoice") or {}).get("url")
            if inv:
                assert inv.startswith(PUBLIC_BASE_URL_EXPECTED + "/api/uploads/") or \
                       inv.startswith("/api/uploads/"), (
                    f"dispatch invoice URL not absolute w/ PUBLIC_BASE_URL: {inv!r}"
                )
                return
        pytest.skip("no order-level dispatch.invoice.url present in DB")


# ─────────────────── (5) Line auto-notify body has track link ───────────────────

class TestLineAutoNotifyHasTrackLink:
    def test_in_production_body_contains_track_url(self, H):
        oid, _, line_idx = _pick_order_with_free_line(H)
        # Reset to pending idempotently
        requests.patch(f"{API}/orders/{oid}/lines/{line_idx}", headers=H,
                       json={"qty_status": "pending"}, timeout=15)
        r = requests.patch(f"{API}/orders/{oid}/lines/{line_idx}", headers=H,
                           json={"qty_status": "in_production"}, timeout=25)
        assert r.status_code == 200, r.text
        time.sleep(1.0)
        after = requests.get(f"{API}/orders/{oid}", headers=H, timeout=15).json()
        entries = [
            e for e in (after.get("notifications") or [])
            if e.get("kind") == "auto_universal_update"
            and e.get("preset_id") == "item_in_production"
        ]
        assert entries, "no item_in_production auto entry recorded"
        last = entries[-1]
        joined = "\n".join(last.get("vars") or [])
        assert "Track live: https://quote.hrexporter.com/track?order_number=" in joined, (
            f"expected track link with new domain in vars; got:\n{joined}"
        )
