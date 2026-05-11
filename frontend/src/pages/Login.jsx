import { useState, useEffect } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import api, { formatApiError } from "@/lib/api";
import { ArrowRight, ShieldCheck } from "@phosphor-icons/react";

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.get("/public/stats").then((r) => setStats(r.data)).catch(() => {});
  }, []);

  if (user) return <Navigate to="/dashboard" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await login(email.trim().toLowerCase(), password);
      navigate("/dashboard");
    } catch (e) {
      setErr(formatApiError(e?.response?.data?.detail) || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen w-full grid lg:grid-cols-2 bg-white industrial">
      {/* Left visual panel */}
      <div className="hidden lg:flex relative bg-[#1A1A1A] text-white flex-col p-12 overflow-hidden">
        <div className="absolute inset-0 opacity-[0.06]" style={{
          backgroundImage:
            'linear-gradient(#FBAE17 1px, transparent 1px), linear-gradient(90deg, #FBAE17 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }} />
        <div className="relative z-10">
          <img src="/hre-logo-dark-bg.png" alt="H R Exporter · An ISO 9001 Company" className="h-44 w-auto object-contain -ml-3" data-testid="login-logo-large" />
        </div>

        <div className="relative z-10 mt-auto">
          <div className="text-[10px] uppercase tracking-[0.22em] text-[#FBAE17] font-bold mb-3">Catalogue · CRM · Quotation</div>
          <h1 className="font-heading font-black text-5xl leading-[1.05] tracking-tight">
            Precision pricing<br/>for industrial<br/>
            <span className="bg-[#FBAE17] text-black px-2">cable terminations.</span>
          </h1>
          <p className="text-zinc-400 mt-6 max-w-md">
            Manage materials, product families, dimension drawings, and bulk pricing across copper and aluminium catalogues — all in one place.
          </p>
          <div className="mt-10 grid grid-cols-3 gap-4 max-w-md">
            {[
              { k: "Materials", v: stats ? stats.materials : "—" },
              { k: "Product Families", v: stats ? stats.families : "—" },
              { k: "Variants", v: stats ? stats.variants : "—" },
            ].map((s) => (
              <div key={s.k} className="border-l-2 border-[#FBAE17] pl-3">
                <div className="font-heading font-black text-2xl" data-testid={`login-stat-${s.k.toLowerCase().replace(/\s+/g, '-')}`}>{s.v}</div>
                <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-bold">{s.k}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 mt-12 flex items-center gap-2 text-zinc-500 text-xs">
          <ShieldCheck size={16} weight="bold" className="text-[#FBAE17]" />
          Internal admin · JWT secured
        </div>
      </div>

      {/* Right form panel */}
      <div className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-md" data-testid="login-form">
          <div className="lg:hidden mb-10">
            <img src="/hre-logo-light-bg.png" alt="H R Exporter" className="h-14 object-contain" />
          </div>

          <div className="text-[10px] uppercase tracking-[0.22em] text-[#FBAE17] font-bold mb-3">Sign in</div>
          <h2 className="font-heading font-black text-4xl text-[#1A1A1A] tracking-tight">Welcome back.</h2>
          <p className="text-zinc-500 mt-2 text-sm">Use your admin credentials to access the catalogue dashboard.</p>

          <div className="mt-10 space-y-5">
            <div>
              <label className="text-[10px] font-bold text-zinc-700 uppercase tracking-[0.2em] mb-2 block">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                data-testid="login-email-input"
                className="w-full border border-zinc-300 px-4 py-3 bg-white text-sm focus:outline-none focus:border-[#FBAE17] focus:ring-2 focus:ring-[#FBAE17]/30 transition-all"
                placeholder="admin@hrexporter.com"
              />
            </div>
            <div>
              <label className="text-[10px] font-bold text-zinc-700 uppercase tracking-[0.2em] mb-2 block">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                data-testid="login-password-input"
                className="w-full border border-zinc-300 px-4 py-3 bg-white text-sm focus:outline-none focus:border-[#FBAE17] focus:ring-2 focus:ring-[#FBAE17]/30 transition-all"
                placeholder="••••••••"
              />
            </div>

            {err && (
              <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3" data-testid="login-error">
                {err}
              </div>
            )}

            <button
              type="submit"
              disabled={busy}
              data-testid="login-submit-btn"
              className="w-full bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold tracking-wide uppercase text-sm py-4 flex items-center justify-center gap-2 transition-colors disabled:opacity-60"
            >
              {busy ? "Signing in…" : "Sign in"}
              {!busy && <ArrowRight size={16} weight="bold" />}
            </button>
          </div>

        </form>
      </div>
    </div>
  );
}
