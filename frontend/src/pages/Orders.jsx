import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MagnifyingGlass, Storefront } from "@phosphor-icons/react";
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

  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow="Operations" title="Orders" subtitle="Track every approved order from PO → Dispatch → LR." testId="orders-header" />

      <div className="px-4 sm:px-8 py-4">
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
                <th className="px-4 sm:px-6 py-3">Updated</th>
                <th className="px-4 sm:px-6 py-3 text-right">Total ₹</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="px-6 py-12 text-center text-zinc-400">Loading…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={7} className="px-6 py-16 text-center text-zinc-400">
                  <Storefront size={36} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  <div className="font-heading font-black text-lg mb-1 text-zinc-600">No orders yet</div>
                  <div className="text-xs">Approve a quotation, then click <strong>Convert to Order</strong> to start tracking.</div>
                </td></tr>
              ) : items.map((o) => (
                <tr key={o.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`order-row-${o.id}`}>
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
                  <td className="px-4 sm:px-6 py-3 text-xs font-mono text-zinc-500">{new Date(o.updated_at).toLocaleDateString()}</td>
                  <td className="px-4 sm:px-6 py-3 text-right font-mono font-bold">₹{(o.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
