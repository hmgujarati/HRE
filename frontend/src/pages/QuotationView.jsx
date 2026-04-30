import { useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { ArrowLeft, PencilSimple, Printer, ArrowsClockwise, Check, X, PaperPlaneTilt, WhatsappLogo } from "@phosphor-icons/react";
import { toast } from "sonner";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";
import { numberToWordsINR } from "@/lib/numberToWords";

// Seller details (HREXPORTER) — to become editable in Settings later
const SELLER = {
  name: "HREXPORTER",
  address: "BLOCK NO 201, BHATGAM ROAD, BHATGAM, OLPAD, SURAT, 394540",
  phones: "+91 9033135768, +91 8980004416 (Guj. Ind)",
  email: "info@hrexporter.com",
  gstin: "24ENVPS1624A1ZZ",
  pan: "ENVPS1624A",
  state: "GUJARAT",
  state_code: "24",
  bank: {
    name: "ICICI BANK",
    account: "183705501244",
    ifsc: "ICIC0001837",
    branch: "L P SAVANI ROAD BRANCH, SURAT.",
  },
};

const stateCodeMap = {
  "ANDHRA PRADESH": "37", "ARUNACHAL PRADESH": "12", "ASSAM": "18", "BIHAR": "10",
  "CHHATTISGARH": "22", "DELHI": "07", "GOA": "30", "GUJARAT": "24", "HARYANA": "06",
  "HIMACHAL PRADESH": "02", "JAMMU AND KASHMIR": "01", "JHARKHAND": "20", "KARNATAKA": "29",
  "KERALA": "32", "MADHYA PRADESH": "23", "MAHARASHTRA": "27", "MANIPUR": "14",
  "MEGHALAYA": "17", "MIZORAM": "15", "NAGALAND": "13", "ODISHA": "21", "PUNJAB": "03",
  "RAJASTHAN": "08", "SIKKIM": "11", "TAMIL NADU": "33", "TELANGANA": "36", "TRIPURA": "16",
  "UTTAR PRADESH": "09", "UTTARAKHAND": "05", "WEST BENGAL": "19",
  "ANDAMAN AND NICOBAR ISLANDS": "35", "CHANDIGARH": "04", "DADRA AND NAGAR HAVELI": "26",
  "DAMAN AND DIU": "25", "LADAKH": "38", "LAKSHADWEEP": "31", "PUDUCHERRY": "34",
};

function inr(n) {
  return Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yy = String(d.getFullYear()).slice(-2);
  return `${dd}-${mm}-${yy}`;
}

export default function QuotationView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [quote, setQuote] = useState(null);

  const load = async () => {
    const r = await api.get(`/quotations/${id}`);
    setQuote(r.data);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  const setStatus = async (status) => {
    try {
      const r = await api.patch(`/quotations/${id}/status`, { status });
      setQuote(r.data);
      toast.success(`Marked as ${status}`);
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  const revise = async () => {
    if (!window.confirm("Create a new revision (draft)? Original will be marked Revised.")) return;
    try {
      const r = await api.post(`/quotations/${id}/revise`);
      toast.success(`Revision ${r.data.quote_number} created`);
      navigate(`/quotations/${r.data.id}/edit`);
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  const dispatch = async () => {
    if (!window.confirm("Generate the PDF and send it to the customer via WhatsApp + Email?")) return;
    try {
      toast.loading("Dispatching…", { id: "dispatch" });
      const r = await api.post(`/quotations/${id}/send`);
      toast.dismiss("dispatch");
      const d = r.data;
      const msgs = [];
      if (d.whatsapp) msgs.push("WhatsApp ✓");
      if (d.email) msgs.push("Email ✓");
      if (Object.keys(d.errors || {}).length) {
        const errs = Object.entries(d.errors).map(([k, v]) => `${k}: ${v}`).join(" · ");
        toast.error(`Sent: ${msgs.join(" + ") || "nothing"} — Errors: ${errs}`);
      } else if (msgs.length) {
        toast.success(`Quote dispatched (${msgs.join(" + ")})`);
        load();
      } else {
        toast("PDF generated but no channel was configured to dispatch.", { icon: "ℹ️" });
      }
    } catch (e) {
      toast.dismiss("dispatch");
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  if (!quote) return <div className="p-8 text-zinc-400">Loading…</div>;

  const canEdit = !["approved", "rejected"].includes(quote.status);
  const buyerStateUpper = (quote.place_of_supply || "").trim().toUpperCase();
  const buyerStateCode = stateCodeMap[buyerStateUpper] || "";
  const isInterstate = buyerStateUpper && buyerStateUpper !== SELLER.state;
  const totalQty = (quote.line_items || []).reduce((s, it) => s + Number(it.quantity || 0), 0);
  const grandWords = numberToWordsINR(quote.grand_total || 0);
  const gstRate = (quote.line_items || [])[0]?.gst_percentage || 18;

  // Extract a short numeric quote number for the "Quot. No" field (e.g. 0009).
  // Falls back to the full quote_number if regex doesn't match.
  const shortQuoteNo = (quote.quote_number || "").match(/(\d+)(?:-R\d+)?$/);
  const quoteNoDisplay = shortQuoteNo ? shortQuoteNo[1] : (quote.quote_number || "");

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={quote.contact_company || quote.contact_name}
        title={quote.quote_number}
        subtitle={`Created ${new Date(quote.created_at).toLocaleString()} by ${quote.created_by || ""}`}
        testId="quote-view-header"
        actions={
          <div className="flex items-center gap-2 print:hidden">
            <Link to="/quotations" className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2">
              <ArrowLeft size={14} weight="bold" /> Back
            </Link>
            <button onClick={() => window.print()} className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2" data-testid="quote-print-btn">
              <Printer size={14} weight="bold" /> Print / PDF
            </button>
            <button onClick={dispatch} className="px-4 py-2 bg-[#25D366] hover:bg-[#1FB358] text-white text-xs font-bold uppercase tracking-wider flex items-center gap-2" data-testid="quote-dispatch-btn">
              <WhatsappLogo size={14} weight="fill" /> Send to Customer
            </button>
            {canEdit && (
              <Link to={`/quotations/${id}/edit`} className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2" data-testid="quote-edit-btn">
                <PencilSimple size={14} weight="bold" /> Edit
              </Link>
            )}
            {quote.status !== "draft" && (
              <button onClick={revise} className="px-4 py-2 border border-[#FBAE17] text-[#1A1A1A] hover:bg-[#FBAE17] text-xs font-bold uppercase tracking-wider flex items-center gap-2" data-testid="quote-revise-btn">
                <ArrowsClockwise size={14} weight="bold" /> Revise
              </button>
            )}
          </div>
        }
      />

      {/* Status action bar */}
      <div className="px-8 py-4 border-b border-zinc-200 bg-zinc-50 flex items-center justify-between print:hidden">
        <div className="flex items-center gap-3">
          <span className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Status</span>
          <QuoteStatusBadge status={quote.status} />
          {quote.version > 1 && <span className="text-xs text-zinc-500">· v{quote.version}</span>}
        </div>
        <div className="flex items-center gap-2">
          {quote.status === "draft" && (
            <button onClick={() => setStatus("sent")} className="bg-blue-600 hover:bg-blue-700 text-white font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2" data-testid="quote-mark-sent-btn">
              <PaperPlaneTilt size={14} weight="bold" /> Mark as Sent
            </button>
          )}
          {quote.status === "sent" && (
            <>
              <button onClick={() => setStatus("approved")} className="bg-emerald-600 hover:bg-emerald-700 text-white font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2" data-testid="quote-mark-approved-btn">
                <Check size={14} weight="bold" /> Mark Approved
              </button>
              <button onClick={() => setStatus("rejected")} className="bg-red-600 hover:bg-red-700 text-white font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2" data-testid="quote-mark-rejected-btn">
                <X size={14} weight="bold" /> Mark Rejected
              </button>
            </>
          )}
        </div>
      </div>

      {/* Printable quotation */}
      <div className="p-8 print:p-0">
        <div className="max-w-[210mm] mx-auto bg-white border border-zinc-300 print:border-0 quote-print" id="printable-quote">
          {/* HEADER */}
          <div className="border-b-2 border-black">
            <div className="text-center pt-3 pb-1">
              <span className="font-bold text-xl tracking-[0.3em] underline">QUOTATION</span>
            </div>
            <div className="grid grid-cols-12 px-4 pb-3 gap-3">
              <div className="col-span-4 flex items-start">
                <img src="/hre-logo-light-bg.png" alt="HREXPORTER" className="h-20 object-contain" />
              </div>
              <div className="col-span-8 text-right">
                <div className="font-bold text-lg leading-tight">{SELLER.name}</div>
                <div className="text-[11px] leading-tight">{SELLER.address}</div>
                <div className="text-[11px] leading-tight">Ph. {SELLER.phones}</div>
                <div className="text-[11px] leading-tight">E-mail :- {SELLER.email}</div>
              </div>
            </div>
            <div className="border-t border-black grid grid-cols-2 text-[11px] font-bold">
              <div className="px-3 py-1 border-r border-black">GSTIN No. {SELLER.gstin}</div>
              <div className="px-3 py-1 text-right">PAN No. :- {SELLER.pan}</div>
            </div>
          </div>

          {/* QUOTE META + BILL TO + REF */}
          <div className="grid grid-cols-12 border-b border-black text-[11px]">
            <div className="col-span-7 border-r border-black p-3">
              <div className="font-bold mb-1">TO :</div>
              <div className="font-bold uppercase">{quote.contact_company || quote.contact_name}</div>
              {quote.billing_address && <div className="whitespace-pre-line">{quote.billing_address}</div>}
              {quote.place_of_supply && (
                <div className="mt-1 grid grid-cols-2 gap-2">
                  <div>State : <span className="font-bold uppercase">{quote.place_of_supply}</span></div>
                  <div>Code : <span className="font-bold">{buyerStateCode}</span></div>
                </div>
              )}
              {quote.contact_gst && <div>GSTIN No. : <span className="font-bold">{quote.contact_gst}</span></div>}
              {quote.contact_phone && <div>Mobile No. : <span className="font-bold">{quote.contact_phone}</span></div>}
            </div>
            <div className="col-span-5 p-3">
              <div className="grid grid-cols-2 gap-x-2 gap-y-1">
                <div className="font-bold">Quot. No</div><div>: {quoteNoDisplay}</div>
                <div className="font-bold">Quot. Date</div><div>: {formatDate(quote.created_at)}</div>
                <div className="font-bold">Ref. No</div><div>:</div>
                <div className="font-bold">Date</div><div>: {quote.valid_until ? formatDate(quote.valid_until) : ""}</div>
                <div className="font-bold">Kind Attn.</div><div>: {quote.contact_name}</div>
                <div className="font-bold">Payment Terms</div><div>:</div>
              </div>
            </div>
          </div>

          {/* GREETING */}
          <div className="px-4 py-2 text-[11px] border-b border-black">
            Dear Sir; We thank you very much for your inquiry as noted and are pleased to submit our most competitive offer for the same as under :
          </div>

          {/* LINE ITEMS TABLE */}
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-black bg-zinc-100">
                <th className="px-2 py-1 border-r border-black text-left w-10">Sr No</th>
                <th className="px-2 py-1 border-r border-black text-left w-28">Item Code</th>
                <th className="px-2 py-1 border-r border-black text-left">Description</th>
                <th className="px-2 py-1 border-r border-black text-center w-20">HSN Code</th>
                <th className="px-2 py-1 border-r border-black text-right w-16">Qty</th>
                <th className="px-2 py-1 border-r border-black text-center w-12">Unit</th>
                <th className="px-2 py-1 border-r border-black text-right w-20">Rate</th>
                <th className="px-2 py-1 border-r border-black text-right w-14">GST (%)</th>
                <th className="px-2 py-1 text-right w-24">Amount</th>
              </tr>
            </thead>
            <tbody>
              {(quote.line_items || []).map((it, i) => {
                const desc = it.description ||
                  [it.family_name, it.cable_size, it.hole_size && `Hole ${it.hole_size}`].filter(Boolean).join(" · ") ||
                  `${it.cable_size || ""}${it.hole_size ? " · Hole " + it.hole_size : ""}`;
                return (
                  <tr key={i} className="border-b border-black/40">
                    <td className="px-2 py-1 border-r border-black/40 align-top">{i + 1}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top font-mono">{it.product_code}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top uppercase">{desc}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top text-center">{it.hsn_code}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top text-right">{Number(it.quantity || 0).toFixed(2)}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top text-center">{it.unit}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top text-right">{Number(it.base_price || 0).toFixed(2)}</td>
                    <td className="px-2 py-1 border-r border-black/40 align-top text-right">{Number(it.gst_percentage || 0).toFixed(2)}</td>
                    <td className="px-2 py-1 align-top text-right">{inr(it.taxable_value ?? (Number(it.quantity || 0) * Number(it.base_price || 0) - (Number(it.discount_amount) || 0)))}</td>
                  </tr>
                );
              })}
              {/* Total row */}
              <tr className="border-t-2 border-black font-bold">
                <td className="px-2 py-1" colSpan={4}>Total</td>
                <td className="px-2 py-1 text-right">{totalQty.toFixed(2)}</td>
                <td colSpan={4}></td>
              </tr>
            </tbody>
          </table>

          {/* TOTALS BLOCK */}
          <div className="grid grid-cols-12 border-t-2 border-black text-[11px]">
            <div className="col-span-7 border-r border-black p-3">
              <div className="grid grid-cols-3 gap-2">
                <div className="font-bold">Taxable Amt.</div>
                <div className="col-span-2 text-right font-mono">{inr(quote.taxable_value)}</div>

                {isInterstate ? (
                  <>
                    <div className="font-bold">IGST {gstRate}%</div>
                    <div className="col-span-2 text-right font-mono">{inr(quote.total_gst)}</div>
                  </>
                ) : (
                  <>
                    <div className="font-bold">CGST {gstRate / 2}%</div>
                    <div className="col-span-2 text-right font-mono">{inr((quote.total_gst || 0) / 2)}</div>
                    <div className="font-bold">SGST {gstRate / 2}%</div>
                    <div className="col-span-2 text-right font-mono">{inr((quote.total_gst || 0) / 2)}</div>
                  </>
                )}

                {quote.total_discount > 0 && (
                  <>
                    <div className="font-bold">Discount</div>
                    <div className="col-span-2 text-right font-mono">- {inr(quote.total_discount)}</div>
                  </>
                )}
              </div>
            </div>
            <div className="col-span-5 p-3">
              <div className="grid grid-cols-2 gap-y-1">
                <div className="font-bold">Amount Before Tax</div>
                <div className="text-right font-mono">{inr(quote.taxable_value)}</div>
                {isInterstate ? (
                  <>
                    <div className="font-bold">IGST Amt.</div>
                    <div className="text-right font-mono">{inr(quote.total_gst)}</div>
                  </>
                ) : (
                  <>
                    <div className="font-bold">CGST Amt.</div>
                    <div className="text-right font-mono">{inr((quote.total_gst || 0) / 2)}</div>
                    <div className="font-bold">SGST Amt.</div>
                    <div className="text-right font-mono">{inr((quote.total_gst || 0) / 2)}</div>
                  </>
                )}
                <div className="border-t-2 border-black mt-2 pt-1 font-bold text-base">G. Total Amount</div>
                <div className="border-t-2 border-black mt-2 pt-1 text-right font-mono font-bold text-base">{inr(quote.grand_total)}</div>
              </div>
            </div>
          </div>

          {/* AMOUNT IN WORDS */}
          <div className="border-t-2 border-black px-4 py-2 text-[12px] font-bold">
            RUPEES : {grandWords}
          </div>

          {/* BANK DETAILS + REMARK */}
          <div className="border-t border-black grid grid-cols-12 text-[11px]">
            <div className="col-span-7 border-r border-black px-4 py-2">
              <div className="font-bold underline mb-1">Bank Details :</div>
              <div className="grid grid-cols-2 gap-y-0.5">
                <div><span className="font-bold">Bank Name :</span> {SELLER.bank.name}</div>
                <div><span className="font-bold">A/c. No. :</span> {SELLER.bank.account}</div>
                <div><span className="font-bold">IFSC Code :</span> {SELLER.bank.ifsc}</div>
                <div><span className="font-bold">Branch :</span> {SELLER.bank.branch}</div>
              </div>
            </div>
            <div className="col-span-5 px-4 py-2 min-h-[80px]">
              <div className="font-bold underline mb-1">Remark :</div>
              <div className="whitespace-pre-line">{quote.notes || ""}</div>
            </div>
          </div>

          {/* TERMS & SIGNATURE */}
          <div className="border-t border-black grid grid-cols-12 text-[11px]">
            <div className="col-span-7 border-r border-black p-3 min-h-[120px]">
              <div className="font-bold underline mb-1">Terms & Conditions :</div>
              <div className="whitespace-pre-line">{quote.terms || ""}</div>
            </div>
            <div className="col-span-5 p-3 flex flex-col items-end justify-between text-right">
              <div className="font-bold">E & O.E.</div>
              <div className="mt-12">
                <div className="font-bold uppercase">For, {SELLER.name}</div>
                <div className="border-t border-black mt-12 pt-1 text-[10px]">(Authorized Signatory)</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Print CSS — hide app chrome on print */}
      <style>{`
        .quote-print { font-family: 'Arial', 'Helvetica', sans-serif; color: #000; }
        @page { size: A4; margin: 10mm; }
        @media print {
          html, body { background: white !important; }
          .sidebar-dark, [data-testid="sidebar"] { display: none !important; }
          main { margin-left: 0 !important; }
          [data-testid="quote-view-header"], .print\\:hidden { display: none !important; }
          #printable-quote { box-shadow: none !important; border: 0 !important; max-width: 100% !important; }
        }
      `}</style>
    </div>
  );
}
