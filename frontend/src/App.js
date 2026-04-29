import "@/index.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "@/contexts/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import DashboardLayout from "@/components/DashboardLayout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Materials from "@/pages/Materials";
import Categories from "@/pages/Categories";
import ProductFamilies from "@/pages/ProductFamilies";
import ProductFamilyDetail from "@/pages/ProductFamilyDetail";
import PricingChart from "@/pages/PricingChart";
import PriceHistory from "@/pages/PriceHistory";
import ComingSoon from "@/pages/ComingSoon";
import Settings from "@/pages/Settings";
import Contacts from "@/pages/Contacts";
import ContactDetail from "@/pages/ContactDetail";
import Quotations from "@/pages/Quotations";
import QuotationBuilder from "@/pages/QuotationBuilder";
import QuotationView from "@/pages/QuotationView";

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster position="top-right" richColors />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute><DashboardLayout /></ProtectedRoute>}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/quotations" element={<Quotations />} />
            <Route path="/quotations/new" element={<QuotationBuilder />} />
            <Route path="/quotations/:id" element={<QuotationView />} />
            <Route path="/quotations/:id/edit" element={<QuotationBuilder />} />
            <Route path="/contacts" element={<Contacts />} />
            <Route path="/contacts/:id" element={<ContactDetail />} />
            <Route path="/pricing-chart" element={<PricingChart />} />
            <Route path="/products" element={<PricingChart />} />
            <Route path="/product-families" element={<ProductFamilies />} />
            <Route path="/product-families/:id" element={<ProductFamilyDetail />} />
            <Route path="/materials" element={<Materials />} />
            <Route path="/categories" element={<Categories />} />
            <Route path="/price-history" element={<PriceHistory />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<ComingSoon title="Not found" />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
