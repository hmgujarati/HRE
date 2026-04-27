import Sidebar from "@/components/Sidebar";
import { Outlet } from "react-router-dom";

export default function DashboardLayout() {
  return (
    <div className="industrial min-h-screen bg-[#F9F9F9]">
      <Sidebar />
      <main className="ml-64 min-h-screen bg-white" data-testid="dashboard-main">
        <Outlet />
      </main>
    </div>
  );
}
