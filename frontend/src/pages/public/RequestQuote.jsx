import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import { ArrowRight, ShieldCheck, Trash, Phone, ShoppingCart, Check } from "@phosphor-icons/react";
import { toast } from "sonner";

const CART_KEY = "hre_public_cart_v1";

function readCart() {
  try { return JSON.parse(localStorage.getItem(CART_KEY)) || []; } catch { return []; }
}
function writeCart(items) {
  localStorage.setItem(CART_KEY, JSON.stringify(items));
}

export default function RequestQuote() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1); // 1=cart+details, 2=otp, 3=review-with-prices, 4=success
  const [cart, setCart] = useState(readCart());
  const [details, setDetails] = useState({
    name: "", company: "", phone: "", email: "", gst_number: "",
    state: "", billing_address: "", shipping_address: "",
  });
  const [requestId, setRequestId] = useState(null);
  const [otp, setOtp] = useState("");
  const [token, setToken] = useState(localStorage.getItem("hre_public_token") || "");
  const [tokenRequestId, setTokenRequestId] = useState(localStorage.getItem("hre_public_request_id") || "");
  const [pricedItems, setPricedItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [savedQuote, setSavedQuote] = useState(null);
  const [devOtp, setDevOtp] = useState("");

  useEffect(() => { setCart(readCart()); }, []);

  // Auto-skip the "enter details" form for already-logged-in customers.
  // If a valid public session token is in localStorage, pull the contact's
  // profile and jump straight to step 3 (review with prices).
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get(`/public/me?token=${token}`);
        if (cancelled || !data?.contact) return;
        setDetails({
          name: data.contact.name || "",
          company: data.contact.company || "",
          phone: data.contact.phone || "",
          email: data.contact.email || "",
          gst_number: data.contact.gst_number || "",
          state: data.contact.state || "",
          billing_address: data.contact.billing_address || "",
          shipping_address: data.contact.shipping_address || "",
        });
        const items = readCart();
        if (!items.length) return; // nothing to price yet — stay on step 1
        const r = await api.get(`/public/variants?token=${token}`);
        const map = {};
        r.data.forEach((v) => { map[v.id] = v; });
        const priced = items.map((c) => {
          const v = map[c.product_variant_id];
          return {
            ...c,
            base_price: Number(v?.final_price || 0),
            gst_percentage: Number(v?.gst_percentage || 18),
            unit: v?.unit || "NOS",
          };
        });
        if (cancelled) return;
        setPricedItems(priced);
        setRequestId(tokenRequestId || null);
        setStep(3);
      } catch {
        // Token invalid/expired — fall back to step 1 (manual details + OTP)
        localStorage.removeItem("hre_public_token");
        localStorage.removeItem("hre_public_request_id");
        setToken("");
        setTokenRequestId("");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateQty = (vid, qty) => {
    const next = cart.map((c) => c.product_variant_id === vid ? { ...c, quantity: Number(qty) } : c);
    setCart(next); writeCart(next);
  };
  const removeLine = (vid) => {
    const next = cart.filter((c) => c.product_variant_id !== vid);
    setCart(next); writeCart(next);
  };

  const submitDetailsAndSendOtp = async (e) => {
    e.preventDefault();
    if (!cart.length) { toast.error("Add at least one product to your cart from the catalogue"); return; }
    setBusy(true);
    try {
      const { data } = await api.post("/public/quote-requests/start", details);
      setRequestId(data.request_id);
      const otpRes = await api.post(`/public/quote-requests/${data.request_id}/send-otp`);
      if (otpRes.data.dev_otp) setDevOtp(otpRes.data.dev_otp);
      setStep(2);
      toast.success("OTP sent to your phone");
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const verifyOtpAndLoadPrices = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post(`/public/quote-requests/${requestId}/verify-otp`, { code: otp });
      setToken(data.token);
      localStorage.setItem("hre_public_token", data.token);
      localStorage.setItem("hre_public_request_id", requestId);
      setTokenRequestId(requestId);
      // Load priced variants for our cart
      const r = await api.get(`/public/variants?token=${data.token}`);
      const map = {};
      r.data.forEach((v) => { map[v.id] = v; });
      const priced = cart.map((c) => {
        const v = map[c.product_variant_id];
        return {
          ...c,
          base_price: Number(v?.final_price || 0),
          gst_percentage: Number(v?.gst_percentage || 18),
          unit: v?.unit || "NOS",
        };
      });
      setPricedItems(priced);
      setStep(3);
      toast.success("Phone verified — pricing unlocked");
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const finalise = async () => {
    setBusy(true);
    try {
      // If the customer is already logged in (token present but no fresh
      // requestId from this session) use the streamlined /public/me/quote/create
      // endpoint so they don't have to re-OTP for every quote.
      const items = pricedItems.map((p) => ({ product_variant_id: p.product_variant_id, quantity: Number(p.quantity || 0) }));
      const url = (token && !requestId)
        ? `/public/me/quote/create?token=${token}`
        : `/public/quote-requests/${requestId}/finalise?token=${token}`;
      const { data } = await api.post(url, { items, notes: "" });
      setSavedQuote(data);
      writeCart([]);
      setCart([]);
      setStep(4);
      toast.success(`Quote ${data.quote_number} created`);
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const totals = pricedItems.reduce((acc, it) => {
    const qty = Number(it.quantity || 0); const base = Number(it.base_price || 0); const gst = Number(it.gst_percentage || 0);
    const gross = qty * base; const ga = gross * gst / 100;
    acc.subtotal += gross; acc.gst += ga;
    return acc;
  }, { subtotal: 0, gst: 0 });
  totals.grand = totals.subtotal + totals.gst;

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6 sm:py-10">
      <Stepper step={step} />

      {step === 1 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Cart */}
          <div className="border border-zinc-200 bg-white">
            <div className="px-5 py-4 border-b border-zinc-200 flex items-center gap-2">
              <ShoppingCart size={18} weight="fill" className="text-[#FBAE17]" />
              <h3 className="font-heading font-black text-lg">Your Quote Cart</h3>
            </div>
            <div className="divide-y divide-zinc-100">
              {cart.map((c) => (
                <div key={c.product_variant_id} className="px-5 py-3 flex items-center gap-3" data-testid={`cart-item-${c.product_variant_id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="font-mono font-bold text-sm">{c.product_code}</div>
                    <div className="text-xs text-zinc-500 truncate">{c.family_name}</div>
                    <div className="text-xs text-zinc-500 font-mono">{c.cable_size}{c.hole_size && ` · hole ${c.hole_size}`}</div>
                  </div>
                  <input
                    type="number" min={1}
                    value={c.quantity}
                    onChange={(e) => updateQty(c.product_variant_id, e.target.value)}
                    className="w-20 border border-zinc-300 px-2 py-1 text-sm font-mono text-right"
                    data-testid={`cart-qty-${c.product_variant_id}`}
                  />
                  <button onClick={() => removeLine(c.product_variant_id)} className="text-zinc-400 hover:text-red-600">
                    <Trash size={16} />
                  </button>
                </div>
              ))}
              {!cart.length && <div className="px-5 py-10 text-center text-sm text-zinc-400">Your cart is empty. Browse the <a href="/catalogue" className="font-bold text-[#FBAE17]">catalogue</a> to add items.</div>}
            </div>
          </div>

          {/* Details */}
          <form onSubmit={submitDetailsAndSendOtp} className="border border-zinc-200 bg-white" data-testid="public-details-form">
            <div className="px-5 py-4 border-b border-zinc-200 flex items-center gap-2">
              <ShieldCheck size={18} weight="fill" className="text-[#FBAE17]" />
              <h3 className="font-heading font-black text-lg">Business Details</h3>
            </div>
            <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-3">
              <Input label="Name *" required value={details.name} onChange={(v) => setDetails({ ...details, name: v })} testId="public-name" />
              <Input label="Company *" required value={details.company} onChange={(v) => setDetails({ ...details, company: v })} testId="public-company" />
              <Input label="Phone *" required value={details.phone} onChange={(v) => setDetails({ ...details, phone: v })} placeholder="+91 98xxx xxxxx" testId="public-phone" />
              <Input label="Email *" required type="email" value={details.email} onChange={(v) => setDetails({ ...details, email: v })} testId="public-email" placeholder="you@company.com" />
              <Input label="GST Number" value={details.gst_number} onChange={(v) => setDetails({ ...details, gst_number: v })} />
              <Input label="State *" required value={details.state} onChange={(v) => setDetails({ ...details, state: v })} placeholder="Gujarat / Maharashtra / …" testId="public-state" />
              <TextArea label="Billing Address" span value={details.billing_address} onChange={(v) => setDetails({ ...details, billing_address: v })} />
              <TextArea label="Shipping Address" span value={details.shipping_address} onChange={(v) => setDetails({ ...details, shipping_address: v })} />
            </div>
            <div className="px-5 py-4 border-t border-zinc-200">
              <button
                type="submit"
                disabled={busy || !cart.length}
                data-testid="public-send-otp-btn"
                className="w-full bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-sm py-3 flex items-center justify-center gap-2 disabled:opacity-60"
              >
                {busy ? "Sending OTP…" : <>Send Verification OTP <ArrowRight size={14} weight="bold" /></>}
              </button>
              <div className="text-[10px] text-zinc-400 mt-2 text-center">We'll WhatsApp a 6-digit code to verify your number before showing prices.</div>
            </div>
          </form>
        </div>
      )}

      {step === 2 && (
        <form onSubmit={verifyOtpAndLoadPrices} className="max-w-md mx-auto border border-zinc-200 bg-white" data-testid="public-otp-form">
          <div className="px-6 py-4 border-b border-zinc-200 flex items-center gap-2">
            <Phone size={18} weight="fill" className="text-[#FBAE17]" />
            <h3 className="font-heading font-black text-lg">Verify Your Phone</h3>
          </div>
          <div className="p-6 space-y-4">
            <p className="text-sm text-zinc-600">We sent a 6-digit code to <span className="font-mono font-bold">{details.phone}</span> via WhatsApp. Enter it below.</p>
            {devOtp && (
              <div className="bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
                <strong>Dev mode:</strong> code is <span className="font-mono font-bold">{devOtp}</span> (this passthrough is removed once WhatsApp API is wired in)
              </div>
            )}
            <input
              autoFocus
              maxLength={6}
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
              placeholder="••••••"
              className="w-full border border-zinc-300 px-4 py-3 text-2xl font-mono tracking-[0.5em] text-center focus:outline-none focus:border-[#FBAE17]"
              data-testid="public-otp-input"
            />
            <button
              type="submit"
              disabled={busy || otp.length !== 6}
              className="w-full bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-sm py-3 flex items-center justify-center gap-2 disabled:opacity-60"
              data-testid="public-verify-otp-btn"
            >
              {busy ? "Verifying…" : <>Verify & Reveal Prices <ArrowRight size={14} weight="bold" /></>}
            </button>
            <button
              type="button"
              onClick={() => setStep(1)}
              className="w-full text-xs uppercase tracking-wider font-bold text-zinc-500 hover:text-[#FBAE17]"
            >Back to details</button>
          </div>
        </form>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <div className="border border-zinc-200 bg-white">
            <div className="px-4 sm:px-5 py-4 border-b border-zinc-200">
              <h3 className="font-heading font-black text-lg">Review Your Quote</h3>
              <div className="text-xs text-zinc-500">Final pricing below. Click "Submit Quote" to receive it via email + WhatsApp.</div>
            </div>

            {/* Mobile cards */}
            <div className="sm:hidden divide-y divide-zinc-100">
              {pricedItems.map((it) => {
                const qty = Number(it.quantity || 0); const base = Number(it.base_price || 0); const gst = Number(it.gst_percentage || 0);
                const gross = qty * base; const ga = gross * gst / 100; const total = gross + ga;
                return (
                  <div key={it.product_variant_id} className="px-4 py-3 text-sm">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="font-mono font-bold">{it.product_code}</div>
                        <div className="text-xs text-zinc-500 font-mono">{it.cable_size}{it.hole_size && ` · hole ${it.hole_size}`}</div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="font-mono font-bold">₹{total.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                        <div className="text-[10px] text-zinc-500 font-mono">{qty} {it.unit} · {gst}% GST</div>
                      </div>
                    </div>
                    <div className="text-[10px] text-zinc-400 font-mono mt-1">Rate ₹{base.toFixed(2)}</div>
                  </div>
                );
              })}
            </div>

            {/* Desktop table */}
            <div className="hidden sm:block overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                    <th className="px-4 py-2">Code</th>
                    <th className="px-4 py-2">Spec</th>
                    <th className="px-4 py-2 text-right">Qty</th>
                    <th className="px-4 py-2 text-right">Rate ₹</th>
                    <th className="px-4 py-2 text-right">GST</th>
                    <th className="px-4 py-2 text-right">Total ₹</th>
                  </tr>
                </thead>
                <tbody>
                  {pricedItems.map((it) => {
                    const qty = Number(it.quantity || 0); const base = Number(it.base_price || 0); const gst = Number(it.gst_percentage || 0);
                    const gross = qty * base; const ga = gross * gst / 100; const total = gross + ga;
                    return (
                      <tr key={it.product_variant_id} className="border-t border-zinc-100">
                        <td className="px-4 py-2 font-mono font-bold">{it.product_code}</td>
                        <td className="px-4 py-2 text-xs text-zinc-600">{it.cable_size}{it.hole_size && ` · hole ${it.hole_size}`}</td>
                        <td className="px-4 py-2 text-right font-mono">{qty} {it.unit}</td>
                        <td className="px-4 py-2 text-right font-mono">₹{base.toFixed(2)}</td>
                        <td className="px-4 py-2 text-right font-mono">{gst}%</td>
                        <td className="px-4 py-2 text-right font-mono font-bold">₹{total.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="border-t-2 border-zinc-300 px-4 sm:px-5 py-3 flex flex-col sm:flex-row sm:items-center sm:justify-end gap-2 sm:gap-8 text-sm">
              <div className="flex justify-between sm:block"><span className="text-zinc-500 sm:mr-2">Subtotal:</span><span className="font-mono">₹{totals.subtotal.toFixed(2)}</span></div>
              <div className="flex justify-between sm:block"><span className="text-zinc-500 sm:mr-2">GST:</span><span className="font-mono">₹{totals.gst.toFixed(2)}</span></div>
              <div className="font-heading font-black text-xl flex justify-between sm:block"><span className="text-zinc-500 sm:hidden text-sm font-sans font-normal">Grand Total:</span>₹{totals.grand.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            </div>
          </div>
          <div className="flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
            <button onClick={() => setStep(1)} className="px-5 py-3 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50">Back</button>
            <button onClick={finalise} disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-6 py-3 flex items-center justify-center gap-2 disabled:opacity-60" data-testid="public-finalise-btn">
              {busy ? "Submitting…" : <>Submit Quote <Check size={14} weight="bold" /></>}
            </button>
          </div>
        </div>
      )}

      {step === 4 && savedQuote && (
        <div className="max-w-xl mx-auto border-2 border-[#FBAE17] bg-white text-center py-12 px-6">
          <div className="w-16 h-16 bg-[#FBAE17] flex items-center justify-center mx-auto mb-4">
            <Check size={32} weight="bold" className="text-black" />
          </div>
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Quote Generated</div>
          <h2 className="font-heading font-black text-3xl mb-2">{savedQuote.quote_number}</h2>
          <div className="text-zinc-600 text-sm">Grand Total</div>
          <div className="font-heading font-black text-4xl mt-1">₹{Number(savedQuote.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          <p className="text-zinc-500 text-sm mt-6 max-w-md mx-auto">
            Your quote will arrive on email and WhatsApp shortly. You can also access it any time from "My Quotes".
          </p>
          <div className="mt-8 flex items-center justify-center gap-2">
            <button onClick={() => navigate(`/my-quotes`)} className="bg-[#1A1A1A] hover:bg-black text-white font-bold uppercase tracking-wider text-xs px-5 py-3" data-testid="public-success-myquotes-btn">View My Quotes</button>
            <button onClick={() => navigate("/catalogue")} className="border border-zinc-300 hover:border-[#FBAE17] text-zinc-800 font-bold uppercase tracking-wider text-xs px-5 py-3">Back to Catalogue</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Stepper({ step }) {
  const steps = ["Cart & Details", "Verify Phone", "Review", "Done"];
  return (
    <div className="flex items-center justify-between gap-1 sm:gap-2 mb-6 sm:mb-8 overflow-x-auto">
      {steps.map((s, i) => {
        const idx = i + 1;
        const active = idx <= step;
        return (
          <div key={s} className="flex-1 flex items-center gap-2 sm:gap-3 min-w-0">
            <div className={`shrink-0 w-7 h-7 flex items-center justify-center text-xs font-bold ${active ? 'bg-[#FBAE17] text-black' : 'bg-zinc-100 text-zinc-400'}`}>{idx}</div>
            <div className={`hidden sm:block text-[10px] uppercase tracking-wider font-bold ${active ? 'text-[#1A1A1A]' : 'text-zinc-400'}`}>{s}</div>
            {idx < steps.length && <div className={`flex-1 h-px ${idx < step ? 'bg-[#FBAE17]' : 'bg-zinc-200'}`} />}
          </div>
        );
      })}
    </div>
  );
}

function Input({ label, span, type = "text", required, value, onChange, placeholder, testId }) {
  return (
    <div className={span ? "md:col-span-2" : ""}>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}</label>
      <input type={type} required={required} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
        data-testid={testId} />
    </div>
  );
}

function TextArea({ label, span, value, onChange }) {
  return (
    <div className={span ? "md:col-span-2" : ""}>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}</label>
      <textarea rows={2} value={value} onChange={(e) => onChange(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
    </div>
  );
}
