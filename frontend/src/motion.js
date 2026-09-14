import { useCallback, useSyncExternalStore } from 'react';

// 动效偏好(issue #73):'full'(默认,所有人看完整动画)| 'reduce'(应用内「减少动效」开关)。
// 不再读 OS 的 prefers-reduced-motion——Windows「关闭动画效果」(远程桌面/虚拟机常为性能默认)
// 与 macOS「减少动态效果」(刻意的无障碍选择)在 CSS 里分不清,内网读者以 Windows 为主,
// 该查询在这里多数是误报(登录演出 v3.22.2、轨面揭示 #73 先后中招)。改为应用内开关:
// 默认全员完整,真正需要的人在 设置 → 外观 打开;生效方式是 <html data-motion="reduce">,
// index.css 里所有降级规则都挂在这个属性上(index.html 首帧脚本先行设置,防首屏闪动)。
//
// 形态是模块级 store(codex 检视 F2):内存快照 + 订阅者集合 + 模块顶层常驻 storage 监听——
// 同标签页 setter 立即生效、React 消费者经 useSyncExternalStore 重渲染、其它已开标签页也同步
// 更新 <html> 属性(即使当时没有任何 React 订阅者)。setter 与 storage 回调都先 applyMotion 再通知,
// DOM 属性恒与 store 一致,非 React 调用点(scrollBehavior)直接读 DOM 即可。
const STORAGE_KEY = 'dorami-motion';
const ATTR = 'data-motion';
const HAS_WINDOW = typeof window !== 'undefined';

function normalize(value) {
  return value === 'reduce' ? 'reduce' : 'full';
}

export function readMotionPref() {
  try {
    return normalize(localStorage.getItem(STORAGE_KEY));
  } catch {
    /* localStorage 不可用时退回 full */
    return 'full';
  }
}

function applyMotion(pref) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (pref === 'reduce') root.setAttribute(ATTR, 'reduce');
  else root.removeAttribute(ATTR);
}

let current = readMotionPref();
const listeners = new Set();

function commit(next) {
  const pref = normalize(next);
  applyMotion(pref);
  if (pref === current) return;
  current = pref;
  listeners.forEach((fn) => fn());
}

/** 写入偏好:同步落 localStorage(失败不阻断,本页生命周期内仍生效)+ DOM 属性 + 通知订阅者。 */
export function setMotionPref(next) {
  const pref = normalize(next);
  try {
    localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    /* 忽略写入失败 */
  }
  commit(pref);
}

function getSnapshot() {
  return current;
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

if (HAS_WINDOW) {
  // 常驻监听:其它标签页改了偏好,本页即使没有任何 React 订阅者也要更新 <html> 属性。
  window.addEventListener('storage', (e) => {
    if (e.key !== STORAGE_KEY && e.key !== null) return;
    commit(e.key === null ? 'full' : e.newValue);
  });
  // 首帧脚本已按 localStorage 挂过属性;这里再对齐一次,兜住脚本缺失/顺序变化。
  applyMotion(current);
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

/** React 订阅:布尔形态,供图表等把偏好显式传进第三方动画(recharts isAnimationActive)。 */
export function useMotionReduced() {
  return useSyncExternalStore(subscribe, getSnapshot, () => 'full') === 'reduce';
}

export function useMotionPref() {
  const motion = useSyncExternalStore(subscribe, getSnapshot, () => 'full');
  const setMotion = useCallback((next) => setMotionPref(next), []);
  return { motion, setMotion, reduced: motion === 'reduce' };
}
