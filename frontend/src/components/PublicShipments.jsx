import { Truck, FileText, CheckCircle, Clock, Receipt } from "@phosphor-icons/react";
import { toDmy } from "@/lib/dates";

const STAGE_STYLES = {
  created:    { label: "Being prepared", cls: "bg-zinc-100 text-zinc-700 border-zinc-300" },
  invoiced:   { label: "Invoiced",       cls: "bg-blue-50 text-blue-800 border-blue-300" },
  dispatched: { label: "In transit",     cls: "bg-purple-50 text-purple-800 border-purple-300" },
  delivered:  { label: "Delivered",      cls: "bg-emerald-50 text-emerald-800 border-emerald-300" },
};

function DocLink({ url, label, idx }) {
  if (!url) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider bg-zinc-50 text-zinc-400 border border-zinc-200 px-2 py-1 cursor-not-allowed">
        <FileText size={11} weight="bold" /> {label}
      </span>
    );
  }
  return (
    <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider bg-[#FBAE17]/10 text-[#1A1A1A] border border-[#FBAE17] px-2 py-1 hover:bg-[#FBAE17]" data-testid={`public-ship-doc-${label.toLowerCase().replace(/\s+/g, '-')}-${idx}`}>
      <FileText size={11} weight="bold" /> {label}
    </a>
  );
}

export default function PublicShipments({ order }) {
  const shipments = order?.shipments || [];
  if (!shipments.length) return null;
  return (
    <div className="border border-zinc-200 bg-white" data-testid={`public-shipments-${order.order_number}`}>
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-zinc-200 bg-zinc-50 flex-wrap">
        <div className="flex items-center gap-2">
          <Truck size={16} weight="bold" className="text-[#FBAE17]" />
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#1A1A1A]">Shipments</div>
        </div>
        <div className="text-[10px] text-zinc-500 font-mono">{shipments.length} {shipments.length === 1 ? "shipment" : "shipments"}</div>
      </div>
      <div className="divide-y divide-zinc-100">
        {shipments.map((s, idx) => {
          const style = STAGE_STYLES[s.stage] || STAGE_STYLES.created;
          return (
            <div key={s.id || idx} className="px-5 py-4 space-y-3" data-testid={`public-shipment-${idx}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-heading font-black text-sm">{s.shipment_number}</span>
                  <span className={`inline-block text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 border ${style.cls}`}>{style.label}</span>
                </div>
                {s.delivered_at ? (
                  <span className="text-[11px] text-emerald-700 font-mono inline-flex items-center gap-1">
                    <CheckCircle size={12} weight="fill" /> Delivered {toDmy(s.delivered_at)}
                  </span>
                ) : s.expected_delivery_date ? (
                  <span className="text-[11px] text-zinc-600 font-mono inline-flex items-center gap-1">
                    <Clock size={12} weight="bold" /> ETA {toDmy(s.expected_delivery_date)}
                  </span>
                ) : null}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1">Items</div>
                  <ul className="space-y-0.5">
                    {(s.items || []).map((li, i) => (
                      <li key={i} className="text-zinc-700">
                        <span className="font-mono font-bold">{li.product_code}</span> · {li.quantity}{li.unit ? ` ${li.unit}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1">Transport</div>
                  {s.transporter_name && <div><span className="text-zinc-500">Carrier:</span> <span className="font-mono">{s.transporter_name}</span></div>}
                  {s.lr_number && <div><span className="text-zinc-500">LR #:</span> <span className="font-mono">{s.lr_number}</span></div>}
                  {s.invoice_number && <div><span className="text-zinc-500">Invoice #:</span> <span className="font-mono">{s.invoice_number}</span></div>}
                  {s.dispatched_at && <div><span className="text-zinc-500">Dispatched:</span> <span className="font-mono">{toDmy(s.dispatched_at)}</span></div>}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <DocLink url={s.documents?.tax_invoice} label="Tax Invoice" idx={idx} />
                <DocLink url={s.documents?.eway_bill}   label="E-Way Bill"  idx={idx} />
                <DocLink url={s.documents?.lr_copy}     label="LR Copy"     idx={idx} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
