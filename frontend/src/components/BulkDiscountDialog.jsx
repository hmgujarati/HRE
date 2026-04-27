import { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { X, Check, Lightning } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function BulkDiscountDialog({ onClose, onApplied, mats, cats, families }) {
  const [scope, setScope] = useState("material");
  const [targetId, setTargetId] = useState("");
  const [discount, setDiscount] = useState(5);
  const [reason, setReason] = useState("");
  const [count, setCount] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => { setTargetId(""); setCount(null); }, [scope]);

  useEffect(() => {
    if (!targetId) { setCount(null); return; }
    api.post("/pricing/bulk-discount/preview", { scope, target_id: targetId })
      .then((r) => setCount(r.data.count))
      .catch(() => setCount(null));
  }, [scope, targetId]);

  const apply = async () => {
    setBusy(true);
    try {
      const url = scope === "material" ? "/pricing/bulk-discount/material"
        : scope === "category" ? "/pricing/bulk-discount/category"
        : "/pricing/bulk-discount/product-family";
      const { data } = await api.post(url, {
        target_id: targetId,
        discount_percentage: Number(discount),
        change_reason: reason || `Bulk ${scope} discount`,
      });
      toast.success(`Updated ${data.updated_count} variants`);
      onApplied && onApplied();
      onClose();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const targetOptions = scope === "material" ? mats.map((m) => ({ id: m.id, label: m.material_name }))
    : scope === "category" ? cats.map((c) => ({ id: c.id, label: c.category_name + (c.parent_category_id ? " (sub)" : "") }))
    : families.map((f) => ({ id: f.id, label: f.family_name }));

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="bg-white w-full max-w-lg border border-zinc-200" data-testid="bulk-discount-dialog">
        <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Lightning size={18} weight="fill" className="text-[#FBAE17]" />
            <h3 className="font-heading font-black text-lg">Bulk Discount Update</h3>
          </div>
          <button onClick={onClose}><X size={20} /></button>
        </div>
        {!confirming ? (
          <div className="p-6 space-y-4">
            <div>
              <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Apply To</label>
              <div className="grid grid-cols-3 gap-2" data-testid="bulk-scope-tabs">
                {[["material", "Material"], ["category", "Category"], ["product_family", "Family"]].map(([k, l]) => (
                  <button key={k} onClick={() => setScope(k)} className={`py-2 px-3 text-xs uppercase tracking-wider font-bold border ${scope === k ? 'border-[#FBAE17] bg-[#FBAE17] text-black' : 'border-zinc-300 text-zinc-700 hover:bg-zinc-50'}`} data-testid={`bulk-scope-${k}`}>{l}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Target *</label>
              <select value={targetId} onChange={(e) => setTargetId(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="bulk-target-select">
                <option value="">Select…</option>
                {targetOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Discount % *</label>
              <input type="number" step="0.01" value={discount} onChange={(e) => setDiscount(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" data-testid="bulk-discount-input" />
            </div>
            <div>
              <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Reason</label>
              <input value={reason} onChange={(e) => setReason(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm" placeholder="Q1 promo / cost adjustment" />
            </div>
            {count !== null && (
              <div className="bg-zinc-50 border border-zinc-200 px-4 py-3 text-sm" data-testid="bulk-preview-count">
                This will update <span className="font-bold">{count}</span> product variants.
              </div>
            )}
          </div>
        ) : (
          <div className="p-6 space-y-3">
            <div className="bg-amber-50 border border-amber-200 text-amber-900 px-4 py-3 text-sm">
              <strong>Confirm bulk update.</strong> This will set discount to <strong>{discount}%</strong> on <strong>{count}</strong> variants and recalculate final prices. Each change will be recorded in price history.
            </div>
          </div>
        )}
        <div className="px-6 py-4 border-t border-zinc-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
          {!confirming ? (
            <button disabled={!targetId || count === 0 || count === null} onClick={() => setConfirming(true)} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-50" data-testid="bulk-next-btn">
              Continue
            </button>
          ) : (
            <button disabled={busy} onClick={apply} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60" data-testid="bulk-apply-btn">
              <Check size={14} weight="bold" /> {busy ? "Applying…" : "Confirm & Apply"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
