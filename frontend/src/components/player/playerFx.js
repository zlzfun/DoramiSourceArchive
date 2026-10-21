// 播放条形变(issue #199,样页 docs/design/podcast-player/global.html?layout=float&tone=blur 拍板)。
// WAAPI,不用 View Transitions;全程只动 transform / opacity / clip-path,不做逐帧布局:
// · 底板 = translate + clip-path: inset(... round r)——位置与尺寸同步插值,每一帧都是真圆角,内容不缩放;
// · 影子单独一层 translate + scale(clip-path 会把外投影一起裁掉);
// · 胶囊的 accent 实心收拢成条上的播放钮,▶ 作为共享元素飞过去、半程换 ⏸;
// · 真实条的内容裁在底板当前矩形里错峰淡入,宽条两端不会先于底板出现。
// 两端矩形对齐到设备像素(125% / 150% 缩放下 getBoundingClientRect 是小数,形变层停在半像素上会糊,
// 交接给真实元素那一帧就会「抖一下变清楚」)。

const SVG_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
export const PLAY_SVG = `${SVG_OPEN}<polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/></svg>`;
export const PAUSE_SVG = `${SVG_OPEN}<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/></svg>`;

const dpr = () => (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
export const snap = (v) => Math.round(v * dpr()) / dpr();

export function rectOf(el) {
  const r = el.getBoundingClientRect();
  const x = snap(r.left);
  const y = snap(r.top);
  return { x, y, w: snap(r.right) - x, h: snap(r.bottom) - y };
}

export function radiusOf(el) {
  return parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
}

/** 起点是否看得见:脱离文档、零尺寸、滚出视口都算看不见(退化为淡入)。 */
export function visibleRect(el) {
  if (!el || !el.isConnected) return null;
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  if (r.bottom <= 0 || r.top >= window.innerHeight || r.right <= 0 || r.left >= window.innerWidth) return null;
  return rectOf(el);
}

/** 一次形变的画布:under = 底板 / 影子层(在条下面),over = 内核 / 图标 / 副本层(在条上面)。 */
export function createFx(under, over) {
  const running = [];
  const owned = [];
  const attach = (el, layer = over) => { layer.append(el); owned.push(el); return el; };
  const track = (a) => { if (a) running.push(a); return a; };
  const span = (cls, layer) => {
    const el = attach(document.createElement('span'), layer);
    el.className = cls;
    return el;
  };

  const box = (cls, from, to, { radiusFrom, radiusTo, dur, easing, delay = 0, extra = [], below = false }) => {
    const W = Math.max(from.w, to.w);
    const H = Math.max(from.h, to.h);
    const el = span(`player-fx-box ${cls}`, below ? under : over);
    el.style.width = `${W}px`;
    el.style.height = `${H}px`;
    const kf = (r, rad) => ({
      transform: `translate(${r.x}px, ${r.y}px)`,
      clipPath: `inset(0px ${W - r.w}px ${H - r.h}px 0px round ${rad}px)`,
    });
    const a = track(el.animate([{ ...kf(from, radiusFrom), ...(extra[0] || {}) }, { ...kf(to, radiusTo), ...(extra[1] || {}) }],
      { duration: dur, easing, delay, fill: 'both' }));
    return { el, a };
  };

  // 让一个静止的元素(位于 at)只露出「正在形变的底板」那一块:inset 按 from → to 同一缓动插值(可以为负:起点在元素框外)
  const clipFollow = (el, at, from, to, { radiusFrom, radiusTo, dur, easing }) => {
    const ins = (r, rad) => `inset(${r.y - at.y}px ${at.x + at.w - (r.x + r.w)}px ${at.y + at.h - (r.y + r.h)}px ${r.x - at.x}px round ${rad}px)`;
    return track(el.animate([{ clipPath: ins(from, radiusFrom) }, { clipPath: ins(to, radiusTo) }], { duration: dur, easing, fill: 'both' }));
  };

  const shadow = (from, to, { dur, easing, radiusTo, endOpacity = 1 }) => {
    const el = span('player-fx-shadow', under);
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.style.borderRadius = `${radiusTo}px`;
    // 第 0 帧没有影子(与按下前一致),起飞后迅速出现;落地与真条的影子重合
    const a = track(el.animate([
      { transform: `translate(${from.x}px, ${from.y}px) scale(${from.w / to.w}, ${from.h / to.h})`, opacity: 0 },
      { transform: `translate(${(from.x + to.x) / 2}px, ${(from.y + to.y) / 2}px) scale(${(from.w / to.w + 1) / 2}, ${(from.h / to.h + 1) / 2})`, opacity: endOpacity ? 1 : 0.7, offset: 0.5 },
      { transform: `translate(${to.x}px, ${to.y}px) scale(1, 1)`, opacity: endOpacity },
    ], { duration: dur, easing, fill: 'both' }));
    return { el, a };
  };

  const fly = (node, from, to, { dur, easing, delay = 0, fade = null }) => {
    const el = span('player-fx-fly', over);
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.append(node);
    const s = from.w / to.w;
    const a = track(el.animate([
      { transform: `translate(${from.x + (from.w - to.w) / 2}px, ${from.y + (from.h - to.h) / 2}px) scale(${s})`, opacity: fade ? fade[0] : 1 },
      { transform: `translate(${to.x}px, ${to.y}px) scale(1)`, opacity: fade ? fade[1] : 1 },
    ], { duration: dur, easing, delay, fill: 'both' }));
    return { el, a };
  };

  // 多段路径(宽条用):stops = [{ r, rad, at, ease, extra }],at 是这一站在时间轴上的位置,ease 是走到下一站的缓动
  // (逐段弹簧,整体时间轴 linear——两段各自从静止起步、静止收尾,拐角处没有速度突变)
  const insetOf = (at, r, rad) => `inset(${r.y - at.y}px ${at.x + at.w - (r.x + r.w)}px ${at.y + at.h - (r.y + r.h)}px ${r.x - at.x}px round ${rad}px)`;
  const pathBox = (cls, stops, { dur, below = false }) => {
    const W = Math.max(...stops.map((s) => s.r.w));
    const H = Math.max(...stops.map((s) => s.r.h));
    const el = span(`player-fx-box ${cls}`, below ? under : over);
    el.style.width = `${W}px`;
    el.style.height = `${H}px`;
    const frames = stops.map((s) => ({
      offset: s.at,
      easing: s.ease || 'linear',
      transform: `translate(${s.r.x}px, ${s.r.y}px)`,
      clipPath: `inset(0px ${W - s.r.w}px ${H - s.r.h}px 0px round ${s.rad}px)`,
      ...(s.extra || {}),
    }));
    return { el, a: track(el.animate(frames, { duration: dur, easing: 'linear', fill: 'both' })) };
  };
  const pathClip = (el, at, stops, { dur }) => track(el.animate(
    stops.map((s) => ({ offset: s.at, easing: s.ease || 'linear', clipPath: insetOf(at, s.r, s.rad) })),
    { duration: dur, easing: 'linear', fill: 'both' },
  ));
  const pathShadow = (stops, { dur, radiusTo, endOpacity }) => {
    const to = stops[stops.length - 1].r;
    const el = span('player-fx-shadow', under);
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.style.borderRadius = `${radiusTo}px`;
    const frames = stops.map((s, i) => ({
      offset: s.at,
      easing: s.ease || 'linear',
      transform: `translate(${s.r.x}px, ${s.r.y}px) scale(${s.r.w / to.w}, ${s.r.h / to.h})`,
      opacity: i === 0 ? 0 : i === stops.length - 1 ? endOpacity : 1,
    }));
    return { el, a: track(el.animate(frames, { duration: dur, easing: 'linear', fill: 'both' })) };
  };
  const pathFly = (node, stops, { dur }) => {
    const to = stops[stops.length - 1].r;
    const el = span('player-fx-fly', over);
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.append(node);
    const frames = stops.map((s) => ({
      offset: s.at,
      easing: s.ease || 'linear',
      transform: `translate(${s.r.x + (s.r.w - to.w) / 2}px, ${s.r.y + (s.r.h - to.h) / 2}px) scale(${s.r.w / to.w})`,
    }));
    return { el, a: track(el.animate(frames, { duration: dur, easing: 'linear', fill: 'both' })) };
  };
  const animate = (el, frames, options) => (el ? track(el.animate(frames, options)) : null);

  return {
    box, clipFollow, shadow, fly, pathBox, pathClip, pathShadow, pathFly, animate, attach, span,
    running,
    done() {
      return Promise.all(running.map((a) => a.finished.catch(() => null)));
    },
    /** 撤掉形变层、取消动画(真实元素回到自己的样式)。落地那一帧调用。 */
    cleanup() {
      running.forEach((a) => { try { a.cancel(); } catch { /* 已结束 */ } });
      running.length = 0;
      owned.forEach((el) => el.remove());
      owned.length = 0;
    },
    finish() {
      running.slice().forEach((a) => { try { a.finish(); } catch { /* 已结束或无限循环 */ } });
    },
  };
}

export function glyphNode({ tone = false } = {}) {
  const el = document.createElement('span');
  el.className = `player-fx-glyph${tone ? ' is-tone' : ''}`;
  el.innerHTML = `<span class="player-g-play">${PLAY_SVG}</span><span class="player-g-pause">${PAUSE_SVG}</span>`;
  return el;
}

/** 主钮的字与时长一起复刻(第 0 帧与按下前逐像素一致),随起飞迅速淡出。 */
function labelReplica(origin) {
  const el = document.createElement('span');
  el.className = 'player-fx-label';
  const text = document.createElement('span');
  text.textContent = origin.text.label;
  el.append(text);
  if (origin.text.len) {
    const len = document.createElement('span');
    len.className = 'listen-len';
    len.textContent = origin.text.len;
    el.append(len);
  }
  const { src, style } = origin;
  // font 简写在按钮上会取到空串(Chrome),逐项拷贝;字从入口上那段字的实际 x 起排
  el.style.cssText = `transform:translate(${src.x}px, ${src.y}px);width:${src.w}px;height:${src.h}px;`
    + `font-family:${style.fontFamily};font-size:${style.fontSize};font-weight:${style.fontWeight};line-height:${style.lineHeight};`
    + `gap:${style.gap}px;padding-left:${origin.textX - src.x}px`;
  return el;
}

/** 按下那一刻量入口(之后入口会就地换成播放态):胶囊、图标、字与时长、字体。看不见时返回 null。 */
export function measureOrigin(btn) {
  const src = visibleRect(btn);
  if (!src) return null;
  // 量图标本身(svg),不量它的 12px 方格:飞出去的 ▶ 第 0 帧要与入口上的 ▶ 一样大
  const icoEl = btn.querySelector('.listen-ico svg, .listen-ico .player-spark') || btn.querySelector('.listen-ico');
  const ico = icoEl ? rectOf(icoEl) : { x: src.x + 12, y: src.y + src.h / 2 - 6, w: 12, h: 12 };
  const cs = getComputedStyle(btn);
  const labelEl = btn.querySelector('.listen-label');
  return {
    src,
    ico,
    textX: labelEl ? labelEl.getBoundingClientRect().left : ico.x + ico.w + 7,
    primary: btn.classList.contains('is-primary'),
    radius: Math.min(parseFloat(cs.borderTopLeftRadius) || src.h / 2, src.h / 2),
    style: {
      fontFamily: cs.fontFamily,
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
      lineHeight: cs.lineHeight,
      gap: parseFloat(cs.columnGap) || 7,
    },
    text: {
      label: btn.querySelector('.listen-label')?.textContent || '',
      len: btn.querySelector('.listen-len')?.textContent || '',
    },
  };
}

const wideParts = (bar) => [
  ...bar.querySelectorAll('.player-skip'),
  bar.querySelector('.player-seek'),
  bar.querySelector('.player-art'),
  bar.querySelector('.player-info-slot'),
  bar.querySelector('.player-rate'),
  bar.querySelector('.player-min'),
  bar.querySelector('.player-close'),
].filter(Boolean);
const narrowParts = (bar) => [
  bar.querySelector('.player-art'),
  bar.querySelector('.player-info-slot'),
  ...bar.querySelectorAll('.player-skip'),
  bar.querySelector('.player-seek'),
  bar.querySelector('.player-rate'),
  bar.querySelector('.player-min'),
  bar.querySelector('.player-close'),
].filter(Boolean);

/**
 * 胶囊 → 底部条。wide(桌面内容区浮层)走「先落再铺」两段:① 落——胶囊收成条的高度、落到条中区的播放钮位置,
 * accent 内核同时收成播放钮;② 铺——带初速度向两侧铺开到全宽,真实条的内容从中区往两端露出。
 * 一步插值的话,胶囊(约 160 × 30)到宽条(1376 × 72)中途是一根细长横条,斜着扫过阅读窗与条目列(样页否掉)。
 * 窄条(手机)一步形变。
 */
export function openMorph(fx, { origin, bar, wide, toneDiffers, E, M }) {
  const play = bar.querySelector('.player-play');
  const card = rectOf(bar);
  const barR = radiusOf(bar);
  const playR = rectOf(play);
  const ico = rectOf(play.querySelector('.player-play-ico'));
  const { src } = origin;
  const srcRad = origin.radius;
  const coreFrom = origin.primary ? src : { x: origin.ico.x - 3, y: origin.ico.y - 3, w: origin.ico.w + 6, h: origin.ico.h + 6 };
  const shadowEnd = getComputedStyle(bar).boxShadow !== 'none' ? 1 : 0;
  // 主钮的字与时长复刻要叠在 accent 内核之上(DOM 顺序即绘制顺序):在内核之后再挂
  const replica = origin.primary ? labelReplica(origin) : null;
  const glyph = glyphNode();
  // 次钮的起点是 ✦ 图标:▶ 从那里淡入,不在第 0 帧压出一颗白三角
  const glyphIn = origin.primary ? null : [{ opacity: 0 }, { opacity: 1, offset: 0.3 }, { opacity: 1 }];

  if (wide) {
    const dur = M.openDur;
    const k = 0.42;
    const pc = playR.x + playR.w / 2;
    const vw = Math.min(card.w, Math.max(src.w + 48, 232));
    const via = { x: snap(Math.max(card.x, Math.min(card.x + card.w - vw, pc - vw / 2))), y: card.y, w: vw, h: card.h };
    const three = (a, b, c, extra = [{}, {}, {}]) => [
      { r: a.r, rad: a.rad, at: 0, ease: E.drop, extra: extra[0] },
      { r: b.r, rad: b.rad, at: k, ease: E.spread, extra: extra[1] },
      { r: c.r, rad: c.rad, at: 1, extra: extra[2] },
    ];
    const plate = three({ r: src, rad: srcRad }, { r: via, rad: via.h / 2 }, { r: card, rad: barR });
    fx.pathShadow(plate, { dur, radiusTo: barR, endOpacity: shadowEnd });
    fx.pathBox('player-fx-plate', plate, { dur, below: true });
    fx.pathClip(bar, card, three({ r: via, rad: via.h / 2 }, { r: via, rad: via.h / 2 }, { r: card, rad: barR }), { dur });
    const coreStops = three({ r: coreFrom, rad: coreFrom.h / 2 }, { r: playR, rad: playR.h / 2 }, { r: playR, rad: playR.h / 2 },
      [{ opacity: origin.primary ? 1 : 0 }, { opacity: 1 }, { opacity: 1 }]);
    fx.pathBox('player-fx-core', coreStops, { dur });
    if (toneDiffers) {
      // 播放钮是墨色(实心 accent 留给标题区主钮):两层同轨迹的内核交叉淡变,只动 opacity
      const t = fx.pathBox('player-fx-core is-tone', coreStops.map((s) => ({ ...s, extra: {} })), { dur });
      fx.animate(t.el, [{ opacity: 0 }, { opacity: 0, offset: k * 0.35 }, { opacity: 1, offset: k * 0.95 }, { opacity: 1 }], { duration: dur, fill: 'both' });
    }
    if (replica) fx.animate(fx.attach(replica), [{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: E.out, fill: 'both' });
    fx.pathFly(glyph, [{ r: origin.ico, at: 0, ease: E.drop }, { r: ico, at: k, ease: 'linear' }, { r: ico, at: 1 }], { dur });
    if (glyphIn) fx.animate(glyph, glyphIn, { duration: dur, fill: 'both' });
    fx.animate(glyph.querySelector('.player-g-play'), [{ opacity: 1 }, { opacity: 0 }], { duration: 160, delay: dur * k - 60, fill: 'both' });
    fx.animate(glyph.querySelector('.player-g-pause'), [{ opacity: 0 }, { opacity: 1 }], { duration: 160, delay: dur * k - 60, fill: 'both' });
    wideParts(bar).forEach((el, i) => {
      fx.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration: 220, delay: dur * k - 20 + i * 16, easing: E.fade, fill: 'both' });
    });
    return M.openDur;
  }

  const dur = M.morphDur;
  const ease = E.morph;
  fx.shadow(src, card, { dur, easing: ease, radiusTo: barR, endOpacity: shadowEnd });
  fx.box('player-fx-plate', src, card, { radiusFrom: srcRad, radiusTo: barR, dur, easing: ease, below: true });
  fx.clipFollow(bar, card, src, card, { radiusFrom: srcRad, radiusTo: barR, dur, easing: ease });
  fx.box('player-fx-core', coreFrom, playR, {
    radiusFrom: coreFrom.h / 2, radiusTo: playR.h / 2, dur, easing: ease,
    extra: [{ opacity: origin.primary ? 1 : 0 }, { opacity: 1 }],
  });
  if (toneDiffers) {
    const t = fx.box('player-fx-core is-tone', coreFrom, playR, { radiusFrom: coreFrom.h / 2, radiusTo: playR.h / 2, dur, easing: ease });
    fx.animate(t.el, [{ opacity: 0 }, { opacity: 0, offset: 0.35 }, { opacity: 1, offset: 0.8 }, { opacity: 1 }], { duration: dur, fill: 'both' });
  }
  if (replica) fx.animate(fx.attach(replica), [{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: E.out, fill: 'both' });
  fx.fly(glyph, origin.ico, ico, { dur, easing: ease });
  if (glyphIn) fx.animate(glyph, glyphIn, { duration: dur, fill: 'both' });
  fx.animate(glyph.querySelector('.player-g-play'), [{ opacity: 1 }, { opacity: 0 }], { duration: 160, delay: dur * 0.5, fill: 'both' });
  fx.animate(glyph.querySelector('.player-g-pause'), [{ opacity: 0 }, { opacity: 1 }], { duration: 160, delay: dur * 0.5, fill: 'both' });
  narrowParts(bar).forEach((el, i) => {
    fx.animate(el, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 220, delay: dur * 0.38 + i * 18, easing: E.fade, fill: 'both' });
  });
  return dur;
}

/** 条 → 迷你之前要量的东西(条隐藏之前):条、封面、播放钮,以及条内容的副本。 */
export function snapshotBar(bar, dock) {
  if (!bar || bar.hidden) return null;
  const cover = bar.querySelector('.player-cover');
  const play = bar.querySelector('.player-play');
  if (!cover || !play) return null;
  const ghost = bar.querySelector('.player-bar-in').cloneNode(true);
  ghost.querySelectorAll('[id]').forEach((n) => n.removeAttribute('id'));
  return {
    from: rectOf(bar),
    radius: radiusOf(bar),
    art: rectOf(cover),
    artRadius: radiusOf(cover),
    play: rectOf(play),
    ico: rectOf(play.querySelector('.player-play-ico')),
    ghost,
    dockWidth: dock?.offsetWidth || 0,
  };
}

export function snapshotMini(mini) {
  if (!mini || mini.hidden) return null;
  const cover = mini.querySelector('.player-cover');
  const play = mini.querySelector('.player-play');
  if (!cover || !play) return null;
  return {
    from: rectOf(mini),
    art: rectOf(cover),
    play: rectOf(play),
    ico: rectOf(play.querySelector('.player-play-ico')),
  };
}

const artCopy = (bar) => bar.querySelector('.player-art-slot')?.cloneNode(true) || null;

/** 条 → 迷你:条收拢成右下角小胶囊;封面与播放钮是共享元素,其余内容在原位快速淡出。 */
export function miniMorph(fx, { snap: s, mini, playing, E, M }) {
  const to = rectOf(mini);
  const artTo = rectOf(mini.querySelector('.player-cover'));
  const playTo = rectOf(mini.querySelector('.player-play'));
  const icoTo = rectOf(mini.querySelector('.player-play-ico'));
  const dur = M.miniDur;
  const ease = E.mini;
  fx.shadow(s.from, to, { dur, easing: ease, radiusTo: to.h / 2 });
  fx.box('player-fx-plate', s.from, to, { radiusFrom: s.radius, radiusTo: to.h / 2, dur, easing: ease, below: true });
  // 副本要和真条吃同一条容器查询,包一层与播放条容器同宽的查询容器
  const cq = fx.span('player-fx-cq');
  cq.style.width = `${s.dockWidth}px`;
  const ghost = s.ghost;
  ghost.classList.add('player-fx-ghost');
  ghost.querySelectorAll('.player-art, .player-play').forEach((n) => { n.style.visibility = 'hidden'; });
  ghost.style.cssText += `;transform:translate(${s.from.x}px, ${s.from.y}px);width:${s.from.w}px;height:${s.from.h}px`;
  cq.append(ghost);
  fx.animate(ghost, [{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: E.out, fill: 'both' });
  fx.clipFollow(ghost, s.from, s.from, to, { radiusFrom: s.radius, radiusTo: to.h / 2, dur, easing: ease });
  const art = fx.box('player-fx-art', s.art, artTo, { radiusFrom: s.artRadius, radiusTo: artTo.h / 2, dur, easing: ease });
  const copy = artCopy(mini);
  if (copy) art.el.append(copy);
  fx.box('player-fx-core is-tone', s.play, playTo, { radiusFrom: s.play.h / 2, radiusTo: playTo.h / 2, dur, easing: ease });
  const glyph = glyphNode({ tone: true });
  glyph.classList.toggle('is-playing', Boolean(playing));
  fx.fly(glyph, s.ico, icoTo, { dur, easing: ease });
  mini.querySelectorAll('.player-ring').forEach((r) => fx.animate(r, [{ opacity: 0 }, { opacity: 1 }], { duration: 200, delay: dur * 0.55, fill: 'both' }));
  return dur;
}

/** 迷你 → 条:反向展开;等飞行中的封面基本落位再淡入文字,封面不压字。 */
export function expandMorph(fx, { snap: s, bar, playing, E, M }) {
  const to = rectOf(bar);
  const barR = radiusOf(bar);
  const coverTo = bar.querySelector('.player-cover');
  const artTo = rectOf(coverTo);
  const artRadiusTo = radiusOf(coverTo);   // 圆角从 token 的计算值读(--r-sm),不写死
  const playTo = rectOf(bar.querySelector('.player-play'));
  const icoTo = rectOf(bar.querySelector('.player-play-ico'));
  const dur = M.expandDur;
  const ease = E.expand;
  const shadowEnd = getComputedStyle(bar).boxShadow !== 'none' ? 1 : 0;
  fx.shadow(s.from, to, { dur, easing: ease, radiusTo: barR, endOpacity: shadowEnd });
  fx.box('player-fx-plate', s.from, to, { radiusFrom: s.from.h / 2, radiusTo: barR, dur, easing: ease, below: true });
  fx.clipFollow(bar, to, s.from, to, { radiusFrom: s.from.h / 2, radiusTo: barR, dur, easing: ease });
  const art = fx.box('player-fx-art', s.art, artTo, { radiusFrom: s.art.h / 2, radiusTo: artRadiusTo, dur, easing: ease });
  const copy = artCopy(bar);
  if (copy) art.el.append(copy);
  fx.box('player-fx-core is-tone', s.play, playTo, { radiusFrom: s.play.h / 2, radiusTo: playTo.h / 2, dur, easing: ease });
  const glyph = glyphNode({ tone: true });
  glyph.classList.toggle('is-playing', Boolean(playing));
  fx.fly(glyph, s.ico, icoTo, { dur, easing: ease });
  [bar.querySelector('.player-info-slot'), ...bar.querySelectorAll('.player-skip'), bar.querySelector('.player-seek'),
    bar.querySelector('.player-rate'), bar.querySelector('.player-min'), bar.querySelector('.player-close')]
    .filter(Boolean)
    .forEach((el, i) => {
      fx.animate(el, [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], { duration: 220, delay: dur * 0.5 + i * 18, easing: E.fade, fill: 'both' });
    });
  return dur;
}

/** 换台之前:旧的封面 / 标题副本,以及被点入口的 ▶ 位置(入口马上要重建)。 */
export function snapshotSwap(host, originEl) {
  if (!host || host.hidden) return null;
  const take = (sel) => {
    const el = host.querySelector(sel);
    if (!el) return null;
    return { rect: rectOf(el), node: el.cloneNode(true), className: el.className };
  };
  const icoEl = originEl?.querySelector?.('.listen-ico');
  return {
    info: take('.player-info-slot'),
    art: take('.player-art-slot'),
    ico: icoEl ? visibleRect(icoEl) : null,
  };
}

/** 换台:条不消失,封面与标题竖向轮换(旧的上移淡出、新的晚 70ms 进来);▶ 从被点的入口飞进播放钮。 */
export function swapMorph(fx, { snap: s, host, E, M }) {
  const ticker = (old, slot, dy) => {
    if (!old || !slot) return;
    const r = old.rect;
    const ghost = fx.span(`player-fx-ticker ${old.className}`);
    ghost.append(...old.node.childNodes);
    ghost.style.cssText = `transform:translate(${r.x}px, ${r.y}px);width:${r.w}px;height:${r.h}px`;
    fx.animate(ghost, [{ transform: `translate(${r.x}px, ${r.y}px)`, opacity: 1 }, { transform: `translate(${r.x}px, ${r.y - dy}px)`, opacity: 0 }], { duration: M.tickerOutDur, easing: E.out, fill: 'both' });
    fx.animate(slot, [{ transform: `translateY(${dy}px)`, opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: M.swapDur, delay: M.tickerDelay, easing: E.swap, fill: 'both' });
  };
  ticker(s.info, host.querySelector('.player-info-slot'), 10);
  ticker(s.art, host.querySelector('.player-art-slot'), 10);
  const play = host.querySelector('.player-play');
  if (s.ico && play) {
    const target = rectOf(play.querySelector('.player-play-ico'));
    const glyph = document.createElement('span');
    glyph.className = 'player-fx-glyph is-token';
    glyph.innerHTML = PLAY_SVG;
    fx.fly(glyph, s.ico, target, { dur: M.flyDur, easing: E.fly, fade: [1, 0.55] });
    fx.animate(play, [{ transform: 'none' }, { transform: 'scale(1.12)' }, { transform: 'none' }], { duration: 260, delay: 300, easing: E.fade });
  }
  return M.flyDur;
}

/** 轻量档(软件合成 / 首次形变掉帧):淡入 + 12px 位移,不建底板 / 影子 / clip-path。 */
export function liteEnter(fx, el, E) {
  fx.animate(el, [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], { duration: 240, easing: E.fade, fill: 'both' });
}

/** 减少动效:条直接出现,只有 120ms 淡入。 */
export function reducedEnter(fx, el) {
  fx.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration: 120, fill: 'both' });
}
