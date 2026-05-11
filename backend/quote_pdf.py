"""Server-side PDF generation for HRE Exporter quotations.

Replicates the visual layout of /app/frontend/src/pages/QuotationView.jsx using
Jinja2 + WeasyPrint so the PDF emailed/WhatsApp'd matches the on-screen print.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from jinja2 import Environment, BaseLoader, select_autoescape
from weasyprint import HTML

# Indian Rupee number-to-words (mirror of frontend src/lib/numberToWords.js)
_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _three_digits(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        parts.append(_two_digits(n))
    return " ".join(parts)


def number_to_words_inr(amount: float) -> str:
    if amount is None:
        return ""
    rupees = int(amount)
    paise = round((float(amount) - rupees) * 100)

    def _whole(n: int) -> str:
        if n == 0:
            return "Zero"
        chunks = []
        crore = n // 10_000_000
        n %= 10_000_000
        lakh = n // 100_000
        n %= 100_000
        thousand = n // 1000
        n %= 1000
        if crore:
            chunks.append(_three_digits(crore) + " Crore")
        if lakh:
            chunks.append(_two_digits(lakh) + " Lakh")
        if thousand:
            chunks.append(_two_digits(thousand) + " Thousand")
        if n:
            chunks.append(_three_digits(n))
        return " ".join(chunks)

    out = _whole(rupees) + " Rupees"
    if paise:
        out += " and " + _two_digits(paise) + " Paise"
    out += " Only"
    return out


SELLER = {
    "name": "HREXPORTER",
    "address": "BLOCK NO 201, BHATGAM ROAD, BHATGAM, OLPAD, SURAT, 394540",
    "phones": "+91 9033135768, +91 8980004416 (Guj. Ind)",
    "email": "info@hrexporter.com",
    "gstin": "24ENVPS1624A1ZZ",
    "pan": "ENVPS1624A",
    "state": "GUJARAT",
    "state_code": "24",
    "bank_name": "ICICI BANK",
    "bank_account": "183705501244",
    "bank_ifsc": "ICIC0001837",
    "bank_branch": "L P SAVANI ROAD BRANCH, SURAT.",
}

STATE_CODE_MAP = {
    "ANDHRA PRADESH": "37", "ARUNACHAL PRADESH": "12", "ASSAM": "18", "BIHAR": "10",
    "CHHATTISGARH": "22", "DELHI": "07", "GOA": "30", "GUJARAT": "24", "HARYANA": "06",
    "HIMACHAL PRADESH": "02", "JAMMU AND KASHMIR": "01", "JHARKHAND": "20", "KARNATAKA": "29",
    "KERALA": "32", "MADHYA PRADESH": "23", "MAHARASHTRA": "27", "MANIPUR": "14",
    "MEGHALAYA": "17", "MIZORAM": "15", "NAGALAND": "13", "ODISHA": "21", "PUNJAB": "03",
    "RAJASTHAN": "08", "SIKKIM": "11", "TAMIL NADU": "33", "TELANGANA": "36", "TRIPURA": "16",
    "UTTAR PRADESH": "09", "UTTARAKHAND": "05", "WEST BENGAL": "19",
}


_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{{ q.quote_number }}</title>
<style>
  @page { size: A4; margin: 10mm; }
  * { box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; color: #000; font-size: 11px; margin: 0; }
  .quote { width: 100%; border: 1.5px solid #000; }
  .center { text-align: center; }
  .right { text-align: right; }
  .bold { font-weight: 700; }
  .underline { text-decoration: underline; }
  .mono { font-family: 'Courier New', monospace; }
  .row { display: flex; }
  .col { flex: 1; }
  table { width: 100%; border-collapse: collapse; }
  td, th { vertical-align: top; padding: 4px 6px; }
  .head-title { font-weight: 700; font-size: 22px; letter-spacing: 6px; padding: 0 0 10px; text-align: center; text-decoration: none; }
  .header { border-bottom: 2px solid #000; }
  .header .top { display: table; width: 100%; padding: 10px 12px; }
  .header .top .l { display: table-cell; width: 62%; vertical-align: middle; }
  .header .top .r { display: table-cell; width: 38%; vertical-align: middle; text-align: right; }
  .header .top img { height: 64px; }
  .header .gstline { display: table; width: 100%; border-top: 1px solid #000; font-weight: 700; }
  .header .gstline > div { display: table-cell; padding: 3px 10px; }
  .header .gstline .l { border-right: 1px solid #000; }
  .header .gstline .r { text-align: right; }

  .meta { display: table; width: 100%; border-bottom: 1px solid #000; }
  .meta .to { display: table-cell; width: 58%; padding: 8px 12px; border-right: 1px solid #000; vertical-align: top; }
  .meta .ref { display: table-cell; width: 42%; padding: 8px 12px; vertical-align: top; }
  .meta .ref table { font-size: 11px; }
  .greeting { padding: 6px 12px; border-bottom: 1px solid #000; }

  .items { width: 100%; }
  .items th, .items td { border-right: 1px solid #000; }
  .items thead tr { border-bottom: 1px solid #000; background: #f3f3f3; }
  .items thead th:last-child, .items tbody td:last-child { border-right: 0; }
  .items tbody tr { border-bottom: 1px solid rgba(0,0,0,0.4); }
  .items tfoot tr { border-top: 2px solid #000; font-weight: 700; }
  .items th { font-weight: 700; }
  .items .num { text-align: right; }
  .items .ctr { text-align: center; }

  .totals { display: table; width: 100%; border-top: 2px solid #000; }
  .totals .l { display: table-cell; width: 58%; padding: 10px 12px; border-right: 1px solid #000; vertical-align: top; }
  .totals .r { display: table-cell; width: 42%; padding: 10px 12px; vertical-align: top; }
  .totals table td.label { font-weight: 700; }
  .totals table td.val { text-align: right; font-family: 'Courier New', monospace; }
  .totals table .gtot td { border-top: 2px solid #000; padding-top: 6px; font-size: 13px; font-weight: 700; }

  .words { border-top: 2px solid #000; padding: 6px 12px; font-weight: 700; }

  .bank { display: table; width: 100%; border-top: 1px solid #000; }
  .bank .l, .bank .r { display: table-cell; padding: 8px 12px; vertical-align: top; }
  .bank .l { width: 58%; border-right: 1px solid #000; }
  .bank .r { width: 42%; min-height: 80px; }

  .terms { display: table; width: 100%; border-top: 1px solid #000; border-bottom: 1.5px solid #000; }
  .terms .l { display: table-cell; width: 58%; padding: 12px; border-right: 1px solid #000; min-height: 120px; vertical-align: top; }
  .terms .r { display: table-cell; width: 42%; padding: 12px; vertical-align: top; text-align: right; }
  .sig { margin-top: 60px; text-align: right; }
  .sig .line { border-top: 1px solid #000; margin-top: 50px; padding-top: 3px; font-size: 10px; }

  .nowrap { white-space: nowrap; }
  pre.preserved { white-space: pre-line; font-family: inherit; margin: 0; }
</style></head>
<body>
<div class="head-title">{{ doc_title }}</div>
<div class="quote">

  <div class="header">
    <div class="top">
      <div class="l">
        <div class="bold" style="font-size:14px;">{{ seller.name }}</div>
        <div>{{ seller.address }}</div>
        <div>Ph. {{ seller.phones }}</div>
        <div>E-mail :- {{ seller.email }}</div>
      </div>
      <div class="r">{% if logo_url %}<img src="{{ logo_url }}" alt="HREXPORTER">{% else %}<div class="bold" style="font-size:22px;letter-spacing:2px;">HREXPORTER</div>{% endif %}</div>
    </div>
    <div class="gstline">
      <div class="l">GSTIN No. {{ seller.gstin }}</div>
      <div class="r">PAN No. :- {{ seller.pan }}</div>
    </div>
  </div>

  <div class="meta">
    <div class="to">
      <div class="bold" style="margin-bottom:3px;">TO :</div>
      <div class="bold" style="text-transform:uppercase;">{{ q.contact_company or q.contact_name }}</div>
      {% if q.billing_address %}<pre class="preserved">{{ q.billing_address }}</pre>{% endif %}
      {% if q.place_of_supply %}<table><tr><td>State : <span class="bold" style="text-transform:uppercase;">{{ q.place_of_supply }}</span></td><td>Code : <span class="bold">{{ buyer_state_code }}</span></td></tr></table>{% endif %}
      {% if q.contact_gst %}<div>GSTIN No. : <span class="bold">{{ q.contact_gst }}</span></div>{% endif %}
      {% if q.contact_phone %}<div>Mobile No. : <span class="bold">{{ q.contact_phone }}</span></div>{% endif %}
    </div>
    <div class="ref">
      <table>
        <tr><td class="bold">Quot. No</td><td>: {{ short_quote_no }}</td></tr>
        <tr><td class="bold">Quot. Date</td><td>: {{ created_date }}</td></tr>
        <tr><td class="bold">Ref. No</td><td>:</td></tr>
        <tr><td class="bold">Date</td><td>: {{ valid_until_date }}</td></tr>
        <tr><td class="bold">Kind Attn.</td><td>: {{ q.contact_name }}</td></tr>
        <tr><td class="bold">Payment Terms</td><td>:</td></tr>
      </table>
    </div>
  </div>

  <div class="greeting">Dear Sir; We thank you very much for your inquiry as noted and are pleased to submit our most competitive offer for the same as under :</div>

  <table class="items">
    <thead><tr>
      <th style="width:5%;">Sr No</th>
      <th style="width:12%;">Item Code</th>
      <th>Description</th>
      <th class="ctr" style="width:9%;">HSN Code</th>
      <th class="num" style="width:7%;">Qty</th>
      <th class="ctr" style="width:5%;">Unit</th>
      <th class="num" style="width:9%;">Rate</th>
      <th class="num" style="width:6%;">GST (%)</th>
      <th class="num" style="width:11%;">Amount</th>
    </tr></thead>
    <tbody>
      {% for it in q.line_items %}
      <tr>
        <td>{{ loop.index }}</td>
        <td class="mono">{{ it.product_code }}</td>
        <td style="text-transform:uppercase;">{{ it.description or (it.family_name ~ (" · " ~ it.cable_size if it.cable_size else "") ~ (" · Hole " ~ it.hole_size if it.hole_size else "")) }}</td>
        <td class="ctr">{{ it.hsn_code }}</td>
        <td class="num">{{ "%.2f"|format(it.quantity|float) }}</td>
        <td class="ctr">{{ it.unit }}</td>
        <td class="num">{{ "%.2f"|format(it.base_price|float) }}</td>
        <td class="num">{{ "%.2f"|format(it.gst_percentage|float) }}</td>
        <td class="num">{{ inr_fmt(it.taxable_value if it.taxable_value is defined else (it.quantity|float * it.base_price|float)) }}</td>
      </tr>
      {% endfor %}
    </tbody>
    <tfoot>
      <tr>
        <td colspan="4">Total</td>
        <td class="num">{{ "%.2f"|format(total_qty) }}</td>
        <td colspan="4"></td>
      </tr>
    </tfoot>
  </table>

  <div class="totals">
    <div class="l">
      <table>
        <tr><td class="label">Taxable Amt.</td><td class="val">{{ inr_fmt(q.taxable_value) }}</td></tr>
        {% if is_interstate %}
        <tr><td class="label">IGST {{ gst_rate }}%</td><td class="val">{{ inr_fmt(q.total_gst) }}</td></tr>
        {% else %}
        <tr><td class="label">CGST {{ "%.1f"|format(gst_rate / 2) }}%</td><td class="val">{{ inr_fmt(q.total_gst / 2) }}</td></tr>
        <tr><td class="label">SGST {{ "%.1f"|format(gst_rate / 2) }}%</td><td class="val">{{ inr_fmt(q.total_gst / 2) }}</td></tr>
        {% endif %}
        {% if (q.total_discount or 0) > 0 %}
        <tr><td class="label">Discount</td><td class="val">- {{ inr_fmt(q.total_discount) }}</td></tr>
        {% endif %}
      </table>
    </div>
    <div class="r">
      <table>
        <tr><td class="label">Amount Before Tax</td><td class="val">{{ inr_fmt(q.taxable_value) }}</td></tr>
        {% if is_interstate %}
        <tr><td class="label">IGST Amt.</td><td class="val">{{ inr_fmt(q.total_gst) }}</td></tr>
        {% else %}
        <tr><td class="label">CGST Amt.</td><td class="val">{{ inr_fmt(q.total_gst / 2) }}</td></tr>
        <tr><td class="label">SGST Amt.</td><td class="val">{{ inr_fmt(q.total_gst / 2) }}</td></tr>
        {% endif %}
        <tr class="gtot"><td class="label">G. Total Amount</td><td class="val">{{ inr_fmt(q.grand_total) }}</td></tr>
      </table>
    </div>
  </div>

  <div class="words">RUPEES : {{ words }}</div>

  <div class="bank">
    <div class="l">
      <div class="bold underline" style="margin-bottom:3px;">Bank Details :</div>
      <table>
        <tr><td><span class="bold">Bank Name :</span> {{ seller.bank_name }}</td><td><span class="bold">A/c. No. :</span> {{ seller.bank_account }}</td></tr>
        <tr><td><span class="bold">IFSC Code :</span> {{ seller.bank_ifsc }}</td><td><span class="bold">Branch :</span> {{ seller.bank_branch }}</td></tr>
      </table>
    </div>
    <div class="r">
      <div class="bold underline" style="margin-bottom:3px;">Remark :</div>
      <pre class="preserved">{{ q.notes or "" }}</pre>
    </div>
  </div>

  <div class="terms">
    <div class="l">
      <div class="bold underline" style="margin-bottom:3px;">Terms &amp; Conditions :</div>
      <pre class="preserved">{{ q.terms or "" }}</pre>
    </div>
    <div class="r">
      <div class="bold">E &amp; O.E.</div>
      <div class="sig">
        <div class="bold" style="text-transform:uppercase;">For, {{ seller.name }}</div>
        {% if doc_title|upper == "QUOTATION" %}
        <div style="margin-top:60px; font-size:10px; font-style:italic; color:#333;">
          This is a system-generated quotation.<br>No signature is required.
        </div>
        {% else %}
        <div class="line">(Authorized Signatory)</div>
        {% endif %}
      </div>
    </div>
  </div>

</div></body></html>"""


def _format_date_dmy(iso: str) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%d-%m-%y")
    except Exception:
        return iso


def _inr_fmt(n: float) -> str:
    n = float(n or 0)
    # Indian-style grouping for the integer part
    sign = "-" if n < 0 else ""
    n = abs(n)
    int_part, frac = f"{n:.2f}".split(".")
    if len(int_part) <= 3:
        grouped = int_part
    else:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        # Add commas every 2 digits in `rest`
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        grouped = ",".join(groups) + "," + last3
    return f"{sign}{grouped}.{frac}"


def render_quote_pdf(quote: Dict[str, Any], output_path: Path, logo_url: str | None = None, doc_title: str = "QUOTATION", meta_labels: Optional[Dict[str, str]] = None) -> Path:
    """Render the supplied quote/PI/invoice dict to a PDF at output_path.
    `doc_title` is shown in the header (e.g. "QUOTATION", "PROFORMA INVOICE").
    `meta_labels` overrides the Quot.No/Quot.Date labels (e.g. PI No/PI Date)."""
    env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html", "xml"]))
    env.globals["inr_fmt"] = _inr_fmt
    tpl = env.from_string(_TEMPLATE)

    buyer_state = (quote.get("place_of_supply") or "").strip().upper()
    # Per business rule: any state OTHER than the seller's (Gujarat) — including
    # empty/unknown — is treated as inter-state → IGST. Intra-Gujarat → CGST+SGST.
    is_interstate = buyer_state != SELLER["state"]
    line_items = quote.get("line_items") or []
    total_qty = sum(float(it.get("quantity") or 0) for it in line_items)
    gst_rate = float((line_items[0].get("gst_percentage") if line_items else 18) or 18)

    short_match = quote.get("quote_number", "")
    # Extract trailing digits for "Quot. No" field
    import re as _re
    m = _re.search(r"(\d+)(?:-R\d+)?$", short_match)
    short_quote_no = m.group(1) if m else short_match

    html_str = tpl.render(
        q=quote,
        seller=SELLER,
        buyer_state_code=STATE_CODE_MAP.get(buyer_state, ""),
        is_interstate=is_interstate,
        gst_rate=gst_rate,
        total_qty=total_qty,
        words=number_to_words_inr(quote.get("grand_total") or 0),
        created_date=_format_date_dmy(quote.get("created_at") or ""),
        valid_until_date=_format_date_dmy(quote.get("valid_until") or ""),
        short_quote_no=short_quote_no,
        logo_url=logo_url,
        doc_title=doc_title,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(output_path.parent)).write_pdf(str(output_path))
    return output_path
