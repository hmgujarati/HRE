"""Iteration 13: Verify DD/MM/YYYY (slash) date format across backend.

- backend/quote_pdf.py::_format_date_dmy → %d/%m/%Y
- backend/server.py strftime spots → %d/%m/%Y
- backend/services/dispatch.py strftime spots → %d/%m/%Y
- PDF download endpoint returns valid PDF with slash-format dates in it
"""
import os
import re
import io
import pytest
import requests

def _load_base_url():
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if v:
        return v.rstrip("/")
    # fall back to frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    raise RuntimeError("REACT_APP_BACKEND_URL not set")


BASE_URL = _load_base_url()
ADMIN_EMAIL = "admin@hrexporter.com"
ADMIN_PASSWORD = "Admin@123"


# ------- source-level assertions (no runtime needed) -------

def _read(p):
    with open(p) as f:
        return f.read()


def test_quote_pdf_format_uses_slash_and_4digit_year():
    src = _read("/app/backend/quote_pdf.py")
    assert 'strftime("%d/%m/%Y")' in src, "quote_pdf._format_date_dmy must use %d/%m/%Y"
    # legacy patterns should not be present anywhere as strftime formats
    assert '%d-%m-%y' not in src
    assert '%d-%m-%Y' not in src


def test_server_py_strftime_uses_slash():
    src = _read("/app/backend/server.py")
    # both timestamps changed to slash format
    slash_hits = re.findall(r"strftime\(['\"]%d/%m/%Y[^'\"]*['\"]\)", src)
    assert len(slash_hits) >= 2, f"expected >=2 slash strftime in server.py, got {slash_hits}"
    assert "strftime('%d-%m-%Y" not in src and 'strftime("%d-%m-%Y' not in src


def test_dispatch_py_strftime_uses_slash():
    src = _read("/app/backend/services/dispatch.py")
    slash_hits = re.findall(r"strftime\(['\"]%d/%m/%Y[^'\"]*['\"]\)", src)
    assert len(slash_hits) >= 3, f"expected >=3 slash strftime in dispatch.py, got {slash_hits}"
    # legacy 'DD-MM-YYYY %H:%M IST' style must be gone
    assert "strftime('%d-%m-%Y %H:%M" not in src
    assert 'strftime("%d-%m-%Y %H:%M' not in src


# ------- unit test the _format_date_dmy helper directly -------

def test_format_date_dmy_helper():
    from backend.quote_pdf import _format_date_dmy  # type: ignore
    assert _format_date_dmy("2026-06-25") == "25/06/2026"
    assert _format_date_dmy("2026-06-25T10:30:00") == "25/06/2026"
    assert _format_date_dmy("") == ""


# ------- runtime: log in and download a quotation PDF -------

@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_quotations_list_ok(headers):
    r = requests.get(f"{BASE_URL}/api/quotations", headers=headers, timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_quotation_pdf_download_contains_slash_dates(headers):
    r = requests.get(f"{BASE_URL}/api/quotations", headers=headers, timeout=30)
    assert r.status_code == 200
    quotes = r.json()
    if not quotes:
        pytest.skip("no quotations available in DB to test PDF")
    qid = quotes[0].get("id") or quotes[0].get("_id")
    assert qid, quotes[0]
    r = requests.get(f"{BASE_URL}/api/quotations/{qid}/pdf",
                     headers=headers, timeout=60)
    assert r.status_code == 200, f"pdf download failed: {r.status_code} {r.text[:200]}"
    assert r.content.startswith(b"%PDF"), "response is not a valid PDF"
    # Best effort: pdfplumber if available
    try:
        import pdfplumber
    except Exception:
        pytest.skip("pdfplumber not installed — content-level slash check skipped")
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    # Expect DD/MM/YYYY somewhere in the doc header (Quot. Date / Date)
    m = re.search(r"\b\d{2}/\d{2}/\d{4}\b", text)
    assert m, f"no DD/MM/YYYY date found in PDF text. First 500 chars:\n{text[:500]}"
    # Ensure legacy DD-MM-YYYY doesn't appear
    assert not re.search(r"\b\d{2}-\d{2}-\d{2,4}\b(?!\d)", text[:2000]), \
        "legacy DD-MM-YY(YY) still present in PDF header area"
