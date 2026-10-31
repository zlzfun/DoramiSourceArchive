import { useRef } from 'react';
import { createPortal } from 'react-dom';
import { useLayer } from './useLayer';
import { useSheetDrag } from './useSheetDrag';
import LayerScrim from './LayerScrim';

// 抓手(样页拍板 33):真 <button>,名字写清对象(「关闭『…』的操作单」「收起往期早报」);点按即关,
// 往下拖也能关(拖动绑在它所在的头部区域,见 useSheetDrag)。抓手条本身 38×5,按钮命中区整条 44 高。
export function SheetGrab({ label, onActivate, consumeClick }) {
  return (
    <button
      type="button"
      className="m-sheet-grab"
      aria-label={label}
      onClick={() => {
        if (consumeClick?.()) return; // 刚拖过:这次 click 是松手合成的,不是点按
        onActivate?.();
      }}
    >
      <span className="m-sheet-grab-bar" aria-hidden="true" />
    </button>
  );
}

// 底页(sheet):遮罩 + 贴底面板 + 抓手。portal 到 body(避开变换祖先),面板 role="dialog" aria-modal。
// head 放在抓手下方、同属可拖区域(动作单的对象标题);children 是正文(菜单 / 月历…)。
export default function Sheet({
  open,
  onClose,
  label,
  grabLabel,
  head = null,
  className = '',
  initialFocus = null,
  panelProps = null,
  children,
}) {
  const rootRef = useRef(null);
  const panelRef = useRef(null);
  useLayer({ open, onClose, kind: 'sheet', panelRef, rootRef, initialFocus });
  const { handleProps, consumeClick } = useSheetDrag(panelRef, onClose);

  if (!open || typeof document === 'undefined') return null;
  return createPortal(
    <div className="m-sheet-layer" ref={rootRef}>
      <LayerScrim onClose={onClose} />
      <div
        ref={panelRef}
        className={`m-sheet ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        data-layer-panel=""
        {...panelProps}
      >
        <div className="m-sheet-head" {...handleProps}>
          <SheetGrab label={grabLabel || `关闭${label || ''}`} onActivate={onClose} consumeClick={consumeClick} />
          {head}
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
