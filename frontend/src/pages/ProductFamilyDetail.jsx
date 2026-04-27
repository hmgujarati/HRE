import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import api, { formatApiError, fileUrl } from "@/lib/api";
import PageHeader from "@/components/PageHeader";
import { ArrowLeft, UploadSimple, Image as ImageIcon, Plus, FileXls, CheckCircle, PencilSimple, Trash, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import VariantFormDialog from "@/components/VariantFormDialog";

function UploadCard({ title, url, onUpload, testId }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);

  const handle = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      await onUpload(f);
      toast.success(`${title} uploaded`);
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border border-zinc-200 bg-white">
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500">{title}</div>
        <button onClick={() => inputRef.current?.click()} disabled={busy} className="text-xs uppercase font-bold tracking-wider text-zinc-700 hover:text-[#FBAE17] flex items-center gap-1 disabled:opacity-50" data-testid={`${testId}-upload-btn`}>
          <UploadSimple size={14} weight="bold" /> {busy ? "…" : "Upload"}
        </button>
        <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={handle} />
      </div>
      <div className="aspect-[4/3] bg-zinc-50 flex items-center justify-center overflow-hidden">
        {url ? (
          <img src={fileUrl(url)} alt={title} className="w-full h-full object-contain p-4" data-testid={`${testId}-img`} />
        ) : (
          <div className="text-zinc-300 flex flex-col items-center gap-2">
            <ImageIcon size={42} weight="thin" />
            <span className="text-xs uppercase tracking-wider">No image yet</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function ProductFamilyDetail() {
  const { id } = useParams();
  const [family, setFamily] = useState(null);
  const [variants, setVariants] = useState([]);
  const [mats, setMats] = useState([]);
  const [cats, setCats] = useState([]);
  const [variantOpen, setVariantOpen] = useState(null);

  const load = async () => {
    const [fr, vr, mr, cr] = await Promise.all([
      api.get(`/product-families/${id}`),
      api.get(`/product-variants`, { params: { product_family_id: id } }),
      api.get("/materials"),
      api.get("/categories"),
    ]);
    // Sort: cable size mm² ascending, then hole size, then product code
    const numericPart = (s) => {
      const m = String(s || "").match(/[\d.]+/);
      return m ? parseFloat(m[0]) : Number.POSITIVE_INFINITY;
    };
    const sorted = [...vr.data].sort((a, b) => {
      const ca = numericPart(a.cable_size);
      const cb = numericPart(b.cable_size);
      if (ca !== cb) return ca - cb;
      const ha = numericPart(a.hole_size);
      const hb = numericPart(b.hole_size);
      if (ha !== hb) return ha - hb;
      return String(a.product_code).localeCompare(String(b.product_code));
    });
    setFamily(fr.data); setVariants(sorted); setMats(mr.data); setCats(cr.data);
  };

  useEffect(() => { load(); }, [id]);

  const upload = async (file, kind) => {
    const fd = new FormData();
    fd.append("file", file);
    const url = kind === "main" ? `/product-families/${id}/upload-image` : `/product-families/${id}/upload-dimension-drawing`;
    await api.post(url, fd, { headers: { "Content-Type": "multipart/form-data" } });
    await load();
  };

  const removeVariant = async (v) => {
    if (!window.confirm(`Delete variant ${v.product_code}? This cannot be undone.`)) return;
    try {
      await api.delete(`/product-variants/${v.id}`);
      toast.success(`Deleted ${v.product_code}`);
      await load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  const removeAllVariants = async () => {
    if (!variants.length) return;
    if (!window.confirm(`Delete ALL ${variants.length} variants in this family? This cannot be undone.`)) return;
    if (!window.confirm("Really delete every variant? Type confirms only — last chance to cancel.")) return;
    try {
      const results = await Promise.allSettled(variants.map((v) => api.delete(`/product-variants/${v.id}`)));
      const ok = results.filter((r) => r.status === "fulfilled").length;
      toast.success(`Deleted ${ok} of ${variants.length} variants`);
      await load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  if (!family) return <div className="p-8 text-zinc-400">Loading…</div>;

  const matName = mats.find((m) => m.id === family.material_id)?.material_name;
  const catName = cats.find((c) => c.id === family.category_id)?.category_name;
  const subName = cats.find((c) => c.id === family.subcategory_id)?.category_name;

  // collect all dimension keys across variants
  const dimKeys = Array.from(new Set(variants.flatMap((v) => Object.keys(v.dimensions || {}))));

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={matName}
        title={family.family_name}
        subtitle={`${catName || ""}${subName ? " · " + subName : ""}`}
        testId="family-detail-header"
        actions={
          <Link to="/product-families" className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-50 flex items-center gap-2">
            <ArrowLeft size={14} weight="bold" /> Back
          </Link>
        }
      />
      <div className="p-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-6">
          <UploadCard title="Product Image" url={family.main_product_image} onUpload={(f) => upload(f, "main")} testId="main-image" />
          <UploadCard title="Dimension Drawing" url={family.dimension_drawing_image} onUpload={(f) => upload(f, "dim")} testId="dim-image" />
        </div>

        <div className="lg:col-span-2 space-y-6">
          <div className="border border-zinc-200 bg-white p-6 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
            <Spec label="Material" value={family.material_description} />
            <Spec label="Specification" value={family.specification_description} />
            <Spec label="Finish" value={family.finish_description} />
            <Spec label="Standard" value={family.standard_reference} />
            {family.insulation_colour_coding && <Spec label="Colour Coding" value={family.insulation_colour_coding} span />}
            {family.description && <Spec label="Description" value={family.description} span />}
          </div>

          <div className="border border-zinc-200 bg-white">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between">
              <div>
                <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Catalogue</div>
                <h3 className="font-heading font-black text-lg">Variants ({variants.length})</h3>
              </div>
              <div className="flex items-center gap-2">
                <ExcelImport familyId={family.id} onDone={load} />
                {variants.length > 0 && (
                  <button
                    onClick={removeAllVariants}
                    data-testid="family-delete-all-btn"
                    className="border border-red-300 text-red-600 hover:bg-red-50 font-bold uppercase tracking-wider text-xs px-3 py-2 flex items-center gap-2 transition-colors"
                    title="Delete all variants in this family"
                  >
                    <Warning size={14} weight="bold" /> Delete All
                  </button>
                )}
                <button
                  onClick={() => setVariantOpen({ family })}
                  data-testid="family-add-variant-btn"
                  className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2"
                >
                  <Plus size={14} weight="bold" /> Add Variant
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-zinc-50">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                    <th className="px-4 py-3">Code</th>
                    <th className="px-4 py-3">Cable</th>
                    <th className="px-4 py-3">Hole</th>
                    {dimKeys.map((k) => <th key={k} className="px-3 py-3 font-mono">{k}</th>)}
                    <th className="px-4 py-3 text-right">Final ₹</th>
                    <th className="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {variants.map((v) => (
                    <tr key={v.id} className="border-t border-zinc-100 hover:bg-zinc-50/60" data-testid={`fam-variant-row-${v.id}`}>
                      <td className="px-4 py-2 font-mono font-bold text-[#1A1A1A]">{v.product_code}</td>
                      <td className="px-4 py-2 font-mono">{v.cable_size}</td>
                      <td className="px-4 py-2 font-mono">{v.hole_size || '—'}</td>
                      {dimKeys.map((k) => <td key={k} className="px-3 py-2 font-mono text-zinc-600">{v.dimensions?.[k] ?? ''}</td>)}
                      <td className="px-4 py-2 text-right font-mono font-bold">₹{v.final_price}</td>
                      <td className="px-4 py-2 text-right whitespace-nowrap">
                        <button
                          onClick={() => setVariantOpen({ family, existing: v })}
                          className="text-zinc-500 hover:text-[#FBAE17] mr-3"
                          data-testid={`fam-variant-edit-${v.id}`}
                          title="Edit variant"
                        >
                          <PencilSimple size={14} />
                        </button>
                        <button
                          onClick={() => removeVariant(v)}
                          className="text-zinc-400 hover:text-red-600"
                          data-testid={`fam-variant-delete-${v.id}`}
                          title="Delete variant"
                        >
                          <Trash size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!variants.length && (
                    <tr><td colSpan={dimKeys.length + 5} className="px-6 py-8 text-center text-zinc-400">No variants yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {variantOpen && (
        <VariantFormDialog
          existing={variantOpen.existing || null}
          initial={!variantOpen.existing ? { product_family_id: family.id, material_id: family.material_id, category_id: family.category_id, subcategory_id: family.subcategory_id } : null}
          onClose={() => setVariantOpen(null)}
          onSaved={load}
          mats={mats}
          cats={cats}
        />
      )}
    </div>
  );
}

function Spec({ label, value, span }) {
  return (
    <div className={span ? "md:col-span-2" : ""}>
      <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-zinc-500 mb-0.5">{label}</div>
      <div className="text-sm text-[#1A1A1A]">{value || <span className="text-zinc-300">—</span>}</div>
    </div>
  );
}

function ExcelImport({ familyId, onDone }) {
  const ref = useRef(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const handle = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    e.target.value = "";
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const { data } = await api.post(`/product-families/${familyId}/upload-variants-excel`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setResult(data);
      toast.success(`Imported ${data.created} new, updated ${data.updated}`);
      onDone && onDone();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        onClick={() => ref.current?.click()}
        disabled={busy}
        data-testid="family-excel-import-btn"
        className="border border-zinc-300 hover:border-[#FBAE17] hover:bg-zinc-50 text-zinc-800 font-bold uppercase tracking-wider text-xs px-4 py-2 flex items-center gap-2 disabled:opacity-60 transition-colors"
        title="Import variants from Excel (.xlsx)"
      >
        <FileXls size={14} weight="bold" /> {busy ? "Importing…" : "Import Excel"}
      </button>
      <input ref={ref} type="file" accept=".xlsx,.xlsm" className="hidden" onChange={handle} />
      {result && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={() => setResult(null)}>
          <div onClick={(e) => e.stopPropagation()} className="bg-white w-full max-w-lg border border-zinc-200" data-testid="excel-import-result">
            <div className="px-6 py-4 border-b border-zinc-200 flex items-center gap-2">
              <CheckCircle size={18} weight="fill" className="text-emerald-600" />
              <h3 className="font-heading font-black text-lg">Import Complete</h3>
            </div>
            <div className="p-6 space-y-3">
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="border border-zinc-200 p-3">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Created</div>
                  <div className="font-heading font-black text-2xl text-emerald-600">{result.created}</div>
                </div>
                <div className="border border-zinc-200 p-3">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Updated</div>
                  <div className="font-heading font-black text-2xl text-[#FBAE17]">{result.updated}</div>
                </div>
                <div className="border border-zinc-200 p-3">
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">Skipped</div>
                  <div className="font-heading font-black text-2xl text-zinc-400">{result.skipped}</div>
                </div>
              </div>
              {result.headers_detected && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1">Detected Columns</div>
                  <div className="flex flex-wrap gap-1">
                    {result.headers_detected.filter(Boolean).map((h, i) => (
                      <span key={i} className="text-[10px] uppercase tracking-wider font-bold bg-zinc-100 text-zinc-700 px-2 py-0.5 font-mono">{h}</span>
                    ))}
                  </div>
                </div>
              )}
              {result.errors?.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900 max-h-32 overflow-y-auto">
                  {result.errors.map((e, i) => <div key={i}>{e}</div>)}
                </div>
              )}
              <p className="text-xs text-zinc-500 pt-2 border-t border-zinc-100">
                Tip: column headers can be Cable Size, Hole, Prod. Code, plus any dimension keys (A, B, C, D, F, H, K, L1, J, etc.). Prices default to ₹0 — set them via Edit Variant or Bulk Discount.
              </p>
            </div>
            <div className="px-6 py-3 border-t border-zinc-200 flex justify-end">
              <button onClick={() => setResult(null)} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-4 py-2">Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
