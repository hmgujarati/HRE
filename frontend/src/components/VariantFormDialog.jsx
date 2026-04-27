import { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { X, Check, Plus, Trash } from "@phosphor-icons/react";
import { toast } from "sonner";

const empty = {
  product_family_id: "", product_code: "", product_name: "", material_id: "", category_id: "",
  subcategory_id: null, cable_size: "", hole_size: "", size: "", unit: "NOS", hsn_code: "85369090",
  gst_percentage: 18.0, base_price: 0, discount_percentage: 0, manual_price_override: false,
  manual_price: null, minimum_order_quantity: 100, dimensions: {}, notes: "", active: true,
};

export default function VariantFormDialog({ initial, existing, onClose, onSaved, mats, cats, families }) {
  const [data, setData] = useState({ ...empty, ...(initial || {}), ...(existing || {}) });
  const [busy, setBusy] = useState(false);
  const [dimRows, setDimRows] = useState(
    Object.entries(data.dimensions || {}).map(([k, v]) => ({ k, v: String(v) }))
  );

  const final = data.manual_price_override
    ? Number(data.manual_price || 0)
    : Math.round((Number(data.base_price || 0) * (1 - Number(data.discount_percentage || 0) / 100)) * 100) / 100;

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const dims = {};
      dimRows.forEach((r) => { if (r.k.trim()) dims[r.k.trim()] = r.v; });
      const payload = {
        ...data,
        dimensions: dims,
        base_price: Number(data.base_price),
        discount_percentage: Number(data.discount_percentage),
        gst_percentage: Number(data.gst_percentage),
        manual_price: data.manual_price_override ? Number(data.manual_price) : null,
        minimum_order_quantity: Number(data.minimum_order_quantity || 1),
      };
      delete payload.created_at; delete payload.updated_at; delete payload.final_price;
      if (data.id) await api.put(`/product-variants/${data.id}`, payload);
      else await api.post("/product-variants", payload);
      toast.success("Variant saved");
      onSaved && onSaved();
      onClose();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const topCats = (matId) => cats.filter((c) => c.material_id === matId && !c.parent_category_id);
  const subCats = (parentId) => cats.filter((c) => c.parent_category_id === parentId);

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6 overflow-y-auto" onClick={onClose}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={save} className="bg-white w-full max-w-3xl border border-zinc-200 my-8" data-testid="variant-form">
        <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
          <h3 className="font-heading font-black text-lg">{data.id ? "Edit Variant" : "Add Variant"}</h3>
          <button type="button" onClick={onClose}><X size={20} /></button>
        </div>
        <div className="p-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          {families && (
            <div className="md:col-span-3">
              <Label>Product Family *</Label>
              <select required value={data.product_family_id} onChange={(e) => {
                const f = families.find((x) => x.id === e.target.value);
                setData({ ...data, product_family_id: e.target.value, material_id: f?.material_id || data.material_id, category_id: f?.category_id || data.category_id, subcategory_id: f?.subcategory_id ?? data.subcategory_id });
              }} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="variant-family-select">
                <option value="">Select</option>
                {families.map((f) => <option key={f.id} value={f.id}>{f.family_name}</option>)}
              </select>
            </div>
          )}
          <div>
            <Label>Product Code *</Label>
            <input required value={data.product_code} onChange={(e) => setData({ ...data, product_code: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" data-testid="variant-code-input" />
          </div>
          <div>
            <Label>Product Name</Label>
            <input value={data.product_name || ""} onChange={(e) => setData({ ...data, product_name: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <Label>Cable Size</Label>
            <input value={data.cable_size || ""} onChange={(e) => setData({ ...data, cable_size: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" placeholder="1.5 mm²" />
          </div>
          <div>
            <Label>Hole Size</Label>
            <input value={data.hole_size || ""} onChange={(e) => setData({ ...data, hole_size: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <Label>Material *</Label>
            <select required value={data.material_id} onChange={(e) => setData({ ...data, material_id: e.target.value, category_id: "", subcategory_id: null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white">
              <option value="">Select</option>
              {mats.map((m) => <option key={m.id} value={m.id}>{m.material_name}</option>)}
            </select>
          </div>
          <div>
            <Label>Category *</Label>
            <select required value={data.category_id} onChange={(e) => setData({ ...data, category_id: e.target.value, subcategory_id: null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white">
              <option value="">Select</option>
              {topCats(data.material_id).map((c) => <option key={c.id} value={c.id}>{c.category_name}</option>)}
            </select>
          </div>
          <div>
            <Label>Subcategory</Label>
            <select value={data.subcategory_id || ""} onChange={(e) => setData({ ...data, subcategory_id: e.target.value || null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white">
              <option value="">None</option>
              {subCats(data.category_id).map((c) => <option key={c.id} value={c.id}>{c.category_name}</option>)}
            </select>
          </div>
          <div>
            <Label>Unit</Label>
            <input value={data.unit} onChange={(e) => setData({ ...data, unit: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <Label>HSN</Label>
            <input value={data.hsn_code} onChange={(e) => setData({ ...data, hsn_code: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" />
          </div>
          <div>
            <Label>GST %</Label>
            <input type="number" step="0.01" value={data.gst_percentage} onChange={(e) => setData({ ...data, gst_percentage: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" />
          </div>
          <div>
            <Label>Base Price ₹</Label>
            <input type="number" step="0.01" value={data.base_price} onChange={(e) => setData({ ...data, base_price: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" data-testid="variant-base-price" />
          </div>
          <div>
            <Label>Discount %</Label>
            <input type="number" step="0.01" value={data.discount_percentage} onChange={(e) => setData({ ...data, discount_percentage: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" data-testid="variant-discount" />
          </div>
          <div>
            <Label>MOQ</Label>
            <input type="number" value={data.minimum_order_quantity} onChange={(e) => setData({ ...data, minimum_order_quantity: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" />
          </div>

          <div className="md:col-span-3 border-t border-zinc-200 pt-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={data.manual_price_override} onChange={(e) => setData({ ...data, manual_price_override: e.target.checked })} data-testid="variant-manual-override" /> Manual Price Override
            </label>
            {data.manual_price_override && (
              <div className="mt-3 max-w-xs">
                <Label>Manual Price ₹</Label>
                <input type="number" step="0.01" value={data.manual_price || ""} onChange={(e) => setData({ ...data, manual_price: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" />
              </div>
            )}
          </div>

          <div className="md:col-span-3 border-t border-zinc-200 pt-4">
            <div className="flex items-center justify-between mb-2">
              <Label>Dimensions (dynamic)</Label>
              <button type="button" onClick={() => setDimRows([...dimRows, { k: "", v: "" }])} className="text-xs uppercase font-bold tracking-wider text-zinc-700 hover:text-[#FBAE17] flex items-center gap-1" data-testid="variant-add-dim-btn">
                <Plus size={14} weight="bold" /> Add Dimension
              </button>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {dimRows.map((row, i) => (
                <div key={i} className="flex items-center gap-1 border border-zinc-200 px-2 py-1">
                  <input value={row.k} onChange={(e) => { const n = [...dimRows]; n[i].k = e.target.value; setDimRows(n); }} placeholder="Key" className="w-12 text-xs font-mono outline-none" />
                  <span className="text-zinc-400">=</span>
                  <input value={row.v} onChange={(e) => { const n = [...dimRows]; n[i].v = e.target.value; setDimRows(n); }} placeholder="Value" className="flex-1 text-xs font-mono outline-none" />
                  <button type="button" onClick={() => setDimRows(dimRows.filter((_, j) => j !== i))} className="text-zinc-400 hover:text-red-600"><Trash size={12} /></button>
                </div>
              ))}
              {!dimRows.length && <div className="text-xs text-zinc-400 col-span-full">No dimensions. Click "Add Dimension" to define keys (e.g., A, B, C, D).</div>}
            </div>
          </div>

          <div className="md:col-span-3 border-t border-zinc-200 pt-4 flex items-center justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500">Computed Final Price</div>
              <div className="font-heading font-black text-2xl text-[#1A1A1A]">₹{final.toFixed(2)}</div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={data.active ?? true} onChange={(e) => setData({ ...data, active: e.target.checked })} /> Active
            </label>
          </div>
        </div>
        <div className="px-6 py-4 border-t border-zinc-200 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
          <button type="submit" disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60" data-testid="variant-save-btn">
            <Check size={16} weight="bold" /> {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Label({ children }) {
  return <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">{children}</label>;
}
