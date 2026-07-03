// 设置面板各分区共用的小排版件。

export function SectionHeading({ title, hint }) {
  return (
    <div className="mb-4">
      <h4 className="text-sm font-black text-slate-800">{title}</h4>
      {hint && <p className="tiny-meta mt-1">{hint}</p>}
    </div>
  );
}

export function FieldRow({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-[var(--dorami-border)] py-3 last:border-b-0">
      <span className="text-sm font-bold text-slate-500">{label}</span>
      <div className="text-right text-sm font-semibold text-slate-800">{children}</div>
    </div>
  );
}
