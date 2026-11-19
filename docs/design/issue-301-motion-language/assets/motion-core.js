/* 哆啦美 · 动效样页 —— 公共层。各方向的取值一概不在这里:这里只有调度、度量与演示工具。

   方向文件(assets/dir-*.js)这样接:
     M.register('a', {
       name: '精密',
       init() {}, dispose() {},        // 切到 / 切走这个方向
       rendered(scope) {},             // 每次重画之后;scope = app | stage | pane | qa | qa-body
       scroll(el) {},                  // 任一滚动容器滚动
       on: { open(ctx, mutate) {}, nav(ctx, mutate) {}, … },   // 接管某类交互;必须调用 mutate() 恰好一次
       notes: { open: '…', nav: '…' }, // 对照页每个场景下的一行参数
     });
   没有接的 kind 直接 mutate()。减少动效(<html data-motion="reduce">)时所有 handler 都跳过。

   工具:M.spring / M.animate / M.after / M.wait / M.vt / M.name / M.flip / M.glide / M.ghost / M.exit /
        M.refind / M.splitLines / M.unsplit / M.rect / M.on
   演示:M.demo.play(场景名) 用一枚假光标按脚本循环操作;约定方向的悬停 / 按下样式写成
        :is(:hover, .m-hover) 与 :is(:active, .m-press),假光标才演得出来。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const springCache = {};
  const lastInd = {};
  const subs = {};
  let vtSeq = 0; let named = []; let spLoop = 0;

  const M = (window.M = {
    dir: '0', dirs: {}, speed: 1, pointer: { x: -1, y: -1 },
    register(id, def) { this.dirs[id] = def; },
    cur() { return this.dirs[this.dir] || {}; },
    reduced() { return root.dataset.motion === 'reduce'; },
    on(name, fn) { (subs[name] = subs[name] || []).push(fn); },
    emit(name, arg) {
      const d = this.cur();
      if (typeof d[name] === 'function') { try { d[name](arg); } catch (e) { console.error('[motion]', this.dir, name, e); } }
      (subs[name] || []).forEach((fn) => { try { fn(arg); } catch (e) { console.error('[motion]', name, e); } });
    },
    setDir(id) {
      const old = this.cur();
      if (old.dispose) { try { old.dispose(); } catch (e) { console.error(e); } }
      Object.keys(lastInd).forEach((k) => delete lastInd[k]);
      this.clearGhosts(); this.unname();
      this.dir = id; root.dataset.dir = id;
      const d = this.cur();
      if (d.init) { try { d.init(); } catch (e) { console.error(e); } }
    },

    /* 交互入口。ctx.el 是触发元素(重画前的),ctx.rect 是它当时的位置,ctx.sel 是重画后找回它的选择器 */
    run(kind, ctx, mutate) {
      ctx = ctx || {};
      let done = false;
      const once = () => { if (done) return; done = true; mutate(); };
      const h = !this.reduced() && this.cur().on && this.cur().on[kind];
      if (!h) { once(); return; }
      ctx.kind = kind;
      ctx.rect = ctx.el && ctx.el.isConnected ? ctx.el.getBoundingClientRect() : null;
      try { h.call(this.cur(), ctx, once); } catch (e) { console.error('[motion]', this.dir, kind, e); once(); }
      if (!done) setTimeout(() => { if (!done) { console.warn('[motion] mutate 超时未调用:', this.dir, kind); once(); } }, 1500 / this.speed);
    },

    /* ── 时间 ── */
    /* 弹簧取样成 linear():duration 是「感知时长」(秒),bounce 0 = 临界阻尼,0.2 = 略有回弹。返回的 duration(毫秒)是落定时长 */
    spring({ duration = 0.4, bounce = 0 } = {}) {
      const key = `${duration}|${bounce}`;
      if (springCache[key]) return springCache[key];
      const zeta = Math.max(0.05, 1 - bounce); const w0 = (2 * Math.PI) / duration;
      const x = (t) => {
        if (zeta >= 1) return 1 - Math.exp(-w0 * t) * (1 + w0 * t);
        const wd = w0 * Math.sqrt(1 - zeta * zeta);
        return 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0) / wd) * Math.sin(wd * t));
      };
      let T = duration; const step = 1 / 240;
      for (let t = duration * 5; t > 0; t -= step) { if (Math.abs(x(t) - 1) > 0.001) { T = t + step; break; } }
      const n = Math.max(16, Math.min(64, Math.round(T * 60)));
      const pts = []; for (let i = 0; i <= n; i++) pts.push(+x((T * i) / n).toFixed(4));
      pts[n] = 1;
      return (springCache[key] = { easing: `linear(${pts.join(', ')})`, duration: Math.round(T * 1000) });
    },
    /* WAAPI 的薄封装:默认只在延迟期垫首帧(fill: backwards),结束后回到样式表的值;opt.spring 直接给弹簧 */
    animate(el, keyframes, opt = {}) {
      if (!el || !el.animate) return null;
      const o = { fill: 'backwards', easing: 'ease-out', ...opt };
      if (o.spring) { const s = this.spring(o.spring); o.easing = s.easing; o.duration = s.duration; delete o.spring; }
      const a = el.animate(keyframes, o);
      if (this.speed !== 1) a.playbackRate = this.speed;
      return a;
    },
    after(ms, fn) { return setTimeout(fn, ms / this.speed); },
    wait(ms) { return new Promise((r) => setTimeout(r, ms / this.speed)); },
    setSpeed(v) {
      this.speed = v > 0 ? v : 1; root.style.setProperty('--m-speed', this.speed);
      const tick = () => {
        const s = M.speed;
        for (const a of document.getAnimations()) { if (a.id !== 'm-ui' && a.playbackRate !== s) a.playbackRate = s; }
        spLoop = s !== 1 ? requestAnimationFrame(tick) : 0;
      };
      if (!spLoop) tick();
    },

    /* ── 度量 ── */
    rect(el) { return el ? el.getBoundingClientRect() : null; },
    /* 重画之后找回触发元素:同选择器里离原位置最近的那个 */
    refind(ctx) {
      const els = ctx.sel ? $$(ctx.sel) : [];
      if (els.length < 2 || !ctx.rect) return els[0] || null;
      const cx = ctx.rect.left + ctx.rect.width / 2; const cy = ctx.rect.top + ctx.rect.height / 2;
      let best = null; let bd = Infinity;
      els.forEach((e) => { const r = e.getBoundingClientRect(); const d = Math.hypot(r.left + r.width / 2 - cx, r.top + r.height / 2 - cy); if (d < bd) { bd = d; best = e; } });
      return best;
    },

    /* ── View Transitions ──
       M.vt(mutate, { type, before, mid, ready, done }):before 里给旧界面的元素起名(M.name),mid 里给新界面的起名;
       样式里用 :root[data-vt~="<type>"]::view-transition-*(…) 写这一类过渡。结束后名字自动清掉。
       注意:后一个过渡会让前一个直接跳到终态——View Transitions 不可中途改向,只给低频的大转场用。 */
    vt(mutate, opt = {}) {
      const plain = () => { if (opt.before) opt.before(); mutate(); if (opt.mid) opt.mid(); this.unname(); if (opt.done) opt.done(); return Promise.resolve(); };
      if (!document.startViewTransition || this.reduced()) return plain();
      const my = ++vtSeq;
      this.unname();
      root.dataset.vt = opt.type || '';
      if (opt.before) opt.before();
      let t;
      try { t = document.startViewTransition(() => { mutate(); if (opt.mid) opt.mid(); }); } catch (e) { console.error(e); delete root.dataset.vt; mutate(); return Promise.resolve(); }
      t.ready.then(() => { if (this.speed !== 1) for (const a of document.getAnimations()) a.playbackRate = this.speed; if (opt.ready) opt.ready(t); }, () => {});
      return t.finished.then(() => {}, () => {}).then(() => { if (my === vtSeq) { delete root.dataset.vt; this.unname(); } if (opt.done) opt.done(); });
    },
    name(el, n) { if (!el) return; el.style.viewTransitionName = n; named.push(el); },
    unname() { named.forEach((el) => { el.style.viewTransitionName = ''; }); named = []; },

    /* ── FLIP ──
       M.flip('.row[data-id]', mutate, { scope: '.list-b', key, move, enter(el, i), exit(ghost, rect) })
       按 key(默认 data-id / data-flip / id)对上重画前后的同一件东西:位置变了的从旧位置滑到新位置;
       新出现的交给 enter;消失的做成幽灵交给 exit(要自己在动画结束时 ghost.remove())。 */
    flip(sel, mutate, opt = {}) {
      const keyOf = opt.key || ((el) => el.dataset.id || el.dataset.flip || el.id);
      const scope = () => (opt.scope ? $(opt.scope) : document) || document;
      const first = new Map();
      $$(sel, scope()).forEach((el) => { const k = keyOf(el); if (k) first.set(k, { el, r: el.getBoundingClientRect(), spec: opt.exit ? this.ghostSpec(el) : null }); });
      mutate();
      const seen = new Set(); const moved = []; const entered = [];
      $$(sel, scope()).forEach((el) => {
        const k = keyOf(el); if (!k) return;
        seen.add(k);
        const f = first.get(k); const l = el.getBoundingClientRect();
        if (!f) { entered.push(el); return; }
        const dx = f.r.left - l.left; const dy = f.r.top - l.top;
        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) moved.push([el, dx, dy]);
      });
      moved.forEach(([el, dx, dy], i) => this.animate(el, [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], typeof opt.move === 'function' ? opt.move(el, i, dx, dy) : opt.move || { duration: 300, easing: 'cubic-bezier(.2, 0, 0, 1)' }));
      if (opt.enter) entered.forEach((el, i) => opt.enter(el, i));
      if (opt.exit) first.forEach((v, k) => { if (!seen.has(k)) opt.exit(this.ghost(v.spec), v.r); });
      return { moved: moved.map((m) => m[0]), entered };
    },

    /* ── 指示器 ──
       M.glide('rail', host, target, { timing, keyframes }):在 host 里放一块绝对定位的 .m-ind 对齐到 target;
       记得上一次的位置(重画之后也记得),从那里滑过来。host 要 position: relative,方向的样式负责把原来的选中底色让给 .m-ind。 */
    glide(key, host, target, opt = {}) {
      if (!host || !target) { delete lastInd[key]; return null; }
      let ind = host.querySelector(`:scope > .m-ind[data-ind="${key}"]`); let prev = lastInd[key];
      const h = host.getBoundingClientRect();
      const rel = (b) => ({ x: b.left - h.left - host.clientLeft + host.scrollLeft, y: b.top - h.top - host.clientTop + host.scrollTop, w: b.width, h: b.height });
      if (ind) { const b = ind.getBoundingClientRect(); if (b.width) prev = rel(b); } else {
        ind = document.createElement('i'); ind.className = 'm-ind'; ind.dataset.ind = key; ind.setAttribute('aria-hidden', 'true'); host.prepend(ind);
      }
      const r = rel(target.getBoundingClientRect());
      Object.assign(ind.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
      lastInd[key] = r;
      const diff = prev && (Math.abs(prev.x - r.x) > 0.5 || Math.abs(prev.y - r.y) > 0.5 || Math.abs(prev.w - r.w) > 0.5 || Math.abs(prev.h - r.h) > 0.5);
      ind.getAnimations().forEach((a) => a.cancel());
      if (diff && !this.reduced() && opt.animate !== false) {
        const kf = opt.keyframes ? opt.keyframes(prev, r) : [{ transform: `translate(${prev.x - r.x}px, ${prev.y - r.y}px) scale(${prev.w / r.w}, ${prev.h / r.h})` }, { transform: 'none' }];
        this.animate(ind, kf, { duration: 260, easing: 'cubic-bezier(.2, 0, 0, 1)', ...(opt.timing || {}) });
      }
      return ind;
    },
    forgetGlide(key) { delete lastInd[key]; },

    /* ── 幽灵(离场用)──
       重画会把旧元素直接拿掉。M.ghost(el) 在原位留一个定位好的副本(连同祖先的类名链,上下文选择器照样命中),
       返回外框;对外框做离场动画,结束时 remove()。M.exit(el, keyframes, opt) 是这三步的合写,要在 mutate() 之前调。 */
    ghostSpec(el) {
      const chain = []; let p = el.parentElement;
      while (p && p.id !== 'app' && p !== document.body && p !== root) { chain.unshift([typeof p.className === 'string' ? p.className : '', p.id || '']); p = p.parentElement; }
      return { el, r: el.getBoundingClientRect(), chain };
    },
    ghost(elOrSpec) {
      const spec = elOrSpec instanceof Element ? this.ghostSpec(elOrSpec) : elOrSpec;
      let layer = $('#m-ghosts'); if (!layer) { layer = document.createElement('div'); layer.id = 'm-ghosts'; document.body.appendChild(layer); }
      const node = spec.el.isConnected ? spec.el.cloneNode(true) : spec.el;
      node.style.cssText += ';position:static!important;inset:auto!important;margin:0!important;transform:none!important;animation:none!important;transition:none!important;box-sizing:border-box!important;width:100%!important;height:100%!important;';
      const box = document.createElement('div'); box.className = 'm-ghost';
      box.style.cssText = `position:fixed;left:${spec.r.left}px;top:${spec.r.top}px;width:${spec.r.width}px;height:${spec.r.height}px;`;
      let inner = box;
      spec.chain.forEach(([cls, id]) => { const w = document.createElement('div'); if (cls) w.className = cls; if (id) w.id = id; w.style.cssText = 'display:contents!important'; inner.appendChild(w); inner = w; });
      inner.appendChild(node); layer.appendChild(box);
      return box;
    },
    exit(el, keyframes, opt = {}) {
      if (!el || !el.isConnected || this.reduced()) return null;
      const g = this.ghost(el); const a = this.animate(g, keyframes, { fill: 'forwards', ...opt });
      if (a) a.finished.then(() => g.remove(), () => g.remove()); else g.remove();
      return g;
    },
    clearGhosts() { const l = $('#m-ghosts'); if (l) l.innerHTML = ''; },

    /* ── 分行 ──
       M.splitLines(el):按浏览器实际折出来的行,把 el 的内容改写成
         <span class="m-line"><span class="m-line-in">这一行</span></span> × N
       返回各行的 .m-line-in。折行点量的是当时的版面,之后容器变宽不会重排——只在入场那一下用,结束后 M.unsplit(el) 还原。 */
    splitLines(el) {
      if (!el) return [];
      if (el._mOrig != null) return $$('.m-line-in', el);
      const leaves = [];
      const walk = (n) => {
        if (n.nodeType === 3) { for (let i = 0; i < n.length; i++) { const rg = document.createRange(); rg.setStart(n, i); rg.setEnd(n, i + 1); const b = rg.getClientRects()[0]; leaves.push({ ch: n.data[i], top: b ? b.top : null, h: b ? b.height : 0 }); } return; }
        if (n.nodeType !== 1) return;
        if (n.matches('wbr, br, .as') || !n.firstChild) { leaves.push({ node: n, top: null }); return; }
        if (n.matches('.pz, .pzh')) { const b = n.getBoundingClientRect(); leaves.push({ node: n, top: b.height ? b.top : null, h: b.height }); return; }
        [...n.childNodes].forEach(walk);
      };
      [...el.childNodes].forEach(walk);
      const lines = []; let cur = null; let top = null;
      leaves.forEach((lf) => {
        if (lf.top != null && (top == null || Math.abs(lf.top - top) > Math.max(6, lf.h * 0.5))) { cur = []; lines.push(cur); top = lf.top; }
        if (!cur) { cur = []; lines.push(cur); }
        cur.push(lf);
      });
      el._mOrig = el.innerHTML;
      const frag = document.createDocumentFragment();
      lines.forEach((ln) => {
        const outer = document.createElement('span'); outer.className = 'm-line';
        const inner = document.createElement('span'); inner.className = 'm-line-in';
        let buf = '';
        const flush = () => { if (buf) { inner.appendChild(document.createTextNode(buf)); buf = ''; } };
        ln.forEach((lf) => { if (lf.node) { flush(); if (!lf.node.matches('wbr, br')) inner.appendChild(lf.node.cloneNode(true)); } else buf += lf.ch; });
        flush(); outer.appendChild(inner); frag.appendChild(outer);
      });
      el.innerHTML = ''; el.appendChild(frag);
      return $$('.m-line-in', el);
    },
    unsplit(el) { if (el && el._mOrig != null) { el.innerHTML = el._mOrig; el._mOrig = null; } },
  });

  document.addEventListener('pointermove', (e) => { M.pointer.x = e.clientX; M.pointer.y = e.clientY; }, true);

  /* 嵌在总览页的 iframe 里时,原生 scrollIntoView / focus 会把外层页面一起滚到这一格上(对照页因此上下跳)。
     这里兜底:scrollIntoView 改成只滚自己最近的滚动容器,focus 一律不带滚动 */
  if (window.self !== window.top) {
    Element.prototype.scrollIntoView = function scrollIntoViewLocal(arg) {
      const o = arg && typeof arg === 'object' ? arg : { block: arg === false ? 'end' : 'start' };
      let sc = this.parentElement;
      while (sc && !(/(auto|scroll)/.test(getComputedStyle(sc).overflowY) && sc.scrollHeight > sc.clientHeight)) sc = sc.parentElement;
      if (!sc) return;
      const a = this.getBoundingClientRect(); const b = sc.getBoundingClientRect();
      const d = o.block === 'nearest' ? (a.top < b.top ? a.top - b.top : a.bottom > b.bottom ? a.bottom - b.bottom : 0) : o.block === 'end' ? a.bottom - b.bottom : o.block === 'center' ? a.top + a.height / 2 - (b.top + b.height / 2) : a.top - b.top;
      if (d) sc.scrollTo({ top: sc.scrollTop + d, behavior: o.behavior === 'smooth' ? 'smooth' : 'auto' });
    };
    const nativeFocus = HTMLElement.prototype.focus;
    HTMLElement.prototype.focus = function focusNoScroll(o) { return nativeFocus.call(this, { ...(o || {}), preventScroll: true }); };
  }

  /* ───────────── 演示:假光标按脚本操作 ─────────────
     步骤:['wait', ms] ['hover', sel] ['click', sel] ['scroll', sel, top, ms] ['key', 'j'] ['boot'] ['unhover']
     每一轮开头先无动效地回到场景的初始状态(早报到场那一场用 boot 重新进场)。 */
  const R = (n) => `.list-b article:nth-of-type(${n})`;
  M.scenes = {
    press: { title: '悬停与按下', view: 'article', crop: [296, 0, 420, 420], steps: [['hover', R(1)], ['wait', 450], ['hover', R(3)], ['wait', 450], ['click', R(3)], ['wait', 900], ['hover', '.list-tools [data-act="favonly"]'], ['wait', 900], ['hover', '.list-tools [data-act="markall"]'], ['wait', 800], ['hover', '.list-tools [data-act="search"]'], ['wait', 800], ['click', '.list-tools [data-act="search"]'], ['wait', 1000], ['click', '.list-find [data-act="search"]'], ['wait', 700]] },
    ind: { title: '指示器与筛选', view: 'article', crop: [296, 0, 420, 420], steps: [['click', '[data-unread="1"]'], ['wait', 1200], ['click', '[data-unread="0"]'], ['wait', 1100], ['click', '.kinds button:nth-child(2)'], ['wait', 1100], ['click', '.kinds button:nth-child(4)'], ['wait', 1100], ['click', '.kinds button:nth-child(1)'], ['wait', 1200]] },
    pop: { title: '浮层开合', view: 'brief', crop: [860, 0, 580, 420], steps: [['click', '[data-act="cal"]'], ['wait', 1500], ['click', '.bf-id h1'], ['wait', 1100], ['click', '[data-act="cal"]'], ['wait', 1300], ['click', '.cal-g button.today'], ['wait', 1200]] },
    open: { title: '打开一篇文章', view: 'article', crop: [296, 0, 1144, 700], steps: [['click', R(3)], ['wait', 1900], ['click', R(5)], ['wait', 1900], ['click', R(2)], ['wait', 1900]] },
    nav: { title: '切换视图', view: 'brief', crop: [0, 0, 1440, 810], steps: [['click', '[data-nav="article"]'], ['wait', 1700], ['click', '[data-nav="rank"]'], ['wait', 1700], ['click', '[data-nav="social"]'], ['wait', 1700], ['click', '.rail-nav [data-nav="brief"]'], ['wait', 1700]] },
    fold: { title: '来源栏收起与展开', view: 'article', crop: [0, 0, 1440, 810], steps: [['click', '.srcbar [data-act="fold"]'], ['wait', 1500], ['click', '.scope'], ['wait', 1400], ['click', '.pop-f [data-act="fold"]'], ['wait', 1600]] },
    qa: { title: '问答面板', view: 'article', crop: [740, 0, 700, 810], steps: [['click', '.rail [data-act="qa"]'], ['wait', 1500], ['click', '.qa-sug button:first-child'], ['wait', 3400], ['click', '.qa-h [data-act="qa"]'], ['wait', 1200]] },
    brief: { title: '早报到场', view: 'brief', crop: [0, 0, 1440, 810], steps: [['boot'], ['wait', 4200]] },
    ai: { title: 'AI 速读卡', view: 'article.fold', crop: [436, 0, 1004, 700], steps: [['click', R(4)], ['wait', 3000], ['click', R(2)], ['wait', 3000]] },
    toast: { title: '收藏、标读与提示', view: 'article', crop: [296, 0, 1144, 810], steps: [['click', '.pane-bar [data-fav]'], ['wait', 1700], ['click', '.list-tools [data-act="markall"]'], ['wait', 1900], ['click', '.toast [data-act="undo"]'], ['wait', 1500], ['click', '.pane-bar [data-fav]'], ['wait', 1500]] },
    theme: { title: '明暗切换', view: 'brief', crop: [0, 0, 1440, 810], steps: [['click', '[data-act="theme"]'], ['wait', 2200], ['click', '[data-act="theme"]'], ['wait', 2200]] },
    scroll: { title: '阅读时滚动', view: 'article.fold', crop: [436, 0, 1004, 700], steps: [['hover', '.doc h1'], ['scroll', '#pane', 1100, 2600], ['wait', 900], ['scroll', '#pane', 0, 1500], ['wait', 1100]] },
    event: { title: '榜单进事件页', view: 'rank', crop: [64, 0, 1376, 810], steps: [['click', '.rk-list article:nth-of-type(1)'], ['wait', 2000], ['click', '[data-act="evback"]'], ['wait', 1700]] },
    sub: { title: '订阅一个来源', view: 'discover', crop: [64, 0, 1376, 560], steps: [['click', '.dg:first-of-type .sc-card:nth-of-type(2) [data-sub]'], ['wait', 2000], ['click', '.toast [data-act="undo"]'], ['wait', 1600]] },
  };
  M.sceneOrder = ['press', 'ind', 'pop', 'open', 'nav', 'fold', 'qa', 'brief', 'ai', 'toast', 'theme', 'scroll', 'event', 'sub'];

  let cursorEl = null; let hovered = [];
  const cursor = () => {
    if (cursorEl) return cursorEl;
    cursorEl = document.createElement('div'); cursorEl.id = 'm-cursor';
    cursorEl.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M5 3l14 8-6.2 1.6L9.6 19z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg><i></i>';
    document.body.appendChild(cursorEl);
    cursorEl._x = innerWidth * 0.5; cursorEl._y = innerHeight * 0.55;
    return cursorEl;
  };
  const chain = (el) => { const out = []; for (let p = el; p && p !== document.body; p = p.parentElement) out.push(p); return out; };
  const setHover = (el) => { hovered.forEach((e) => e.classList.remove('m-hover')); hovered = el ? chain(el) : []; hovered.forEach((e) => e.classList.add('m-hover')); };
  const fire = (el, type, x, y, Ctor = PointerEvent) => el && el.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, detail: 1, pointerType: 'mouse', view: window }));
  const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function moveTo(el, ms = 420) {
    const c = cursor(); const b = el.getBoundingClientRect();
    const tx = b.left + Math.min(b.width - 6, Math.max(6, b.width * 0.5)); const ty = b.top + Math.min(b.height - 4, Math.max(4, b.height * 0.55));
    const sx = c._x; const sy = c._y; const t0 = performance.now();
    await new Promise((res) => {
      const f = (now) => {
        const k = Math.min(1, (now - t0) / ms); const e = ease(k);
        c._x = sx + (tx - sx) * e; c._y = sy + (ty - sy) * e;
        c.style.transform = `translate(${c._x}px, ${c._y}px)`;
        const under = document.elementFromPoint(c._x, c._y);
        if (under) { fire(under, 'pointermove', c._x, c._y); fire(under, 'mousemove', c._x, c._y, MouseEvent); }
        if (k < 1) requestAnimationFrame(f); else res();
      };
      requestAnimationFrame(f);
    });
    setHover(el); fire(el, 'pointerover', c._x, c._y); fire(el, 'mouseover', c._x, c._y, MouseEvent);
    return [c._x, c._y];
  }
  const find = (sel) => (typeof sel === 'function' ? sel() : $(sel));
  M.demo = {
    seq: 0,
    stop() { this.seq++; setHover(null); root.classList.remove('m-demo'); if (cursorEl) cursorEl.style.opacity = 0; },
    async play(name, { loop = true } = {}) {
      const sc = M.scenes[name]; if (!sc) return;
      const my = ++this.seq; const live = () => my === this.seq;
      root.classList.add('m-demo');
      const theme = (location.hash.includes('dark') ? 'dark' : 'light');
      const hash = `${theme}.${sc.view}${root.classList.contains('bare') ? '.bare' : ''}`;
      const c = cursor(); c.style.opacity = 1; c.style.transform = `translate(${c._x}px, ${c._y}px)`;
      do {
        if (name !== 'brief') { window.__proto.initState(hash); window.__proto.render(); await sleep(700); }
        for (const st of sc.steps) {
          if (!live()) return;
          const [op, a, b, d] = st;
          if (op === 'wait') await sleep(a / M.speed);
          else if (op === 'boot') { setHover(null); window.__proto.reboot(hash); }
          else if (op === 'unhover') setHover(null);
          else if (op === 'key') document.body.dispatchEvent(new KeyboardEvent('keydown', { key: a, bubbles: true }));
          else if (op === 'hover' || op === 'click') {
            const el = find(a); if (!el) continue;
            const [x, y] = await moveTo(el);
            if (op === 'click' && live()) {
              await sleep(90);
              const ch = chain(el); ch.forEach((e) => e.classList.add('m-press')); c.classList.add('down'); fire(el, 'pointerdown', x, y); fire(el, 'mousedown', x, y, MouseEvent);
              await sleep(110);
              ch.forEach((e) => e.classList.remove('m-press')); c.classList.remove('down'); fire(el, 'pointerup', x, y); fire(el, 'mouseup', x, y, MouseEvent);
              if (el.isConnected) fire(el, 'click', x, y, MouseEvent);
              /* 重画之后原来的元素没了,悬停态落到新画出来的同位置元素上 */
              await sleep(30); const again = document.elementFromPoint(x, y); setHover(again && again.closest('button, a, article, [tabindex]') || again);
            }
          } else if (op === 'scroll') {
            const el = find(a); if (!el) continue;
            const from = el.scrollTop; const t0 = performance.now(); const ms = d / M.speed;
            await new Promise((res) => { const f = (now) => { const k = Math.min(1, (now - t0) / ms); el.scrollTop = from + (b - from) * ease(k); if (k < 1 && live()) requestAnimationFrame(f); else res(); }; requestAnimationFrame(f); });
          }
        }
      } while (loop && live());
    },
  };
})();
