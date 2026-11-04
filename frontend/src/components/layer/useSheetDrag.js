import { useEffect, useRef, useState } from 'react';
import { createSheetDrag } from '../../utils/sheetDrag';

// 底页下拖关闭的事件绑定(状态机在 utils/sheetDrag.js):把返回的 handleProps 铺到抓手所在的头部区域。
// - 超过 4px 才算拖,这时才 setPointerCapture(点按不抢捕获,抓手按钮的 click 照常落到按钮上);
// - 拖动中面板挂 .is-dragging(去掉过渡、跟手),松手不够远就清掉 transform,由 CSS 过渡回弹
//   (减少动效时全局兜底把过渡压到 1ms,即「拖拽照常、没有回弹」);够远 / 够快就调 onClose;
// - 头部要 touch-action: none(否则触屏上浏览器接管成滚动,发 pointercancel)。
// consumeClick:抓手按钮的 onClick 先问它——刚拖过就吞掉这次 click。
export function useSheetDrag(panelRef, onClose) {
  const [drag] = useState(() => createSheetDrag());
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  const captured = useRef(null);

  const reset = () => {
    const panel = panelRef.current;
    if (!panel) return;
    panel.classList.remove('is-dragging');
    panel.style.transform = '';
  };

  const finish = (event, cancelled) => {
    const result = cancelled ? drag.cancel() : drag.end(event.clientY, event.timeStamp);
    if (captured.current != null) {
      try { event.currentTarget.releasePointerCapture?.(captured.current); } catch { /* 已释放 */ }
      captured.current = null;
    }
    if (result.close) {
      panelRef.current?.classList.remove('is-dragging'); // transform 留着:面板随即卸载,不回弹一下再消失
      closeRef.current?.();
      return;
    }
    reset();
  };

  const handleProps = {
    onPointerDown: (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      drag.start(event.clientY, event.timeStamp);
    },
    onPointerMove: (event) => {
      if (!drag.isActive()) return;
      const { dragging, offset } = drag.move(event.clientY, event.timeStamp);
      if (!dragging) return;
      if (captured.current == null) {
        try { event.currentTarget.setPointerCapture?.(event.pointerId); captured.current = event.pointerId; } catch { /* 不支持 */ }
      }
      const panel = panelRef.current;
      if (!panel) return;
      panel.classList.add('is-dragging');
      panel.style.transform = `translateY(${offset}px)`;
    },
    onPointerUp: (event) => finish(event, false),
    onPointerCancel: (event) => finish(event, true),
  };

  return { handleProps, consumeClick: () => drag.consumeClick() };
}
