import { useEffect, useMemo, useRef, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { MagnifyingGlass, Plus, PencilSimple, Trash, ClockCounterClockwise, Lightning, Funnel, FileXls, CheckCircle } from "@phosphor-icons/react";
import { toast } from "sonner";
import VariantFormDialog from "@/components/VariantFormDialog";
import PriceHistoryModal from "@/components/PriceHistoryModal";
import BulkDiscountDialog from "@/components/BulkDiscountDialog";

export default function PricingChart() {
  const [variants, setVariants] = useState([]);
  const [mats, setMats] = useState([]);
  const [cats, setCats] = useState([]);
  const [families, setFamilies] = useState([]);
  const [filters, setFilters] = useState({ q: "", material_id: "", category_id: "", product_family_id: "", active: "" });
  const [edit, setEdit] = useState(null);
  const [historyId, setHistoryId] = useState(null);
  const [bulk, setBulk] = useState(false);

  const load = async () => {
    const params = {};
    Object.entries(filters).forEach(([k, v]) => { if (v !== "") params[k] = v; });
    const [v, m, c, f] = await Promise.all([
      api.get("/product-variants", { params }),
      api.get("/materials"),
      api.get("/categories"),
      api.get("/product-families"),
    ]);
    setVariants(v.data); setMats(m.data); setCats(c.data); setFamilies(f.data);
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [filters]);

  const matName = (id) => mats.find((m) => m.id === id)?.material_name || "—";
  const catName = (id) => cats.find((c) => c.id === id)?.category_name || "—";
  const famName = (id) => families.find((f) => f.id === id)?.family_name || "—";

  const dimSummary = (d) => {
    if (!d) return "";
    return Object.entries(d).slice(0, 4).map(([k, v]) => `${k}=${v}`).join(" · ");
  };

  const remove = async (v) => {
    if (!window.confirm(`Delete variant ${v.product_code}?`)) return;
    try {
      await api.delete(`/product-variants/${v.id}`);
      toast.success("Deleted");
      await load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="Catalogue"
        title="Pricing Chart"
        subtitle="All product variants with live pricing, discount and final price calculations."
        testId="pricing-header"
        actions={
          <div className="flex items-center gap-2">
            <PriceImport onDone={load} />
            <button onClick={() => setBulk(true)} data-testid="bulk-discount-btn" className="border border-[#FBAE17] text-[#1A1A1A] hover:bg-[#FBAE17] font-bold uppercase tracking-wider text-xs px-4 py-3 flex items-center gap-2 transition-colors">
              <Lightning size={14} weight="fill" /> Bulk Discount
            </button>
            <button onClick={() => setEdit({})} data-testid="pricing-add-variant-btn" className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-3 flex items-center gap-2">
              <Plus size={14} weight="bold" /> Add Variant
            </button>
          </div>
        }
      />

      <div className="p-8 space-y-4">
        {/* Filters */}
        <div className="border border-zinc-200 bg-white p-4 grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
          <div className="md:col-span-2">
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Search</label>
            <div className="relative">
              <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
              <input value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} className="w-full border border-zinc-300 pl-9 pr-3 py-2 text-sm" placeholder="Product code or name" data-testid="filter-search" />
            </div>
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Material</label>
            <select value={filters.material_id} onChange={(e) => setFilters({ ...filters, material_id: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="filter-material">
              <option value="">All</option>
              {mats.map((m) => <option key={m.id} value={m.id}>{m.material_name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Family</label>
            <select value={filters.product_family_id} onChange={(e) => setFilters({ ...filters, product_family_id: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="filter-family">
              <option value="">All</option>
              {families.map((f) => <option key={f.id} value={f.id}>{f.family_name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Status</label>
            <select value={filters.active} onChange={(e) => setFilters({ ...filters, active: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="filter-active">
              <option value="">All</option>
              <option value="true">Active</option>
              <option value="false">Inactive</option>
            </select>
          </div>
        </div>

        {/* Table */}
        <div className="border border-zinc-200 bg-white overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold border-b-2 border-zinc-200">
                <th className="px-4 py-3">Code</th>
                <th className="px-4 py-3">Family</th>
                <th className="px-4 py-3">Material</th>
                <th className="px-4 py-3">Category</th>
                <th className="px-4 py-3">Cable</th>
                <th className="px-4 py-3">Hole</th>
                <th className="px-4 py-3">Dimensions</th>
                <th className="px-4 py-3">HSN</th>
                <th className="px-4 py-3 text-right">GST</th>
                <th className="px-4 py-3 text-right">Base</th>
                <th className="px-4 py-3 text-right">Disc</th>
                <th className="px-4 py-3 text-right">Final</th>
                <th className="px-4 py-3 text-center">Active</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {variants.map((v) => (
                <tr key={v.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`variant-row-${v.id}`}>
                  <td className="px-4 py-2 font-mono font-bold text-[#1A1A1A]">{v.product_code}</td>
                  <td className="px-4 py-2 text-zinc-700 max-w-[200px] truncate">{famName(v.product_family_id)}</td>
                  <td className="px-4 py-2"><span className="text-[10px] uppercase tracking-wider font-bold bg-zinc-100 text-zinc-600 px-2 py-0.5">{matName(v.material_id)}</span></td>
                  <td className="px-4 py-2 text-zinc-600">{catName(v.subcategory_id || v.category_id)}</td>
                  <td className="px-4 py-2 font-mono">{v.cable_size}</td>
                  <td className="px-4 py-2 font-mono">{v.hole_size || '—'}</td>
                  <td className="px-4 py-2 font-mono text-zinc-500 max-w-[180px] truncate" title={JSON.stringify(v.dimensions)}>{dimSummary(v.dimensions)}</td>
                  <td className="px-4 py-2 font-mono text-zinc-500">{v.hsn_code}</td>
                  <td className="px-4 py-2 font-mono text-right">{v.gst_percentage}%</td>
                  <td className="px-4 py-2 font-mono text-right">₹{v.base_price}</td>
                  <td className="px-4 py-2 font-mono text-right">{v.discount_percentage}%</td>
                  <td className="px-4 py-2 font-mono text-right font-bold text-[#1A1A1A]">₹{v.final_price}{v.manual_price_override && <sup className="text-[#FBAE17]">*</sup>}</td>
                  <td className="px-4 py-2 text-center">
                    <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 ${v.active ? 'bg-emerald-50 text-emerald-700' : 'bg-zinc-100 text-zinc-500'}`}>{v.active ? 'Yes' : 'No'}</span>
                  </td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    <button onClick={() => setHistoryId(v.id)} className="text-zinc-500 hover:text-[#FBAE17] mr-2" title="Price history" data-testid={`variant-history-${v.id}`}><ClockCounterClockwise size={16} /></button>
                    <button onClick={() => setEdit({ ...v })} className="text-zinc-500 hover:text-[#FBAE17] mr-2" data-testid={`variant-edit-${v.id}`}><PencilSimple size={16} /></button>
                    <button onClick={() => remove(v)} className="text-zinc-400 hover:text-red-600" data-testid={`variant-delete-${v.id}`}><Trash size={16} /></button>
                  </td>
                </tr>
              ))}
              {!variants.length && <tr><td colSpan={14} className="px-6 py-12 text-center text-zinc-400">No variants found.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {edit && (
        <VariantFormDialog
          existing={edit.id ? edit : null}
          initial={!edit.id ? { material_id: mats[0]?.id || "" } : null}
          families={families}
          mats={mats}
          cats={cats}
          onClose={() => setEdit(null)}
          onSaved={load}
        />
      )}
      {historyId && <PriceHistoryModal variantId={historyId} onClose={() => setHistoryId(null)} />}
      {bulk && <BulkDiscountDialog mats={mats} cats={cats} families={families} onClose={() => setBulk(false)} onApplied={load} />}
    </div>
  );
}

function PriceImport({ onDone }) {
  const ref = useRef(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const handle = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    e.target.value = "";
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const { data } = await api.post("/pricing/upload-prices-excel", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setResult(data);
      toast.success(`Updated ${data.updated} prices`);
      onDone && onDone();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        onClick={() => ref.current?.click()}
        disabled={busy}
        data-testid="pricing-import-prices-btn"
        className="border border-zinc-300 hover:border-[#FBAE17] hover:bg-zinc-50 text-zinc-800 font-bold uppercase tracking-wider text-xs px-4 py-3 flex items-center gap-2 disabled:opacity-60 transition-colors"
        title="Bulk update prices from Excel (.xlsx)"
      >
        <FileXls size={14} weight="bold" /> {busy ? "Importing…" : "Import Prices"}
      </button>
      <input ref={ref} type="file" accept=".xlsx,.xlsm" className="hidden" onChange={handle} />
      {result && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={() => setResult(null)}>
          <div onClick={(e) => e.stopPropagation()} className="bg-white w-full max-w-lg border border-zinc-200" data-testid="price-import-result">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center gap-2">
              <CheckCircle size={18} weight="fill" className="text-emerald-600" />
              <h3 className="font-heading font-black text-lg">Price Import Complete</h3>
            </div>
            <div className="p-6 space-y-3">
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="border border-zinc-200 p-3">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Updated</div>
                  <div className="font-heading font-black text-2xl text-emerald-600">{result.updated}</div>
                </div>
                <div className="border border-zinc-200 p-3">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Not Found</div>
                  <div className="font-heading font-black text-2xl text-amber-600">{result.not_found}</div>
                </div>
                <div className="border border-zinc-200 p-3">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Skipped</div>
                  <div className="font-heading font-black text-2xl text-zinc-400">{result.skipped}</div>
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1">Price column used</div>
                <div className="text-sm font-mono bg-zinc-100 px-2 py-1 inline-block">{result.price_column || "—"}</div>
              </div>
              {result.headers_detected && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1">All Detected Columns</div>
                  <div className="flex flex-wrap gap-1">
                    {result.headers_detected.filter(Boolean).map((h, i) => (
                      <span key={i} className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 font-mono ${h === result.price_column ? 'bg-[#FBAE17] text-black' : 'bg-zinc-100 text-zinc-700'}`}>{h}</span>
                    ))}
                  </div>
                </div>
              )}
              {result.errors?.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900 max-h-32 overflow-y-auto">
                  {result.errors.map((e, i) => <div key={i}>{e}</div>)}
                </div>
              )}
              <p className="text-xs text-zinc-500 pt-2 border-t border-zinc-100">
                Tip: column headers can be Cable Size, Hole, Prod. Code, plus a price column labelled <span className="font-mono">Price</span>, <span className="font-mono">Rate</span>, <span className="font-mono">MRP</span>, <span className="font-mono">HRE</span>, or <span className="font-mono">Cost</span>. Final price recalculates from base × discount.
              </p>
            </div>
            <div className="px-6 py-3 border-t border-zinc-200 flex justify-end">
              <button onClick={() => setResult(null)} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2">Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
