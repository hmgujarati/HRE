import { useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { ArrowLeft, PencilSimple, Printer, ArrowsClockwise, Check, X, PaperPlaneTilt } from "@phosphor-icons/react";
import { toast } from "sonner";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";

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
    if (!window.confirm("Create a new revision (draft) of this quote? Original will be marked Revised.")) return;
    try {
      const r = await api.post(`/quotations/${id}/revise`);
      toast.success(`Revision ${r.data.quote_number} created`);
      navigate(`/quotations/${r.data.id}/edit`);
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  if (!quote) return <div className="p-8 text-zinc-400">Loading…</div>;

  const canEdit = !["approved", "rejected"].includes(quote.status);

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={quote.contact_company || quote.contact_name}
        title={quote.quote_number}
        subtitle={`Created ${new Date(quote.created_at).toLocaleString()} by ${quote.created_by || ''}`}
        testId="quote-view-header"
        actions={
          <div className="flex items-center gap-2 print:hidden">
            <Link to="/quotations" className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2">
              <ArrowLeft size={14} weight="bold" /> Back
            </Link>
            <button onClick={() => window.print()} className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2" data-testid="quote-print-btn">
              <Printer size={14} weight="bold" /> Print / PDF
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

      {/* Printable area */}
      <div className="p-8 print:p-0">
        <div className="max-w-4xl mx-auto bg-white border border-zinc-200 print:border-0" id="printable-quote">
          {/* Header */}
          <div className="px-8 py-6 border-b-2 border-[#1A1A1A] flex items-start justify-between gap-6">
            <div>
              <img src="/hre-logo-light-bg.png" alt="HREXPORTER" className="h-16 object-contain" />
              <div className="mt-2 text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500">An ISO 9001 Company</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17]">Quotation</div>
              <div className="font-heading font-black text-2xl text-[#1A1A1A] mt-1">{quote.quote_number}</div>
              <div className="text-xs text-zinc-500 mt-1 font-mono">Date: {new Date(quote.created_at).toLocaleDateString()}</div>
              {quote.valid_until && <div className="text-xs text-zinc-500 font-mono">Valid Until: {quote.valid_until}</div>}
            </div>
          </div>

          {/* Bill to / Ship to */}
          <div className="px-8 py-5 grid grid-cols-2 gap-8 border-b border-zinc-200">
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Bill To</div>
              <div className="font-bold text-sm">{quote.contact_name}</div>
              {quote.contact_company && <div className="text-sm">{quote.contact_company}</div>}
              <div className="text-xs whitespace-pre-line text-zinc-600 mt-1">{quote.billing_address}</div>
              {quote.contact_gst && <div className="text-xs font-mono text-zinc-700 mt-1">GST: {quote.contact_gst}</div>}
              {quote.contact_phone && <div className="text-xs font-mono text-zinc-600">{quote.contact_phone}</div>}
              {quote.contact_email && <div className="text-xs font-mono text-zinc-600">{quote.contact_email}</div>}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Ship To</div>
              <div className="text-xs whitespace-pre-line text-zinc-700">{quote.shipping_address || quote.billing_address || <span className="text-zinc-400">Same as billing</span>}</div>
              {quote.place_of_supply && <div className="text-xs text-zinc-500 mt-2">Place of Supply: <span className="text-zinc-800 font-medium">{quote.place_of_supply}</span></div>}
            </div>
          </div>

          {/* Line items */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-zinc-50">
                <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold border-b-2 border-zinc-300">
                  <th className="px-4 py-3">#</th>
                  <th className="px-4 py-3">Code</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3">HSN</th>
                  <th className="px-4 py-3 text-right">Qty</th>
                  <th className="px-4 py-3 text-right">Rate ₹</th>
                  <th className="px-4 py-3 text-right">Disc</th>
                  <th className="px-4 py-3 text-right">GST</th>
                  <th className="px-4 py-3 text-right">Total ₹</th>
                </tr>
              </thead>
              <tbody>
                {(quote.line_items || []).map((it, i) => (
                  <tr key={i} className="border-b border-zinc-100" data-testid={`quote-view-line-${i}`}>
                    <td className="px-4 py-3 font-mono text-zinc-400">{i + 1}</td>
                    <td className="px-4 py-3 font-mono font-bold">{it.product_code}</td>
                    <td className="px-4 py-3">
                      <div>{it.description || `${it.cable_size}${it.hole_size ? ` · hole ${it.hole_size}` : ''}`}</div>
                    </td>
                    <td className="px-4 py-3 font-mono text-zinc-600">{it.hsn_code}</td>
                    <td className="px-4 py-3 text-right font-mono">{it.quantity} {it.unit}</td>
                    <td className="px-4 py-3 text-right font-mono">₹{Number(it.base_price).toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono">{it.discount_percentage}%</td>
                    <td className="px-4 py-3 text-right font-mono">{it.gst_percentage}%</td>
                    <td className="px-4 py-3 text-right font-mono font-bold">₹{Number(it.line_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Totals */}
          <div className="px-8 py-5 grid grid-cols-2 gap-8 border-t-2 border-zinc-300">
            <div>
              {quote.notes && (
                <div>
                  <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Notes</div>
                  <div className="text-xs whitespace-pre-line text-zinc-700">{quote.notes}</div>
                </div>
              )}
              {quote.terms && (
                <div className="mt-4">
                  <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Terms & Conditions</div>
                  <div className="text-xs whitespace-pre-line text-zinc-700">{quote.terms}</div>
                </div>
              )}
            </div>
            <div className="border border-zinc-200">
              <SumRow label="Subtotal" value={quote.subtotal} />
              <SumRow label="Discount" value={-quote.total_discount} />
              <SumRow label="Taxable Value" value={quote.taxable_value} />
              <SumRow label="Total GST" value={quote.total_gst} />
              <SumRow label="Grand Total" value={quote.grand_total} bold />
            </div>
          </div>

          {/* Footer signature */}
          <div className="px-8 py-6 border-t border-zinc-200 grid grid-cols-2 gap-8">
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500 mb-1">Customer Acceptance</div>
              <div className="border-b border-zinc-400 mt-12 mb-1"></div>
              <div className="text-[10px] text-zinc-500">Signature & Seal</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500 mb-1">For HREXPORTER</div>
              <div className="border-b border-zinc-400 mt-12 mb-1"></div>
              <div className="text-[10px] text-zinc-500">Authorised Signatory</div>
            </div>
          </div>

          <div className="px-8 py-3 bg-[#1A1A1A] text-white text-center text-[10px] uppercase tracking-[0.22em] font-bold">
            Thank you for your business · HREXPORTER · ISO 9001 Certified
          </div>
        </div>
      </div>

      <style>{`
        @media print {
          body { background: white !important; }
          .sidebar-dark, [data-testid="sidebar"] { display: none !important; }
          main { margin-left: 0 !important; }
          [data-testid="quote-view-header"], .print\\:hidden { display: none !important; }
          #printable-quote { box-shadow: none !important; border: 0 !important; max-width: 100% !important; }
        }
      `}</style>
    </div>
  );
}

function SumRow({ label, value, bold }) {
  return (
    <div className={`flex justify-between px-4 py-2 ${bold ? 'bg-[#FBAE17] text-black border-t-2 border-[#1A1A1A]' : 'border-b border-zinc-200'}`}>
      <span className={`text-xs uppercase tracking-wider font-bold ${bold ? '' : 'text-zinc-700'}`}>{label}</span>
      <span className={`font-mono ${bold ? 'font-black text-base' : 'text-sm'}`}>₹{Math.abs(Number(value || 0)).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
    </div>
  );
}
