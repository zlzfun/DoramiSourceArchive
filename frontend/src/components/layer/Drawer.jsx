import { useRef } from 'react';
import { useLayer } from './useLayer';
import LayerScrim from './LayerScrim';

// 侧抽屉(drawer):遮罩 + 左侧面板,面板 role="dialog" aria-modal。原地渲染(不 portal),层级由 .m-drawer-layer 定。
// 出口:点遮罩(卡外)/ 面板里的关闭钮(由调用方放在标题行)/ Esc / 系统返回。
export default function Drawer({
  open,
  onClose,
  label,
  className = '',
  initialFocus = null,
  children,
}) {
  const rootRef = useRef(null);
  const panelRef = useRef(null);
  useLayer({ open, onClose, kind: 'drawer', panelRef, rootRef, initialFocus });

  if (!open) return null;
  return (
    <div className="m-drawer-layer" ref={rootRef}>
      <LayerScrim onClose={onClose} />
      <div
        ref={panelRef}
        className={`m-drawer ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        data-layer-panel=""
      >
        {children}
      </div>
    </div>
  );
}
