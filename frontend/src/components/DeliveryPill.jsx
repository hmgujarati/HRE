import { CheckCircle, Clock, Eye, PaperPlaneTilt, Warning, WhatsappLogo, Envelope } from "@phosphor-icons/react";

const WA_STATUS_META = {
  accepted: { label: "Queued", color: "bg-amber-100 text-amber-800", Icon: Clock },
  sent: { label: "Sent", color: "bg-blue-100 text-blue-800", Icon: PaperPlaneTilt },
  delivered: { label: "Delivered", color: "bg-emerald-100 text-emerald-800", Icon: CheckCircle },
  read: { label: "Read", color: "bg-violet-100 text-violet-800", Icon: Eye },
  failed: { label: "Failed", color: "bg-red-100 text-red-800", Icon: Warning },
  pending: { label: "Pending", color: "bg-zinc-100 text-zinc-600", Icon: Clock },
};

const EMAIL_STATUS_META = {
  sent: { label: "Emailed", color: "bg-emerald-100 text-emerald-800", Icon: CheckCircle },
  pending: { label: "Pending", color: "bg-zinc-100 text-zinc-600", Icon: Clock },
  failed: { label: "Failed", color: "bg-red-100 text-red-800", Icon: Warning },
};

/** Single pill for a channel/status. */
export function DeliveryPill({ channel, status, size = "sm", title }) {
  const meta = channel === "email"
    ? (EMAIL_STATUS_META[status] || EMAIL_STATUS_META.pending)
    : (WA_STATUS_META[status] || WA_STATUS_META.pending);
  const ChannelIcon = channel === "email" ? Envelope : WhatsappLogo;
  const { Icon, label, color } = meta;
  const pad = size === "xs" ? "px-1.5 py-0.5 text-[9px]" : "px-2 py-0.5 text-[10px]";
  return (
    <span title={title || `${channel}: ${label}`} className={`inline-flex items-center gap-1 font-bold uppercase tracking-wider ${pad} ${color}`}>
      <ChannelIcon size={10} weight="fill" />
      <Icon size={10} weight="fill" />
      {label}
    </span>
  );
}

/** Returns the most recent entry per channel from a dispatch_log array. */
export function latestByChannel(log) {
  const latest = { whatsapp: null, email: null };
  for (const e of (log || [])) {
    if (!e || !e.channel) continue;
    const cur = latest[e.channel];
    if (!cur || new Date(e.sent_at) > new Date(cur.sent_at)) {
      latest[e.channel] = e;
    }
  }
  return latest;
}

/** Compact row of pills showing the latest delivery status per channel. */
export function DeliveryStrip({ log, size = "sm" }) {
  const latest = latestByChannel(log);
  if (!latest.whatsapp && !latest.email) {
    return <span className="text-[10px] text-zinc-400 italic">Not sent</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 flex-wrap">
      {latest.whatsapp && <DeliveryPill channel="whatsapp" status={latest.whatsapp.status} size={size} />}
      {latest.email && <DeliveryPill channel="email" status={latest.email.status} size={size} />}
    </span>
  );
}
