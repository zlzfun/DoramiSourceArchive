import { useRef, useState } from 'react';
import Sheet from '../layer/Sheet';
import { nextMenuIndex } from '../../utils/menuNav';

const GRAB_TITLE_MAX = 24;
const shortTitle = (text) => (text.length > GRAB_TITLE_MAX ? `${text.slice(0, GRAB_TITLE_MAX)}…` : text);

// 长按动作单(移动波 Wave2,样页 dorami-mobile-quiet 画面⑤;issue #230 迁到层原语 Sheet):
// 桌面右键菜单(ContextMenu)的 bottom-sheet 翻译——items 契约完全同构
// ({key,label,icon,onClick,disabled,danger} | {type:'sep'} | {type:'label',text}),
// 组序即跨端肌肉记忆:读态/收藏首组 → 链接次组 → 重操作/破坏性末组。行高 44px(触摸目标);disabled 不隐藏(结构稳定,降透明)。
// 层的行为全在 Sheet:遮罩「按下与松开同在遮罩」才关(长按松手不再立刻关掉,审计 M-01)、返回键 / Esc / 抓手点按与下拖、
// 下层 inert、焦点进出。菜单按 WAI-ARIA APG(审计 M-35):键盘打开焦点落首项、指针打开落在菜单上,
// ↑ ↓ / Home / End 走项(漫游 tabindex),Esc 由层栈消费,不再自挂 document 监听。
// 正文「⋯」底页(issue #230 批 3c)不带对象标题:label 直接给层名(「更多操作」,抓手念「关闭更多操作」),
// className 给面板换样式(is-float = 样页 read-menu 的浮动磨砂卡)。
// preview(issue #230 批 3b,样页拍板 5「长按写清对象」):给了就在动作单上方浮一张抬起的预览卡,代替一行对象标题;
// 预览卡只读(aria-hidden、不接指针,点它等于点遮罩),读屏仍由动作单名说清对象。
export default function ActionSheet({ open, title = '', items = [], onClose, label: labelProp = '', className = '', preview = null }) {
  const menuRef = useRef(null);
  const label = labelProp || (title ? `「${shortTitle(title)}」的操作` : '操作');
  return (
    <Sheet
      open={open}
      onClose={onClose}
      label={label}
      grabLabel={`关闭${label}`}
      className={className}
      head={preview
        ? <div className="m-sheet-peek" aria-hidden="true">{preview}</div>
        : title ? <div className="m-sheet-obj">{title}</div> : null}
      initialFocus={(panel, { keyboard }) => (keyboard
        ? menuRef.current?.querySelector('[role="menuitem"]')
        : menuRef.current) || panel}
    >
      {/* Sheet 关着时不渲染 children:菜单每次打开都是新实例,漫游起点回到首项 */}
      <ActionMenu menuRef={menuRef} title={title || labelProp} items={items} onClose={onClose} />
    </Sheet>
  );
}

function ActionMenu({ menuRef, title, items, onClose }) {
  const [active, setActive] = useState(0);
  const nodes = () => Array.from(menuRef.current?.querySelectorAll('[role="menuitem"]') || []);
  const onKeyDown = (event) => {
    const list = nodes();
    const next = nextMenuIndex(list.length, list.indexOf(document.activeElement), event.key);
    if (next == null) return;
    event.preventDefault();
    setActive(next);
    list[next]?.focus();
  };

  // 菜单项在漫游序列里的序号(分隔线 / 组标签不占位)
  const order = [];
  for (let i = 0, n = 0; i < items.length; i += 1) {
    order.push(items[i].type === 'sep' || items[i].type === 'label' ? -1 : n++);
  }
  return (
    <div ref={menuRef} className="m-sheet-menu" role="menu" aria-label={title || '操作'} tabIndex={-1} onKeyDown={onKeyDown}>
      {items.map((item, i) => {
        if (item.type === 'sep') return <div key={`sep-${i}`} className="m-sheet-sep" role="separator" />;
        if (item.type === 'label') return <div key={`label-${i}`} className="m-sheet-label" role="presentation">{item.text}</div>;
        const index = order[i];
        const Icon = item.icon;
        const disabled = Boolean(item.disabled);
        return (
          <button
            key={item.key || i}
            type="button"
            role="menuitem"
            tabIndex={index === active ? 0 : -1}
            aria-disabled={disabled || undefined}
            className={`m-sheet-item ${item.danger ? 'is-danger' : ''}`}
            onFocus={() => setActive(index)}
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
  );
}
