import { NavLink, useNavigate } from "react-router-dom";
import {
  ChartBar, Tag, Stack, Wrench, Folders, Package, ClockCounterClockwise,
  ChatCircleDots, Storefront, GearSix, SignOut, FileText, AddressBook
} from "@phosphor-icons/react";
import { useAuth } from "@/contexts/AuthContext";

const items = [
  { to: "/dashboard", label: "Dashboard", icon: ChartBar },
  { to: "/quotations", label: "Quotations", icon: FileText },
  { to: "/orders", label: "Orders", icon: Storefront },
  { to: "/contacts", label: "Contacts", icon: AddressBook },
  { to: "/pricing-chart", label: "Pricing Chart", icon: Tag },
  { to: "/product-families", label: "Product Families", icon: Stack },
  { to: "/materials", label: "Materials", icon: Wrench },
  { to: "/categories", label: "Categories", icon: Folders },
  { to: "/products", label: "Products / Variants", icon: Package },
  { to: "/price-history", label: "Price History", icon: ClockCounterClockwise },
];

const soon = [
  { label: "WhatsApp Bot", icon: ChatCircleDots },
  { label: "Expo Leads", icon: Storefront },
];

export default function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <aside className="sidebar-dark fixed left-0 top-0 h-screen w-64 bg-[#1A1A1A] text-white flex flex-col border-r border-zinc-900 z-40" data-testid="sidebar">
      {/* Brand */}
      <div className="px-4 py-4 border-b border-zinc-800 flex items-center justify-center">
        <img src="/hre-logo-dark-bg.png" alt="H R Exporter · An ISO 9001 Company" className="h-28 max-w-full object-contain" data-testid="sidebar-logo" />
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-4">
        <div className="px-6 mb-3 text-[10px] uppercase tracking-[0.2em] text-zinc-500 font-bold">CRM</div>
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            data-testid={`nav-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}
            className={({ isActive }) =>
              `flex items-center gap-3 px-6 py-3 text-sm font-medium transition-colors border-r-4 ${
                isActive
                  ? 'bg-zinc-900/60 text-[#FBAE17] border-[#FBAE17]'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-900/40 border-transparent'
              }`
            }
          >
            <Icon size={18} weight="regular" />
            <span>{label}</span>
          </NavLink>
        ))}

        <div className="px-6 mt-6 mb-3 text-[10px] uppercase tracking-[0.2em] text-zinc-500 font-bold">Coming Soon</div>
        {soon.map(({ label, icon: Icon }) => (
          <div
            key={label}
            data-testid={`nav-soon-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}
            className="flex items-center justify-between gap-3 px-6 py-3 text-sm text-zinc-600 cursor-not-allowed"
          >
            <span className="flex items-center gap-3">
              <Icon size={18} weight="regular" />
              {label}
            </span>
            <span className="text-[9px] uppercase tracking-wider bg-zinc-800 text-zinc-500 px-2 py-0.5 font-bold">Soon</span>
          </div>
        ))}

        <div className="px-6 mt-6 mb-3 text-[10px] uppercase tracking-[0.2em] text-zinc-500 font-bold">System</div>
        <NavLink
          to="/settings"
          data-testid="nav-settings"
          className={({ isActive }) =>
            `flex items-center gap-3 px-6 py-3 text-sm font-medium transition-colors border-r-4 ${
              isActive
                ? 'bg-zinc-900/60 text-[#FBAE17] border-[#FBAE17]'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-900/40 border-transparent'
            }`
          }
        >
          <GearSix size={18} />
          <span>Settings</span>
        </NavLink>
      </nav>

      {/* User */}
      <div className="border-t border-zinc-800 px-6 py-4 flex items-center justify-between">
        <div className="leading-tight">
          <div className="text-xs text-white font-bold truncate max-w-[140px]" data-testid="sidebar-user-name">{user?.name || "User"}</div>
          <div className="text-[10px] uppercase tracking-wider text-[#FBAE17] font-bold">{user?.role}</div>
        </div>
        <button
          onClick={async () => { await logout(); navigate("/login"); }}
          data-testid="sidebar-logout-btn"
          className="text-zinc-400 hover:text-[#FBAE17] transition-colors"
          title="Logout"
        >
          <SignOut size={20} />
        </button>
      </div>
    </aside>
  );
}
