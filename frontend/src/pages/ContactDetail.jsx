import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { ArrowLeft, Phone, Envelope, MapPin, Plus, FileText, Trash } from "@phosphor-icons/react";
import QuoteStatusBadge from "@/components/QuoteStatusBadge";
import { toast } from "sonner";

export default function ContactDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [contact, setContact] = useState(null);
  const [quotes, setQuotes] = useState([]);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get(`/contacts/${id}`),
      api.get(`/contacts/${id}/quotations`),
    ]).then(([c, q]) => { setContact(c.data); setQuotes(q.data); });
  }, [id]);

  const hasLinkedRecords = quotes.length > 0;

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await api.delete(`/contacts/${id}`);
      toast.success(`Contact ${contact.name} deleted`);
      navigate("/contacts");
    } catch (e) {
      const detail = e?.response?.data?.detail;
      toast.error(formatApiError(detail) || "Cannot delete contact — remove linked quotes and orders first.");
      setConfirmingDelete(false);
    } finally {
      setDeleting(false);
    }
  };

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
            <button
              onClick={() => setConfirmingDelete(true)}
              disabled={hasLinkedRecords}
              data-testid="contact-detail-delete-btn"
              title={hasLinkedRecords ? "Cannot delete — this contact has linked quotes/orders. Archive or delete those first." : "Delete this contact"}
              className={`px-4 py-3 border text-xs font-bold uppercase tracking-wider flex items-center gap-2 ${
                hasLinkedRecords
                  ? "border-zinc-200 text-zinc-300 cursor-not-allowed bg-zinc-50"
                  : "border-red-300 text-red-600 hover:bg-red-50"
              }`}
            >
              <Trash size={14} weight="bold" /> Delete
            </button>
          </div>
        }
      />

      <div className="p-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <div className="border border-zinc-200 bg-white p-6 space-y-3">
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Contact</div>
            {contact.phone && <Row icon={Phone} label="Phone" value={contact.phone} />}
            {contact.email && <Row icon={Envelope} label="Email" value={contact.email} />}
            {(contact.state || contact.address) && (
              <Row icon={MapPin} label="Address" value={[contact.address, contact.state].filter(Boolean).join(", ")} />
            )}
          </div>
        </div>

        <div className="lg:col-span-2">
          <div className="border border-zinc-200 bg-white">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileText size={16} weight="fill" className="text-[#FBAE17]" />
                <span className="text-xs font-bold uppercase tracking-wider">Quotations ({quotes.length})</span>
              </div>
            </div>
            {quotes.length === 0 ? (
              <div className="px-6 py-10 text-center text-zinc-400 text-sm">No quotations yet</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 border-b border-zinc-200">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                    <th className="px-6 py-3">Quote No.</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3 text-right">Grand Total</th>
                    <th className="px-6 py-3">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {quotes.map((q) => (
                    <tr key={q.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                      <td className="px-6 py-3 font-mono text-xs">
                        <Link to={`/quotations/${q.id}`} className="text-[#1A1A1A] hover:text-[#FBAE17]" data-testid={`contact-quote-link-${q.id}`}>
                          {q.quote_number}
                        </Link>
                      </td>
                      <td className="px-6 py-3"><QuoteStatusBadge status={q.status} /></td>
                      <td className="px-6 py-3 text-right font-mono font-bold">₹{Number(q.grand_total || 0).toLocaleString("en-IN")}</td>
                      <td className="px-6 py-3 text-xs font-mono text-zinc-500">{new Date(q.created_at).toLocaleDateString("en-GB")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {confirmingDelete && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center px-4" onClick={() => setConfirmingDelete(false)} data-testid="contact-delete-confirm-modal">
          <div className="bg-white max-w-md w-full border-2 border-red-500 p-6" onClick={(e) => e.stopPropagation()}>
            <div className="text-lg font-black text-red-700 mb-2">Delete contact?</div>
            <div className="text-sm text-zinc-600 mb-5">
              <b>{contact.name}</b>{contact.company ? ` (${contact.company})` : ""} will be permanently removed.
              This cannot be undone.
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setConfirmingDelete(false)}
                className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50"
                data-testid="contact-delete-cancel-btn"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-xs font-bold uppercase tracking-wider disabled:opacity-40"
                data-testid="contact-delete-confirm-btn"
              >
                {deleting ? "Deleting…" : "Delete Contact"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-3">
      <Icon size={16} weight="regular" className="text-zinc-400 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-bold">{label}</div>
        <div className="text-sm text-zinc-900 break-words">{value}</div>
      </div>
    </div>
  );
}
