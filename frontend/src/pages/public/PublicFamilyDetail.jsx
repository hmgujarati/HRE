import { useEffect, useState, useMemo } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import api, { fileUrl } from "@/lib/api";
import { ArrowLeft, Image as ImageIcon, ShoppingCart, Plus, Check, MagnifyingGlass, X } from "@phosphor-icons/react";
import { toast } from "sonner";

const CART_KEY = "hre_public_cart_v1";

function readCart() {
  try { return JSON.parse(localStorage.getItem(CART_KEY)) || []; } catch { return []; }
}
function writeCart(items) {
  localStorage.setItem(CART_KEY, JSON.stringify(items));
}

// Parse a size string into a numeric range. Handles "4-6 mm2", "1.5", "10 mm", "M6", "Ø6.5", etc.
// Returns { min, max } or null if no number can be extracted.
function parseSize(str) {
  if (str === null || str === undefined) return null;
  const matches = String(str).match(/\d+(?:\.\d+)?/g);
  if (!matches || !matches.length) return null;
  const arr = matches.map(Number).filter((n) => !Number.isNaN(n));
  if (!arr.length) return null;
  return { min: Math.min(...arr), max: Math.max(...arr) };
}

// Distance between a target number and a range. 0 if inside the range.
function rangeDistance(target, range) {
  if (range === null || range === undefined) return Infinity;
  if (target >= range.min && target <= range.max) return 0;
  return Math.min(Math.abs(target - range.min), Math.abs(target - range.max));
}

export default function PublicFamilyDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [cart, setCart] = useState(readCart());
  const [added, setAdded] = useState({});
  const [cableQ, setCableQ] = useState("");
  const [holeQ, setHoleQ] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    api.get(`/public/family/${id}`).then((r) => setData(r.data));
  }, [id]);

  const dimKeys = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.variants.flatMap((v) => Object.keys(v.dimensions || {}))));
  }, [data]);

  // Build searchable index once
  const indexed = useMemo(() => {
    if (!data) return [];
    return data.variants.map((v) => ({
      v,
      cableRange: parseSize(v.cable_size),
      holeRange: parseSize(v.hole_size),
    }));
  }, [data]);

  const cableTarget = useMemo(() => {
    const m = cableQ.match(/\d+(?:\.\d+)?/);
    return m ? Number(m[0]) : null;
  }, [cableQ]);
  const holeTarget = useMemo(() => {
    const m = holeQ.match(/\d+(?:\.\d+)?/);
    return m ? Number(m[0]) : null;
  }, [holeQ]);

  const hasQuery = cableTarget !== null || holeTarget !== null;

  // Top 5 closest variants based on numeric distance
  const matches = useMemo(() => {
    if (!hasQuery) return [];
    const scored = indexed.map(({ v, cableRange, holeRange }) => {
      let score = 0;
      let hits = 0;
      if (cableTarget !== null) {
        score += rangeDistance(cableTarget, cableRange);
        hits++;
      }
      if (holeTarget !== null) {
        score += rangeDistance(holeTarget, holeRange);
        hits++;
      }
      return { v, score: hits ? score / hits : Infinity };
    });
    scored.sort((a, b) => a.score - b.score);
    return scored.slice(0, 5);
  }, [indexed, cableTarget, holeTarget, hasQuery]);

  const visibleVariants = showAll
    ? data?.variants || []
    : matches.map((m) => m.v);

  const addToCart = (v) => {
    const next = [...cart];
    const existing = next.find((x) => x.product_variant_id === v.id);
    if (existing) {
      existing.quantity += Number(v.minimum_order_quantity || 100);
    } else {
      next.push({
        product_variant_id: v.id,
        product_code: v.product_code,
        cable_size: v.cable_size,
        hole_size: v.hole_size,
        family_name: data.family.family_name,
        quantity: Number(v.minimum_order_quantity || 100),
      });
    }
    setCart(next);
    writeCart(next);
    setAdded((s) => ({ ...s, [v.id]: true }));
    toast.success(`${v.product_code} added to quote cart`);
    setTimeout(() => setAdded((s) => ({ ...s, [v.id]: false })), 1200);
  };

  if (!data) return <div className="max-w-7xl mx-auto p-8 text-zinc-400">Loading…</div>;
  const fam = data.family;

  return (
    <div className="animate-fade-in max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
      <div className="flex items-center justify-between gap-2 mb-6">
        <Link to="/catalogue" className="text-xs uppercase font-bold tracking-wider text-zinc-700 hover:text-[#FBAE17] flex items-center gap-2">
          <ArrowLeft size={14} weight="bold" /> All Products
        </Link>
        {cart.length > 0 && (
          <button
            onClick={() => navigate("/request-quote")}
            data-testid="public-cart-summary-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-3 sm:px-4 py-2 flex items-center gap-2"
          >
            <ShoppingCart size={14} weight="fill" /> <span className="hidden sm:inline">Quote </span>Cart ({cart.length})
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6 mb-6 sm:mb-8">
        <div className="lg:col-span-1 space-y-4">
          <div className="border border-zinc-200 bg-white">
            <div className="aspect-[4/3] bg-zinc-50 flex items-center justify-center overflow-hidden">
              {fam.main_product_image ? (
                <img src={fileUrl(fam.main_product_image)} alt={fam.family_name} className="w-full h-full object-contain p-4" />
              ) : (
                <ImageIcon size={42} weight="thin" className="text-zinc-300" />
              )}
            </div>
          </div>
          {fam.dimension_drawing_image && (
            <div className="border border-zinc-200 bg-white">
              <div className="px-4 py-2 border-b border-zinc-200 text-[10px] uppercase tracking-wider font-bold text-zinc-500">Dimension Drawing</div>
              <div className="aspect-[4/3] bg-zinc-50 flex items-center justify-center overflow-hidden">
                <img src={fileUrl(fam.dimension_drawing_image)} alt="Dimensions" className="w-full h-full object-contain p-4" />
              </div>
            </div>
          )}
        </div>

        <div className="lg:col-span-2">
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">{fam.product_type}</div>
          <h1 className="font-heading font-black text-2xl sm:text-3xl text-[#1A1A1A] tracking-tight mb-4">{fam.family_name}</h1>
          <div className="border border-zinc-200 bg-white p-4 sm:p-6 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Spec label="Material" value={fam.material_description} />
            <Spec label="Specification" value={fam.specification_description} />
            <Spec label="Finish" value={fam.finish_description} />
            <Spec label="Standard" value={fam.standard_reference} />
            {fam.insulation_colour_coding && <Spec label="Colour Coding" value={fam.insulation_colour_coding} span />}
          </div>
        </div>
      </div>

      {/* Smart Search */}
      <div className="border border-zinc-200 bg-white mb-4">
        <div className="px-4 sm:px-6 py-4 border-b border-zinc-200">
          <div className="flex items-center gap-2 mb-1">
            <MagnifyingGlass size={14} weight="bold" className="text-[#FBAE17]" />
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17]">Find Your Size</div>
          </div>
          <h2 className="font-heading font-black text-lg sm:text-xl">Smart Variant Finder</h2>
          <div className="text-xs text-zinc-500 mt-1">
            Type your <span className="font-bold">cable size</span> or <span className="font-bold">hole size</span> — we'll show the 5 closest matches from {data.variants.length} variants. Pricing unlocks after phone verification.
          </div>
        </div>
        <div className="p-4 sm:p-6 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <SizeInput
            label="Cable Size (mm²)"
            value={cableQ}
            onChange={setCableQ}
            placeholder="e.g. 5, 4-6, 16"
            testId="smart-search-cable"
            target={cableTarget}
          />
          <SizeInput
            label="Hole Size (mm)"
            value={holeQ}
            onChange={setHoleQ}
            placeholder="e.g. 6, 8.5, 10"
            testId="smart-search-hole"
            target={holeTarget}
          />
        </div>
        {hasQuery && (
          <div className="px-4 sm:px-6 pb-4 flex items-center justify-between gap-2 text-xs">
            <div className="text-zinc-500">
              Showing top {Math.min(5, matches.length)} closest match{matches.length === 1 ? '' : 'es'}
            </div>
            <button
              onClick={() => { setCableQ(""); setHoleQ(""); setShowAll(false); }}
              className="text-zinc-500 hover:text-[#FBAE17] uppercase tracking-wider font-bold flex items-center gap-1"
              data-testid="smart-search-clear"
            >
              <X size={12} weight="bold" /> Clear
            </button>
          </div>
        )}
      </div>

      {/* Variants */}
      <div className="border border-zinc-200 bg-white">
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-zinc-200 flex items-center justify-between gap-2 flex-wrap">
          <div>
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">
              {hasQuery && !showAll ? "Closest Matches" : showAll ? "All Variants" : "Variants"}
            </div>
            <h2 className="font-heading font-black text-base sm:text-lg">
              {hasQuery && !showAll
                ? `${matches.length} match${matches.length === 1 ? '' : 'es'}`
                : showAll
                  ? `All ${data.variants.length} sizes`
                  : `${data.variants.length} sizes available`}
            </h2>
          </div>
          <button
            onClick={() => setShowAll((s) => !s)}
            className="text-[10px] sm:text-xs uppercase tracking-wider font-bold px-3 py-1.5 border border-zinc-300 hover:border-[#FBAE17] hover:bg-[#FBAE17] hover:text-black"
            data-testid="toggle-show-all-variants"
          >
            {showAll ? "Hide list" : "Show all"}
          </button>
        </div>

        {!hasQuery && !showAll ? (
          <div className="px-6 py-12 sm:py-16 text-center">
            <MagnifyingGlass size={36} weight="thin" className="mx-auto text-zinc-300 mb-3" />
            <div className="font-heading font-black text-base sm:text-lg mb-1">Type a size above to begin</div>
            <div className="text-xs text-zinc-500 max-w-sm mx-auto">
              Enter your cable or hole size — we'll match against {data.variants.length} variants and surface the closest fits. Or tap <span className="font-bold">"Show all"</span> to browse the full list.
            </div>
          </div>
        ) : visibleVariants.length === 0 ? (
          <div className="px-6 py-12 text-center text-sm text-zinc-400">No variants match.</div>
        ) : (
          <>
            {/* Mobile cards */}
            <div className="sm:hidden divide-y divide-zinc-100">
              {visibleVariants.map((v) => (
                <div key={v.id} className="px-4 py-3 flex items-center gap-3" data-testid={`public-variant-row-${v.id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="font-mono font-bold text-sm">{v.product_code}</div>
                    <div className="text-xs text-zinc-600 font-mono mt-0.5">
                      <span className="text-zinc-400">Cable</span> {v.cable_size}
                      {v.hole_size && <> · <span className="text-zinc-400">Hole</span> {v.hole_size}</>}
                    </div>
                  </div>
                  <button
                    onClick={() => addToCart(v)}
                    data-testid={`public-add-cart-${v.id}`}
                    className={`shrink-0 text-xs uppercase tracking-wider font-bold px-3 py-2 border flex items-center gap-1 transition-colors ${added[v.id] ? 'bg-emerald-50 border-emerald-300 text-emerald-700' : 'border-zinc-300 hover:border-[#FBAE17] hover:bg-[#FBAE17]'}`}
                  >
                    {added[v.id] ? <><Check size={12} weight="bold" /> Added</> : <><Plus size={12} weight="bold" /> Add</>}
                  </button>
                </div>
              ))}
            </div>

            {/* Desktop table */}
            <div className="hidden sm:block overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-zinc-50">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                    <th className="px-3 py-2">Code</th>
                    <th className="px-3 py-2">Cable</th>
                    <th className="px-3 py-2">Hole</th>
                    {dimKeys.map((k) => <th key={k} className="px-3 py-2 font-mono">{k}</th>)}
                    <th className="px-3 py-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleVariants.map((v) => (
                    <tr key={v.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`public-variant-row-desktop-${v.id}`}>
                      <td className="px-3 py-2 font-mono font-bold">{v.product_code}</td>
                      <td className="px-3 py-2 font-mono">{v.cable_size}</td>
                      <td className="px-3 py-2 font-mono">{v.hole_size || '—'}</td>
                      {dimKeys.map((k) => <td key={k} className="px-3 py-2 font-mono text-zinc-600">{v.dimensions?.[k] ?? ''}</td>)}
                      <td className="px-3 py-2 text-right">
                        <button
                          onClick={() => addToCart(v)}
                          data-testid={`public-add-cart-desktop-${v.id}`}
                          className={`text-xs uppercase tracking-wider font-bold px-3 py-1.5 border flex items-center gap-1 ml-auto transition-colors ${added[v.id] ? 'bg-emerald-50 border-emerald-300 text-emerald-700' : 'border-zinc-300 hover:border-[#FBAE17] hover:bg-[#FBAE17]'}`}
                        >
                          {added[v.id] ? <><Check size={12} weight="bold" /> Added</> : <><Plus size={12} weight="bold" /> Add</>}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Spec({ label, value, span }) {
  return (
    <div className={span ? "md:col-span-2" : ""}>
      <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500 mb-0.5">{label}</div>
      <div className="text-sm text-[#1A1A1A]">{value || <span className="text-zinc-300">—</span>}</div>
    </div>
  );
}

function SizeInput({ label, value, onChange, placeholder, testId, target }) {
  return (
    <div>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}</label>
      <div className="relative">
        <input
          type="text"
          inputMode="decimal"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full border border-zinc-300 px-3 py-3 text-base sm:text-sm focus:outline-none focus:border-[#FBAE17] font-mono"
          data-testid={testId}
        />
        {target !== null && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] uppercase tracking-wider font-bold bg-[#FBAE17] text-black px-2 py-0.5">
            {target}
          </span>
        )}
      </div>
    </div>
  );
}
