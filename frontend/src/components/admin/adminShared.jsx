// 运维看板的共享展示组件（纯 props，无业务态）。

// 图表小面板：统一的标题 + 图表容器（flex-1 居中，让矮图在同行高图旁垂直居中）。
export function ChartPanel({ title, action, children }) {
  return (
    <div className="flex flex-col rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4">
      <div className="mb-3 flex items-center gap-3">
        <p className="micro-label text-slate-500">{title}</p>
        {action && <div className="ml-auto">{action}</div>}
      </div>
      <div className="flex flex-1 flex-col justify-center">{children}</div>
    </div>
  );
}

// KPI 统计卡：图标 + 标签 + 数字（+ 可选副行）。
export function StatCard({ icon: Icon, label, value, sub, valueClass = 'text-slate-800' }) {
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4">
      <div className="flex items-center gap-2 text-slate-500">
        <Icon className="h-4 w-4" />
        <span className="micro-label">{label}</span>
      </div>
      <p className={`stat-number mt-2 ${valueClass}`}>{value}</p>
      {sub && <p className="tiny-meta mt-0.5">{sub}</p>}
    </div>
  );
}

// 子页卡片头：左侧色条 + 标题 + 说明，右侧放操作/开关等（children）。
// 收敛 AdminOpsTab 里重复 4 遍的「w-1 h-5 色条 + section-title + tiny-meta」头部结构。
export function PanelHeader({ barClass, title, hint, children }) {
  return (
    <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
      <div className={`w-1 h-5 rounded-full ${barClass}`} />
      <h3 className="section-title">{title}</h3>
      {hint && <span className="tiny-meta">{hint}</span>}
      {children}
    </div>
  );
}
