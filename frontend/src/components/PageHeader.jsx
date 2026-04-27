export default function PageHeader({ title, subtitle, eyebrow, actions, testId }) {
  return (
    <div className="px-8 pt-8 pb-6 border-b border-zinc-200 bg-white flex items-start justify-between gap-6" data-testid={testId || "page-header"}>
      <div>
        {eyebrow && (
          <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17] mb-2">{eyebrow}</div>
        )}
        <h1 className="font-heading font-black text-3xl text-[#1A1A1A] tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-zinc-500 mt-1 max-w-2xl">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}
