import { useEffect, useState, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowLeft, FileArrowUp, FileText, Package, Truck, ClipboardText,
  CheckCircle, Clock, PaperPlaneTilt, ArrowRight, FileArrowDown, WhatsappLogo,
  ArrowClockwise, EnvelopeSimple,
} from "@phosphor-icons/react";
import { StageBadge, STAGE_LABELS, STAGE_ORDER } from "./Orders";

const STAGE_ICONS = {
  pending_po: ClipboardText, po_received: FileText, proforma_issued: FileText,
  order_placed: ClipboardText, raw_material_check: Package, procuring_raw_material: Package,
  in_production: Package, packaging: Package, dispatched: Truck, lr_received: Truck, delivered: CheckCircle,
};

export default function OrderView() {
  const { id } = useParams();
  const [order, setOrder] = useState(null);
  const [busy, setBusy] = useState(false);
  const [productionNote, setProductionNote] = useState("");

  const load = async () => {
    try {
      const { data } = await api.get(`/orders/${id}`);
      setOrder(data);
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };
  useEffect(() => { load(); }, [id]);

  if (!order) return <div className="p-8 text-zinc-400">Loading…</div>;

  const advance = async (stage, note = "") => {
    const target = STAGE_LABELS[stage] || stage;
    if (!window.confirm(`Are you sure you want to move this order forward to "${target}"?\n\nThis will trigger automatic WhatsApp + Email notifications to the customer (if configured) and cannot be undone.`)) return;
    setBusy(true);
    try {
      await api.post(`/orders/${id}/advance`, { stage, note });
      toast.success(`Moved to ${target}`);
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const setRawMaterial = async (status) => {
    setBusy(true);
    try {
      await api.post(`/orders/${id}/raw-material`, { status });
      toast.success("Raw material status updated");
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const addProductionNote = async () => {
    if (!productionNote.trim()) return;
    setBusy(true);
    try {
      await api.post(`/orders/${id}/production-update`, { note: productionNote });
      setProductionNote("");
      toast.success("Production note added");
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const generatePI = async () => {
    setBusy(true);
    try {
      await api.post(`/orders/${id}/proforma/generate`);
      toast.success("Proforma Invoice generated");
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const generateInvoice = async () => {
    if (!window.confirm("Generate a Tax Invoice PDF from this order's line items?\n\nIf an invoice already exists, it will be regenerated with the same number.")) return;
    setBusy(true);
    try {
      await api.post(`/orders/${id}/invoice/generate`);
      toast.success("Tax Invoice generated");
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const saveExpectedCompletion = async (date) => {
    setBusy(true);
    try {
      await api.put(`/orders/${id}/expected-completion`, { date: date || null });
      toast.success(date ? "Expected completion date saved" : "Expected completion date cleared");
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const refireNotification = async () => {
    if (!window.confirm("Re-send the most recent customer notification on WhatsApp + Email?\n\nThis will not advance the stage — only re-fire the last update.")) return;
    setBusy(true);
    try {
      const { data } = await api.post(`/orders/${id}/refire-notification`);
      const last = (data.notifications || []).slice(-1)[0] || {};
      const channels = [];
      if (last.whatsapp) channels.push("WhatsApp");
      if (last.email) channels.push("Email");
      toast.success(channels.length ? `Re-fired on ${channels.join(" + ")}` : "Re-fire attempted — see logs");
      load();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  return (
    <div className="animate-fade-in">
      <div className="px-4 sm:px-8 py-4 border-b border-zinc-200 bg-zinc-50 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <Link to="/orders" className="text-zinc-500 hover:text-[#FBAE17]" data-testid="order-back">
            <ArrowLeft size={18} weight="bold" />
          </Link>
          <div>
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17]">Order</div>
            <div className="font-heading font-black text-lg sm:text-xl">{order.order_number}</div>
          </div>
          <StageBadge stage={order.stage} />
        </div>
        <div className="text-right text-xs text-zinc-500 font-mono">
          ₹{(order.grand_total || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })} · {order.line_items?.length || 0} items
        </div>
      </div>

      <div className="px-4 sm:px-8 py-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Stage actions */}
        <div className="lg:col-span-2 space-y-6">
          <ContactCard order={order} />

          <ExpectedCompletionEditor order={order} onSave={saveExpectedCompletion} busy={busy} />

          <StageActions
            order={order}
            onAdvance={advance}
            onRawMaterial={setRawMaterial}
            onGeneratePI={generatePI}
            onGenerateInvoice={generateInvoice}
            onUploaded={load}
            busy={busy}
          />

          {/* Production Updates */}
          {STAGE_ORDER.indexOf(order.stage) >= STAGE_ORDER.indexOf("order_placed") && (
            <div className="border border-zinc-200 bg-white">
              <div className="px-5 py-4 border-b border-zinc-200">
                <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Production Updates</div>
                <h3 className="font-heading font-black text-lg">Floor notes</h3>
                <div className="text-xs text-zinc-500">Add free-form updates from the production floor (process, %, blockers).</div>
              </div>
              <div className="p-5">
                <div className="flex gap-2 mb-4">
                  <input
                    value={productionNote}
                    onChange={(e) => setProductionNote(e.target.value)}
                    placeholder="e.g. In crimping process, 60% done"
                    className="flex-1 border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
                    data-testid="production-note-input"
                    onKeyDown={(e) => e.key === "Enter" && addProductionNote()}
                  />
                  <button onClick={addProductionNote} disabled={busy || !productionNote.trim()} data-testid="production-note-submit" className="bg-[#1A1A1A] hover:bg-black text-white text-xs font-bold uppercase tracking-wider px-4 py-2 disabled:opacity-50">
                    Add
                  </button>
                </div>
                {(order.production_updates || []).length === 0 ? (
                  <div className="text-xs text-zinc-400">No updates yet.</div>
                ) : (
                  <div className="space-y-2">
                    {[...order.production_updates].reverse().map((u) => (
                      <div key={u.id} className="border-l-2 border-[#FBAE17] pl-3 py-1">
                        <div className="text-sm">{u.note}</div>
                        <div className="text-[10px] text-zinc-500 font-mono">{new Date(u.at).toLocaleString()} · {u.by}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Timeline */}
          <div className="border border-zinc-200 bg-white">
            <div className="px-5 py-4 border-b border-zinc-200">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Audit Trail</div>
              <h3 className="font-heading font-black text-lg">Timeline</h3>
            </div>
            <div className="p-5 space-y-3">
              {[...(order.timeline || [])].reverse().map((ev) => {
                const Icon = STAGE_ICONS[ev.stage] || ClipboardText;
                return (
                  <div key={ev.id} className="flex gap-3 text-sm" data-testid={`timeline-${ev.id}`}>
                    <div className="shrink-0 w-7 h-7 bg-zinc-100 flex items-center justify-center">
                      <Icon size={14} weight="bold" className="text-zinc-700" />
                    </div>
                    <div className="flex-1">
                      <div className="font-medium">{ev.label}</div>
                      {ev.note && <div className="text-xs text-zinc-600 mt-0.5">{ev.note}</div>}
                      <div className="text-[10px] text-zinc-500 font-mono">{new Date(ev.at).toLocaleString()} · {ev.by}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right: Documents + Notifications */}
        <div className="space-y-6">
          <div className="border border-zinc-200 bg-white">
            <div className="px-5 py-4 border-b border-zinc-200">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Documents</div>
              <h3 className="font-heading font-black text-lg">Files</h3>
            </div>
            <div className="p-5 space-y-3 text-sm">
              <DocRow label="Purchase Order" doc={order.documents?.po} number={order.po_number} />
              <DocRow label="Proforma Invoice" doc={order.proforma?.url ? order.proforma : null} number={order.proforma?.number} />
              <DocRow label="Tax Invoice" doc={order.documents?.invoice} number={order.documents?.invoice?.number} />
              <DocRow label="E-way Bill" doc={order.documents?.eway_bill} number={order.documents?.eway_bill?.number} />
              <DocRow label="LR Copy" doc={order.documents?.lr} number={order.documents?.lr?.number} />
            </div>
          </div>

          {(order.notifications || []).length > 0 && (
            <div className="border border-zinc-200 bg-white">
              <div className="px-5 py-4 border-b border-zinc-200 flex items-center justify-between flex-wrap gap-2">
                <div>
                  <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Auto Notifications</div>
                  <h3 className="font-heading font-black text-lg">Customer pings</h3>
                </div>
                <button
                  onClick={refireNotification}
                  disabled={busy}
                  data-testid="refire-notification-btn"
                  className="text-[10px] uppercase tracking-wider font-bold bg-[#1A1A1A] hover:bg-black text-white px-3 py-2 disabled:opacity-50 flex items-center gap-1.5"
                  title="Re-send the most recent stage / production-update notification on WhatsApp + Email"
                >
                  <ArrowClockwise size={12} weight="bold" /> Re-fire last
                </button>
              </div>
              <div className="p-5 space-y-2 text-xs">
                {[...order.notifications].reverse().map((n, i) => {
                  const isProdUpdate = n.kind === "production_update";
                  // Status hierarchy: failed → sent → delivered → read
                  const waLevel = n.whatsapp_error ? "failed" : (n.whatsapp_status || (n.whatsapp ? "sent" : "—"));
                  const emLevel = n.email_error ? "failed" : (n.email_status || (n.email ? "sent" : "—"));
                  const STATUS_COLORS = {
                    "—": "text-zinc-400", "sent": "text-emerald-700", "delivered": "text-blue-700",
                    "read": "text-violet-700", "failed": "text-red-600", "accepted": "text-emerald-700",
                    "pending": "text-zinc-400",
                  };
                  return (
                    <div key={i} className={`border-l-2 ${n.whatsapp || n.email ? "border-emerald-400" : "border-red-400"} pl-3 py-1.5`}>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-bold uppercase tracking-wider text-[10px]">{isProdUpdate ? "Floor update" : (STAGE_LABELS[n.stage] || n.stage)}</span>
                        {n.refire_of && <span className="text-[9px] uppercase tracking-wider font-bold bg-zinc-100 text-zinc-600 px-1.5 py-0.5">re-fire</span>}
                        <span className="text-[10px] text-zinc-500 ml-auto font-mono">{n.at ? new Date(n.at).toLocaleString() : ""}</span>
                      </div>
                      <div className="flex items-center gap-3 mt-1 flex-wrap">
                        <span className="flex items-center gap-1">
                          <WhatsappLogo size={11} weight="fill" className={n.whatsapp ? "text-[#25D366]" : "text-zinc-300"} />
                          <span className={`text-[10px] uppercase tracking-wider font-bold ${STATUS_COLORS[waLevel] || "text-zinc-500"}`}>WA · {waLevel}</span>
                          {n.whatsapp_status_updated_at && waLevel !== "sent" && (
                            <span className="text-[9px] font-mono text-zinc-400">@ {new Date(n.whatsapp_status_updated_at).toLocaleTimeString()}</span>
                          )}
                        </span>
                        <span className="flex items-center gap-1">
                          <EnvelopeSimple size={11} weight="fill" className={n.email ? "text-[#FBAE17]" : "text-zinc-300"} />
                          <span className={`text-[10px] uppercase tracking-wider font-bold ${STATUS_COLORS[emLevel] || "text-zinc-500"}`}>Email · {emLevel}</span>
                          {n.email_status_updated_at && emLevel === "read" && (
                            <span className="text-[9px] font-mono text-zinc-400">@ {new Date(n.email_status_updated_at).toLocaleTimeString()}</span>
                          )}
                        </span>
                      </div>
                      {isProdUpdate && n.note && <div className="text-zinc-600 text-[11px] mt-1 italic">"{n.note}"</div>}
                      {(n.whatsapp_error || n.email_error) && (
                        <div className="text-red-600 text-[10px] mt-1 leading-snug">
                          {n.whatsapp_error && <div>WA: {n.whatsapp_error}</div>}
                          {n.email_error && <div>Email: {n.email_error}</div>}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ContactCard({ order }) {
  return (
    <div className="border border-zinc-200 bg-white p-5 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
      <div>
        <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Customer</div>
        <div className="font-bold">{order.contact_name}</div>
        {order.contact_company && <div className="text-zinc-600">{order.contact_company}</div>}
        <div className="text-xs text-zinc-500 font-mono mt-1">{order.contact_phone} · {order.contact_email}</div>
        {order.contact_gst && <div className="text-xs text-zinc-500 font-mono">GST: {order.contact_gst}</div>}
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Source Quote</div>
        <Link to={`/quotations/${order.quote_id}`} className="font-mono font-bold text-[#1A1A1A] hover:text-[#FBAE17]">{order.quote_number}</Link>
        <div className="text-xs text-zinc-500 mt-1">PO: <span className="font-mono font-bold text-[#1A1A1A]">{order.po_number || "—"}</span></div>
        <div className="text-xs text-zinc-500">Place of Supply: {order.place_of_supply || "—"}</div>
      </div>
    </div>
  );
}

function DocRow({ label, doc, number }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-zinc-100 pb-2 last:border-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-500">{label}</div>
        {doc ? (
          <div className="font-mono text-xs truncate" title={doc.original_name || doc.filename}>{number || doc.original_name || doc.filename}</div>
        ) : <div className="text-xs text-zinc-400 italic">Not uploaded</div>}
      </div>
      {doc?.url && (
        <a href={doc.url} target="_blank" rel="noreferrer" className="shrink-0 text-[10px] uppercase tracking-wider font-bold border border-zinc-300 hover:border-[#FBAE17] px-2 py-1 flex items-center gap-1">
          <FileArrowDown size={10} weight="bold" /> View
        </a>
      )}
    </div>
  );
}

function ExpectedCompletionEditor({ order, onSave, busy }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(order.expected_completion_date || "");
  useEffect(() => { setVal(order.expected_completion_date || ""); }, [order.expected_completion_date]);
  const display = order.expected_completion_date
    ? new Date(order.expected_completion_date + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })
    : "Not set";
  const save = async () => {
    await onSave(val);
    setEditing(false);
  };
  return (
    <div className="border border-zinc-200 bg-white p-5">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Expected Completion</div>
          <div className="text-xs text-zinc-500 mb-2">Visible to the customer in every WhatsApp + Email update.</div>
          {!editing ? (
            <div className="flex items-center gap-3">
              <span className={`text-lg font-bold ${order.expected_completion_date ? "text-[#1A1A1A]" : "text-zinc-400 italic"}`} data-testid="eta-display">{display}</span>
              <button onClick={() => setEditing(true)} className="text-xs uppercase font-bold tracking-wider text-[#FBAE17] hover:text-[#E59D12]" data-testid="eta-edit-btn">
                {order.expected_completion_date ? "Change" : "Set"}
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="date"
                value={val}
                onChange={(e) => setVal(e.target.value)}
                className="border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
                data-testid="eta-input"
                min={new Date().toISOString().slice(0, 10)}
              />
              <button onClick={save} disabled={busy} className="bg-[#FBAE17] hover:bg-[#E59D12] text-black text-xs uppercase tracking-wider font-bold px-3 py-2 disabled:opacity-50" data-testid="eta-save-btn">Save</button>
              {order.expected_completion_date && (
                <button onClick={() => onSave("")} disabled={busy} className="text-xs uppercase tracking-wider font-bold text-red-500 hover:text-red-700 px-2" data-testid="eta-clear-btn">Clear</button>
              )}
              <button onClick={() => { setEditing(false); setVal(order.expected_completion_date || ""); }} className="text-xs uppercase tracking-wider font-bold text-zinc-500 hover:text-black px-2">Cancel</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function StageActions({ order, onAdvance, onRawMaterial, onGeneratePI, onGenerateInvoice, onUploaded, busy }) {
  const stage = order.stage;
  return (
    <div className="border border-zinc-200 bg-white p-5 space-y-4">
      <div>
        <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Stage Actions</div>
        <h3 className="font-heading font-black text-lg">What's next?</h3>
      </div>

      {stage === "pending_po" && (
        <UploadAction
          label="Upload Purchase Order"
          orderId={order.id}
          path="upload-po"
          extraFields={[{ name: "po_number", placeholder: "Buyer PO #", value: order.po_number || "" }]}
          fileLabel="Purchase Order PDF"
          icon={FileArrowUp}
          color="bg-blue-600 hover:bg-blue-700"
          onDone={onUploaded}
        />
      )}

      {stage === "po_received" && (
        <div className="space-y-3">
          <button onClick={onGeneratePI} disabled={busy} data-testid="generate-pi-btn" className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
            <FileText size={14} weight="bold" /> Auto-generate Proforma Invoice PDF
          </button>
          <div className="text-center text-[10px] uppercase tracking-wider text-zinc-400 font-bold">— or —</div>
          <UploadAction
            label="Upload your own PI"
            orderId={order.id}
            path="proforma/upload"
            extraFields={[{ name: "pi_number", placeholder: "PI Number" }]}
            fileLabel="Proforma Invoice PDF"
            icon={FileArrowUp}
            color="bg-zinc-700 hover:bg-zinc-800"
            onDone={onUploaded}
          />
        </div>
      )}

      {stage === "proforma_issued" && (
        <button onClick={() => onAdvance("order_placed", "Order placed with factory")} disabled={busy} data-testid="advance-order-placed" className="w-full bg-amber-600 hover:bg-amber-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
          <ArrowRight size={14} weight="bold" /> Place Order with Factory
        </button>
      )}

      {(stage === "order_placed" || stage === "raw_material_check") && (
        <div className="space-y-2">
          <div className="text-xs text-zinc-600 mb-1">Raw material status:</div>
          <button onClick={() => onRawMaterial("available")} disabled={busy} data-testid="rm-available" className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
            <CheckCircle size={14} weight="fill" /> RM Available · Start Production
          </button>
          <button onClick={() => onRawMaterial("procuring")} disabled={busy} data-testid="rm-procuring" className="w-full bg-orange-600 hover:bg-orange-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
            <Clock size={14} weight="bold" /> Procuring Raw Material
          </button>
        </div>
      )}

      {stage === "procuring_raw_material" && (
        <button onClick={() => onRawMaterial("procured")} disabled={busy} data-testid="rm-procured" className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
          <CheckCircle size={14} weight="fill" /> RM Procured · Start Production
        </button>
      )}

      {stage === "in_production" && (
        <button onClick={() => onAdvance("packaging", "Production complete, moving to packaging")} disabled={busy} data-testid="advance-packaging" className="w-full bg-purple-600 hover:bg-purple-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
          <Package size={14} weight="bold" /> Move to Packaging
        </button>
      )}

      {stage === "packaging" && (
        <div className="space-y-3">
          <button onClick={onGenerateInvoice} disabled={busy} data-testid="generate-invoice-btn" className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
            <FileText size={14} weight="bold" /> Auto-generate Tax Invoice PDF
          </button>
          <div className="text-center text-[10px] uppercase tracking-wider text-zinc-400 font-bold">— then —</div>
          <UploadAction
            label="Mark Dispatched + Upload Invoice & E-way Bill"
            orderId={order.id}
            path="upload-dispatch"
            extraFields={[
              { name: "invoice_number", placeholder: "Tax Invoice #" },
              { name: "eway_bill_number", placeholder: "E-way Bill #" },
              { name: "transporter_name", placeholder: "Transporter name" },
            ]}
            fileFields={[
              { name: "invoice", label: "Invoice PDF (skip if auto-generated above)" },
              { name: "eway_bill", label: "E-way Bill PDF" },
            ]}
            icon={Truck}
            color="bg-cyan-600 hover:bg-cyan-700"
            onDone={onUploaded}
          />
          <div className="text-[10px] text-zinc-500 italic">Tip: click "Auto-generate Tax Invoice" first, then below you only need to attach the e-way bill.</div>
        </div>
      )}

      {stage === "dispatched" && (
        <UploadAction
          label="Upload LR Copy"
          orderId={order.id}
          path="upload-lr"
          extraFields={[{ name: "lr_number", placeholder: "LR #" }]}
          fileLabel="LR Copy (PDF or image)"
          icon={FileArrowUp}
          color="bg-teal-600 hover:bg-teal-700"
          onDone={onUploaded}
        />
      )}

      {stage === "lr_received" && (
        <button onClick={() => onAdvance("delivered", "Goods delivered to customer")} disabled={busy} data-testid="advance-delivered" className="w-full bg-emerald-700 hover:bg-emerald-800 text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-50">
          <CheckCircle size={14} weight="fill" /> Mark Delivered
        </button>
      )}

      {stage === "delivered" && (
        <div className="text-center py-2">
          <CheckCircle size={32} weight="fill" className="text-emerald-500 mx-auto mb-1" />
          <div className="font-heading font-black text-lg text-emerald-700">Order Delivered</div>
        </div>
      )}
    </div>
  );
}

function UploadAction({ label, orderId, path, extraFields = [], fileLabel, fileFields = [], icon: Icon, color, onDone }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const fileRefs = useRef({});
  const [fields, setFields] = useState(() => Object.fromEntries(extraFields.map((f) => [f.name, f.value || ""])));
  const submit = async () => {
    setBusy(true);
    try {
      const fd = new FormData();
      // Multi-file mode
      if (fileFields.length) {
        let any = false;
        for (const ff of fileFields) {
          const f = fileRefs.current[ff.name]?.files?.[0];
          if (f) { fd.append(ff.name, f); any = true; }
        }
        if (!any) {
          // allow zero files (e.g. dispatch with only transporter, no PDFs yet)
        }
      } else {
        const f = fileRefs.current["file"]?.files?.[0];
        if (!f) { toast.error("Select a file"); setBusy(false); return; }
        fd.append("file", f);
      }
      // Append extra text fields as query params (FastAPI will accept either)
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(fields)) if (v) qs.set(k, v);
      const url = `/orders/${orderId}/${path}${qs.toString() ? `?${qs}` : ""}`;
      await api.post(url, fd, { headers: { "Content-Type": "multipart/form-data" } });
      toast.success(`${label} done`);
      setOpen(false);
      onDone?.();
    } catch (e) { toast.error(formatApiError(e?.response?.data?.detail)); }
    finally { setBusy(false); }
  };
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} data-testid={`upload-action-${path}`} className={`w-full ${color} text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2`}>
        <Icon size={14} weight="bold" /> {label}
      </button>
    );
  }
  return (
    <div className="space-y-2 border border-zinc-200 p-3 bg-zinc-50">
      <div className="text-[10px] uppercase tracking-wider font-bold">{label}</div>
      {extraFields.map((f) => (
        <input
          key={f.name}
          value={fields[f.name] || ""}
          onChange={(e) => setFields({ ...fields, [f.name]: e.target.value })}
          placeholder={f.placeholder}
          className="w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17]"
          data-testid={`upload-${path}-${f.name}`}
        />
      ))}
      {fileFields.length ? fileFields.map((ff) => (
        <div key={ff.name}>
          <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-600 mb-1">{ff.label}</div>
          <input type="file" ref={(el) => fileRefs.current[ff.name] = el} className="text-xs w-full" data-testid={`upload-${path}-${ff.name}`} accept=".pdf,image/*" />
        </div>
      )) : (
        <div>
          <div className="text-[10px] uppercase tracking-wider font-bold text-zinc-600 mb-1">{fileLabel || "File"}</div>
          <input type="file" ref={(el) => fileRefs.current["file"] = el} className="text-xs w-full" data-testid={`upload-${path}-file`} accept=".pdf,image/*" />
        </div>
      )}
      <div className="flex gap-2">
        <button onClick={() => setOpen(false)} className="flex-1 px-3 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-zinc-100" data-testid={`upload-${path}-cancel`}>Cancel</button>
        <button onClick={submit} disabled={busy} className={`flex-1 ${color} text-white text-xs font-bold uppercase tracking-wider py-2 disabled:opacity-50`} data-testid={`upload-${path}-submit`}>{busy ? "Uploading…" : "Upload"}</button>
      </div>
    </div>
  );
}
