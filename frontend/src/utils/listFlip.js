// 筛选后留下的行滑到新位置(issue #301 第三批,conventions §7「连续」)。
//
// 现网的筛选(未读 / 只看收藏 / 内容类型 / 换来源)都要回服务端重拉:列表先整个换成骨架,新列表到了再重画——
// 新旧两份行不是同一批节点,中间还隔着一段空白,React 这边没法直接做 FLIP。这里的做法是纯表现层的一座桥:
//   ① 指针点下筛选控件的那一刻(捕获阶段,先于 React 的处理),把看得见的行各记一个位置、各克隆一份,摆成一层替身盖在列表上
//      ——此刻替身与真行一模一样,盖上去看不出变化;
//   ② 列表重拉期间替身留在原位(骨架被它挡在下面);
//   ③ 新列表挂上的那一刻:两边都有的行,从旧位置滑到新位置(move 弹簧);新来的淡入并上移 10px;走了的替身原地淡出;
//      列表选中块若还在,跟着它那一行同拍滑。
// 新列表迟迟不来(超过 HOLD_MS)就让替身淡出、把骨架露出来,回到原来的换法。
// 只在列表停在顶上时做(滚到中间时两份列表对不上位置);键盘触发、减少动效不做(readerMotion.pointerMotion)。
// 与页面组件解耦:挂在桌面读者面的外壳上,靠事件与 MutationObserver 自己跟,行上只需要一个 data-flip-id。
import { MOTION, fadeIn, pointerMotion } from './readerMotion.js';
import { motionSpring } from './spring.js';

const TRIGGERS = [
  '.reader-list-head .reader-seg-btn',
  '.reader-list-head .reader-fav-toggle',
  '.reader-kind-chip',
  '.reader-col-sources .reader-source-row',
  '.reader-srcpop .reader-source-row',
].join(', ');
const LIST = '.reader-list-scroll';
const ROW = '.reader-entry[data-flip-id]';
const LAYER = 'reader-flip-layer';
const IDLE_MS = 180;    // 点了但列表没动(点的就是当前项):撤掉替身
const HOLD_MS = 700;    // 新列表超过这么久没到:替身让位给骨架

const realRows = (list) => [...list.querySelectorAll(ROW)].filter((row) => !row.closest(`.${LAYER}`));

function boxIn(list, el) {
  const h = list.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return { x: r.left - h.left - list.clientLeft + list.scrollLeft, y: r.top - h.top - list.clientTop + list.scrollTop, w: r.width, h: r.height };
}

function ghostOf(el, box) {
  const ghost = el.cloneNode(true);
  for (const attr of ['id', 'tabindex', 'data-flip-id']) ghost.removeAttribute(attr);
  ghost.setAttribute('aria-hidden', 'true');
  Object.assign(ghost.style, { position: 'absolute', left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px`, margin: '0', contentVisibility: 'visible' });
  return ghost;
}

/** 在 shell(桌面读者面的 .reader-shell)上挂起这座桥,返回卸载函数。 */
export function mountListFlip(shell) {
  if (!shell || typeof MutationObserver === 'undefined') return () => {};
  let job = null;   // { list, rects: Map(id → box), ghosts: Map(id → node), layer, observer, timer, waiting, activeId }

  const finish = (fade = false) => {
    if (!job) return;
    const { layer, observer, timer } = job;
    job = null;
    clearTimeout(timer);
    observer.disconnect();
    if (fade && layer.isConnected) {
      const done = () => layer.remove();
      layer.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: 'linear', fill: 'forwards' }).finished.then(done, done);
    } else {
      layer.remove();
    }
  };

  const play = () => {
    const { list, rects, ghosts, layer, activeId } = job;
    const spring = motionSpring('move');
    const view = { top: list.scrollTop, bottom: list.scrollTop + list.clientHeight };
    const rows = realRows(list);
    // 新列表外面那层的整体淡入(reader-list-enter)让位:留下的行不该跟着闪一下
    for (const wrap of list.querySelectorAll('.reader-list-enter')) wrap.style.animation = 'none';
    const seen = new Set();
    for (const row of rows) {
      const id = row.dataset.flipId;
      const to = boxIn(list, row);
      if (to.y > view.bottom || to.y + to.h < view.top) continue;
      const from = rects.get(id);
      if (from) {
        seen.add(id);
        const keyframes = [{ transform: `translate(${(from.x - to.x).toFixed(1)}px, ${(from.y - to.y).toFixed(1)}px)` }, { transform: 'none' }];
        row.animate(keyframes, { duration: spring.duration, easing: spring.easing });
        if (id === activeId) list.querySelector(':scope > .reader-ind')?.animate(keyframes, { duration: spring.duration, easing: spring.easing });
      } else {
        row.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 240, delay: 70, easing: MOTION.ease, fill: 'backwards' });
      }
    }
    // 日期组头、空态没有 id,跟不了:淡入一下
    for (const el of list.querySelectorAll('.reader-date-label, .reader-empty')) if (!el.closest(`.${LAYER}`)) fadeIn(el, MOTION.fast);
    // 替身:留下来的那些当场撤掉(真行已经在同一个位置接手),走了的原地淡出
    layer.style.background = 'transparent';
    for (const [id, ghost] of ghosts) if (seen.has(id)) ghost.remove();
    for (const ghost of layer.children) ghost.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(0.98)' }], { duration: MOTION.fast, easing: 'linear', fill: 'forwards' });
    const { observer, timer } = job;
    job = null;
    clearTimeout(timer);
    observer.disconnect();
    setTimeout(() => layer.remove(), MOTION.fast + 20);
  };

  const onMutate = () => {
    if (!job) return;
    const rows = realRows(job.list);
    if (rows.length === 0) {
      // 重拉回来是空的(「没有未读内容」这类空态已经画出来了):等于全都走了,当场播
      if ([...job.list.querySelectorAll('.reader-empty')].some((el) => !el.closest(`.${LAYER}`))) { play(); return; }
      // 列表进了「正在重拉」:替身守着,等新行;等太久就让位给骨架
      if (!job.waiting) {
        job.waiting = true;
        clearTimeout(job.timer);
        job.timer = setTimeout(() => finish(true), HOLD_MS);
      }
      return;
    }
    // 有真行:是不是换过一批了(重拉回来,或没经过骨架直接换了内容)
    const changed = job.waiting || rows.length !== job.first.length || rows.some((row, i) => row !== job.first[i]);
    if (changed) play();
  };

  const arm = (list) => {
    finish();
    const first = realRows(list);
    if (first.length === 0) return;
    const view = list.getBoundingClientRect();
    const layer = document.createElement('div');
    layer.className = LAYER;
    layer.setAttribute('aria-hidden', 'true');
    layer.setAttribute('inert', '');
    const rects = new Map();
    const ghosts = new Map();
    for (const row of first) {
      const r = row.getBoundingClientRect();
      if (r.bottom <= view.top || r.top >= view.bottom) continue;
      const box = boxIn(list, row);
      rects.set(row.dataset.flipId, box);
      const ghost = ghostOf(row, box);
      ghosts.set(row.dataset.flipId, ghost);
      layer.appendChild(ghost);
    }
    for (const label of list.querySelectorAll('.reader-date-label')) {
      const r = label.getBoundingClientRect();
      if (r.bottom <= view.top || r.top >= view.bottom) continue;
      layer.appendChild(ghostOf(label, boxIn(list, label)));
    }
    layer.style.height = `${Math.max(list.clientHeight, list.scrollHeight)}px`;
    // 垫一层列表栏自己的底色:重拉期间骨架被挡在下面
    for (let el = list; el && el !== document.documentElement; el = el.parentElement) {
      const bg = getComputedStyle(el).backgroundColor;
      if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') { layer.style.background = bg; break; }
    }
    const observer = new MutationObserver(onMutate);
    job = { list, rects, ghosts, layer, observer, first, waiting: false, activeId: list.querySelector('.reader-entry.is-active')?.dataset.flipId || null, timer: setTimeout(() => finish(), IDLE_MS) };
    list.appendChild(layer);
    observer.observe(list, { childList: true, subtree: true });
  };

  const onClick = (event) => {
    const hit = event.target.closest?.(TRIGGERS);
    if (!hit || !shell.contains(hit)) return;
    const list = shell.querySelector(LIST);
    if (!list || list.scrollTop > 2 || pointerMotion() !== 'full') { finish(); return; }
    arm(list);
  };
  shell.addEventListener('click', onClick, true);

  return () => {
    shell.removeEventListener('click', onClick, true);
    finish();
  };
}
