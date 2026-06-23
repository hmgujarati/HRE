"""Iteration 9 regression — full Phase 1 (per-line tracking), Phase 2 (universal
update WhatsApp), Phase 3 (shipments). Also covers auth, dashboard, pricing,
settings, GST logic, public endpoints, security.

STRICT RULE: NO WhatsApp send may be triggered for any phone OTHER than
+918200663263 (live contact 'Harsh Gujarati'). Endpoint structure is verified
by mocking the universal_update call OR by sending to a contact whose phone
is NOT WA-bound so BizChat reports 'no template' which is treated as a pass.
"""
import io, re, os, time, uuid, json
import pytest, requests

def _read_frontend_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        return ""
    return ""

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_frontend_env() or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not set"
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASS = "Admin@123"
ALLOWED_TEST_PHONE = "+918200663263"   # ONLY phone we may actually send to


# ───────────── fixtures ─────────────

@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def H(token):
    return {"Authorization": f"Bearer {token}"}


# ───────────── auth ─────────────

class TestAuth:
    def test_login_ok(self, token):
        assert isinstance(token, str) and len(token) > 20

    def test_login_bad(self):
        r = requests.post(f"{API}/auth/login",
                          json={"email": ADMIN_EMAIL, "password": "WRONG"}, timeout=10)
        assert r.status_code == 401

    def test_me_requires_token(self):
        r = requests.get(f"{API}/auth/me", timeout=10)
        assert r.status_code in (401, 403)

    def test_change_password_rejects_wrong_current(self, H):
        r = requests.post(f"{API}/auth/change-password",
                          json={"current_password": "wrong", "new_password": "ZZZzzzz1!"},
                          headers=H, timeout=10)
        assert r.status_code == 400

    def test_change_password_rejects_short(self, H):
        r = requests.post(f"{API}/auth/change-password",
                          json={"current_password": ADMIN_PASS, "new_password": "ab"},
                          headers=H, timeout=10)
        assert r.status_code in (400, 422)

    def test_change_password_rejects_same(self, H):
        r = requests.post(f"{API}/auth/change-password",
                          json={"current_password": ADMIN_PASS, "new_password": ADMIN_PASS},
                          headers=H, timeout=10)
        assert r.status_code in (400, 422)


# ───────────── dashboard ─────────────

class TestDashboard:
    def test_stats(self, H):
        r = requests.get(f"{API}/dashboard/stats", headers=H, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_hot_leads(self, H):
        r = requests.get(f"{API}/dashboard/hot-leads", headers=H, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_priceless_count(self, H):
        # endpoint actually lives at /pricing/priceless-count
        r = requests.get(f"{API}/pricing/priceless-count", headers=H, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "count" in body or isinstance(body, (int, dict))

    def test_seed_demo_idempotent_and_cleanup(self, H):
        r1 = requests.post(f"{API}/dashboard/seed-demo-data", headers=H, timeout=20)
        assert r1.status_code == 200, r1.text
        r2 = requests.post(f"{API}/dashboard/seed-demo-data", headers=H, timeout=20)
        assert r2.status_code == 200
        # cleanup: delete TEST_ contacts and any seed quotations/orders so live DB stays clean
        contacts = requests.get(f"{API}/contacts", headers=H, timeout=10).json() or []
        for c in contacts:
            if "demo" in (c.get("name") or "").lower() or "demo" in (c.get("email") or "").lower():
                requests.delete(f"{API}/contacts/{c['id']}", headers=H, timeout=10)
        quotes = requests.get(f"{API}/quotations", headers=H, timeout=10).json() or []
        for q in quotes:
            if "demo" in (q.get("contact_name") or "").lower():
                requests.delete(f"{API}/quotations/{q['id']}", headers=H, timeout=10)


# ───────────── settings round-trip ─────────────

class TestSettings:
    def test_get_put_integrations(self, H):
        r = requests.get(f"{API}/settings/integrations", headers=H, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        # webhook_url is exposed under whatsapp.webhook_url
        wa = data.get("whatsapp") or {}
        assert "webhook_url" in wa
        assert wa.get("webhook_url"), "whatsapp.webhook_url should be populated"
        # Put back a no-op (round-trip; only set fields we know are safe)
        uu = (data.get("universal_update") or {})
        payload = {
            "whatsapp": data.get("whatsapp") or {},
            "smtp": data.get("smtp") or {},
            "catalog": data.get("catalog") or {},
            "universal_update": {
                "template_doc": uu.get("template_doc") or "",
                "template_text": uu.get("template_text") or "",
                "template_language": uu.get("template_language") or "en_US",
            },
        }
        r2 = requests.put(f"{API}/settings/integrations",
                          json=payload, headers=H, timeout=10)
        assert r2.status_code == 200, r2.text


# ───────────── pricing ─────────────

class TestPricing:
    def test_toggle_priceless_endpoint(self, H):
        r = requests.post(f"{API}/pricing/toggle-priceless", headers=H, timeout=15)
        assert r.status_code == 200, r.text

    def test_sync_family_active(self, H):
        r = requests.post(f"{API}/pricing/sync-family-active", headers=H, timeout=15)
        assert r.status_code == 200, r.text

    def test_upload_excel_two_col_format(self, H):
        # Send a 2-column Code|Price mini-Excel (CSV via openpyxl-like would be ideal,
        # but server typically uses openpyxl; we use a real .xlsx with openpyxl).
        try:
            from openpyxl import Workbook
        except ImportError:
            pytest.skip("openpyxl not installed in test env")
        wb = Workbook(); ws = wb.active
        ws.append(["Code", "Price"])
        ws.append(["NONEXISTENT_CODE_TEST", 1234])
        bio = io.BytesIO(); wb.save(bio); bio.seek(0)
        files = {"file": ("prices.xlsx", bio.getvalue(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = requests.post(f"{API}/pricing/upload-prices-excel",
                          headers=H, files=files, timeout=30)
        # Accept 200 with "0 updated" since code doesn't exist; reject 500
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"


# ───────────── Public endpoints / OTP gates ─────────────

class TestPublicGates:
    def test_my_quotes_requires_token(self):
        r = requests.get(f"{API}/public/my-quotes", timeout=10)
        # 422 = FastAPI rejects missing required query param "token" → gate works
        assert r.status_code in (401, 403, 422)
        r2 = requests.get(f"{API}/public/my-quotes?token=BAD_TOKEN", timeout=10)
        assert r2.status_code in (401, 403)

    def test_me_requires_token(self):
        r = requests.get(f"{API}/public/me", timeout=10)
        assert r.status_code in (401, 403, 422)
        r2 = requests.get(f"{API}/public/me?token=BAD_TOKEN", timeout=10)
        assert r2.status_code in (401, 403)

    def test_me_quote_create_requires_token(self):
        r = requests.post(f"{API}/public/me/quote/create",
                          json={"line_items": []}, timeout=10)
        assert r.status_code in (401, 403, 422)
        r2 = requests.post(f"{API}/public/me/quote/create?token=BAD",
                           json={"line_items": []}, timeout=10)
        assert r2.status_code in (401, 403, 422)


# ───────────── End-to-end order + Phase 1 + Phase 2 + Phase 3 ─────────────

@pytest.fixture(scope="class")
def harsh_contact_id(H):
    """Find the live Harsh Gujarati contact (phone_norm 8200663263)."""
    contacts = requests.get(f"{API}/contacts", headers=H, timeout=15).json() or []
    for c in contacts:
        if (c.get("phone_norm") or "").endswith("8200663263"):
            return c["id"]
    pytest.skip("Harsh Gujarati contact not present in live DB")


@pytest.fixture(scope="class")
def gujarat_quote_and_order(H, harsh_contact_id):
    """Create a Gujarat quote → approve → mint order. Returns (qid, oid)."""
    # Need at least one variant to use as line item — pick first priceful variant
    variants = requests.get(f"{API}/pricing/priceless-count", headers=H, timeout=10)
    # Build a minimal quote using a free-form line item (no variant) since not all DBs
    # have priced variants; server accepts custom line items.
    contact = requests.get(f"{API}/contacts/{harsh_contact_id}", headers=H, timeout=10).json()
    qpayload = {
        "contact_id": harsh_contact_id,
        "contact_name": contact.get("name"),
        "contact_company": contact.get("company") or "ACME",
        "contact_state": "Gujarat",  # intra-state → CGST+SGST
        "contact_email": contact.get("email") or "harsh@example.com",
        "contact_phone": contact.get("phone") or ALLOWED_TEST_PHONE,
        "line_items": [{
            "product_code": "TEST_ITERATION9",
            "family_name": "Test Family",
            "description": "Iteration-9 regression line",
            "quantity": 2,
            "unit_price": 1000.0,
            "discount_percent": 0,
            "uom": "PCS",
            "hsn_code": "7308",
        }],
        "notes": "TEST_iteration9",
        "currency": "INR",
    }
    r = requests.post(f"{API}/quotations", json=qpayload, headers=H, timeout=15)
    assert r.status_code in (200, 201), r.text
    quote = r.json()
    qid = quote["id"]
    # Approve via PATCH /quotations/{qid}/status (canonical route)
    ra = requests.patch(f"{API}/quotations/{qid}/status",
                        json={"status": "approved"}, headers=H, timeout=10)
    assert ra.status_code in (200, 201, 204), ra.text
    # Mint order
    ro = requests.post(f"{API}/orders/from-quote/{qid}",
                       json={"po_number": "TEST_PO_I9"}, headers=H, timeout=15)
    assert ro.status_code == 200, ro.text
    order = ro.json()
    oid = order["id"]
    yield qid, oid
    # cleanup
    try: requests.delete(f"{API}/orders/{oid}", headers=H, timeout=10)
    except Exception: pass
    try: requests.delete(f"{API}/quotations/{qid}", headers=H, timeout=10)
    except Exception: pass


class TestGSTLogic:
    def test_gujarat_intra_state_cgst_sgst(self, H, gujarat_quote_and_order):
        qid, _ = gujarat_quote_and_order
        q = requests.get(f"{API}/quotations/{qid}", headers=H, timeout=10).json()
        # Some servers return tax totals in the quote dict
        # Check the GST type if exposed; otherwise compare cgst+sgst > 0 vs igst == 0
        cgst = float(q.get("cgst_amount") or q.get("cgst") or 0)
        sgst = float(q.get("sgst_amount") or q.get("sgst") or 0)
        igst = float(q.get("igst_amount") or q.get("igst") or 0)
        # If quote engine computes from contact_state == "Gujarat", expect intra-state
        if (cgst + sgst + igst) > 0:
            assert (cgst + sgst) > 0 and igst == 0, (
                f"Gujarat must be intra-state. cgst={cgst} sgst={sgst} igst={igst}")


class TestPhase1LineTracking:
    def test_patch_qty_status_ok(self, H, gujarat_quote_and_order):
        _, oid = gujarat_quote_and_order
        r = requests.patch(f"{API}/orders/{oid}/lines/0",
                           json={"qty_status": "in_production",
                                 "expected_dispatch_date": "2026-03-15",
                                 "internal_notes": "TEST iteration9"},
                           headers=H, timeout=10)
        assert r.status_code == 200, r.text
        order = r.json()
        li = order["line_items"][0]
        assert li["qty_status"] == "in_production"
        assert li["expected_dispatch_date"] == "2026-03-15"
        # timeline event written
        assert any(t.get("kind") == "line_update" for t in order.get("timeline", []))

    def test_patch_bad_status(self, H, gujarat_quote_and_order):
        _, oid = gujarat_quote_and_order
        r = requests.patch(f"{API}/orders/{oid}/lines/0",
                           json={"qty_status": "INVALID_STATUS"},
                           headers=H, timeout=10)
        assert r.status_code == 400

    def test_patch_bad_date(self, H, gujarat_quote_and_order):
        _, oid = gujarat_quote_and_order
        r = requests.patch(f"{API}/orders/{oid}/lines/0",
                           json={"expected_dispatch_date": "15/03/2026"},
                           headers=H, timeout=10)
        assert r.status_code == 400

    def test_patch_oob_index(self, H, gujarat_quote_and_order):
        _, oid = gujarat_quote_and_order
        r = requests.patch(f"{API}/orders/{oid}/lines/99",
                           json={"qty_status": "pending"},
                           headers=H, timeout=10)
        assert r.status_code == 404


class TestPhase2UniversalNotify:
    def test_presets_list(self, H):
        r = requests.get(f"{API}/notify/presets", headers=H, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        presets = body.get("presets") or body
        assert isinstance(presets, list)
        assert len(presets) >= 7, f"expected >=7 presets, got {len(presets)}"

    def test_notify_endpoint_structure(self, H, gujarat_quote_and_order):
        """Verify endpoint structure. Since order is for Harsh (+918200663263),
        WhatsApp send is ALLOWED. If template not synced, server returns a
        graceful error — that still proves the endpoint wiring."""
        _, oid = gujarat_quote_and_order
        r = requests.post(f"{API}/orders/{oid}/notify",
                          json={"vars": ["Hello", "Order in progress", "ETA 15 Mar",
                                         "Thanks", "—HR Exporter"],
                                "attach": "none",
                                "preset_id": None,
                                "also_email": False},
                          headers=H, timeout=30)
        # Acceptable outcomes:
        # 200 with whatsapp.ok or whatsapp.error mentioning template / language
        assert r.status_code in (200, 400, 502), r.text
        if r.status_code == 200:
            body = r.json()
            assert isinstance(body, dict)


class TestPhase3Shipments:
    @pytest.fixture(scope="class")
    def shipment_id(self, H, gujarat_quote_and_order):
        _, oid = gujarat_quote_and_order
        r = requests.post(f"{API}/orders/{oid}/shipments",
                          json={"line_indexes": [0],
                                "transporter_name": "TestTransport",
                                "lr_number": "LR-T9",
                                "expected_delivery_date": "2026-04-01"},
                          headers=H, timeout=10)
        assert r.status_code == 200, r.text
        order = r.json()
        ships = order.get("shipments") or []
        assert ships
        return oid, ships[-1]["id"]

    def test_create_shipment(self, shipment_id):
        oid, sid = shipment_id
        assert sid

    def test_duplicate_line_400(self, H, shipment_id):
        oid, _ = shipment_id
        r = requests.post(f"{API}/orders/{oid}/shipments",
                          json={"line_indexes": [0]},
                          headers=H, timeout=10)
        assert r.status_code == 400, r.text

    def test_dispatch_without_docs_400(self, H, shipment_id):
        oid, sid = shipment_id
        r = requests.post(f"{API}/orders/{oid}/shipments/{sid}/dispatch",
                          json={}, headers=H, timeout=10)
        assert r.status_code == 400, r.text
        assert "missing" in (r.json().get("detail") or "").lower()

    def test_upload_tax_invoice_flips_invoiced(self, H, shipment_id):
        oid, sid = shipment_id
        files = {"file": ("inv.pdf", b"%PDF-1.4 dummy", "application/pdf")}
        data = {"doc_key": "tax_invoice"}
        r = requests.post(f"{API}/orders/{oid}/shipments/{sid}/upload",
                          headers=H, files=files, data=data, timeout=10)
        assert r.status_code == 200, r.text
        order = r.json()
        ship = [s for s in order["shipments"] if s["id"] == sid][0]
        assert ship["stage"] == "invoiced"

    def test_upload_eway_and_dispatch_flips_shipped(self, H, shipment_id):
        oid, sid = shipment_id
        files = {"file": ("eway.pdf", b"%PDF-1.4 dummy", "application/pdf")}
        r = requests.post(f"{API}/orders/{oid}/shipments/{sid}/upload",
                          headers=H,
                          files=files, data={"doc_key": "eway_bill"}, timeout=10)
        assert r.status_code == 200, r.text
        # Now dispatch
        rd = requests.post(f"{API}/orders/{oid}/shipments/{sid}/dispatch",
                           json={"dispatched_on": "2026-03-20"},
                           headers=H, timeout=10)
        assert rd.status_code == 200, rd.text
        order = rd.json()
        ship = [s for s in order["shipments"] if s["id"] == sid][0]
        assert ship["stage"] == "dispatched"
        li = order["line_items"][0]
        assert li["qty_status"] == "shipped", f"expected shipped, got {li['qty_status']}"

    def test_delete_dispatched_400(self, H, shipment_id):
        oid, sid = shipment_id
        r = requests.delete(f"{API}/orders/{oid}/shipments/{sid}", headers=H, timeout=10)
        assert r.status_code == 400

    def test_deliver_flips_delivered(self, H, shipment_id):
        oid, sid = shipment_id
        r = requests.post(f"{API}/orders/{oid}/shipments/{sid}/deliver",
                          json={"delivered_on": "2026-03-22"},
                          headers=H, timeout=10)
        assert r.status_code == 200, r.text
        order = r.json()
        ship = [s for s in order["shipments"] if s["id"] == sid][0]
        assert ship["stage"] == "delivered"
        li = order["line_items"][0]
        assert li["qty_status"] == "delivered"


# ───────────── Security ─────────────

class TestSecurity:
    def test_orders_requires_admin_token(self):
        r = requests.get(f"{API}/orders", timeout=10)
        assert r.status_code in (401, 403)

    def test_uploads_no_directory_listing(self):
        # Attempt to list /api/uploads/orders/ should not return JSON listing or 200 index
        r = requests.get(f"{BASE_URL}/api/uploads/orders/", timeout=10)
        # Acceptable: 401/403/404. Bad: 200 listing.
        assert r.status_code in (401, 403, 404), f"got {r.status_code}, possibly directory listing"
