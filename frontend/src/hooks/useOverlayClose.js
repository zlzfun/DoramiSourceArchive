import { useState } from 'react';
import { createOverlayCloseGuard } from '../utils/overlayClose';

// 遮罩点击关闭的事件处理(issue #104 判定 + issue #230 层原语):「按下与松开同在遮罩」才关。
// Modal 的 .modal-overlay 与层原语的遮罩(components/layer/LayerScrim)共用这一个出口——
// 长按弹出的动作单(M-01)正是靠它:手指在条目上按下、松手的合成 click 落到新盖上的遮罩,按下不在遮罩,不关。
// 返回要铺到遮罩元素上的 handler;enabled 为假时返回 null(不铺)。
export function useOverlayClose(onClose, enabled = true) {
  // 判定状态机只建一次、跨渲染持有 pointer 序列;它只判定不动作,关不关在 onClick 里决定。
  const [guard] = useState(createOverlayCloseGuard);
  if (!enabled) return null;
  return {
    onPointerDown: guard.onPointerDown,
    onPointerUp: guard.onPointerUp,
    onPointerCancel: guard.onPointerCancel,
    onClick: (event) => { if (guard.shouldClose(event)) onClose?.(); },
  };
}
