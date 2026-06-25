import { useEffect } from 'react';
import { useModalTransition } from '../hooks/useModalTransition';

// 统一 modal 外壳：封装进退场动画（useModalTransition）+ body 滚动锁 + .modal-overlay/.modal-panel 结构。
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
  children,
}) {
  const { mounted, closing } = useModalTransition(open, transitionMs);

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
        className={`modal-panel ${SIZE_CLASS[size] || SIZE_CLASS['2xl']} ${panelClassName}`.trim()}
        onMouseDown={closeOnOverlay ? (e) => e.stopPropagation() : undefined}
      >
        {children}
      </div>
    </div>
  );
}
