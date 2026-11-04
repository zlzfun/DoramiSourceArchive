// 运维首页图表件的共用工具(issue #114):格式化、柱形路径、容器实测、浮层上下文与 hook。
// 与组件分文件放(react-refresh 要求组件文件只导出组件)。
import { createContext, useCallback, useContext, useLayoutEffect, useState } from 'react';

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
export const fmt = (n) => Number(n || 0).toLocaleString('en-US');
export const md = (iso) => String(iso || '').slice(5);
export function dayHead(iso, todayIso) {
  const d = new Date(`${iso}T00:00:00`);
  const wd = Number.isNaN(d.getTime()) ? '' : ` ${WEEK[d.getDay()]}`;
  return `${md(iso)}${wd}${iso === todayIso ? ' · 今天' : ''}`;
}
export function niceCeil(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 3 ? 3 : f <= 4 ? 4 : f <= 5 ? 5 : 10) * p;
}
export const axisFmt = (v) => (v >= 1e6 ? `${+(v / 1e6).toFixed(1)}M` : v >= 1e4 ? `${Math.round(v / 1e3)}k` : v >= 1e3 ? `${+(v / 1e3).toFixed(1)}k` : String(+v.toFixed(1)));

// 柱:顶端圆角、基线方角。
export function colPath(x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  const f = (v) => v.toFixed(1);
  if (!rr) return `M${f(x)},${f(y + h)}V${f(y)}H${f(x + w)}V${f(y + h)}Z`;
  return `M${f(x)},${f(y + h)}V${f(y + rr)}q0,-${f(rr)} ${f(rr)},-${f(rr)}H${f(x + w - rr)}q${f(rr)},0 ${f(rr)},${f(rr)}V${f(y + h)}Z`;
}

// 容器实测宽高(图按像素重画,不靠 viewBox 拉伸,字号恒为真实 12px)。
// callback ref:空态 → 数据态换了节点也会重新挂观察器(codex r1 #7)。
export function useBox() {
  const [el, ref] = useState(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    if (!el) return undefined;
    const read = () => setBox((prev) => {
      const w = Math.floor(el.clientWidth); const h = Math.floor(el.clientHeight);
      return prev.w === w && prev.h === h ? prev : { w, h };
    });
    read();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [ref, box];
}

export const TipCtx = createContext(null);

export function useOpsTip() {
  return useContext(TipCtx) || { show: () => {}, hide: () => {} };
}

// 数字 / 页名的锚定浮层(锚在元素下方,贴底翻到上方)。
export function useAnchorTip(build) {
  const tip = useOpsTip();
  const onEnter = useCallback((e) => {
    const t = build();
    if (t) tip.show({ ...t, anchor: e.currentTarget.getBoundingClientRect() });
  }, [build, tip]);
  return { onMouseEnter: onEnter, onMouseLeave: tip.hide, onFocus: onEnter, onBlur: tip.hide };
}

