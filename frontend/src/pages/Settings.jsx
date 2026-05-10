import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import { useAuth } from "@/contexts/AuthContext";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { WhatsappLogo, Envelope, User, Palette, CheckCircle, Warning, PaperPlaneRight, FloppyDisk } from "@phosphor-icons/react";

const TABS = [
  { id: "whatsapp", label: "WhatsApp", icon: WhatsappLogo },
  { id: "smtp", label: "Email (SMTP)", icon: Envelope },
  { id: "account", label: "Account", icon: User },
  { id: "branding", label: "Branding", icon: Palette },
];

export default function Settings() {
  const { user } = useAuth();
  const [tab, setTab] = useState("whatsapp");
  const isAdmin = user?.role === "admin";

  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow="System" title="Settings" subtitle="Integrations, account & branding." testId="settings-header" />

      <div className="px-4 sm:px-8 pt-2">
        <div className="border-b border-zinc-200 flex gap-1 overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                data-testid={`settings-tab-${t.id}`}
                className={`shrink-0 flex items-center gap-2 px-4 py-3 text-xs uppercase tracking-wider font-bold border-b-2 transition-colors ${active ? "border-[#FBAE17] text-[#1A1A1A]" : "border-transparent text-zinc-500 hover:text-[#1A1A1A]"}`}
              >
                <Icon size={14} weight={active ? "fill" : "regular"} />
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="p-4 sm:p-8">
        {tab === "whatsapp" && <WhatsAppTab canEdit={isAdmin} />}
        {tab === "smtp" && <SmtpTab canEdit={isAdmin} />}
        {tab === "account" && <AccountTab user={user} />}
        {tab === "branding" && <BrandingTab />}
      </div>
    </div>
  );
}

// ---------------- WhatsApp Tab ----------------
function WhatsAppTab({ canEdit }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [testPhone, setTestPhone] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [templates, setTemplates] = useState(null);
  const [tplBusy, setTplBusy] = useState(false);

  useEffect(() => { load(); }, []);
  const load = async () => {
    try {
      const { data } = await api.get("/settings/integrations");
      setData(data);
      setForm({ ...data.whatsapp, token: "" });
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  const fetchTemplates = async () => {
    setTplBusy(true);
    try {
      const { data } = await api.get("/settings/whatsapp/templates");
      // BizChatAPI shape: { data: { templateList: { data: [{ template_name, language, status, category }] } } }
      const list = data?.data?.templateList?.data || data?.data || data?.templates || (Array.isArray(data) ? data : []);
      const approved = (Array.isArray(list) ? list : []).filter((t) => !t.status || t.status === "APPROVED");
      setTemplates(approved);
      toast.success(`Loaded ${approved.length} approved template${approved.length === 1 ? '' : 's'}`);
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
      setTemplates([]);
    } finally { setTplBusy(false); }
  };

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = { whatsapp: { ...form } };
      if (form.token === "" || form.token === null) delete payload.whatsapp.token;
      const { data } = await api.put("/settings/integrations", payload);
      setData(data);
      setForm({ ...data.whatsapp, token: "" });
      toast.success("WhatsApp settings saved");
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const sendTest = async () => {
    if (!testPhone) { toast.error("Enter a phone number"); return; }
    setTestBusy(true); setTestResult(null);
    try {
      const { data } = await api.post("/settings/whatsapp/test", { phone: testPhone, mode: "template" });
      setTestResult({ ok: true, response: data });
      toast.success("Test OTP template sent");
    } catch (err) {
      const msg = formatApiError(err?.response?.data?.detail);
      setTestResult({ ok: false, error: msg });
      toast.error(msg);
    } finally { setTestBusy(false); }
  };

  if (!data || !form) return <div className="text-zinc-400 text-sm">Loading…</div>;

  // Build deduplicated lists of unique template names, plus a name → languages map
  const tplNames = Array.from(new Set((templates || []).map((t) => t?.template_name || t?.name).filter(Boolean)));
  const tplLangsByName = (templates || []).reduce((acc, t) => {
    const n = t?.template_name || t?.name; if (!n) return acc;
    acc[n] = acc[n] || new Set();
    if (t.language) acc[n].add(t.language);
    return acc;
  }, {});
  const otpLangs = form.otp_template_name && tplLangsByName[form.otp_template_name] ? Array.from(tplLangsByName[form.otp_template_name]) : [];
  const quoteLangs = form.quote_template_name && tplLangsByName[form.quote_template_name] ? Array.from(tplLangsByName[form.quote_template_name]) : [];
  // Per-stage langs (computed lazily from the loaded template list)
  const piLangs = form.order_pi_template && tplLangsByName[form.order_pi_template] ? Array.from(tplLangsByName[form.order_pi_template]) : [];
  const prodLangs = form.order_production_template && tplLangsByName[form.order_production_template] ? Array.from(tplLangsByName[form.order_production_template]) : [];
  const pkgLangs = form.order_packaging_template && tplLangsByName[form.order_packaging_template] ? Array.from(tplLangsByName[form.order_packaging_template]) : [];
  const dispLangs = form.order_dispatched_template && tplLangsByName[form.order_dispatched_template] ? Array.from(tplLangsByName[form.order_dispatched_template]) : [];
  const lrLangs = form.order_lr_template && tplLangsByName[form.order_lr_template] ? Array.from(tplLangsByName[form.order_lr_template]) : [];
  const prodUpdateLangs = form.order_production_update_template && tplLangsByName[form.order_production_update_template] ? Array.from(tplLangsByName[form.order_production_update_template]) : [];
  const poAdminLangs = form.po_received_admin_template && tplLangsByName[form.po_received_admin_template] ? Array.from(tplLangsByName[form.po_received_admin_template]) : [];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 border border-zinc-200 bg-white">
        <div className="px-5 sm:px-6 py-4 border-b border-zinc-200 flex items-center gap-2">
          <WhatsappLogo size={20} weight="fill" className="text-[#25D366]" />
          <div>
            <h3 className="font-heading font-black text-lg">BizChatAPI Configuration</h3>
            <div className="text-xs text-zinc-500">OTP delivery for the public quote portal + auto-dispatch of finalised quotation PDFs.</div>
          </div>
          <StatusPill enabled={data.whatsapp.enabled && data.whatsapp.vendor_uid && data.whatsapp.token} />
        </div>
        <form onSubmit={save} className="p-5 sm:p-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="sm:col-span-2 flex items-center justify-between border border-zinc-200 px-4 py-3 bg-zinc-50">
            <div>
              <div className="text-xs uppercase tracking-wider font-bold">Enable WhatsApp Send</div>
              <div className="text-[11px] text-zinc-500">When off, OTPs fall back to dev mode (logged + returned in API).</div>
            </div>
            <Toggle checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} disabled={!canEdit} testId="wa-enabled" />
          </div>

          <Field label="API Base URL" value={form.api_base_url} onChange={(v) => setForm({ ...form, api_base_url: v })} disabled={!canEdit} testId="wa-base-url" placeholder="https://bizchatapi.in/api" />
          <Field label="Vendor UID" mono value={form.vendor_uid} onChange={(v) => setForm({ ...form, vendor_uid: v })} disabled={!canEdit} testId="wa-vendor-uid" placeholder="5a1795ja-b76h-..." />
          <Field
            label="API Token"
            mono
            type="password"
            placeholder={data.whatsapp.token ? `Saved (${data.whatsapp.token}) — type to replace` : "xmg5n1nIL..."}
            value={form.token || ""}
            onChange={(v) => setForm({ ...form, token: v })}
            disabled={!canEdit}
            testId="wa-token"
            span
          />
          <Field label="From Phone Number ID (optional)" value={form.from_phone_number_id} onChange={(v) => setForm({ ...form, from_phone_number_id: v })} disabled={!canEdit} testId="wa-phone-id" placeholder="leave blank for default" />
          <Field label="Default Country Code" value={form.default_country_code} onChange={(v) => setForm({ ...form, default_country_code: v })} disabled={!canEdit} testId="wa-cc" placeholder="91" />

          <div className="sm:col-span-2 border-t border-zinc-200 mt-2 pt-4 flex items-center justify-between gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17]">Templates</div>
              <div className="text-xs text-zinc-500">Click "Load templates" to pull the approved list from BizChat.</div>
            </div>
            <button type="button" onClick={fetchTemplates} disabled={tplBusy || !data.whatsapp.vendor_uid} data-testid="wa-load-templates" className="px-3 py-2 border border-zinc-300 hover:border-[#FBAE17] text-xs uppercase tracking-wider font-bold disabled:opacity-50">
              {tplBusy ? "Loading…" : templates ? `Reload (${templates.length})` : "Load templates"}
            </button>
          </div>

          <TemplateField label="OTP Template Name" testId="wa-template-name" value={form.otp_template_name} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, otp_template_name: v, otp_template_language: langs.includes(form.otp_template_language) ? form.otp_template_language : (langs[0] || form.otp_template_language) });
          }} options={tplNames} disabled={!canEdit} placeholder="otp" hint="Authentication template with body containing {{1}}" />
          <TemplateField label="OTP Template Language" testId="wa-template-lang" value={form.otp_template_language} onChange={(v) => setForm({ ...form, otp_template_language: v })} options={otpLangs} disabled={!canEdit} placeholder="en or en_us" />
          <TemplateField label="Quote Dispatch Template Name" testId="wa-quote-template-name" value={form.quote_template_name} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, quote_template_name: v, quote_template_language: langs.includes(form.quote_template_language) ? form.quote_template_language : (langs[0] || form.quote_template_language) });
          }} options={tplNames} disabled={!canEdit} placeholder="hre_quote_pdf" hint="Document-header template. Body vars: {{1}}=customer, {{2}}=quote#, {{3}}=total line, {{4}}=validity/items" />
          <TemplateField label="Quote Template Language" testId="wa-quote-template-lang" value={form.quote_template_language} onChange={(v) => setForm({ ...form, quote_template_language: v })} options={quoteLangs} disabled={!canEdit} placeholder="en or en_us" />

          <div className="sm:col-span-2 border-t border-zinc-200 mt-2 pt-4">
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Order Tracking Auto-Notify</div>
            <div className="text-xs text-zinc-500 mb-2">
              These templates fire automatically when an order moves through stages. Each template should accept body vars: <span className="font-mono">{`{{1}}`}</span>=customer, <span className="font-mono">{`{{2}}`}</span>=order#, <span className="font-mono">{`{{3}}`}</span>=stage, <span className="font-mono">{`{{4}}`}</span>=timestamp. Document header optional (auto-attached for PI / Dispatch / LR).
            </div>
          </div>
          <TemplateField label="Proforma Issued Template" testId="wa-pi-template" value={form.order_pi_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, order_pi_template: v, order_pi_template_language: langs[0] || form.order_pi_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank to skip" />
          <TemplateField label="Proforma Template Language" testId="wa-pi-template-lang" value={form.order_pi_template_language || ""} onChange={(v) => setForm({ ...form, order_pi_template_language: v })} options={piLangs} disabled={!canEdit} placeholder="en or en_US" />

          <TemplateField label="In Production Template" testId="wa-prod-template" value={form.order_production_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, order_production_template: v, order_production_template_language: langs[0] || form.order_production_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank to skip" />
          <TemplateField label="In Production Template Language" testId="wa-prod-template-lang" value={form.order_production_template_language || ""} onChange={(v) => setForm({ ...form, order_production_template_language: v })} options={prodLangs} disabled={!canEdit} placeholder="en or en_US" />

          <TemplateField label="Packaging Template" testId="wa-packaging-template" value={form.order_packaging_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, order_packaging_template: v, order_packaging_template_language: langs[0] || form.order_packaging_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank to skip" />
          <TemplateField label="Packaging Template Language" testId="wa-packaging-template-lang" value={form.order_packaging_template_language || ""} onChange={(v) => setForm({ ...form, order_packaging_template_language: v })} options={pkgLangs} disabled={!canEdit} placeholder="en or en_US" />

          <TemplateField label="Dispatched Template" testId="wa-dispatched-template" value={form.order_dispatched_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, order_dispatched_template: v, order_dispatched_template_language: langs[0] || form.order_dispatched_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank to skip" />
          <TemplateField label="Dispatched Template Language" testId="wa-dispatched-template-lang" value={form.order_dispatched_template_language || ""} onChange={(v) => setForm({ ...form, order_dispatched_template_language: v })} options={dispLangs} disabled={!canEdit} placeholder="en or en_US" />

          <TemplateField label="LR Received Template" testId="wa-lr-template" value={form.order_lr_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, order_lr_template: v, order_lr_template_language: langs[0] || form.order_lr_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank to skip" />
          <TemplateField label="LR Received Template Language" testId="wa-lr-template-lang" value={form.order_lr_template_language || ""} onChange={(v) => setForm({ ...form, order_lr_template_language: v })} options={lrLangs} disabled={!canEdit} placeholder="en or en_US" />

          <div className="sm:col-span-2 border-t border-zinc-100 mt-1 pt-3">
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Ad-hoc Production Updates</div>
            <div className="text-xs text-zinc-500 mb-2">Fires when admin posts a free-form floor note (e.g. "plating process started"). Body vars: <span className="font-mono">{`{{1}}`}</span>=customer, <span className="font-mono">{`{{2}}`}</span>=order#, <span className="font-mono">{`{{3}}`}</span>=note text, <span className="font-mono">{`{{4}}`}</span>=timestamp. Email always fires when SMTP is enabled.</div>
          </div>
          <TemplateField label="Production Update Template" testId="wa-prod-update-template" value={form.order_production_update_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, order_production_update_template: v, order_production_update_template_language: langs[0] || form.order_production_update_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank → email only" />
          <TemplateField label="Production Update Template Language" testId="wa-prod-update-template-lang" value={form.order_production_update_template_language || ""} onChange={(v) => setForm({ ...form, order_production_update_template_language: v })} options={prodUpdateLangs} disabled={!canEdit} placeholder="en or en_US" />

          <div className="sm:col-span-2 border-t border-zinc-200 mt-2 pt-4">
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Internal Admin Alerts</div>
            <div className="text-xs text-zinc-500 mb-2">
              When a customer submits a PO from the public portal, ping our admin instantly. Body vars: <span className="font-mono">{`{{1}}`}</span>=customer, <span className="font-mono">{`{{2}}`}</span>=quote#, <span className="font-mono">{`{{3}}`}</span>=order#, <span className="font-mono">{`{{4}}`}</span>=timestamp.
            </div>
          </div>
          <Field label="Admin WhatsApp Phone" value={form.admin_notify_phone} onChange={(v) => setForm({ ...form, admin_notify_phone: v })} disabled={!canEdit} testId="wa-admin-notify-phone" placeholder="+91 98xxx xxxxx" />
          <TemplateField label="PO Received Admin Template" testId="wa-po-admin-template" value={form.po_received_admin_template} onChange={(v) => {
            const langs = tplLangsByName[v] ? Array.from(tplLangsByName[v]) : [];
            setForm({ ...form, po_received_admin_template: v, po_received_admin_template_language: langs[0] || form.po_received_admin_template_language || "en" });
          }} options={tplNames} disabled={!canEdit} placeholder="leave blank to skip" />
          <TemplateField label="PO Admin Template Language" testId="wa-po-admin-template-lang" value={form.po_received_admin_template_language || ""} onChange={(v) => setForm({ ...form, po_received_admin_template_language: v })} options={poAdminLangs} disabled={!canEdit} placeholder="en or en_US" />

          <div className="sm:col-span-2 flex justify-end pt-2">
            <button type="submit" disabled={busy || !canEdit} data-testid="wa-save-btn" className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60">
              <FloppyDisk size={14} weight="bold" /> {busy ? "Saving…" : "Save WhatsApp Settings"}
            </button>
          </div>

          {data.whatsapp.webhook_url && (
            <div className="sm:col-span-2 border-t border-zinc-200 mt-2 pt-4">
              <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Status Webhook</div>
              <h4 className="font-heading font-black text-base mb-1">Real-time Delivery Push</h4>
              <div className="text-xs text-zinc-500 mb-3">
                Copy this URL into BizChat's <span className="font-bold">Webhook</span> settings (Message-Status or message.status event). When the customer's phone receives / reads the PDF, we'll update the quote pill instantly — no polling needed.
              </div>
              <div className="flex items-center gap-2 border border-zinc-300 bg-zinc-50 px-3 py-2 font-mono text-[11px] break-all">
                <span className="flex-1" data-testid="wa-webhook-url">{data.whatsapp.webhook_url}</span>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(data.whatsapp.webhook_url);
                      toast.success("Webhook URL copied");
                    } catch { toast.error("Could not copy. Please select and copy manually."); }
                  }}
                  className="shrink-0 bg-[#1A1A1A] hover:bg-black text-white text-[10px] uppercase tracking-wider font-bold px-3 py-1.5"
                  data-testid="wa-webhook-copy"
                >Copy</button>
              </div>
              <div className="text-[10px] text-zinc-500 mt-2">
                Secret is baked into the URL — keep the URL private. Rotate anytime with the <span className="font-mono">Rotate Secret</span> button below.
              </div>
              <button
                type="button"
                onClick={async () => {
                  if (!window.confirm("Rotate the webhook secret? You'll need to update the URL in BizChat after this.")) return;
                  try {
                    await api.put("/settings/integrations", { whatsapp: { ...form, webhook_secret_rotate: true } });
                    toast.success("Secret rotated — refresh to see new URL");
                    load();
                  } catch (err) { toast.error(formatApiError(err?.response?.data?.detail)); }
                }}
                className="mt-2 text-[10px] uppercase tracking-wider font-bold text-zinc-600 hover:text-red-600"
                data-testid="wa-webhook-rotate"
              >
                Rotate Secret
              </button>
            </div>
          )}
        </form>
      </div>

      <div className="border border-zinc-200 bg-white h-fit">
        <div className="px-5 py-4 border-b border-zinc-200">
          <h3 className="font-heading font-black text-base">Send Test OTP</h3>
          <div className="text-xs text-zinc-500">Sends a sample OTP <span className="font-mono">123456</span> via your configured template.</div>
        </div>
        <div className="p-5 space-y-3">
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Phone (with country code)</label>
            <input
              type="tel"
              value={testPhone}
              onChange={(e) => setTestPhone(e.target.value)}
              placeholder="918856066529"
              className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono focus:outline-none focus:border-[#FBAE17]"
              data-testid="wa-test-phone"
            />
          </div>
          <button
            onClick={sendTest}
            disabled={testBusy}
            data-testid="wa-test-send"
            className="w-full bg-[#1A1A1A] hover:bg-black text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-60"
          >
            <PaperPlaneRight size={14} weight="bold" /> {testBusy ? "Sending…" : "Send Test"}
          </button>
          {testResult && (
            <div className={`border px-3 py-2 text-xs ${testResult.ok ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-red-300 bg-red-50 text-red-900"}`}>
              <div className="font-bold flex items-center gap-1 mb-1">
                {testResult.ok ? <><CheckCircle size={14} weight="fill" /> Sent</> : <><Warning size={14} weight="fill" /> Failed</>}
              </div>
              <pre className="font-mono text-[10px] whitespace-pre-wrap break-all">{JSON.stringify(testResult.ok ? testResult.response : testResult.error, null, 2)}</pre>
            </div>
          )}
          <div className="border-t border-zinc-100 pt-3 mt-3 text-[11px] text-zinc-500 leading-relaxed">
            <div className="font-bold uppercase tracking-wider text-[9px] text-zinc-700 mb-1">Quote PDF Template</div>
            Your <span className="font-bold">Quote Dispatch Template</span> needs a <span className="font-bold">Document</span> header (PDF) plus 4 body variables:<br/>
            <span className="font-mono bg-zinc-100 px-1">{`{{1}}`}</span> = customer name<br/>
            <span className="font-mono bg-zinc-100 px-1">{`{{2}}`}</span> = quote number (e.g. HRE/QT/2026-27/0011)<br/>
            <span className="font-mono bg-zinc-100 px-1">{`{{3}}`}</span> = total line (e.g. "Total: ₹12,345.00")<br/>
            <span className="font-mono bg-zinc-100 px-1">{`{{4}}`}</span> = validity / item count
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------- SMTP Tab ----------------
function SmtpTab({ canEdit }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [testEmail, setTestEmail] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => { load(); }, []);
  const load = async () => {
    try {
      const { data } = await api.get("/settings/integrations");
      setData(data);
      setForm({ ...data.smtp, password: "" });
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    }
  };

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = { smtp: { ...form } };
      if (form.password === "" || form.password === null) delete payload.smtp.password;
      const { data } = await api.put("/settings/integrations", payload);
      setData(data);
      setForm({ ...data.smtp, password: "" });
      toast.success("SMTP settings saved");
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const sendTest = async () => {
    if (!testEmail) { toast.error("Enter a recipient email"); return; }
    setTestBusy(true); setTestResult(null);
    try {
      const { data } = await api.post("/settings/smtp/test", { to_email: testEmail });
      setTestResult({ ok: true, response: data });
      toast.success("Test email dispatched");
    } catch (err) {
      const msg = formatApiError(err?.response?.data?.detail);
      setTestResult({ ok: false, error: msg });
      toast.error(msg);
    } finally { setTestBusy(false); }
  };

  if (!data || !form) return <div className="text-zinc-400 text-sm">Loading…</div>;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 border border-zinc-200 bg-white">
        <div className="px-5 sm:px-6 py-4 border-b border-zinc-200 flex items-center gap-2">
          <Envelope size={20} weight="fill" className="text-[#FBAE17]" />
          <div>
            <h3 className="font-heading font-black text-lg">Hostinger / SMTP Configuration</h3>
            <div className="text-xs text-zinc-500">Used to email quote PDFs and notifications to customers.</div>
          </div>
          <StatusPill enabled={data.smtp.enabled && data.smtp.host && data.smtp.username} />
        </div>
        <form onSubmit={save} className="p-5 sm:p-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="sm:col-span-2 flex items-center justify-between border border-zinc-200 px-4 py-3 bg-zinc-50">
            <div>
              <div className="text-xs uppercase tracking-wider font-bold">Enable Email Send</div>
              <div className="text-[11px] text-zinc-500">When off, no SMTP is invoked.</div>
            </div>
            <Toggle checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} disabled={!canEdit} testId="smtp-enabled" />
          </div>
          <Field label="Host" value={form.host} onChange={(v) => setForm({ ...form, host: v })} disabled={!canEdit} testId="smtp-host" placeholder="smtp.hostinger.com" />
          <Field label="Port" type="number" value={form.port} onChange={(v) => setForm({ ...form, port: Number(v) || 0 })} disabled={!canEdit} testId="smtp-port" placeholder="465" />
          <div className="sm:col-span-2 flex items-center gap-2 text-xs">
            <input id="smtp-ssl" type="checkbox" checked={form.use_ssl} onChange={(e) => setForm({ ...form, use_ssl: e.target.checked })} disabled={!canEdit} data-testid="smtp-ssl" />
            <label htmlFor="smtp-ssl" className="font-bold uppercase tracking-wider">Use SSL (port 465). Uncheck for STARTTLS on port 587.</label>
          </div>
          <Field label="Username" value={form.username} onChange={(v) => setForm({ ...form, username: v })} disabled={!canEdit} testId="smtp-username" placeholder="quotes@hrexporter.com" />
          <Field
            label="Password"
            type="password"
            placeholder={data.smtp.password ? `Saved (${data.smtp.password}) — type to replace` : "••••••••"}
            value={form.password || ""}
            onChange={(v) => setForm({ ...form, password: v })}
            disabled={!canEdit}
            testId="smtp-password"
          />
          <Field label="From Email" value={form.from_email} onChange={(v) => setForm({ ...form, from_email: v })} disabled={!canEdit} testId="smtp-from-email" placeholder="quotes@hrexporter.com" />
          <Field label="From Name" value={form.from_name} onChange={(v) => setForm({ ...form, from_name: v })} disabled={!canEdit} testId="smtp-from-name" placeholder="HRE Exporter" />
          <Field
            label="Admin Notify Email"
            value={form.admin_notify_email}
            onChange={(v) => setForm({ ...form, admin_notify_email: v })}
            disabled={!canEdit}
            testId="smtp-admin-notify-email"
            placeholder="leave blank to use From Email"
            span
          />
          <div className="sm:col-span-2 flex justify-end pt-2">
            <button type="submit" disabled={busy || !canEdit} data-testid="smtp-save-btn" className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-60">
              <FloppyDisk size={14} weight="bold" /> {busy ? "Saving…" : "Save SMTP Settings"}
            </button>
          </div>
        </form>
      </div>

      <div className="border border-zinc-200 bg-white h-fit">
        <div className="px-5 py-4 border-b border-zinc-200">
          <h3 className="font-heading font-black text-base">Send Test Email</h3>
          <div className="text-xs text-zinc-500">Sends a plain-text email to verify SMTP creds.</div>
        </div>
        <div className="p-5 space-y-3">
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">To Email</label>
            <input
              type="email"
              value={testEmail}
              onChange={(e) => setTestEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono focus:outline-none focus:border-[#FBAE17]"
              data-testid="smtp-test-email"
            />
          </div>
          <button
            onClick={sendTest}
            disabled={testBusy}
            data-testid="smtp-test-send"
            className="w-full bg-[#1A1A1A] hover:bg-black text-white font-bold uppercase tracking-wider text-xs py-3 flex items-center justify-center gap-2 disabled:opacity-60"
          >
            <PaperPlaneRight size={14} weight="bold" /> {testBusy ? "Sending…" : "Send Test Email"}
          </button>
          {testResult && (
            <div className={`border px-3 py-2 text-xs ${testResult.ok ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-red-300 bg-red-50 text-red-900"}`}>
              <div className="font-bold flex items-center gap-1 mb-1">
                {testResult.ok ? <><CheckCircle size={14} weight="fill" /> Sent</> : <><Warning size={14} weight="fill" /> Failed</>}
              </div>
              <pre className="font-mono text-[10px] whitespace-pre-wrap break-all">{JSON.stringify(testResult.ok ? testResult.response : testResult.error, null, 2)}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------- Account & Branding tabs (kept) ----------------
function AccountTab({ user }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div className="border border-zinc-200 bg-white p-6">
        <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Account</div>
        <h3 className="font-heading font-black text-lg mb-4">Signed in as</h3>
        <div className="space-y-2 text-sm">
          <div><span className="text-zinc-500 mr-2">Name:</span><span className="font-medium">{user?.name}</span></div>
          <div><span className="text-zinc-500 mr-2">Email:</span><span className="font-mono">{user?.email}</span></div>
          <div><span className="text-zinc-500 mr-2">Role:</span><span className="text-[10px] uppercase tracking-wider font-bold bg-[#FBAE17] text-black px-2 py-0.5">{user?.role}</span></div>
        </div>
      </div>
    </div>
  );
}

function BrandingTab() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div className="border border-zinc-200 bg-white p-6">
        <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Branding</div>
        <h3 className="font-heading font-black text-lg mb-4">HREXPORTER</h3>
        <div className="border border-zinc-200 bg-zinc-50 p-4 mb-4 flex items-center justify-center">
          <img src="/hre-logo-light-bg.png" alt="HREXPORTER" className="h-20 object-contain" />
        </div>
        <div className="grid grid-cols-3 gap-3 text-xs">
          <div><div className="h-12 bg-[#FBAE17]" /><div className="font-mono mt-1">#FBAE17</div></div>
          <div><div className="h-12 bg-[#1A1A1A]" /><div className="font-mono mt-1">#1A1A1A</div></div>
          <div><div className="h-12 bg-white border border-zinc-300" /><div className="font-mono mt-1">#FFFFFF</div></div>
        </div>
        <p className="text-xs text-zinc-500 mt-4">Logo upload and currency selector will be enabled in a later phase.</p>
      </div>
    </div>
  );
}

// ---------------- Reusable bits ----------------
function Field({ label, value, onChange, type = "text", placeholder, span, mono, disabled, testId, hint }) {
  return (
    <div className={span ? "sm:col-span-2" : ""}>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}</label>
      <input
        type={type}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        data-testid={testId}
        className={`w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17] disabled:bg-zinc-50 disabled:text-zinc-500 ${mono ? "font-mono" : ""}`}
      />
      {hint && <div className="text-[10px] text-zinc-500 mt-1">{hint}</div>}
    </div>
  );
}

function TemplateField({ label, value, onChange, options, placeholder, disabled, testId, hint }) {
  const hasOptions = Array.isArray(options) && options.length > 0;
  return (
    <div>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}</label>
      {hasOptions ? (
        <select
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          data-testid={testId}
          className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono focus:outline-none focus:border-[#FBAE17] disabled:bg-zinc-50"
        >
          <option value="">— Select —</option>
          {options.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
          {value && !options.includes(value) && <option value={value}>{value} (custom)</option>}
        </select>
      ) : (
        <input
          type="text"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          data-testid={testId}
          className="w-full border border-zinc-300 px-3 py-2 text-sm font-mono focus:outline-none focus:border-[#FBAE17] disabled:bg-zinc-50"
        />
      )}
      {hint && <div className="text-[10px] text-zinc-500 mt-1">{hint}</div>}
    </div>
  );
}

function Toggle({ checked, onChange, disabled, testId }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => !disabled && onChange(!checked)}
      data-testid={testId}
      disabled={disabled}
      className={`relative w-11 h-6 transition-colors ${checked ? "bg-[#FBAE17]" : "bg-zinc-300"} ${disabled ? "opacity-50" : ""}`}
    >
      <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white transition-transform ${checked ? "translate-x-5" : ""}`} />
    </button>
  );
}

function StatusPill({ enabled }) {
  return (
    <span className={`ml-auto text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 ${enabled ? "bg-emerald-100 text-emerald-700" : "bg-zinc-100 text-zinc-500"}`}>
      {enabled ? "Live" : "Inactive"}
    </span>
  );
}
