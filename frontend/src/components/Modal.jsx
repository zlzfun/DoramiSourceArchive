import { useRef } from 'react';
import { createPortal } from 'react-dom';
import { useModalTransition } from '../hooks/useModalTransition';
import { useOverlayClose } from '../hooks/useOverlayClose';
import { useLayer } from './layer/useLayer';

// 统一 modal 外壳：封装进退场动画（useModalTransition）+ .modal-overlay/.modal-panel 结构;
// 层的行为(issue #230 起)走层原语 components/layer/useLayer(kind='modal'):Esc / 焦点陷阱与归还、
// body 滚动锁、下层 inert,手机壳里另接返回键握手(系统返回先关这一层、不离站;桌面不挂)。
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
  const overlayRef = useRef(null);
  const panelRef = useRef(null);
  // 退场动画期间（open=false 但仍 mounted）不再抢焦点/拦 Esc/inert，交由离场；故用 open 而非 mounted。
  // 滚动锁走全站共用的引用计数锁(多层浮层关闭顺序不对称也不会把 hidden 留在 body 上)。
  useLayer({ open: open && mounted, onClose, kind: 'modal', panelRef, rootRef: overlayRef });

  // 遮罩判定(hooks/useOverlayClose):只认「按下与松开同在遮罩」;不需要面板 stopPropagation。
  // data-modal-layer:「点外即收起」的浮层(问答面板等)据此把弹窗里的点击(含遮罩)当作上层操作、不收起自己。
  const overlayHandlers = useOverlayClose(onClose, closeOnOverlay);

  if (!mounted) return null;

  const sizeClass = SIZE_CLASS[size] ?? SIZE_CLASS['2xl'];
  const node = (
    <div
      ref={overlayRef}
      className={`modal-overlay ${centered ? 'items-center' : ''} ${closing ? 'is-closing' : ''} ${overlayClassName}`.trim()}
      data-modal-layer=""
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
