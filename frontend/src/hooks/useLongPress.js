import { useCallback, useEffect, useRef } from 'react';

/**
 * 长按手势(移动波 Wave2):桌面右键菜单的触屏等价原语。
 *
 * 返回 bind(payload) 工厂——展开到目标元素上,长按(默认 450ms)即回调 onLongPress(payload)。
 * 两条触发路径互补:
 *  - touch 计时:iOS Safari 不派发 contextmenu(长按走原生 callout),唯一可靠路径。
 *    位移超容差(滚动意图)即取消;触发后抑制随后的合成 click(否则长按松手会顺带
 *    打开文章——click 由同一次触摸合成,capture 阶段拦截)。
 *  - contextmenu:Android 长按/桌面右键会派发,preventDefault 后直接触发,
 *    与 touch 计时可能双双命中(同 payload 重复 set state,幂等无害)。
 * 消费侧配 CSS `-webkit-touch-callout: none; user-select: none;` 抑制 iOS 原生
 * 放大镜/选词(.m-press 类,见 mobile.css)。
 * 吞 click 只认长按刚触发后的那一下(SWALLOW_MS 内):长按松手不一定合成 click(Android 长按、iOS 长按都可能不发),
 * 旗标若一直挂着,下一次真点击(鼠标、键盘 Enter、外接触控板)会被白白吞掉、要点两下才进正文(issue #230 批 4a 查出)。
 */
const SWALLOW_MS = 800;
export function useLongPress(onLongPress, { ms = 450, moveTolerance = 12 } = {}) {
  const timerRef = useRef(null);
  const originRef = useRef(null);
  const firedRef = useRef(0);   // 长按触发的时刻;0 = 没有待吞的 click
  const cbRef = useRef(onLongPress);
  useEffect(() => { cbRef.current = onLongPress; });

  const clear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    originRef.current = null;
  }, []);

  return useCallback((payload) => ({
    onTouchStart: (e) => {
      if (e.touches.length !== 1) { clear(); return; }
      const t = e.touches[0];
      originRef.current = { x: t.clientX, y: t.clientY };
      firedRef.current = 0;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        firedRef.current = Date.now();
        cbRef.current?.(payload);
      }, ms);
    },
    onTouchMove: (e) => {
      const o = originRef.current;
      if (!o || !timerRef.current) return;
      const t = e.touches[0];
      if (Math.abs(t.clientX - o.x) > moveTolerance || Math.abs(t.clientY - o.y) > moveTolerance) clear();
    },
    onTouchEnd: clear,
    onTouchCancel: clear,
    // 长按已触发 → 这次触摸的合成 click 不再向下传递(capture 先于子元素 onClick)
    onClickCapture: (e) => {
      const firedAt = firedRef.current;
      firedRef.current = 0;
      if (firedAt && Date.now() - firedAt < SWALLOW_MS + ms) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    onContextMenu: (e) => {
      e.preventDefault();
      clear(); // touch 计时未到而系统 contextmenu 先到:取消计时,单路径触发
      cbRef.current?.(payload);
    },
  }), [clear, ms, moveTolerance]);
}
