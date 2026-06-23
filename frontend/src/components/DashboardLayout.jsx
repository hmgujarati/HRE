import Sidebar from "@/components/Sidebar";
import { Outlet } from "react-router-dom";
import { useEffect, useState } from "react";
import api from "@/lib/api";

export default function DashboardLayout() {
  const [testMode, setTestMode] = useState(null);
  useEffect(() => {
    let alive = true;
    api.get("/settings/integrations")
      .then(({ data }) => { if (alive) setTestMode(data?.test_mode || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  const restrictPhone = testMode?.restrict_outbound_phone;
  const restrictEmail = testMode?.restrict_outbound_email;
  const showBanner = !!(restrictPhone || restrictEmail);
  return (
    <div className="industrial min-h-screen bg-[#F9F9F9]">
      <Sidebar />
      <main className="ml-64 min-h-screen bg-white" data-testid="dashboard-main">
        {showBanner && (
          <div
            data-testid="test-mode-banner"
            className="sticky top-0 z-40 bg-amber-100 border-b-2 border-amber-500 text-amber-900 text-xs font-bold tracking-wide px-4 py-2 flex items-center gap-2"
          >
            <span className="inline-block w-2 h-2 rounded-full bg-amber-600 animate-pulse" />
            TEST MODE ACTIVE — All outbound
            {restrictPhone ? <span className="font-mono"> WhatsApp → {restrictPhone}</span> : null}
            {restrictPhone && restrictEmail ? " and " : null}
            {restrictEmail ? <span className="font-mono"> Email → {restrictEmail}</span> : null}
            <span className="ml-1 font-normal">(real customers will NOT receive messages)</span>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
