// ── 层 × 浏览器返回键握手的核心(移动波 Wave3 起;issue #230 抽成纯模块以便单测) ──
// 手机(尤其微信内浏览器)的「返回」是肌肉记忆级操作:层开着时按返回,期望是关层,而不是整站退出。
// 做法:层打开时 push 一条同 URL 历史,返回键触发 popstate 即关最上层;程序性关层(点关闭钮 / 遮罩 / Esc)
// 则消费掉那条历史(history.back),前进 / 后退栈不堆积。
//
// 多层并存靠一个栈协调:popstate 只关**最后打开**的层;程序性关层若不在栈顶(极端时序)按中间移除处理。
// 历史条目都是同一 URL,能对上的只是「条数」——back 与 push 在同一次提交里谁先谁后都不影响净深度。
// 与 App 的 hash 路由共存:pushState 原样携带既有 state 与 URL,App 的 popstate 处理器按 state.tab
// 回放同一路由,互不干扰。
//
// createLayerHistory(win) 不依赖 React:hooks/useLayerHistory.js 绑定真实 window 的单例,
// test/layerPrimitives.test.mjs 用假 window 走 打开 / 返回键 / 程序性关闭 / 卸载合并 各路径。
export function createLayerHistory(win) {
  const stack = [];          // 打开序的层登记(尾 = 栈顶)
  let suppress = 0;          // 程序性 back / go 自触发 popstate 的计数
  let listening = false;
  const pendingUnmounts = new Map();

  const ensureListener = () => {
    if (listening || !win) return;
    listening = true;
    win.addEventListener('popstate', () => {
      if (suppress > 0) {
        suppress -= 1;
        return;
      }
      const top = stack.pop();
      if (top) top.close();
    });
  };

  const go = (delta) => {
    suppress += 1;
    try {
      if (delta === -1) win.history.back();
      else win.history.go(delta);
    } catch {
      suppress -= 1;
    }
  };

  const flushUnmounts = () => {
    let count = 0;
    for (const [entry, mounted] of pendingUnmounts) {
      if (mounted.current) continue;
      const index = stack.indexOf(entry);
      if (index < 0) continue; // 已经由返回键消费
      stack.splice(index, 1);
      count += 1;
    }
    pendingUnmounts.clear();
    if (count) go(-count);
    return count;
  };

  return {
    // 层打开:登记并 push 一条历史;返回登记项(关闭 / 卸载时交回)
    open(close) {
      ensureListener();
      const entry = { close };
      stack.push(entry);
      try {
        win.history.pushState({ ...(win.history.state || {}), mLayer: stack.length }, '', win.location.href);
      } catch { /* 沙箱环境禁用 history:层照常工作,只失去返回键关层 */ }
      return entry;
    },
    // 程序性关层:移出登记并消费那条历史(自触发的 popstate 由计数吞掉);
    // 已由返回键弹栈的登记项(不在栈里)不再动历史
    close(entry) {
      const index = stack.indexOf(entry);
      if (index < 0) return false;
      stack.splice(index, 1);
      go(-1);
      return true;
    },
    // 卸载:同一次提交里卸载的多层合并成一次回退(版式切换会一并卸载几层),
    // 延到提交之后再判,StrictMode 的清理 / 重挂载不删仍在使用的历史(mounted.current 又变回 true)
    retireOnUnmount(entry, mounted, schedule = queueMicrotask) {
      if (pendingUnmounts.size === 0) schedule(flushUnmounts);
      pendingUnmounts.set(entry, mounted);
    },
    flushUnmounts,
    depth: () => stack.length,
  };
}

let shared = null;
export function sharedLayerHistory() {
  if (!shared && typeof window !== 'undefined') shared = createLayerHistory(window);
  return shared;
}
