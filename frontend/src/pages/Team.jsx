import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilSimple, Key, CheckCircle, XCircle, User as UserIcon, Trash } from "@phosphor-icons/react";

const DEFAULT_TABS = [
  { key: "dashboard", label: "Dashboard" },
  { key: "quotations", label: "Quotations" },
  { key: "orders", label: "Orders" },
  { key: "contacts", label: "Contacts" },
  { key: "pricing-chart", label: "Pricing Chart" },
  { key: "product-families", label: "Product Families" },
  { key: "materials", label: "Materials" },
  { key: "categories", label: "Categories" },
  { key: "products", label: "Products / Variants" },
  { key: "price-history", label: "Price History" },
  { key: "team", label: "Team" },
  { key: "activity", label: "Activity" },
  { key: "settings", label: "Settings" },
];

export default function Team() {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState(null); // null = closed; {} = create; {id, ...} = edit
  const [resetting, setResetting] = useState(null); // user object

  const load = async () => {
    try {
      const { data } = await api.get("/users");
      setUsers(data);
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };
  useEffect(() => { load(); }, []);

  const toggleActive = async (u) => {
    try {
      const url = u.active ? `/users/${u.id}/deactivate` : `/users/${u.id}/activate`;
      await api.post(url);
      toast.success(`${u.name} ${u.active ? "deactivated" : "activated"}`);
      load();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    }
  };

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="Team"
        title="Employees & Access"
        subtitle="Create accounts, assign roles, restrict tabs, and control who can edit or delete."
        testId="team-header"
        actions={
          <button
            onClick={() => setForm({})}
            data-testid="team-new-user-btn"
            className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2"
          >
            <Plus size={14} weight="bold" /> New Employee
          </button>
        }
      />

      <div className="p-4 sm:p-8">
        <div className="border border-zinc-200 bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Email</th>
                <th className="px-6 py-3">Role</th>
                <th className="px-6 py-3">Permissions</th>
                <th className="px-6 py-3">Tabs</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-zinc-100 hover:bg-zinc-50" data-testid={`team-row-${u.id}`}>
                  <td className="px-6 py-3 font-bold">{u.name}</td>
                  <td className="px-6 py-3 font-mono text-xs">{u.email}</td>
                  <td className="px-6 py-3">
                    <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 ${
                      u.role === "admin" ? "bg-red-100 text-red-700"
                      : u.role === "manager" ? "bg-amber-100 text-amber-700"
                      : "bg-zinc-100 text-zinc-700"
                    }`}>{u.role}</span>
                  </td>
                  <td className="px-6 py-3 text-xs">
                    {u.role === "admin" ? (
                      <span className="text-zinc-500">Full</span>
                    ) : (
                      <>
                        <span className={u.can_edit ? "text-emerald-700" : "text-zinc-400"}>✎ Edit</span>
                        <span className="mx-1">·</span>
                        <span className={u.can_delete ? "text-red-700" : "text-zinc-400"}>🗑 Delete</span>
                      </>
                    )}
                  </td>
                  <td className="px-6 py-3 text-xs text-zinc-600">
                    {u.role === "admin" || !u.allowed_tabs?.length ? "All" : `${u.allowed_tabs.length} tab${u.allowed_tabs.length === 1 ? "" : "s"}`}
                  </td>
                  <td className="px-6 py-3">
                    {u.active ? (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-emerald-700"><CheckCircle size={12} weight="fill" /> Active</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-zinc-400"><XCircle size={12} weight="fill" /> Disabled</span>
                    )}
                  </td>
                  <td className="px-6 py-3 text-right whitespace-nowrap">
                    <button onClick={() => setForm(u)} className="text-zinc-500 hover:text-[#FBAE17] mr-3" data-testid={`team-edit-${u.id}`} title="Edit">
                      <PencilSimple size={14} />
                    </button>
                    <button onClick={() => setResetting(u)} className="text-zinc-500 hover:text-[#FBAE17] mr-3" data-testid={`team-reset-pw-${u.id}`} title="Reset password">
                      <Key size={14} />
                    </button>
                    <button onClick={() => toggleActive(u)} className={`${u.active ? "text-red-500 hover:text-red-700" : "text-emerald-500 hover:text-emerald-700"}`} data-testid={`team-toggle-active-${u.id}`} title={u.active ? "Deactivate" : "Activate"}>
                      {u.active ? <Trash size={14} /> : <CheckCircle size={14} />}
                    </button>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr><td colSpan={7} className="px-6 py-10 text-center text-zinc-400">No employees yet — click <b>New Employee</b> to add one.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {form && (
        <UserFormModal
          existing={form.id ? form : null}
          onClose={() => setForm(null)}
          onSaved={() => { setForm(null); load(); }}
        />
      )}
      {resetting && (
        <ResetPasswordModal
          user={resetting}
          onClose={() => setResetting(null)}
          onDone={() => setResetting(null)}
        />
      )}
    </div>
  );
}

function UserFormModal({ existing, onClose, onSaved }) {
  const isEdit = !!existing;
  const [form, setForm] = useState({
    name: existing?.name || "",
    email: existing?.email || "",
    mobile: existing?.mobile || "",
    role: existing?.role || "employee",
    can_edit: existing?.can_edit ?? true,
    can_delete: existing?.can_delete ?? false,
    allowed_tabs: existing?.allowed_tabs || [],
    password: "",
  });
  const [busy, setBusy] = useState(false);

  const toggleTab = (k) => {
    setForm((f) => ({
      ...f,
      allowed_tabs: f.allowed_tabs.includes(k)
        ? f.allowed_tabs.filter((t) => t !== k)
        : [...f.allowed_tabs, k],
    }));
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (isEdit) {
        const patch = { name: form.name, mobile: form.mobile, role: form.role, can_edit: form.can_edit, can_delete: form.can_delete, allowed_tabs: form.allowed_tabs };
        await api.patch(`/users/${existing.id}`, patch);
        toast.success(`${form.name} updated`);
      } else {
        await api.post("/users", form);
        toast.success(`Employee ${form.name} created`);
      }
      onSaved();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center px-4 py-8" onClick={onClose} data-testid="team-user-modal">
      <form onSubmit={submit} className="bg-white max-w-2xl w-full border-2 border-[#1A1A1A] max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-6 py-4 border-b border-zinc-200 bg-[#1A1A1A] text-white flex items-center justify-between">
          <div className="flex items-center gap-2">
            <UserIcon size={18} weight="fill" className="text-[#FBAE17]" />
            <span className="font-bold uppercase tracking-wider">{isEdit ? `Edit ${existing.name}` : "New Employee"}</span>
          </div>
        </div>
        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Full Name" value={form.name} onChange={(v) => setForm({ ...form, name: v })} required testId="team-form-name" />
            <Field label="Email" value={form.email} onChange={(v) => setForm({ ...form, email: v })} type="email" required disabled={isEdit} testId="team-form-email" />
            <Field label="Mobile" value={form.mobile} onChange={(v) => setForm({ ...form, mobile: v })} testId="team-form-mobile" />
            <div>
              <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">Role</label>
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} data-testid="team-form-role" className="w-full border border-zinc-300 px-3 py-2 text-sm">
                <option value="employee">Employee</option>
                <option value="manager">Manager</option>
                <option value="admin">Admin (full access)</option>
              </select>
            </div>
            {!isEdit && (
              <Field label="Initial Password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} type="password" required testId="team-form-password" hint="Min 8 characters. Employee should change on first login." />
            )}
          </div>

          {form.role !== "admin" && (
            <>
              <div className="pt-2 border-t border-zinc-200">
                <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">Fine-grained Permissions</div>
                <div className="flex items-center gap-6">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={form.can_edit} onChange={(e) => setForm({ ...form, can_edit: e.target.checked })} data-testid="team-form-can-edit" /> Can Edit
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={form.can_delete} onChange={(e) => setForm({ ...form, can_delete: e.target.checked })} data-testid="team-form-can-delete" /> Can Delete
                  </label>
                </div>
              </div>

              <div className="pt-2 border-t border-zinc-200">
                <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-1">Allowed Tabs</div>
                <div className="text-xs text-zinc-500 mb-2">Leave all unchecked to grant access to every tab.</div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {DEFAULT_TABS.map((t) => (
                    <label key={t.key} className="flex items-center gap-2 text-sm border border-zinc-200 px-2 py-1">
                      <input
                        type="checkbox"
                        checked={form.allowed_tabs.includes(t.key)}
                        onChange={() => toggleTab(t.key)}
                        data-testid={`team-form-tab-${t.key}`}
                      />
                      {t.label}
                    </label>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
        <div className="px-6 py-4 border-t border-zinc-200 bg-zinc-50 flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-white">Cancel</button>
          <button type="submit" disabled={busy} data-testid="team-form-save-btn" className="bg-[#1A1A1A] text-[#FBAE17] px-5 py-2 text-xs font-bold uppercase tracking-wider disabled:opacity-40">
            {busy ? "Saving…" : (isEdit ? "Save changes" : "Create employee")}
          </button>
        </div>
      </form>
    </div>
  );
}

function ResetPasswordModal({ user, onClose, onDone }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post(`/users/${user.id}/reset-password`, { new_password: pw });
      toast.success(`Password reset for ${user.name}`);
      onDone();
    } catch (e) {
      toast.error(formatApiError(e?.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center px-4" onClick={onClose} data-testid="team-reset-password-modal">
      <form onSubmit={submit} className="bg-white max-w-md w-full border-2 border-[#1A1A1A]" onClick={(e) => e.stopPropagation()}>
        <div className="px-6 py-3 border-b bg-[#1A1A1A] text-white font-bold uppercase tracking-wider text-sm">Reset password — {user.name}</div>
        <div className="p-6 space-y-3">
          <Field label="New Password" value={pw} onChange={setPw} type="password" required testId="team-reset-pw-input" hint="Min 8 characters." />
        </div>
        <div className="px-6 py-4 border-t bg-zinc-50 flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 border border-zinc-300 text-xs font-bold uppercase tracking-wider hover:bg-white">Cancel</button>
          <button type="submit" disabled={busy || pw.length < 8} data-testid="team-reset-pw-submit" className="bg-[#1A1A1A] text-[#FBAE17] px-5 py-2 text-xs font-bold uppercase tracking-wider disabled:opacity-40">
            {busy ? "Resetting…" : "Reset password"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", required, disabled, testId, hint }) {
  return (
    <div>
      <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-700 mb-1 block">{label}{required && " *"}</label>
      <input
        type={type}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        disabled={disabled}
        data-testid={testId}
        className="w-full border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:border-[#FBAE17] disabled:bg-zinc-50"
      />
      {hint && <div className="text-[10px] text-zinc-500 mt-1">{hint}</div>}
    </div>
  );
}
