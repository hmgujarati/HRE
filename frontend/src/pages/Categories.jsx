import { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Plus, PencilSimple, Trash, CaretRight, CaretDown, X, Check } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function Categories() {
  const [cats, setCats] = useState([]);
  const [mats, setMats] = useState([]);
  const [edit, setEdit] = useState(null);
  const [openIds, setOpenIds] = useState({});

  const load = async () => {
    const c = await api.get("/categories");
    const m = await api.get("/materials");
    setCats(c.data);
    setMats(m.data);
  };

  useEffect(() => { load(); }, []);

  const childrenOf = (parentId) => cats.filter((c) => (c.parent_category_id || null) === (parentId || null));
  const matName = (id) => mats.find((m) => m.id === id)?.material_name || "";

  const toggle = (id) => setOpenIds((s) => ({ ...s, [id]: !s[id] }));

  const renderNode = (cat, depth) => {
    const kids = childrenOf(cat.id);
    const isOpen = openIds[cat.id] !== false;
    return (
      <div key={cat.id}>
        <div
          className="flex items-center gap-2 py-2 border-b border-zinc-100 hover:bg-zinc-50/60 px-3"
          style={{ paddingLeft: 12 + depth * 24 }}
          data-testid={"cat-node-" + cat.id}
        >
          <button onClick={() => toggle(cat.id)} className="text-zinc-500 hover:text-[#FBAE17]">
            {kids.length > 0 && isOpen && <CaretDown size={14} weight="bold" />}
            {kids.length > 0 && !isOpen && <CaretRight size={14} weight="bold" />}
            {kids.length === 0 && <span className="w-3.5 inline-block" />}
          </button>
          <div className="flex-1 min-w-0 flex items-center gap-3">
            <span className="font-medium text-sm text-[#1A1A1A]">{cat.category_name}</span>
            {!cat.active && <span className="text-[10px] uppercase tracking-wider font-bold bg-zinc-100 text-zinc-400 px-2 py-0.5">Inactive</span>}
          </div>
          <button
            onClick={() => setEdit({ category_name: "", material_id: cat.material_id, parent_category_id: cat.id, description: "", active: true })}
            className="text-zinc-500 hover:text-[#FBAE17]"
            title="Add subcategory"
            data-testid={"cat-addchild-" + cat.id}
          >
            <Plus size={16} />
          </button>
          <button onClick={() => setEdit({ ...cat })} className="text-zinc-500 hover:text-[#FBAE17]" data-testid={"cat-edit-" + cat.id}>
            <PencilSimple size={16} />
          </button>
          <button onClick={() => remove(cat)} className="text-zinc-400 hover:text-red-600" data-testid={"cat-delete-" + cat.id}>
            <Trash size={16} />
          </button>
        </div>
        {isOpen && kids.map((k) => renderNode(k, depth + 1))}
      </div>
    );
  };

  const save = async (e) => {
    e.preventDefault();
    try {
      const payload = {
        category_name: edit.category_name,
        material_id: edit.material_id,
        parent_category_id: edit.parent_category_id || null,
        description: edit.description || "",
        active: edit.active ?? true,
      };
      if (edit.id) await api.put("/categories/" + edit.id, payload);
      else await api.post("/categories", payload);
      toast.success("Category saved");
      setEdit(null);
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  const remove = async (c) => {
    if (!window.confirm("Delete category \"" + c.category_name + "\"?")) return;
    try {
      await api.delete("/categories/" + c.id);
      toast.success("Deleted");
      await load();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="Catalogue"
        title="Categories"
        subtitle="Nested category structure organised by base material."
        testId="categories-header"
        actions={
          <button
            onClick={() => setEdit({ category_name: "", material_id: mats[0]?.id || "", parent_category_id: null, description: "", active: true })}
            data-testid="cat-add-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2"
          >
            <Plus size={16} weight="bold" /> Add Category
          </button>
        }
      />

      <div className="p-8 space-y-6">
        {mats.map((m) => {
          const tops = cats.filter((c) => c.material_id === m.id && !c.parent_category_id);
          return (
            <div key={m.id} className="border border-zinc-200 bg-white">
              <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between bg-zinc-50">
                <div className="flex items-center gap-3">
                  <div className="w-2 h-8 bg-[#FBAE17]" />
                  <h3 className="font-heading font-black text-lg">{m.material_name}</h3>
                </div>
                <button
                  onClick={() => setEdit({ category_name: "", material_id: m.id, parent_category_id: null, description: "", active: true })}
                  className="text-xs uppercase tracking-wider font-bold text-zinc-700 hover:text-[#FBAE17] flex items-center gap-1"
                  data-testid={"cat-add-" + m.id}
                >
                  <Plus size={14} weight="bold" /> Add Top-level
                </button>
              </div>
              <div>
                {tops.map((cat) => renderNode(cat, 0))}
                {tops.length === 0 && (
                  <div className="px-6 py-6 text-sm text-zinc-400">No categories under {m.material_name} yet.</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {edit && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={() => setEdit(null)}>
          <form onClick={(e) => e.stopPropagation()} onSubmit={save} className="bg-white w-full max-w-lg border border-zinc-200" data-testid="cat-form">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <h3 className="font-heading font-black text-lg">{edit.id ? "Edit Category" : "Add Category"}</h3>
              <button type="button" onClick={() => setEdit(null)}><X size={20} /></button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Name *</label>
                <input
                  required
                  value={edit.category_name}
                  onChange={(e) => setEdit({ ...edit, category_name: e.target.value })}
                  className="w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
                  data-testid="cat-name-input"
                />
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Material *</label>
                <select required value={edit.material_id} onChange={(e) => setEdit({ ...edit, material_id: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="cat-material-select">
                  <option value="">Select material</option>
                  {mats.map((m) => (<option key={m.id} value={m.id}>{m.material_name}</option>))}
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Parent Category</label>
                <select value={edit.parent_category_id || ""} onChange={(e) => setEdit({ ...edit, parent_category_id: e.target.value || null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="cat-parent-select">
                  <option value="">None (top-level)</option>
                  {cats.filter((c) => c.material_id === edit.material_id && c.id !== edit.id).map((c) => (
                    <option key={c.id} value={c.id}>{c.category_name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Description</label>
                <textarea rows={2} value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active
              </label>
            </div>
            <div className="px-6 py-4 border-t border-zinc-200 flex justify-end gap-2">
              <button type="button" onClick={() => setEdit(null)} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
              <button type="submit" className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2" data-testid="cat-save-btn">
                <Check size={16} weight="bold" /> Save
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
