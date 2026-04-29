import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import api, { fileUrl } from "@/lib/api";
import { ArrowRight, Image as ImageIcon, MagnifyingGlass } from "@phosphor-icons/react";

export default function PublicCatalogue() {
  const [data, setData] = useState({ families: [], materials: [], categories: [] });
  const [q, setQ] = useState("");
  const [matFilter, setMatFilter] = useState("");

  useEffect(() => {
    api.get("/public/catalogue").then((r) => setData(r.data));
  }, []);

  const matName = (id) => data.materials.find((m) => m.id === id)?.material_name || "";

  const filtered = useMemo(() => {
    return data.families.filter((f) => {
      if (matFilter && f.material_id !== matFilter) return false;
      if (q) {
        const s = q.toLowerCase();
        return f.family_name?.toLowerCase().includes(s) || f.short_name?.toLowerCase().includes(s) || f.product_type?.toLowerCase().includes(s);
      }
      return true;
    });
  }, [data, q, matFilter]);

  return (
    <div className="animate-fade-in">
      {/* Hero */}
      <div className="bg-[#1A1A1A] text-white relative overflow-hidden">
        <div className="absolute inset-0 opacity-[0.05]" style={{
          backgroundImage: 'linear-gradient(#FBAE17 1px, transparent 1px), linear-gradient(90deg, #FBAE17 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }} />
        <div className="max-w-7xl mx-auto px-6 py-16 relative z-10">
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-4">Catalogue</div>
          <h1 className="font-heading font-black text-5xl md:text-6xl tracking-tight max-w-3xl">
            Industrial cable terminations,<br />
            <span className="bg-[#FBAE17] text-black px-2">precision engineered.</span>
          </h1>
          <p className="text-zinc-300 mt-6 max-w-xl">
            Browse our complete range of copper and aluminium lugs, ferrules, and connectors. Build a quote, verify your business via OTP, and receive pricing instantly.
          </p>
          <div className="mt-8 flex flex-wrap gap-6 text-xs">
            <Stat label="Materials" value={data.materials.length} />
            <Stat label="Product Families" value={data.families.length} />
            <Stat label="Categories" value={data.categories.length} />
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="border-b border-zinc-200 bg-white sticky top-[73px] z-20">
        <div className="max-w-7xl mx-auto px-6 py-4 flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[260px]">
            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search families, ring lugs, pin terminals…"
              className="w-full border border-zinc-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
              data-testid="public-catalogue-search"
            />
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setMatFilter("")}
              className={`text-xs uppercase tracking-wider font-bold px-3 py-2 border transition-colors ${matFilter === "" ? 'bg-[#FBAE17] border-[#FBAE17] text-black' : 'border-zinc-300 hover:border-[#FBAE17]'}`}
              data-testid="public-mat-filter-all"
            >All</button>
            {data.materials.map((m) => (
              <button
                key={m.id}
                onClick={() => setMatFilter(m.id)}
                className={`text-xs uppercase tracking-wider font-bold px-3 py-2 border transition-colors ${matFilter === m.id ? 'bg-[#FBAE17] border-[#FBAE17] text-black' : 'border-zinc-300 hover:border-[#FBAE17]'}`}
                data-testid={`public-mat-filter-${m.id}`}
              >{m.material_name}</button>
            ))}
          </div>
          <Link
            to="/request-quote"
            className="ml-auto bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2"
            data-testid="public-build-quote-cta"
          >
            Build Quote <ArrowRight size={14} weight="bold" />
          </Link>
        </div>
      </div>

      {/* Grid */}
      <div className="max-w-7xl mx-auto px-6 py-8 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
        {filtered.map((f) => (
          <Link
            key={f.id}
            to={`/catalogue/${f.id}`}
            className="border border-zinc-200 bg-white flex flex-col group hover:border-[#FBAE17] transition-colors"
            data-testid={`public-family-card-${f.id}`}
          >
            <div className="aspect-square bg-zinc-50 border-b border-zinc-200 relative flex items-center justify-center overflow-hidden">
              {f.main_product_image ? (
                <img src={fileUrl(f.main_product_image)} alt={f.family_name} className="w-full h-full object-contain p-3" />
              ) : (
                <div className="text-zinc-300 flex flex-col items-center gap-1">
                  <ImageIcon size={28} weight="thin" />
                </div>
              )}
              <span className="absolute top-2 right-2 text-[9px] uppercase tracking-wider font-bold bg-[#FBAE17] text-black px-1.5 py-0.5">{matName(f.material_id)}</span>
            </div>
            <div className="p-3 flex-1 flex flex-col">
              <div className="text-[9px] uppercase tracking-[0.18em] text-zinc-500 font-bold mb-1 truncate">{f.product_type || "Family"}</div>
              <h3 className="font-heading font-black text-xs text-[#1A1A1A] leading-snug line-clamp-3" title={f.family_name}>{f.family_name}</h3>
            </div>
          </Link>
        ))}
        {!filtered.length && <div className="col-span-full text-zinc-400 text-sm py-12 text-center">No families match your search.</div>}
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="border-l-2 border-[#FBAE17] pl-3">
      <div className="font-heading font-black text-2xl">{value || '—'}</div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-bold">{label}</div>
    </div>
  );
}
