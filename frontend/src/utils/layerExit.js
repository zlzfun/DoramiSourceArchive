import { motionReduced } from '../motion.js';

// 推入页的退场(issue #230 批 4a;样页动效清单「推入页:420ms 弹簧 translateX(32px → 0)+ 240ms 淡入;返回反向」,
// 审计 M-33「退场与入场成对」;批 2 的设置页、批 3a 的推入页只有推入):
// React 卸载那一层之前克隆一份「替身」原地盖住,替身往右退 32px 并淡出,跑完移除——状态当场就切回(返回键、焦点归还、
// 列表位置都不等动画),与正文页收回原卡(utils/readTransition)同一种做法。
// 替身 inert + aria-hidden、不接点击,去掉 id / 角色 / 焦点位;滚动区的位置用子元素平移冻结(不给替身设 scrollTop,
// 免得派发滚动事件被当成真的滚动);内嵌媒体不复制。减少动效时不播。
//
// absolute:被替换的是层里的一块(设置分区页),替身按它在定位祖先里的矩形绝对定位,不挤进原来的 flex 流。
export const PUSH_EXIT = { duration: 240, distance: 32, easing: 'cubic-bezier(0.4, 0, 0.8, 0.2)' };
const SCROLLERS = '.m-page-scroll, .m-flow-scroll, .reader-social-scroll';

export function makeLayerGhost(panel, { scrollers = SCROLLERS, absolute = false } = {}) {
  const ghost = panel.cloneNode(true);
  for (const attr of ['id', 'role', 'aria-label', 'aria-modal', 'tabindex', 'data-layer-panel']) ghost.removeAttribute(attr);
  ghost.setAttribute('aria-hidden', 'true');
  ghost.setAttribute('inert', '');
  ghost.classList.add('is-ghost');
  ghost.style.pointerEvents = 'none';
  ghost.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
  ghost.querySelectorAll('iframe, video, audio').forEach((node) => node.remove());
  // 冻结滚动位置:面板自己与里面的滚动区,子元素整体上移 scrollTop,替身不滚
  const live = [panel, ...panel.querySelectorAll(scrollers)];
  const copies = [ghost, ...ghost.querySelectorAll(scrollers)];
  live.forEach((el, i) => {
    const top = el.scrollTop;
    const copy = copies[i];
    if (!copy || !top) return;
    copy.style.overflow = 'hidden';
    for (const child of copy.children) child.style.transform = `translateY(${-top}px)`;
  });
  if (absolute) {
    Object.assign(ghost.style, {
      position: 'absolute',
      top: `${panel.offsetTop}px`,
      left: `${panel.offsetLeft}px`,
      width: `${panel.offsetWidth}px`,
      height: `${panel.offsetHeight}px`,
      margin: '0',
      zIndex: '1',
    });
  }
  return ghost;
}

function animateGhost(ghost) {
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    ghost.remove();
  };
  const safety = window.setTimeout(finish, PUSH_EXIT.duration + 400);   // 动画没起来 / 被打断时兜底移除
  let anim;
  try {
    anim = ghost.animate([
      { transform: 'none', opacity: 1 },
      { transform: `translateX(${PUSH_EXIT.distance}px)`, opacity: 0 },
    ], { duration: PUSH_EXIT.duration, easing: PUSH_EXIT.easing, fill: 'forwards' });
  } catch {
    anim = null;
  }
  if (!anim) { window.clearTimeout(safety); finish(); return null; }
  const end = () => { window.clearTimeout(safety); finish(); };
  anim.onfinish = end;
  anim.oncancel = end;
  return ghost;
}

const canPlay = (panel) => Boolean(panel && panel.parentNode && panel.isConnected && !motionReduced() && typeof panel.animate === 'function');

// 在替换之前调用(层还在,马上要换成别的内容):立即放替身;返回替身,不播时返回 null
export function playPushExit(panel, options = {}) {
  if (!canPlay(panel)) return null;
  const ghost = makeLayerGhost(panel, options);
  panel.parentNode.insertBefore(ghost, panel.nextSibling);
  return animateGhost(ghost);
}

// 在 React 卸载清理里调用:此刻面板还在原位(先克隆,滚动位置还量得到),等这一轮提交摘掉面板之后(微任务)才放替身;
// 面板还在(StrictMode 开发期的模拟卸载 / 重挂)就作罢
export function playPushExitAfterUnmount(panel, options = {}) {
  if (!canPlay(panel)) return;
  const ghost = makeLayerGhost(panel, options);
  const parent = panel.parentNode;
  const next = panel.nextSibling;
  queueMicrotask(() => {
    if (panel.isConnected || !parent.isConnected) return;
    parent.insertBefore(ghost, next && next.parentNode === parent ? next : null);
    animateGhost(ghost);
  });
}
