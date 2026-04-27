import { useEffect, useState } from "react";
import api from "@/lib/api";
import { X, ClockCounterClockwise } from "@phosphor-icons/react";

export default function PriceHistoryModal({ variantId, onClose }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    api.get(`/product-variants/${variantId}/price-history`).then((r) => setItems(r.data));
  }, [variantId]);

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="bg-white w-full max-w-2xl border border-zinc-200 max-h-[80vh] flex flex-col" data-testid="price-history-modal">
        <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClockCounterClockwise size={18} weight="bold" className="text-[#FBAE17]" />
            <h3 className="font-heading font-black text-lg">Price History</h3>
          </div>
          <button onClick={onClose}><X size={20} /></button>
        </div>
        <div className="overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 sticky top-0">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                <th className="px-4 py-3">When</th>
                <th className="px-4 py-3">By</th>
                <th className="px-4 py-3">Base ₹</th>
                <th className="px-4 py-3">Disc %</th>
                <th className="px-4 py-3">Final ₹</th>
                <th className="px-4 py-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {items.map((h) => (
                <tr key={h.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`hist-row-${h.id}`}>
                  <td className="px-4 py-3 text-xs font-mono text-zinc-500">{new Date(h.changed_at).toLocaleString()}</td>
                  <td className="px-4 py-3 text-xs">{h.changed_by}</td>
                  <td className="px-4 py-3 font-mono text-xs">₹{h.old_base_price ?? '—'} → ₹{h.new_base_price}</td>
                  <td className="px-4 py-3 font-mono text-xs">{h.old_discount_percentage ?? 0}% → {h.new_discount_percentage}%</td>
                  <td className="px-4 py-3 font-mono text-xs font-bold">₹{h.old_final_price ?? '—'} → ₹{h.new_final_price}</td>
                  <td className="px-4 py-3 text-xs text-zinc-600">{h.change_reason}</td>
                </tr>
              ))}
              {!items.length && <tr><td colSpan={6} className="px-6 py-8 text-center text-zinc-400">No history.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
