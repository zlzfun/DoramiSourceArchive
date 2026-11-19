/* D 刊物 —— 一份每天出版的报(取值与 dir-d.css 顶部的 token 一一对应)。
   两件自造的工具:
   · 翻牌 flapText(el):数字与星期各住一个定宽格,整格绕中线翻转换字;有旧值就像真牌一样顺着往前翻到新值,没有旧值从空牌翻几次落定。
     「月 / 日 / : / .」不翻。翻完把元素还原成原来的 HTML。
   · 逐行揭示 revealLines(el):M.splitLines 按实际折行切开,每行从自己的版线后面升起(transform 与 clip-path 同步,不需要外框裁切),
     结束后 M.unsplit 还原。期间元素被重画掉也不会报错。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
  const T = { line: 520, lineGap: 70, head: 420, rule: 600, flap: 80, wipe: 160, wipeGap: 18, cut: 80, pop: 200 };
  const DIG = '0123456789'; const WK = '一二三四五六日';
  const SHADOW = 28; /* 展开 / 卷回时给投影留的余量 */

  let io = null; let dayIdx = null; let bootTimer = 0;
  const drawn = new Set(); const segLast = {};
  const view = () => { const s = $('#stage'); return (s && s.dataset.view) || ''; };
  const live = (el) => !!(el && el.isConnected);
  const settle = (anim, ms) => Promise.race([anim ? anim.finished.catch(() => {}) : Promise.resolve(), M.wait(ms + 150)]);

  /* ── 基本动作 ── */
  const fade = (el, delay = 0, duration = 180) => M.animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration, delay, easing: 'ease-out' });
  const cut = (el, at = 0) => fade(el, at, T.cut);
  /* 从版线后面升起:元素下移一个自身高度,同时把下沿裁掉同样多——看上去像从一条固定的线后面升出来 */
  const rise = (el, { duration = T.head, delay = 0 } = {}) => M.animate(el, [
    { transform: 'translateY(100%)', clipPath: 'inset(-0.25em -0.4em 100% -0.4em)' },
    { transform: 'translateY(0)', clipPath: 'inset(-0.25em -0.4em 0% -0.4em)' },
  ], { duration, delay, easing: EASE });
  const wipe = (el, { duration = T.wipe, delay = 0 } = {}) => M.animate(el, [{ clipPath: 'inset(0 100% 0 0)' }, { clipPath: 'inset(0 0% 0 0)' }], { duration, delay, easing: EASE });
  const unroll = (el, { duration = T.pop, delay = 0 } = {}) => M.animate(el, [
    { clipPath: `inset(-${SHADOW}px -${SHADOW}px 100% -${SHADOW}px)` }, { clipPath: `inset(-${SHADOW}px -${SHADOW}px -${SHADOW}px -${SHADOW}px)` },
  ], { duration, delay, easing: EASE });
  const rollUp = (el, duration = 120) => M.exit(el, [
    { clipPath: `inset(-${SHADOW}px -${SHADOW}px -${SHADOW}px -${SHADOW}px)` }, { clipPath: `inset(-${SHADOW}px -${SHADOW}px 100% -${SHADOW}px)` },
  ], { duration, easing: EASE });
  const pseudo = (el, pe, keyframes, opt = {}) => {
    if (!live(el)) return null;
    const a = el.animate(keyframes, { fill: 'backwards', easing: EASE, ...opt, pseudoElement: pe });
    if (M.speed !== 1) a.playbackRate = M.speed;
    return a;
  };
  const drawX = (el, delay = 0, duration = T.rule) => M.animate(el, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration, delay, easing: EASE });
  /* 画一条已有的边线:盖一片与底同色的遮片在线上,向右收走 */
  function cover(host, edge = 'bottom', { duration = T.rule, delay = 0 } = {}) {
    if (!live(host)) return;
    const cs = getComputedStyle(host); const w = parseFloat(edge === 'top' ? cs.borderTopWidth : cs.borderBottomWidth) || 1;
    const c = document.createElement('i'); c.className = 'd-cover'; c.setAttribute('aria-hidden', 'true');
    c.style.cssText = `position:absolute;left:0;right:0;${edge}:-${w}px;height:${w + 0.5}px;background:var(--dorami-surface);transform-origin:right;pointer-events:none;z-index:2;`;
    host.appendChild(c);
    const a = M.animate(c, [{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }], { duration, delay, easing: EASE, fill: 'both' });
    settle(a, duration + delay).then(() => c.remove());
  }
  function wipeRows(rows, { each = T.wipe, gap = T.wipeGap, max = 8, delay = 0 } = {}) {
    rows.forEach((r, i) => wipe(r, { duration: each, delay: delay + Math.min(i, max - 1) * gap }));
  }
  const fontsReady = (ms) => Promise.race([document.fonts && document.fonts.status !== 'loaded' ? document.fonts.ready : Promise.resolve(), new Promise((r) => setTimeout(r, ms))]);

  /* ── 逐行揭示 ── */
  function revealLines(el, { gap = T.lineGap, duration = T.line, delay = 0 } = {}) {
    if (!live(el)) return Promise.resolve();
    const lines = M.splitLines(el);
    if (!lines.length) return Promise.resolve();
    /* 分行时 <wbr> 被拿掉,句读(.pz)后面只剩文字节点,会被底稿的 .pz:last-child 误判成「标题末尾」而松开挤压——这里按原样补回 */
    $$('.pz', el).forEach((p) => { if (p.nextSibling || p.closest('.m-line') !== el.lastElementChild) p.style.marginRight = '-0.42em'; });
    let last = null;
    lines.forEach((ln, i) => { last = rise(ln, { duration, delay: delay + i * gap }); });
    return settle(last, delay + (lines.length - 1) * gap + duration).then(() => M.unsplit(el));
  }

  /* ── 翻牌 ── */
  const lead = (ch, n) => { /* 没有旧值:从空牌翻 n 次落到 ch */
    const set = DIG.includes(ch) ? DIG : WK; const i = set.indexOf(ch); const out = [];
    for (let k = n - 1; k >= 0; k--) out.push(set[(i - k + set.length * 4) % set.length]);
    return out;
  };
  const forward = (from, ch, cap = 5) => { /* 有旧值:顺着牌序往前翻到 ch(太远就只翻最后几张) */
    if (from === ch) return [];
    const set = DIG.includes(ch) && DIG.includes(from) ? DIG : WK.includes(ch) && WK.includes(from) ? WK : null;
    if (!set) return [ch];
    const out = []; let i = set.indexOf(from);
    do { i = (i + 1) % set.length; out.push(set[i]); } while (set[i] !== ch);
    return out.length > cap ? out.slice(out.length - cap) : out;
  };
  async function runCell(cell, a, b, seq, step, delay) {
    let d = delay; const half = step / 2;
    for (const ch of seq) {
      if (!cell.isConnected) return;
      b.textContent = ch;
      const A = M.animate(a, [{ transform: 'perspective(3em) rotateX(0deg)', opacity: 1 }, { transform: 'perspective(3em) rotateX(-90deg)', opacity: 0.4 }], { duration: half, delay: d, easing: 'cubic-bezier(0.4, 0, 1, 0.6)', fill: 'both' });
      const B = M.animate(b, [{ transform: 'perspective(3em) rotateX(90deg)', opacity: 0.4 }, { transform: 'perspective(3em) rotateX(0deg)', opacity: 1 }], { duration: half, delay: d + half, easing: 'cubic-bezier(0, 0.4, 0.4, 1)', fill: 'both' });
      await settle(B, d + step);
      a.textContent = ch;
      if (A) A.cancel(); if (B) B.cancel();
      d = 0;
    }
  }
  const unflap = (el) => { if (el && el._dfOrig != null) { el.innerHTML = el._dfOrig; el._dfOrig = null; el.classList.remove('df-on'); } };
  /* opt.from:旧文字(按第几个会翻的字符对位);opt.all:所有非空白字符都翻(按钮换字用);opt.dotGap:小数点后的格子晚多少起步 */
  function flapText(el, { delay = 0, steps = 4, step = T.flap, gap = 34, dotGap = 0, from = null, all = false, dot = false } = {}) {
    if (!live(el) || M.reduced()) return Promise.resolve();
    unflap(el);
    const isFl = (ch, prev) => (all ? /\S/.test(ch) : DIG.includes(ch) || (WK.includes(ch) && prev === '周'));
    let fromFl = null;
    if (from != null) { fromFl = []; let pv = ''; for (const ch of from) { if (isFl(ch, pv)) fromFl.push(ch); pv = ch; } }
    const orig = el.innerHTML;
    const texts = []; const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (tw.nextNode()) texts.push(tw.currentNode);
    const cells = []; let late = false; let prev = '';
    texts.forEach((n) => {
      if (!n.data.trim()) return;
      const run = document.createElement('span'); run.className = 'df-run'; let buf = '';
      const flush = () => { if (buf) { const s = document.createElement('span'); s.className = 'df-s'; s.textContent = buf; run.appendChild(s); buf = ''; } };
      for (const ch of n.data) {
        const isDot = dot && from == null && ch === '.' && cells.length > 0;
        if (isFl(ch, prev) || isDot) {
          flush();
          const cell = document.createElement('span'); cell.className = 'df-cell';
          cell.innerHTML = '<span class="df-size"></span><span class="df-a df-g"></span><span class="df-b df-g"></span>';
          cell.firstChild.textContent = ch; run.appendChild(cell); cells.push({ cell, ch, late, isDot });
          if (isDot) late = true;
        } else { buf += ch; if (ch === '.') late = true; }
        prev = ch;
      }
      flush(); n.replaceWith(run);
    });
    if (!cells.length) { el.innerHTML = orig; return Promise.resolve(); }
    el._dfOrig = orig; el.classList.add('df-on');
    const jobs = cells.map(({ cell, ch, late: lt, isDot }, i) => {
      const f = fromFl ? (fromFl[i] != null ? fromFl[i] : '') : null;
      /* 小数点跟着前一格的第一翻一起落下,免得空牌期间只剩一个孤零零的点 */
      const seq = isDot ? ['.'] : f == null ? lead(ch, steps) : f === '' ? [ch] : forward(f, ch);
      const a = cell.children[1]; const b = cell.children[2];
      a.textContent = f == null ? '' : seq.length ? f : ch;
      return runCell(cell, a, b, seq, step, delay + (isDot ? i - 1 : i) * gap + (lt ? dotGap : 0));
    });
    return Promise.all(jobs).then(() => { if (el._dfOrig === orig) { if (el.isConnected) el.innerHTML = orig; el._dfOrig = null; el.classList.remove('df-on'); } });
  }

  /* ── 指示器 ── */
  const stretch = (axis) => (p, r) => {
    if (axis === 'y') {
      const u = Math.min(p.y, r.y); const ul = Math.max(p.y + p.h, r.y + r.h) - u;
      return [{ transform: `translate(0, ${p.y - r.y}px) scale(1, ${p.h / r.h})` }, { transform: `translate(0, ${u - r.y}px) scale(1, ${ul / r.h})`, offset: 0.4 }, { transform: 'none' }];
    }
    const u = Math.min(p.x, r.x); const ul = Math.max(p.x + p.w, r.x + r.w) - u;
    return [{ transform: `translate(${p.x - r.x}px, ${p.y - r.y}px) scale(${p.w / r.w}, 1)` }, { transform: `translate(${u - r.x}px, 0) scale(${ul / r.w}, 1)`, offset: 0.4 }, { transform: 'none' }];
  };
  function bar(key, host, axis, target) {
    if (!host) return;
    const sel = `:scope > .m-ind[data-ind="${key}"]`;
    if (!target) { const old = host.querySelector(sel); if (old) old.remove(); M.forgetGlide(key); return; }
    /* 自己先把指示器放在末尾:公共层是 prepend,会把 .kinds button:nth-child(n) 的次序顶歪 */
    if (!host.querySelector(sel)) { const i = document.createElement('i'); i.className = 'm-ind'; i.dataset.ind = key; i.setAttribute('aria-hidden', 'true'); host.appendChild(i); }
    M.glide(key, host, target, { keyframes: stretch(axis), timing: { duration: 340, easing: EASE } });
  }
  function syncBars() {
    const v = view();
    $$('.tabs').forEach((t) => bar(`d-tabs:${v}:${t.closest('.doc') ? 'doc' : 'page'}`, t, 'x', $(':scope > button.on', t)));
    $$('.kinds').forEach((k) => bar(`d-kinds:${v}`, k, 'x', $(':scope > button.on', k)));
    const rail = $('.rail'); if (rail) bar('d-rail', rail, 'y', $('.rail-nav a.on', rail));
  }
  const segKey = (seg) => `${view()}:${seg.closest('.srcbar') ? 'src' : seg.closest('.pane-bar') ? 'pane' : seg.closest('.pop') ? 'pop' : seg.closest('.pg-bar') ? 'bar' : seg.closest('.list') ? 'list' : 'page'}`;
  function syncSegs() {
    $$('.seg').forEach((seg) => {
      const on = $(':scope > button.on', seg); const key = segKey(seg);
      let th = $(':scope > .d-thumb:not(.d-thumb-old)', seg);
      if (!on) { if (th) th.remove(); delete segLast[key]; return; }
      if (!th) { th = document.createElement('i'); th.className = 'd-thumb'; th.setAttribute('aria-hidden', 'true'); seg.appendChild(th); }
      const h = seg.getBoundingClientRect(); const b = on.getBoundingClientRect();
      const r = { x: b.left - h.left - seg.clientLeft, y: b.top - h.top - seg.clientTop, w: b.width, h: b.height };
      const put = (el, q) => Object.assign(el.style, { left: `${q.x}px`, top: `${q.y}px`, width: `${q.w}px`, height: `${q.h}px` });
      put(th, r);
      const p = segLast[key]; segLast[key] = r;
      if (!p || Math.abs(p.x - r.x) < 0.5 || M.reduced()) return;
      const right = r.x > p.x; const rad = getComputedStyle(th).borderRadius || '7px';
      const hid = right ? `inset(0 100% 0 0 round ${rad})` : `inset(0 0 0 100% round ${rad})`;
      const gone = right ? `inset(0 0 0 100% round ${rad})` : `inset(0 100% 0 0 round ${rad})`;
      const full = `inset(0 0% 0 0% round ${rad})`;
      M.animate(th, [{ clipPath: hid }, { clipPath: full }], { duration: 200, easing: EASE });
      const old = document.createElement('i'); old.className = 'd-thumb d-thumb-old'; old.setAttribute('aria-hidden', 'true'); put(old, p); seg.appendChild(old);
      const a = M.animate(old, [{ clipPath: full }, { clipPath: gone }], { duration: 200, easing: EASE, fill: 'forwards' });
      settle(a, 200).then(() => old.remove());
    });
  }

  /* ── 重画之后要补上的常驻件 ── */
  function wrapTitles() {
    $$('.row h3, .bi h3, .rk h3, .tl-i[data-open] h4').forEach((h) => {
      const f = h.firstElementChild;
      if (f && f.classList.contains('d-ul') && h.childNodes.length === 1) return;
      const s = document.createElement('span'); s.className = 'd-ul';
      while (h.firstChild) s.appendChild(h.firstChild);
      h.appendChild(s);
    });
  }
  function syncBarTitle() {
    const pane = $('#pane'); const bar = pane && $('.pane-bar', pane); const h1 = pane && $('.doc h1', pane);
    if (!bar || !h1) return;
    let t = $('.d-bar-title', bar);
    if (!t) {
      t = document.createElement('span'); t.className = 'd-bar-title'; t.innerHTML = '<span></span>'; t.firstChild.textContent = h1.textContent;
      const anchor = $('.pane-src, .back', bar); if (anchor) anchor.after(t); else bar.prepend(t);
    }
    bar.classList.toggle('d-titled', h1.getBoundingClientRect().bottom < bar.getBoundingClientRect().bottom + 2);
  }
  function watchSecs() {
    if (io) { io.disconnect(); io = null; }
    const pg = $('.page.brief'); if (!pg) return;
    io = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add('d-in'); drawn.add(e.target.dataset.sec); if (io) io.unobserve(e.target);
    }), { root: pg, rootMargin: '0px 0px -10% 0px' });
    $$('.sec', pg).forEach((s) => { if (drawn.has(s.dataset.sec) || M.reduced()) s.classList.add('d-in'); else io.observe(s); });
  }
  /* 列表吸顶日期:顶上换了一天,那一天的数字从前一天翻过来 */
  function stickyDay(list) {
    const days = $$(':scope > .day', list); if (days.length < 2) return;
    const top = list.getBoundingClientRect().top + list.clientTop; let cur = 0;
    days.forEach((d, i) => { if (d.getBoundingClientRect().top <= top + 0.5) cur = i; });
    if (dayIdx != null && cur !== dayIdx && days[dayIdx] && !M.reduced()) flapText(days[cur], { from: days[dayIdx].textContent, step: 70, gap: 24 });
    dayIdx = cur;
  }

  /* ── 各页的进场 ── */
  /* 换视图:页头大字从版线后升起,页头细线从左重画;内容硬切 */
  function enterView(v) {
    const st = $('#stage'); if (st && st.firstElementChild) cut(st.firstElementChild);
    if (v === 'brief') { rise($('.bf-id h1')); drawX($('.bf-id > i'), 60, 500); return; }
    if (v === 'feed') {
      rise($('.srcbar .col-h h2')); rise($('.list .scope span'));
      cover($('.pane-bar'), 'bottom', { duration: 500 });
      return;
    }
    rise($('.pg-h h1')); cover($('.pg-bar'), 'bottom', { duration: 500 });
  }
  /* 「开版」:今天第一次打开早报 */
  function openEdition() {
    const pg = $('.page.brief'); if (!pg) return;
    root.classList.add('d-booting'); clearTimeout(bootTimer); bootTimer = M.after(1000, () => root.classList.remove('d-booting'));
    rise($('.bf-id h1', pg), { duration: 460 });
    drawX($('.bf-id > i', pg), 80);
    flapText($('.bf-date', pg), { delay: 140, steps: 4, gap: 34 });
    flapText($('.bf-ed > span', pg), { delay: 260, steps: 3, gap: 34 });
    fade($('.hero-glow', pg), 0, 500);
    const kick = $('.hero .kick', pg);
    if (kick) { pseudo(kick, '::before', [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 320, delay: 60 }); $$(':scope > *', kick).forEach((s) => fade(s, 120, 160)); }
    /* 头条逐行升起。折行点量的是当时的字形:字体没到位就先等一下(至多 350ms),免得按后备字体折错行 */
    const title = $('.hero-title', pg);
    if (title) {
      title.getBoundingClientRect(); title.style.visibility = 'hidden';
      fontsReady(350).then(() => { title.style.visibility = ''; revealLines(title, { gap: 90, duration: 700, delay: 60 }); });
    }
    $$('.hero-orig, .hero-by, .hero-sum, .hero-act', pg).forEach((el) => fade(el, 380, 180));
    const side = $('.hero-side', pg);
    if (side) M.animate(side, [{ clipPath: 'inset(0 0 100% 0)' }, { clipPath: 'inset(0 0 0% 0)' }], { duration: 640, delay: 300, easing: EASE });
    flapText($('.hero-score .sc', pg), { delay: 560, steps: 5, gap: 0, dotGap: 120, dot: true });
    const toc = $('.toc', pg);
    if (toc) { cut(toc, 300); cover(toc, 'top', { delay: 300 }); cover(toc, 'bottom', { delay: 380 }); }
    $$('.sec', pg).forEach((s) => cut(s, 300));
  }
  /* 阅读窗:标题逐行升起,眉题前的短线先画,署名晚 200ms,卡与正文 120ms 处切入 */
  const mainScore = (scope = document) => [$('.side-blk:not(.minor) .side-score .sc', scope), $('.ai-scores .sc', scope)].find((e) => e && e.offsetParent);
  function paneEnter(prevScore) {
    const doc = $('#pane .doc'); if (!doc) return;
    const kick = $('.doc-h .kick', doc);
    if (kick) { pseudo(kick, '::before', [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 300 }); $$(':scope > *', kick).forEach((s) => fade(s, 80, 160)); }
    revealLines($('.doc-h h1', doc));
    $$('.doc-h .orig, .doc-by, .doc-cover, .doc-act', doc).forEach((el) => fade(el, 200, 200));
    $$(':scope > .ai, :scope > .side, :scope > .prose, :scope > .tabs, :scope > .note, :scope > .doc-end', doc).forEach((el) => cut(el, 120));
    /* 速读卡:卡头栏线画出,各句自左向右擦出,分数翻牌(十分位晚 120ms) */
    pseudo($('.ai header', doc), '::after', [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: T.rule, delay: 140 });
    $$('.ai-body p', doc).slice(0, 6).forEach((p, i) => wipe(p, { duration: 260, delay: 140 + i * 60 }));
    flapText(mainScore(doc), { delay: 180, steps: 5, gap: 0, dotGap: 120, dot: true, from: prevScore || null });
  }
  function eventEnter(rank) {
    const pg = $('.page.event') || $('#stage .page'); if (!pg) return;
    cut(pg);
    const head = $('.ev-h', pg);
    const kick = $('.ev-h .kick', pg);
    if (kick) { pseudo(kick, '::before', [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 300 }); flapText($('span', kick), { delay: 80, steps: 3, from: rank || null }); }
    revealLines($('.ev-h h1', pg));
    fade($('.ev-h .doc-by', pg), 200, 200);
    if (head && head.querySelector('.kick')) cover(head, 'bottom', { delay: 100 });
    cut($('.ev-grid', pg), 120);
    $$('.tl-i time', pg).slice(0, 6).forEach((t, i) => flapText(t, { delay: 240 + i * 60, steps: 3, gap: 20 }));
  }

  M.register('d', {
    name: '刊物',
    init() { drawn.clear(); dayIdx = null; Object.keys(segLast).forEach((k) => delete segLast[k]); },
    dispose() {
      if (io) { io.disconnect(); io = null; }
      clearTimeout(bootTimer); root.classList.remove('d-booting');
      $$('.d-thumb, .d-cover, .d-bar-title, .d-roller, .m-ind[data-ind^="d-"]').forEach((e) => e.remove());
      $$('.d-ul').forEach((s) => { const h = s.parentNode; while (s.firstChild) h.insertBefore(s.firstChild, s); s.remove(); });
      $$('.df-on').forEach(unflap);
      $$('.d-in, .d-titled, .d-folding').forEach((e) => e.classList.remove('d-in', 'd-titled', 'd-folding'));
      $$('.hero-title, .doc h1, .ev-h h1').forEach((e) => { M.unsplit(e); e.style.visibility = ''; });
      ['d-rail'].forEach((k) => M.forgetGlide(k));
      drawn.clear(); dayIdx = null;
    },
    rendered(scope) {
      if (scope === 'qa' || scope === 'qa-body') return;
      wrapTitles(); syncBars(); syncSegs(); syncBarTitle(); watchSecs(); dayIdx = null;
    },
    scroll(el) {
      if (el.id === 'pane') {
        const bar = $('.pane-bar', el); const h1 = $('.doc h1', el);
        if (bar && h1) bar.classList.toggle('d-titled', h1.getBoundingClientRect().bottom < bar.getBoundingClientRect().bottom + 2);
      } else if (el.classList.contains('list-b')) stickyDay(el);
    },
    on: {
      boot(ctx, mutate) {
        drawn.clear(); mutate();
        if (ctx.view === 'brief') openEdition(); else enterView(view());
      },
      nav(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        drawn.clear(); mutate(); enterView(view());
      },
      back(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        drawn.clear(); mutate(); enterView(view());
      },
      open(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        const prev = (mainScore() || {}).textContent;
        if (ctx.from === 'list' || ctx.from === 'go') { mutate(); paneEnter(prev); return; }
        /* 从早报 / 榜单 / 事件页 / 发现进来:整页换成列表 + 阅读窗 */
        if (ctx.inQa) M.exit($('.qa'), [{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(28px)', opacity: 0 }], { duration: 160, easing: EASE });
        mutate(); enterView('feed'); paneEnter(null);
      },
      event(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        const b = ctx.open && ctx.el ? $('.rk-n b', ctx.el) : null;
        const rank = b ? b.textContent : null;
        mutate();
        if (ctx.open) eventEnter(rank); else { const st = $('#stage'); if (st && st.firstElementChild) cut(st.firstElementChild); }
      },
      fold(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        const pop = $('.pop.srcpop'); if (pop) rollUp(pop);
        mutate();
        const feed = $('.feed'); if (!feed) return;
        feed.classList.add('d-folding'); M.after(320, () => feed.classList.remove('d-folding'));
        /* 从浮层「固定为来源栏」展开时工作区整段重画,列宽过渡接不上,用 WAAPI 补一段 */
        if (pop && !ctx.fold) {
          const cs = getComputedStyle(root); const sw = cs.getPropertyValue('--src-w').trim() || '232px'; const lw = cs.getPropertyValue('--list-w').trim() || '372px';
          M.animate(feed, [{ gridTemplateColumns: `0px ${lw} minmax(0, 1fr)` }, { gridTemplateColumns: `${sw} ${lw} minmax(0, 1fr)` }], { duration: 260, easing: EASE });
        }
      },
      pop(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        if (!ctx.open) { rollUp($(ctx.sel)); mutate(); return; }
        mutate(); unroll($(ctx.sel));
      },
      qa(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        if (!ctx.open) { M.exit($('.qa'), [{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(28px)', opacity: 0 }], { duration: 160, easing: EASE }); mutate(); return; }
        mutate();
        const p = $('.qa'); if (!p) return;
        M.animate(p, [{ transform: 'translateX(32px)', opacity: 0 }, { opacity: 1, offset: 0.3 }, { transform: 'translateX(0)', opacity: 1 }], { duration: 300, easing: EASE });
        pseudo(p, '::before', [{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], { duration: T.rule, delay: 120 });
      },
      qaask(ctx, mutate) {
        mutate();
        const a = $('.qa-b .qa-a'); if (!a) return;
        /* 回答逐行排出:每段按行数分级裁切,一行一格(28ms);角标在它那一行出现时落下 */
        const LG = 28; let line = 0; const t0 = ctx.key ? 0 : 100;
        $$(':scope > p:not(.qa-demo)', a).forEach((p) => {
          const lh = parseFloat(getComputedStyle(p).lineHeight) || 25.5; const box = p.getBoundingClientRect();
          const n = Math.max(1, Math.round(box.height / lh));
          M.animate(p, [{ clipPath: 'inset(0 0 100% 0)' }, { clipPath: 'inset(0 0 0% 0)' }], { duration: n * LG, delay: t0 + line * LG, easing: `steps(${n}, end)` });
          $$('sup', p).forEach((s) => {
            const at = Math.max(0, Math.round((s.getBoundingClientRect().top - box.top) / lh));
            M.animate(s, [{ transform: 'scale(1.4)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], { duration: 140, delay: t0 + (line + at + 1) * LG + 30, easing: EASE });
          });
          line += n;
        });
        const end = t0 + line * LG;
        const src = $('.qa-src', a);
        if (src) { cut(src, end + 20); $$('button', src).forEach((b, i) => wipe(b, { duration: 180, delay: end + 40 + i * 40 })); }
        cut($('.qa-demo', a), end + 200);
      },
      why(ctx, mutate) {
        mutate(); if (ctx.key || !ctx.open) return;
        const w = $('.ai-why'); if (w && w.offsetParent) M.animate(w, [{ clipPath: 'inset(0 0 100% 0)' }, { clipPath: 'inset(0 0 0% 0)' }], { duration: 200, easing: EASE });
      },
      filter(ctx, mutate) {
        mutate(); if (ctx.key) return;
        const g = ctx.group;
        if (g === 'unread' || g === 'kind' || g === 'src' || g === 'favonly') wipeRows($$('.list-b .row').slice(0, 14));
        else if (g === 'ranktab' || g === 'rankwin') wipeRows($$('.rk-list .rk').slice(0, 8), { each: 200, gap: 26, max: 6 });
        else if (g === 'disctab' || g === 'discshape') wipeRows($$('.sc-card, .coll').slice(0, 12), { gap: 14 });
        else if (g === 'podtab') cut($('.doc .prose'));
      },
      fav(ctx, mutate) {
        mutate(); if (ctx.key || !ctx.on) return;
        /* 盖章:略大、略歪地落下,回正 */
        const el = M.refind(ctx); const ic = el && $('.ic', el);
        M.animate(ic, [{ transform: 'scale(1.5) rotate(-8deg)', opacity: 0 }, { transform: 'scale(0.94) rotate(2deg)', opacity: 1, offset: 0.7 }, { transform: 'scale(1) rotate(0deg)', opacity: 1 }], { duration: 220, easing: 'cubic-bezier(0.2, 0, 0, 1)' });
      },
      sub(ctx, mutate) {
        const before = ctx.el && ctx.el.isConnected ? ctx.el.textContent.trim() : '';
        mutate(); if (ctx.key || ctx.undo || !ctx.on) return;
        const b = M.refind(ctx); if (!b) return;
        const lab = $$('span', b).find((s) => s.offsetParent !== null && s.textContent.trim());
        if (lab) flapText(lab, { all: true, from: before, step: 110, gap: 50 });
        const path = $('svg path', b);
        if (path && path.getTotalLength) { const len = path.getTotalLength(); M.animate(path, [{ strokeDasharray: len, strokeDashoffset: len }, { strokeDasharray: len, strokeDashoffset: 0 }], { duration: 220, delay: 60, easing: EASE }); }
      },
      markall(ctx, mutate) {
        mutate(); if (ctx.key || ctx.undo) return;
        wipeRows($$('.list-b .row').slice(0, 12), { each: 140, gap: 20, max: 10 });
      },
      toast(ctx, mutate) {
        const full = 'inset(-20px -20px -20px -20px round 10px)'; const shut = 'inset(-20px 50% -20px 50% round 10px)';
        if (ctx.phase === 'out') { M.exit($('.toast'), [{ clipPath: full }, { clipPath: shut }], { duration: 140, easing: EASE }); mutate(); return; }
        mutate();
        const t = $('.toast'); if (!t) return;
        /* 提示条自中线向两侧展开,字从版线后升起 */
        M.animate(t, [{ clipPath: shut }, { clipPath: full }], { duration: 220, easing: EASE });
        rise($('span', t), { duration: 300, delay: 60 });
        fade($('button', t), 140, 120);
      },
      theme(ctx, mutate) {
        if (ctx.key) { mutate(); return; }
        M.vt(mutate, {
          type: 'd-theme',
          mid() { const r = document.createElement('i'); r.className = 'd-roller'; r.setAttribute('aria-hidden', 'true'); document.body.appendChild(r); },
          done() { $$('.d-roller').forEach((r) => r.remove()); },
        });
      },
    },
    tokens: [
      { name: '--md-ease', value: 'cubic-bezier(0.16, 1, 0.3, 1)', use: '揭示:字行升起、画线、擦出、浮层展开' },
      { name: '--md-ease-io', value: 'cubic-bezier(0.65, 0, 0.35, 1)', use: '明暗翻页(整屏压过)' },
      { name: '--md-line / --md-line-gap', value: '520ms / 70ms', use: '多行标题逐行升起(早报头条 700ms / 90ms)' },
      { name: '--md-head', value: '420ms', use: '单行页头大字升起' },
      { name: '--md-rule', value: '600ms', use: '一条细线从左画到右' },
      { name: '--md-flap', value: '80ms × 3–5 次', use: '翻牌:数字、星期、钟点(逐格错开 34ms)' },
      { name: '--md-wipe / --md-wipe-gap', value: '160ms / 18ms', use: '列表行逐行擦出(至多 8 行错峰)' },
      { name: '--md-cut', value: '80ms', use: '硬切的垫帧:大块内容不淡入' },
      { name: '--md-underline', value: '220ms(收 120ms)', use: '悬停标题下划线' },
      { name: '--md-pop', value: '200ms(卷回 120ms)', use: '浮层自上沿展开' },
      { name: '--md-page', value: '560ms', use: '明暗「翻页」' },
    ],
    notes: {
      press: '标题下划线 220ms 画出;箭头前挪 3px;按下沉 1px',
      ind: '墨线先拉长再收 340ms;拇指擦除 200ms;新行逐行擦出',
      pop: '自上沿展开 200ms;向上卷回 120ms;Esc 直接关',
      open: '标题逐行升起 520ms、行间 70ms;正文 120ms 处切入',
      nav: '页头大字升起 420ms、细线重画 500ms;内容硬切',
      fold: '列宽 260ms,栏缘带一条细线',
      qa: '右侧滑入 300ms、左缘线下画;回答逐行 28ms、角标盖章',
      brief: '开版 ≈ 1.2s:报头翻牌、头条逐行升起 700ms、细线画出',
      ai: '分数翻牌 ≈ 450ms;各句擦出 260ms、句间 60ms',
      toast: '自中线展开 220ms、字升起;星标盖章 220ms',
      theme: '翻页:自上而下压过 560ms,压线带细影',
      scroll: '小标题升入吸顶栏 280ms;吸顶日期翻牌;节线入视口画出',
      event: '名次与钟点翻牌、标题逐行升起;返回硬切',
      sub: '按钮字逐格翻成「已订阅」,对勾 220ms 画出',
    },
  });
})();
