// 统一空状态：复用 .empty-state 角色类。文案遵循 conventions §1「指向第一个动作」。
// icon 可选；title 主行；subtitle 次行说明；action 放引导按钮。
export default function EmptyState({ icon: Icon, title, subtitle, action, className = '' }) {
  return (
    <div className={`empty-state flex flex-col items-center justify-center gap-2 px-6 py-10 ${className}`.trim()}>
      {Icon && <Icon className="h-7 w-7 text-slate-300" />}
      {title && <div className="font-semibold text-slate-500">{title}</div>}
      {subtitle && <div className="text-slate-500">{subtitle}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
