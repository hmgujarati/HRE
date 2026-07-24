"""Iteration 20 — Company / PDF settings tab: seller + T&C in settings and PDFs.

Verifies:
- GET /api/settings/integrations exposes `seller` + `terms.default_terms` (with defaults merged in).
- PUT /api/settings/integrations updates seller + terms; next GET returns updated values.
- Quote PDF regeneration honours updated seller (via pdfplumber assertion on header/GSTIN/bank).
- Quote PDF T&C block falls back to settings.terms.default_terms when quote has no terms.
- Proforma + Tax Invoice regen use the same seller override.
- GST intra/inter detection flips with seller.state via PUT.
- Restores original settings at teardown so downstream tests aren't polluted.
"""
import io
import os
import time

import pdfplumber
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"


# ------------------------- fixtures -------------------------

@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def original_settings(client):
    """Snapshot original seller + terms, restore on teardown."""
    r = client.get(f"{BASE_URL}/api/settings/integrations")
    assert r.status_code == 200, r.text
    data = r.json()
    orig_seller = dict(data.get("seller") or {})
    orig_terms = dict(data.get("terms") or {})
    yield {"seller": orig_seller, "terms": orig_terms}
    # Teardown — restore
    payload = {"seller": orig_seller, "terms": orig_terms}
    client.put(f"{BASE_URL}/api/settings/integrations", json=payload, timeout=20)


# ------------------------- helpers -------------------------

def _pdf_text(url_or_bytes):
    if isinstance(url_or_bytes, (bytes, bytearray)):
        stream = io.BytesIO(url_or_bytes)
    else:
        stream = io.BytesIO(url_or_bytes)
    with pdfplumber.open(stream) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _fetch_pdf_bytes(client, path):
    """path either absolute /api/... or full URL. Always rewrites to BASE_URL host
    (backend may emit PUBLIC_BASE_URL that isn't reachable from the test runner)."""
    if path.startswith("http"):
        # Strip host, keep the /api/... path
        from urllib.parse import urlparse
        p = urlparse(path)
        url = f"{BASE_URL}{p.path}"
        if p.query:
            url += "?" + p.query
    else:
        url = f"{BASE_URL}{path}"
    r = client.get(url, timeout=30)
    assert r.status_code == 200, f"PDF fetch failed {r.status_code} {url}"
    assert r.headers.get("content-type", "").startswith("application/pdf") or url.endswith(".pdf"), \
        f"Expected pdf, got {r.headers.get('content-type')}"
    return r.content


def _first_quote(client):
    r = client.get(f"{BASE_URL}/api/quotations?limit=50")
    assert r.status_code == 200, r.text
    quotes = r.json()
    assert quotes, "No quotations found in DB — cannot test PDF regen"
    return quotes[0]


# ------------------------- GET / PUT settings -------------------------

def test_get_settings_exposes_seller_and_terms(client, original_settings):
    r = client.get(f"{BASE_URL}/api/settings/integrations")
    assert r.status_code == 200
    data = r.json()
    seller = data.get("seller")
    terms = data.get("terms")
    assert isinstance(seller, dict), "seller missing from settings"
    assert isinstance(terms, dict), "terms missing from settings"
    for k in ("name", "address", "gstin", "pan", "state", "state_code",
              "bank_name", "bank_account", "bank_ifsc", "bank_branch",
              "phones", "email"):
        assert k in seller, f"seller.{k} missing"
    assert "default_terms" in terms
    # Defaults should be non-empty when unset
    assert seller["name"], "seller.name is blank"
    assert terms["default_terms"], "terms.default_terms is blank"


def test_put_settings_persists_seller_and_terms(client, original_settings):
    new_seller = {
        **original_settings["seller"],
        "name": "TESTCO PVT LTD",
        "address": "TEST ADDRESS LINE X",
        "gstin": "24TEST0000A1ZZ",
        "pan": "TESTPAN123",
        "bank_name": "TEST BANK",
        "bank_account": "9999999999",
    }
    new_terms = {"default_terms": "1. TEST TERM ALPHA\n2. TEST TERM BETA"}
    r = client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": new_seller, "terms": new_terms},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    # Verify via GET
    r2 = client.get(f"{BASE_URL}/api/settings/integrations")
    assert r2.status_code == 200
    got = r2.json()
    assert got["seller"]["name"] == "TESTCO PVT LTD"
    assert got["seller"]["gstin"] == "24TEST0000A1ZZ"
    assert got["seller"]["bank_name"] == "TEST BANK"
    assert got["terms"]["default_terms"] == new_terms["default_terms"]

    # Restore original for next tests
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": original_settings["seller"], "terms": original_settings["terms"]},
        timeout=20,
    )


# ------------------------- Quote PDF regeneration -------------------------

def test_quote_pdf_reflects_updated_seller_and_terms(client, original_settings):
    # Find a quote with EMPTY terms so the default_terms fallback kicks in
    r = client.get(f"{BASE_URL}/api/quotations?limit=200")
    assert r.status_code == 200
    quotes = r.json()
    quote = None
    for q in quotes:
        detail = client.get(f"{BASE_URL}/api/quotations/{q['id']}").json()
        if not (detail.get("terms") or "").strip():
            quote = detail
            break
    if not quote:
        pytest.skip("No quote with empty terms found — cannot verify default_terms fallback")
    qid = quote["id"]

    # Update seller + terms
    marker_name = "PDFMARK EXPORTS LTD"
    marker_gstin = "24MARKR9999A1ZZ"
    marker_bank = "TESTBK123"
    marker_terms = "1. PDFMARK CLAUSE ONE\n2. PDFMARK CLAUSE TWO"
    new_seller = {
        **original_settings["seller"],
        "name": marker_name,
        "gstin": marker_gstin,
        "bank_name": marker_bank,
    }
    r = client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": new_seller, "terms": {"default_terms": marker_terms}},
        timeout=20,
    )
    assert r.status_code == 200, r.text

    # Regenerate PDF
    r_pdf = client.get(f"{BASE_URL}/api/quotations/{qid}/pdf", timeout=60)
    assert r_pdf.status_code == 200, f"PDF gen failed: {r_pdf.status_code} {r_pdf.text[:200]}"

    # If endpoint returns JSON with url, follow it; else assume bytes
    ct = r_pdf.headers.get("content-type", "")
    if "application/json" in ct:
        payload = r_pdf.json()
        pdf_url = payload.get("url") or payload.get("pdf_url")
        assert pdf_url, f"No pdf url in response: {payload}"
        pdf_bytes = _fetch_pdf_bytes(client, pdf_url)
    else:
        pdf_bytes = r_pdf.content

    text = _pdf_text(pdf_bytes)
    # Normalise whitespace — pdfplumber can insert newlines in narrow columns
    text_norm = " ".join(text.split())
    assert marker_name in text_norm, f"seller.name not in PDF header. First 500 chars: {text[:500]}"
    assert marker_gstin in text_norm, f"seller.gstin not in PDF. Text sample: {text[:500]}"
    # T&C fallback
    assert "PDFMARK CLAUSE ONE" in text_norm, f"default_terms not applied. Text sample: {text[-800:]}"
    # Bank name propagation
    assert marker_bank in text_norm, (
        "seller.bank_name NOT reflected in PDF. "
        f"Text sample: {text[:1000]}"
    )

    # Restore original settings
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": original_settings["seller"], "terms": original_settings["terms"]},
        timeout=20,
    )


# ------------------------- Proforma / Tax Invoice regen -------------------------

def test_proforma_and_invoice_use_seller_override(client, original_settings):
    # Find an order that already has proforma/invoice OR create via generation endpoints
    r = client.get(f"{BASE_URL}/api/orders?limit=50")
    assert r.status_code == 200
    orders = r.json()
    if not orders:
        pytest.skip("No orders present — cannot verify proforma/invoice PDF")

    # Update seller marker
    marker_name = "PROFMARK EXPORTS"
    marker_gstin = "24PROFM9999A1ZZ"
    new_seller = {**original_settings["seller"], "name": marker_name, "gstin": marker_gstin}
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": new_seller, "terms": original_settings["terms"]},
        timeout=20,
    )

    tested_proforma = False
    tested_invoice = False
    for od in orders:
        oid = od["id"]
        if not tested_proforma:
            r_pf = client.post(f"{BASE_URL}/api/orders/{oid}/proforma/generate", timeout=60)
            if r_pf.status_code == 200:
                payload = r_pf.json() if r_pf.headers.get("content-type", "").startswith("application/json") else {}
                url = payload.get("url") or (payload.get("proforma") or {}).get("url")
                if url:
                    pdf_bytes = _fetch_pdf_bytes(client, url)
                    text = _pdf_text(pdf_bytes)
                    assert marker_name in text, f"proforma missing seller override. Sample: {text[:400]}"
                    tested_proforma = True
        if not tested_invoice:
            r_inv = client.post(f"{BASE_URL}/api/orders/{oid}/invoice/generate", timeout=60)
            if r_inv.status_code == 200:
                payload = r_inv.json() if r_inv.headers.get("content-type", "").startswith("application/json") else {}
                url = payload.get("url") or (payload.get("invoice") or {}).get("url") \
                      or ((payload.get("documents") or {}).get("invoice") or {}).get("url")
                if url:
                    pdf_bytes = _fetch_pdf_bytes(client, url)
                    text = _pdf_text(pdf_bytes)
                    assert marker_name in text, f"invoice missing seller override. Sample: {text[:400]}"
                    tested_invoice = True
        if tested_proforma and tested_invoice:
            break

    # Restore
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": original_settings["seller"], "terms": original_settings["terms"]},
        timeout=20,
    )

    if not (tested_proforma or tested_invoice):
        pytest.skip("Could not exercise proforma/invoice generation endpoints (likely need stage prerequisites).")


# ------------------------- GST intra/inter flip -------------------------

def test_gst_logic_flips_with_seller_state(client, original_settings):
    quote = _first_quote(client)
    qid = quote["id"]
    detail = client.get(f"{BASE_URL}/api/quotations/{qid}", timeout=20).json()
    place = (detail.get("place_of_supply") or "").upper()
    if not place:
        pytest.skip("Quote has no place_of_supply — cannot verify GST flip")

    # Case A: seller.state = GUJARAT
    seller_a = {**original_settings["seller"], "state": "GUJARAT", "state_code": "24"}
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": seller_a, "terms": original_settings["terms"]},
        timeout=20,
    )
    r_a = client.get(f"{BASE_URL}/api/quotations/{qid}/pdf", timeout=60)
    assert r_a.status_code == 200
    pdf_a = r_a.content if not r_a.headers.get("content-type", "").startswith("application/json") \
            else _fetch_pdf_bytes(client, r_a.json().get("url") or r_a.json().get("pdf_url"))
    txt_a = _pdf_text(pdf_a)

    # Case B: seller.state = MAHARASHTRA
    seller_b = {**original_settings["seller"], "state": "MAHARASHTRA", "state_code": "27"}
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": seller_b, "terms": original_settings["terms"]},
        timeout=20,
    )
    r_b = client.get(f"{BASE_URL}/api/quotations/{qid}/pdf", timeout=60)
    assert r_b.status_code == 200
    pdf_b = r_b.content if not r_b.headers.get("content-type", "").startswith("application/json") \
            else _fetch_pdf_bytes(client, r_b.json().get("url") or r_b.json().get("pdf_url"))
    txt_b = _pdf_text(pdf_b)

    def _kind(text):
        # Return "IGST" or "CGST+SGST" or "unknown"
        has_igst = "IGST" in text
        has_cgst = "CGST" in text and "SGST" in text
        if has_igst and not has_cgst:
            return "IGST"
        if has_cgst and not has_igst:
            return "CGST+SGST"
        return "unknown"

    kind_a = _kind(txt_a)
    kind_b = _kind(txt_b)

    if place == "GUJARAT":
        assert kind_a == "CGST+SGST", f"Expected CGST+SGST when seller=GUJ, buyer=GUJ; got {kind_a}"
        assert kind_b == "IGST", f"Expected IGST when seller=MH, buyer=GUJ; got {kind_b}"
    elif place == "MAHARASHTRA":
        assert kind_a == "IGST", f"Expected IGST when seller=GUJ, buyer=MH; got {kind_a}"
        assert kind_b == "CGST+SGST", f"Expected CGST+SGST when seller=MH, buyer=MH; got {kind_b}"
    else:
        # Different buyer state: flip should show change — case_a IGST, case_b IGST too UNLESS
        # buyer state matches one of them. Just assert that A and B give some GST layout each.
        assert kind_a in ("IGST", "CGST+SGST"), f"Case A unrecognised GST layout: {kind_a}"
        assert kind_b in ("IGST", "CGST+SGST"), f"Case B unrecognised GST layout: {kind_b}"

    # Restore
    client.put(
        f"{BASE_URL}/api/settings/integrations",
        json={"seller": original_settings["seller"], "terms": original_settings["terms"]},
        timeout=20,
    )


def test_teardown_restored(client, original_settings):
    """Final sanity: settings after all tests match the original snapshot."""
    r = client.get(f"{BASE_URL}/api/settings/integrations")
    data = r.json()
    assert data["seller"]["name"] == original_settings["seller"]["name"]
    assert data["seller"]["gstin"] == original_settings["seller"]["gstin"]
    assert data["seller"]["state"] == original_settings["seller"]["state"]
    assert data["terms"]["default_terms"] == original_settings["terms"]["default_terms"]
