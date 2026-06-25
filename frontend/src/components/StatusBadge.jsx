import { TONE_CLASS } from '../statusMeta';

// 统一状态徽标：复用 .status-badge 角色类 + 集中的 tone 配色。
// 吃 statusMeta.js 的 meta（{ label, tone, icon?, iconClassName?, extraClassName? }），
// 或显式传 label/tone/icon。状态始终带文字标签（conventions §2：不靠纯色单独传达）。
export default function StatusBadge({ meta, label, tone, icon, iconClassName = '', className = '', children }) {
  const m = meta || {};
  const finalTone = tone || m.tone || 'slate';
  const finalLabel = children ?? label ?? m.label;
  const Icon = icon || m.icon;
  const finalIconClassName = iconClassName || m.iconClassName || '';
  const extra = m.extraClassName || '';
  return (
    <span className={`status-badge ${TONE_CLASS[finalTone] || TONE_CLASS.slate} ${extra} ${className}`.trim()}>
      {Icon && <Icon className={finalIconClassName} />}
      {finalLabel}
    </span>
  );
}
