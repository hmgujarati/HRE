import { useEffect, useState, useMemo } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import api, { fileUrl } from "@/lib/api";
import { ArrowLeft, Image as ImageIcon, ShoppingCart, Plus, Check } from "@phosphor-icons/react";
import { toast } from "sonner";

const CART_KEY = "hre_public_cart_v1";

function readCart() {
  try { return JSON.parse(localStorage.getItem(CART_KEY)) || []; } catch { return []; }
}
function writeCart(items) {
  localStorage.setItem(CART_KEY, JSON.stringify(items));
}

export default function PublicFamilyDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [cart, setCart] = useState(readCart());
  const [added, setAdded] = useState({});

  useEffect(() => {
    api.get(`/public/family/${id}`).then((r) => setData(r.data));
  }, [id]);

  const dimKeys = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.variants.flatMap((v) => Object.keys(v.dimensions || {}))));
  }, [data]);

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
    <div className="animate-fade-in max-w-7xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <Link to="/catalogue" className="text-xs uppercase font-bold tracking-wider text-zinc-700 hover:text-[#FBAE17] flex items-center gap-2">
          <ArrowLeft size={14} weight="bold" /> All Products
        </Link>
        {cart.length > 0 && (
          <button
            onClick={() => navigate("/request-quote")}
            data-testid="public-cart-summary-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2"
          >
            <ShoppingCart size={14} weight="fill" /> Quote Cart ({cart.length})
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
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
          <h1 className="font-heading font-black text-3xl text-[#1A1A1A] tracking-tight mb-4">{fam.family_name}</h1>
          <div className="border border-zinc-200 bg-white p-6 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Spec label="Material" value={fam.material_description} />
            <Spec label="Specification" value={fam.specification_description} />
            <Spec label="Finish" value={fam.finish_description} />
            <Spec label="Standard" value={fam.standard_reference} />
            {fam.insulation_colour_coding && <Spec label="Colour Coding" value={fam.insulation_colour_coding} span />}
          </div>
        </div>
      </div>

      {/* Variants table */}
      <div className="border border-zinc-200 bg-white">
        <div className="px-6 py-4 border-b border-zinc-200">
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Variants</div>
          <h2 className="font-heading font-black text-xl">Available Sizes ({data.variants.length})</h2>
          <div className="text-xs text-zinc-500 mt-1">Add items below to your quote cart. Pricing is shown after phone verification.</div>
        </div>
        <div className="overflow-x-auto">
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
              {data.variants.map((v) => (
                <tr key={v.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`public-variant-row-${v.id}`}>
                  <td className="px-3 py-2 font-mono font-bold">{v.product_code}</td>
                  <td className="px-3 py-2 font-mono">{v.cable_size}</td>
                  <td className="px-3 py-2 font-mono">{v.hole_size || '—'}</td>
                  {dimKeys.map((k) => <td key={k} className="px-3 py-2 font-mono text-zinc-600">{v.dimensions?.[k] ?? ''}</td>)}
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => addToCart(v)}
                      data-testid={`public-add-cart-${v.id}`}
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
