import { Link, useLocation, Outlet } from "react-router-dom";
import { ShoppingCart, ListBullets, FileText, SignIn } from "@phosphor-icons/react";

export default function PublicLayout() {
  const { pathname } = useLocation();
  return (
    <div className="min-h-screen bg-white industrial flex flex-col">
      <header className="border-b border-zinc-200 bg-white sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/catalogue" className="flex items-center gap-3">
            <img src="/hre-logo-light-bg.png" alt="HREXPORTER" className="h-12 object-contain" />
          </Link>
          <nav className="flex items-center gap-6 text-sm font-bold uppercase tracking-wider">
            <Link
              to="/catalogue"
              className={`hover:text-[#FBAE17] flex items-center gap-2 ${pathname.startsWith("/catalogue") ? "text-[#FBAE17]" : "text-zinc-700"}`}
              data-testid="public-nav-catalogue"
            >
              <ListBullets size={16} weight="bold" /> Catalogue
            </Link>
            <Link
              to="/request-quote"
              className={`hover:text-[#FBAE17] flex items-center gap-2 ${pathname.startsWith("/request-quote") ? "text-[#FBAE17]" : "text-zinc-700"}`}
              data-testid="public-nav-request-quote"
            >
              <ShoppingCart size={16} weight="bold" /> Build Quote
            </Link>
            <Link
              to="/my-quotes"
              className={`hover:text-[#FBAE17] flex items-center gap-2 ${pathname.startsWith("/my-quotes") ? "text-[#FBAE17]" : "text-zinc-700"}`}
              data-testid="public-nav-my-quotes"
            >
              <FileText size={16} weight="bold" /> My Quotes
            </Link>
            <Link
              to="/login"
              className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 hover:text-[#FBAE17] flex items-center gap-1"
              data-testid="public-nav-admin"
            >
              <SignIn size={12} /> Admin
            </Link>
          </nav>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-zinc-200 bg-[#1A1A1A] text-zinc-400 text-xs">
        <div className="max-w-7xl mx-auto px-6 py-6 flex items-center justify-between">
          <div>© HREXPORTER · An ISO 9001 Company</div>
          <div className="font-mono">+91 9033135768 · info@hrexporter.com</div>
        </div>
      </footer>
    </div>
  );
}
