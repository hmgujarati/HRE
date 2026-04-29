import { useState } from "react";
import { Link, useLocation, Outlet } from "react-router-dom";
import { ShoppingCart, ListBullets, FileText, SignIn, List, X } from "@phosphor-icons/react";

const NAV = [
  { to: "/catalogue", label: "Catalogue", icon: ListBullets, testId: "public-nav-catalogue" },
  { to: "/request-quote", label: "Build Quote", icon: ShoppingCart, testId: "public-nav-request-quote" },
  { to: "/my-quotes", label: "My Quotes", icon: FileText, testId: "public-nav-my-quotes" },
];

export default function PublicLayout() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const cur = (to) => pathname.startsWith(to);

  return (
    <div className="min-h-screen bg-white industrial flex flex-col">
      <header className="border-b border-zinc-200 bg-white sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between">
          <Link to="/catalogue" className="flex items-center gap-3" onClick={() => setOpen(false)}>
            <img src="/hre-logo-light-bg.png" alt="HREXPORTER" className="h-9 sm:h-12 object-contain" />
          </Link>
          {/* Desktop nav */}
          <nav className="hidden md:flex items-center gap-6 text-sm font-bold uppercase tracking-wider">
            {NAV.map((n) => {
              const Icon = n.icon;
              return (
                <Link key={n.to} to={n.to} data-testid={n.testId}
                  className={`hover:text-[#FBAE17] flex items-center gap-2 ${cur(n.to) ? "text-[#FBAE17]" : "text-zinc-700"}`}>
                  <Icon size={16} weight="bold" /> {n.label}
                </Link>
              );
            })}
            <Link to="/login" className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 hover:text-[#FBAE17] flex items-center gap-1" data-testid="public-nav-admin">
              <SignIn size={12} /> Admin
            </Link>
          </nav>
          {/* Mobile burger */}
          <button onClick={() => setOpen((s) => !s)} className="md:hidden text-zinc-700" data-testid="public-nav-toggle">
            {open ? <X size={26} weight="bold" /> : <List size={26} weight="bold" />}
          </button>
        </div>
        {/* Mobile drawer */}
        {open && (
          <nav className="md:hidden border-t border-zinc-200 bg-white" data-testid="public-mobile-nav">
            {NAV.map((n) => {
              const Icon = n.icon;
              return (
                <Link key={n.to} to={n.to} onClick={() => setOpen(false)}
                  className={`px-5 py-3 border-b border-zinc-100 text-sm font-bold uppercase tracking-wider flex items-center gap-3 ${cur(n.to) ? "text-[#FBAE17] bg-zinc-50" : "text-zinc-700"}`}>
                  <Icon size={16} weight="bold" /> {n.label}
                </Link>
              );
            })}
            <Link to="/login" onClick={() => setOpen(false)} className="px-5 py-3 text-xs uppercase tracking-wider font-bold text-zinc-500 flex items-center gap-2">
              <SignIn size={14} /> Admin
            </Link>
          </nav>
        )}
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-zinc-200 bg-[#1A1A1A] text-zinc-400 text-xs">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
          <div>© HREXPORTER · An ISO 9001 Company</div>
          <div className="font-mono">+91 9033135768 · info@hrexporter.com</div>
        </div>
      </footer>
    </div>
  );
}
