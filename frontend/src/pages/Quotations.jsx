import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Plus, MagnifyingGlass, FileText, Funnel } from "@phosphor-icons/react";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";

export default function Quotations() {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const navigate = useNavigate();

  const load = async () => {
    const params = {};
    if (q) params.q = q;
    if (statusFilter) params.status_filter = statusFilter;
    const r = await api.get("/quotations", { params });
    setItems(r.data);
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [q, statusFilter]);
  useEffect(() => { api.get("/dashboard/quote-stats").then((r) => setStats(r.data)); }, []);

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="CRM"
        title="Quotations"
        subtitle="Build, send, revise and track quotes across the entire customer pipeline."
        testId="quotations-header"
        actions={
          <button
            onClick={() => navigate("/quotations/new")}
            data-testid="quotations-add-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2"
          >
            <Plus size={16} weight="bold" /> New Quotation
          </button>
        }
      />

      <div className="p-8 space-y-4">
        {/* Mini stats row */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Stat label="Drafts" value={stats.counts?.draft || 0} />
            <Stat label="Sent" value={stats.counts?.sent || 0} accent />
            <Stat label="Approved" value={stats.counts?.approved || 0} good />
            <Stat label="Pipeline ₹" value={inr(stats.pipeline_value)} mono />
            <Stat label="Won ₹" value={inr(stats.won_value)} mono good />
          </div>
        )}

        {/* Filters */}
        <div className="border border-zinc-200 bg-white p-4 grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <div className="md:col-span-3">
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Search</label>
            <div className="relative">
              <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)} className="w-full border border-zinc-300 pl-9 pr-3 py-2 text-sm" placeholder="Quote number, contact, company" data-testid="quotations-search" />
            </div>
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Status</label>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="quotations-status-filter">
              <option value="">All</option>
              <option value="draft">Draft</option>
              <option value="sent">Sent</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="revised">Revised</option>
              <option value="expired">Expired</option>
            </select>
          </div>
        </div>

        {/* Table */}
        <div className="border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold border-b-2 border-zinc-200">
                <th className="px-6 py-3">Quote No.</th>
                <th className="px-6 py-3">Customer</th>
                <th className="px-6 py-3">Date</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Lines</th>
                <th className="px-6 py-3 text-right">Total ₹</th>
              </tr>
            </thead>
            <tbody>
              {items.map((q) => (
                <tr key={q.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`quote-row-${q.id}`}>
                  <td className="px-6 py-3 font-mono font-bold">
                    <Link to={`/quotations/${q.id}`} className="hover:text-[#FBAE17]" data-testid={`quote-link-${q.id}`}>{q.quote_number}</Link>
                  </td>
                  <td className="px-6 py-3">
                    <div className="font-medium text-[#1A1A1A]">{q.contact_name}</div>
                    {q.contact_company && <div className="text-xs text-zinc-500">{q.contact_company}</div>}
                  </td>
                  <td className="px-6 py-3 text-xs font-mono text-zinc-500">{new Date(q.created_at).toLocaleDateString()}</td>
                  <td className="px-6 py-3"><QuoteStatusBadge status={q.status} /></td>
                  <td className="px-6 py-3 text-right font-mono">{(q.line_items || []).length}</td>
                  <td className="px-6 py-3 text-right font-mono font-bold text-[#1A1A1A]">₹{(q.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                </tr>
              ))}
              {!items.length && (
                <tr><td colSpan={6} className="px-6 py-12 text-center text-zinc-400">
                  <FileText size={32} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  No quotations yet. Click <strong>New Quotation</strong> to create one.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, accent, good, mono }) {
  return (
    <div className={`border bg-white p-4 ${accent ? 'border-[#FBAE17]' : good ? 'border-emerald-200' : 'border-zinc-200'}`}>
      <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500">{label}</div>
      <div className={`font-heading font-black text-2xl mt-1 ${good ? 'text-emerald-600' : accent ? 'text-[#1A1A1A]' : 'text-[#1A1A1A]'} ${mono ? 'font-mono text-lg' : ''}`}>{value}</div>
    </div>
  );
}

function inr(v) {
  return "₹" + (v || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
