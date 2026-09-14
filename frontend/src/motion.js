import { useCallback, useEffect, useState } from 'react';

// 动效偏好(issue #73):'full'(默认,所有人看完整动画)| 'reduce'(应用内「减少动效」开关)。
// 不再读 OS 的 prefers-reduced-motion——Windows「关闭动画效果」(远程桌面/虚拟机常为性能默认)
// 与 macOS「减少动态效果」(刻意的无障碍选择)在 CSS 里分不清,内网读者以 Windows 为主,
// 该查询在这里多数是误报(登录演出 v3.22.2、轨面揭示 #73 先后中招)。改为应用内开关:
// 默认全员完整,真正需要的人在 设置 → 外观 打开;生效方式是 <html data-motion="reduce">,
// index.css 里所有降级规则都挂在这个属性上(index.html 首帧脚本先行设置,防首屏闪动)。
const STORAGE_KEY = 'dorami-motion';
const ATTR = 'data-motion';

export function readMotionPref() {
  try {
    if (localStorage.getItem(STORAGE_KEY) === 'reduce') return 'reduce';
  } catch {
    /* localStorage 不可用时退回 full */
  }
  return 'full';
}

function applyMotion(pref) {
  const root = document.documentElement;
  if (pref === 'reduce') root.setAttribute(ATTR, 'reduce');
  else root.removeAttribute(ATTR);
}

/** 同步读取当前是否处于减少动效(供 JS 编排的动画判定,如速读卡里程表、平滑滚动)。 */
export function motionReduced() {
  return typeof document !== 'undefined'
    && document.documentElement.getAttribute(ATTR) === 'reduce';
}

/** 平滑滚动的 behavior 取值:减少动效时直接跳到位置。 */
export function scrollBehavior() {
  return motionReduced() ? 'auto' : 'smooth';
}

export function useMotionPref() {
  const [motion, setMotionState] = useState(readMotionPref);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, motion);
    } catch {
      /* 忽略写入失败 */
    }
    applyMotion(motion);
  }, [motion]);

  const setMotion = useCallback((next) => setMotionState(next === 'reduce' ? 'reduce' : 'full'), []);
  return { motion, setMotion, reduced: motion === 'reduce' };
}
