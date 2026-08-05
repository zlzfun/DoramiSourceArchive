import { useEffect } from 'react';
import { createPortal } from 'react-dom';

// 长按动作单(移动波 Wave2,样页 dorami-mobile-quiet 画面⑤):
// 桌面右键菜单(ContextMenu)的 bottom-sheet 翻译——items 契约完全同构
// ({key,label,icon,onClick,disabled,danger} | {type:'sep'} | {type:'label',text}),
// 组序即跨端肌肉记忆:读态/收藏首组 → 链接次组 → 重操作/破坏性末组。
// 行高 44px(触摸目标);disabled 不隐藏(结构稳定,降透明);外点/Esc 即关。
export default function ActionSheet({ open, title = '', items = [], onClose }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="m-sheet-layer" role="presentation">
      <div className="m-dim" onClick={onClose} aria-hidden="true" />
      <div className="m-sheet" role="menu" aria-label={title || '操作'}>
        <div className="m-sheet-grab" aria-hidden="true" />
        {title && <div className="m-sheet-obj">{title}</div>}
        {items.map((item, i) => {
          if (item.type === 'sep') return <div key={`sep-${i}`} className="m-sheet-sep" role="separator" />;
          if (item.type === 'label') return <div key={`label-${i}`} className="m-sheet-label">{item.text}</div>;
          const Icon = item.icon;
          const disabled = Boolean(item.disabled);
          return (
            <button
              key={item.key || i}
              type="button"
              role="menuitem"
              aria-disabled={disabled || undefined}
              className={`m-sheet-item ${item.danger ? 'is-danger' : ''}`}
              onClick={() => {
                if (disabled) return;
                onClose?.();          // 先关(与 ContextMenu 同序):动作可能弹 Toast/改列表
                item.onClick?.();
              }}
            >
              {Icon && <Icon aria-hidden="true" />}
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
    </div>,
    document.body,
  );
}
