import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import api from "@/lib/api";
import PublicTrackingStrip from "@/components/PublicTrackingStrip";
import PublicLineItemStatus from "@/components/PublicLineItemStatus";
import PublicShipments from "@/components/PublicShipments";
import { MagnifyingGlass, Phone, ArrowRight } from "@phosphor-icons/react";

export default function TrackOrder() {
  const params = useParams();
  const [search, setSearch] = useSearchParams();
  const orderNumberFromUrl = params["*"] || search.get("order_number") || "";
  const phoneFromUrl = search.get("phone") || "";

  const [orderNumber, setOrderNumber] = useState(orderNumberFromUrl);
  const [phone, setPhone] = useState(phoneFromUrl);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const fetchOrder = async (ord, ph) => {
    if (!ord) return;
    setBusy(true); setErr("");
    try {
      const qs = new URLSearchParams({ order_number: ord });
      if (ph) qs.set("phone", ph);
      const { data } = await api.get(`/public/track?${qs.toString()}`);
      setData(data);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Order not found");
      setData(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (orderNumberFromUrl) {
      fetchOrder(orderNumberFromUrl, phoneFromUrl);
    }
    // eslint-disable-next-line
  }, []);

  const onSubmit = (e) => {
    e.preventDefault();
    const next = new URLSearchParams();
    next.set("order_number", orderNumber);
    if (phone) next.set("phone", phone);
    setSearch(next);
    fetchOrder(orderNumber, phone);
  };

  return (
    <div className="min-h-[60vh] py-8 sm:py-12">
      <div className="max-w-3xl mx-auto px-4">
        <div className="border-l-4 border-[#FBAE17] pl-4 mb-8">
          <div className="text-[10px] tracking-[0.3em] text-zinc-500 font-bold">ORDER TRACKING</div>
          <h1 className="text-3xl sm:text-4xl font-black text-[#1A1A1A] mt-1">Track Your Order</h1>
          <p className="text-sm text-zinc-500 mt-2">Enter your order number to see live status. Add your phone to unlock full details.</p>
        </div>

        <form onSubmit={onSubmit} className="bg-white border-2 border-[#1A1A1A] p-5 mb-8" data-testid="track-order-form">
          <label className="block text-[10px] tracking-[0.2em] font-bold text-zinc-500 mb-1">ORDER NUMBER</label>
          <input
            type="text"
            value={orderNumber}
            onChange={(e) => setOrderNumber(e.target.value)}
            placeholder="HRE/ORD/2026-27/0042"
            className="w-full border border-zinc-300 px-3 py-2 font-mono text-sm focus:outline-none focus:border-[#1A1A1A] mb-3"
            data-testid="track-order-number-input"
            required
          />
          <label className="block text-[10px] tracking-[0.2em] font-bold text-zinc-500 mb-1">PHONE (optional, last 10 digits)</label>
          <div className="flex items-center gap-2 mb-4">
            <Phone size={16} className="text-zinc-400" />
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 12))}
              placeholder="9999999999"
              className="flex-1 border border-zinc-300 px-3 py-2 font-mono text-sm focus:outline-none focus:border-[#1A1A1A]"
              data-testid="track-order-phone-input"
            />
          </div>
          <button
            type="submit"
            disabled={busy || !orderNumber}
            className="bg-[#1A1A1A] text-[#FBAE17] font-bold text-xs tracking-widest px-5 py-2.5 disabled:opacity-40 inline-flex items-center gap-2 hover:bg-[#2a2a2a]"
            data-testid="track-order-submit"
          >
            {busy ? "LOOKING UP…" : "TRACK"} <ArrowRight size={14} />
          </button>
        </form>

        {err && (
          <div className="border border-red-200 bg-red-50 text-red-700 text-sm p-3 mb-6" data-testid="track-order-error">
            {err}
          </div>
        )}

        {data && (
          <div className="bg-white border-2 border-[#1A1A1A] p-5" data-testid="track-order-result">
            <div className="flex items-baseline justify-between mb-3">
              <div className="font-mono text-sm text-zinc-600">{data.order_number}</div>
              {data.verified ? (
                <span className="text-[10px] font-bold tracking-widest bg-emerald-100 text-emerald-800 px-2 py-1">VERIFIED</span>
              ) : (
                <span className="text-[10px] font-bold tracking-widest bg-zinc-100 text-zinc-600 px-2 py-1">PUBLIC VIEW</span>
              )}
            </div>
            <PublicTrackingStrip order={data} />
            {data.verified && data.line_status?.length > 0 && (
              <div className="mt-6">
                <PublicLineItemStatus items={data.line_status} />
              </div>
            )}
            {data.verified && data.shipments?.length > 0 && (
              <div className="mt-6">
                <PublicShipments shipments={data.shipments} />
              </div>
            )}
            {!data.verified && (
              <div className="mt-4 text-xs text-zinc-500">
                Enter the phone number on file to see line items, documents and shipment details.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
