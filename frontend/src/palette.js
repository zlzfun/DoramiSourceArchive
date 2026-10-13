import { useCallback, useSyncExternalStore } from 'react';

// 中性色偏好(issue #184):'porcelain' 瓷·冷调(默认)| 'graphite' 墨·中性 | 'paper' 宣·暖调。
// 三套只在中性色(底色、细线、墨色)的色温上不同,结构、明暗次序与强调色一致,亮色暗色都可用。
// 生效方式是 <html data-palette="…">,index.css 里 graphite / paper 的 token 覆盖挂在这个属性上;
// index.html 首帧脚本先行设置,防首屏闪色。形态照搬 motion.js 的模块级 store:
// 同标签页 setter 立即生效、React 消费者经 useSyncExternalStore 重渲染、其它标签页经 storage 事件同步。
export const PALETTES = ['porcelain', 'graphite', 'paper'];
export const DEFAULT_PALETTE = 'porcelain';
const STORAGE_KEY = 'dorami-palette';
const ATTR = 'data-palette';
const HAS_WINDOW = typeof window !== 'undefined';

export function normalizePalette(value) {
  return PALETTES.includes(value) ? value : DEFAULT_PALETTE;
}

export function readPalettePref() {
  try {
    return normalizePalette(localStorage.getItem(STORAGE_KEY));
  } catch {
    /* localStorage 不可用时退回默认 */
    return DEFAULT_PALETTE;
  }
}

function applyPalette(palette) {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute(ATTR, palette);
}

let current = readPalettePref();
const listeners = new Set();

function commit(next) {
  const palette = normalizePalette(next);
  applyPalette(palette);
  if (palette === current) return;
  current = palette;
  listeners.forEach((fn) => fn());
}

/** 写入偏好:同步落 localStorage(失败不阻断,本页生命周期内仍生效)+ DOM 属性 + 通知订阅者。 */
export function setPalettePref(next) {
  const palette = normalizePalette(next);
  try {
    localStorage.setItem(STORAGE_KEY, palette);
  } catch {
    /* 忽略写入失败 */
  }
  commit(palette);
}

function getSnapshot() {
  return current;
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

if (HAS_WINDOW) {
  window.addEventListener('storage', (e) => {
    if (e.key !== STORAGE_KEY && e.key !== null) return;
    commit(e.key === null ? DEFAULT_PALETTE : e.newValue);
  });
  // 首帧脚本已按 localStorage 挂过属性;这里再对齐一次,兜住脚本缺失或顺序变化。
  applyPalette(current);
}

export function usePalettePref() {
  const palette = useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT_PALETTE);
  const setPalette = useCallback((next) => setPalettePref(next), []);
  return { palette, setPalette };
}
