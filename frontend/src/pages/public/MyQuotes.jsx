import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import { Phone, ArrowRight, FileText, SignOut } from "@phosphor-icons/react";
import { toast } from "sonner";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";

export default function MyQuotes() {
  const [token, setToken] = useState(localStorage.getItem("hre_public_token") || "");
  const [quotes, setQuotes] = useState([]);
  const [stage, setStage] = useState("idle"); // idle | enter-phone | enter-otp | listing
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [requestId, setRequestId] = useState("");
  const [devOtp, setDevOtp] = useState("");
  const [busy, setBusy] = useState(false);

  const loadQuotes = async (t) => {
    try {
      const { data } = await api.get(`/public/my-quotes?token=${t}`);
      setQuotes(data);
      setStage("listing");
    } catch (err) {
      // token invalid/expired
      localStorage.removeItem("hre_public_token");
      setToken("");
      setStage("enter-phone");
    }
  };

  useEffect(() => {
    if (token) loadQuotes(token);
    else setStage("enter-phone");
    // eslint-disable-next-line
  }, []);

  const sendOtp = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post("/public/my-quotes/login/start", { phone });
      setRequestId(data.request_id);
      if (data.dev_otp) setDevOtp(data.dev_otp);
      setStage("enter-otp");
      toast.success("OTP sent");
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const verifyOtp = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post(`/public/quote-requests/${requestId}/verify-otp`, { code: otp });
      localStorage.setItem("hre_public_token", data.token);
      setToken(data.token);
      await loadQuotes(data.token);
      toast.success("Logged in");
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const logout = () => {
    localStorage.removeItem("hre_public_token");
    setToken("");
    setQuotes([]);
    setStage("enter-phone");
  };

  if (stage === "listing") {
    return (
      <div className="max-w-5xl mx-auto px-6 py-10">
        <div className="flex items-center justify-between mb-6">
          <div>
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Customer Portal</div>
            <h1 className="font-heading font-black text-3xl">My Quotes</h1>
          </div>
          <button onClick={logout} className="text-xs uppercase font-bold tracking-wider text-zinc-500 hover:text-red-600 flex items-center gap-1">
            <SignOut size={14} weight="bold" /> Sign out
          </button>
        </div>
        <div className="border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                <th className="px-6 py-3">Quote No.</th>
                <th className="px-6 py-3">Date</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Lines</th>
                <th className="px-6 py-3 text-right">Total ₹</th>
              </tr>
            </thead>
            <tbody>
              {quotes.map((q) => (
                <tr key={q.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`my-quote-${q.id}`}>
                  <td className="px-6 py-3 font-mono font-bold">{q.quote_number}</td>
                  <td className="px-6 py-3 text-xs font-mono text-zinc-500">{new Date(q.created_at).toLocaleDateString()}</td>
                  <td className="px-6 py-3"><QuoteStatusBadge status={q.status} /></td>
                  <td className="px-6 py-3 text-right font-mono">{(q.line_items || []).length}</td>
                  <td className="px-6 py-3 text-right font-mono font-bold">₹{Number(q.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                </tr>
              ))}
              {!quotes.length && (
                <tr><td colSpan={5} className="px-6 py-12 text-center text-zinc-400">
                  <FileText size={32} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  No quotes yet. <Link to="/request-quote" className="text-[#FBAE17] font-bold">Build one now</Link>.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto px-6 py-16">
      <div className="border border-zinc-200 bg-white">
        <div className="px-6 py-4 border-b border-zinc-200 flex items-center gap-2">
          <Phone size={18} weight="fill" className="text-[#FBAE17]" />
          <h3 className="font-heading font-black text-lg">My Quotes Sign-In</h3>
        </div>
        {stage === "enter-phone" && (
          <form onSubmit={sendOtp} className="p-6 space-y-4" data-testid="my-quotes-phone-form">
            <p className="text-sm text-zinc-600">Enter your phone number. We'll WhatsApp a 6-digit code to verify it.</p>
            <input
              autoFocus required value={phone} onChange={(e) => setPhone(e.target.value)}
              placeholder="+91 98xxx xxxxx"
              className="w-full border border-zinc-300 px-4 py-3 text-sm font-mono focus:outline-none focus:border-[#FBAE17]"
              data-testid="my-quotes-phone-input"
            />
            <button type="submit" disabled={busy} className="w-full bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-sm py-3 flex items-center justify-center gap-2 disabled:opacity-60" data-testid="my-quotes-send-otp-btn">
              {busy ? "Sending…" : <>Send OTP <ArrowRight size={14} weight="bold" /></>}
            </button>
          </form>
        )}
        {stage === "enter-otp" && (
          <form onSubmit={verifyOtp} className="p-6 space-y-4" data-testid="my-quotes-otp-form">
            <p className="text-sm text-zinc-600">Enter the 6-digit code sent to {phone}.</p>
            {devOtp && (
              <div className="bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
                <strong>Dev mode:</strong> code is <span className="font-mono font-bold">{devOtp}</span>
              </div>
            )}
            <input
              autoFocus maxLength={6}
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
              placeholder="••••••"
              className="w-full border border-zinc-300 px-4 py-3 text-2xl font-mono tracking-[0.5em] text-center focus:outline-none focus:border-[#FBAE17]"
              data-testid="my-quotes-otp-input"
            />
            <button type="submit" disabled={busy || otp.length !== 6} className="w-full bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-sm py-3 disabled:opacity-60" data-testid="my-quotes-verify-otp-btn">
              {busy ? "Verifying…" : "Verify & Sign In"}
            </button>
            <button type="button" onClick={() => setStage("enter-phone")} className="w-full text-xs uppercase tracking-wider font-bold text-zinc-500 hover:text-[#FBAE17]">Use a different number</button>
          </form>
        )}
      </div>
    </div>
  );
}
