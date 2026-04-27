import PageHeader from "@/components/PageHeader";
import { useAuth } from "@/contexts/AuthContext";

export default function Settings() {
  const { user } = useAuth();
  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow="System" title="Settings" subtitle="Account & branding preferences." testId="settings-header" />
      <div className="p-8 grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="border border-zinc-200 bg-white p-6">
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Account</div>
          <h3 className="font-heading font-black text-lg mb-4">Signed in as</h3>
          <div className="space-y-2 text-sm">
            <div><span className="text-zinc-500 mr-2">Name:</span><span className="font-medium">{user?.name}</span></div>
            <div><span className="text-zinc-500 mr-2">Email:</span><span className="font-mono">{user?.email}</span></div>
            <div><span className="text-zinc-500 mr-2">Role:</span><span className="text-[10px] uppercase tracking-wider font-bold bg-[#FBAE17] text-black px-2 py-0.5">{user?.role}</span></div>
          </div>
        </div>
        <div className="border border-zinc-200 bg-white p-6">
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Branding</div>
          <h3 className="font-heading font-black text-lg mb-4">HRE Exporter</h3>
          <div className="grid grid-cols-3 gap-3 text-xs">
            <div><div className="h-12 bg-[#FBAE17]" /><div className="font-mono mt-1">#FBAE17</div></div>
            <div><div className="h-12 bg-[#1A1A1A]" /><div className="font-mono mt-1">#1A1A1A</div></div>
            <div><div className="h-12 bg-white border border-zinc-300" /><div className="font-mono mt-1">#FFFFFF</div></div>
          </div>
          <p className="text-xs text-zinc-500 mt-4">Logo upload and currency selector will be enabled in a later phase. Currency is currently set to <span className="font-bold">INR ₹</span>.</p>
        </div>
      </div>
    </div>
  );
}
