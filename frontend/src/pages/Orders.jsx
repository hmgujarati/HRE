import { useEffect, useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { MagnifyingGlass, Storefront, Calendar, Warning, DotsThreeVertical, Trash } from "@phosphor-icons/react";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { toast } from "sonner";

export const STAGE_LABELS = {
  pending_po: "Awaiting PO",
  po_received: "PO Received",
  proforma_issued: "PI Issued",
  order_placed: "Order Placed",
  raw_material_check: "Raw Material Check",
  procuring_raw_material: "Procuring RM",
  in_production: "In Production",
  packaging: "Packaging",
  dispatched: "Dispatched",
  lr_received: "LR Received",
  delivered: "Delivered",
};

export const STAGE_COLORS = {
  pending_po: "bg-zinc-100 text-zinc-700",
  po_received: "bg-blue-100 text-blue-800",
  proforma_issued: "bg-indigo-100 text-indigo-800",
  order_placed: "bg-amber-100 text-amber-800",
  raw_material_check: "bg-amber-100 text-amber-800",
  procuring_raw_material: "bg-orange-100 text-orange-800",
  in_production: "bg-yellow-100 text-yellow-800",
  packaging: "bg-purple-100 text-purple-800",
  dispatched: "bg-cyan-100 text-cyan-800",
  lr_received: "bg-teal-100 text-teal-800",
  delivered: "bg-emerald-100 text-emerald-800",
};

export const STAGE_ORDER = [
  "pending_po", "po_received", "proforma_issued", "order_placed",
  "raw_material_check", "procuring_raw_material", "in_production",
  "packaging", "dispatched", "lr_received", "delivered",
];

export function StageBadge({ stage, size = "sm" }) {
  const cls = STAGE_COLORS[stage] || "bg-zinc-100 text-zinc-700";
  const label = STAGE_LABELS[stage] || stage;
  const pad = size === "xs" ? "px-1.5 py-0.5 text-[9px]" : "px-2.5 py-1 text-[10px]";
  return <span className={`inline-flex items-center font-bold uppercase tracking-wider ${pad} ${cls}`}>{label}</span>;
}

export default function Orders() {
  const [items, setItems] = useState([]);
  const [q, setQ] = useState("");
  const [stage, setStage] = useState("");
  const [loading, setLoading] = useState(true);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const params = {};
      if (q) params.q = q;
      if (stage) params.stage = stage;
      const { data } = await api.get("/orders", { params });
      setItems(data);
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [stage]);  // eslint-disable-line
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q]);  // eslint-disable-line

  // Close the row menu on outside click / Esc
  useEffect(() => {
    const close = () => setOpenMenuId(null);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("click", close); document.removeEventListener("keydown", onKey); };
  }, []);

  const doDelete = async () => {
    if (!confirmDelete) return;
    try {
      await api.delete(`/orders/${confirmDelete.id}`);
      toast.success(`Order ${confirmDelete.order_number} deleted`);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  // In-flight = anything past pending_po and before delivered
  const inFlightWithoutEta = useMemo(() => {
    return items.filter((o) => {
      const idx = STAGE_ORDER.indexOf(o.stage);
      const isInFlight = idx > 0 && idx < STAGE_ORDER.indexOf("delivered");
      return isInFlight && !o.expected_completion_date;
    });
  }, [items]);

  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow="Operations" title="Orders" subtitle="Track every approved order from PO → Dispatch → LR." testId="orders-header" />

      <div className="px-4 sm:px-8 py-4">
        {inFlightWithoutEta.length > 0 && (
          <div className="mb-4 border border-amber-300 bg-amber-50 p-4 flex items-start gap-3" data-testid="missing-eta-banner">
            <Warning size={18} weight="fill" className="text-amber-600 shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="font-bold text-sm text-amber-900">
                {inFlightWithoutEta.length} in-flight order{inFlightWithoutEta.length === 1 ? "" : "s"} {inFlightWithoutEta.length === 1 ? "is" : "are"} missing an Expected Completion Date
              </div>
              <div className="text-xs text-amber-800 mt-0.5">
                Customers won't see an ETA in their WhatsApp + Email updates until you set this. Click an order below (yellow row) and use the "Expected Completion" card.
              </div>
            </div>
          </div>
        )}

        <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-6">
          <div className="relative flex-1 sm:max-w-md">
            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search order #, customer, PO #, quote #…"
              className="w-full border border-zinc-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
              data-testid="orders-search"
            />
          </div>
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            data-testid="orders-stage-filter"
            className="border border-zinc-300 px-3 py-2 text-sm font-bold uppercase tracking-wider focus:outline-none focus:border-[#FBAE17]"
          >
            <option value="">All stages</option>
            {STAGE_ORDER.map((s) => <option key={s} value={s}>{STAGE_LABELS[s]}</option>)}
          </select>
        </div>

        <div className="border border-zinc-200 bg-white overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold border-b-2 border-zinc-200">
                <th className="px-4 sm:px-6 py-3">Order #</th>
                <th className="px-4 sm:px-6 py-3">Customer</th>
                <th className="px-4 sm:px-6 py-3">Quote</th>
                <th className="px-4 sm:px-6 py-3">PO #</th>
                <th className="px-4 sm:px-6 py-3">Stage</th>
                <th className="px-4 sm:px-6 py-3">ETA</th>
                <th className="px-4 sm:px-6 py-3">Updated</th>
                <th className="px-4 sm:px-6 py-3 text-right">Total ₹</th>
                <th className="px-4 sm:px-6 py-3 text-right w-12"></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={9} className="px-6 py-12 text-center text-zinc-400">Loading…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={9} className="px-6 py-16 text-center text-zinc-400">
                  <Storefront size={36} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  <div className="font-heading font-black text-lg mb-1 text-zinc-600">No orders yet</div>
                  <div className="text-xs">Approve a quotation, then click <strong>Convert to Order</strong> to start tracking.</div>
                </td></tr>
              ) : items.map((o) => {
                const idx = STAGE_ORDER.indexOf(o.stage);
                const isInFlight = idx > 0 && idx < STAGE_ORDER.indexOf("delivered");
                const missingEta = isInFlight && !o.expected_completion_date;
                const etaDisplay = o.expected_completion_date
                  ? new Date(o.expected_completion_date + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" })
                  : "";
                return (
                  <tr key={o.id} className={`border-t border-zinc-100 hover:bg-zinc-50/60 ${missingEta ? "bg-amber-50/40" : ""}`} data-testid={`order-row-${o.id}`}>
                    <td className="px-4 sm:px-6 py-3 font-mono font-bold">
                      <Link to={`/orders/${o.id}`} className="hover:text-[#FBAE17]" data-testid={`order-link-${o.id}`}>{o.order_number}</Link>
                    </td>
                    <td className="px-4 sm:px-6 py-3">
                      <div className="font-medium text-[#1A1A1A]">{o.contact_name}</div>
                      {o.contact_company && <div className="text-xs text-zinc-500">{o.contact_company}</div>}
                    </td>
                    <td className="px-4 sm:px-6 py-3 font-mono text-xs text-zinc-500">{o.quote_number || "—"}</td>
                    <td className="px-4 sm:px-6 py-3 font-mono text-xs">{o.po_number || <span className="text-zinc-300">—</span>}</td>
                    <td className="px-4 sm:px-6 py-3"><StageBadge stage={o.stage} /></td>
                    <td className="px-4 sm:px-6 py-3 text-xs">
                      {etaDisplay ? (
                        <span className="inline-flex items-center gap-1 font-mono font-bold text-emerald-700">
                          <Calendar size={11} weight="bold" /> {etaDisplay}
                        </span>
                      ) : missingEta ? (
                        <Link to={`/orders/${o.id}`} className="inline-flex items-center gap-1 text-amber-700 hover:text-amber-900 font-bold text-[10px] uppercase tracking-wider" data-testid={`eta-missing-${o.id}`}>
                          <Warning size={11} weight="fill" /> Set ETA
                        </Link>
                      ) : (
                        <span className="text-zinc-300">—</span>
                      )}
                    </td>
                    <td className="px-4 sm:px-6 py-3 text-xs font-mono text-zinc-500">{new Date(o.updated_at).toLocaleDateString()}</td>
                    <td className="px-4 sm:px-6 py-3 text-right font-mono font-bold">₹{(o.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className="px-4 sm:px-6 py-3 text-right relative">
                      <button
                        onClick={(e) => { e.stopPropagation(); setOpenMenuId(openMenuId === o.id ? null : o.id); }}
                        className="text-zinc-500 hover:text-[#1A1A1A] p-1"
                        data-testid={`order-menu-${o.id}`}
                        aria-label="Row actions"
                      >
                        <DotsThreeVertical size={16} weight="bold" />
                      </button>
                      {openMenuId === o.id && (
                        <div
                          onClick={(e) => e.stopPropagation()}
                          className="absolute right-4 top-9 z-20 w-44 bg-white border border-zinc-200 shadow-lg text-left"
                          data-testid={`order-menu-popover-${o.id}`}
                        >
                          <button
                            onClick={() => { setOpenMenuId(null); setConfirmDelete(o); }}
                            data-testid={`order-delete-${o.id}`}
                            className="w-full px-3 py-2 text-xs font-bold uppercase tracking-wider hover:bg-red-50 text-red-600 flex items-center gap-2"
                          >
                            <Trash size={14} /> Delete order
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Delete confirmation modal */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setConfirmDelete(null)} data-testid="delete-order-modal">
          <div className="bg-white border border-zinc-200 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-zinc-200">
              <div className="font-heading font-black text-lg flex items-center gap-2 text-red-600"><Trash size={18} weight="bold" /> Delete order?</div>
            </div>
            <div className="px-5 py-4 space-y-3">
              <p className="text-sm text-zinc-700">
                Are you sure you want to delete <span className="font-mono font-bold">{confirmDelete.order_number}</span>?
                <br /><span className="text-xs text-zinc-500">
                  This cannot be undone. All uploaded files (PO, Proforma, Invoice, LR) and the production timeline will be lost.
                </span>
              </p>
            </div>
            <div className="px-5 py-4 border-t border-zinc-200 flex justify-end gap-2">
              <button onClick={() => setConfirmDelete(null)} data-testid="delete-order-cancel" className="px-4 py-2 text-xs font-bold uppercase tracking-wider border border-zinc-300 hover:bg-zinc-50">Cancel</button>
              <button onClick={doDelete} data-testid="delete-order-confirm" className="px-4 py-2 text-xs font-bold uppercase tracking-wider bg-red-600 hover:bg-red-700 text-white flex items-center gap-2">
                <Trash size={12} weight="bold" /> Delete forever
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
