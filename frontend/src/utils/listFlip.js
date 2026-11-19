// 筛选 / 换范围之后留下的条目滑到新位置(issue #301 第三批;第七批推广到榜单与发现页。conventions §7「连续」)。
//
// 现网的筛选(未读 / 只看收藏 / 内容类型 / 换来源)与榜单的时间范围都要回服务端重拉:内容先整个换成骨架或「加载中」,新的到了再重画——
// 新旧两份不是同一批节点,中间还隔着一段空白,React 这边没法直接做 FLIP。这里的做法是纯表现层的一座桥:
//   ① 指针点下筛选控件的那一刻(捕获阶段,先于 React 的处理),把看得见的条目各记一个位置、各克隆一份,摆成一层替身盖在原处
//      ——此刻替身与真条目一模一样,盖上去看不出变化;
//   ② 重拉期间替身留在原位(骨架被它挡在下面);
//   ③ 新内容挂上的那一刻:两边都有的,从旧位置滑到新位置(move 弹簧);新来的淡入并上移 10px;走了的替身原地淡出;
//      条目列的选中块若还在,跟着它那一行同拍滑;同名的日期组头也滑过去。
// 新内容迟迟不来(超过 HOLD_MS)就让替身淡出、把骨架露出来,回到原来的换法。
// 键盘触发、减少动效不做(readerMotion.pointerMotion)。
// 与页面组件解耦:挂在桌面读者面的外壳上,靠事件与 MutationObserver 自己跟,条目上只需要一个能认出「同一条」的属性。
import { MOTION, fadeIn, motionIntent, pointerMotion } from './readerMotion.js';
import { motionSpring } from './spring.js';

const LAYER = 'reader-flip-layer';
const IDLE_MS = 180;    // 点了但内容没动(点的就是当前项):撤掉替身
const HOLD_MS = 700;    // 新内容超过这么久没到:替身让位给骨架

// 哪几处做,一处一行。
//   host   重拉期间不会被换掉的容器,替身层挂在它里面;
//   area   替身层盖住的那一块(缺省 = host 整个)。给了的话替身层是它的一份空壳(类名跟着,条目的样式才对得上);
//   cover  替身层要不要垫底色:要回服务端重拉的垫(把骨架 /「加载中」挡在下面),当场就换的不垫;
//   atRest 只在这个条件下做(滚到中间时两份内容对不上位置)。
export const FLIP_ZONES = [
  {
    name: 'list',   // 条目列:未读 / 只看收藏 / 内容类型 / 换来源
    triggers: [
      '.reader-list-head .reader-seg-btn',
      '.reader-list-head .reader-fav-toggle',
      '.reader-kind-chip',
      '.reader-col-sources .reader-source-row',
      '.reader-srcpop .reader-source-row',
    ].join(', '),
    host: '.reader-list-scroll',
    row: '.reader-entry[data-flip-id]',
    idOf: (el) => el.dataset.flipId,
    label: '.reader-date-label',
    empty: '.reader-empty',
    enterWrap: '.reader-list-enter',
    indicator: ':scope > .reader-ind',
    active: '.reader-entry.is-active',
    atRest: (host) => host.scrollTop <= 2,
    cover: true,
  },
  {
    name: 'rank',   // 榜单:换时间范围
    triggers: '.rk-page .rk-range .mini-seg-btn',
    host: '.rk-page .rk-wrap:not(.rk-event)',
    area: '.rk-list',
    row: '.rk-row[data-row]',
    idOf: (el) => el.dataset.row,
    empty: '.rk-empty, .ranking-state.is-error',
    atRest: () => true,
    cover: true,
  },
  {
    name: 'discover',   // 发现页:形态筛选(停在页顶时;滚下去之后改为各组淡入上移,见 swapMotion)
    triggers: '.reader-disc-seg .reader-seg-btn',
    host: ':scope > .reader-disc',   // 桌面上滚的是发现页这一整块(里面那层 .reader-disc-scroll 在桌面不滚)
    row: '.reader-disc-card[data-flip-id]',
    idOf: (el) => el.dataset.flipId,
    empty: '.reader-disc-empty',
    atRest: (host) => host.scrollTop < 2,
    cover: false,
  },
];

const outside = (el) => !el.closest(`.${LAYER}`);
const realRows = (host, zone) => [...host.querySelectorAll(zone.row)].filter(outside);

function boxIn(host, el) {
  const h = host.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return { x: r.left - h.left - host.clientLeft + host.scrollLeft, y: r.top - h.top - host.clientTop + host.scrollTop, w: r.width, h: r.height };
}

// 看得见的范围(视口坐标):host 自己是滚动容器就是它的盒子,否则是最近的那个滚动祖先
function clipRect(host) {
  for (let el = host; el && el !== document.documentElement; el = el.parentElement) {
    const flow = getComputedStyle(el).overflowY;
    if (flow === 'auto' || flow === 'scroll') return el.getBoundingClientRect();
  }
  return { top: 0, bottom: window.innerHeight };
}
const within = (rect, clip) => rect.bottom > clip.top && rect.top < clip.bottom;

function ghostOf(el, box, origin) {
  const ghost = el.cloneNode(true);
  for (const attr of ['id', 'tabindex', 'data-flip-id', 'data-row']) ghost.removeAttribute(attr);
  ghost.setAttribute('aria-hidden', 'true');
  Object.assign(ghost.style, { position: 'absolute', left: `${box.x - origin.x}px`, top: `${box.y - origin.y}px`, width: `${box.w}px`, height: `${box.h}px`, margin: '0', contentVisibility: 'visible' });
  return ghost;
}

const slide = (from, to) => [{ transform: `translate(${(from.x - to.x).toFixed(1)}px, ${(from.y - to.y).toFixed(1)}px)` }, { transform: 'none' }];

/** 在 shell(桌面读者面的 .reader-shell)上挂起这座桥,返回卸载函数。 */
export function mountListFlip(shell) {
  if (!shell || typeof MutationObserver === 'undefined') return () => {};
  let job = null;   // { zone, host, rects, ghosts, labels, layer, observer, timer, first, intent, waiting, activeId }

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
    // 等待期间情况变了(另一个标签页打开了「减少动效」):撤掉替身,直接落位
    if (pointerMotion(job.intent) !== 'full') { finish(); return; }
    const { zone, host, rects, ghosts, labels, layer, activeId } = job;
    const spring = motionSpring('move');
    const timing = { duration: spring.duration, easing: spring.easing };
    const clip = clipRect(host);
    // 新内容外面那层的整体淡入让位:留下的条目不该跟着闪一下
    if (zone.enterWrap) for (const wrap of host.querySelectorAll(zone.enterWrap)) wrap.style.animation = 'none';
    const seen = new Set();
    for (const row of realRows(host, zone)) {
      if (!within(row.getBoundingClientRect(), clip)) continue;
      const id = zone.idOf(row);
      const to = boxIn(host, row);
      const from = rects.get(id);
      if (from) {
        seen.add(id);
        row.animate(slide(from, to), timing);
        if (zone.indicator && id === activeId) host.querySelector(zone.indicator)?.animate(slide(from, to), timing);
      } else {
        row.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 240, delay: 70, easing: MOTION.ease, fill: 'backwards' });
      }
    }
    // 组头:同名的滑到新位置,新出现的淡入
    if (zone.label) {
      for (const el of host.querySelectorAll(zone.label)) {
        if (!outside(el)) continue;
        const key = el.textContent.trim();
        const old = labels.get(key);
        if (old && within(el.getBoundingClientRect(), clip)) {
          el.animate(slide(old.box, boxIn(host, el)), timing);
          old.ghost.remove();
        } else {
          fadeIn(el, MOTION.fast);
        }
      }
    }
    // 空态跟不了:淡入一下
    if (zone.empty) for (const el of host.querySelectorAll(zone.empty)) if (outside(el)) fadeIn(el, MOTION.fast);
    // 替身:留下来的那些当场撤掉(真条目已经在同一个位置接手),走了的原地淡出
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
    const { zone, host } = job;
    const rows = realRows(host, zone);
    if (rows.length === 0) {
      // 重拉回来是空的(「没有未读内容」这类空态已经画出来了):等于全都走了,当场播
      if (zone.empty && [...host.querySelectorAll(zone.empty)].some(outside)) { play(); return; }
      // 进了「正在重拉」:替身守着,等新内容;等太久就让位给骨架
      if (!job.waiting) {
        job.waiting = true;
        clearTimeout(job.timer);
        job.timer = setTimeout(() => finish(true), HOLD_MS);
      }
      return;
    }
    // 有真条目:是不是换过一批了(重拉回来,或没经过骨架直接换了内容)
    const changed = job.waiting || rows.length !== job.first.length || rows.some((row, i) => row !== job.first[i]);
    if (changed) play();
  };

  const arm = (zone, host, intent) => {
    finish();
    const first = realRows(host, zone);
    if (first.length === 0) return;
    const area = zone.area ? host.querySelector(zone.area) : host;
    if (!area) return;
    const clip = clipRect(host);
    let layer;
    let origin = { x: 0, y: 0 };
    if (zone.area) {
      // 替身层是那一块的空壳:类名跟着,里面条目的样式(选择器挂在它名下的那些)才对得上
      layer = area.cloneNode(false);
      layer.removeAttribute('id');
      layer.classList.add(LAYER);
      const box = boxIn(host, area);
      origin = { x: box.x, y: box.y };
      Object.assign(layer.style, { display: 'block', left: `${box.x}px`, top: `${box.y}px`, right: 'auto', width: `${box.w}px`, height: `${box.h}px`, margin: '0', padding: '0', listStyle: 'none' });
    } else {
      layer = document.createElement('div');
      layer.className = LAYER;
      layer.style.height = `${Math.max(host.clientHeight, host.scrollHeight)}px`;
    }
    layer.setAttribute('aria-hidden', 'true');
    layer.setAttribute('inert', '');
    const rects = new Map();
    const ghosts = new Map();
    const labels = new Map();
    for (const row of first) {
      if (!within(row.getBoundingClientRect(), clip)) continue;
      const id = zone.idOf(row);
      const box = boxIn(host, row);
      rects.set(id, box);
      const ghost = ghostOf(row, box, origin);
      ghosts.set(id, ghost);
      layer.appendChild(ghost);
    }
    if (zone.label) {
      for (const label of host.querySelectorAll(zone.label)) {
        if (!within(label.getBoundingClientRect(), clip)) continue;
        const box = boxIn(host, label);
        const ghost = ghostOf(label, box, origin);
        labels.set(label.textContent.trim(), { box, ghost });
        layer.appendChild(ghost);
      }
    }
    if (zone.cover) {
      // 垫一层这一栏自己的底色:重拉期间骨架被挡在下面
      for (let el = host; el && el !== document.documentElement; el = el.parentElement) {
        const bg = getComputedStyle(el).backgroundColor;
        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') { layer.style.background = bg; break; }
      }
    }
    const observer = new MutationObserver(onMutate);
    const activeId = zone.active ? (host.querySelector(zone.active)?.dataset.flipId || null) : null;
    job = { zone, host, rects, ghosts, labels, layer, observer, first, intent, waiting: false, activeId, timer: setTimeout(() => finish(), IDLE_MS) };
    host.appendChild(layer);
    observer.observe(host, { childList: true, subtree: true });
  };

  const onClick = (event) => {
    const zone = FLIP_ZONES.find((z) => event.target.closest?.(z.triggers));
    if (!zone || !shell.contains(event.target)) return;
    const host = shell.querySelector(zone.host);
    // 触发方式取这一下自己的(不看别的请求挂起的那一份:指针点了「下一条」还没回来时,用键盘切筛选仍算键盘),并记进这次任务
    const intent = motionIntent();
    if (!host || !zone.atRest(host) || pointerMotion(intent) !== 'full') { finish(); return; }
    arm(zone, host, intent);
  };
  shell.addEventListener('click', onClick, true);

  return () => {
    shell.removeEventListener('click', onClick, true);
    finish();
  };
}
