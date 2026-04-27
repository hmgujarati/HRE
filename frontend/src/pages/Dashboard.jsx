import { useEffect, useState } from "react";
import api from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Stack, Package, Wrench, Folders, ClockCounterClockwise, ArrowRight, Cube } from "@phosphor-icons/react";
import { Link } from "react-router-dom";

function StatCard({ label, value, icon: Icon, accent }) {
  return (
    <div className="border border-zinc-200 bg-white p-6 flex flex-col gap-4 hover:border-[#FBAE17] transition-colors" data-testid={`stat-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="flex items-center justify-between">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-zinc-500">{label}</div>
        <div className={`w-8 h-8 flex items-center justify-center ${accent ? 'bg-[#FBAE17] text-black' : 'bg-zinc-100 text-zinc-700'}`}>
          <Icon size={16} weight="bold" />
        </div>
      </div>
      <div className="font-heading font-black text-4xl text-[#1A1A1A] tracking-tight">{value}</div>
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.get("/dashboard/stats").then((r) => setStats(r.data));
  }, []);

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="Overview"
        title="Catalogue Dashboard"
        subtitle="Real-time snapshot of materials, product families, variants, and pricing activity."
        testId="dashboard-header"
      />

      <div className="p-8 space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4">
          <StatCard label="Product Families" value={stats?.total_families ?? '—'} icon={Stack} accent />
          <StatCard label="Total Variants" value={stats?.total_variants ?? '—'} icon={Package} />
          <StatCard label="Active Variants" value={stats?.active_variants ?? '—'} icon={Cube} />
          <StatCard label="Categories" value={stats?.total_categories ?? '—'} icon={Folders} />
          <StatCard label="Materials" value={Object.keys(stats?.material_counts || {}).length || '—'} icon={Wrench} />
        </div>

        {/* Material breakdown */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1 border border-zinc-200 bg-white p-6">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-[#FBAE17] mb-1">By Material</div>
            <h3 className="font-heading font-black text-xl mb-4">Variants per Material</h3>
            <div className="space-y-3">
              {Object.entries(stats?.material_counts || {}).map(([name, count]) => (
                <div key={name} className="flex items-center justify-between py-2 border-b border-zinc-100" data-testid={`mat-count-${name.toLowerCase()}`}>
                  <span className="text-sm font-medium text-zinc-800">{name}</span>
                  <span className="font-mono text-sm bg-zinc-100 px-2 py-0.5">{count}</span>
                </div>
              ))}
              {!stats && <div className="text-sm text-zinc-400">Loading…</div>}
            </div>
          </div>

          {/* Recent families */}
          <div className="lg:col-span-2 border border-zinc-200 bg-white p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-[#FBAE17] mb-1">Recently Added</div>
                <h3 className="font-heading font-black text-xl">Product Families</h3>
              </div>
              <Link to="/product-families" className="text-xs uppercase tracking-wider font-bold text-zinc-700 hover:text-[#FBAE17] flex items-center gap-1">
                View all <ArrowRight size={14} weight="bold" />
              </Link>
            </div>
            <div className="divide-y divide-zinc-100">
              {(stats?.recent_families || []).map((f) => (
                <Link
                  key={f.id}
                  to={`/product-families/${f.id}`}
                  className="flex items-start justify-between gap-4 py-3 hover:bg-zinc-50 px-2 -mx-2 transition-colors"
                  data-testid={`recent-family-${f.id}`}
                >
                  <div className="min-w-0">
                    <div className="font-medium text-sm text-[#1A1A1A] truncate">{f.family_name}</div>
                    <div className="text-xs text-zinc-500 mt-0.5">{f.short_name || f.product_type}</div>
                  </div>
                  <ArrowRight size={14} weight="bold" className="text-zinc-400 mt-1" />
                </Link>
              ))}
              {!stats?.recent_families?.length && <div className="text-sm text-zinc-400 py-4">No families yet.</div>}
            </div>
          </div>
        </div>

        {/* Recent price changes */}
        <div className="border border-zinc-200 bg-white">
          <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ClockCounterClockwise size={18} weight="bold" className="text-[#FBAE17]" />
              <h3 className="font-heading font-black text-lg">Recent Price Changes</h3>
            </div>
            <Link to="/price-history" className="text-xs uppercase tracking-wider font-bold text-zinc-700 hover:text-[#FBAE17]">View History</Link>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50">
                <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                  <th className="px-6 py-3">Variant</th>
                  <th className="px-6 py-3">Old → New Price</th>
                  <th className="px-6 py-3">Discount</th>
                  <th className="px-6 py-3">Changed By</th>
                  <th className="px-6 py-3">When</th>
                </tr>
              </thead>
              <tbody>
                {(stats?.recent_price_changes || []).map((c) => (
                  <tr key={c.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`price-change-${c.id}`}>
                    <td className="px-6 py-3 font-mono text-xs text-zinc-700">{c.product_variant_id?.slice(0, 8)}…</td>
                    <td className="px-6 py-3">
                      <span className="font-mono">₹{c.old_final_price ?? '—'}</span>
                      <span className="mx-2 text-zinc-400">→</span>
                      <span className="font-mono font-bold text-[#1A1A1A]">₹{c.new_final_price}</span>
                    </td>
                    <td className="px-6 py-3 font-mono text-xs">{c.new_discount_percentage ?? 0}%</td>
                    <td className="px-6 py-3 text-xs text-zinc-600">{c.changed_by}</td>
                    <td className="px-6 py-3 text-xs text-zinc-500 font-mono">{new Date(c.changed_at).toLocaleString()}</td>
                  </tr>
                ))}
                {!stats?.recent_price_changes?.length && (
                  <tr><td colSpan={5} className="px-6 py-6 text-sm text-zinc-400 text-center">No price changes yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
