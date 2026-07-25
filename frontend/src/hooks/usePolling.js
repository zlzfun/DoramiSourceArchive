import { useEffect } from 'react';

// 周期轮询统一封装:每 ms 触发一次 fn。
// - pauseWhenHidden(默认开):页面不可见时跳过本轮请求,计时照常,回到可见后
//   在下一周期自然恢复——与各 Tab 原手写轮询的 document.hidden 判断语义一致。
// - immediate:挂载时是否立即先跑一轮(false = 纯后台刷新,首拉由挂载逻辑负责)。
// - enabled=false 时不轮询(条件轮询,如「仅生成中」)。
// - 递归 setTimeout 而非 setInterval:上一轮完成后才计时,慢请求不堆叠。
// fn 需用 useCallback 稳定——fn/间隔变化会重启轮询;轮询失败静默,等下一周期。
export function usePolling(fn, ms, { immediate = true, pauseWhenHidden = true, enabled = true } = {}) {
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      if (cancelled) return;
      if (!(pauseWhenHidden && document.hidden)) {
        try { await fn(); } catch { /* 轮询失败静默,等下一周期 */ }
      }
      if (!cancelled) timer = setTimeout(tick, ms);
    };
    if (immediate) tick();
    else timer = setTimeout(tick, ms);
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [fn, ms, immediate, pauseWhenHidden, enabled]);
}
