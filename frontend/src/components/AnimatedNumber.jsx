import { useEffect, useRef, useState } from 'react';

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// 统计数字滚动到位（count-up）。value 变化时从当前显示值缓动到新值。
// 非有限数值（NaN/字符串）直接原样渲染，不做动画。
export default function AnimatedNumber({ value, duration = 1400, className }) {
  const target = Number(value);
  const animatable = Number.isFinite(target);
  const [display, setDisplay] = useState(animatable ? target : value);
  const fromRef = useRef(animatable ? target : 0);
  const rafRef = useRef(null);

  useEffect(() => {
    if (!animatable) { setDisplay(value); return undefined; }
    if (prefersReducedMotion()) { setDisplay(target); fromRef.current = target; return undefined; }

    const from = Number.isFinite(fromRef.current) ? fromRef.current : 0;
    if (from === target) { setDisplay(target); return undefined; }

    const start = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      const current = from + (target - from) * eased;
      setDisplay(current);
      if (p < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        fromRef.current = target;
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [target, value, animatable, duration]);

  if (!animatable) return <span className={className}>{value}</span>;
  return <span className={className}>{Math.round(display).toLocaleString()}</span>;
}
