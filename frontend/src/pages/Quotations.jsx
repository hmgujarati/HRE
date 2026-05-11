import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Plus, MagnifyingGlass, FileText, Archive, ArrowCounterClockwise, Trash, DotsThreeVertical } from "@phosphor-icons/react";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";
import { DeliveryStrip } from "@/components/DeliveryPill";
import { toast } from "sonner";

export default function Quotations() {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const navigate = useNavigate();

  const load = async () => {
    const params = {};
    if (q) params.q = q;
    if (statusFilter) params.status_filter = statusFilter;
    if (showArchived) params.archived = "true";
    const r = await api.get("/quotations", { params });
    setItems(r.data);
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [q, statusFilter, showArchived]);
  useEffect(() => { api.get("/dashboard/quote-stats").then((r) => setStats(r.data)); }, []);

  // Close the row menu on outside click / Esc
  useEffect(() => {
    const close = () => setOpenMenuId(null);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("click", close); document.removeEventListener("keydown", onKey); };
  }, []);

  const doArchive = async (quote, archive) => {
    try {
      await api.post(`/quotations/${quote.id}/${archive ? "archive" : "unarchive"}`);
      toast.success(archive ? `Quote ${quote.quote_number} archived` : `Quote ${quote.quote_number} restored`);
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  const doDelete = async () => {
    if (!confirmDelete) return;
    try {
      await api.delete(`/quotations/${confirmDelete.id}`);
      toast.success(`Quote ${confirmDelete.quote_number} deleted`);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

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
        <div className="border border-zinc-200 bg-white p-4 grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
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
          <button
            onClick={() => setShowArchived((v) => !v)}
            data-testid="quotations-archived-toggle"
            className={`border px-3 py-2 text-xs uppercase tracking-wider font-bold flex items-center justify-center gap-2 transition-colors ${
              showArchived ? "border-[#FBAE17] bg-[#FBAE17]/10 text-[#1A1A1A]" : "border-zinc-300 bg-white text-zinc-600 hover:bg-zinc-50"
            }`}
          >
            <Archive size={14} weight={showArchived ? "fill" : "regular"} />
            {showArchived ? "Showing archive" : "Show archived"}
          </button>
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
                <th className="px-6 py-3">Delivery</th>
                <th className="px-6 py-3 text-right">Lines</th>
                <th className="px-6 py-3 text-right">Total ₹</th>
                <th className="px-6 py-3 text-right w-12"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((qt) => (
                <tr key={qt.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`quote-row-${qt.id}`}>
                  <td className="px-6 py-3 font-mono font-bold">
                    <Link to={`/quotations/${qt.id}`} className="hover:text-[#FBAE17]" data-testid={`quote-link-${qt.id}`}>{qt.quote_number}</Link>
                  </td>
                  <td className="px-6 py-3">
                    <div className="font-medium text-[#1A1A1A]">{qt.contact_name}</div>
                    {qt.contact_company && <div className="text-xs text-zinc-500">{qt.contact_company}</div>}
                  </td>
                  <td className="px-6 py-3 text-xs font-mono text-zinc-500">{new Date(qt.created_at).toLocaleDateString()}</td>
                  <td className="px-6 py-3"><QuoteStatusBadge status={qt.status} /></td>
                  <td className="px-6 py-3"><DeliveryStrip log={qt.dispatch_log} size="xs" /></td>
                  <td className="px-6 py-3 text-right font-mono">{(qt.line_items || []).length}</td>
                  <td className="px-6 py-3 text-right font-mono font-bold text-[#1A1A1A]">₹{(qt.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="px-6 py-3 text-right relative">
                    <button
                      onClick={(e) => { e.stopPropagation(); setOpenMenuId(openMenuId === qt.id ? null : qt.id); }}
                      className="text-zinc-500 hover:text-[#1A1A1A] p-1"
                      data-testid={`quote-menu-${qt.id}`}
                      aria-label="Row actions"
                    >
                      <DotsThreeVertical size={16} weight="bold" />
                    </button>
                    {openMenuId === qt.id && (
                      <div
                        onClick={(e) => e.stopPropagation()}
                        className="absolute right-4 top-9 z-20 w-44 bg-white border border-zinc-200 shadow-lg text-left"
                        data-testid={`quote-menu-popover-${qt.id}`}
                      >
                        {showArchived ? (
                          <button
                            onClick={() => { setOpenMenuId(null); doArchive(qt, false); }}
                            data-testid={`quote-unarchive-${qt.id}`}
                            className="w-full px-3 py-2 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2"
                          >
                            <ArrowCounterClockwise size={14} /> Unarchive
                          </button>
                        ) : (
                          <button
                            onClick={() => { setOpenMenuId(null); doArchive(qt, true); }}
                            data-testid={`quote-archive-${qt.id}`}
                            className="w-full px-3 py-2 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2"
                          >
                            <Archive size={14} /> Archive
                          </button>
                        )}
                        <button
                          onClick={() => { setOpenMenuId(null); setConfirmDelete(qt); }}
                          data-testid={`quote-delete-${qt.id}`}
                          className="w-full px-3 py-2 text-xs font-bold uppercase tracking-wider hover:bg-red-50 text-red-600 flex items-center gap-2 border-t border-zinc-100"
                        >
                          <Trash size={14} /> Delete
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {!items.length && (
                <tr><td colSpan={8} className="px-6 py-12 text-center text-zinc-400">
                  <FileText size={32} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  {showArchived
                    ? "No archived quotations."
                    : <>No quotations yet. Click <strong>New Quotation</strong> to create one.</>}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Delete confirmation modal */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setConfirmDelete(null)} data-testid="delete-quote-modal">
          <div className="bg-white border border-zinc-200 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-zinc-200">
              <div className="font-heading font-black text-lg flex items-center gap-2 text-red-600"><Trash size={18} weight="bold" /> Delete quotation?</div>
            </div>
            <div className="px-5 py-4 space-y-3">
              <p className="text-sm text-zinc-700">
                Are you sure you want to delete <span className="font-mono font-bold">{confirmDelete.quote_number}</span>?
                <br /><span className="text-xs text-zinc-500">This cannot be undone. If you only need to hide it, choose <strong>Archive</strong> instead.</span>
              </p>
            </div>
            <div className="px-5 py-4 border-t border-zinc-200 flex justify-end gap-2">
              <button onClick={() => setConfirmDelete(null)} data-testid="delete-quote-cancel" className="px-4 py-2 text-xs font-bold uppercase tracking-wider border border-zinc-300 hover:bg-zinc-50">Cancel</button>
              <button onClick={doDelete} data-testid="delete-quote-confirm" className="px-4 py-2 text-xs font-bold uppercase tracking-wider bg-red-600 hover:bg-red-700 text-white flex items-center gap-2">
                <Trash size={12} weight="bold" /> Delete forever
              </button>
            </div>
          </div>
        </div>
      )}
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
