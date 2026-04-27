import { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Plus, PencilSimple, Trash, X, Check } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function Materials() {
  const [items, setItems] = useState([]);
  const [edit, setEdit] = useState(null); // null=closed, {} new, {id,..} edit
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const r = await api.get("/materials");
    setItems(r.data);
  };

  useEffect(() => { load(); }, []);

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = {
        material_name: edit.material_name,
        description: edit.description || "",
        active: edit.active ?? true,
      };
      if (edit.id) await api.put(`/materials/${edit.id}`, payload);
      else await api.post("/materials", payload);
      toast.success("Material saved");
      setEdit(null);
      await load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (m) => {
    if (!window.confirm(`Delete material "${m.material_name}"?`)) return;
    try {
      await api.delete(`/materials/${m.id}`);
      toast.success("Deleted");
      await load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="Catalogue"
        title="Materials"
        subtitle="Base material types used across product families and variants."
        testId="materials-header"
        actions={
          <button
            onClick={() => setEdit({ material_name: "", description: "", active: true })}
            data-testid="materials-add-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 transition-colors"
          >
            <Plus size={16} weight="bold" /> Add Material
          </button>
        }
      />

      <div className="p-8">
        <div className="border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold border-b border-zinc-200">
                <th className="px-6 py-3">Material Name</th>
                <th className="px-6 py-3">Description</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`material-row-${m.id}`}>
                  <td className="px-6 py-4 font-bold text-[#1A1A1A]">{m.material_name}</td>
                  <td className="px-6 py-4 text-zinc-600">{m.description || <span className="text-zinc-400">—</span>}</td>
                  <td className="px-6 py-4">
                    <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-1 ${m.active ? 'bg-emerald-50 text-emerald-700' : 'bg-zinc-100 text-zinc-500'}`}>
                      {m.active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button onClick={() => setEdit(m)} className="text-zinc-600 hover:text-[#FBAE17] mr-3" data-testid={`material-edit-${m.id}`}><PencilSimple size={18} /></button>
                    <button onClick={() => remove(m)} className="text-zinc-400 hover:text-red-600" data-testid={`material-delete-${m.id}`}><Trash size={18} /></button>
                  </td>
                </tr>
              ))}
              {!items.length && (
                <tr><td colSpan={4} className="px-6 py-12 text-center text-zinc-400">No materials yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {edit && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={() => setEdit(null)}>
          <form onClick={(e) => e.stopPropagation()} onSubmit={save} className="bg-white w-full max-w-lg border border-zinc-200" data-testid="material-form">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <h3 className="font-heading font-black text-lg">{edit.id ? "Edit Material" : "Add Material"}</h3>
              <button type="button" onClick={() => setEdit(null)}><X size={20} /></button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Material Name *</label>
                <input
                  required
                  value={edit.material_name}
                  onChange={(e) => setEdit({ ...edit, material_name: e.target.value })}
                  className="w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
                  data-testid="material-name-input"
                />
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Description</label>
                <textarea
                  rows={3}
                  value={edit.description}
                  onChange={(e) => setEdit({ ...edit, description: e.target.value })}
                  className="w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
                  data-testid="material-desc-input"
                />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} data-testid="material-active-input" />
                Active
              </label>
            </div>
            <div className="px-6 py-4 border-t border-zinc-200 flex justify-end gap-2">
              <button type="button" onClick={() => setEdit(null)} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
              <button type="submit" disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60" data-testid="material-save-btn">
                <Check size={16} weight="bold" /> {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
