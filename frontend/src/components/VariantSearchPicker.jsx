import { useEffect, useState, useRef } from "react";
import api from "@/lib/api";
import { MagnifyingGlass, X } from "@phosphor-icons/react";

export default function VariantSearchPicker({ onPick }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!q) { setResults([]); return; }
    const t = setTimeout(async () => {
      const r = await api.get("/product-variants", { params: { q, active: true } });
      setResults(r.data.slice(0, 30));
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const click = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", click);
    return () => document.removeEventListener("mousedown", click);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <div className="relative">
        <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder="Search product code or name to add line item…"
          className="w-full border border-zinc-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
          data-testid="variant-search-input"
        />
        {q && (
          <button onClick={() => { setQ(""); setResults([]); }} className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-700">
            <X size={14} />
          </button>
        )}
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-30 left-0 right-0 mt-1 bg-white border border-zinc-300 max-h-80 overflow-y-auto shadow-lg" data-testid="variant-search-results">
          {results.map((v) => (
            <button
              key={v.id}
              type="button"
              onClick={() => { onPick(v); setQ(""); setResults([]); setOpen(false); }}
              className="w-full text-left px-4 py-2 hover:bg-zinc-50 border-b border-zinc-100 flex items-center justify-between gap-3"
              data-testid={`variant-pick-${v.id}`}
            >
              <div className="min-w-0">
                <div className="font-mono font-bold text-sm text-[#1A1A1A]">{v.product_code}</div>
                <div className="text-xs text-zinc-500 truncate">{v.cable_size} {v.hole_size && `· hole ${v.hole_size}`}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-xs font-mono font-bold">₹{v.final_price}</div>
                <div className="text-[10px] text-zinc-400 uppercase">{v.unit}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
