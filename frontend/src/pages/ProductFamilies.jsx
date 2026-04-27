import { useEffect, useState } from "react";
import api, { formatApiError, fileUrl } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { Plus, PencilSimple, Trash, X, Check, Image as ImageIcon, ArrowRight } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

const empty = {
  family_name: "", short_name: "", material_id: "", category_id: "", subcategory_id: null,
  product_type: "", catalogue_title: "", material_description: "", specification_description: "",
  finish_description: "", insulation_colour_coding: "", standard_reference: "", description: "", active: true,
};

export default function ProductFamilies() {
  const [items, setItems] = useState([]);
  const [mats, setMats] = useState([]);
  const [cats, setCats] = useState([]);
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const [f, m, c] = await Promise.all([
      api.get("/product-families"),
      api.get("/materials"),
      api.get("/categories"),
    ]);
    setItems(f.data); setMats(m.data); setCats(c.data);
  };
  useEffect(() => { load(); }, []);

  const matName = (id) => mats.find((m) => m.id === id)?.material_name || "—";
  const catName = (id) => cats.find((c) => c.id === id)?.category_name || "—";
  const topCats = (matId) => cats.filter((c) => c.material_id === matId && !c.parent_category_id);
  const subCats = (parentId) => cats.filter((c) => c.parent_category_id === parentId);

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = { ...edit };
      delete payload.created_at; delete payload.updated_at;
      delete payload.main_product_image; delete payload.dimension_drawing_image; delete payload.catalogue_reference_image;
      if (edit.id) await api.put(`/product-families/${edit.id}`, payload);
      else await api.post("/product-families", payload);
      toast.success("Family saved");
      setEdit(null);
      await load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (f) => {
    if (!window.confirm(`Delete family "${f.family_name}"?`)) return;
    try {
      await api.delete(`/product-families/${f.id}`);
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
        title="Product Families"
        subtitle="Each family stores catalogue specifications, technical descriptions, and images shared by all of its variants."
        testId="families-header"
        actions={
          <button
            onClick={() => setEdit({ ...empty, material_id: mats[0]?.id || "" })}
            data-testid="family-add-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2"
          >
            <Plus size={16} weight="bold" /> Add Family
          </button>
        }
      />

      <div className="p-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {items.map((f) => (
          <div key={f.id} className="border border-zinc-200 bg-white flex flex-col group hover:border-[#FBAE17] transition-colors" data-testid={`family-card-${f.id}`}>
            <div className="aspect-[4/3] bg-zinc-50 border-b border-zinc-200 relative flex items-center justify-center overflow-hidden">
              {f.main_product_image ? (
                <img src={fileUrl(f.main_product_image)} alt={f.family_name} className="w-full h-full object-contain p-4" />
              ) : (
                <div className="text-zinc-300 flex flex-col items-center gap-2">
                  <ImageIcon size={42} weight="thin" />
                  <span className="text-xs uppercase tracking-wider">No image</span>
                </div>
              )}
              {!f.active && <span className="absolute top-3 left-3 text-[10px] uppercase tracking-wider font-bold bg-zinc-900 text-white px-2 py-0.5">Inactive</span>}
              <span className="absolute top-3 right-3 text-[10px] uppercase tracking-wider font-bold bg-[#FBAE17] text-black px-2 py-0.5">{matName(f.material_id)}</span>
            </div>
            <div className="p-5 flex-1 flex flex-col">
              <div className="text-[10px] uppercase tracking-[0.22em] text-zinc-500 font-bold mb-1">{f.product_type || "Family"}</div>
              <h3 className="font-heading font-black text-base text-[#1A1A1A] leading-snug line-clamp-2">{f.family_name}</h3>
              <div className="text-xs text-zinc-500 mt-2 line-clamp-2">{f.specification_description || f.material_description}</div>
              <div className="mt-4 flex items-center justify-between pt-3 border-t border-zinc-100">
                <Link to={`/product-families/${f.id}`} className="text-xs uppercase tracking-wider font-bold text-[#1A1A1A] hover:text-[#FBAE17] flex items-center gap-1" data-testid={`family-view-${f.id}`}>
                  View Details <ArrowRight size={14} weight="bold" />
                </Link>
                <div className="flex items-center gap-3">
                  <button onClick={() => setEdit({ ...f })} className="text-zinc-500 hover:text-[#FBAE17]" data-testid={`family-edit-${f.id}`}><PencilSimple size={16} /></button>
                  <button onClick={() => remove(f)} className="text-zinc-400 hover:text-red-600" data-testid={`family-delete-${f.id}`}><Trash size={16} /></button>
                </div>
              </div>
            </div>
          </div>
        ))}
        {!items.length && <div className="col-span-full text-zinc-400 text-sm">No product families yet.</div>}
      </div>

      {edit && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6 overflow-y-auto" onClick={() => setEdit(null)}>
          <form onClick={(e) => e.stopPropagation()} onSubmit={save} className="bg-white w-full max-w-3xl border border-zinc-200 my-8" data-testid="family-form">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <h3 className="font-heading font-black text-lg">{edit.id ? "Edit Family" : "Add Family"}</h3>
              <button type="button" onClick={() => setEdit(null)}><X size={20} /></button>
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Family Name *</label>
                <input required value={edit.family_name} onChange={(e) => setEdit({ ...edit, family_name: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" data-testid="family-name-input" />
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Short Name</label>
                <input value={edit.short_name || ""} onChange={(e) => setEdit({ ...edit, short_name: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Product Type</label>
                <input value={edit.product_type || ""} onChange={(e) => setEdit({ ...edit, product_type: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Material *</label>
                <select required value={edit.material_id} onChange={(e) => setEdit({ ...edit, material_id: e.target.value, category_id: "", subcategory_id: null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="family-mat-select">
                  <option value="">Select</option>
                  {mats.map((m) => <option key={m.id} value={m.id}>{m.material_name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Category *</label>
                <select required value={edit.category_id} onChange={(e) => setEdit({ ...edit, category_id: e.target.value, subcategory_id: null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white" data-testid="family-cat-select">
                  <option value="">Select</option>
                  {topCats(edit.material_id).map((c) => <option key={c.id} value={c.id}>{c.category_name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Subcategory</label>
                <select value={edit.subcategory_id || ""} onChange={(e) => setEdit({ ...edit, subcategory_id: e.target.value || null })} className="w-full border border-zinc-300 px-3 py-2 text-sm bg-white">
                  <option value="">None</option>
                  {subCats(edit.category_id).map((c) => <option key={c.id} value={c.id}>{c.category_name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Standard Reference</label>
                <input value={edit.standard_reference || ""} onChange={(e) => setEdit({ ...edit, standard_reference: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              </div>
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Catalogue Title</label>
                <input value={edit.catalogue_title || ""} onChange={(e) => setEdit({ ...edit, catalogue_title: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              </div>
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Material Description</label>
                <input value={edit.material_description || ""} onChange={(e) => setEdit({ ...edit, material_description: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" placeholder="Copper Strip / Tape to IS-1897" />
              </div>
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Specification</label>
                <input value={edit.specification_description || ""} onChange={(e) => setEdit({ ...edit, specification_description: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" placeholder="E.C. Grade 99.25% IACS" />
              </div>
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Finish</label>
                <input value={edit.finish_description || ""} onChange={(e) => setEdit({ ...edit, finish_description: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" placeholder="Electro Tinned to BS 1872 (1984)" />
              </div>
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Insulation Colour Coding</label>
                <input value={edit.insulation_colour_coding || ""} onChange={(e) => setEdit({ ...edit, insulation_colour_coding: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" placeholder="1.5 = Red, 2.5 = Blue, 4-6 = Yellow" />
              </div>
              <div className="md:col-span-2">
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-2 block">Description</label>
                <textarea rows={3} value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} className="w-full border border-zinc-300 px-3 py-2 text-sm" />
              </div>
              <label className="flex items-center gap-2 text-sm md:col-span-2">
                <input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active
              </label>
            </div>
            <div className="px-6 py-4 border-t border-zinc-200 flex justify-end gap-2">
              <button type="button" onClick={() => setEdit(null)} className="px-4 py-2 border border-zinc-300 text-sm font-bold uppercase tracking-wider hover:bg-zinc-50">Cancel</button>
              <button type="submit" disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60" data-testid="family-save-btn">
                <Check size={16} weight="bold" /> {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
