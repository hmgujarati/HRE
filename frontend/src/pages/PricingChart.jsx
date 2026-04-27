import { useEffect, useMemo, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { MagnifyingGlass, Plus, PencilSimple, Trash, ClockCounterClockwise, Lightning, Funnel } from "@phosphor-icons/react";
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
