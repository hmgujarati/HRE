import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import api from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { ArrowLeft, Phone, Envelope, MapPin, Plus, FileText } from "@phosphor-icons/react";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";

export default function ContactDetail() {
  const { id } = useParams();
  const [contact, setContact] = useState(null);
  const [quotes, setQuotes] = useState([]);

  useEffect(() => {
    Promise.all([
      api.get(`/contacts/${id}`),
      api.get(`/contacts/${id}/quotations`),
    ]).then(([c, q]) => { setContact(c.data); setQuotes(q.data); });
  }, [id]);

  if (!contact) return <div className="p-8 text-zinc-400">Loading…</div>;

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={contact.company || "Contact"}
        title={contact.name}
        subtitle={contact.gst_number ? `GST: ${contact.gst_number}` : ""}
        testId="contact-detail-header"
        actions={
          <div className="flex items-center gap-2">
            <Link to="/contacts" className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2">
              <ArrowLeft size={14} weight="bold" /> Back
            </Link>
            <Link
              to={`/quotations/new?contact=${contact.id}`}
              data-testid="contact-detail-create-quote-btn"
              className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2"
            >
              <Plus size={14} weight="bold" /> New Quote
            </Link>
          </div>
        }
      />

      <div className="p-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <div className="border border-zinc-200 bg-white p-6 space-y-3">
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Contact</div>
            {contact.phone && <Row icon={Phone} label="Phone" value={contact.phone} />}
            {contact.email && <Row icon={Envelope} label="Email" value={contact.email} />}
            {(contact.state || contact.country) && <Row icon={MapPin} label="Location" value={[contact.state, contact.country].filter(Boolean).join(", ")} />}
          </div>
          {contact.billing_address && (
            <div className="border border-zinc-200 bg-white p-6">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Billing Address</div>
              <div className="text-sm whitespace-pre-line text-zinc-700">{contact.billing_address}</div>
            </div>
          )}
          {contact.shipping_address && (
            <div className="border border-zinc-200 bg-white p-6">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Shipping Address</div>
              <div className="text-sm whitespace-pre-line text-zinc-700">{contact.shipping_address}</div>
            </div>
          )}
          {contact.notes && (
            <div className="border border-zinc-200 bg-white p-6">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Notes</div>
              <div className="text-sm whitespace-pre-line text-zinc-600">{contact.notes}</div>
            </div>
          )}
        </div>

        <div className="lg:col-span-2 border border-zinc-200 bg-white">
          <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">History</div>
              <h3 className="font-heading font-black text-lg">Quotations ({quotes.length})</h3>
            </div>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                <th className="px-6 py-3">Quote No.</th>
                <th className="px-6 py-3">Date</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Total ₹</th>
              </tr>
            </thead>
            <tbody>
              {quotes.map((q) => (
                <tr key={q.id} className="border-t border-zinc-100 hover:bg-zinc-50/60">
                  <td className="px-6 py-3 font-mono font-bold">
                    <Link to={`/quotations/${q.id}`} className="hover:text-[#FBAE17]">{q.quote_number}</Link>
                  </td>
                  <td className="px-6 py-3 text-xs font-mono text-zinc-500">{new Date(q.created_at).toLocaleDateString()}</td>
                  <td className="px-6 py-3"><QuoteStatusBadge status={q.status} /></td>
                  <td className="px-6 py-3 text-right font-mono font-bold">₹{(q.grand_total || 0).toLocaleString("en-IN")}</td>
                </tr>
              ))}
              {!quotes.length && (
                <tr><td colSpan={4} className="px-6 py-12 text-center text-zinc-400">
                  <FileText size={32} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  No quotations yet for this contact.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Row({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-3">
      <Icon size={16} className="text-zinc-400 mt-0.5" />
      <div>
        <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">{label}</div>
        <div className="text-sm text-[#1A1A1A] font-mono">{value}</div>
      </div>
    </div>
  );
}
