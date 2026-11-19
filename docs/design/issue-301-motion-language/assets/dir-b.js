/* B 连续 —— 东西不凭空出现(设计说明与 token 见 dir-b.css 顶部)。
   手法:低频的大转场用 View Transitions 做共享元素(标题、分数、来源、卡面);
        高频或紧跟提示的小动作用 FLIP / 幽灵 / WAAPI,可以被下一次点击打断。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const VT = !!document.startViewTransition;
  const T = {
    move: { duration: 0.42, bounce: 0.12 },    // 位移
    settle: { duration: 0.5, bounce: 0.18 },   // 大变形
    snap: { duration: 0.3, bounce: 0 },        // 小件就位
    pop: { duration: 0.34, bounce: 0.45 },     // 星标、落点
    easeOut: 'cubic-bezier(0.23, 1, 0.32, 1)',
    easeInOut: 'cubic-bezier(0.77, 0, 0.175, 1)',
    drawer: 'cubic-bezier(0.32, 0.72, 0, 1)',
  };
  const VARS = ['--mb-move-ease', '--mb-move-dur', '--mb-settle-ease', '--mb-settle-dur', '--mb-t-ow', '--mb-t-nw', '--mb-t-fr', '--mb-s-ow', '--mb-s-nw', '--mb-s-fr'];
  const sp = (k) => { const s = M.spring(T[k]); return { duration: s.duration, easing: s.easing }; };
  const px = (el) => parseFloat(getComputedStyle(el).fontSize) || 16;
  const inView = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 4 && r.top < innerHeight - 4 && r.right > 0 && r.left < innerWidth;
  };
  const drop = (g) => { if (g && g.parentNode) g.parentNode.removeChild(g); };
  const fadeUp = (els, opt = {}) => {
    const s = sp(opt.spring || 'move'); const d = opt.dist == null ? 14 : opt.dist;
    els.filter(Boolean).forEach((el) => M.animate(el, [{ opacity: 0, transform: `translateY(${d}px)` }, { opacity: 1, transform: 'none' }], { duration: s.duration, easing: s.easing, delay: opt.delay || 0 }));
  };

  /* ── 弹簧位置函数(指示器的两条边各走各的,用得到原函数) ── */
  const sx = (t, dur, bounce) => {
    const z = Math.max(0.05, 1 - bounce); const w0 = (2 * Math.PI) / dur;
    if (z >= 1) return 1 - Math.exp(-w0 * t) * (1 + w0 * t);
    const wd = w0 * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w0 * t) * (Math.cos(wd * t) + ((z * w0) / wd) * Math.sin(wd * t));
  };
  /* 指示器滑动:朝目标的那条边先到(lead),后面的边跟上(trail)——中途被拉长,落定时收拢 */
  const LIQ = 0.56;
  const liquid = (lead, trail) => (p, r) => {
    const N = 28; const kf = [];
    const dl = r.x - p.x; const dr = r.x + r.w - (p.x + p.w); const dt = r.y - p.y; const db = r.y + r.h - (p.y + p.h);
    for (let i = 0; i <= N; i++) {
      const t = (i / N) * LIQ; const L = i === N ? 1 : sx(t, lead, 0); const S = i === N ? 1 : sx(t, trail, 0.12);
      const left = p.x + dl * (dl >= 0 ? S : L); const right = p.x + p.w + dr * (dr >= 0 ? L : S);
      const top = p.y + dt * (dt >= 0 ? S : L); const bot = p.y + p.h + db * (db >= 0 ? L : S);
      kf.push({ transform: `translate(${(left - r.x).toFixed(2)}px, ${(top - r.y).toFixed(2)}px) scale(${Math.max(0.05, (right - left) / r.w).toFixed(4)}, ${Math.max(0.05, (bot - top) / r.h).toFixed(4)})` });
    }
    return kf;
  };
  const G_NEAR = { keyframes: liquid(0.2, 0.4), timing: { duration: LIQ * 1000, easing: 'linear' } };
  const G_FAR = { keyframes: liquid(0.34, 0.42), timing: { duration: LIQ * 1000, easing: 'linear' } };
  const groupKey = (el, pre) => {
    const b = el.querySelector('button'); const d = b ? Object.keys(b.dataset)[0] || 'x' : 'x';
    const where = el.closest('.pop') ? ':pop' : el.closest('.srcbar') ? ':bar' : el.closest('.pane-bar') ? ':pane' : el.closest('#pane') ? ':doc' : '';
    return `${pre}:${d}${where}`;
  };
  function indicators() {
    const rail = $('.rail'); if (rail) M.glide('rail', rail, $('.rail-nav a.on', rail), G_NEAR);
    $$('.seg').forEach((el) => M.glide(groupKey(el, 'seg'), el, $(':scope > button.on', el), G_NEAR));
    $$('.tabs').forEach((el) => M.glide(groupKey(el, 'tabs'), el, $(':scope > button.on', el), G_NEAR));
    $$('.kinds').forEach((el) => M.glide('kinds', el, $(':scope > button.on', el), G_NEAR));
    const sb = $('.srcbar-b'); if (sb) M.glide('src', sb, $(':scope > .src.on', sb), G_FAR);
    const lb = $('.list-b'); if (lb) M.glide('row', lb, $('.row.on', lb), G_FAR);
  }

  /* ── 吸顶栏里的小标题(样式里用 view-timeline 跟着大标题走;不支持时退回滚动事件) ── */
  const SDA = typeof CSS !== 'undefined' && CSS.supports && CSS.supports('animation-timeline: view()');
  function barTitle() {
    const bar = $('#pane .pane-bar'); const h1 = $('#pane .doc h1');
    if (!bar || !h1 || !bar.firstElementChild) return;
    let t = $('.mb-bar-title', bar);
    if (!t) { t = document.createElement('span'); t.className = 'mb-bar-title'; t.setAttribute('aria-hidden', 'true'); bar.firstElementChild.after(t); }
    t.textContent = h1.textContent;
    /* 随滚动走的动画不能跟着「慢放」改速率(那会让它只走到一小段),标成公共层跳过的那一类 */
    if (SDA) requestAnimationFrame(() => bar.getAnimations({ subtree: true }).forEach((a) => { if (a.animationName === 'mb-dock' || a.animationName === 'mb-fade-in') { a.id = 'm-ui'; a.playbackRate = 1; } }));
  }
  function dockByScroll(pane) {
    const t = $('.mb-bar-title', pane); const h1 = $('.doc h1', pane); if (!t || !h1) return;
    const top = pane.getBoundingClientRect().top + 52; const b = h1.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, (top - b.top) / Math.max(1, b.height)));
    t.style.opacity = p.toFixed(3); t.style.transform = `translateY(${((1 - p) * 12).toFixed(1)}px)`;
  }

  /* ── 共享元素:旧端记下字号与宽度,新端据此写出缩放比(样式里按它让两端的字全程一样大) ── */
  let share = {};
  const resetShare = () => { share = {}; ['t', 's'].forEach((k) => { root.style.setProperty(`--mb-${k}-ow`, 'auto'); root.style.setProperty(`--mb-${k}-nw`, 'auto'); root.style.setProperty(`--mb-${k}-fr`, '1'); }); };
  const takeOld = (name, el) => { if (!inView(el)) return false; M.name(el, `mb-${name}`); share[name] = { fs: px(el), w: el.getBoundingClientRect().width }; return true; };
  const takeNew = (name, el, k) => {
    const o = share[name]; if (!o || !inView(el)) return false;
    M.name(el, `mb-${name}`);
    if (k) { root.style.setProperty(`--mb-${k}-ow`, `${o.w}px`); root.style.setProperty(`--mb-${k}-nw`, `${el.getBoundingClientRect().width}px`); root.style.setProperty(`--mb-${k}-fr`, (px(el) / o.fs).toFixed(4)); }
    return true;
  };
  const paneScore = () => $$('#pane .side-blk:not(.minor) .side-score .sc, #pane .ai-scores .sc').find(inView) || null;
  const titleIn = (el) => (el ? (el.matches('h1, h2, h3, h4') ? el : $('.hero-title, h3, h4, :scope > span', el)) : null);

  /* 列表内换一篇:标题 / 分数 / 来源从行里飞到阅读窗,行本身留在原地 */
  function openInPlace(ctx, mutate) {
    resetShare();
    const row = ctx.from === 'list' ? ctx.el : null;
    const title = row ? $('h3', row) : titleIn(ctx.el);
    M.vt(mutate, {
      type: 'mb-open',
      before() {
        M.name($('#pane'), 'mb-pane');
        takeOld('title', title);
        if (row) { takeOld('score', $(':scope > .sc', row)); takeOld('logo', $('.row-meta .lg', row)); takeOld('src', $('.row-src', row)); }
      },
      mid() {
        M.unname();
        const pane = $('#pane'); if (!pane) return;
        M.name(pane, 'mb-pane');
        takeNew('title', $('.doc h1', pane), 't');
        takeNew('score', paneScore(), 's');
        takeNew('logo', $('.pane-src .lg', pane));
        takeNew('src', $('.pane-src > span:last-child', pane));
        fadeUp([...$$('.doc-h > :not(h1)', pane), ...$$('.doc > :not(.doc-h)', pane)], { delay: 40 });
      },
    });
  }
  /* 换页(早报 / 榜单 / 事件 ⇄ 正文):整页沿横轴走,标题是同一个 */
  function across(ctx, mutate, type, oldTitle, newTitle) {
    resetShare();
    M.vt(mutate, {
      type,
      before() { M.name($('#stage'), 'mb-stage'); takeOld('title', oldTitle); },
      mid() { M.unname(); M.name($('#stage'), 'mb-stage'); takeNew('title', newTitle(), 't'); },
    });
  }

  /* ── 浮层:从触发钮的位置长出来 ──
     开窗落在浮层靠近触发钮的那个角上、大小等于触发钮,同时把整块平移到钮上;展开到整块。 */
  function windowOf(pop, trig) {
    const w = Math.min(trig.width, pop.width); const h = Math.min(trig.height, pop.height);
    const right = trig.left + trig.width / 2 > pop.left + pop.width / 2; const bottom = trig.top + trig.height / 2 > pop.top + pop.height / 2;
    const ix = pop.width - w; const iy = pop.height - h;
    return {
      clip: `inset(${bottom ? iy : 0}px ${right ? 0 : ix}px ${bottom ? 0 : iy}px ${right ? ix : 0}px round 10px)`,
      move: `translate(${(right ? trig.right - pop.right : trig.left - pop.left).toFixed(1)}px, ${(bottom ? trig.bottom - pop.bottom : trig.top - pop.top).toFixed(1)}px)`,
    };
  }
  const FULL = 'inset(-48px -48px -48px -48px round 14px)';   // 留出阴影
  const TRIGGER = { cal: '[data-act="cal"]', menu: '.pane-bar [data-act="menu"]', srcpop: '.scope[data-act="srcpop"]' };
  function growFrom(el, trig) {
    const w = windowOf(el.getBoundingClientRect(), trig); const s = sp('snap');
    M.animate(el, [{ clipPath: w.clip, transform: w.move }, { clipPath: FULL, transform: 'none' }], { duration: s.duration, easing: s.easing });
    [...el.children].forEach((c) => M.animate(c, [{ opacity: 0 }, { opacity: 1 }], { duration: 160, delay: 70, easing: 'linear' }));
  }
  function shrinkTo(el, trig) {
    const w = windowOf(el.getBoundingClientRect(), trig);
    const g = M.ghost(el);
    const a = M.animate(g, [{ clipPath: FULL, transform: 'none', opacity: 1 }, { clipPath: w.clip, transform: w.move, opacity: 1, offset: 0.8 }, { clipPath: w.clip, transform: w.move, opacity: 0 }], { duration: 200, easing: T.easeOut, fill: 'forwards' });
    const inner = g.querySelector('.pop'); if (inner) [...inner.children].forEach((c) => M.animate(c, [{ opacity: 1 }, { opacity: 0 }], { duration: 90, easing: 'linear', fill: 'forwards' }));
    if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g);
  }

  /* ── 问答面板:以导轨上的问答钮为圆心展开 / 收回;星标是同一枚,飞过去再飞回来 ── */
  function circleOf(box, b) {
    const cx = b.left + b.width / 2 - box.left; const cy = b.top + b.height / 2 - box.top;
    const nx = Math.max(0, Math.min(box.width, cx)); const ny = Math.max(0, Math.min(box.height, cy));
    const r0 = Math.max(0, Math.hypot(cx - nx, cy - ny) - 6);
    const r1 = Math.max(Math.hypot(cx, cy), Math.hypot(cx - box.width, cy), Math.hypot(cx, cy - box.height), Math.hypot(cx - box.width, cy - box.height)) + 90;
    const at = `at ${cx.toFixed(1)}px ${cy.toFixed(1)}px`;
    return [`circle(${r0.toFixed(1)}px ${at})`, `circle(${r1.toFixed(1)}px ${at})`];
  }
  function sparkFly(fromEl, toEl, opt = {}) {
    if (!fromEl || !toEl) return;
    const a = fromEl.getBoundingClientRect(); const b = toEl.getBoundingClientRect();
    const f = fromEl.cloneNode(true); f.classList.add('mb-fly'); f.style.cssText = `width:${a.width}px;height:${a.height}px;margin:0;display:block;`;
    document.body.appendChild(f);
    const sc = b.width / Math.max(1, a.width); const s = sp('move');
    const an = M.animate(f, [{ transform: `translate(${a.left}px, ${a.top}px) scale(1)`, opacity: 1 }, { transform: `translate(${b.left}px, ${b.top}px) scale(${sc.toFixed(3)})`, opacity: 1 }], { duration: s.duration * 0.8, easing: s.easing, fill: 'forwards' });
    const land = () => { drop(f); if (opt.land) opt.land(); };
    if (an) an.finished.then(land, land); else land();
    return f;
  }

  /* ── 列表 FLIP:留下的滑到新位置,新来的淡入,走的原地淡出(只处理看得见的) ── */
  function flipList(sel, scopeSel, mutate, key) {
    const sc = $(scopeSel); const clip = sc ? sc.getBoundingClientRect() : null; const s = sp('move');
    M.flip(sel, mutate, {
      scope: scopeSel, key,
      move: () => ({ duration: s.duration, easing: s.easing }),
      enter: (el) => M.animate(el, [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 240, delay: 70, easing: T.easeOut }),
      exit: (g, r) => {
        if (!clip || r.bottom <= clip.top || r.top >= clip.bottom) { drop(g); return; }
        g.style.clipPath = `inset(${Math.max(0, clip.top - r.top)}px 0 ${Math.max(0, r.bottom - clip.bottom)}px 0)`;
        const a = M.animate(g, [{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(0.98)' }], { duration: 150, easing: 'linear', fill: 'forwards' });
        if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g);
      },
    });
  }
  /* 原位换状态(标读 / 撤销):旧样子叠在上面淡掉,行不挪窝 */
  function crossfadeRows(mutate, pick) {
    const lb = $('.list-b'); const clip = lb ? lb.getBoundingClientRect() : null;
    const ghosts = [];
    if (clip) $$('.row[data-id]', lb).filter(pick).forEach((row) => {
      const r = row.getBoundingClientRect(); if (r.bottom <= clip.top || r.top >= clip.bottom || ghosts.length >= 14) return;
      const g = M.ghost(row); g.style.clipPath = `inset(${Math.max(0, clip.top - r.top)}px 0 ${Math.max(0, r.bottom - clip.bottom)}px 0)`; ghosts.push(g);
    });
    mutate();
    ghosts.forEach((g) => { const a = M.animate(g, [{ opacity: 1 }, { opacity: 0 }], { duration: 220, easing: 'linear', fill: 'forwards' }); if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g); });
  }

  M.register('b', {
    name: '连续',
    init() {
      const mv = M.spring(T.move); const st = M.spring(T.settle);
      root.style.setProperty('--mb-move-ease', mv.easing); root.style.setProperty('--mb-move-dur', `${mv.duration}ms`);
      root.style.setProperty('--mb-settle-ease', st.easing); root.style.setProperty('--mb-settle-dur', `${st.duration}ms`);
    },
    dispose() {
      VARS.forEach((v) => root.style.removeProperty(v));
      $$('.mb-bar-title, .mb-fly, .m-ind').forEach(drop);
      share = {};
    },
    rendered() { indicators(); barTitle(); },
    scroll(el) { if (!SDA && el.id === 'pane') dockByScroll(el); },

    on: {
      /* 到场:三拍,间隔递减;每拍上升 16px。别的视图只有工作区一拍 */
      boot(ctx, mutate) {
        mutate();
        M.animate($('.rail'), [{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: 'linear' });
        if (ctx.view !== 'brief') { fadeUp([$('#stage')], { spring: 'settle', dist: 12 }); return; }
        fadeUp([$('.bf-top'), $('.hero-main')], { spring: 'settle', dist: 16 });
        fadeUp([$('.hero-side')], { spring: 'settle', dist: 16, delay: 90 });
        fadeUp([$('.toc'), ...$$('.brief .sec')], { spring: 'settle', dist: 16, delay: 150 });
        M.animate($('.hero-glow'), [{ opacity: 0 }, { opacity: 1 }], { duration: 600, easing: 'linear' });
      },

      nav(ctx, mutate) {
        if (ctx.key || !VT) return mutate();
        const type = ctx.dy > 0 ? 'mb-nav-down' : ctx.dy < 0 ? 'mb-nav-up' : 'mb-nav-same';
        M.vt(mutate, { type, before: () => M.name($('#stage'), 'mb-stage'), mid: () => { M.unname(); M.name($('#stage'), 'mb-stage'); } });
      },

      open(ctx, mutate) {
        if (ctx.key || !VT) return mutate();
        if (ctx.from === 'list' || ctx.from === 'go') return openInPlace(ctx, mutate);
        /* 从问答的引用进来:面板先收回钮里(幽灵要在起名之前做,副本才不会带上同名) */
        if (ctx.inQa) { const qa = $('.qa'); const btn = $('.rail [data-act="qa"]'); if (qa && btn) { const g = M.ghost(qa); const c = circleOf(qa.getBoundingClientRect(), btn.getBoundingClientRect()); const a = M.animate(g, [{ clipPath: c[1] }, { clipPath: c[0] }], { duration: 240, easing: T.easeOut, fill: 'forwards' }); if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g); } }
        across(ctx, mutate, 'mb-fwd', titleIn(ctx.el), () => $('#pane .doc h1'));
      },
      back(ctx, mutate) {
        if (ctx.key || !VT) return mutate();
        across(ctx, mutate, 'mb-back', $('#pane .doc h1'), () => titleIn($(`#stage [data-open="${CSS.escape(String(ctx.id))}"]`)));
      },

      /* 榜单卡展开成事件页;返回时收回卡里(卡不在视口里就按横轴退回) */
      event(ctx, mutate) {
        if (ctx.key || !VT) return mutate();
        resetShare();
        const card = ctx.open && ctx.el ? ctx.el.closest('.rk') : null;
        if (ctx.open && !card) return across(ctx, mutate, 'mb-fwd', null, () => $('.ev-h h1'));
        if (ctx.open) {
          M.vt(mutate, {
            type: 'mb-ev-open',
            before() { M.name(card, 'mb-card'); takeOld('title', $('h3', card)); },
            mid() { M.unname(); M.name($('#stage .page'), 'mb-card'); takeNew('title', $('.ev-h h1'), 't'); fadeUp([$('.ev-top'), ...$$('.ev-h > :not(h1)'), $('.ev-grid')], { spring: 'settle', dist: 12, delay: 60 }); },
          });
          return;
        }
        const page = $('#stage .page'); const h1 = $('.ev-h h1');
        let landed = false;
        M.vt(mutate, {
          type: 'mb-ev-back',
          before() { M.name(page, 'mb-card'); takeOld('title', h1); },
          mid() {
            M.unname();
            const c = $(`.rk[data-event="${CSS.escape(String(ctx.id))}"]`);
            if (inView(c)) { landed = true; M.name(c, 'mb-card'); takeNew('title', $('h3', c), 't'); } else root.dataset.vt = 'mb-back', M.name($('#stage'), 'mb-stage');
          },
        });
        void landed;
      },

      /* 收起 / 展开走样式里的过渡;从「固定为来源栏」展开时,浮层变形成来源栏 */
      fold(ctx, mutate) {
        const pop = !ctx.fold && $('.pop.srcpop');
        if (ctx.key || !VT || !pop) return mutate();
        M.vt(mutate, {
          type: 'mb-unfold',
          before() { M.name(pop, 'mb-srcbar'); M.name($('.list'), 'mb-list'); M.name($('#pane'), 'mb-pane'); },
          mid() { M.unname(); M.name($('.srcbar'), 'mb-srcbar'); M.name($('.list'), 'mb-list'); M.name($('#pane'), 'mb-pane'); },
        });
      },

      pop(ctx, mutate) {
        if (ctx.key) return mutate();
        if (ctx.open) {
          const trig = ctx.rect;
          mutate();
          const el = $(ctx.sel); if (el && trig) growFrom(el, trig);
          return;
        }
        const el = $(ctx.sel); const trig = $(TRIGGER[ctx.name] || 'x');
        if (el && trig) shrinkTo(el, trig.getBoundingClientRect());
        mutate();
      },

      qa(ctx, mutate) {
        if (ctx.key) return mutate();
        const btn = $('.rail [data-act="qa"]'); const b = btn ? btn.getBoundingClientRect() : null;
        if (ctx.open) {
          mutate();
          const el = $('.qa'); if (!el || !b) return;
          const c = circleOf(el.getBoundingClientRect(), b);
          M.animate(el, [{ clipPath: c[0], transform: 'translateX(-18px)' }, { clipPath: c[1], transform: 'none' }], { duration: 380, easing: T.drawer });
          const mark = $('.qa-h .spark', el);
          if (mark) { M.animate(mark, [{ opacity: 0 }, { opacity: 0 }], { duration: sp('move').duration * 0.78, easing: 'linear' }); sparkFly($('.spark svg', btn) || $('.spark', btn), $('svg', mark) || mark); }
          return;
        }
        const el = $('.qa');
        if (el && b) {
          const c = circleOf(el.getBoundingClientRect(), b); const mark = $('.qa-h .spark svg', el);
          if (mark) sparkFly(mark, $('.spark svg', btn) || btn, {});
          const g = M.ghost(el);
          const a = M.animate(g, [{ clipPath: c[1], transform: 'none' }, { clipPath: c[0], transform: 'translateX(-18px)' }], { duration: 240, easing: T.easeOut, fill: 'forwards' });
          if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g);
        }
        mutate();
      },
      /* 点建议问题:那一条飞上去变成提问气泡;回答在下面分拍升起 */
      qaask(ctx, mutate) {
        if (ctx.key) return mutate();
        const sug = ctx.el ? ctx.el.closest('.qa-sug button') : null; const from = sug ? sug.getBoundingClientRect() : null; const g = sug ? M.ghost(sug) : null;
        mutate();
        const q = $('.qa-q'); const s = sp('move');
        if (q && from) {
          const to = q.getBoundingClientRect(); const dx = to.left - from.left; const dy = to.top - from.top;
          const a = M.animate(g, [{ transform: 'none', opacity: 1 }, { transform: `translate(${dx}px, ${dy}px)`, opacity: 0 }], { duration: s.duration, easing: s.easing, fill: 'forwards' });
          if (a) { M.animate(g, [{ opacity: 1 }, { opacity: 0 }], { duration: 150, easing: 'linear', fill: 'forwards' }); a.finished.then(() => drop(g), () => drop(g)); } else drop(g);
          M.animate(q, [{ transform: `translate(${-dx}px, ${-dy}px)` }, { transform: 'none' }], { duration: s.duration, easing: s.easing });
          M.animate(q, [{ opacity: 0 }, { opacity: 1 }], { duration: 150, easing: 'linear' });
        } else { drop(g); if (q) fadeUp([q], { dist: 10 }); }
        const parts = $$('.qa-a > *'); const gaps = [140, 230, 300, 350];
        parts.forEach((el, i) => fadeUp([el], { dist: 12, delay: gaps[Math.min(i, gaps.length - 1)] }));
      },

      /* 「依据」从分数钮向下展开:卡面跟着长高,下面的内容被推下去(窄窗才有这颗钮) */
      why(ctx, mutate) {
        if (ctx.key) return mutate();
        const old = $('#pane .ai-why'); const hOld = old ? old.getBoundingClientRect().height + 12 : 0;
        mutate();
        const ai = $('#pane .ai'); if (!ai) return;
        const el = $('.ai-why', ai); const s = sp('snap');
        const after = (n) => { const out = []; for (let x = n.nextElementSibling; x; x = x.nextElementSibling) out.push(x); return out; };
        if (ctx.open && el && el.getBoundingClientRect().height) {
          const h = el.getBoundingClientRect().height + 12;
          M.animate(ai, [{ clipPath: `inset(0 0 ${h}px 0 round 14px)` }, { clipPath: 'inset(0 0 0 0 round 14px)' }], { duration: s.duration, easing: s.easing });
          M.animate(el, [{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: 200, delay: 50, easing: T.easeOut });
          [...after(el), ...after(ai)].forEach((x) => M.animate(x, [{ transform: `translateY(${-h}px)` }, { transform: 'none' }], { duration: s.duration, easing: s.easing }));
        } else if (!ctx.open && hOld > 12) {
          after(ai).forEach((x) => M.animate(x, [{ transform: `translateY(${hOld}px)` }, { transform: 'none' }], { duration: 200, easing: T.easeOut }));
        }
      },

      filter(ctx, mutate) {
        if (ctx.key) return mutate();
        const g = ctx.group;
        if (g === 'unread' || g === 'kind' || g === 'favonly' || g === 'src' || g === 'scope') {
          const lb = $('.list-b'); const keeps = g === 'unread' || g === 'favonly' || (lb && lb.scrollTop < 2);
          if (lb && keeps) return flipList('.row[data-id], .day', '.list-b', mutate, (el) => el.dataset.id || `day:${el.textContent}`);
          mutate(); fadeUp([$('.list-b')], { dist: 0 }); return;
        }
        if (g === 'rankwin') return flipList('.rk[data-event]', '.rk-list', mutate, (el) => el.dataset.event);
        if (g === 'discshape') { const pg = $('.page.disc'); if (pg && pg.scrollTop < 2) return flipList('.sc-card[data-preview]', '.page.disc', mutate, (el) => el.dataset.preview); }
        mutate();
        if (g === 'ranktab') fadeUp([$('.rk-list')], { dist: 12 });
        else if (g === 'disctab' || g === 'discshape') fadeUp($$('.page.disc .dg, .page.disc .colls, .page.disc .wrap > .note, .page.disc .wrap > .empty'), { dist: 12 });
        else if (g === 'podtab') fadeUp([$('#pane .prose')], { dist: 8 });
      },

      /* 以下几类后面紧跟一次 toast,都不用 View Transitions */
      fav(ctx, mutate) {
        mutate(); if (ctx.key) return;
        const el = M.refind(ctx); const ic = el ? $('.ic', el) : null; if (!ic) return;
        if (ctx.on) M.animate(ic, [{ transform: 'scale(0.6)' }, { transform: 'scale(1)' }], { spring: T.pop });
        else M.animate(ic, [{ transform: 'scale(1.16)' }, { transform: 'scale(1)' }], { duration: 180, easing: T.easeOut });
      },
      markall(ctx, mutate) { if (ctx.key) return mutate(); crossfadeRows(mutate, (row) => (ctx.undo ? row.classList.contains('read') : !row.classList.contains('read'))); },
      unread(ctx, mutate) { if (ctx.key) return mutate(); crossfadeRows(mutate, (row) => row.dataset.id === String(ctx.id)); },
      /* 订阅:来源徽标沿弧线飞到导轨上对应形态的图标,落点弹一下;撤销 / 退订不飞 */
      sub(ctx, mutate) {
        const card = ctx.el ? ctx.el.closest('.sc-card') : null; const lg = card ? $('.lg', card) : null;
        const fly = ctx.on && !ctx.undo && !ctx.key && lg ? lg.cloneNode(true) : null; const from = lg ? lg.getBoundingClientRect() : null;
        mutate();
        if (ctx.key) return;
        const btn = M.refind(ctx);
        if (btn) M.animate(btn, [{ transform: 'scale(0.9)', opacity: 0.4 }, { transform: 'scale(1)', opacity: 1 }], { spring: { duration: 0.3, bounce: 0.3 } });
        if (!fly) return;
        const src = window.D && window.D.sources ? window.D.sources[ctx.id] : null;
        const target = $(`.rail-nav [data-nav="${src ? src.shape : 'article'}"] .ic`); if (!target) return;
        const to = target.getBoundingClientRect();
        fly.classList.add('mb-fly'); fly.style.setProperty('--s', `${from.width}px`); document.body.appendChild(fly);
        const x0 = from.left; const y0 = from.top; const x2 = to.left + to.width / 2 - from.width / 2; const y2 = to.top + to.height / 2 - from.height / 2;
        const dist = Math.hypot(x2 - x0, y2 - y0); const x1 = (x0 + x2) / 2; const y1 = Math.min(y0, y2) - Math.min(140, dist * 0.22);
        const N = 30; const kf = [];
        for (let i = 0; i <= N; i++) {
          const k = i / N; const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
          const x = (1 - e) * (1 - e) * x0 + 2 * (1 - e) * e * x1 + e * e * x2; const y = (1 - e) * (1 - e) * y0 + 2 * (1 - e) * e * y1 + e * e * y2;
          kf.push({ transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${(1 - 0.45 * e).toFixed(3)})`, opacity: k > 0.9 ? (1 - k) * 10 : 1 });
        }
        const a = M.animate(fly, kf, { duration: 520, easing: 'linear', fill: 'forwards' });
        const land = () => { drop(fly); const ic = $(`.rail-nav [data-nav="${src ? src.shape : 'article'}"] .ic`); if (ic) M.animate(ic, [{ transform: 'scale(1.32)' }, { transform: 'scale(1)' }], { spring: T.pop }); };
        if (a) a.finished.then(land, () => drop(fly)); else land();
      },
      toast(ctx, mutate) {
        const old = $('.toast');
        if (ctx.phase === 'out') { M.exit(old, [{ transform: 'translateY(0)', opacity: 1 }, { transform: 'translateY(18px)', opacity: 0 }], { duration: 180, easing: T.easeOut }); return mutate(); }
        if (old) M.exit(old, [{ transform: 'scale(1)', opacity: 1 }, { transform: 'translateY(6px) scale(0.94)', opacity: 0 }], { duration: 140, easing: T.easeOut });
        mutate();
        M.animate($('.toast'), [{ transform: 'translate(-50%, 22px) scale(0.97)', opacity: 0 }, { transform: 'translate(-50%, 0) scale(1)', opacity: 1 }], { spring: { duration: 0.36, bounce: 0.25 } });
      },

      /* 明暗:以主题钮为圆心展开 */
      theme(ctx, mutate) {
        if (ctx.key || !VT) return mutate();
        const r = ctx.rect; const x = r ? r.left + r.width / 2 : innerWidth / 2; const y = r ? r.top + r.height / 2 : innerHeight / 2;
        const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y)) + 8;
        M.vt(mutate, {
          type: 'mb-theme',
          ready() {
            const a = root.animate({ clipPath: [`circle(14px at ${x}px ${y}px)`, `circle(${R}px at ${x}px ${y}px)`] }, { duration: 520, easing: T.easeInOut, pseudoElement: '::view-transition-new(root)', fill: 'both' });
            a.playbackRate = M.speed;
          },
        });
      },
    },

    tokens: [
      { name: '--mb-move', value: 'spring(0.42s, bounce 0.12) ≈ 落定 530ms', use: '位移:共享元素、切视图、列表行、提问气泡' },
      { name: '--mb-settle', value: 'spring(0.50s, bounce 0.18) ≈ 落定 640ms', use: '大变形:卡面展开成页、到场三拍' },
      { name: '--mb-snap', value: 'spring(0.30s, bounce 0) ≈ 落定 440ms', use: '容器变形:浮层长出、依据展开' },
      { name: '--mb-pop', value: 'spring(0.34s, bounce 0.45)', use: '只给星标与飞行件的落点' },
      { name: '--mb-fx', value: '180ms cubic-bezier(0.23, 1, 0.32, 1)', use: '明度:淡入淡出,不带位移' },
      { name: '--mb-press', value: '120ms → scale 0.97(整行 / 整卡 0.985)', use: '按下' },
      { name: '--mb-ease-drawer', value: '360ms cubic-bezier(0.32, 0.72, 0, 1)', use: '来源栏、问答面板展开' },
      { name: '--mb-ease-in-out', value: '520ms cubic-bezier(0.77, 0, 0.175, 1)', use: '明暗的圆形展开' },
    ],
    notes: {
      press: '按下 0.97(整行 0.985)120ms,松手 220ms',
      ind: '指示器前缘 0.2s 先到、后缘 0.4s 跟上;行 FLIP',
      pop: '从触发钮长出 ≈ 300ms;收回钮里 200ms',
      open: '标题 / 分数 / 来源共享 ≈ 380ms;键盘不动',
      nav: '沿导轨方向 28px 升入 ≈ 320ms;旧的退 170ms',
      fold: '整块滑到导轨下 360ms;浮层变形成来源栏',
      qa: '自问答钮圆形展开 380ms、收回 240ms;建议变气泡',
      brief: '三拍各升 16px,间隔 90 / 60ms',
      ai: '分数是从行里飞来的那一个;依据向下展开',
      toast: '自下弹入(bounce 0.25),原路退 180ms;标读原位淡换',
      theme: '以主题钮为圆心展开 520ms',
      scroll: '大标题随滚动停靠进吸顶栏,可逆',
      event: '卡面展开成页 ≈ 480ms,标题共享;返回收回卡里',
      sub: '徽标沿弧线飞到导轨图标 520ms,落点弹一下',
    },
  });
})();
