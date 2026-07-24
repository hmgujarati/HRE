import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { MagnifyingGlass, ArrowClockwise } from "@phosphor-icons/react";

const METHOD_COLORS = {
  POST: "bg-emerald-100 text-emerald-700",
  PUT: "bg-blue-100 text-blue-700",
  PATCH: "bg-amber-100 text-amber-700",
  DELETE: "bg-red-100 text-red-700",
};

const STATUS_COLOR = (code) => {
  if (code >= 500) return "text-red-700 font-bold";
  if (code >= 400) return "text-amber-700 font-bold";
  if (code >= 300) return "text-blue-700";
  return "text-emerald-700";
};

function fmt(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${dd}/${mm}/${d.getFullYear()} ${hh}:${mi}`;
}

export default function Activity() {
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [filters, setFilters] = useState({ user_email: "", method: "", path_contains: "" });
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      const params = new URLSearchParams();
      if (filters.user_email) params.set("user_email", filters.user_email);
      if (filters.method) params.set("method", filters.method);
      if (filters.path_contains) params.set("path_contains", filters.path_contains);
      params.set("limit", "200");
      const [r, s] = await Promise.all([
        api.get(`/audit-logs?${params.toString()}`),
        api.get("/audit-logs/summary"),
      ]);
      setRows(r.data.rows);
      setSummary(s.data);
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="Activity"
        title="Audit Log"
        subtitle="Every create / edit / delete across the system — searchable."
        testId="activity-header"
        actions={
          <button
            onClick={load}
            disabled={busy}
            data-testid="activity-refresh-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2 disabled:opacity-40"
          >
            <ArrowClockwise size={14} weight="bold" /> Refresh
          </button>
        }
      />

      <div className="p-4 sm:p-8 space-y-6">
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Total Actions" value={summary.total} testId="activity-stat-total" />
            <Stat label="Today" value={summary.today} testId="activity-stat-today" />
            <Stat label="Last 7 Days" value={summary.last_7_days} testId="activity-stat-week" />
            <div className="border border-zinc-200 bg-white p-4">
              <div className="text-[10px] uppercase tracking-[0.2em] font-bold text-zinc-500">Top 7-day Users</div>
              <div className="text-xs mt-1 space-y-0.5" data-testid="activity-stat-top-users">
                {(summary.top_users_7d || []).map((u) => (
                  <div key={u.user_email} className="flex justify-between"><span className="truncate max-w-[140px]">{u.user_email}</span><span className="font-bold">{u.count}</span></div>
                ))}
                {(!summary.top_users_7d || summary.top_users_7d.length === 0) && <span className="text-zinc-400">—</span>}
              </div>
            </div>
          </div>
        )}

        <div className="border border-zinc-200 bg-white p-4 flex flex-wrap items-end gap-3">
          <FilterField label="Filter by user email" value={filters.user_email} onChange={(v) => setFilters({ ...filters, user_email: v })} placeholder="e.g. admin@hrexporter.com" testId="activity-filter-user" />
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Method</label>
            <select value={filters.method} onChange={(e) => setFilters({ ...filters, method: e.target.value })} className="border border-zinc-300 px-3 py-2 text-sm" data-testid="activity-filter-method">
              <option value="">All</option>
              <option>POST</option>
              <option>PUT</option>
              <option>PATCH</option>
              <option>DELETE</option>
            </select>
          </div>
          <FilterField label="Path contains" value={filters.path_contains} onChange={(v) => setFilters({ ...filters, path_contains: v })} placeholder="e.g. /orders" testId="activity-filter-path" />
          <button onClick={load} className="bg-[#1A1A1A] text-[#FBAE17] px-5 py-2 text-xs font-bold uppercase tracking-wider flex items-center gap-2" data-testid="activity-search-btn">
            <MagnifyingGlass size={14} weight="bold" /> Search
          </button>
        </div>

        <div className="border border-zinc-200 bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                <th className="px-4 py-3">When</th>
                <th className="px-4 py-3">Who</th>
                <th className="px-4 py-3">Method</th>
                <th className="px-4 py-3">Path</th>
                <th className="px-4 py-3">Entity</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Latency</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-zinc-100 hover:bg-zinc-50" data-testid={`activity-row-${r.id}`}>
                  <td className="px-4 py-2 font-mono text-xs whitespace-nowrap">{fmt(r.at)}</td>
                  <td className="px-4 py-2 text-xs">
                    <div className="font-bold">{r.user_email || "—"}</div>
                    {r.user_role && <div className="text-[10px] uppercase text-zinc-500">{r.user_role}</div>}
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-[10px] font-bold px-2 py-1 ${METHOD_COLORS[r.method] || "bg-zinc-100"}`}>{r.method}</span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs break-all">{r.path}</td>
                  <td className="px-4 py-2 font-mono text-[10px] text-zinc-600 break-all">{r.entity_id || "—"}</td>
                  <td className={`px-4 py-2 font-mono text-xs ${STATUS_COLOR(r.status_code)}`}>{r.status_code}</td>
                  <td className="px-4 py-2 font-mono text-xs text-zinc-500">{r.latency_ms}ms</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="px-6 py-10 text-center text-zinc-400">No activity records match your filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, testId }) {
  return (
    <div className="border border-zinc-200 bg-white p-4" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-[0.2em] font-bold text-zinc-500">{label}</div>
      <div className="text-3xl font-black text-[#1A1A1A] mt-1">{Number(value || 0).toLocaleString("en-IN")}</div>
    </div>
  );
}

function FilterField({ label, value, onChange, placeholder, testId }) {
  return (
    <div>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        data-testid={testId}
        className="border border-zinc-300 px-3 py-2 text-sm w-56 focus:outline-none focus:border-[#FBAE17]"
      />
    </div>
  );
}
