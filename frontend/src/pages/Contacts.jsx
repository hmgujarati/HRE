import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Plus, MagnifyingGlass, PencilSimple, Trash, X, Check, AddressBook, FileText, Phone, Envelope } from "@phosphor-icons/react";
import { toast } from "sonner";

const empty = {
  name: "", company: "", phone: "", email: "", gst_number: "",
  billing_address: "", shipping_address: "", state: "", country: "India",
  source: "manual", tags: [], notes: "",
};

const SOURCE_LABELS = {
  manual: { label: "Manual", color: "bg-zinc-100 text-zinc-700" },
  expo: { label: "Expo", color: "bg-emerald-50 text-emerald-700" },
  quotation: { label: "Quote", color: "bg-[#FBAE17] text-black" },
  whatsapp: { label: "WhatsApp", color: "bg-green-50 text-green-700" },
};

export default function Contacts() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const params = {};
    if (q) params.q = q;
    if (source) params.source = source;
    const r = await api.get("/contacts", { params });
    setItems(r.data);
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [q, source]);

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = { ...edit };
      if (edit.id) await api.put(`/contacts/${edit.id}`, payload);
      else await api.post("/contacts", payload);
      toast.success("Contact saved");
      setEdit(null);
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c) => {
    if (!window.confirm(`Delete contact "${c.name}"?`)) return;
    try {
      await api.delete(`/contacts/${c.id}`);
      toast.success("Deleted");
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="CRM"
        title="Contacts"
        subtitle="Customers, leads, and contacts. Auto-merged across manual entries, expo leads, and quotations."
        testId="contacts-header"
        actions={
          <button
            onClick={() => setEdit({ ...empty })}
            data-testid="contacts-add-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2"
          >
            <Plus size={16} weight="bold" /> Add Contact
          </button>
        }
      />

      <div className="p-8 space-y-4">
        <div className="border border-zinc-200 bg-white p-4 grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <div className="md:col-span-3">
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Search</label>
            <div className="relative">
              <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)} className="w-full border border-zinc-300 pl-9 pr-3 py-2 text-sm" placeholder="Name, company, phone, email" data-testid="contacts-search" />
            </div>
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Source</label>
            <select value={source} onChange={(e) => setSource(e.target.value)} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="contacts-source-filter">
              <option value="">All</option>
              <option value="manual">Manual</option>
              <option value="expo">Expo</option>
              <option value="quotation">Quotation</option>
              <option value="whatsapp">WhatsApp</option>
            </select>
          </div>
        </div>

        <div className="border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold border-b-2 border-zinc-200">
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Company</th>
                <th className="px-6 py-3">Contact</th>
                <th className="px-6 py-3">GST</th>
                <th className="px-6 py-3">Source</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => {
                const src = SOURCE_LABELS[c.source] || SOURCE_LABELS.manual;
                return (
                  <tr key={c.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`contact-row-${c.id}`}>
                    <td className="px-6 py-3 font-bold text-[#1A1A1A]">
                      <Link to={`/contacts/${c.id}`} className="hover:text-[#FBAE17]" data-testid={`contact-link-${c.id}`}>{c.name}</Link>
                    </td>
                    <td className="px-6 py-3 text-zinc-700">{c.company || <span className="text-zinc-400">—</span>}</td>
                    <td className="px-6 py-3">
                      {c.phone && <div className="text-xs flex items-center gap-1 text-zinc-700"><Phone size={12} /> {c.phone}</div>}
                      {c.email && <div className="text-xs flex items-center gap-1 text-zinc-500"><Envelope size={12} /> {c.email}</div>}
                    </td>
                    <td className="px-6 py-3 font-mono text-xs text-zinc-600">{c.gst_number || <span className="text-zinc-300">—</span>}</td>
                    <td className="px-6 py-3">
                      <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 ${src.color}`}>{src.label}</span>
                    </td>
                    <td className="px-6 py-3 text-right whitespace-nowrap">
                      <button
                        onClick={() => navigate(`/quotations/new?contact=${c.id}`)}
                        title="Create quotation"
                        className="text-zinc-500 hover:text-[#FBAE17] mr-3"
                        data-testid={`contact-quote-${c.id}`}
                      >
                        <FileText size={16} />
                      </button>
                      <button onClick={() => setEdit({ ...c })} className="text-zinc-500 hover:text-[#FBAE17] mr-3" data-testid={`contact-edit-${c.id}`}><PencilSimple size={16} /></button>
                      <button onClick={() => remove(c)} className="text-zinc-400 hover:text-red-600" data-testid={`contact-delete-${c.id}`}><Trash size={16} /></button>
                    </td>
                  </tr>
                );
              })}
              {!items.length && (
                <tr><td colSpan={6} className="px-6 py-12 text-center text-zinc-400">
                  <AddressBook size={32} weight="thin" className="mx-auto mb-2 text-zinc-300" />
                  No contacts yet. Click <strong>Add Contact</strong> to create one.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {edit && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6 overflow-y-auto" onClick={() => setEdit(null)}>
          <form onClick={(e) => e.stopPropagation()} onSubmit={save} className="bg-white w-full max-w-2xl border border-zinc-200 my-8" data-testid="contact-form">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <h3 className="font-heading font-black text-lg">{edit.id ? "Edit Contact" : "Add Contact"}</h3>
              <button type="button" onClick={() => setEdit(null)}><X size={20} /></button>
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Name *">
                <input required value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} className="cinp" data-testid="contact-name-input" />
              </Field>
              <Field label="Company *">
                <input required value={edit.company || ""} onChange={(e) => setEdit({ ...edit, company: e.target.value })} className="cinp" data-testid="contact-company-input" />
              </Field>
              <Field label="Phone">
                <input value={edit.phone || ""} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} className="cinp font-mono" placeholder="+91 98xxx xxxxx" data-testid="contact-phone-input" />
              </Field>
              <Field label="Email">
                <input type="email" value={edit.email || ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} className="cinp font-mono" data-testid="contact-email-input" />
              </Field>
              <Field label="GST Number">
                <input value={edit.gst_number || ""} onChange={(e) => setEdit({ ...edit, gst_number: e.target.value })} className="cinp font-mono" placeholder="22AAAAA0000A1Z5" />
              </Field>
              <Field label="State *">
                <input required value={edit.state || ""} onChange={(e) => setEdit({ ...edit, state: e.target.value })} className="cinp" placeholder="Gujarat / Maharashtra / …" data-testid="contact-state-input" />
              </Field>
              <Field label="Source">
                <select value={edit.source || "manual"} onChange={(e) => setEdit({ ...edit, source: e.target.value })} className="cinp bg-white">
                  <option value="manual">Manual</option>
                  <option value="expo">Expo</option>
                  <option value="quotation">Quotation</option>
                  <option value="whatsapp">WhatsApp</option>
                </select>
              </Field>
              <Field label="Country">
                <input value={edit.country || ""} onChange={(e) => setEdit({ ...edit, country: e.target.value })} className="cinp" />
              </Field>
              <Field label="Billing Address" span>
                <textarea rows={2} value={edit.billing_address || ""} onChange={(e) => setEdit({ ...edit, billing_address: e.target.value })} className="cinp" />
              </Field>
              <Field label="Shipping Address" span>
                <textarea rows={2} value={edit.shipping_address || ""} onChange={(e) => setEdit({ ...edit, shipping_address: e.target.value })} className="cinp" />
              </Field>
              <Field label="Notes" span>
                <textarea rows={2} value={edit.notes || ""} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} className="cinp" />
              </Field>
            </div>
            <div className="px-6 py-4 border-t border-zinc-200 flex justify-end gap-2">
              <button type="button" onClick={() => setEdit(null)} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
              <button type="submit" disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60" data-testid="contact-save-btn">
                <Check size={16} weight="bold" /> {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}

      <style>{`.cinp{width:100%;border:1px solid #d4d4d8;padding:.5rem .75rem;font-size:.875rem;outline:none}.cinp:focus{border-color:#FBAE17}`}</style>
    </div>
  );
}

function Field({ label, span, children }) {
  return (
    <div className={span ? "md:col-span-2" : ""}>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">{label}</label>
      {children}
    </div>
  );
}
