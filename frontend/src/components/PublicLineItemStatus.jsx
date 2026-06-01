import { Package, Calendar } from "@phosphor-icons/react";

const STATUS_STYLES = {
  pending:        { label: "Pending",        cls: "bg-zinc-100 text-zinc-700 border-zinc-300" },
  in_production:  { label: "In Production",  cls: "bg-amber-50 text-amber-800 border-amber-300" },
  ready:          { label: "Ready",          cls: "bg-blue-50 text-blue-800 border-blue-300" },
  packed:         { label: "Packed",         cls: "bg-indigo-50 text-indigo-800 border-indigo-300" },
  shipped:        { label: "Shipped",        cls: "bg-purple-50 text-purple-800 border-purple-300" },
  delivered:      { label: "Delivered",      cls: "bg-emerald-50 text-emerald-800 border-emerald-300" },
};

function formatDate(d) {
  if (!d) return "";
  try { return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }); }
  catch { return ""; }
}

export default function PublicLineItemStatus({ order }) {
  const lines = order?.line_status || [];
  if (!lines.length) return null;
  return (
    <div className="border border-zinc-200 bg-white" data-testid={`line-status-${order.order_number}`}>
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-zinc-200 bg-zinc-50 flex-wrap">
        <div className="flex items-center gap-2">
          <Package size={16} weight="bold" className="text-[#FBAE17]" />
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#1A1A1A]">Per-Item Tracking</div>
        </div>
        <div className="text-[10px] text-zinc-500 font-mono">{lines.length} {lines.length === 1 ? "item" : "items"}</div>
      </div>
      <div className="divide-y divide-zinc-100">
        {lines.map((li, idx) => {
          const style = STATUS_STYLES[li.qty_status] || STATUS_STYLES.pending;
          return (
            <div key={`${li.product_code || idx}-${idx}`} className="px-5 py-3 flex flex-wrap items-center gap-3" data-testid={`line-status-row-${idx}`}>
              <div className="min-w-[120px] flex-1">
                <div className="font-mono font-bold text-sm text-[#1A1A1A]">{li.product_code || `Item ${idx + 1}`}</div>
                <div className="text-[11px] text-zinc-500 mt-0.5">
                  {li.family_name || li.description}
                  {li.quantity ? <> · <span className="font-mono">{li.quantity}{li.unit ? ` ${li.unit}` : ""}</span></> : null}
                </div>
              </div>
              <span className={`inline-block text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 border ${style.cls}`}>
                {style.label}
              </span>
              <div className="text-[11px] text-zinc-700 font-mono inline-flex items-center gap-1 min-w-[120px] justify-end">
                <Calendar size={12} weight="bold" className="text-zinc-400" />
                {li.expected_dispatch_date ? formatDate(li.expected_dispatch_date) : <span className="text-zinc-400">ETA pending</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
