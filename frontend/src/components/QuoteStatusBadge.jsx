const STATUS_STYLES = {
  draft:    { label: "Draft",    cls: "bg-zinc-100 text-zinc-700 border-zinc-300" },
  sent:     { label: "Sent",     cls: "bg-blue-50 text-blue-700 border-blue-200" },
  approved: { label: "Approved", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  rejected: { label: "Rejected", cls: "bg-red-50 text-red-700 border-red-200" },
  revised:  { label: "Revised",  cls: "bg-amber-50 text-amber-700 border-amber-200" },
  expired:  { label: "Expired",  cls: "bg-zinc-100 text-zinc-500 border-zinc-300" },
};

export default function QuoteStatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.draft;
  return (
    <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 border ${s.cls}`} data-testid={`status-badge-${status}`}>
      {s.label}
    </span>
  );
}
