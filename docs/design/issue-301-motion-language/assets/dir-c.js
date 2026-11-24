/* C 光 —— 东西不位移,而是亮起、对上焦、熄灭(取值与护栏见 dir-c.css 顶部)。
   这里做四件事:① 把指针位置写到悬停元素上(光标照明);② 每次重画后把光条指示器摆到选中项;
   ③ 各类交互的对焦 / 点亮编排;④ 三个签名时刻:早报「破晓」、问答的玻璃面板与极光、明暗「晨昏线」。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const FOCUS = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
  const LIT = '.row, .bi, .rk, .sc-card, .tl-i[data-open], .src, .btn, .qa-sug button, .qa-src button, .menu button, .pop-f button, .doc-nav button';
  const state = () => (window.__proto ? window.__proto.state : {});
  const dark = () => root.dataset.theme === 'dark';
  const accent = (a) => `rgba(${getComputedStyle(root).getPropertyValue('--dorami-accent-rgb').trim()}, ${a})`;
  const cssv = (n) => getComputedStyle(root).getPropertyValue(n).trim();
  const drop = (el) => { if (el && el.parentNode) el.parentNode.removeChild(el); };

  let instant = false;        // 键盘路径:这一次重画里指示器不滑、压暗不过渡
  let raf = 0; let lastMove = null; let io = null; let watched = null; let qaSeq = 0;
  const timers = new Set();
  const later = (ms, fn) => { const id = M.after(ms, () => { timers.delete(id); fn(); }); timers.add(id); return id; };

  /* ── 键盘路径:直接改,连样式表里的过渡也压掉一帧 ── */
  function now(mutate) {
    instant = true; root.classList.add('mc-now');
    try { mutate(); } finally { instant = false; }
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('mc-now')));
  }

  /* ── ① 光标照明 ── */
  function light(x, y, target) {
    let el = target && target.closest ? target.closest(LIT) : null; let n = 0;
    while (el && n < 3) {
      const r = el.getBoundingClientRect();
      const k = r.height ? Math.min(1, Math.max(0, (y - r.top) / r.height)) : 0.5;
      el.style.setProperty('--mx', `${(x - r.left).toFixed(1)}px`); el.style.setProperty('--my', `${(y - r.top).toFixed(1)}px`);
      el.style.setProperty('--mt', (1 - k).toFixed(2)); el.style.setProperty('--mb', k.toFixed(2));
      el = el.parentElement ? el.parentElement.closest(LIT) : null; n++;
    }
  }
  const onMove = (e) => {
    lastMove = e;
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; const ev = lastMove; if (ev) light(ev.clientX, ev.clientY, ev.target); });
  };
  /* 重画之后指针没动,新画出来的元素也要拿到指针位置 */
  function relight() {
    const { x, y } = M.pointer; if (x < 0) return;
    const el = document.elementFromPoint(x, y); if (el) light(x, y, el);
  }
  const onVisible = () => root.classList.toggle('mc-hidden', document.hidden);

  /* ── ② 指示器:一条光。滑动时先拉长到盖住起止两处并降到半透明,再收到新位置 ── */
  const streak = (p, r) => {
    const ux = Math.min(p.x, r.x); const uy = Math.min(p.y, r.y);
    const uw = Math.max(p.x + p.w, r.x + r.w) - ux; const uh = Math.max(p.y + p.h, r.y + r.h) - uy;
    return [
      { transform: `translate(${p.x - r.x}px, ${p.y - r.y}px) scale(${p.w / r.w}, ${p.h / r.h})`, opacity: 1 },
      { transform: `translate(${ux - r.x}px, ${uy - r.y}px) scale(${uw / r.w}, ${uh / r.h})`, opacity: 0.5, offset: 0.42 },
      { transform: 'none', opacity: 1 },
    ];
  };
  const where = (el) => (['.pop', '.srcbar', '.pane-bar', '.doc', '.list', '.rank', '.disc', '.social', '.qa'].find((s) => el.closest(s)) || '.x').slice(1);
  function indicators() {
    const opt = { animate: !instant, keyframes: streak, timing: { duration: 320, easing: FOCUS } };
    const rail = $('.rail'); if (rail) M.glide('c-rail', rail, $('.rail-nav a.on', rail), opt);
    const sb = $('.srcbar-b'); if (sb) M.glide('c-src', sb, $('.src.on', sb), opt);
    $$('.seg').forEach((s) => M.glide(`c-seg-${where(s)}`, s, $('button.on', s), opt));
    $$('.tabs').forEach((s) => M.glide(`c-tabs-${where(s)}`, s, $('button.on', s), opt));
    $$('.kinds').forEach((s) => M.glide(`c-kinds-${where(s)}`, s, $('button.on', s), opt));
  }

  /* ── 压暗:浮层 / 问答面板下面的工作区退后一步 ── */
  function scrim(k, z) {
    const stage = $('#app > .stage'); if (!stage) return;
    let s = $(`:scope > .mc-scrim[data-k="${k}"]:not(.off)`, stage);
    if (!s) { s = document.createElement('i'); s.className = 'mc-scrim'; s.dataset.k = k; s.setAttribute('aria-hidden', 'true'); stage.appendChild(s); }
    if (z != null) s.style.zIndex = z;
    void s.offsetWidth; s.classList.add('on');
  }
  function dropScrim(k, hard) {
    $$(`#app > .stage > .mc-scrim[data-k="${k}"]:not(.off)`).forEach((s) => {
      if (hard || instant) { drop(s); return; }
      s.classList.add('off'); s.classList.remove('on');
      setTimeout(() => drop(s), 220 / M.speed);
    });
  }
  function syncScrims() {
    if (!$('.pop')) dropScrim('pop');
    if (state().qa) scrim('qa'); else dropScrim('qa');
  }

  /* ── 光的几个基本动作 ── */
  /* 对焦:由虚到实 */
  const focus = (el, opt = {}) => M.animate(el, [{ opacity: opt.from == null ? 0 : opt.from, filter: `blur(${opt.blur || 8}px)` }, { opacity: 1, filter: 'blur(0px)' }], { duration: opt.duration || 260, delay: opt.delay || 0, easing: FOCUS });
  const fade = (el, delay = 0, duration = 240) => M.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration, delay, easing: 'ease-out' });
  /* 点亮:一次辉光(分数、图标)。暗色里连明度一起提,亮色只给 accent 的晕 */
  function ignite(el, delay = 0) {
    if (!el) return;
    const g = dark() ? 0.75 : 0.4; const b = dark() ? 1.55 : 1;
    M.animate(el, [
      { opacity: 0, filter: `brightness(${b}) drop-shadow(0 0 0px ${accent(0)})`, offset: 0 },
      { opacity: 1, filter: `brightness(${b}) drop-shadow(0 0 16px ${accent(g)})`, offset: 0.35 },
      { opacity: 1, filter: `brightness(1) drop-shadow(0 0 0px ${accent(0)})`, offset: 1 },
    ], { duration: 700, delay, easing: 'ease-out' });
  }
  function halo(el, delay = 0) {
    if (!el) return;
    M.animate(el, [{ filter: `drop-shadow(0 0 0px ${accent(0)})`, offset: 0 }, { filter: `drop-shadow(0 0 11px ${accent(dark() ? 0.95 : 0.6)})`, offset: 0.3 }], { duration: 600, delay, easing: 'ease-out' });
  }
  /* 极光沿边一圈(样式表里的 mc-sweep);重复触发时从头再来 */
  function sweep(el) { if (!el) return; el.classList.remove('mc-sweep'); void el.offsetWidth; el.classList.add('mc-sweep'); }

  /* ── 阅读窗到场:标题区对焦,速读卡各句依次对焦,页边与正文只做明度(长正文不加模糊)── */
  function aiArrive(ai, delay) {
    if (!ai) return;
    fade(ai, delay, 160);
    $$('.ai-body p', ai).slice(0, 6).forEach((p, i) => focus(p, { blur: 5, duration: 200, delay: delay + 40 + i * 70 }));
    sweep(ai);
  }
  function focusPane(viaSwap) {
    const p = $('#pane'); if (!p) return;
    focus($('.doc-h', p), { blur: 10, duration: 280, from: viaSwap ? 0.5 : 0 });
    $$('.doc > :not(.doc-h):not(.ai)', p).forEach((el) => fade(el, 60, 280));
    aiArrive($('.ai', p), 60);
    ignite($('.side-blk:not(.minor) .side-score .sc', p) || $('.ai-scores .sc', p), 160);
  }
  /* 整屏转场之后,新视图里最要紧的那一块再对一次焦(有边界的小块) */
  function lead() {
    const el = $('.hero-title') || $('#pane .doc-h') || $('.ev-h h1') || $('.pg-h h1');
    if (el) M.animate(el, [{ filter: 'blur(8px)', offset: 0 }, { filter: 'blur(0px)', offset: 1 }], { duration: 260, delay: 60, easing: FOCUS });
  }
  const swap = (mutate, after) => M.vt(mutate, { type: 'c-swap', mid: after });

  /* ── ④ 早报「破晓」:光晕从暗处绽开 → 眉题的点先亮 → 标题逐行对焦 → 分数点亮;目录与各节 700ms 内一起亮起 ── */
  function dawn() {
    const hero = $('.hero'); if (!hero) return;
    const glow = $('.hero-glow', hero); const title = $('.hero-title', hero); const kick = $('.kick', hero);
    M.animate(glow, [{ opacity: 0, transform: 'scale(0.5)', offset: 0 }, { opacity: 1, transform: 'scale(1.02)', offset: 0.72 }, { opacity: 0.8, transform: 'scale(1)', offset: 1 }], { duration: 1400, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' });
    if (kick) {
      fade(kick, 0, 320);
      M.animate(kick, [{ boxShadow: `0 0 0 0px ${accent(0)}`, transform: 'scale(0.3)', offset: 0 }, { boxShadow: `0 0 0 7px ${accent(dark() ? 0.3 : 0.2)}, 0 0 14px 2px ${accent(dark() ? 0.7 : 0.35)}`, transform: 'scale(1.2)', offset: 0.4 }, { boxShadow: `0 0 0 0px ${accent(0)}`, transform: 'scale(1)', offset: 1 }], { duration: 900, pseudoElement: '::before', easing: 'ease-out' });
    }
    const lines = M.splitLines(title);
    lines.forEach((ln, i) => focus(ln, { blur: 14, duration: 560, delay: 120 + i * 90 }));
    later(120 + lines.length * 90 + 620, () => M.unsplit(title));
    $$('.hero-orig, .hero-by, .hero-sum, .hero-act', hero).forEach((el) => fade(el, 300, 420));
    fade($('.hero-side', hero), 380, 420);
    ignite($('.hero-score .sc', hero), 560);
    $$('.toc, .sec').forEach((el) => fade(el, 200, 500));
  }
  /* 其它视图的第一次到场:整块淡入,最要紧的一块对焦 */
  function arrive() {
    fade($('#stage'), 0, 240); lead();
    const p = $('#pane'); if (p) { aiArrive($('.ai', p), 80); ignite($('.side-blk:not(.minor) .side-score .sc', p) || $('.ai-scores .sc', p), 200); }
  }

  /* ── 问答:先一行流光的「正在检索」,回答逐段由虚到实,引用角标依次亮起、对应来源条跟着亮 ── */
  function answer() {
    const b = $('.qa-b'); const a = b && $('.qa-a', b); if (!a) return;
    const seq = ++qaSeq; const live = () => seq === qaSeq && a.isConnected;
    a.classList.add('mc-wait');
    focus($('.qa-q', b), { blur: 4, duration: 200 });
    const think = document.createElement('div'); think.className = 'mc-think'; think.textContent = '正在检索…'; a.before(think);
    fade(think, 120, 180);
    sweep($('.qa'));
    later(900, () => {
      drop(think); a.classList.remove('mc-wait');
      if (!live()) return;
      const ps = $$(':scope > p:not(.qa-demo)', a); const src = $('.qa-src', a);
      ps.forEach((p, i) => focus(p, { blur: 6, duration: 220, delay: i * 140 }));
      const t1 = ps.length * 140 + 60;
      fade(src, t1, 220); fade($('.qa-demo', a), t1, 220);
      const chips = src ? $$('button', src) : [];
      $$('sup', a).forEach((s, i) => {
        const d = t1 + 180 + i * 150;
        M.animate(s, [{ boxShadow: `0 0 0 0px ${accent(0)}`, offset: 0 }, { boxShadow: `0 0 0 2px ${accent(0.4)}, 0 0 12px 2px ${accent(dark() ? 0.7 : 0.4)}`, backgroundColor: accent(dark() ? 0.42 : 0.24), offset: 0.3 }], { duration: 620, delay: d, easing: 'ease-out' });
        M.animate(chips[i], [{ backgroundColor: accent(0), offset: 0 }, { backgroundColor: accent(dark() ? 0.2 : 0.12), offset: 0.3 }], { duration: 760, delay: d, easing: 'ease-out' });
      });
    });
  }

  /* ── 事件页:时间线的节点自上而下依次点亮(跑道灯)── */
  function runway() {
    $$('.tl-i').slice(0, 8).forEach((li, i) => {
      const d = 240 + i * 40;
      M.animate(li, [{ opacity: 0.25, boxShadow: `0 0 0 1.5px ${accent(0.25)}`, offset: 0 }, { opacity: 1, boxShadow: `0 0 0 1.5px ${accent(1)}, 0 0 12px 3px ${accent(dark() ? 0.75 : 0.45)}`, offset: 0.4 }], { duration: 560, delay: d, pseudoElement: '::before', easing: 'ease-out' });
      M.animate(li, [{ opacity: 0.4, offset: 0 }, { opacity: 1, offset: 1 }], { duration: 320, delay: d, easing: 'ease-out' });
    });
  }

  /* ── 收藏:星标点亮,荡开一圈光环 ── */
  function ring(btn, ic) {
    const b = btn.getBoundingClientRect(); const c = ic.getBoundingClientRect();
    const r = document.createElement('i'); r.className = 'mc-ring'; r.setAttribute('aria-hidden', 'true');
    r.style.left = `${c.left - b.left + c.width / 2}px`; r.style.top = `${c.top - b.top + c.height / 2}px`;
    btn.appendChild(r);
    const a = M.animate(r, [{ transform: 'translate(-50%, -50%) scale(0.5)', opacity: 0.95 }, { transform: 'translate(-50%, -50%) scale(2.5)', opacity: 0 }], { duration: 450, easing: 'cubic-bezier(0.2, 0.7, 0.2, 1)', fill: 'both' });
    if (a) a.finished.then(() => drop(r), () => drop(r)); else drop(r);
  }

  M.register('c', {
    name: '光',
    init() {
      document.addEventListener('pointermove', onMove, true);
      document.addEventListener('visibilitychange', onVisible);
      onVisible();
    },
    dispose() {
      document.removeEventListener('pointermove', onMove, true);
      document.removeEventListener('visibilitychange', onVisible);
      if (raf) cancelAnimationFrame(raf); raf = 0; lastMove = null;
      if (io) io.disconnect(); io = null; watched = null;
      timers.forEach((id) => clearTimeout(id)); timers.clear(); qaSeq++;
      root.classList.remove('mc-now', 'mc-hidden');
      $$('.mc-scrim, .mc-think, .mc-ring').forEach(drop);
      $$('.mc-wait').forEach((el) => el.classList.remove('mc-wait'));
    },
    rendered() {
      indicators();
      const p = $('#pane'); const bar = p && $('.pane-bar', p); if (bar) bar.classList.toggle('mc-glass', p.scrollTop > 8);
      syncScrims();
      /* 头条光晕的呼吸:滚出视口就停 */
      const g = $('.hero-glow');
      if (g && g !== watched) {
        watched = g; if (io) io.disconnect();
        io = new IntersectionObserver((es) => es.forEach((e) => { const t = $('.hero-glow', e.target); if (t) t.classList.toggle('mc-off', !e.isIntersecting); }));
        io.observe(g.parentElement);
      }
      relight();
    },
    scroll(el) {
      if (el.classList.contains('pane')) { const bar = $('.pane-bar', el); if (bar) bar.classList.toggle('mc-glass', el.scrollTop > 8); }
    },
    on: {
      boot(ctx, mutate) { mutate(); if (ctx.view === 'brief') dawn(); else arrive(); },
      nav(ctx, mutate) {
        if (ctx.key) return now(mutate);
        swap(mutate, () => { lead(); halo($('.rail-nav a.on .ic')); });
      },
      open(ctx, mutate) {
        if (ctx.key) return now(mutate);
        if (ctx.from === 'list' || ctx.from === 'go') { mutate(); focusPane(false); return; }
        swap(mutate, () => focusPane(true));
      },
      back(ctx, mutate) { if (ctx.key) return now(mutate); swap(mutate, lead); },
      event(ctx, mutate) {
        if (ctx.key) return now(mutate);
        swap(mutate, () => { lead(); if (ctx.open) runway(); });
      },
      fold(ctx, mutate) {
        if (ctx.key) return now(mutate);
        const pop = $('.pop.srcpop');
        if (!pop) { mutate(); return; }                      // 只切类名:列宽与栏内淡出由样式表过渡
        /* 从浮层点「固定为来源栏」:这次是整段重画,过渡接不上,用 WAAPI 补同样的一段 */
        M.exit(pop, [{ opacity: 1, filter: 'blur(0px)' }, { opacity: 0, filter: 'blur(4px)' }], { duration: 140, easing: 'ease-out' });
        dropScrim('pop'); mutate();
        const f = $('.feed'); if (!f || ctx.fold) return;
        const c = getComputedStyle(f).gridTemplateColumns.split(' ').map(parseFloat);
        if (c.length === 3) M.animate(f, [{ gridTemplateColumns: `0px ${c[1]}px ${c[0] + c[2]}px` }, { gridTemplateColumns: `${c[0]}px ${c[1]}px ${c[2]}px` }], { duration: 300, easing: FOCUS });
        focus($('.srcbar-in', f), { blur: 4, duration: 260, delay: 60 });
      },
      pop(ctx, mutate) {
        if (ctx.key) return now(mutate);
        if (ctx.open) {
          mutate();
          const el = $(ctx.sel); if (!el) return;
          M.animate(el, [{ opacity: 0, filter: 'blur(6px)', transform: 'scale(0.985)' }, { opacity: 1, filter: 'blur(0px)', transform: 'none' }], { duration: 200, easing: FOCUS });
          scrim('pop', ctx.name === 'menu' ? 4 : 5);
        } else {
          M.exit($(ctx.sel), [{ opacity: 1, filter: 'blur(0px)' }, { opacity: 0, filter: 'blur(4px)' }], { duration: 140, easing: 'ease-out' });
          dropScrim('pop'); mutate();
        }
      },
      qa(ctx, mutate) {
        if (ctx.key) return now(mutate);
        if (ctx.open) {
          mutate();
          const el = $('.qa'); if (!el) return;
          M.animate(el, [{ opacity: 0, filter: 'blur(8px)', transform: 'scale(0.985)' }, { opacity: 1, filter: 'blur(0px)', transform: 'none' }], { duration: 260, easing: FOCUS });
          sweep(el); halo($('.rail [data-act="qa"] .ic'));
        } else {
          qaSeq++;
          M.exit($('.qa'), [{ opacity: 1, filter: 'blur(0px)' }, { opacity: 0, filter: 'blur(6px)' }], { duration: 160, easing: 'ease-out' });
          mutate();
        }
      },
      /* 提问多半是回车发出的;等待与逐段出现对应的是回答真实的到达过程,所以键盘路径也照常演 */
      qaask(ctx, mutate) { mutate(); answer(); },
      why(ctx, mutate) {
        mutate(); if (ctx.key || !ctx.open) return;
        focus($('.ai-why'), { blur: 4, duration: 200 });
      },
      filter(ctx, mutate) {
        if (ctx.key) return now(mutate);
        const list = ['unread', 'kind', 'src', 'favonly'].includes(ctx.group);
        if (list) {
          /* 列表整体交叉淡化:旧的一份留在原位淡出(连滚动位置),新的同时淡入;不做位移 */
          const lb = $('.list-b'); const top = lb ? lb.scrollTop : 0;
          const g = M.exit(lb, [{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: 'ease-out' });
          const c = g && $('.list-b', g); if (c) c.scrollTop = top;
        }
        mutate();
        const sel = { podtab: '#pane .prose', ranktab: '.rk-list', rankwin: '.rk-list', disctab: '.disc .dg, .disc .colls, .disc .note', discshape: '.disc .dg', scope: '.feed > .list, .feed > .pane, .feed > .page' }[ctx.group] || '.list-b';
        $$(sel).forEach((el) => fade(el, 0, 200));
      },
      fav(ctx, mutate) {
        mutate(); if (ctx.key) return;
        const el = M.refind(ctx); const ic = el && $('.ic', el); if (!ic) return;
        if (ctx.on) { ring(el, ic); M.animate(ic, [{ filter: `brightness(${dark() ? 1.6 : 1}) drop-shadow(0 0 9px ${accent(0.95)})`, offset: 0 }], { duration: 600, easing: 'ease-out' }); } else M.animate(ic, [{ opacity: 0.35, offset: 0 }], { duration: 200, easing: 'ease-out' });
      },
      sub(ctx, mutate) {
        mutate(); if (ctx.key) return;
        const b = M.refind(ctx); if (!b) return;
        fade(b, 0, 180);
        if (!ctx.on || ctx.undo) return;
        sweep(b.closest('.sc-card'));
        M.animate($('.ic', b), [{ color: cssv('--dorami-accent'), filter: `drop-shadow(0 0 8px ${accent(0.95)})`, offset: 0 }, { color: cssv('--dorami-accent'), filter: `drop-shadow(0 0 8px ${accent(0.95)})`, offset: 0.4 }], { duration: 900, easing: 'ease-out' });
      },
      markall(ctx, mutate) {
        if (ctx.key || ctx.undo) { mutate(); return; }
        /* 视口里还亮着的行,自上而下一行一行暗下去(每行 30ms,至多 10 行) */
        const lb = $('.list-b'); const box = lb && lb.getBoundingClientRect();
        const ids = lb ? $$('.row:not(.read)', lb).filter((r) => { const b = r.getBoundingClientRect(); return b.bottom > box.top && b.top < box.bottom; }).slice(0, 10).map((r) => r.dataset.id) : [];
        mutate();
        const ink = cssv('--dorami-ink');
        ids.forEach((id, i) => M.animate($(`.row[data-id="${CSS.escape(id)}"] h3`), [{ fontWeight: 600, color: ink, offset: 0 }], { duration: 280, delay: i * 30, easing: 'ease-out' }));
      },
      unread(ctx, mutate) {
        mutate(); if (ctx.key) return;
        M.animate($('.row.on h3'), [{ fontWeight: 400, color: cssv('--dorami-ink-2'), offset: 0 }], { duration: 240, easing: 'ease-out' });
      },
      toast(ctx, mutate) {
        if (ctx.phase === 'out') {
          M.exit($('.toast'), [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(4px)' }], { duration: 160, easing: 'ease-out' });
          mutate(); return;
        }
        if (ctx.replace) M.exit($('.toast'), [{ opacity: 1 }, { opacity: 0 }], { duration: 120, easing: 'ease-out' });
        mutate();
        const el = $('.toast'); if (!el) return;
        M.animate(el, [{ opacity: 0, transform: 'translate(-50%, 4px)' }, { opacity: 1, transform: 'translate(-50%, 0)' }], { duration: 200, easing: FOCUS });
        el.classList.add('mc-gloss');
      },
      theme(ctx, mutate) {
        if (ctx.key) return now(mutate);
        M.vt(mutate, { type: `c-theme${ctx.to === 'dark' ? ' c-to-dark' : ''}`, mid: () => halo($('.rail [data-act="theme"] .ic'), 300) });
      },
    },
    tokens: [
      { name: '--mc-focus', value: '260ms · cubic-bezier(0.2, 0.8, 0.2, 1)', use: '对焦:由虚到实(浮层、面板、阅读窗标题区)' },
      { name: '--mc-blur', value: '6–10px(标题逐行 14px)', use: '对焦起点的模糊半径;只落在有边界的块上' },
      { name: '--mc-fade', value: '200ms ease-out', use: '明度类:交叉淡化、淡入' },
      { name: '--mc-out', value: '140–160ms', use: '离场:淡出并略虚' },
      { name: '--mc-trail', value: '320ms', use: '光条指示器滑动;中途拉长到两端、降到半透明' },
      { name: '--mc-glow', value: '600–700ms', use: '点亮一次:分数、图标光晕、引用角标、提示扫光' },
      { name: '--mc-sweep', value: '1150ms', use: '极光沿边一圈:AI 速读卡、问答面板、订阅成功' },
      { name: '--mc-dawn', value: '1400ms;之后 16s 一周', use: '早报到场光晕绽开;之后的呼吸(不可见时停)' },
      { name: '--mc-terminator', value: '700ms,羽化 30% 屏宽', use: '明暗切换的晨昏线' },
      { name: '--mc-spot-a', value: '暗 0.06 白光 / 亮 0.075 accent 淡染', use: '光标照明的强度' },
    ],
    notes: {
      press: '光池随指针,发丝线近处亮起;按下明度沉到 0.9',
      ind: '光条 320ms 带拖影;列表 200ms 交叉淡化',
      pop: '由虚到实 200ms(6px → 0);出 140ms;页面压暗',
      open: '标题对焦 280ms,其余晚 60ms;行光余晖 400ms',
      nav: '旧 120ms 暗下并略虚,新 220ms 清晰;图标光晕 600ms',
      fold: '列宽 300ms;栏内淡出并虚化 160ms',
      qa: '玻璃面板 260ms;极光绕边 1.15s;回答逐段对焦',
      brief: '光晕 1.4s 绽开;标题逐行对焦,行间 90ms',
      ai: '极光绕边 1.15s;各句对焦,句间 70ms;分数点亮',
      toast: '玻璃胶囊 200ms + 扫光 600ms;星标光环 450ms',
      theme: '晨昏线 700ms,羽化 30% 屏宽',
      scroll: '滚动后吸顶栏变玻璃;进度线发光',
      event: '页由虚到实;时间线节点依次点亮,每点 40ms',
      sub: '来源卡极光绕边 1.15s;对勾亮起 900ms',
    },
  });
})();
