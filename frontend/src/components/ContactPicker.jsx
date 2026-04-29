import { useEffect, useState, useRef } from "react";
import api from "@/lib/api";
import { MagnifyingGlass, Plus, X, AddressBook } from "@phosphor-icons/react";

export default function ContactPicker({ value, onPick }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [draft, setDraft] = useState({ name: "", company: "", phone: "", email: "" });
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(async () => {
      const r = await api.get("/contacts", { params: q ? { q } : {} });
      setResults(r.data.slice(0, 30));
    }, 200);
    return () => clearTimeout(t);
  }, [q, open]);

  useEffect(() => {
    const click = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", click);
    return () => document.removeEventListener("mousedown", click);
  }, []);

  const quickAdd = async (e) => {
    e.preventDefault();
    const { data } = await api.post("/contacts", { ...draft, source: "quotation" });
    onPick(data);
    setAddOpen(false);
    setDraft({ name: "", company: "", phone: "", email: "" });
  };

  return (
    <div className="relative" ref={ref} data-testid="contact-picker">
      {value ? (
        <div className="border border-zinc-300 px-4 py-3 flex items-center justify-between bg-white">
          <div>
            <div className="font-bold text-sm">{value.name}</div>
            {value.company && <div className="text-xs text-zinc-500">{value.company}</div>}
            <div className="text-xs text-zinc-500 font-mono mt-0.5">
              {value.phone}{value.phone && value.email && " · "}{value.email}
            </div>
          </div>
          <button type="button" onClick={() => onPick(null)} className="text-zinc-400 hover:text-red-600" data-testid="contact-picker-clear">
            <X size={16} />
          </button>
        </div>
      ) : (
        <>
          <div className="relative">
            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); setOpen(true); }}
              onFocus={() => setOpen(true)}
              placeholder="Search contact by name, phone, email…"
              className="w-full border border-zinc-300 pl-9 pr-32 py-3 text-sm focus:outline-none focus:border-[#FBAE17]"
              data-testid="contact-picker-input"
            />
            <button
              type="button"
              onClick={() => setAddOpen(true)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-xs uppercase font-bold tracking-wider text-zinc-700 hover:text-[#FBAE17] flex items-center gap-1 px-2 py-1"
              data-testid="contact-picker-quick-add"
            >
              <Plus size={12} weight="bold" /> Quick Add
            </button>
          </div>
          {open && (
            <div className="absolute z-30 left-0 right-0 mt-1 bg-white border border-zinc-300 max-h-80 overflow-y-auto shadow-lg">
              {results.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => { onPick(c); setOpen(false); setQ(""); }}
                  className="w-full text-left px-4 py-2 hover:bg-zinc-50 border-b border-zinc-100"
                  data-testid={`contact-picker-item-${c.id}`}
                >
                  <div className="font-bold text-sm">{c.name}</div>
                  <div className="text-xs text-zinc-500">{c.company} · {c.phone || c.email}</div>
                </button>
              ))}
              {!results.length && (
                <div className="px-4 py-6 text-sm text-zinc-400 text-center">
                  <AddressBook size={20} weight="thin" className="mx-auto mb-1 text-zinc-300" />
                  No contacts found. Use Quick Add.
                </div>
              )}
            </div>
          )}
        </>
      )}

      {addOpen && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={() => setAddOpen(false)}>
          <form onClick={(e) => e.stopPropagation()} onSubmit={quickAdd} className="bg-white w-full max-w-md border border-zinc-200" data-testid="contact-quick-add-form">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <h3 className="font-heading font-black text-lg">Quick Add Contact</h3>
              <button type="button" onClick={() => setAddOpen(false)}><X size={20} /></button>
            </div>
            <div className="p-6 space-y-3">
              <input required placeholder="Name *" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" data-testid="quick-add-name" />
              <input placeholder="Company" value={draft.company} onChange={(e) => setDraft({ ...draft, company: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              <input placeholder="Phone" value={draft.phone} onChange={(e) => setDraft({ ...draft, phone: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" data-testid="quick-add-phone" />
              <input type="email" placeholder="Email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono" />
            </div>
            <div className="px-6 py-3 border-t border-zinc-200 flex justify-end gap-2">
              <button type="button" onClick={() => setAddOpen(false)} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
              <button type="submit" className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2" data-testid="quick-add-save">Add & Use</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
