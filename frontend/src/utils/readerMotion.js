// 桌面读者面的动效编排(issue #301,conventions §7「桌面读者面的动效语言」)。
// 这里只放 JS 编排的那一部分(WAAPI);悬停、按下这类状态过渡在 index.css 文件尾「动效语言 · 桌面读者面」一段。
//
// 三条通用规矩都收在 pointerMotion() 里:
//   键盘触发的一律不动(回车 / 空格激活条目行、导轨钮时内容直接换);
//   减少动效时去掉位移与缩放,只留不超过 200ms 的明度淡入;
//   没有 WAAPI 的环境直接落终态。
// 取值是 index.css 里那套 token 的 JS 镜像(WAAPI 不读 CSS 变量),守卫 test/motionTokens.test.mjs 逐项比对。
import { motionReduced } from '../motion.js';
import { lastInputWasKeyboard } from './inputModality.js';
import { motionSpring } from './spring.js';

export const MOTION = Object.freeze({
  ease: 'cubic-bezier(0.22, 1, 0.36, 1)',        // --motion-ease
  easeDrawer: 'cubic-bezier(0.32, 0.72, 0, 1)',  // --motion-ease-drawer
  easeFocus: 'cubic-bezier(0.2, 0.8, 0.2, 1)',   // --motion-ease-focus
  press: 120,     // --motion-press
  hoverIn: 100,   // --motion-hover-in
  hoverOut: 180,  // --motion-hover-out
  fast: 150,      // --motion-fast
  base: 200,      // --motion-base
  drawer: 360,    // --motion-drawer
});

/** 这一次交互该怎么动:'full'(指针触发、完整)/ 'fade'(减少动效:只淡入)/ 'none'(键盘触发或环境不支持)。 */
export function pointerMotion() {
  if (typeof Element === 'undefined' || !Element.prototype.animate) return 'none';
  if (lastInputWasKeyboard()) return 'none';
  return motionReduced() ? 'fade' : 'full';
}

const animate = (el, keyframes, options) => (el && el.animate ? el.animate(keyframes, { fill: 'backwards', ...options }) : null);

/** 明度淡入(线性)。延迟期间垫首帧,结束后回到样式表的值。 */
export function fadeIn(el, duration = 120, delay = 0) {
  return animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration, delay, easing: 'linear' });
}

/** 淡入 + 上移 dist(默认 6px):标题区这类「主角」用。 */
export function riseIn(el, { duration = 160, dist = 6, delay = 0 } = {}) {
  return animate(el, [{ opacity: 0, transform: `translateY(${dist}px)` }, { opacity: 1, transform: 'none' }], { duration, delay, easing: MOTION.ease });
}

/** 一档弹簧的 WAAPI 动画:springTo(el, keyframes, 'pop')。 */
export function springTo(el, keyframes, name, extra = {}) {
  const s = motionSpring(name);
  return animate(el, keyframes, { duration: s.duration, easing: s.easing, ...extra });
}

/**
 * 打开一篇文章(精密):标题区是主角——淡入 + 上移 6px(160ms);其余(速读卡、正文、页边)一起淡入 120ms,不各自进场。
 * 只动这一刻已经在阅读窗里的块;正文晚到时直接换上,不再补一次入场。
 */
export function enterPane(pane) {
  const mode = pointerMotion();
  if (mode === 'none' || !pane) return;
  const doc = pane.querySelector('.reader-doc') || pane;
  for (const child of doc.children) {
    if (mode === 'full' && child.matches('.reader-pane-head')) riseIn(child);
    else fadeIn(child, 120);
  }
  for (const side of pane.querySelectorAll(':scope > :not(.reader-doc)')) fadeIn(side, 120);
}

/** 切视图 / 返回(精密):内容直接换,工作区 100ms 淡入。els 是导轨之外的各栏。 */
export function enterStage(els) {
  if (pointerMotion() === 'none') return;
  for (const el of els) fadeIn(el, MOTION.hoverIn);
}
