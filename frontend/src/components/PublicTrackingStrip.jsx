import { CheckCircle, Circle, Truck, Package, Factory, FileText, ClipboardText, ShieldCheck } from "@phosphor-icons/react";

const ICONS = {
  po_received: ShieldCheck,
  proforma_issued: FileText,
  in_production: Factory,
  packaging: Package,
  dispatched: Truck,
  delivered: CheckCircle,
};

function formatDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }); }
  catch { return ""; }
}

export default function PublicTrackingStrip({ order }) {
  if (!order) return null;
  const milestones = order.milestones || [];
  const currentIdx = milestones.findIndex((m) => !m.done);
  // If all done, currentIdx = -1; the "active" pulse should be the last completed
  const activeIdx = currentIdx === -1 ? milestones.length - 1 : Math.max(0, currentIdx - 1);

  return (
    <div className="border border-zinc-200 bg-white" data-testid={`tracking-strip-${order.order_number}`}>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-b border-zinc-200 bg-zinc-50">
        <div className="flex items-center gap-3">
          <ClipboardText size={18} weight="bold" className="text-[#FBAE17]" />
          <div>
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500">Order</div>
            <div className="font-mono font-bold text-sm">{order.order_number}</div>
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500">Current Stage</div>
          <div className="text-sm font-bold text-[#1A1A1A]">{order.stage_label}</div>
        </div>
      </div>

      {/* Milestones */}
      <div className="px-5 py-5">
        <div className="hidden md:flex items-start justify-between relative">
          {/* Connecting line */}
          <div className="absolute left-0 right-0 top-4 h-[2px] bg-zinc-200" />
          <div
            className="absolute left-0 top-4 h-[2px] bg-[#FBAE17] transition-all duration-500"
            style={{ width: `${(activeIdx / Math.max(1, milestones.length - 1)) * 100}%` }}
          />
          {milestones.map((m, i) => {
            const Icon = ICONS[m.key] || Circle;
            const isActive = i === activeIdx && currentIdx !== -1;
            return (
              <div key={m.key} className="relative flex flex-col items-center text-center" style={{ width: `${100 / milestones.length}%` }}>
                <div
                  className={`relative z-10 w-8 h-8 flex items-center justify-center border-2 ${
                    m.done
                      ? "bg-[#FBAE17] border-[#FBAE17] text-black"
                      : isActive
                      ? "bg-white border-[#FBAE17] text-[#FBAE17] animate-pulse"
                      : "bg-white border-zinc-300 text-zinc-300"
                  }`}
                >
                  <Icon size={14} weight={m.done ? "fill" : "bold"} />
                </div>
                <div className={`mt-2 text-[10px] uppercase tracking-wider font-bold ${m.done || isActive ? "text-[#1A1A1A]" : "text-zinc-400"}`}>
                  {m.label}
                </div>
                {m.at && <div className="text-[10px] font-mono text-zinc-500 mt-0.5">{formatDate(m.at)}</div>}
              </div>
            );
          })}
        </div>

        {/* Mobile vertical list */}
        <div className="md:hidden space-y-3">
          {milestones.map((m, i) => {
            const Icon = ICONS[m.key] || Circle;
            const isActive = i === activeIdx && currentIdx !== -1;
            return (
              <div key={m.key} className="flex items-start gap-3">
                <div
                  className={`flex-shrink-0 w-8 h-8 flex items-center justify-center border-2 ${
                    m.done
                      ? "bg-[#FBAE17] border-[#FBAE17] text-black"
                      : isActive
                      ? "bg-white border-[#FBAE17] text-[#FBAE17]"
                      : "bg-white border-zinc-300 text-zinc-300"
                  }`}
                >
                  <Icon size={14} weight={m.done ? "fill" : "bold"} />
                </div>
                <div className="flex-1">
                  <div className={`text-xs uppercase tracking-wider font-bold ${m.done || isActive ? "text-[#1A1A1A]" : "text-zinc-400"}`}>
                    {m.label}
                  </div>
                  {m.at && <div className="text-[10px] font-mono text-zinc-500 mt-0.5">{formatDate(m.at)}</div>}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Footer with key references */}
      {(order.proforma_number || order.lr_number || order.transporter_name) && (
        <div className="px-5 py-3 border-t border-zinc-200 bg-zinc-50/60 flex flex-wrap gap-x-6 gap-y-2 text-xs">
          {order.proforma_number && (
            <div>
              <span className="uppercase tracking-wider text-zinc-500 font-bold mr-2">Proforma</span>
              <span className="font-mono font-bold">{order.proforma_number}</span>
              {order.proforma_url && (
                <a href={order.proforma_url} target="_blank" rel="noreferrer" className="ml-2 text-[#FBAE17] font-bold hover:underline">View PDF</a>
              )}
            </div>
          )}
          {order.lr_number && (
            <div>
              <span className="uppercase tracking-wider text-zinc-500 font-bold mr-2">LR</span>
              <span className="font-mono font-bold">{order.lr_number}</span>
            </div>
          )}
          {order.transporter_name && (
            <div>
              <span className="uppercase tracking-wider text-zinc-500 font-bold mr-2">Transporter</span>
              <span className="font-bold">{order.transporter_name}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
