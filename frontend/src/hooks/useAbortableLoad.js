import { useCallback, useEffect, useRef } from 'react';

// 竞态安全的列表加载：把三个 Tab 各自手写的「发新弃旧 + 卸载中止 + AbortError 静默」
// 收敛为一处约定。返回一个 run(fn) —— fn 收到一个 AbortSignal，发起请求前会 abort 掉
// 上一笔仍在飞行的请求；组件卸载时也 abort。fn 内部若因 signal 取消而抛 AbortError，
// run 会静默吞掉（返回 undefined），其余错误照常抛出交由调用方处理。
//
// 用法：
//   const run = useAbortableLoad();
//   const load = useCallback((page) => run(async (signal) => {
//     const data = await fetchArticles(filters, size, skip, true, { signal });
//     setArticles(data.items);           // 只有「最新一笔」才会走到这里
//   }), [run, filters]);
export function useAbortableLoad() {
  const abortRef = useRef(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  return useCallback(async (fn) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      return await fn(controller.signal);
    } catch (error) {
      if (error?.name === 'AbortError') return undefined; // 被更新的请求取消，静默丢弃
      throw error;
    }
  }, []);
}
