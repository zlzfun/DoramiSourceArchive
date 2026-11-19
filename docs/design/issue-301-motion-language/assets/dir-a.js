/* A 精密 —— 取值与说明见 dir-a.css 顶部。
   这里做四件事:① 指示器滑动(M.glide)② 分数逐位滚动 ③ 吸顶栏换标题 ④ 各类交互的进出场(只有淡入、6px 位移、对准触发点的缩放)。
   键盘触发(ctx.key)一律 still():连指示器也直接落位。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
  const DRAWER = 'cubic-bezier(0.32, 0.72, 0, 1)';
  const SP = {
    snappy: { duration: 0.28, bounce: 0 },
    glide: { duration: 0.32, bounce: 0.12 },
    pop: { duration: 0.35, bounce: 0.45 },
    roll: { duration: 0.4, bounce: 0 },
    toast: { duration: 0.35, bounce: 0.2 },
  };
  const st = () => (window.__proto ? window.__proto.state : {});
  const fadeIn = (el, ms = 120, delay = 0) => M.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration: ms, delay, easing: 'linear' });
  const drop = (g) => g && g.remove();

  let quiet = false;      // 这一次重画不动(键盘触发)
  let railOnly = false;   // 切视图:只有导轨的选中块滑,别处的指示器直接落位
  let indTiming = null;   // 筛选:列表选中块与 FLIP 的行同拍
  let bootRoll = false;   // 到场:头条分数滚一次
  let paneId = null; let paneTxt = null;   // 阅读窗分数:上一次滚到的是哪篇、什么值
  let tipTimer = 0; let coolTimer = 0;

  function still(mutate) {
    quiet = true; root.classList.add('ma-quiet');
    try { mutate(); } finally { quiet = false; requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('ma-quiet'))); }
  }

  /* ── 指示器 ── */
  const firstKey = (h) => { const b = $('button', h); return (b && Object.keys(b.dataset)[0]) || 'x'; };
  const where = (h) => (h.closest('.pop') ? 'pop' : h.closest('.srcbar') ? 'bar' : h.closest('.pane-bar') ? 'pane' : h.closest('.list') ? 'list' : 'page');
  const HOSTS = [
    ['.rail-nav', ':scope > a.on', () => 'rail'],
    ['.seg', ':scope > button.on', (h) => `seg:${firstKey(h)}:${where(h)}:${st().view}:${st().shape}`],
    ['.tabs', ':scope > button.on', (h) => `tabs:${firstKey(h)}`],
    ['.kinds', ':scope > button.on', () => `kinds:${st().shape}`],
    ['.srcbar-b', ':scope > .src.on', () => `src:${st().shape}`],
    ['.list-b', ':scope > .row.on', () => `row:${st().shape}`],
  ];
  function syncInd() {
    const sp = M.spring(SP.glide);
    HOSTS.forEach(([hs, ts, keyOf]) => $$(hs).forEach((h) => {
      if (h.closest('#m-ghosts')) return;
      const key = keyOf(h); const t = h.querySelector(ts);
      let ind = h.querySelector(':scope > .m-ind');
      if (!t) { if (ind) ind.remove(); M.forgetGlide(key); return; }
      /* 自己先把 .m-ind 放到末尾再交给 M.glide:公共层是 prepend,会让底稿里的 :nth-child 选择器错一位 */
      if (!ind) { ind = document.createElement('i'); ind.className = 'm-ind'; ind.setAttribute('aria-hidden', 'true'); h.appendChild(ind); }
      ind.dataset.ind = key;
      M.glide(key, h, t, { animate: !quiet && !(railOnly && key !== 'rail'), timing: indTiming || { easing: sp.easing, duration: sp.duration } });
    }));
  }

  /* ── 分数逐位滚动 ── */
  const DIGITS = '0123456789'.split('').map((d) => `<span>${d}</span>`).join('');
  function buildRoll(el, from) {
    const txt = el.textContent.trim();
    if (!/^\d+(\.\d+)?$/.test(txt)) return;
    /* 行内的分数先钉住原宽:逐格排出来会比整串文字宽零点几像素,旁边的东西不该因此挪动 */
    if (getComputedStyle(el).display.startsWith('inline')) { const w = el.getBoundingClientRect().width; if (w) el.style.width = `${w}px`; }
    el.classList.add('ma-roll'); el.setAttribute('aria-label', txt); el.dataset.v = txt;
    el.innerHTML = [...txt].map((ch) => (ch === '.' ? '<span class="ma-dot">.</span>'
      : `<span class="ma-cell"><span class="ma-strip" data-d="${ch}" style="transform:translateY(-${ch * 10}%)">${DIGITS}</span></span>`)).join('');
    if (from == null) return;
    const old = from.replace(/\D/g, '').split('').reverse();
    const sp = M.spring(SP.roll);
    /* 右边一位先动,往左每位晚 60ms;数字没变的那一位不动 */
    $$('.ma-strip', el).reverse().forEach((s, j) => {
      const to = +s.dataset.d; const f = +(old[j] || 0);
      if (f !== to) M.animate(s, [{ transform: `translateY(-${f * 10}%)` }, { transform: `translateY(-${to * 10}%)` }], { easing: sp.easing, duration: sp.duration, delay: j * 60 });
    });
  }
  function scores() {
    const id = st().sel;
    const play = !quiet && !M.reduced() && id != null && id !== paneId;
    /* 宽窗口的分数在页边,窄窗口在卡头:两处都换成滚动结构,看得见的那一处在滚 */
    const els = [$('#pane .side-blk:not(.minor) .side-score .sc'), $('#pane .ai-scores .sc')].filter((el) => el && !el.classList.contains('ma-roll'));
    let txt = null;
    els.forEach((el) => { txt = el.textContent.trim(); buildRoll(el, play ? (paneTxt || txt.replace(/\d/g, '0')) : null); });
    if (els.length) { paneId = id; paneTxt = txt; }
    const hero = $('.hero-score .sc');
    if (hero && !hero.classList.contains('ma-roll')) buildRoll(hero, bootRoll && !M.reduced() ? hero.textContent.trim().replace(/\d/g, '0') : null);
  }

  /* ── 吸顶栏:标题滚出后,左侧由来源名换成文章标题 ── */
  function updateBar(pane) {
    const bar = $('.pane-bar', pane); if (!bar) return;
    const h1 = $('.doc h1', pane);
    pane.classList.toggle('ma-scrolled', pane.scrollTop > 4);
    pane.classList.toggle('ma-titled', !!h1 && !!$('.ma-bar-title', bar) && h1.getBoundingClientRect().bottom < bar.getBoundingClientRect().bottom + 2);
  }
  function barTitle() {
    const pane = $('#pane'); if (!pane) return;
    const bar = $('.pane-bar', pane); const h1 = $('.doc h1', pane);
    if (bar && h1 && !$('.ma-bar-title', bar)) {
      const lead = $('.back, .pane-src', bar); const grow = $('.grow', bar); const next = grow && grow.nextElementSibling;
      const b = bar.getBoundingClientRect();
      const logo = lead && lead.matches('.pane-src') ? $('.lg', lead) : null;
      const edge = logo ? logo.getBoundingClientRect().right + 8 : lead ? lead.getBoundingClientRect().right + 12 : b.left + 20;
      const t = document.createElement('span'); t.className = 'ma-bar-title'; t.setAttribute('aria-hidden', 'true'); t.textContent = h1.textContent;
      t.style.left = `${Math.round(edge - b.left)}px`;
      t.style.right = `${Math.round(next ? b.right - next.getBoundingClientRect().left + 16 : 16)}px`;
      bar.appendChild(t);
    }
    updateBar(pane);
  }

  /* ── 浮层:以触发钮为原点 ── */
  const TRIG = { cal: '[data-act="cal"]', menu: '[data-act="menu"]', srcpop: '.scope' };
  function popOrigin(p, name, el) {
    const trig = el && el.isConnected && !el.closest('.pop') ? el : $(TRIG[name]);
    if (!trig) return '50% 0px';
    const b = p.getBoundingClientRect(); const t = trig.getBoundingClientRect();
    return `${Math.round(Math.max(12, Math.min(b.width - 12, t.left + t.width / 2 - b.left)))}px 0px`;
  }
  function exitPop(p, name, el) {
    if (!p) return;
    const origin = popOrigin(p, name, el);
    const g = M.exit(p, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.98)' }], { duration: 100, easing: EASE_OUT });
    if (g) g.style.transformOrigin = origin;
  }
  const exitQa = () => M.exit($('.qa'), [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(24px)' }], { duration: 180, easing: EASE_OUT });

  /* ── 阅读窗换篇:标题区是主角(淡入 + 上移 6px),其余一起淡入,不各自进场 ── */
  function enterDoc() {
    const doc = $('#pane .doc'); if (!doc) return;
    [...doc.children].forEach((c) => {
      if (c.matches('.doc-h')) M.animate(c, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 160, easing: EASE_OUT });
      else fadeIn(c, 120);
    });
  }
  const fadeStage = () => fadeIn($('#stage'), 100);

  /* ── 列表筛选:留下的滑到新位置,新来的淡入,走的淡出 ── */
  function flipList(mutate) {
    const lb = $('.list-b'); const box = lb.getBoundingClientRect();
    const T = { duration: 260, easing: EASE_OUT };
    let changed = 0;
    indTiming = T;
    try {
      const res = M.flip('.row[data-id]', mutate, {
        scope: '.list-b',
        /* 从一屏之外赶来的不滑(会横穿整列),改成淡入 */
        move: (el, i, dx, dy) => { if (Math.abs(dy) <= box.height) return T; fadeIn(el, 150); return { duration: 1 }; },
        /* 走的先淡出(90ms),新来的晚 70ms 再淡入:两层字不叠在一起 */
        enter: (el) => fadeIn(el, 140, 70),
        exit: (g, r) => {
          changed += 1;
          if (r.top < box.top + 34 || r.bottom > box.bottom) { drop(g); return; }
          const a = M.animate(g, [{ opacity: 1 }, { opacity: 0 }], { duration: 90, easing: 'linear', fill: 'forwards' });
          if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g);
        },
      });
      changed += res.moved.length + res.entered.length;
    } finally { indTiming = null; }
    /* 日期组头没有 key,跟不了 FLIP:列表真的变了才让它们淡入一下 */
    if (changed) $$('.list-b .day, .list-b .empty').forEach((d) => fadeIn(d, 150));
  }
  function flipRank(mutate) {
    const T = { duration: 260, easing: EASE_OUT };
    M.flip('.rk[data-event]', mutate, { scope: '.rk-list', key: (el) => el.dataset.event, move: T, enter: (el) => fadeIn(el, 150, 60), exit: drop });
    fadeIn($('.rk-list .empty'), 150);
  }

  /* ── 行状态变了(已读 ⇄ 未读):颜色 200ms 过渡;粗细直接换(两种字重交叉淡化会出重影,连拍里看过,弃了)。
        全部标读时自上而下每行晚 20ms,至多 8 行——是「这一下扫过去了」的因果,不是入场 ── */
  function rowSwap(mutate, cascade) {
    const lb = $('.list-b'); if (!lb) { mutate(); return; }
    const box = lb.getBoundingClientRect();
    const col = (el) => (el ? getComputedStyle(el).color : null);
    const snaps = $$('.row[data-id]', lb).filter((r) => { const b = r.getBoundingClientRect(); return b.bottom > box.top && b.top < box.bottom; })
      .map((r) => ({ id: r.dataset.id, read: r.classList.contains('read'), w: getComputedStyle($('h3', r)).fontWeight, h: col($('h3', r)), s: col($('.row-sum', r)) }));
    mutate();
    let i = 0;
    snaps.forEach((s) => {
      const now = $(`.list-b .row[data-id="${CSS.escape(s.id)}"]`);
      if (!now || now.classList.contains('read') === s.read) return;
      const delay = cascade ? Math.min(i, 8) * 20 : 0; i += 1;
      const h3 = $('h3', now); const sum = $('.row-sum', now);
      if (delay) M.animate(h3, [{ fontWeight: s.w }, { fontWeight: s.w }], { duration: delay, easing: 'linear' });   // 轮到这一行之前保持原字重
      M.animate(h3, [{ color: s.h }, { color: col(h3) }], { duration: 200, delay, easing: 'ease' });
      if (sum && s.s) M.animate(sum, [{ color: s.s }, { color: col(sum) }], { duration: 200, delay, easing: 'ease' });
    });
  }

  /* ── 「依据」展开 / 收起 ── */
  function wrapAcc(w) {
    if (w.parentElement.classList.contains('ma-acc-in')) return w.parentElement.parentElement;
    const acc = document.createElement('div'); acc.className = 'ma-acc';
    const inner = document.createElement('div'); inner.className = 'ma-acc-in';
    w.before(acc); acc.appendChild(inner); inner.appendChild(w);
    return acc;
  }

  /* ── 提示的冷热:第一个等 350ms,看过一个之后 600ms 内下一个立刻出 ── */
  function onOver(e) {
    const t = e.target.closest && e.target.closest('[data-tip]');
    const warm = root.classList.contains('ma-tip-warm');
    if (t) {
      clearTimeout(coolTimer); coolTimer = 0;
      if (!warm) { clearTimeout(tipTimer); tipTimer = setTimeout(() => root.classList.add('ma-tip-warm'), 520); }
    } else {
      clearTimeout(tipTimer);
      if (warm && !coolTimer) coolTimer = setTimeout(() => { root.classList.remove('ma-tip-warm'); coolTimer = 0; }, 600);
    }
  }

  M.register('a', {
    name: '精密',
    init() {
      Object.entries({ snappy: SP.snappy, pop: SP.pop, glide: SP.glide }).forEach(([k, cfg]) => {
        const s = M.spring(cfg); root.style.setProperty(`--ma-spring-${k}`, s.easing); root.style.setProperty(`--ma-spring-${k}-d`, `${s.duration}ms`);
      });
      paneId = null; paneTxt = null; quiet = false; railOnly = false; indTiming = null; bootRoll = false;
      document.addEventListener('pointerover', onOver, true);
    },
    dispose() {
      document.removeEventListener('pointerover', onOver, true);
      clearTimeout(tipTimer); clearTimeout(coolTimer); coolTimer = 0;
      root.classList.remove('ma-tip-warm', 'ma-quiet');
      ['snappy', 'pop', 'glide'].forEach((k) => { root.style.removeProperty(`--ma-spring-${k}`); root.style.removeProperty(`--ma-spring-${k}-d`); });
      $$('.m-ind').forEach((el) => el.remove());
      $$('.ma-bar-title').forEach((el) => el.remove());
      $$('.sc.ma-roll').forEach((el) => { el.textContent = el.dataset.v || ''; el.classList.remove('ma-roll'); el.removeAttribute('aria-label'); });
      $$('.ma-scrolled, .ma-titled').forEach((el) => el.classList.remove('ma-scrolled', 'ma-titled'));
    },
    rendered(scope) {
      syncInd();
      if (scope === 'qa' || scope === 'qa-body') return;
      scores(); barTitle();
    },
    scroll(el) { if (el.id === 'pane') updateBar(el); },

    on: {
      boot(ctx, mutate) {
        bootRoll = true; try { mutate(); } finally { bootRoll = false; }
        fadeIn($('#app'), 180);
      },
      nav(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        railOnly = true; try { mutate(); } finally { railOnly = false; }
        if (ctx.from !== ctx.to) fadeStage();
      },
      open(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        const paneOnly = ctx.from === 'list' || ctx.from === 'go';
        if (ctx.inQa) exitQa();
        railOnly = !paneOnly; try { mutate(); } finally { railOnly = false; }
        if (!paneOnly) fadeStage();
        enterDoc();
      },
      back(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        railOnly = true; try { mutate(); } finally { railOnly = false; }
        fadeStage();
      },
      event(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        railOnly = true; try { mutate(); } finally { railOnly = false; }
        if (ctx.open) M.animate($('#stage .page .wrap'), [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 160, easing: EASE_OUT });
        else fadeStage();
      },
      fold(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        const pop = $('.pop.srcpop');
        if (!pop) { mutate(); return; }   // 只换类名:列宽过渡在样式表里
        /* 从浮层里点「固定为来源栏」:工作区整段重画,样式过渡接不上,这里手动补同一条列宽过渡 */
        exitPop(pop, 'srcpop');
        mutate();
        const cs = getComputedStyle(root); const sw = cs.getPropertyValue('--src-w').trim() || '232px'; const lw = cs.getPropertyValue('--list-w').trim() || '372px';
        if (!ctx.fold) M.animate($('.feed'), [{ gridTemplateColumns: `0px ${lw} minmax(0px, 1fr)` }, { gridTemplateColumns: `${sw} ${lw} minmax(0px, 1fr)` }], { duration: 280, easing: DRAWER });
      },
      pop(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        if (!ctx.open) { exitPop($(ctx.sel), ctx.name, ctx.el); mutate(); return; }
        mutate();
        const p = $(ctx.sel); if (!p) return;
        p.style.transformOrigin = popOrigin(p, ctx.name, M.refind({ sel: TRIG[ctx.name], rect: ctx.rect }));
        M.animate(p, [{ opacity: 0, transform: 'scale(0.96)' }, { opacity: 1, transform: 'none' }], { duration: 150, easing: EASE_OUT });
      },
      qa(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        if (!ctx.open) { exitQa(); mutate(); return; }
        mutate();
        M.animate($('.qa'), [{ opacity: 0, transform: 'translateX(24px)' }, { opacity: 1, transform: 'none' }], { duration: 280, easing: DRAWER });
      },
      qaask(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        mutate();
        const b = $('.qa-b'); if (!b) return;
        /* 自己的提问立刻在;回答按段出现(样页里模拟流式,落地时跟着真实的流走),来源一起出 */
        fadeIn($('.qa-q', b), 120);
        const ps = $$('.qa-a > p:not(.qa-demo)', b);
        ps.forEach((p, i) => fadeIn(p, 160, 200 + i * 90));
        $$('.qa-a > .qa-src, .qa-a > .qa-demo', b).forEach((el) => fadeIn(el, 160, 200 + ps.length * 90));
      },
      why(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        const chev = () => $('#pane .ai-scores .ic');
        if (ctx.open) {
          mutate();
          const w = $('#pane .ai-why'); if (!w || w.offsetParent === null) return;   // 宽窗口没有这一块
          M.animate(wrapAcc(w), [{ gridTemplateRows: '0fr' }, { gridTemplateRows: '1fr' }], { duration: 220, easing: DRAWER });
          fadeIn(w, 220);
          M.animate(chev(), [{ transform: 'rotate(0deg)' }, { transform: 'rotate(180deg)' }], { duration: 150, easing: EASE_OUT });
          return;
        }
        const w = $('#pane .ai-why');
        if (!w || w.offsetParent === null) { mutate(); return; }
        M.animate(w, [{ opacity: 1 }, { opacity: 0 }], { duration: 120, easing: 'linear', fill: 'forwards' });
        M.animate(chev(), [{ transform: 'rotate(180deg)' }, { transform: 'rotate(0deg)' }], { duration: 150, easing: EASE_OUT, fill: 'forwards' });
        const a = M.animate(wrapAcc(w), [{ gridTemplateRows: '1fr' }, { gridTemplateRows: '0fr' }], { duration: 160, easing: DRAWER, fill: 'forwards' });
        if (a) a.finished.then(mutate, mutate); else mutate();
      },
      filter(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        const g = ctx.group;
        exitPop($('.pop.srcpop'), 'srcpop');
        if ((g === 'unread' || g === 'kind' || g === 'favonly' || g === 'src') && $('.list-b')) { flipList(mutate); return; }
        if (g === 'rankwin' && $('.rk-list')) { flipRank(mutate); return; }
        mutate();
        if (g === 'ranktab') fadeIn($('.rk-list'), 100);
        else if (g === 'disctab' || g === 'discshape') $$('.page.disc .wrap > :not(.pg-h):not(.pg-bar)').forEach((el) => fadeIn(el, 100));
        else if (g === 'podtab') fadeIn($('#pane .prose'), 100);
        else if (g === 'scope') fadeStage();
      },
      fav(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        mutate();
        const b = M.refind(ctx); const ic = b && $('.ic', b); if (!ic) return;
        if (ctx.on) M.animate(ic, [{ transform: 'scale(0.6)' }, { transform: 'scale(1)' }], { spring: SP.pop });
        else M.animate(ic, [{ opacity: 0.35 }, { opacity: 1 }], { duration: 120, easing: 'linear' });
      },
      sub(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        mutate();
        const b = M.refind(ctx); if (!b) return;
        M.animate(b, [{ opacity: 0.3 }, { opacity: 1 }], { duration: 150, easing: 'linear' });
        const path = ctx.on && $('svg path', b);
        if (path) M.animate(path, [{ strokeDasharray: '24', strokeDashoffset: '24' }, { strokeDasharray: '24', strokeDashoffset: '0' }], { duration: 220, delay: 40, easing: EASE_OUT });
      },
      markall(ctx, mutate) { if (ctx.key) { still(mutate); return; } rowSwap(mutate, !ctx.undo); },
      unread(ctx, mutate) { if (ctx.key) { still(mutate); return; } rowSwap(mutate, false); },
      toast(ctx, mutate) {
        const old = $('.toast');
        if (ctx.phase === 'out') {
          M.exit(old, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(calc(100% + 24px))' }], { duration: 180, easing: EASE_OUT });
          mutate(); return;
        }
        /* 换提示:旧的缩到 0.94 退下去,新的照常自下弹入 */
        if (old) M.exit(old, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.94)' }], { duration: 160, easing: EASE_OUT });
        mutate();
        const t = $('.toast'); if (!t) return;
        M.animate(t, [{ transform: 'translate(-50%, calc(100% + 24px))' }, { transform: 'translate(-50%, 0)' }], { spring: SP.toast });
        fadeIn(t, 120);
      },
      theme(ctx, mutate) {
        if (ctx.key) { still(mutate); return; }
        M.vt(mutate, { type: 'a-theme' });
      },
    },

    tokens: [
      { name: '--ma-ease-out', value: 'cubic-bezier(0.23, 1, 0.32, 1)', use: '出入场、位移收尾' },
      { name: '--ma-ease-drawer', value: 'cubic-bezier(0.32, 0.72, 0, 1)', use: '问答面板、来源栏列宽' },
      { name: '--ma-press', value: '120ms', use: '按下(缩到 0.97;整行 0.985)' },
      { name: '--ma-hover-in / out', value: '100ms / 180ms', use: '悬停底色:进快出慢' },
      { name: '--ma-fast', value: '150ms', use: '浮层入、标题区、淡入' },
      { name: '--ma-base', value: '200ms', use: '行状态交叉淡化' },
      { name: '--ma-panel', value: '280ms', use: '问答面板入、来源栏列宽' },
      { name: 'spring snappy', value: '0.28s · bounce 0', use: '松手回弹' },
      { name: 'spring glide', value: '0.32s · bounce 0.12', use: '指示器滑动' },
      { name: 'spring pop', value: '0.35s · bounce 0.45', use: '导轨图标、星标(过冲约 5%)' },
      { name: 'spring roll', value: '0.40s · bounce 0', use: '分数逐位滚动(落定约 590ms)' },
    ],
    notes: {
      press: '按下 0.97 · 120ms;悬停底色进 100 出 180ms',
      ind: '指示器弹簧滑动 0.32s;筛选后行滑到新位 260ms',
      pop: '对准触发钮 0.96 → 1 · 150ms;关 100ms',
      open: '选中块滑过去;标题区淡入上移 6px · 160ms',
      nav: '内容直接换 + 100ms 淡入;导轨选中块滑动',
      fold: '列宽 280ms(抽屉曲线)',
      qa: '右侧滑入 24px · 280ms,出 180ms;回答逐段出',
      brief: '整页 180ms 淡入;头条分数逐位滚 ≈ 590ms',
      ai: '分数逐位滚 ≈ 590ms(原 2200ms);卡体 120ms 淡入',
      toast: '自下弹入(弹簧 0.35s),出 180ms;星标弹一下',
      theme: '整页交叉淡化 180ms',
      scroll: '标题滚出后吸顶栏换成文章标题 · 160ms',
      event: '淡入 + 上移 6px · 160ms;返回 100ms 淡入',
      sub: '按下 0.97;新字 150ms 淡入,对勾画出 220ms',
    },
  });
})();
