// 滑动指示器(issue #301 第三批,conventions §7「连续」):选中项的底色 / 底线不再各画各的,而是每个宿主里一块会滑动的指示器。
//
// 做法是独立的一层:挂在桌面读者面的根上(ReaderTab 的 .reader-shell),用 MutationObserver 盯 class 与子节点变化,
// 每次变化之后(微任务里、绘制之前)对下面这张宿主表各量一次——选中项在哪,指示器就摆到哪;选中项换了且是指针触发的,
// 就从上一个位置滑过去。各页面的组件不用知道它的存在(榜单、发现页自己重渲染时这里照样跟得上),
// 手机壳与管理面不挂这一层。指示器在场时宿主带 .has-ind,样式表据此把选中项自己的底色 / 底线让出来;
// 这一层没挂上或被卸掉时,各项照旧自己着色。
//
// 滑法(样页选定稿):小指示器朝目标的那条边先到、后面的边跟上——中途被拉长,落定时收拢;
// 列表选中块面积大,不拉伸,整块用 glide 弹簧滑过去。键盘触发、减少动效一律直接落位(readerMotion.pointerMotion)。
import { pointerMotion } from './readerMotion.js';
import { motionSpring } from './spring.js';

// kind:near = 分段、页签、类型签、导轨(前缘 0.20s / 后缘 0.40s);far = 来源栏(0.34s / 0.42s);row = 列表选中块(整块弹簧)
// look:seg = 面色 + 细影的拇指;select = 中性加深的选中底;line = 2px 底线
// scroll:宿主自己是滚动容器,指示器跟着内容走(位置按内容坐标算,滚动时不用重摆)
// front:指示器放在宿主最前面、不设层级(靠文档顺序垫在各项下面),宿主不另起层叠上下文——导轨用:
//        它的浮签要压在右边各栏之上,导轨一旦自成层叠上下文,浮签就会被后面的栏盖住。别的宿主用 z-index: -1 + isolation。
export const GLIDE_HOSTS = Object.freeze([
  { host: '.reader-vrail', item: '.reader-vrail-btn.is-on:not(.reader-vrail-qa)', kind: 'near', look: 'select', front: true },
  { host: '.reader-seg:not(.reader-disc-viewseg)', item: ':scope > .reader-seg-btn.is-on', kind: 'near', look: 'seg' },
  { host: '.reader-axis', item: ':scope > .reader-axis-btn.is-on', kind: 'near', look: 'seg' },
  { host: '.rankings-page .mini-seg', item: ':scope > .mini-seg-btn.is-on', kind: 'near', look: 'seg' },
  { host: '.reader-tabs', item: ':scope > .reader-tab.is-on', kind: 'near', look: 'line' },
  { host: '.reader-disc-viewseg', item: ':scope > .reader-seg-btn.is-on', kind: 'near', look: 'line' },
  { host: '.reader-kind-band', item: ':scope > .reader-kind-chip.is-on', kind: 'near', look: 'select' },
  { host: '.reader-source-scroll', item: '.reader-source-row-active', kind: 'far', look: 'select', scroll: true },
  { host: '.reader-list-scroll', item: '.reader-entry.is-active', kind: 'row', look: 'select', scroll: true },
]);

const LIQUID_MS = 560;
const LIQUID_STEPS = 28;
const EDGES = { near: [0.2, 0.4], far: [0.34, 0.42] };

// 阻尼振子的阶跃响应(t 秒,dur = 无阻尼周期,bounce 0 = 临界阻尼)
function step(t, dur, bounce) {
  const zeta = Math.max(0.05, 1 - bounce);
  const w0 = (2 * Math.PI) / dur;
  if (zeta >= 1) return 1 - Math.exp(-w0 * t) * (1 + w0 * t);
  const wd = w0 * Math.sqrt(1 - zeta * zeta);
  return 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0) / wd) * Math.sin(wd * t));
}

/** 从矩形 p 滑到 r 的关键帧(相对 r 的 transform,原点左上):朝目标的那条边用 lead 的节奏先到,另一条边用 trail 跟上。 */
export function liquidKeyframes(p, r, [lead, trail]) {
  const out = [];
  const dl = r.x - p.x;
  const dr = r.x + r.w - (p.x + p.w);
  const dt = r.y - p.y;
  const db = r.y + r.h - (p.y + p.h);
  for (let i = 0; i <= LIQUID_STEPS; i += 1) {
    const t = (i / LIQUID_STEPS) * (LIQUID_MS / 1000);
    const fast = i === LIQUID_STEPS ? 1 : step(t, lead, 0);
    const slow = i === LIQUID_STEPS ? 1 : step(t, trail, 0.12);
    const left = p.x + dl * (dl >= 0 ? slow : fast);
    const right = p.x + p.w + dr * (dr >= 0 ? fast : slow);
    const top = p.y + dt * (dt >= 0 ? slow : fast);
    const bottom = p.y + p.h + db * (db >= 0 ? fast : slow);
    const sx = Math.max(0.05, (right - left) / r.w);
    const sy = Math.max(0.05, (bottom - top) / r.h);
    out.push({ transform: `translate(${(left - r.x).toFixed(2)}px, ${(top - r.y).toFixed(2)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})` });
  }
  return out;
}

/** 整块滑过去(不拉伸到中途变形:只是位移,尺寸差由首帧的缩放带过)。 */
export function slideKeyframes(p, r) {
  return [
    { transform: `translate(${(p.x - r.x).toFixed(2)}px, ${(p.y - r.y).toFixed(2)}px) scale(${(p.w / r.w).toFixed(4)}, ${(p.h / r.h).toFixed(4)})` },
    { transform: 'none' },
  ];
}

const same = (a, b) => a && b && Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5 && Math.abs(a.w - b.w) < 0.5 && Math.abs(a.h - b.h) < 0.5;

// el 在宿主内容坐标里的视觉矩形(含它自己和祖先的 transform;滚动容器加上滚动量)
function visualBox(host, el, cfg) {
  const h = host.getBoundingClientRect();
  const t = el.getBoundingClientRect();
  return {
    x: t.left - h.left - host.clientLeft + (cfg.scroll ? host.scrollLeft : 0),
    y: t.top - h.top - host.clientTop + (cfg.scroll ? host.scrollTop : 0),
    w: t.width,
    h: t.height,
  };
}

// el 的布局矩形(不含 transform):沿 offsetParent 一路加到宿主。宿主带 .has-ind 时是定位元素,链一定经过它;
// 万一没经过,退回「视觉矩形的中心 + 布局尺寸」(按压缩放是绕中心的)。取值是整数像素,只在带着 transform 时临时用。
function layoutBox(host, el, cfg) {
  let x = 0;
  let y = 0;
  let node = el;
  while (node && node !== host) {
    x += node.offsetLeft;
    y += node.offsetTop;
    node = node.offsetParent;
  }
  if (node === host) return { x, y, w: el.offsetWidth, h: el.offsetHeight };
  const v = visualBox(host, el, cfg);
  return { x: v.x + (v.w - el.offsetWidth) / 2, y: v.y + (v.h - el.offsetHeight) / 2, w: el.offsetWidth, h: el.offsetHeight };
}

const TRANSFORMS = ['transform', 'scale', 'translate', 'rotate'];
const movesBox = (anim) => (anim.transitionProperty ? TRANSFORMS.includes(anim.transitionProperty)
  : Boolean(anim.effect?.getKeyframes?.().some((k) => TRANSFORMS.some((p) => p in k))));

/** 选中项到宿主之间,正在改 transform 的动画 / 过渡(第二批的按压回弹、筛选时行的位移):此时视觉矩形是变了形的。 */
function boxMovers(host, target) {
  const out = [];
  for (let el = target; el && el !== host; el = el.parentElement) {
    for (const anim of el.getAnimations()) if (anim.playState !== 'finished' && anim.playState !== 'idle' && movesBox(anim)) out.push(anim);
  }
  return out;
}

const lineOf = (rect) => ({ x: rect.x, y: rect.y + rect.h - 1, w: rect.w, h: 2 });   // 底线:压在页签底边下 1px,与原来的 ::after 同位

function place(ind, rect, radius) {
  const s = ind.style;
  s.left = `${rect.x.toFixed(2)}px`;
  s.top = `${rect.y.toFixed(2)}px`;
  s.width = `${rect.w.toFixed(2)}px`;
  s.height = `${rect.h.toFixed(2)}px`;
  if (radius != null) s.borderRadius = radius;
}

/**
 * 在 root(桌面读者面的 .reader-shell)上挂起指示器这一层,返回卸载函数。
 * 幂等:量出来的位置没变就什么都不写,自己的写入引起的那一轮变化回调不会再触发写入。
 */
export function mountGlideIndicators(root) {
  if (!root || typeof MutationObserver === 'undefined') return () => {};
  const states = new Map();   // host → { ind, target, rect, anim }
  let resettle = 0;           // 等「带着 transform 的那几段」跑完再重摆一次的凭据

  const drop = (host) => {
    const st = states.get(host);
    if (!st) return;
    st.anim?.cancel();
    st.ind.remove();
    host.classList.remove('has-ind');
    states.delete(host);
  };

  const syncHost = (host, cfg, settle) => {
    const target = host.querySelector(cfg.item);
    let st = states.get(host);
    if (!target || target.getClientRects().length === 0) { drop(host); return; }
    if (!host.classList.contains('has-ind')) host.classList.add('has-ind');   // 先让宿主成为定位元素,下面的布局矩形才量得到它头上
    // 选中项正带着 transform(刚松手还在回弹、正跟着筛选滑):视觉矩形不能当终点,改用布局矩形;那几段跑完再按视觉矩形精确重摆一次
    const movers = boxMovers(host, target);
    const box = movers.length ? layoutBox(host, target, cfg) : visualBox(host, target, cfg);
    const rect = cfg.look === 'line' ? lineOf(box) : box;
    if (movers.length) {
      const token = ++resettle;
      Promise.allSettled(movers.map((anim) => anim.finished)).then(() => { if (token === resettle) sync(true); });
    }
    const radius = cfg.look === 'line' ? null : getComputedStyle(target).borderRadius;
    if (!st || !st.ind.isConnected) {
      // 第一次出现(或宿主被重画过):直接落位
      st?.anim?.cancel();
      const ind = document.createElement('i');
      ind.className = `reader-ind is-${cfg.look}${cfg.front ? ' is-front' : ''}`;
      ind.setAttribute('aria-hidden', 'true');
      place(ind, rect, radius);
      if (cfg.front) host.prepend(ind); else host.appendChild(ind);
      states.set(host, { ind, target, rect, anim: null });
      return;
    }
    if (st.target === target && same(st.rect, rect)) return;
    // 选中项换了、上一个还在文档里(同一份列表里挪了位置)、且是指针触发:滑过去;其余情况(尺寸变了、列表重拉、键盘、减少动效)直接落位
    const glide = !settle && st.target !== target && st.target.isConnected && pointerMotion() === 'full';
    // 起点:上一段还没滑完(连点改了向)就从它此刻看得见的位置接着走,不先跳到上一次的终点
    const from = st.anim && st.anim.playState === 'running' ? visualBox(host, st.ind, cfg) : st.rect;
    place(st.ind, rect, radius);
    st.anim?.cancel();
    st.anim = null;
    if (glide) {
      if (cfg.kind === 'row') {
        const s = motionSpring('glide');
        st.anim = st.ind.animate(slideKeyframes(from, rect), { duration: s.duration, easing: s.easing });
      } else {
        st.anim = st.ind.animate(liquidKeyframes(from, rect, EDGES[cfg.kind]), { duration: LIQUID_MS, easing: 'linear' });
      }
    }
    st.target = target;
    st.rect = rect;
  };

  const sync = (settle = false) => {
    const live = new Set();
    for (const cfg of GLIDE_HOSTS) {
      for (const host of root.querySelectorAll(cfg.host)) {
        if (host.closest('[inert]')) continue;   // 替身里克隆出来的宿主(来源栏收起时的替身、筛选的替身层):它带着自己那份指示器,不再补一份
        live.add(host);
        syncHost(host, cfg, settle);
      }
    }
    for (const host of [...states.keys()]) if (!live.has(host) || !host.isConnected) drop(host);
  };

  // 只有碰到宿主的变化才量:宿主里面换了选中态或增删了东西(指示器自己的增删不算),或者外面挂上 / 卸掉了含宿主的一块。
  // 别处的变化不看——播放条每 240ms 改一次走时、正文与问答内容的更新都落在外壳里,不该每次都把十个宿主量一遍。
  const anyHost = GLIDE_HOSTS.map((h) => h.host).join(', ');
  const ownNode = (n) => n.nodeType === 1 && n.classList.contains('reader-ind');
  const relevant = (m) => {
    const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
    if (!el) return false;
    if (el === root) return true;
    const inside = Boolean(el.closest(anyHost));
    if (m.type === 'attributes') return inside;
    const nodes = [...m.addedNodes, ...m.removedNodes];
    if (inside) return nodes.some((n) => !ownNode(n));
    return nodes.some((n) => n.nodeType === 1 && (n.matches(anyHost) || n.querySelector(anyHost)));
  };
  const observer = new MutationObserver((records) => { if (records.some(relevant)) sync(); });
  observer.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  // 尺寸变了(窗口、来源栏收放、字体晚到):重摆,不滑
  const resize = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => sync(true)) : null;
  resize?.observe(root);
  sync(true);

  return () => {
    observer.disconnect();
    resize?.disconnect();
    for (const host of [...states.keys()]) drop(host);
  };
}
