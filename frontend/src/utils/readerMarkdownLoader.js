// ReaderMarkdownView(react-markdown + remark / rehype + KaTeX)的按需加载器(issue #230 审计 M-14)。
// 单例:同一会话只拉一次;失败不缓存,下次调用重试。组件侧入口见 components/ReaderMarkdown.jsx。
let view = null;
let pending = null;

export function loadedReaderMarkdownView() {
  return view;
}

export function loadReaderMarkdownView() {
  if (view) return Promise.resolve(view);
  if (!pending) {
    pending = import('../components/ReaderMarkdownView.jsx').then(
      (mod) => {
        view = mod.default;
        return view;
      },
      (error) => {
        pending = null;
        throw error;
      },
    );
  }
  return pending;
}

// 读者首屏落定后空闲预取(首屏不下载、首开也不必等):工作区挂载 delayMs 之后、浏览器空闲时才拉;
// 省流量模式与 2G 不预取(首开时照常按需拉)。返回清理函数,供 useEffect 直接返回。
export function prefetchReaderMarkdownWhenIdle(delayMs = 5000) {
  if (typeof window === 'undefined' || view) return () => {};
  const connection = typeof navigator !== 'undefined' ? navigator.connection : null;
  if (connection && (connection.saveData || /(^|-)2g$/.test(connection.effectiveType || ''))) return () => {};
  let idle = null;
  const timer = window.setTimeout(() => {
    const run = () => { loadReaderMarkdownView().catch(() => {}); };
    if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(run, { timeout: 5000 });
    else run();
  }, delayMs);
  return () => {
    window.clearTimeout(timer);
    if (idle != null && typeof window.cancelIdleCallback === 'function') window.cancelIdleCallback(idle);
  };
}
