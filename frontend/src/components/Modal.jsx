import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock';
import { createOverlayCloseGuard } from '../utils/overlayClose';

// 统一 modal 外壳：封装进退场动画（useModalTransition）+ body 滚动锁 + 可访问性
// （Esc 关闭 / 焦点陷阱 / role=dialog aria-modal，见 useModalA11y）+ .modal-overlay/.modal-panel 结构。
// 各 modal 只需把 header/body/footer 作为 children 传入，不再各自重复这套样板。
//
// 遮罩关闭判定只在这里一处(issue #104):closeOnOverlay 时按「按下与松开同在遮罩」核对,
// 见 utils/overlayClose.js;业务弹窗不得自写 .modal-overlay 元素(lint dorami/no-raw-modal-overlay 拦)。
// 迁移口子:as(面板元素,表单弹窗传 'form')/ panelProps(透传面板,如 onSubmit)/ role
// (确认框 'alertdialog')/ portal(挂到 body,避开变换祖先造成的 fixed 错位)/ size='none'
// (面板自带宽度,如 .sett-cab / .csrc-sheet——utilities 层的 max-w-* 会压过它们)。
const SIZE_CLASS = {
  none: '',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
  '4xl': 'max-w-4xl',
  '5xl': 'max-w-5xl',
  '6xl': 'max-w-6xl',
};

export default function Modal({
  open,
  onClose,
  size = '2xl',
  closeOnOverlay = false,
  centered = false,
  overlayClassName = '',
  panelClassName = '',
  transitionMs,
  ariaLabel,
  role = 'dialog',
  as: PanelTag = 'div',
  panelProps,
  portal = false,
  children,
}) {
  const { mounted, closing } = useModalTransition(open, transitionMs);
  const panelRef = useRef(null);
  // 退场动画期间（open=false 但仍 mounted）不再抢焦点/拦 Esc，交由离场；故用 open 而非 mounted。
  useModalA11y(open && mounted, onClose, panelRef);

  // 判定状态机只建一次、跨渲染持有 pointer 序列;它只判定不动作,关不关在 onClick 里决定。
  const [overlayGuard] = useState(createOverlayCloseGuard);
  const overlayHandlers = closeOnOverlay ? {
    onPointerDown: overlayGuard.onPointerDown,
    onPointerUp: overlayGuard.onPointerUp,
    onPointerCancel: overlayGuard.onPointerCancel,
    onClick: (event) => { if (overlayGuard.shouldClose(event)) onClose?.(); },
  } : null;

  // 滚动锁走全站共用的引用计数锁(多层浮层关闭顺序不对称也不会把 hidden 留在 body 上)。
  useBodyScrollLock(open);

  if (!mounted) return null;

  const sizeClass = SIZE_CLASS[size] ?? SIZE_CLASS['2xl'];
  const node = (
    <div
      className={`modal-overlay ${centered ? 'items-center' : ''} ${closing ? 'is-closing' : ''} ${overlayClassName}`.trim()}
      {...overlayHandlers}
    >
      <PanelTag
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        className={`modal-panel ${sizeClass} ${panelClassName}`.trim()}
        {...panelProps}
      >
        {children}
      </PanelTag>
    </div>
  );
  return portal ? createPortal(node, document.body) : node;
}
