// 空间转场里不走 View Transitions 的几件(issue #301 第四批,conventions §7「连续」):
//   早报到场的三拍、早报换版、订阅成功时徽标飞到导轨、标题滚动停靠的脚本退路。
// 榜单卡成页、浮层变来源栏、来源栏收起在 readerMotion.viewTransit / slideSources 那边。
import { motionReduced } from '../motion.js';
import { MOTION, fadeIn, motionIntent, pointerMotion, springTo } from './readerMotion.js';
import { motionSpring } from './spring.js';

// ── 早报到场(入场清单之一):三拍,间隔递减;每拍上升 16px ──
// 「到场」只有一次:页面载入(或登录抵达)后早报第一次有内容的那一刻。之后在视图之间来回、换日期都不算
// ——那是切视图,按精密的换法。载入时落在别的视图、或早报还没出内容人就走开了,这一次也就作废。
let arrival = true;
let waiting = false;              // 正在等品牌抵达幕走完
const CURTAIN_WAIT_MS = 6000;     // 幕布迟迟不走的兜底
const ARRIVAL_LAST_BEAT_MS = 150; // 第三拍起步的时刻
const arriving = new WeakSet();   // 三拍还在演的那张版面:这期间换版不再叠一层入场

/** 这次载入不会再有「到场」了(落地不在早报,或早报出内容之前已经切走)。 */
export function endArrival() {
  arrival = false;
}

/**
 * 早报第一次有内容时调用。sheet 是 .bp-sheet;rail 是导轨(一起淡入)。
 * 到场不是哪一下点击触发的,不看输入方式;减少动效时各拍只淡入、不上升。
 */
export function enterBrief(sheet, rail) {
  if (!arrival || !sheet || waiting) return;
  if (typeof Element === 'undefined' || !Element.prototype.animate) { arrival = false; return; }
  // 登录抵达时品牌的抵达幕(.app-arrive)与主界面显影(.app-arriving)还在场:等它们走完再播,否则前几拍被幕布挡住、后半段与显影叠在一起。
  // 一次性标记到真正开播时才消费——等待期间人切走了(endArrival),这次也就不播了。
  const curtain = () => document.querySelector('.app-arrive, .app-arriving');
  if (curtain()) {
    waiting = true;
    const go = () => { observer.disconnect(); clearTimeout(timer); waiting = false; enterBrief(sheet, rail); };
    const observer = new MutationObserver(() => { if (!curtain()) go(); });
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    const timer = setTimeout(go, CURTAIN_WAIT_MS);
    return;
  }
  if (!sheet.isConnected) { arrival = false; return; }
  arrival = false;
  const reduced = motionReduced();
  const settle = motionSpring('settle');
  arriving.add(sheet);
  setTimeout(() => arriving.delete(sheet), ARRIVAL_LAST_BEAT_MS + (reduced ? 160 : settle.duration));
  const pick = (selector) => sheet.querySelector(selector);
  const beats = [
    [0, [pick('.bp-top'), pick('.bp-kicker'), pick('.bp-title'), pick('.bp-orig'), pick('.bp-lower-main'), pick('.bp-cue')]],
    [90, [pick('.bp-verdict')]],
    [ARRIVAL_LAST_BEAT_MS, [pick('.bp-toc'), ...[...sheet.querySelectorAll('.bp-act')].slice(0, 4)]],
  ];
  for (const [delay, els] of beats) {
    for (const el of els) {
      if (!el) continue;
      if (reduced) fadeIn(el, 160, delay);
      else el.animate([{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }], { duration: settle.duration, easing: settle.easing, delay, fill: 'backwards' });
    }
  }
  fadeIn(rail, reduced ? 160 : 240);
  const light = pick('.bp-light');
  if (light && !reduced) light.animate([{ opacity: 0 }, { opacity: getComputedStyle(light).opacity }], { duration: 600, easing: 'linear' });
}

// ── 早报换版(页内换内容,不是入场编排):旧版面淡出,新版面淡入并上移 12px ──
// 今天这一版由系统自己换(issue #343:新内容落地、读者改了兴趣或订阅、失败后重试),不是哪一下点击的结果——
// 硬切会让人以为页面跳了,所以先让旧版面淡出(150ms)再换;新版面按「页内换内容」的换法上来(move 弹簧,单独一步、不分拍)。
// 页头那一行(日期、编排时刻、往期)不动,它是这一页的框。不看输入方式;减少动效时不演淡出,新版面只淡入。
const SWAP_DIST = 12;
const canAnimate = () => typeof Element !== 'undefined' && !!Element.prototype.animate;
const swapTargets = (sheet) => [
  ...sheet.querySelectorAll(':scope > .bp-poster > .bp-wrap > :not(.bp-top):not(.brief-onb):not(.bp-notice)'),
  sheet.querySelector(':scope > .bp-wrap'),
].filter(Boolean);

/**
 * 换版的前一半:旧版面淡出。返回 { animations, duration }——调用方等 duration 毫秒后换内容,
 * 并在换上的那次提交里把 animations 撤掉(留在场的节点带着 forwards 的终帧)。不必演时 duration 为 0。
 */
export function leaveBrief(sheet) {
  const still = { animations: [], duration: 0 };
  if (!sheet || !canAnimate() || motionReduced()) return still;
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return still;
  const animations = swapTargets(sheet).map((el) => el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: MOTION.fast, easing: 'linear', fill: 'forwards' }));
  return animations.length ? { animations, duration: MOTION.fast } : still;
}

/** 换版的后一半:新版面换上的那次提交里调用。「到场」的三拍还在演(或在等幕布)时不叠第二层。 */
export function swapInBrief(sheet) {
  if (!sheet || !canAnimate() || waiting || arriving.has(sheet)) return;
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
  const reduced = motionReduced();
  for (const el of swapTargets(sheet)) {
    if (reduced) fadeIn(el, 160);
    else springTo(el, [{ opacity: 0, transform: `translateY(${SWAP_DIST}px)` }, { opacity: 1, transform: 'none' }], 'move');
  }
}

// ── 订阅一个来源(入场清单之一):徽标沿弧线飞到导轨上对应形态的图标,落点弹一下;订阅钮自己也弹一下 ──
// 与发现页组件解耦:捕获阶段记下「点了哪张卡的订阅钮」,等那颗钮真的翻过来(请求成功)才演;失败、游客被拦都不演。
// 订阅:钮弹一下 + 徽标飞;退订:只有钮弹一下。
const RAIL_LABEL = { article: '文章', podcast: '播客', social: '社交媒体' };
const FLY_MS = 520;
const FLY_WAIT_MS = 8000;

function flyLogo(shell, card) {
  const logo = card.querySelector('.reader-disc-card-main > :first-child');
  const target = shell.querySelector(`.reader-vrail button[aria-label="${RAIL_LABEL[card.dataset.shape] || RAIL_LABEL.article}"] > svg`);
  if (!logo || !target) return;
  const from = logo.getBoundingClientRect();
  const to = target.getBoundingClientRect();
  if (!from.width || from.bottom < 0 || from.top > window.innerHeight) return;
  const ghost = logo.cloneNode(true);
  ghost.setAttribute('aria-hidden', 'true');
  Object.assign(ghost.style, { position: 'fixed', left: '0', top: '0', width: `${from.width}px`, height: `${from.height}px`, margin: '0', zIndex: '120', pointerEvents: 'none', willChange: 'transform' });
  document.body.appendChild(ghost);
  const x0 = from.left;
  const y0 = from.top;
  const x2 = to.left + to.width / 2 - from.width / 2;
  const y2 = to.top + to.height / 2 - from.height / 2;
  const x1 = (x0 + x2) / 2;
  const y1 = Math.min(y0, y2) - Math.min(140, Math.hypot(x2 - x0, y2 - y0) * 0.22);   // 控制点在两端上方:走一条上拱的弧
  const steps = 30;
  const keyframes = [];
  for (let i = 0; i <= steps; i += 1) {
    const k = i / steps;
    const e = k < 0.5 ? 4 * k * k * k : 1 - ((-2 * k + 2) ** 3) / 2;   // 两头慢中间快
    const x = (1 - e) * (1 - e) * x0 + 2 * (1 - e) * e * x1 + e * e * x2;
    const y = (1 - e) * (1 - e) * y0 + 2 * (1 - e) * e * y1 + e * e * y2;
    keyframes.push({ transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${(1 - 0.45 * e).toFixed(3)})`, opacity: k > 0.9 ? (1 - k) * 10 : 1 });
  }
  const drop = () => ghost.remove();
  ghost.animate(keyframes, { duration: FLY_MS, easing: 'linear', fill: 'forwards' }).finished.then(() => {
    drop();
    // 落点:导轨图标弹一下。图标自身带着上移 7px 的 transform,这里只动独立的 scale 属性
    if (target.isConnected) springTo(target, [{ scale: '1.32' }, { scale: '1' }], 'pop');
  }, drop);
}

export function mountSubscribeFly(shell) {
  if (!shell || typeof MutationObserver === 'undefined') return () => {};
  const pending = new Map();   // card → { observer, timer }:每张卡各等各的,连着订阅几个来源各有各的反馈
  const clear = (card) => {
    const job = pending.get(card);
    if (!job) return;
    job.observer.disconnect();
    clearTimeout(job.timer);
    pending.delete(card);
  };
  const onClick = (event) => {
    const btn = event.target.closest?.('.reader-disc-card .reader-disc-sub');
    if (!btn || !shell.contains(btn)) return;
    const was = btn.classList.contains('is-subbed');   // 点之前是不是已订阅:等它翻过来(订阅成功 / 退订成功)
    const card = btn.closest('.reader-disc-card');
    clear(card);
    const intent = motionIntent();
    const observer = new MutationObserver(() => {
      if (!card.isConnected) { clear(card); return; }
      const now = card.querySelector('.reader-disc-sub');
      if (!now || now.classList.contains('is-subbed') === was || now.querySelector('.animate-spin')) return;
      clear(card);
      const mode = pointerMotion(intent);
      if (mode === 'none') return;
      if (mode === 'fade') { fadeIn(now, 120); return; }
      springTo(now, [{ transform: 'scale(0.9)', opacity: 0.4 }, { transform: 'scale(1)', opacity: 1 }], 'pop');
      if (!was) flyLogo(shell, card);   // 退订只有钮自己弹一下,没有东西飞
    });
    observer.observe(card, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    pending.set(card, { observer, timer: setTimeout(() => clear(card), FLY_WAIT_MS) });
  };
  shell.addEventListener('click', onClick, true);
  return () => {
    shell.removeEventListener('click', onClick, true);
    for (const card of [...pending.keys()]) clear(card);
  };
}

// ── 标题滚动停靠的脚本退路 ──
// 支持滚动驱动动画的浏览器(Chrome / Edge 116 起)由样式表里的 view-timeline 管,这里什么都不挂;
// 不支持的,按滚动位置直接写吸顶栏小标题的明度与位移(可逆、无时间轴)。
export function mountTitleDock(shell) {
  if (!shell || typeof CSS === 'undefined' || !CSS.supports) return () => {};
  if (CSS.supports('animation-timeline: view()') && CSS.supports('timeline-scope: --x')) return () => {};
  const onScroll = (event) => {
    const pane = event.target;
    if (!(pane instanceof Element) || !pane.matches('.reader-pane')) return;
    const title = pane.querySelector('.reader-pane-title');
    const docked = pane.parentElement?.querySelector('.reader-bar-title');
    if (!title || !docked) return;
    const top = pane.getBoundingClientRect().top;
    const box = title.getBoundingClientRect();
    const progress = Math.max(0, Math.min(1, (top - box.top) / Math.max(1, box.height)));
    docked.style.opacity = progress.toFixed(3);
    docked.style.transform = motionReduced() ? 'none' : `translateY(${((1 - progress) * 12).toFixed(1)}px)`;
  };
  // 换了一篇(阅读窗按文章 id 重挂载,从顶上开始,不会自己发 scroll):吸顶栏的小标题是沿用的那一个,把上一篇留下的显现状态复位
  const observer = typeof MutationObserver === 'undefined' ? null : new MutationObserver((records) => {
    for (const m of records) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1 || !node.matches('.reader-pane')) continue;
        const docked = node.parentElement?.querySelector('.reader-bar-title');
        if (docked) { docked.style.opacity = '0'; docked.style.transform = ''; }
      }
    }
  });
  observer?.observe(shell, { subtree: true, childList: true });
  shell.addEventListener('scroll', onScroll, true);
  return () => {
    shell.removeEventListener('scroll', onScroll, true);
    observer?.disconnect();
  };
}
