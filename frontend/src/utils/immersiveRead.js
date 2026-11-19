// 沉浸阅读(issue #301 第六批,conventions §7):正文往下读过一屏,导轨、来源栏、条目列退后到 30%;
// 指针碰到那三块、往回滚、按键、焦点进了那三块、换了一篇,立刻回来。
// 规矩(样页「在场与退场」):退场的只能是当前这件事用不到的东西;入口本身不藏——只降明度,不动版式、不拦点击;
// 悬停、键盘、往回滚三条路都能把它叫回来。减少动效时不退(样式表里兜底)。
// 与页面组件解耦:挂在桌面读者面的外壳上,自己听阅读窗的滚动(捕获阶段——scroll 不冒泡),状态写在外壳的 data-reading 上。

export const IMMERSIVE = Object.freeze({
  enterTop: 240,    // 至少读到这么远
  enterDown: 160,   // 且连续往下滚了这么多(顶上 wakeTop 以内不攒:从头往下读,实际在 280 上下进入)
  wakeTop: 120,     // 回到顶上附近:回来
  wakeUp: 24,       // 或往回滚了这么多:回来
});
const PANE = '.reader-pane';
const REGIONS = '.reader-vrail, .reader-col-sources, .reader-col-list';
// 用键盘往下读(焦点不在那三块里):这几个键只是滚动,回不回来由滚动方向管——否则每按一下空格三栏就亮一次
const SCROLL_KEYS = new Set([' ', 'Spacebar', 'PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', 'Home', 'End']);
const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'Fn']);

/** 在 shell(桌面读者面的 .reader-shell)上挂起沉浸阅读,返回卸载函数。 */
export function mountImmersiveRead(shell) {
  if (!shell || typeof window === 'undefined') return () => {};
  let pane = null;
  let lastTop = 0;
  let down = 0;
  let up = 0;
  const reading = () => shell.hasAttribute('data-reading');
  const wake = () => {
    down = 0;
    if (reading()) shell.removeAttribute('data-reading');
  };

  const onScroll = (event) => {
    const el = event.target;
    if (!el || typeof el.matches !== 'function' || !el.matches(PANE)) return;
    const top = el.scrollTop;
    if (el !== pane) {   // 换了一篇(阅读窗按文章重挂载):从头算
      pane = el; lastTop = top; down = 0; up = 0;
      wake();
      return;
    }
    const delta = top - lastTop;
    lastTop = top;
    if (delta > 0) { down += delta; up = 0; } else if (delta < 0) { up -= delta; down = 0; }
    if (top < IMMERSIVE.wakeTop || up > IMMERSIVE.wakeUp) wake();
    else if (top > IMMERSIVE.enterTop && down > IMMERSIVE.enterDown && !reading()) shell.setAttribute('data-reading', '');
  };
  const inRegions = (target) => Boolean(target && typeof target.closest === 'function' && target.closest(REGIONS));
  // 指针:真的动到了那三块上才算(pointerover 在内容自己重排到指针底下时也会发,不能当成人的意图)
  const onPointer = (event) => { if (reading() && inRegions(event.target)) wake(); };
  const onFocus = (event) => { if (reading() && inRegions(event.target)) wake(); };
  const onKey = (event) => {
    if (!reading() || MODIFIER_KEYS.has(event.key)) return;
    if (SCROLL_KEYS.has(event.key) && !inRegions(event.target)) return;
    wake();
  };
  // 那一篇不在了(换篇、切视图)而没有新的滚动:也回来
  const observer = typeof MutationObserver === 'undefined' ? null : new MutationObserver(() => {
    if (reading() && (!pane || !pane.isConnected)) wake();
  });
  observer?.observe(shell, { childList: true, subtree: true });

  shell.addEventListener('scroll', onScroll, true);
  shell.addEventListener('pointermove', onPointer, { capture: true, passive: true });
  shell.addEventListener('pointerdown', onPointer, { capture: true, passive: true });
  shell.addEventListener('focusin', onFocus, true);
  window.addEventListener('keydown', onKey, true);
  return () => {
    shell.removeEventListener('scroll', onScroll, true);
    shell.removeEventListener('pointermove', onPointer, { capture: true });
    shell.removeEventListener('pointerdown', onPointer, { capture: true });
    shell.removeEventListener('focusin', onFocus, true);
    window.removeEventListener('keydown', onKey, true);
    observer?.disconnect();
    shell.removeAttribute('data-reading');
  };
}
