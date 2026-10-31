import { motionReduced } from '../motion.js';

// 正文入栈 = 容器展开(issue #230 批 3c;样页拍板 8、动效清单「容器展开」):正文页从被点的那张卡片的矩形长出来
// (clip-path 弹簧 420ms + 90ms 后正文淡入上移),返回时收回原卡(320ms);找不到卡片矩形(深链、播放条、卡片已不在屏上)
// 时退回普通推入 / 退出(上移淡入 320ms / 下移淡出 240ms)。减少动效(<html data-motion="reduce">)时不播任何动效。
//
// 「来处」靠一个捕获阶段的 click 监听记下:点到的元素往上找卡片(条目卡 / 早报条目与头条 / 榜单行,或任何标了
// data-read-origin 的元素),记它当时的矩形;正文页挂载时取走(4 秒内有效——早报 / 榜单要先按 id 拉文章)。
// 收回时卡片还在屏上(内容列表在正文页下面一直挂着)就收回它的矩形,否则退回普通退出。
//
// 退出用「替身」:React 卸载正文页之前克隆一份(去掉 id / 角色 / 焦点位,inert + aria-hidden,不接点击),
// 原地盖住,再让替身收回卡片后移除——状态当场就切回列表(返回键、焦点归还、位置恢复都不等动画)。
// 替身里的滚动位置用子元素平移冻结(不给替身设 scrollTop,免得派发滚动事件被阅读位置记录当成真的滚动)。

export const READ_ORIGIN_SELECTOR = '[data-read-origin], .reader-entry, .bp-item, .bp-head, .ranking-content-row';
export const READ_ORIGIN_MAX_AGE = 4000;
// 露出部分小于这个边长就不算可见的来处(卡片滚出屏外 / 被裁成一条线)
const MIN_SIDE = 12;
const FALLBACK_RADIUS = 14;
export const FULL_INSET = 'inset(0px 0px 0px 0px round 0px)';
export const READ_MOTION = {
  enter: 420,          // clip-path 展开(--motion-sheet 弹簧)
  bodyDelay: 90,       // 正文淡入上移的延迟
  body: 300,
  exit: 320,           // 收回原卡
  exitEase: 'cubic-bezier(0.32, 0.72, 0, 1)',
  pushIn: 320,         // 没有来处:上移 16px 淡入
  pushOut: 240,        // 没有来处:下移 24px 淡出
  ease: 'cubic-bezier(0.22, 1, 0.36, 1)',
};

let lastOrigin = null;

// 记下来处:target 往上找卡片;找不到不覆盖上一次的记录(点卡片里的收藏星之类不该清掉来处,也不会新建来处)
export function rememberReadOrigin(target, now = Date.now()) {
  const el = target && typeof target.closest === 'function' ? target.closest(READ_ORIGIN_SELECTOR) : null;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  lastOrigin = { el, rect: { top: r.top, left: r.left, right: r.right, bottom: r.bottom }, at: now };
  return lastOrigin;
}

// 取走来处(一次性):过期(> maxAge)当没有
export function takeReadOrigin(now = Date.now(), maxAge = READ_ORIGIN_MAX_AGE) {
  const origin = lastOrigin;
  lastOrigin = null;
  if (!origin || now - origin.at > maxAge) return null;
  return origin;
}

// 捕获阶段记来处(幂等;键盘 Enter 激活条目也会派发 click)
export function installReadOriginTracker(doc = typeof document === 'undefined' ? null : document) {
  if (!doc || doc.__doramiReadOrigin) return;
  doc.__doramiReadOrigin = true;
  doc.addEventListener('click', (event) => { rememberReadOrigin(event.target); }, true);
}

const round = (value) => Math.round(value * 10) / 10;

// 卡片矩形 → 相对层框(frame = 正文页的矩形,满屏)的 clip-path inset;露出部分太小或完全在框外时返回 null
export function clipInset(rect, frame) {
  if (!rect || !frame) return null;
  const right = frame.left + frame.width;
  const bottom = frame.top + frame.height;
  const visibleW = Math.min(rect.right, right) - Math.max(rect.left, frame.left);
  const visibleH = Math.min(rect.bottom, bottom) - Math.max(rect.top, frame.top);
  if (!(visibleW >= MIN_SIDE) || !(visibleH >= MIN_SIDE)) return null;
  const clampX = (v) => round(Math.min(Math.max(v, 0), frame.width));
  const clampY = (v) => round(Math.min(Math.max(v, 0), frame.height));
  return {
    top: clampY(rect.top - frame.top),
    right: clampX(right - rect.right),
    bottom: clampY(bottom - rect.bottom),
    left: clampX(rect.left - frame.left),
  };
}

export function insetPath(inset, radius = 0) {
  return `inset(${inset.top}px ${inset.right}px ${inset.bottom}px ${inset.left}px round ${round(radius)}px)`;
}

function originRadius(el) {
  try {
    const value = parseFloat(getComputedStyle(el).borderTopLeftRadius);
    return value > 0 ? value : FALLBACK_RADIUS;
  } catch {
    return FALLBACK_RADIUS;
  }
}

function liveRect(el) {
  if (!el || !el.isConnected) return null;
  const r = el.getBoundingClientRect();
  return r.width && r.height ? r : null;
}

// 弹簧缓动读 token(不支持 linear() 时 index.css 已把 --motion-sheet-ease 退成三次贝塞尔)
function sheetEase() {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue('--motion-sheet-ease').trim() || READ_MOTION.ease;
  } catch {
    return READ_MOTION.ease;
  }
}

function safeAnimate(el, frames, options) {
  if (!el || typeof el.animate !== 'function') return null;
  try {
    return el.animate(frames, options);
  } catch {
    return null;   // 缓动串不认识等:不播,落终态
  }
}

// 入栈:返回启动的动画(调用方在卸载 / 重跑时 cancel)
export function playReadEnter(panel, origin) {
  if (!panel || motionReduced()) return [];
  const frame = panel.getBoundingClientRect();
  const inset = origin ? clipInset(origin.rect, frame) : null;
  const ease = sheetEase();
  if (!inset) {
    return [safeAnimate(panel, [
      { opacity: 0, transform: 'translateY(16px)' },
      { opacity: 1, transform: 'none' },
    ], { duration: READ_MOTION.pushIn, easing: ease })].filter(Boolean);
  }
  const scroller = panel.querySelector('.m-read-scroll');
  return [
    safeAnimate(panel, [
      { clipPath: insetPath(inset, originRadius(origin.el)) },
      { clipPath: FULL_INSET },
    ], { duration: READ_MOTION.enter, easing: ease }),
    safeAnimate(scroller, [
      { opacity: 0, transform: 'translateY(12px)' },
      { opacity: 1, transform: 'none' },
    ], { duration: READ_MOTION.body, delay: READ_MOTION.bodyDelay, easing: READ_MOTION.ease, fill: 'backwards' }),
  ].filter(Boolean);
}

function makeGhost(panel) {
  const ghost = panel.cloneNode(true);
  for (const attr of ['id', 'role', 'aria-label', 'aria-modal', 'tabindex', 'data-layer-panel']) ghost.removeAttribute(attr);
  ghost.setAttribute('aria-hidden', 'true');
  ghost.setAttribute('inert', '');
  ghost.classList.add('is-ghost');
  ghost.style.pointerEvents = 'none';
  ghost.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
  // 内嵌媒体不复制(免得重新加载 / 出声),换成同高的空块
  const media = panel.querySelectorAll('iframe, video, audio');
  ghost.querySelectorAll('iframe, video, audio').forEach((node, i) => {
    const box = document.createElement('div');
    box.style.height = `${media[i]?.offsetHeight || 0}px`;
    node.replaceWith(box);
  });
  // 冻结滚动位置:子元素整体上移 scrollTop,滚动区不滚
  const scrollers = panel.querySelectorAll('.m-read-scroll');
  ghost.querySelectorAll('.m-read-scroll').forEach((node, i) => {
    const top = scrollers[i]?.scrollTop || 0;
    node.style.overflow = 'hidden';
    if (top) for (const child of node.children) child.style.transform = `translateY(${-top}px)`;
  });
  return ghost;
}

// 出栈:在调用方让 React 卸载正文页之前调用;返回替身(测试 / 调试用),不播时返回 null
export function playReadExit(panel, origin) {
  if (!panel || !panel.parentNode || motionReduced() || typeof panel.animate !== 'function') return null;
  const ghost = makeGhost(panel);
  panel.parentNode.insertBefore(ghost, panel.nextSibling);
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    ghost.remove();
  };
  const safety = window.setTimeout(finish, 1200);   // 动画没起来 / 被打断时兜底移除
  // 等 React 提交关闭(列表解除 inert、位置恢复)之后再量卡片
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const frame = ghost.getBoundingClientRect();
    const rect = origin ? liveRect(origin.el) : null;
    const inset = rect ? clipInset(rect, frame) : null;
    const anim = inset
      ? safeAnimate(ghost, [
        { clipPath: FULL_INSET, opacity: 1 },
        { clipPath: insetPath(inset, originRadius(origin.el)), opacity: 0.2 },
      ], { duration: READ_MOTION.exit, easing: READ_MOTION.exitEase, fill: 'forwards' })
      : safeAnimate(ghost, [
        { opacity: 1, transform: 'none' },
        { opacity: 0, transform: 'translateY(24px)' },
      ], { duration: READ_MOTION.pushOut, easing: READ_MOTION.ease, fill: 'forwards' });
    if (!anim) { window.clearTimeout(safety); finish(); return; }
    const end = () => { window.clearTimeout(safety); finish(); };
    anim.onfinish = end;
    anim.oncancel = end;
  }));
  return ghost;
}
