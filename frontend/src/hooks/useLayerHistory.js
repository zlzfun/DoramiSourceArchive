import { useEffect, useRef } from 'react';

// ── 移动层 × 浏览器返回键握手(移动波 Wave3) ──
// 手机(尤其微信内浏览器)的「返回」是肌肉记忆级操作:层(正文页/发现页/抽屉/动作单/
// 设置栈)开着时按返回,期望是关层,而不是整站退出。做法:层打开时 push 一条同 URL
// 历史,返回键触发 popstate 即关最上层;程序性关层(点关闭钮/遮罩)则消费掉那条
// 历史(history.back),前进/后退栈不堆积。
//
// 多层并存靠模块级栈协调:popstate 只关**最后打开**的层(单一全局监听,逐层 hook
// 实例登记/注销);程序性关层若不在栈顶(极端时序)按中间移除处理,深度容差不崩。
// 与 App 的 hash 路由共存:pushState 原样携带既有 state 与 URL,App 的 popstate
// 处理器按 state.tab 回放同一路由,互不干扰;桌面不挂本 hook,零波及。
const layerStack = [];       // 打开序的层登记(尾=栈顶)
let suppressNextPop = 0;     // 程序性 history.back() 的自触发 popstate 计数
let listening = false;
const pendingUnmounts = new Map();

function retireUnmountedLayers() {
  let count = 0;
  for (const [entry, mounted] of pendingUnmounts) {
    if (mounted.current) continue; // StrictMode 的重新挂载仍复用这条历史
    const index = layerStack.indexOf(entry);
    if (index < 0) continue; // 已经由返回键消费
    layerStack.splice(index, 1);
    count += 1;
  }
  pendingUnmounts.clear();
  if (count) {
    suppressNextPop += 1;
    try { window.history.go(-count); } catch { suppressNextPop -= 1; }
  }
}

function ensureListener() {
  if (listening || typeof window === 'undefined') return;
  listening = true;
  window.addEventListener('popstate', () => {
    if (suppressNextPop > 0) {
      suppressNextPop -= 1;
      return;
    }
    const top = layerStack.pop();
    if (top) top.close();
  });
}

export function useLayerHistory(open, onClose) {
  const entryRef = useRef(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });

  useEffect(() => {
    ensureListener();
    if (open && !entryRef.current) {
      const entry = { close: () => closeRef.current?.() };
      layerStack.push(entry);
      entryRef.current = entry;
      try {
        window.history.pushState({ ...(window.history.state || {}), mLayer: layerStack.length }, '', window.location.href);
      } catch { /* 沙箱环境禁用 history,层照常工作,只失去返回键关层 */ }
    } else if (!open && entryRef.current) {
      const entry = entryRef.current;
      entryRef.current = null;
      const idx = layerStack.indexOf(entry);
      if (idx >= 0) {
        // 程序性关层:移出登记并消费我们那条历史(自触发的 popstate 由计数吞掉)
        layerStack.splice(idx, 1);
        suppressNextPop += 1;
        try { window.history.back(); } catch { suppressNextPop -= 1; }
      }
      // idx < 0:该层由返回键路径关闭(popstate 已弹栈),历史无需再动
    }
  }, [open]);

  // 版式切换会卸载多个层；合并回退一次，避免留下空的返回步骤。
  // 延迟到当前 commit 后，StrictMode 的清理/重挂载不应删除仍在使用的历史。
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const entry = entryRef.current;
      if (!entry) return;
      if (pendingUnmounts.size === 0) queueMicrotask(retireUnmountedLayers);
      pendingUnmounts.set(entry, mountedRef);
    };
  }, []);
}
