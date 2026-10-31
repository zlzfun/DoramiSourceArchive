import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLayer } from './useLayer';
import { useSheetDrag } from './useSheetDrag';
import LayerScrim from './LayerScrim';
import { motionReduced } from '../../motion';

// 退场时长(issue #230 批 4a,审计 M-33「退场与入场成对」):底页滑回屏外、遮罩淡出,跑完再卸载。
// 与 mobile.css「批 4a」段 .m-sheet-layer.is-leaving 的过渡同长;减少动效时不播、当场卸载。
export const SHEET_LEAVE_MS = 260;

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
// head 放在抓手下方、同属可拖区域(动作单的对象标题);children 是正文(菜单 / 月历…);
// aside 是浮在面板上方的只读块(长按的预览卡;批 4a 从 head 里挪出:不跟着拖动区、量高时以面板为准)。
// 退场(批 4a):关的当下层行为(返回键、Esc、焦点、inert)就交还,同一批 DOM 再播 SHEET_LEAVE_MS 的退场(.is-leaving:
// 面板滑回、遮罩淡出、不接指针、整层 inert),跑完卸载。退场期间画的是调用方此刻给的内容——关的时候别清空
// (MobileReader 的长按动作单留着上一份;往期底页 / 正文「⋯」的内容本来就不随开合变)。
export default function Sheet({
  open,
  onClose,
  label,
  grabLabel,
  head = null,
  aside = null,
  className = '',
  initialFocus = null,
  panelProps = null,
  children,
}) {
  const rootRef = useRef(null);
  const panelRef = useRef(null);
  useLayer({ open, onClose, kind: 'sheet', panelRef, rootRef, initialFocus });
  const { handleProps, consumeClick } = useSheetDrag(panelRef, onClose);

  // open 由真变假的那一次渲染里就进入退场(渲染期按上一次的 open 调整状态,React 推荐的写法):
  // 这样关的那次提交不摘 DOM,面板与遮罩是同一批元素,过渡才接得上
  const [wasOpen, setWasOpen] = useState(open);
  const [leaving, setLeaving] = useState(false);
  if (open !== wasOpen) {
    setWasOpen(open);
    setLeaving(!open && !motionReduced());
  }
  useEffect(() => {
    if (!leaving) return undefined;
    const timer = window.setTimeout(() => setLeaving(false), SHEET_LEAVE_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  // 拖过才关的:面板身上还留着跟手的 transform,清掉后由 .is-leaving 的过渡从当前位置接着滑走
  useLayoutEffect(() => {
    if (!leaving) return;
    const panel = panelRef.current;
    if (!panel) return;
    panel.classList.remove('is-dragging');
    panel.style.transform = '';
  }, [leaving]);

  if ((!open && !leaving) || typeof document === 'undefined') return null;
  return createPortal(
    <div className={`m-sheet-layer${open ? '' : ' is-leaving'}`} ref={rootRef} inert={!open || undefined}>
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
        {aside}
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
