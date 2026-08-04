import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useModalA11y } from '../hooks/useModalA11y';

// 右键上下文菜单(v3.28,设计样页 docs/design/dorami-context-menu-quiet.html)。
// portal 到 body 是必须而非可选:祖先带 transform 会把 fixed 元素圈进自己的
// containing block(轨语言浮层已踩过的 stacking context 坑)。
// items 协议:{ key, label, icon, onClick, danger?, disabled? }
//           | { type: 'sep' } | { type: 'label', text }
// disabled 项不隐藏(菜单结构稳定,用户不用每次重新扫描):降透明 + 真 disabled,
// 方向键/Tab 天然跳过。
export default function ContextMenu({ x, y, items, onClose }) {
  const panelRef = useRef(null);
  // 定位快照:首帧隐形渲染实测宽高一次(高度随 items 数变,不能像 DateRangePicker
  // 用死常量),再夹取视口 + 贴下缘上翻;此后绝不跟随——滚动/缩放即关(快照纪律)。
  const [pos, setPos] = useState(null);
  // Esc(捕获阶段)+ 焦点移入/关闭归还 + Tab 内循环,复用模态的 a11y 原语(§8)。
  useModalA11y(true, onClose, panelRef);

  useLayoutEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = Math.max(8, Math.min(x, window.innerWidth - w - 8));
    let top = y;
    if (y + h > window.innerHeight - 8 && y - h > 8) top = y - h;
    setPos({ left, top: Math.max(8, top) });
  }, [x, y, items]);

  useEffect(() => {
    // 外点关闭沿全站浮层约定(mousedown,见 ShareMenu);滚动监听走捕获阶段——
    // 一个监听同时覆盖有 ref 的条目列与无 ref 的源栏原生滚动容器。
    const onDown = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) onClose?.();
    };
    const onSnapshotInvalid = () => onClose?.();
    document.addEventListener('mousedown', onDown);
    document.addEventListener('scroll', onSnapshotInvalid, true);
    window.addEventListener('resize', onSnapshotInvalid);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('scroll', onSnapshotInvalid, true);
      window.removeEventListener('resize', onSnapshotInvalid);
    };
  }, [onClose]);

  // 方向键循环 + Home/End(disabled 项不可聚焦,天然跳过);Esc 在 useModalA11y。
  const onKeyDown = (e) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const nodes = Array.from(panelRef.current?.querySelectorAll('.ctx-item:not(:disabled)') || []);
    if (nodes.length === 0) return;
    const idx = nodes.indexOf(document.activeElement);
    let next = 0;
    if (e.key === 'End') next = nodes.length - 1;
    else if (e.key === 'ArrowDown') next = idx < 0 ? 0 : (idx + 1) % nodes.length;
    else if (e.key === 'ArrowUp') next = idx < 0 ? nodes.length - 1 : (idx - 1 + nodes.length) % nodes.length;
    nodes[next].focus();
  };

  return createPortal(
    <div
      ref={panelRef}
      role="menu"
      tabIndex={-1}
      className="ctx-menu"
      /* 首帧直接渲染在光标处即可:useLayoutEffect 在 paint 前实测并夹取,不会闪帧。
         不可用 visibility:hidden 遮测量帧——隐藏元素不可聚焦,useModalA11y 的
         首焦(passive effect 可能在重定位 re-render 前刷新)会静默失败。 */
      style={pos ? { left: pos.left, top: pos.top } : { left: x, top: y }}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item, i) => {
        if (item.type === 'sep') return <div key={`sep-${i}`} className="ctx-sep" role="separator" />;
        if (item.type === 'label') return <div key={`label-${i}`} className="ctx-label">{item.text}</div>;
        const Icon = item.icon;
        return (
          <button
            key={item.key}
            type="button"
            role="menuitem"
            className={`ctx-item ${item.danger ? 'is-danger' : ''}`}
            disabled={item.disabled}
            aria-disabled={item.disabled || undefined}
            onClick={() => { onClose?.(); item.onClick?.(); }}
          >
            {Icon && <Icon aria-hidden="true" />}
            {item.label}
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
