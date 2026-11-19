// 桌面读者面的动效编排(issue #301,conventions §7「桌面读者面的动效语言」)。
// 这里只放 JS 编排的那一部分(WAAPI);悬停、按下这类状态过渡在 index.css 文件尾「动效语言 · 桌面读者面」一段。
//
// 三条通用规矩都收在 pointerMotion() 里:
//   键盘触发的一律不动(回车 / 空格激活条目行、导轨钮时内容直接换);
//   减少动效时去掉位移与缩放,只留不超过 200ms 的明度淡入;
//   没有 WAAPI 的环境直接落终态。
// 「怎么触发的」按触发那一刻算,不按动画开播那一刻算:同步路径两者是同一刻;请求回来才换内容的路径(从早报 / 榜单打开文章、
// 收藏)在入口处 motionIntent() / holdIntent() 记下,带到播放处——否则回车发起、等待时点了一下空白,回来就会被当成指针触发。
// 取值是 index.css 里那套 token 的 JS 镜像(WAAPI 不读 CSS 变量),守卫 test/motionTokens.test.mjs 逐项比对。
import { flushSync } from 'react-dom';
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

/** 此刻的触发方式:'key'(最近一次输入是键盘)/ 'pointer'。在交互入口调用,异步回来时把它传给编排函数。 */
export function motionIntent() {
  return lastInputWasKeyboard() ? 'key' : 'pointer';
}

// 挂起的触发方式:请求发出到内容换上之间,没有显式带 intent 的编排(阅读窗挂载、工作区淡入)都按它判。
// 放下的时机有三个:内容换上的那次提交(ReaderTab 在布局阶段放,同步、不与下一次挂起抢先后);请求失败或被盖过(按凭据放);
// 兜底是 10 秒过期——内容其实没换(打开的就是正在看的那篇)时不会有「换上」,不能让它一直压着后面的交互。
let held = null;
const HOLD_TTL = 10000;
const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
const heldHow = () => (held && nowMs() - held.at < HOLD_TTL ? held.how : null);

/** 入口处挂起触发方式,返回凭据;内容换上(或请求失败 / 被盖过)之后 releaseIntent。how 缺省取此刻的。 */
export function holdIntent(how = motionIntent()) {
  held = { how, at: nowMs() };
  return held;
}

/** 放下挂起的触发方式。带凭据时只在它仍是当前那一份时才放(被更晚的一次盖过就不动);不带则无条件放。 */
export function releaseIntent(token) {
  if (!token || held === token) held = null;
}

/**
 * 这一轮微任务跑完再放下此刻挂起的那一份。内容换上的那次提交用它:提交里的布局副作用已经按它判过了,
 * 但滑动指示器那一层是在提交之后的微任务里(MutationObserver)才判的,当场放下会让它看不到。
 */
export function releaseIntentSoon() {
  const token = held;
  if (token) queueMicrotask(() => { if (held === token) held = null; });
}

/** 这一刻该按哪种触发方式算:有挂起的就是它,否则是此刻的最近输入。要把它绑到「这篇文章」上、留给晚挂载的块用时取这个。 */
export function currentIntent() {
  return heldHow() || motionIntent();
}

/**
 * 这一次交互该怎么动:'full'(指针触发、完整)/ 'fade'(减少动效:只淡入)/ 'none'(键盘触发或环境不支持)。
 * intent 是入口处记下的触发方式;不给就看挂起的那一份,再没有才看此刻的最近输入。减少动效一律按播放这一刻的偏好。
 */
export function pointerMotion(intent) {
  if (typeof Element === 'undefined' || !Element.prototype.animate) return 'none';
  const how = intent || currentIntent();
  if (how === 'key') return 'none';
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

/** 收藏(精密):点亮时星标从 0.6 弹到 1(pop 弹簧);取消只闪一下明度。减少动效时两种都只走明度。 */
export function popStar(icon, on, intent) {
  const mode = pointerMotion(intent);
  if (mode === 'none' || !icon) return;
  if (on && mode === 'full') springTo(icon, [{ transform: 'scale(0.6)' }, { transform: 'scale(1)' }], 'pop');
  else animate(icon, [{ opacity: 0.35 }, { opacity: 1 }], { duration: 120, easing: 'linear' });
}

const CASCADE_STEP = 20;
const CASCADE_MAX = 8;   // 第 9 行起不再更晚:最多晚 160ms

/**
 * 全部标读(精密):看得见的未读行自上而下每行晚 20ms 换成已读,最多晚 160ms——是「这一下扫过去了」的因果,不是入场。
 * 这里只给每行写 --read-delay;换色本身是样式表里标题的 color 过渡(粗细在同一刻直接换)。返回撤掉这些变量的函数。
 */
export function armReadCascade(list) {
  if (!list || pointerMotion() !== 'full') return () => {};
  const box = list.getBoundingClientRect();
  const rows = [...list.querySelectorAll('.reader-entry:not(.is-read)')].filter((row) => {
    const r = row.getBoundingClientRect();
    return r.bottom > box.top && r.top < box.bottom;
  });
  rows.forEach((row, i) => row.style.setProperty('--read-delay', `${Math.min(i, CASCADE_MAX) * CASCADE_STEP}ms`));
  return () => rows.forEach((row) => row.style.removeProperty('--read-delay'));
}

/**
 * 明暗切换(精密):整页交叉淡化 180ms(View Transitions;时长在 index.css 的 .theme-fading 规则里)。
 * 键盘触发、浏览器不支持时直接换。toggle 必须同步把 <html data-theme> 改掉(theme.js 的 toggleTheme 如此)。
 */
export function themeFade(toggle) {
  if (pointerMotion() === 'none' || typeof document === 'undefined' || !document.startViewTransition) { toggle(); return; }
  const root = document.documentElement;
  root.classList.add('theme-fading');
  const done = () => root.classList.remove('theme-fading');
  try {
    document.startViewTransition(() => { flushSync(toggle); }).finished.then(done, done);
  } catch {
    done();
    toggle();
  }
}

/** 浮层的变形原点:对准触发钮的水平中心(夹在浮层两端各留 12px 之内),纵向取贴着触发钮的那条边。 */
export function popOrigin(pop, trigger) {
  if (!pop || !trigger || !trigger.isConnected) return '50% 0px';
  const b = pop.getBoundingClientRect();
  const t = trigger.getBoundingClientRect();
  const x = Math.round(Math.max(12, Math.min(b.width - 12, t.left + t.width / 2 - b.left)));
  return `${x}px ${t.top + t.height / 2 > b.top + b.height / 2 ? '100%' : '0px'}`;
}

/** 浮层入场(精密):对准触发钮 0.96 → 1,150ms。trigger 传 'keep' 时沿用样式表里的原点。返回原点,离场时用同一个。 */
export function enterPop(pop, trigger) {
  const origin = trigger === 'keep' ? getComputedStyle(pop).transformOrigin : popOrigin(pop, trigger);
  if (trigger !== 'keep') pop.style.transformOrigin = origin;
  const mode = pointerMotion();
  if (mode === 'full') animate(pop, [{ opacity: 0, transform: 'scale(0.96)' }, { opacity: 1, transform: 'none' }], { duration: MOTION.fast, easing: MOTION.ease });
  else if (mode === 'fade') fadeIn(pop, 120);
  return origin;
}

/**
 * 离场替身(精密:有入场就有离场,更短、原路返回):在节点被移出文档之前调用——克隆一份,等节点真的走了再把替身放回原位,
 * 100ms 淡出并缩到 0.98。键盘触发不留替身。节点其实没走(StrictMode 开发态多跑的那次清理)就什么都不做。
 * 起点取节点此刻的明度与变形:入场走到一半就被关掉时,替身从那一半接着退,不先跳到完整状态。
 */
export function exitGhost(el, origin) {
  const mode = pointerMotion();
  if (mode === 'none' || !el || !el.isConnected) return;
  const parent = el.parentNode;
  const next = el.nextSibling;
  const now = getComputedStyle(el);
  const from = { opacity: now.opacity, transform: now.transform === 'none' ? 'none' : now.transform };
  const ghost = el.cloneNode(true);
  for (const attr of ['id', 'role', 'aria-label', 'aria-modal', 'tabindex']) ghost.removeAttribute(attr);
  ghost.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
  ghost.setAttribute('aria-hidden', 'true');
  ghost.setAttribute('inert', '');
  Object.assign(ghost.style, { pointerEvents: 'none', animation: 'none', transformOrigin: origin || '' });
  // 滚动位置跟着替身走(来源下拉滚过之后关掉,不该跳回顶)
  const scrolled = [el, ...el.querySelectorAll('*')].map((node, i) => [i, node.scrollTop]).filter(([, top]) => top > 0);
  queueMicrotask(() => {
    if (el.isConnected || !parent.isConnected) return;
    parent.insertBefore(ghost, next && next.parentNode === parent ? next : null);
    const copies = [ghost, ...ghost.querySelectorAll('*')];
    for (const [i, top] of scrolled) if (copies[i]) copies[i].scrollTop = top;
    const keyframes = mode === 'full'
      ? [from, { opacity: 0, transform: 'scale(0.98)' }]
      : [{ opacity: from.opacity }, { opacity: 0 }];
    const drop = () => ghost.remove();
    const anim = ghost.animate(keyframes, { duration: 100, easing: MOTION.ease, fill: 'forwards' });
    anim.finished.then(drop, drop);
  });
}
