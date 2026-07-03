import { useEffect, useRef } from 'react';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';

// 统一 modal 外壳：封装进退场动画（useModalTransition）+ body 滚动锁 + 可访问性
// （Esc 关闭 / 焦点陷阱 / role=dialog aria-modal，见 useModalA11y）+ .modal-overlay/.modal-panel 结构。
// 各 modal 只需把 header/body/footer 作为 children 传入，不再各自重复这套样板。
const SIZE_CLASS = {
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
  children,
}) {
  const { mounted, closing } = useModalTransition(open, transitionMs);
  const panelRef = useRef(null);
  // 退场动画期间（open=false 但仍 mounted）不再抢焦点/拦 Esc，交由离场；故用 open 而非 mounted。
  useModalA11y(open && mounted, onClose, panelRef);

  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open]);

  if (!mounted) return null;

  // 用 mousedown 关闭遮罩，避免在面板内拖选文本松手落到遮罩而误关。
  const handleOverlayMouseDown = closeOnOverlay ? () => onClose?.() : undefined;

  return (
    <div
      className={`modal-overlay ${centered ? 'items-center' : ''} ${closing ? 'is-closing' : ''} ${overlayClassName}`.trim()}
      onMouseDown={handleOverlayMouseDown}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        className={`modal-panel ${SIZE_CLASS[size] || SIZE_CLASS['2xl']} ${panelClassName}`.trim()}
        onMouseDown={closeOnOverlay ? (e) => e.stopPropagation() : undefined}
      >
        {children}
      </div>
    </div>
  );
}
