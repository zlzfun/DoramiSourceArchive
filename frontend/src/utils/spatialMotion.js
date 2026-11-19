// 空间转场里不走 View Transitions 的几件(issue #301 第四批,conventions §7「连续」):
//   早报到场的三拍、订阅成功时徽标飞到导轨、标题滚动停靠的脚本退路。
// 榜单卡成页、浮层变来源栏、来源栏收起在 readerMotion.viewTransit / slideSources 那边。
import { motionReduced } from '../motion.js';
import { fadeIn, motionIntent, pointerMotion, springTo } from './readerMotion.js';
import { motionSpring } from './spring.js';

// ── 早报到场(入场清单之一):三拍,间隔递减;每拍上升 16px ──
// 「到场」只有一次:页面载入(或登录抵达)后早报第一次有内容的那一刻。之后在视图之间来回、换日期都不算
// ——那是切视图,按精密的换法。载入时落在别的视图、或早报还没出内容人就走开了,这一次也就作废。
let arrival = true;

/** 这次载入不会再有「到场」了(落地不在早报,或早报出内容之前已经切走)。 */
export function endArrival() {
  arrival = false;
}

/**
 * 早报第一次有内容时调用。sheet 是 .bp-sheet;rail 是导轨(一起淡入)。
 * 到场不是哪一下点击触发的,不看输入方式;减少动效时各拍只淡入、不上升。
 */
export function enterBrief(sheet, rail) {
  if (!arrival || !sheet) return;
  arrival = false;
  if (typeof Element === 'undefined' || !Element.prototype.animate) return;
  const reduced = motionReduced();
  const settle = motionSpring('settle');
  const pick = (selector) => sheet.querySelector(selector);
  const beats = [
    [0, [pick('.bp-top'), pick('.bp-kicker'), pick('.bp-title'), pick('.bp-orig'), pick('.bp-lower-main'), pick('.bp-cue')]],
    [90, [pick('.bp-verdict')]],
    [150, [pick('.bp-toc'), ...[...sheet.querySelectorAll('.bp-act')].slice(0, 4)]],
  ];
  for (const [delay, els] of beats) {
    for (const el of els) {
      if (!el) continue;
      if (reduced) fadeIn(el, 160, delay);
      else el.animate([{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }], { duration: settle.duration, easing: settle.easing, delay, fill: 'backwards' });
    }
  }
  fadeIn(rail, 240);
  const light = pick('.bp-light');
  if (light && !reduced) light.animate([{ opacity: 0 }, { opacity: getComputedStyle(light).opacity }], { duration: 600, easing: 'linear' });
}

// ── 订阅一个来源(入场清单之一):徽标沿弧线飞到导轨上对应形态的图标,落点弹一下;订阅钮自己也弹一下 ──
// 与发现页组件解耦:捕获阶段记下「点了哪张卡的订阅钮」,等那颗钮真的变成「已订阅」(请求成功)才飞;失败、游客被拦都不飞。
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
  let pending = null;   // { card, intent, observer, timer }
  const clear = () => {
    if (!pending) return;
    pending.observer.disconnect();
    clearTimeout(pending.timer);
    pending = null;
  };
  const onClick = (event) => {
    const btn = event.target.closest?.('.reader-disc-card .reader-disc-sub');
    if (!btn || !shell.contains(btn) || btn.classList.contains('is-subbed')) return;
    clear();
    const card = btn.closest('.reader-disc-card');
    const intent = motionIntent();
    const observer = new MutationObserver(() => {
      const now = card.querySelector('.reader-disc-sub');
      if (!now || !now.classList.contains('is-subbed') || now.querySelector('.animate-spin')) return;
      clear();
      const mode = pointerMotion(intent);
      if (mode === 'none') return;
      if (mode === 'fade') { fadeIn(now, 120); return; }
      springTo(now, [{ transform: 'scale(0.9)', opacity: 0.4 }, { transform: 'scale(1)', opacity: 1 }], 'pop');
      flyLogo(shell, card);
    });
    observer.observe(card, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    pending = { card, intent, observer, timer: setTimeout(clear, FLY_WAIT_MS) };
  };
  shell.addEventListener('click', onClick, true);
  return () => {
    shell.removeEventListener('click', onClick, true);
    clear();
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
  shell.addEventListener('scroll', onScroll, true);
  return () => shell.removeEventListener('scroll', onScroll, true);
}
