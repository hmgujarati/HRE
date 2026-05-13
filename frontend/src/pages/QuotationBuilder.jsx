import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams, Link } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { ArrowLeft, Plus, Trash, Check, FloppyDisk } from "@phosphor-icons/react";
import { toast } from "sonner";
import ContactPicker from "@/components/ContactPicker";
import VariantSearchPicker from "@/components/VariantSearchPicker";
import StateSelect from "@/components/StateSelect";

export default function QuotationBuilder() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const [contact, setContact] = useState(null);
  const [items, setItems] = useState([]);
  const [notes, setNotes] = useState("");
  const [terms, setTerms] = useState("Prices are exclusive of freight unless specified.\nValidity: 30 days.\nPayment: 50% advance, 50% before dispatch.");
  const [validUntil, setValidUntil] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 30);
    return d.toISOString().slice(0, 10);
  });
  const [placeOfSupply, setPlaceOfSupply] = useState("");
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // Load existing quote if editing, or pre-fill from ?contact=
  useEffect(() => {
    (async () => {
      if (id) {
        const { data } = await api.get(`/quotations/${id}`);
        if (data.status === "approved" || data.status === "rejected") {
          toast.error("Cannot edit a finalised quote — use Revise.");
          navigate(`/quotations/${id}`);
          return;
        }
        const c = await api.get(`/contacts/${data.contact_id}`);
        setContact(c.data);
        setItems(data.line_items || []);
        setNotes(data.notes || "");
        setTerms(data.terms || "");
        setPlaceOfSupply(data.place_of_supply || "");
        setValidUntil(data.valid_until || "");
      } else {
        const cid = search.get("contact");
        if (cid) {
          try {
            const c = await api.get(`/contacts/${cid}`);
            setContact(c.data);
          } catch {}
        }
        const p = await api.get("/quotations/next-number");
        setPreview(p.data.preview);
      }
      setLoaded(true);
    })();
    // eslint-disable-next-line
  }, [id]);

  const addLine = (v) => {
    setItems((prev) => [
      ...prev,
      {
        product_variant_id: v.id,
        product_code: v.product_code,
        family_name: "",
        description: "",
        cable_size: v.cable_size,
        hole_size: v.hole_size,
        dimensions: v.dimensions || {},
        hsn_code: v.hsn_code,
        quantity: v.minimum_order_quantity || 1,
        unit: v.unit || "NOS",
        base_price: Number(v.final_price),
        discount_percentage: 0,
        gst_percentage: Number(v.gst_percentage || 18),
      },
    ]);
  };

  const removeLine = (idx) => setItems(items.filter((_, i) => i !== idx));
  const setLine = (idx, patch) => setItems(items.map((it, i) => (i === idx ? { ...it, ...patch } : it)));

  const totals = useMemo(() => {
    let subtotal = 0, totalDiscount = 0, totalGst = 0;
    items.forEach((it) => {
      const qty = Number(it.quantity || 0);
      const base = Number(it.base_price || 0);
      const disc = Number(it.discount_percentage || 0);
      const gst = Number(it.gst_percentage || 0);
      const gross = qty * base;
      const da = gross * disc / 100;
      const taxable = gross - da;
      const ga = taxable * gst / 100;
      subtotal += gross;
      totalDiscount += da;
      totalGst += ga;
    });
    const taxable = subtotal - totalDiscount;
    return {
      subtotal: round(subtotal),
      total_discount: round(totalDiscount),
      taxable_value: round(taxable),
      total_gst: round(totalGst),
      grand_total: round(taxable + totalGst),
    };
  }, [items]);

  const save = async (newStatus) => {
    if (!contact) { toast.error("Select a contact first"); return; }
    if (!items.length) { toast.error("Add at least one line item"); return; }
    setBusy(true);
    try {
      const payload = {
        contact_id: contact.id,
        place_of_supply: placeOfSupply,
        valid_until: validUntil,
        notes,
        terms,
        line_items: items.map((it) => ({
          product_variant_id: it.product_variant_id,
          product_code: it.product_code,
          family_name: it.family_name,
          description: it.description,
          cable_size: it.cable_size,
          hole_size: it.hole_size,
          dimensions: it.dimensions || {},
          hsn_code: it.hsn_code,
          quantity: Number(it.quantity || 0),
          unit: it.unit,
          base_price: Number(it.base_price || 0),
          discount_percentage: Number(it.discount_percentage || 0),
          gst_percentage: Number(it.gst_percentage || 0),
        })),
      };
      let saved;
      if (id) {
        const r = await api.put(`/quotations/${id}`, payload);
        saved = r.data;
      } else {
        const r = await api.post("/quotations", payload);
        saved = r.data;
      }
      if (newStatus && newStatus !== saved.status) {
        const r = await api.patch(`/quotations/${saved.id}/status`, { status: newStatus });
        saved = r.data;
      }
      toast.success(`Quote ${saved.quote_number} saved`);
      navigate(`/quotations/${saved.id}`);
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  if (!loaded) return <div className="p-8 text-zinc-400">Loading…</div>;

  return (
    <div className="animate-fade-in pb-32">
      <PageHeader
        eyebrow={id ? "Edit Quotation" : "New Quotation"}
        title={id ? `Editing Quote` : (preview || "Building Quote")}
        subtitle="Choose a customer, add line items from the catalogue, and finalise pricing."
        testId="quote-builder-header"
        actions={
          <Link to="/quotations" className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2">
            <ArrowLeft size={14} weight="bold" /> Back
          </Link>
        }
      />

      <div className="p-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          {/* Contact */}
          <div className="border border-zinc-200 bg-white p-6">
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-3">Customer</div>
            <ContactPicker value={contact} onPick={setContact} />
          </div>

          {/* Line items */}
          <div className="border border-zinc-200 bg-white">
            <div className="px-6 py-4 border-b border-zinc-200">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Line Items</div>
              <h3 className="font-heading font-black text-lg mb-3">Add products</h3>
              <VariantSearchPicker onPick={addLine} />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-zinc-50">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">Code</th>
                    <th className="px-3 py-2">Description</th>
                    <th className="px-3 py-2 text-right">Qty</th>
                    <th className="px-3 py-2 text-right">Rate ₹</th>
                    <th className="px-3 py-2 text-right">Disc %</th>
                    <th className="px-3 py-2 text-right">GST %</th>
                    <th className="px-3 py-2 text-right">Total ₹</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, idx) => {
                    const qty = Number(it.quantity || 0);
                    const base = Number(it.base_price || 0);
                    const disc = Number(it.discount_percentage || 0);
                    const gst = Number(it.gst_percentage || 0);
                    const gross = qty * base;
                    const da = gross * disc / 100;
                    const taxable = gross - da;
                    const total = taxable + (taxable * gst / 100);
                    return (
                      <tr key={idx} className="border-t border-zinc-100" data-testid={`quote-line-${idx}`}>
                        <td className="px-3 py-2 font-mono text-zinc-400">{idx + 1}</td>
                        <td className="px-3 py-2 font-mono font-bold">{it.product_code}</td>
                        <td className="px-3 py-2">
                          <input value={it.description || ""} onChange={(e) => setLine(idx, { description: e.target.value })} className="w-full border border-zinc-200 px-2 py-1 text-xs" placeholder={`${it.cable_size} ${it.hole_size ? '· hole ' + it.hole_size : ''}`} />
                        </td>
                        <td className="px-3 py-2 text-right">
                          <input type="number" step="any" value={it.quantity} onChange={(e) => setLine(idx, { quantity: e.target.value })} className="w-20 border border-zinc-200 px-2 py-1 text-xs font-mono text-right" data-testid={`qty-${idx}`} />
                        </td>
                        <td className="px-3 py-2 text-right">
                          <input type="number" step="0.01" value={it.base_price} onChange={(e) => setLine(idx, { base_price: e.target.value })} className="w-24 border border-zinc-200 px-2 py-1 text-xs font-mono text-right" />
                        </td>
                        <td className="px-3 py-2 text-right">
                          <input type="number" step="0.01" value={it.discount_percentage} onChange={(e) => setLine(idx, { discount_percentage: e.target.value })} className="w-16 border border-zinc-200 px-2 py-1 text-xs font-mono text-right" />
                        </td>
                        <td className="px-3 py-2 text-right">
                          <input type="number" step="0.01" value={it.gst_percentage} onChange={(e) => setLine(idx, { gst_percentage: e.target.value })} className="w-16 border border-zinc-200 px-2 py-1 text-xs font-mono text-right" />
                        </td>
                        <td className="px-3 py-2 text-right font-mono font-bold">₹{total.toFixed(2)}</td>
                        <td className="px-3 py-2 text-right">
                          <button onClick={() => removeLine(idx)} className="text-zinc-400 hover:text-red-600" data-testid={`remove-line-${idx}`}><Trash size={14} /></button>
                        </td>
                      </tr>
                    );
                  })}
                  {!items.length && (
                    <tr><td colSpan={9} className="px-6 py-8 text-center text-zinc-400">No items yet — search above to add.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Notes & terms */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="border border-zinc-200 bg-white p-6">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Internal Notes</div>
              <textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm" data-testid="quote-notes" />
            </div>
            <div className="border border-zinc-200 bg-white p-6">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Terms & Conditions</div>
              <textarea rows={4} value={terms} onChange={(e) => setTerms(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm" data-testid="quote-terms" />
            </div>
          </div>
        </div>

        {/* Totals */}
        <div className="lg:col-span-1">
          <div className="border-2 border-[#FBAE17] bg-white sticky top-6">
            <div className="px-6 py-4 border-b border-zinc-200 bg-[#1A1A1A] text-white">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Quote Total</div>
              <div className="font-heading font-black text-3xl">₹{totals.grand_total.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            </div>
            <div className="p-6 space-y-3 text-sm">
              <Row label="Subtotal" value={totals.subtotal} />
              <Row label="Discount" value={-totals.total_discount} muted />
              <Row label="Taxable Value" value={totals.taxable_value} />
              <Row label="GST" value={totals.total_gst} muted />
              <div className="border-t border-zinc-200 pt-3">
                <Row label="Grand Total" value={totals.grand_total} bold />
              </div>
            </div>

            <div className="px-6 pb-6 space-y-3">
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Valid Until</label>
                <input type="date" value={validUntil || ""} onChange={(e) => setValidUntil(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" data-testid="quote-valid-until" />
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Place of Supply</label>
                <StateSelect value={placeOfSupply} onChange={setPlaceOfSupply} placeholder={contact?.state || "Select state"} data-testid="quote-place-of-supply" />
                <div className="text-[10px] text-zinc-500 mt-1">Used for GST: <span className="font-bold">Gujarat → CGST + SGST</span>, others → IGST.</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Sticky footer actions */}
      <div className="fixed bottom-0 left-64 right-0 bg-white border-t border-zinc-200 px-8 py-4 flex items-center justify-end gap-2 z-30">
        <button onClick={() => save("draft")} disabled={busy} className="px-5 py-3 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2 disabled:opacity-60" data-testid="quote-save-draft-btn">
          <FloppyDisk size={14} weight="bold" /> Save as Draft
        </button>
        <button onClick={() => save("sent")} disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60" data-testid="quote-save-send-btn">
          <Check size={14} weight="bold" /> Save & Mark Sent
        </button>
      </div>
    </div>
  );
}

function Row({ label, value, bold, muted }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className={`text-xs uppercase tracking-wider font-bold ${muted ? 'text-zinc-500' : 'text-zinc-700'}`}>{label}</span>
      <span className={`font-mono ${bold ? 'font-bold text-lg text-[#1A1A1A]' : 'text-sm text-[#1A1A1A]'}`}>₹{Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
    </div>
  );
}

function round(n) { return Math.round((Number(n) || 0) * 100) / 100; }
