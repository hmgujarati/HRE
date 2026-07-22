"""Iteration 14 — Auto-notify on state transitions.

Covers:
  - PATCH /api/orders/{oid}/lines/{i} qty_status → in_production auto-fires
    universal-update preset `item_in_production` (kind='auto_universal_update').
  - PATCH qty_status → ready fires `item_ready`.
  - PATCH with the same qty_status is a no-op (no new auto entry appended).
  - PATCH expected_dispatch_date change fires `schedule_revision`.
  - Shipment dispatch fires `shipment_dispatched` preset.
  - Shipment deliver fires `shipment_delivered` preset.
  - resolve_preset_tokens unit test (line ctx, shipment ctx, no ctx fallback,
    missing tokens → '—').
  - Manual POST /orders/{oid}/notify still works (kind='universal_update').

RESTRICT_OUTBOUND_TO_PHONE=+918200663263 and RESTRICT_OUTBOUND_TO_EMAIL are set
in backend/.env for the duration of this suite so no real customer numbers are
hit — every WhatsApp send is rerouted to Harsh's QA phone.
"""
import os
import sys
import time
import uuid
import pytest
import requests

# Allow importing backend modules for the unit test on resolve_preset_tokens
sys.path.insert(0, "/app/backend")


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


def _pick_order_with_line(H, need_line_idx: int = 0):
    """Return (oid, order) for an order with at least 1 line item."""
    orders = requests.get(f"{API}/orders", headers=H, timeout=15).json() or []
    for o in orders:
        if (o.get("line_items") or []):
            full = requests.get(f"{API}/orders/{o['id']}", headers=H, timeout=15).json()
            if len(full.get("line_items") or []) > need_line_idx:
                return full["id"], full
    pytest.skip("No order with line items found in DB")


def _get_order(H, oid):
    r = requests.get(f"{API}/orders/{oid}", headers=H, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def _reset_line_to_pending(H, oid, line_idx):
    """Force line back to 'pending' (idempotent — auto-notify doesn't fire on
    transitions *to* pending since it's not in the preset trigger map)."""
    r = requests.patch(
        f"{API}/orders/{oid}/lines/{line_idx}",
        headers=H, json={"qty_status": "pending"}, timeout=15,
    )
    assert r.status_code == 200, r.text


def _count_auto(order, preset_id=None, trigger_prefix=None):
    n = 0
    for e in (order.get("notifications") or []):
        if e.get("kind") != "auto_universal_update":
            continue
        if preset_id and e.get("preset_id") != preset_id:
            continue
        if trigger_prefix and not (e.get("trigger") or "").startswith(trigger_prefix):
            continue
        n += 1
    return n


# ─────────────────── Unit test: resolve_preset_tokens ───────────────────

class TestResolvePresetTokens:
    """Direct import — pure function, no server involvement."""

    def test_line_context_substitutes_product_and_qty(self):
        from services.universal_update import resolve_preset_tokens
        order = {"order_number": "HRE/ORD/TEST/0001", "grand_total": "12345.00",
                 "line_items": []}
        line = {"product_code": "SKU-X1", "quantity": 42,
                "expected_dispatch_date": "2026-09-01"}
        lines = resolve_preset_tokens("item_in_production", order, line=line)
        assert len(lines) == 5
        joined = "\n".join(lines)
        assert "HRE/ORD/TEST/0001" in joined
        assert "SKU-X1" in joined
        assert "42" in joined
        assert "2026-09-01" in joined

    def test_shipment_context_aggregates_product_codes(self):
        from services.universal_update import resolve_preset_tokens
        order = {
            "order_number": "HRE/ORD/TEST/0002",
            "line_items": [
                {"product_code": "A1", "quantity": 5},
                {"product_code": "B2", "quantity": 3},
                {"product_code": "C3", "quantity": 7},
            ],
        }
        shipment = {"line_indexes": [0, 2],
                    "transporter_name": "BlueDart",
                    "lr_number": "LR-9988"}
        lines = resolve_preset_tokens("shipment_dispatched", order, shipment=shipment)
        joined = "\n".join(lines)
        assert "A1" in joined and "C3" in joined
        assert "B2" not in joined  # index 1 not in shipment
        assert "BlueDart" in joined
        assert "LR-9988" in joined

    def test_no_context_falls_back_to_first_line(self):
        from services.universal_update import resolve_preset_tokens
        order = {
            "order_number": "HRE/ORD/TEST/0003",
            "line_items": [{"product_code": "FIRST", "quantity": 1}],
        }
        lines = resolve_preset_tokens("item_ready", order)
        joined = "\n".join(lines)
        assert "FIRST" in joined

    def test_missing_tokens_fallback_dash(self):
        from services.universal_update import resolve_preset_tokens
        order = {"order_number": "HRE/ORD/TEST/0004"}  # no line_items, no ETA
        lines = resolve_preset_tokens("item_in_production", order)
        # ETA and product_code should default to '—'
        assert any("—" in ln for ln in lines)

    def test_unknown_preset_returns_empty(self):
        from services.universal_update import resolve_preset_tokens
        order = {"order_number": "X"}
        lines = resolve_preset_tokens("no_such_preset", order)
        assert lines == ["", "", "", "", ""]


# ─────────────────── Auto-notify on line qty_status ───────────────────

class TestLineStatusAutoNotify:

    def test_in_production_appends_auto_entry(self, H):
        oid, _ = _pick_order_with_line(H)
        _reset_line_to_pending(H, oid, 0)
        before = _get_order(H, oid)
        n_before = _count_auto(before, preset_id="item_in_production",
                                trigger_prefix="line_status:in_production")

        r = requests.patch(f"{API}/orders/{oid}/lines/0",
                           headers=H, json={"qty_status": "in_production"}, timeout=20)
        assert r.status_code == 200, r.text
        time.sleep(1.0)  # WhatsApp send happens in-request, but be safe
        after = _get_order(H, oid)
        n_after = _count_auto(after, preset_id="item_in_production",
                               trigger_prefix="line_status:in_production")
        assert n_after == n_before + 1, (
            f"expected one new auto_universal_update entry (item_in_production); "
            f"before={n_before} after={n_after}"
        )
        # Last entry sanity
        last = [e for e in (after.get("notifications") or [])
                if e.get("kind") == "auto_universal_update"][-1]
        assert last["preset_id"] == "item_in_production"
        assert last["trigger"] == "line_status:in_production"
        assert last["by"] == "system"

    def test_ready_appends_auto_entry(self, H):
        oid, _ = _pick_order_with_line(H)
        _reset_line_to_pending(H, oid, 0)
        # Move pending → ready (skips in_production but that's OK — the endpoint
        # allows any allowed status; auto-notify only fires on ready target)
        before = _get_order(H, oid)
        n_before = _count_auto(before, preset_id="item_ready")
        r = requests.patch(f"{API}/orders/{oid}/lines/0",
                           headers=H, json={"qty_status": "ready"}, timeout=20)
        assert r.status_code == 200, r.text
        time.sleep(1.0)
        after = _get_order(H, oid)
        assert _count_auto(after, preset_id="item_ready") == n_before + 1

    def test_idempotent_same_status_no_new_entry(self, H):
        oid, _ = _pick_order_with_line(H)
        _reset_line_to_pending(H, oid, 0)
        # Move to in_production once
        requests.patch(f"{API}/orders/{oid}/lines/0", headers=H,
                       json={"qty_status": "in_production"}, timeout=20)
        time.sleep(1.0)
        before = _get_order(H, oid)
        n_before = _count_auto(before, preset_id="item_in_production")
        # PATCH same status — no changes list → no auto entry
        r = requests.patch(f"{API}/orders/{oid}/lines/0", headers=H,
                           json={"qty_status": "in_production"}, timeout=20)
        assert r.status_code == 200, r.text
        time.sleep(1.0)
        after = _get_order(H, oid)
        assert _count_auto(after, preset_id="item_in_production") == n_before, (
            "idempotent PATCH must NOT append an auto_universal_update entry"
        )


# ─────────────────── Auto-notify on ETA change ───────────────────

class TestLineEtaAutoNotify:
    def test_eta_change_fires_schedule_revision(self, H):
        oid, _ = _pick_order_with_line(H)
        # Set to a known date first
        requests.patch(f"{API}/orders/{oid}/lines/0", headers=H,
                       json={"expected_dispatch_date": "2026-06-01"}, timeout=15)
        time.sleep(0.5)
        before = _get_order(H, oid)
        n_before = _count_auto(before, preset_id="schedule_revision",
                                trigger_prefix="line_eta_change")
        # Change it — must fire schedule_revision
        new_date = "2026-08-15"
        r = requests.patch(f"{API}/orders/{oid}/lines/0", headers=H,
                           json={"expected_dispatch_date": new_date}, timeout=20)
        assert r.status_code == 200, r.text
        time.sleep(1.0)
        after = _get_order(H, oid)
        n_after = _count_auto(after, preset_id="schedule_revision",
                                trigger_prefix="line_eta_change")
        assert n_after == n_before + 1


# ─────────────────── Shipment dispatch / deliver auto-notify ───────────────────

@pytest.fixture(scope="module")
def ship_ctx(H):
    """Create a fresh shipment for a picked order + line, upload required docs.
    Returns (oid, sid). Guarantees line 0 is picked and docs are attached."""
    oid, order = _pick_order_with_line(H)
    # We need to pick a line NOT already in another non-deleted shipment.
    booked = set()
    for s in (order.get("shipments") or []):
        if s.get("stage") in {"created", "invoiced", "dispatched", "delivered"}:
            for i in (s.get("line_indexes") or []):
                booked.add(i)
    line_idx = next((i for i in range(len(order.get("line_items") or []))
                     if i not in booked), None)
    if line_idx is None:
        pytest.skip("all lines already in shipments — cannot create a fresh one")

    r = requests.post(f"{API}/orders/{oid}/shipments", headers=H, json={
        "line_indexes": [line_idx],
        "transporter_name": "TestExpress",
        "lr_number": f"LR-{uuid.uuid4().hex[:6]}",
        "expected_delivery_date": "2026-12-01",
    }, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["shipments"][-1]["id"]

    # Upload tax_invoice + eway_bill (tiny in-memory PDFs — content doesn't matter)
    dummy = b"%PDF-1.4\n%TEST\n"
    for key in ("tax_invoice", "eway_bill"):
        files = {"file": (f"{key}.pdf", dummy, "application/pdf")}
        data = {"doc_key": key}
        rr = requests.post(f"{API}/orders/{oid}/shipments/{sid}/upload",
                           headers=H, files=files, data=data, timeout=20)
        assert rr.status_code == 200, rr.text
    return oid, sid


class TestShipmentAutoNotify:

    def test_dispatch_fires_shipment_dispatched(self, H, ship_ctx):
        oid, sid = ship_ctx
        before = _get_order(H, oid)
        n_before = _count_auto(before, preset_id="shipment_dispatched",
                                trigger_prefix=f"shipment_dispatched:{sid}")
        r = requests.post(f"{API}/orders/{oid}/shipments/{sid}/dispatch",
                          headers=H, json={"dispatched_on": "2026-07-20"}, timeout=25)
        assert r.status_code == 200, r.text
        time.sleep(1.5)
        after = _get_order(H, oid)
        n_after = _count_auto(after, preset_id="shipment_dispatched",
                                trigger_prefix=f"shipment_dispatched:{sid}")
        assert n_after == n_before + 1
        # Check the vars mention transporter + LR
        last = [e for e in (after.get("notifications") or [])
                if e.get("kind") == "auto_universal_update"
                and e.get("preset_id") == "shipment_dispatched"][-1]
        joined_vars = "\n".join(last.get("vars") or [])
        assert "TestExpress" in joined_vars or "TestExpress" in (last.get("trigger") or "")

    def test_deliver_fires_shipment_delivered(self, H, ship_ctx):
        oid, sid = ship_ctx
        before = _get_order(H, oid)
        n_before = _count_auto(before, preset_id="shipment_delivered",
                                trigger_prefix=f"shipment_delivered:{sid}")
        r = requests.post(f"{API}/orders/{oid}/shipments/{sid}/deliver",
                          headers=H, json={"delivered_on": "2026-07-25"}, timeout=25)
        assert r.status_code == 200, r.text
        time.sleep(1.5)
        after = _get_order(H, oid)
        n_after = _count_auto(after, preset_id="shipment_delivered",
                                trigger_prefix=f"shipment_delivered:{sid}")
        assert n_after == n_before + 1


# ─────────────────── Manual notify no regression ───────────────────

class TestManualNotifyRegression:
    def test_manual_notify_still_pushes_universal_update_kind(self, H):
        oid, _ = _pick_order_with_line(H)
        r = requests.post(f"{API}/orders/{oid}/notify", headers=H, json={
            "vars": ["Line 1", "Line 2", "Line 3", "Line 4", "Line 5"],
            "attach": "none",
            "preset_id": "custom",
            "also_email": False,
        }, timeout=25)
        assert r.status_code == 200, r.text
        time.sleep(1.0)
        after = _get_order(H, oid)
        # Last notif of kind universal_update (NOT auto)
        entries = [e for e in (after.get("notifications") or [])
                   if e.get("kind") == "universal_update"]
        assert entries, "manual notify must append a universal_update entry"
        last = entries[-1]
        assert last["kind"] == "universal_update"
        assert last.get("by") == ADMIN_EMAIL


# ─────────────────── Best-effort never raises (indirect) ───────────────────

class TestBestEffortNeverRaises:
    """Because auto_send_preset swallows all exceptions, PATCH/dispatch/deliver
    must return 200 even when the underlying send fails. We can't monkeypatch
    the running server, so we assert the observed behaviour: a state transition
    that is guaranteed to have some send-side failure (e.g., contact_phone
    reroute + template mismatch, or SMTP disabled) still returns 200 and still
    commits the DB change. All the tests above already exercise this — this
    test just double-checks the response contract on a fresh transition."""

    def test_patch_returns_200_and_persists(self, H):
        oid, _ = _pick_order_with_line(H)
        _reset_line_to_pending(H, oid, 0)
        r = requests.patch(f"{API}/orders/{oid}/lines/0", headers=H,
                           json={"qty_status": "in_production"}, timeout=25)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["line_items"][0]["qty_status"] == "in_production"
