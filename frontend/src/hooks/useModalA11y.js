import { useEffect } from 'react';

// 模态可访问性：Esc 关闭 + 打开时焦点移入面板 + Tab 焦点陷阱 + 关闭后焦点归还触发者。
// 用法：给面板元素挂 ref（面板需可聚焦，建议 tabIndex={-1}），active 为弹窗是否打开。
//   const panelRef = useRef(null);
//   useModalA11y(active, onClose, panelRef);
//   <div ref={panelRef} role="dialog" aria-modal="true" tabIndex={-1}>…</div>
// 说明：
// - 尊重 React 的 autoFocus——若 commit 阶段已把焦点放进面板内，则不再抢焦点。
// - keydown 用捕获阶段监听，保证 Esc 在冒泡被 stopPropagation 前先被拦到。
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useModalA11y(active, onClose, panelRef) {
  useEffect(() => {
    if (!active) return undefined;
    const panel = panelRef.current;
    const previouslyFocused = document.activeElement;

    const focusables = () => (panel
      ? Array.from(panel.querySelectorAll(FOCUSABLE)).filter((el) => el.offsetParent !== null)
      : []);

    // 焦点移入：React autoFocus 已把焦点落进面板内则不打扰，否则聚焦首个可聚焦元素（兜底面板本身）。
    if (!(panel && panel.contains(document.activeElement))) {
      (focusables()[0] || panel)?.focus?.();
    }

    const onKeyDown = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose?.(); return; }
      if (e.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) { e.preventDefault(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      // 焦点归还给触发者（若它仍在文档内）。
      if (previouslyFocused && previouslyFocused.focus && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [active, onClose, panelRef]);
}
