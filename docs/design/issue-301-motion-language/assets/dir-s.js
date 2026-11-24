/* 选定稿 —— 2026-11-18 用户逐场景选择之后合成的一份,11-19 把「切换视图」「AI 速读卡」改成精密(设计说明与 token 见 dir-s.css 顶部)。
   每类交互取自被选中的那个方向,合并时只做了三件事:
     ① 取值收成一套:曲线三条、弹簧五档(提示的弹簧并进 glide,订阅钮并进 pop,分数滚动并进 move);
     ② 交界处定了归属:指示器的滑法跟「指示器」一行(连续),列表选中块面积大不拉伸;速读卡按精密(分数逐位滚动),光只留给问答面板;
        「依据」展开没有出现在任何一格里,取精密的行高过渡;
     ③ View Transitions 只剩两处低频转场(榜单卡成页、浮层变来源栏),明暗切换是整页交叉淡化。
        切视图、打开文章都不用它:内容直接换 + 100ms 淡入,动的只有导轨上的指示器。
   键盘触发(ctx.key)一律 still():不动,连指示器也直接落位。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const VT = !!document.startViewTransition;
  const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
  const DRAWER = 'cubic-bezier(0.32, 0.72, 0, 1)';
  const FOCUS = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
  const SP = {
    snappy: { duration: 0.28, bounce: 0 },     // 松手回弹
    glide: { duration: 0.32, bounce: 0.12 },   // 列表选中块、提示弹入
    move: { duration: 0.42, bounce: 0.12 },    // 位移:筛选后的行、分数逐位滚动
    settle: { duration: 0.5, bounce: 0.18 },   // 大变形:卡面成页、到场
    pop: { duration: 0.35, bounce: 0.45 },     // 落点:星标、导轨图标、订阅钮
  };
  const sp = (k) => { const s = M.spring(SP[k]); return { duration: s.duration, easing: s.easing }; };
  const st = () => (window.__proto ? window.__proto.state : {});
  const dark = () => root.dataset.theme === 'dark';
  const accent = (a) => `rgba(${getComputedStyle(root).getPropertyValue('--dorami-accent-rgb').trim()}, ${a})`;
  const px = (el) => parseFloat(getComputedStyle(el).fontSize) || 16;
  const drop = (g) => { if (g && g.parentNode) g.parentNode.removeChild(g); };
  const inView = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 4 && r.top < innerHeight - 4 && r.right > 0 && r.left < innerWidth;
  };
  const fadeIn = (el, ms = 120, delay = 0) => M.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration: ms, delay, easing: 'linear' });
  const fadeUp = (els, opt = {}) => {
    const s = sp(opt.spring || 'move'); const d = opt.dist == null ? 14 : opt.dist;
    els.filter(Boolean).forEach((el) => M.animate(el, [{ opacity: 0, transform: `translateY(${d}px)` }, { opacity: 1, transform: 'none' }], { duration: s.duration, easing: s.easing, delay: opt.delay || 0 }));
  };

  let quiet = false;      // 这一次重画不动(键盘触发)
  let railOnly = false;   // 换了视图:只有导轨的指示器滑,别处的直接落位
  let indTiming = null;   // 筛选:列表选中块与 FLIP 的行同拍
  let immersiveByMe = false;
  let paneId = null; let paneTxt = null;   // 阅读窗分数:上一次滚到的是哪篇、什么值
  let tipTimer = 0; let coolTimer = 0; let qaSeq = 0;
  const timers = new Set();
  const later = (ms, fn) => { const id = M.after(ms, () => { timers.delete(id); fn(); }); timers.add(id); return id; };

  function still(mutate) {
    quiet = true; root.classList.add('ms-quiet');
    try { mutate(); } finally { quiet = false; requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('ms-quiet'))); }
  }
  const withRail = (mutate) => { railOnly = true; try { mutate(); } finally { railOnly = false; } };

  /* ── 指示器(连续):朝目标的那条边先到,后面的边跟上——中途被拉长,落定时收拢 ── */
  const sx = (t, dur, bounce) => {
    const z = Math.max(0.05, 1 - bounce); const w0 = (2 * Math.PI) / dur;
    if (z >= 1) return 1 - Math.exp(-w0 * t) * (1 + w0 * t);
    const wd = w0 * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w0 * t) * (Math.cos(wd * t) + ((z * w0) / wd) * Math.sin(wd * t));
  };
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
  const G = {
    near: { keyframes: liquid(0.2, 0.4), timing: { duration: LIQ * 1000, easing: 'linear' } },
    far: { keyframes: liquid(0.34, 0.42), timing: { duration: LIQ * 1000, easing: 'linear' } },
  };
  const firstKey = (h) => { const b = $('button', h); return (b && Object.keys(b.dataset)[0]) || 'x'; };
  const where = (h) => (h.closest('.pop') ? 'pop' : h.closest('.srcbar') ? 'bar' : h.closest('.pane-bar') ? 'pane' : h.closest('.list') ? 'list' : 'page');
  const HOSTS = [
    ['.rail-nav', ':scope > a.on', () => 'rail', 'near'],
    ['.seg', ':scope > button.on', (h) => `seg:${firstKey(h)}:${where(h)}:${st().view}:${st().shape}`, 'near'],
    ['.tabs', ':scope > button.on', (h) => `tabs:${firstKey(h)}`, 'near'],
    ['.kinds', ':scope > button.on', () => `kinds:${st().shape}`, 'near'],
    ['.srcbar-b', ':scope > .src.on', () => `src:${st().shape}`, 'far'],
    ['.list-b', ':scope > .row.on', () => `row:${st().shape}`, 'row'],
  ];
  function syncInd() {
    HOSTS.forEach(([hs, ts, keyOf, kind]) => $$(hs).forEach((h) => {
      if (h.closest('#m-ghosts')) return;
      const key = keyOf(h); const t = h.querySelector(ts);
      let ind = h.querySelector(':scope > .m-ind');
      if (!t) { if (ind) ind.remove(); M.forgetGlide(key); return; }
      /* 自己先把 .m-ind 放到末尾再交给 M.glide:公共层是 prepend,会让底稿里的 :nth-child 选择器错一位 */
      if (!ind) { ind = document.createElement('i'); ind.className = 'm-ind'; ind.setAttribute('aria-hidden', 'true'); h.appendChild(ind); }
      ind.dataset.ind = key;
      const animate = !quiet && !(railOnly && key !== 'rail');
      if (kind === 'row') M.glide(key, h, t, { animate, timing: indTiming || sp('glide') });
      else M.glide(key, h, t, { animate, ...G[kind] });
    }));
  }

  /* ── 分数逐位滚动(精密):换篇时右边一位先动,往左每位晚 60ms;数字没变的那一位不动 ── */
  const DIGITS = '0123456789'.split('').map((d) => `<span>${d}</span>`).join('');
  function buildRoll(el, from) {
    const txt = el.textContent.trim();
    if (!/^\d+(\.\d+)?$/.test(txt)) return;
    /* 行内的分数先钉住原宽:逐格排出来会比整串文字宽零点几像素,旁边的东西不该因此挪动 */
    if (getComputedStyle(el).display.startsWith('inline')) { const w = el.getBoundingClientRect().width; if (w) el.style.width = `${w}px`; }
    el.classList.add('ms-roll'); el.setAttribute('aria-label', txt); el.dataset.v = txt;
    el.innerHTML = [...txt].map((ch) => (ch === '.' ? '<span class="ms-dot">.</span>'
      : `<span class="ms-cell"><span class="ms-strip" data-d="${ch}" style="transform:translateY(-${ch * 10}%)">${DIGITS}</span></span>`)).join('');
    if (from == null) return;
    const old = from.replace(/\D/g, '').split('').reverse(); const s = sp('move');
    $$('.ms-strip', el).reverse().forEach((strip, j) => {
      const to = +strip.dataset.d; const f = +(old[j] || 0);
      if (f !== to) M.animate(strip, [{ transform: `translateY(-${f * 10}%)` }, { transform: `translateY(-${to * 10}%)` }], { easing: s.easing, duration: s.duration, delay: j * 60 });
    });
  }
  function scores() {
    const id = st().sel;
    const play = !quiet && !M.reduced() && id != null && id !== paneId;
    /* 宽窗口的分数在页边,窄窗口在卡头:两处都换成滚动结构,看得见的那一处在滚 */
    const els = [$('#pane .side-blk:not(.minor) .side-score .sc'), $('#pane .ai-scores .sc')].filter((el) => el && !el.classList.contains('ms-roll'));
    let txt = null;
    els.forEach((el) => { txt = el.textContent.trim(); buildRoll(el, play ? (paneTxt || txt.replace(/\d/g, '0')) : null); });
    if (els.length) { paneId = id; paneTxt = txt; }
  }

  /* ── 吸顶栏里的小标题(连续):样式里用 view-timeline 跟着大标题走;不支持时退回滚动事件 ── */
  const SDA = typeof CSS !== 'undefined' && CSS.supports && CSS.supports('animation-timeline: view()');
  function barTitle() {
    const bar = $('#pane .pane-bar'); const h1 = $('#pane .doc h1');
    if (!bar || !h1 || !bar.firstElementChild) return;
    let t = $('.ms-bar-title', bar);
    if (!t) { t = document.createElement('span'); t.className = 'ms-bar-title'; t.setAttribute('aria-hidden', 'true'); bar.firstElementChild.after(t); }
    t.textContent = h1.textContent;
    /* 随滚动走的动画不能跟着「慢放」改速率(那会让它只走到一小段),标成公共层跳过的那一类 */
    if (SDA) requestAnimationFrame(() => bar.getAnimations({ subtree: true }).forEach((a) => { if (a.animationName === 'ms-dock' || a.animationName === 'ms-fade-in') { a.id = 'm-ui'; a.playbackRate = 1; } }));
  }
  function dockByScroll(pane) {
    const t = $('.ms-bar-title', pane); const h1 = $('.doc h1', pane); if (!t || !h1) return;
    const top = pane.getBoundingClientRect().top + 52; const b = h1.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, (top - b.top) / Math.max(1, b.height)));
    t.style.opacity = p.toFixed(3); t.style.transform = `translateY(${((1 - p) * 12).toFixed(1)}px)`;
  }

  /* ── 浮层(精密):以触发钮为原点 ── */
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

  /* ── 光的几个基本动作(只给问答面板)── */
  const focus = (el, opt = {}) => M.animate(el, [{ opacity: opt.from == null ? 0 : opt.from, filter: `blur(${opt.blur || 8}px)` }, { opacity: 1, filter: 'blur(0px)' }], { duration: opt.duration || 260, delay: opt.delay || 0, easing: FOCUS });
  const fade = (el, delay = 0, duration = 240) => M.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration, delay, easing: 'ease-out' });
  function halo(el, delay = 0) {
    if (!el) return;
    M.animate(el, [{ filter: `drop-shadow(0 0 0px ${accent(0)})`, offset: 0 }, { filter: `drop-shadow(0 0 11px ${accent(dark() ? 0.95 : 0.6)})`, offset: 0.3 }], { duration: 600, delay, easing: 'ease-out' });
  }
  /* 极光沿边一圈(样式表里的 ms-sweep);重复触发时从头再来 */
  function sweep(el) { if (!el) return; el.classList.remove('ms-sweep'); void el.offsetWidth; el.classList.add('ms-sweep'); }
  /* 压暗:问答面板下面的工作区退后一步 */
  function syncScrim() {
    const stage = $('#app > .stage'); if (!stage) return;
    const cur = $(':scope > .ms-scrim:not(.off)', stage);
    if (st().qa) {
      if (cur) return;
      const s = document.createElement('i'); s.className = 'ms-scrim'; s.setAttribute('aria-hidden', 'true'); stage.appendChild(s);
      void s.offsetWidth; s.classList.add('on');
    } else if (cur) {
      if (quiet) { drop(cur); return; }
      cur.classList.add('off'); cur.classList.remove('on');
      setTimeout(() => drop(cur), 220 / M.speed);
    }
  }
  const exitQa = () => { qaSeq++; M.exit($('.qa'), [{ opacity: 1, filter: 'blur(0px)' }, { opacity: 0, filter: 'blur(6px)' }], { duration: 160, easing: 'ease-out' }); };

  /* 阅读窗换篇:标题区是主角(淡入 + 上移 6px),其余(含速读卡)一起淡入,不各自进场;分数在 scores() 里逐位滚 */
  function enterDoc() {
    const doc = $('#pane .doc'); if (!doc) return;
    [...doc.children].forEach((c) => {
      if (c.matches('.doc-h')) M.animate(c, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 160, easing: EASE_OUT });
      else fadeIn(c, 120);
    });
  }
  const fadeStage = () => fadeIn($('#stage'), 100);

  /* ── 列表 FLIP(连续):留下的滑到新位置,新来的淡入,走的原地淡出(只处理看得见的)── */
  function flipList(sel, scopeSel, mutate, key) {
    const sc = $(scopeSel); const clip = sc ? sc.getBoundingClientRect() : null; const s = sp('move');
    indTiming = s;
    try {
      M.flip(sel, mutate, {
        scope: scopeSel, key,
        move: () => ({ duration: s.duration, easing: s.easing }),
        enter: (el) => M.animate(el, [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 240, delay: 70, easing: EASE_OUT }),
        exit: (g, r) => {
          if (!clip || r.bottom <= clip.top || r.top >= clip.bottom) { drop(g); return; }
          g.style.clipPath = `inset(${Math.max(0, clip.top - r.top)}px 0 ${Math.max(0, r.bottom - clip.bottom)}px 0)`;
          const a = M.animate(g, [{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(0.98)' }], { duration: 150, easing: 'linear', fill: 'forwards' });
          if (a) a.finished.then(() => drop(g), () => drop(g)); else drop(g);
        },
      });
    } finally { indTiming = null; }
  }

  /* ── 行状态变了(精密):颜色 200ms 过渡,粗细直接换;全部标读时自上而下每行晚 20ms,至多 8 行 ── */
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

  /* ── 「依据」展开 / 收起(精密)── */
  function wrapAcc(w) {
    if (w.parentElement.classList.contains('ms-acc-in')) return w.parentElement.parentElement;
    const acc = document.createElement('div'); acc.className = 'ms-acc';
    const inner = document.createElement('div'); inner.className = 'ms-acc-in';
    w.before(acc); acc.appendChild(inner); inner.appendChild(w);
    return acc;
  }

  /* ── 提示的冷热:第一个等 350ms,看过一个之后 600ms 内下一个立刻出 ── */
  function onOver(e) {
    const t = e.target.closest && e.target.closest('[data-tip]');
    const warm = root.classList.contains('ms-tip-warm');
    if (t) {
      clearTimeout(coolTimer); coolTimer = 0;
      if (!warm) { clearTimeout(tipTimer); tipTimer = setTimeout(() => root.classList.add('ms-tip-warm'), 520); }
    } else {
      clearTimeout(tipTimer);
      if (warm && !coolTimer) coolTimer = setTimeout(() => { root.classList.remove('ms-tip-warm'); coolTimer = 0; }, 600);
    }
  }

  /* ── 问答的回答(光):先一行流光的「正在检索」,回答逐段由虚到实,引用角标依次亮起、对应来源条跟着亮 ── */
  function answer() {
    const b = $('.qa-b'); const a = b && $('.qa-a', b); if (!a) return;
    const seq = ++qaSeq; const live = () => seq === qaSeq && a.isConnected;
    a.classList.add('ms-wait');
    focus($('.qa-q', b), { blur: 4, duration: 200 });
    const think = document.createElement('div'); think.className = 'ms-think'; think.textContent = '正在检索…'; a.before(think);
    fade(think, 120, 180);
    sweep($('.qa'));
    later(900, () => {
      drop(think); a.classList.remove('ms-wait');
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

  /* ── 共享标题(榜单卡 ⇄ 事件页):旧端记下字号与宽度,新端据此写出缩放比 ── */
  let share = null;
  const resetShare = () => { share = null; root.style.setProperty('--ms-t-ow', 'auto'); root.style.setProperty('--ms-t-nw', 'auto'); root.style.setProperty('--ms-t-fr', '1'); };
  const takeOld = (el) => { if (!inView(el)) return; M.name(el, 'ms-title'); share = { fs: px(el), w: el.getBoundingClientRect().width }; };
  const takeNew = (el) => {
    if (!share || !inView(el)) return;
    M.name(el, 'ms-title');
    root.style.setProperty('--ms-t-ow', `${share.w}px`); root.style.setProperty('--ms-t-nw', `${el.getBoundingClientRect().width}px`); root.style.setProperty('--ms-t-fr', (px(el) / share.fs).toFixed(4));
  };

  const CSS_VARS = ['--ms-spring-snappy', '--ms-spring-snappy-d', '--ms-spring-pop', '--ms-spring-pop-d', '--ms-move-ease', '--ms-move-dur', '--ms-settle-ease', '--ms-settle-dur', '--ms-t-ow', '--ms-t-nw', '--ms-t-fr'];

  M.register('s', {
    name: '选定稿',
    init() {
      [['snappy', '--ms-spring-snappy', '--ms-spring-snappy-d'], ['pop', '--ms-spring-pop', '--ms-spring-pop-d'], ['move', '--ms-move-ease', '--ms-move-dur'], ['settle', '--ms-settle-ease', '--ms-settle-dur']]
        .forEach(([k, e, d]) => { const s = M.spring(SP[k]); root.style.setProperty(e, s.easing); root.style.setProperty(d, `${s.duration}ms`); });
      quiet = false; railOnly = false; indTiming = null; share = null; paneId = null; paneTxt = null;
      document.addEventListener('pointerover', onOver, true);
      /* 「沉浸阅读」是选定稿的一部分:默认打开(开关条上仍可以关) */
      immersiveByMe = !root.classList.contains('p-immersive');
      root.classList.add('p-immersive');
    },
    dispose() {
      document.removeEventListener('pointerover', onOver, true);
      clearTimeout(tipTimer); clearTimeout(coolTimer); coolTimer = 0;
      timers.forEach((id) => clearTimeout(id)); timers.clear(); qaSeq++;
      root.classList.remove('ms-tip-warm', 'ms-quiet');
      if (immersiveByMe) { root.classList.remove('p-immersive', 'reading'); immersiveByMe = false; }
      CSS_VARS.forEach((v) => root.style.removeProperty(v));
      $$('.m-ind, .ms-bar-title, .ms-fly, .ms-scrim, .ms-think').forEach(drop);
      $$('.ms-wait, .ms-sweep').forEach((el) => el.classList.remove('ms-wait', 'ms-sweep'));
      $$('.sc.ms-roll').forEach((el) => { el.textContent = el.dataset.v || ''; el.classList.remove('ms-roll'); el.removeAttribute('aria-label'); el.style.width = ''; });
    },
    rendered(scope) {
      syncInd(); syncScrim();
      if (scope === 'qa' || scope === 'qa-body') return;
      scores(); barTitle();
    },
    scroll(el) { if (!SDA && el.id === 'pane') dockByScroll(el); },

    on: {
      /* 到场(连续):三拍,间隔递减;每拍上升 16px。别的视图只有工作区一拍 */
      boot(ctx, mutate) {
        mutate();
        M.animate($('.rail'), [{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: 'linear' });
        if (ctx.view !== 'brief') { fadeUp([$('#stage')], { spring: 'settle', dist: 12 }); return; }
        fadeUp([$('.bf-top'), $('.hero-main')], { spring: 'settle', dist: 16 });
        fadeUp([$('.hero-side')], { spring: 'settle', dist: 16, delay: 90 });
        fadeUp([$('.toc'), ...$$('.brief .sec')], { spring: 'settle', dist: 16, delay: 150 });
        M.animate($('.hero-glow'), [{ opacity: 0 }, { opacity: 1 }], { duration: 600, easing: 'linear' });
      },

      /* 切视图(精密):内容直接换,工作区 100ms 淡入;动的只有导轨上的指示器 */
      nav(ctx, mutate) {
        if (ctx.key) return still(mutate);
        withRail(mutate);
        if (ctx.from !== ctx.to) fadeStage();
      },

      /* 打开文章(精密):选中块滑过去;标题区淡入上移 6px;速读卡随正文淡入,分数逐位滚 */
      open(ctx, mutate) {
        if (ctx.key) return still(mutate);
        const paneOnly = ctx.from === 'list' || ctx.from === 'go';
        if (ctx.inQa) exitQa();
        if (paneOnly) mutate(); else withRail(mutate);
        if (!paneOnly) fadeStage();
        enterDoc();
      },
      back(ctx, mutate) {
        if (ctx.key) return still(mutate);
        withRail(mutate); fadeStage();
      },

      /* 榜单进事件页(连续):卡面展开成页,标题是同一个;返回时收回卡里 */
      event(ctx, mutate) {
        if (ctx.key) return still(mutate);
        const card = ctx.open && ctx.el ? ctx.el.closest('.rk') : null;
        if (!VT || (ctx.open && !card)) {
          /* 不是从榜单卡进来的(文章里的事件链接等):没有来处可接,按精密的写法淡入上移 6px */
          withRail(mutate);
          if (ctx.open) M.animate($('#stage .page .wrap'), [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 160, easing: EASE_OUT });
          else fadeStage();
          return;
        }
        resetShare();
        if (ctx.open) {
          M.vt(() => withRail(mutate), {
            type: 'ms-ev-open',
            before() { M.name(card, 'ms-card'); takeOld($('h3', card)); },
            mid() { M.unname(); M.name($('#stage .page'), 'ms-card'); takeNew($('.ev-h h1')); fadeUp([$('.ev-top'), ...$$('.ev-h > :not(h1)'), $('.ev-grid')], { spring: 'settle', dist: 12, delay: 60 }); },
          });
          return;
        }
        const page = $('#stage .page'); const h1 = $('.ev-h h1');
        M.vt(() => withRail(mutate), {
          type: 'ms-ev-back',
          before() { M.name(page, 'ms-card'); takeOld(h1); },
          mid() {
            M.unname();
            const c = $(`.rk[data-event="${CSS.escape(String(ctx.id))}"]`);
            if (inView(c)) { M.name(c, 'ms-card'); takeNew($('h3', c)); } else { root.dataset.vt = 'ms-swap'; M.name($('#stage'), 'ms-stage'); }   // 卡已经滚出视口:没有去处可收,工作区交叉淡化
          },
        });
      },

      /* 来源栏(连续):收起 / 展开走样式里的过渡;从「固定为来源栏」展开时,浮层变形成来源栏 */
      fold(ctx, mutate) {
        if (ctx.key) return still(mutate);
        const pop = $('.pop.srcpop');
        if (!pop) return mutate();
        if (ctx.fold || !VT) { exitPop(pop, 'srcpop'); return mutate(); }
        M.vt(mutate, {
          type: 'ms-unfold',
          before() { M.name(pop, 'ms-srcbar'); M.name($('.list'), 'ms-list'); M.name($('#pane'), 'ms-pane'); },
          mid() { M.unname(); M.name($('.srcbar'), 'ms-srcbar'); M.name($('.list'), 'ms-list'); M.name($('#pane'), 'ms-pane'); },
        });
      },

      /* 浮层(精密):对准触发钮 0.96 → 1 */
      pop(ctx, mutate) {
        if (ctx.key) return still(mutate);
        if (!ctx.open) { exitPop($(ctx.sel), ctx.name, ctx.el); mutate(); return; }
        mutate();
        const p = $(ctx.sel); if (!p) return;
        p.style.transformOrigin = popOrigin(p, ctx.name, M.refind({ sel: TRIG[ctx.name], rect: ctx.rect }));
        M.animate(p, [{ opacity: 0, transform: 'scale(0.96)' }, { opacity: 1, transform: 'none' }], { duration: 150, easing: EASE_OUT });
      },

      /* 问答面板(光):玻璃面板由虚到实,极光沿边一圈,工作区压暗 */
      qa(ctx, mutate) {
        if (ctx.key) return still(mutate);
        if (!ctx.open) { exitQa(); mutate(); return; }
        mutate();
        const el = $('.qa'); if (!el) return;
        M.animate(el, [{ opacity: 0, filter: 'blur(8px)', transform: 'scale(0.985)' }, { opacity: 1, filter: 'blur(0px)', transform: 'none' }], { duration: 260, easing: FOCUS });
        sweep(el); halo($('.rail [data-act="qa"] .ic'));
      },
      /* 提问多半是回车发出的;等待与逐段出现对应的是回答真实的到达过程,所以键盘路径也照常演 */
      qaask(ctx, mutate) { mutate(); answer(); },

      why(ctx, mutate) {
        if (ctx.key) return still(mutate);
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

      /* 筛选(连续):留下的行滑到新位置 */
      filter(ctx, mutate) {
        if (ctx.key) return still(mutate);
        const g = ctx.group;
        exitPop($('.pop.srcpop'), 'srcpop');
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
        if (ctx.key) return still(mutate);
        mutate();
        const b = M.refind(ctx); const ic = b && $('.ic', b); if (!ic) return;
        if (ctx.on) M.animate(ic, [{ transform: 'scale(0.6)' }, { transform: 'scale(1)' }], { spring: SP.pop });
        else M.animate(ic, [{ opacity: 0.35 }, { opacity: 1 }], { duration: 120, easing: 'linear' });
      },
      markall(ctx, mutate) { if (ctx.key) return still(mutate); rowSwap(mutate, !ctx.undo); },
      unread(ctx, mutate) { if (ctx.key) return still(mutate); rowSwap(mutate, false); },
      /* 订阅(连续):来源徽标沿弧线飞到导轨上对应形态的图标,落点弹一下;撤销 / 退订不飞 */
      sub(ctx, mutate) {
        if (ctx.key) return still(mutate);
        const card = ctx.el ? ctx.el.closest('.sc-card') : null; const lg = card ? $('.lg', card) : null;
        const fly = ctx.on && !ctx.undo && lg ? lg.cloneNode(true) : null; const from = lg ? lg.getBoundingClientRect() : null;
        mutate();
        const btn = M.refind(ctx);
        if (btn) M.animate(btn, [{ transform: 'scale(0.9)', opacity: 0.4 }, { transform: 'scale(1)', opacity: 1 }], { spring: SP.pop });
        if (!fly) return;
        const src = window.D && window.D.sources ? window.D.sources[ctx.id] : null;
        const tsel = `.rail-nav [data-nav="${src ? src.shape : 'article'}"] .ic`;
        const target = $(tsel); if (!target) return;
        const to = target.getBoundingClientRect();
        fly.classList.add('ms-fly'); fly.style.setProperty('--s', `${from.width}px`); document.body.appendChild(fly);
        const x0 = from.left; const y0 = from.top; const x2 = to.left + to.width / 2 - from.width / 2; const y2 = to.top + to.height / 2 - from.height / 2;
        const dist = Math.hypot(x2 - x0, y2 - y0); const x1 = (x0 + x2) / 2; const y1 = Math.min(y0, y2) - Math.min(140, dist * 0.22);
        const N = 30; const kf = [];
        for (let i = 0; i <= N; i++) {
          const k = i / N; const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
          const x = (1 - e) * (1 - e) * x0 + 2 * (1 - e) * e * x1 + e * e * x2; const y = (1 - e) * (1 - e) * y0 + 2 * (1 - e) * e * y1 + e * e * y2;
          kf.push({ transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${(1 - 0.45 * e).toFixed(3)})`, opacity: k > 0.9 ? (1 - k) * 10 : 1 });
        }
        const a = M.animate(fly, kf, { duration: 520, easing: 'linear', fill: 'forwards' });
        const land = () => { drop(fly); const ic = $(tsel); if (ic) M.animate(ic, [{ transform: 'scale(1.32)' }, { transform: 'scale(1)' }], { spring: SP.pop }); };
        if (a) a.finished.then(land, () => drop(fly)); else land();
      },
      /* 提示(精密):自下弹入,原路退出;换提示时旧的缩到 0.94 退后 */
      toast(ctx, mutate) {
        const old = $('.toast');
        if (ctx.phase === 'out') {
          M.exit(old, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(calc(100% + 24px))' }], { duration: 180, easing: EASE_OUT });
          mutate(); return;
        }
        if (old) M.exit(old, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.94)' }], { duration: 160, easing: EASE_OUT });
        mutate();
        const t = $('.toast'); if (!t) return;
        M.animate(t, [{ transform: 'translate(-50%, calc(100% + 24px))' }, { transform: 'translate(-50%, 0)' }], { spring: SP.glide });
        fadeIn(t, 120);
      },

      /* 明暗(精密):整页交叉淡化 */
      theme(ctx, mutate) {
        if (ctx.key) return still(mutate);
        M.vt(mutate, { type: 'ms-theme' });
      },
    },

    tokens: [
      { name: '--ease-out', value: 'cubic-bezier(0.23, 1, 0.32, 1)', use: '出入场、位移收尾(现行 --motion-ease 是 0.22, 1, 0.36, 1,并到这一条)' },
      { name: '--ease-drawer', value: 'cubic-bezier(0.32, 0.72, 0, 1)', use: '来源栏收起与展开、「依据」展开' },
      { name: '--ease-focus', value: 'cubic-bezier(0.2, 0.8, 0.2, 1)', use: '对焦:只在问答面板上' },
      { name: '--dur-press', value: '120ms', use: '按下(小件缩到 0.97,整行 / 整卡 0.985,榜单行 0.99)' },
      { name: '--dur-hover-in / out', value: '100ms / 180ms', use: '悬停底色:进快出慢;100ms 也是切视图、返回时工作区的淡入' },
      { name: '--dur-fast', value: '150ms', use: '浮层入(出 100ms)、标题区上移、淡入' },
      { name: '--dur-base', value: '200ms', use: '行状态换色' },
      { name: '--dur-drawer', value: '360ms', use: '来源栏整块滑入 / 滑出' },
      { name: 'spring snappy', value: '0.28s · bounce 0(落定 413ms)', use: '松手回弹' },
      { name: 'spring glide', value: '0.32s · bounce 0.12(落定 446ms)', use: '列表选中块、提示弹入' },
      { name: 'spring move', value: '0.42s · bounce 0.12(落定 583ms)', use: '位移:筛选后的行、速读卡分数逐位滚动(取代 2200ms 里程表)' },
      { name: 'spring settle', value: '0.50s · bounce 0.18(落定 687ms)', use: '大变形:榜单卡展开成页、到场三拍' },
      { name: 'spring pop', value: '0.35s · bounce 0.45(落定 704ms)', use: '落点:星标、导轨图标、订阅钮' },
      { name: 'indicator lead / trail', value: '0.20s / 0.40s(来源栏 0.34 / 0.42)', use: '小指示器滑动:前缘先到、后缘跟上' },
      { name: '--ai-focus', value: '260ms;模糊起点 4–8px', use: '由虚到实:问答面板、回答各段' },
      { name: '--ai-sweep', value: '1150ms', use: '极光沿边一圈:问答面板打开与提问' },
      { name: '--presence-out / in', value: '600ms(延迟 240ms)/ 160ms', use: '沉浸阅读:三栏降到 30% / 一碰就回' },
    ],
    notes: {
      press: '精密:按下 0.97 · 120ms;悬停底色进 100 出 180ms',
      ind: '连续:指示器前缘 0.2s 先到、后缘 0.4s 跟上;行 FLIP',
      pop: '精密:对准触发钮 0.96 → 1 · 150ms;关 100ms',
      open: '精密:选中块滑过去;标题区上移 6px · 160ms',
      nav: '精密:内容直接换 + 100ms 淡入;导轨指示器滑过去',
      fold: '连续:整块滑到导轨下 360ms;浮层变形成来源栏',
      qa: '光:玻璃面板 260ms;极光绕边 1.15s;回答逐段对焦',
      brief: '连续:三拍各升 16px,间隔 90 / 60ms',
      ai: '精密:分数逐位滚 ≈ 580ms(原 2200ms);卡体 120ms 淡入',
      toast: '精密:自下弹入(弹簧 0.32s),出 180ms;星标弹一下',
      theme: '精密:整页交叉淡化 180ms',
      scroll: '连续:大标题随滚动停靠进吸顶栏,可逆',
      event: '连续:卡面展开成页 ≈ 690ms,标题共享;返回收回卡里',
      sub: '连续:徽标沿弧线飞到导轨图标 520ms,落点弹一下',
    },
  });
})();
