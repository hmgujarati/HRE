import { useEffect, useState } from "react";
import api from "@/lib/api";
import PageHeader from "@/components/PageHeader";

export default function PriceHistory() {
  const [items, setItems] = useState([]);
  const [variants, setVariants] = useState({});

  useEffect(() => {
    Promise.all([api.get("/price-history?limit=500"), api.get("/product-variants")]).then(([h, v]) => {
      setItems(h.data);
      const map = {};
      v.data.forEach((x) => { map[x.id] = x; });
      setVariants(map);
    });
  }, []);

  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow="Audit" title="Price History" subtitle="Complete log of all price/discount changes across the catalogue." testId="ph-header" />
      <div className="p-8">
        <div className="border border-zinc-200 bg-white overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                <th className="px-4 py-3">When</th>
                <th className="px-4 py-3">Variant</th>
                <th className="px-4 py-3">By</th>
                <th className="px-4 py-3">Base ₹</th>
                <th className="px-4 py-3">Discount</th>
                <th className="px-4 py-3">Manual</th>
                <th className="px-4 py-3">Final ₹</th>
                <th className="px-4 py-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {items.map((h) => {
                const v = variants[h.product_variant_id];
                return (
                  <tr key={h.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`ph-row-${h.id}`}>
                    <td className="px-4 py-3 text-xs font-mono text-zinc-500 whitespace-nowrap">{new Date(h.changed_at).toLocaleString()}</td>
                    <td className="px-4 py-3 font-mono font-bold text-xs">{v?.product_code || h.product_variant_id?.slice(0, 8)}</td>
                    <td className="px-4 py-3 text-xs">{h.changed_by}</td>
                    <td className="px-4 py-3 font-mono text-xs">₹{h.old_base_price ?? '—'} → ₹{h.new_base_price}</td>
                    <td className="px-4 py-3 font-mono text-xs">{h.old_discount_percentage ?? 0}% → {h.new_discount_percentage}%</td>
                    <td className="px-4 py-3 font-mono text-xs">{h.old_manual_price_override ? `₹${h.old_manual_price}` : '—'} → {h.new_manual_price_override ? `₹${h.new_manual_price}` : '—'}</td>
                    <td className="px-4 py-3 font-mono text-xs font-bold">₹{h.old_final_price ?? '—'} → ₹{h.new_final_price}</td>
                    <td className="px-4 py-3 text-xs text-zinc-600">{h.change_reason}</td>
                  </tr>
                );
              })}
              {!items.length && <tr><td colSpan={8} className="px-6 py-12 text-center text-zinc-400">No history yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
