import { useEffect } from 'react';

// 触屏长按 = 右键菜单(issue #230 批 4a,审计 M-15;方案 §8 平板档 a「≥ 1025 触屏补触屏路径」)。
// 桌面四栏在 iPad 横屏这类触屏上没有右键:Android 长按本会派发 contextmenu,iOS / iPadOS Safari 不会(长按走系统气泡),
// 条目行、源行、社交卡上的右键菜单(收藏 / 标读 / 复制链接 / 退订…)于是够不着,源行的退订钮还只在悬停时浮出。
// 这里在根节点上做一层桥:手指按住 selector 命中的那一行 ms 毫秒不动,就在那一行上派发一个合成的 contextmenu
// (带按住的坐标),行上既有的 onContextMenu 原样接住、在手指处弹菜单;系统自己派发了 contextmenu 就取消计时、不重复。
// 长按触发后这次触摸的 touchend 取消默认动作——不再合成 mousedown / click,行不会被点开,菜单也不会被松手立刻关掉。
// 只认触摸事件:鼠标、触控板的行为与观感零变化。
export function useTouchHoldMenu(rootRef, selector, { ms = 450, tolerance = 10 } = {}) {
  useEffect(() => {
    const root = rootRef.current;
    if (!root || typeof window === 'undefined') return undefined;
    let timer = null;
    let origin = null;
    let fired = false;
    const clear = () => {
      if (timer) window.clearTimeout(timer);
      timer = null;
    };
    const onStart = (event) => {
      clear();
      fired = false;
      if (event.touches.length !== 1) return;
      const row = event.target instanceof Element ? event.target.closest(selector) : null;
      if (!row || !root.contains(row)) return;
      const touch = event.touches[0];
      origin = { x: touch.clientX, y: touch.clientY };
      timer = window.setTimeout(() => {
        timer = null;
        if (!row.isConnected) return;
        fired = true;
        row.dispatchEvent(new MouseEvent('contextmenu', {
          bubbles: true, cancelable: true, view: window, button: 2, clientX: origin.x, clientY: origin.y,
        }));
      }, ms);
    };
    const onMove = (event) => {
      if (!timer || !origin) return;
      const touch = event.touches[0];
      if (Math.abs(touch.clientX - origin.x) > tolerance || Math.abs(touch.clientY - origin.y) > tolerance) clear();
    };
    const onEnd = (event) => {
      clear();
      if (!fired) return;
      fired = false;
      if (event.cancelable) event.preventDefault();
    };
    // 系统派发的 contextmenu(Android 长按):已经有人弹菜单了,本层的计时作废
    const onNativeMenu = (event) => { if (event.isTrusted) clear(); };
    root.addEventListener('touchstart', onStart, { passive: true });
    root.addEventListener('touchmove', onMove, { passive: true });
    root.addEventListener('touchend', onEnd, { passive: false });
    root.addEventListener('touchcancel', clear, { passive: true });
    root.addEventListener('contextmenu', onNativeMenu, true);
    return () => {
      clear();
      root.removeEventListener('touchstart', onStart);
      root.removeEventListener('touchmove', onMove);
      root.removeEventListener('touchend', onEnd);
      root.removeEventListener('touchcancel', clear);
      root.removeEventListener('contextmenu', onNativeMenu, true);
    };
  }, [rootRef, selector, ms, tolerance]);
}
