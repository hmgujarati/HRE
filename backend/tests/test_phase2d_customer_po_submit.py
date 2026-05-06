"""Phase 2D backend tests — Customer-side PO submission via /public/quote/{qid}/submit-po

Coverage:
- Auth: invalid/missing token (401), forged token belonging to a different phone (403)
- Validation: both empty → 400; quote in draft → 400
- Happy paths: instructions only, PDF + instructions
- Idempotency: second submission attaches to the same order (no duplicate)
- Public summary in GET /api/public/my-quotes carries po_submitted_by_customer / po_url / po_instructions
- Settings: PUT integrations persists admin_notify_phone, po_received_admin_template, admin_notify_email
- Graceful admin notification (no 500) when WhatsApp/SMTP disabled
- Stage never auto-advances (remains pending_po after customer PO)
"""
import io
import os
import time
import uuid
import pytest
import requests
from typing import Any, Dict

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


def _assert_no_mongo_id(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        assert "_id" not in obj, f"Mongo _id leaked at {path or 'root'}"
        for k, v in obj.items():
            _assert_no_mongo_id(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, it in enumerate(obj):
            _assert_no_mongo_id(it, f"{path}[{i}]")


# ------------- fixtures -------------
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_client(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


def _login_public(phone: str) -> str:
    """Run the /public/my-quotes/login/start + verify-otp flow, return session token."""
    r = requests.post(f"{BASE_URL}/api/public/my-quotes/login/start",
                      json={"phone": phone}, timeout=20)
    assert r.status_code == 200, f"login/start failed: {r.status_code} {r.text}"
    j = r.json()
    rid = j["request_id"]
    code = j.get("dev_otp")
    assert code, f"dev_otp not returned (DEV_OTP_PASSTHROUGH should be on): {j}"
    r2 = requests.post(f"{BASE_URL}/api/public/quote-requests/{rid}/verify-otp",
                       json={"code": code}, timeout=20)
    assert r2.status_code == 200, f"verify-otp failed: {r2.status_code} {r2.text}"
    tok = r2.json()["token"]
    assert tok
    return tok


@pytest.fixture(scope="session")
def seeded_setup(admin_client):
    """Create a fresh contact + sent quote dedicated to PO submission tests, plus an OTHER contact for forged-token test."""
    # ---- primary contact ----
    phone = "9" + str(int(time.time()))[-9:]
    payload = {
        "name": f"TEST_PO_{uuid.uuid4().hex[:5]}",
        "company": "TEST_PoCo",
        "phone": phone,
        "email": f"TEST_po_{uuid.uuid4().hex[:5]}@example.com",
        "state": "Karnataka",
        "source": "manual",
    }
    r = admin_client.post(f"{BASE_URL}/api/contacts", json=payload)
    assert r.status_code == 200, r.text
    contact = r.json()
    STATE["contact_id"] = contact["id"]
    STATE["phone"] = phone

    # variant
    r = admin_client.get(f"{BASE_URL}/api/product-variants")
    assert r.status_code == 200
    variants = r.json()
    assert variants, "No variants seeded; Phase 1 seed required"
    v = variants[0]

    q_payload = {
        "contact_id": contact["id"],
        "place_of_supply": "Karnataka",
        "valid_until": "2026-12-31",
        "line_items": [{
            "product_variant_id": v["id"],
            "product_code": v["product_code"],
            "family_name": "TestFam",
            "description": "desc",
            "hsn_code": "85369090",
            "quantity": 5,
            "unit": "NOS",
            "base_price": 200.0,
            "discount_percentage": 0.0,
            "gst_percentage": 18.0,
        }],
    }
    r = admin_client.post(f"{BASE_URL}/api/quotations", json=q_payload)
    assert r.status_code == 200, r.text
    q = r.json()
    STATE["quote_id"] = q["id"]
    STATE["quote_number"] = q["quote_number"]
    assert q.get("status") in ("draft", None), f"unexpected initial status: {q.get('status')}"

    # ---- secondary contact for forged-token test ----
    phone2 = "8" + str(int(time.time()))[-9:]
    payload2 = {
        "name": f"TEST_OTHER_{uuid.uuid4().hex[:5]}",
        "company": "TEST_OtherCo",
        "phone": phone2,
        "email": f"TEST_oth_{uuid.uuid4().hex[:5]}@example.com",
        "state": "Karnataka",
        "source": "manual",
    }
    r = admin_client.post(f"{BASE_URL}/api/contacts", json=payload2)
    assert r.status_code == 200, r.text
    c2 = r.json()
    STATE["other_contact_id"] = c2["id"]
    STATE["other_phone"] = phone2

    return STATE


# ============================================================
#  Validation gates that should fire WHILE the quote is draft
# ============================================================
class TestADraftQuoteRejected:
    def test_login_and_get_token(self, seeded_setup):
        STATE["token"] = _login_public(STATE["phone"])
        STATE["other_token"] = _login_public(STATE["other_phone"])

    def test_submit_po_against_draft_quote_400(self):
        qid = STATE["quote_id"]
        files = {}
        data = {"token": STATE["token"], "instructions": "Please proceed"}
        r = requests.post(f"{BASE_URL}/api/public/quote/{qid}/submit-po", data=data, files=files, timeout=30)
        assert r.status_code == 400, f"draft must be rejected: {r.status_code} {r.text}"
        assert "ready" in r.text.lower() or "sent" in r.text.lower() or "approved" in r.text.lower()


# ============================================================
#  Send the quote so subsequent tests can submit a PO
# ============================================================
class TestBPromoteQuoteToSent:
    def test_send_quote(self, admin_client):
        qid = STATE["quote_id"]
        # Trigger the /send endpoint (PDF generation, dispatch attempted). With integrations disabled,
        # status will NOT auto-flip to 'sent', so we explicitly PATCH to 'sent' afterwards — that's the
        # expected admin workflow when WhatsApp/SMTP are not configured.
        r = admin_client.post(f"{BASE_URL}/api/quotations/{qid}/send")
        assert r.status_code == 200, r.text
        r2 = admin_client.patch(f"{BASE_URL}/api/quotations/{qid}/status", json={"status": "sent"})
        assert r2.status_code == 200, r2.text
        r = admin_client.get(f"{BASE_URL}/api/quotations/{qid}")
        assert r.status_code == 200
        q = r.json()
        assert q.get("status") in ("sent", "approved"), f"expected sent/approved got {q.get('status')}"


# ============================================================
#  Auth / validation
# ============================================================
class TestCAuthAndValidation:
    def test_invalid_token_401(self):
        qid = STATE["quote_id"]
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"token": "garbage_invalid_token_xyz", "instructions": "hello"},
            timeout=20,
        )
        assert r.status_code == 401, f"{r.status_code} {r.text}"

    def test_missing_token_422(self):
        # token is Form(...) required ⇒ FastAPI returns 422 if not provided at all
        qid = STATE["quote_id"]
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"instructions": "hello"},
            timeout=20,
        )
        assert r.status_code in (401, 422), r.text

    def test_forged_token_403(self):
        """Token belongs to a contact whose phone differs from the quote owner."""
        qid = STATE["quote_id"]
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"token": STATE["other_token"], "instructions": "stealth attempt"},
            timeout=20,
        )
        assert r.status_code == 403, f"{r.status_code} {r.text}"

    def test_both_empty_400(self):
        qid = STATE["quote_id"]
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"token": STATE["token"], "instructions": ""},
            timeout=20,
        )
        assert r.status_code == 400, f"{r.status_code} {r.text}"
        assert "instructions" in r.text.lower() or "po" in r.text.lower()


# ============================================================
#  Happy path 1 — instructions only (no file) creates the order
# ============================================================
class TestDInstructionsOnly:
    def test_instructions_only_creates_order_in_pending_po(self):
        qid = STATE["quote_id"]
        instructions = "Please proceed with order. Ship to Mumbai. Reference: CUST-PO-001"
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"token": STATE["token"], "instructions": instructions},
            timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        _assert_no_mongo_id(body)
        assert body.get("ok") is True
        # Should NOT auto-advance — stage stays pending_po
        assert body.get("stage") == "pending_po", f"stage auto-advanced: {body.get('stage')}"
        assert body.get("po_attached") is False
        assert body.get("had_existing_order") is False, "expected fresh order on first submit"
        order_no = body.get("order_number")
        assert order_no, "order_number missing"
        STATE["order_number"] = order_no
        # admin_notified shape: dict with email + whatsapp keys, no 500 even if creds missing
        admin = body.get("admin_notified") or {}
        assert "email" in admin and "whatsapp" in admin
        assert admin["email"] in (False, True)
        assert admin["whatsapp"] in (False, True)

    def test_admin_can_see_order_with_customer_po_flag(self, admin_client):
        order_no = STATE.get("order_number")
        # Find via admin orders list
        r = admin_client.get(f"{BASE_URL}/api/orders")
        assert r.status_code == 200
        orders = r.json()
        match = [o for o in orders if o.get("order_number") == order_no]
        assert match, f"order {order_no} not found in admin list"
        order = match[0]
        STATE["order_id"] = order["id"]
        po = (order.get("documents") or {}).get("po") or {}
        assert po.get("submitted_by_customer") is True, f"submitted_by_customer flag missing: {po}"
        assert po.get("customer_instructions") == "Please proceed with order. Ship to Mumbai. Reference: CUST-PO-001"
        # No file path for instructions-only
        assert po.get("filename", "") == ""
        assert po.get("url", "") == ""
        # Timeline contains a customer_po event
        tl = order.get("timeline") or []
        kinds = [ev.get("kind") for ev in tl]
        assert "customer_po" in kinds, f"timeline missing customer_po event; have {kinds}"
        # Stage still pending_po
        assert order.get("stage") == "pending_po"


# ============================================================
#  Idempotency — second submit must not create a new order
# ============================================================
class TestEIdempotency:
    def test_second_submit_attaches_to_same_order(self):
        qid = STATE["quote_id"]
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"token": STATE["token"], "instructions": "Updated instructions — please rush."},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("had_existing_order") is True, "must not create duplicate order"
        assert body.get("order_number") == STATE["order_number"], (
            f"order number changed: {body.get('order_number')} vs {STATE['order_number']}"
        )
        assert body.get("stage") == "pending_po", "second submit must not auto-advance"


# ============================================================
#  Happy path 2 — PDF file + instructions
# ============================================================
class TestFPdfWithInstructions:
    def test_pdf_file_uploaded_and_recorded(self):
        qid = STATE["quote_id"]
        # minimal valid-ish PDF (header + EOF marker — server doesn't validate PDF structure)
        pdf_bytes = b"%PDF-1.4\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"
        files = {"file": ("customer_po.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        data = {"token": STATE["token"], "instructions": "Final PO with file. Pay 50% advance."}
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data=data, files=files, timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert body.get("po_attached") is True
        assert body.get("order_number") == STATE["order_number"], "should not create new order"
        assert body.get("stage") == "pending_po"

    def test_admin_sees_pdf_metadata(self, admin_client):
        oid = STATE["order_id"]
        r = admin_client.get(f"{BASE_URL}/api/orders/{oid}")
        assert r.status_code == 200, r.text
        order = r.json()
        po = (order.get("documents") or {}).get("po") or {}
        assert po.get("submitted_by_customer") is True
        assert po.get("customer_instructions") == "Final PO with file. Pay 50% advance."
        assert po.get("filename", "").endswith(".pdf"), f"bad filename: {po.get('filename')}"
        url = po.get("url", "")
        assert url, "url missing"
        # url should point to /api/uploads/orders/{oid}/po_*.pdf
        assert f"/api/uploads/orders/{oid}/" in url, f"url not under order folder: {url}"
        assert "/po_" in url, f"url not prefixed with po_: {url}"


# ============================================================
#  Public summary surfaces the new fields
# ============================================================
class TestGPublicSummary:
    def test_my_quotes_carries_order_summary(self):
        r = requests.get(
            f"{BASE_URL}/api/public/my-quotes",
            params={"token": STATE["token"]}, timeout=20,
        )
        assert r.status_code == 200, r.text
        items = r.json()
        _assert_no_mongo_id(items)
        match = [q for q in items if q.get("id") == STATE["quote_id"]]
        assert match, "quote not visible to customer"
        q = match[0]
        order = q.get("order") or {}
        assert order, "order summary missing"
        assert order.get("po_submitted_by_customer") is True
        assert order.get("po_url"), "po_url should be set after PDF upload"
        assert "Final PO with file" in (order.get("po_instructions") or "")
        assert order.get("stage") == "pending_po"
        # milestones array exists
        ms = order.get("milestones") or []
        assert isinstance(ms, list) and len(ms) > 0


# ============================================================
#  Settings — admin_notify_phone, po_received_admin_template, admin_notify_email
# ============================================================
class TestHIntegrationsExtraFields:
    def test_put_persists_new_fields(self, admin_client):
        # First read existing to preserve required fields
        r = admin_client.get(f"{BASE_URL}/api/settings/integrations")
        assert r.status_code == 200
        cur = r.json()
        wa = cur.get("whatsapp", {})
        sm = cur.get("smtp", {})
        payload = {
            "whatsapp": {
                "enabled": False,
                "vendor_uid": wa.get("vendor_uid", ""),
                "token": "",
                "default_country_code": wa.get("default_country_code", "91"),
                "quote_template_name": wa.get("quote_template_name", ""),
                "quote_template_language": wa.get("quote_template_language", "en"),
                "admin_notify_phone": "919999000111",
                "po_received_admin_template": "TEST_po_received_admin_v1",
            },
            "smtp": {
                "enabled": False,
                "host": sm.get("host", "smtp.example.com"),
                "port": sm.get("port", 465),
                "use_ssl": sm.get("use_ssl", True),
                "username": sm.get("username", "test@example.com"),
                "password": "",
                "from_email": sm.get("from_email", "test@example.com"),
                "from_name": sm.get("from_name", "TEST HRE"),
                "admin_notify_email": "admin-alerts@example.com",
            },
        }
        r = admin_client.put(f"{BASE_URL}/api/settings/integrations", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["whatsapp"].get("admin_notify_phone") == "919999000111"
        assert d["whatsapp"].get("po_received_admin_template") == "TEST_po_received_admin_v1"
        assert d["smtp"].get("admin_notify_email") == "admin-alerts@example.com"
        # Re-read for persistence
        r2 = admin_client.get(f"{BASE_URL}/api/settings/integrations")
        d2 = r2.json()
        assert d2["whatsapp"]["admin_notify_phone"] == "919999000111"
        assert d2["whatsapp"]["po_received_admin_template"] == "TEST_po_received_admin_v1"
        assert d2["smtp"]["admin_notify_email"] == "admin-alerts@example.com"
        _assert_no_mongo_id(d2)


# ============================================================
#  Graceful notification — even with the new fields configured,
#  WhatsApp/SMTP are disabled so admin_notified must be {false,false} (no 500).
# ============================================================
class TestIGracefulNotification:
    def test_submit_po_returns_admin_notified_false_no_500(self):
        qid = STATE["quote_id"]
        r = requests.post(
            f"{BASE_URL}/api/public/quote/{qid}/submit-po",
            data={"token": STATE["token"], "instructions": "Re-submit, integrations disabled"},
            timeout=30,
        )
        assert r.status_code == 200, f"got {r.status_code} {r.text}"
        body = r.json()
        admin = body.get("admin_notified") or {}
        # Both must be False since enabled=False
        assert admin.get("email") is False, f"email should be skipped: {admin}"
        assert admin.get("whatsapp") is False, f"whatsapp should be skipped: {admin}"
        # No error keys leaking tracebacks
        assert not admin.get("email_error"), f"unexpected email_error: {admin.get('email_error')}"
        assert not admin.get("whatsapp_error"), f"unexpected whatsapp_error: {admin.get('whatsapp_error')}"


# ============================================================
#  Cleanup
# ============================================================
class TestZCleanup:
    def test_delete_order(self, admin_client):
        oid = STATE.get("order_id")
        if not oid:
            pytest.skip("no order to delete")
        r = admin_client.delete(f"{BASE_URL}/api/orders/{oid}")
        assert r.status_code in (200, 204, 404), r.text

    def test_delete_quote(self, admin_client):
        qid = STATE.get("quote_id")
        if not qid:
            pytest.skip("no quote to delete")
        r = admin_client.delete(f"{BASE_URL}/api/quotations/{qid}")
        assert r.status_code in (200, 204, 404), r.text

    def test_delete_contacts(self, admin_client):
        for key in ("contact_id", "other_contact_id"):
            cid = STATE.get(key)
            if not cid:
                continue
            r = admin_client.delete(f"{BASE_URL}/api/contacts/{cid}")
            assert r.status_code in (200, 204, 404), r.text
