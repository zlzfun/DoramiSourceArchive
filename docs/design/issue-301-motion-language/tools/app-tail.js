  /* ── 调度:每个交互都经 M.run(kind, ctx, mutate)。mutate 只改状态与 DOM;怎么动由当前方向(assets/dir-*.js)决定 ── */
  const M = window.M;
  const root = document.documentElement;
  const ORDER = ['brief', 'article', 'podcast', 'social', 'rank', 'discover'];
  let toastTimer = 0; let toastHtml = ''; let toastUndo = null;
  const renderToast = () => { const o = $('#toast-slot'); if (o) o.innerHTML = toastHtml; };
  function hideToast() {
    clearTimeout(toastTimer); if (!toastHtml) return;
    M.run('toast', { phase: 'out', sel: '.toast' }, () => { toastHtml = ''; toastUndo = null; renderToast(); });
  }
  function toast(msg, undo) {
    clearTimeout(toastTimer);
    M.run('toast', { phase: 'in', replace: !!toastHtml, msg, sel: '.toast' }, () => {
      toastHtml = `<div class="toast ${undo ? '' : 'plain'}" role="status"><span>${esc(msg)}</span>${undo ? '<button data-act="undo">撤销</button>' : ''}</div>`;
      toastUndo = undo || null; renderToast();
    });
    toastTimer = setTimeout(hideToast, Math.max(3200, 1800 / (M.speed || 1)));
  }
  function renderQaSlot(quiet) {
    const o = $('#qa-slot'); if (o) o.innerHTML = state.qa ? renderQa() : '';
    const b = $('.rail [data-act="qa"]'); if (b) b.classList.toggle('on', state.qa);
    if (!quiet) M.emit('rendered', 'qa');
  }
  function renderStage(keepScroll, quiet) {
    const st = $('#stage'); if (!st) return;
    const keep = keepScroll ? $$('[data-scroll]', st).map((el) => el.scrollTop) : null;
    const v = state.view;
    st.innerHTML = v === 'brief' ? renderBrief() : v === 'feed' ? renderFeed() : v === 'social' ? renderSocial() : v === 'rank' ? renderRank() : renderDisc();
    st.dataset.view = v;
    if (keep) $$('[data-scroll]', st).forEach((el, i) => { if (keep[i] != null) el.scrollTop = keep[i]; });
    $('#dock').innerHTML = renderDock();
    const sq = $('[data-sq]'); if (sq && state.search) { sq.focus(); sq.setSelectionRange(sq.value.length, sq.value.length); }
    if (!quiet) M.emit('rendered', 'stage');
  }
  function renderPaneOnly(keepScroll) {
    const p = $('#pane'); if (!p) return;
    const sp = p.scrollTop; p.innerHTML = renderPane(); p.scrollTop = keepScroll ? sp : 0;
    M.emit('rendered', 'pane');
  }
  function render(keepScroll) {
    root.dataset.theme = state.theme; root.dataset.rail = state.rail; root.dataset.weight = state.weight;
    if (state.palette === 'porcelain') delete root.dataset.palette; else root.dataset.palette = state.palette;
    $('#app').innerHTML = shell();
    renderStage(keepScroll, true); renderQaSlot(true); renderToast(); syncLab();
    const h = `#${state.theme}.${navKey()}${state.fold ? '.fold' : ''}${state.palette !== 'porcelain' ? '.' + state.palette : ''}`;
    if (location.hash.split('.bare')[0] !== h) history.replaceState(null, '', location.pathname + location.search + h + (root.classList.contains('bare') ? '.bare' : ''));
    M.emit('rendered', 'app');
  }
  function open(id, from, shape) {
    state.view = 'feed'; state.shape = shape || 'article'; state.scope = 'sub'; state.sel = id; state.from = from || null; state.read.add(id);
    state.srcPop = false; state.search = false; state.sq = ''; state.favOnly = false; state.unread = false; state.preview = false;
    if (from) { state.src = null; state.kind = 'all'; }
  }
  const firstPick = (shape) => (shape === 'podcast' ? feedItems().find((x) => D.details[x.id]) || feedItems()[0] : feedItems()[1] || feedItems()[0]);
  const openPop = () => (state.srcPop ? 'srcpop' : state.cal ? 'cal' : state.menu ? 'menu' : '');
  const closePops = () => { const had = openPop(); state.srcPop = false; state.cal = false; state.menu = false; return had; };

  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const q = (sel) => t.closest(sel);
    /* 键盘触发的点击(回车、j / k 合成)detail 为 0:这是高频路径,方向可以据 ctx.key 不做动效 */
    const key = ev.detail === 0;
    let el;
    if ((el = q('[data-lab]'))) { lab(el); return; }
    if ((el = q('[data-fav]'))) {
      ev.preventDefault(); ev.stopPropagation(); const id = el.dataset.fav; const on = !state.fav.has(id);
      M.run('fav', { id, on, el, key, sel: `[data-fav="${CSS.escape(id)}"]` }, () => { if (on) state.fav.add(id); else state.fav.delete(id); renderStage(true); });
      toast(on ? '已收藏' : '已取消收藏'); return;
    }
    if ((el = q('[data-sub]'))) {
      ev.stopPropagation(); const s = D.sources[el.dataset.sub]; const on = !s.on; const sel = `[data-sub="${CSS.escape(s.id)}"]`;
      const apply = (v) => { s.on = v; const i = D.subscribed.indexOf(s.id); if (v && i < 0) D.subscribed.push(s.id); if (!v && i >= 0) D.subscribed.splice(i, 1); };
      M.run('sub', { id: s.id, on, el, key, sel }, () => { apply(on); if (on) state.preview = false; renderStage(true); });
      toast(on ? `已订阅「${s.name}」` : `已取消订阅「${s.name}」`, () => M.run('sub', { id: s.id, on: !on, undo: true, sel }, () => { apply(!on); renderStage(true); }));
      return;
    }
    if ((el = q('[data-act]'))) {
      ev.preventDefault(); const a = el.dataset.act;
      if (a === 'noop') { toast(el.dataset.msg || '样页未接'); return; }
      if (a === 'undo') { const u = toastUndo; hideToast(); if (u) u(); return; }
      if (a === 'theme') { const to = state.theme === 'dark' ? 'light' : 'dark'; M.run('theme', { to, el, key }, () => { state.theme = to; render(true); }); return; }
      if (a === 'srcpop') { if (!state.fold) return; const o = !state.srcPop; M.run('pop', { name: 'srcpop', open: o, el, key, sel: '.pop.srcpop' }, () => { closePops(); state.srcPop = o; renderStage(true); }); return; }
      if (a === 'cal') { ev.stopPropagation(); const o = !state.cal; M.run('pop', { name: 'cal', open: o, el, key, sel: '.pop.cal' }, () => { closePops(); state.cal = o; renderStage(true); }); return; }
      if (a === 'calday') {
        if (!el.classList.contains('today')) { toast('样页只带了今天这一期'); return; }
        M.run('pop', { name: 'cal', open: false, key, sel: '.pop.cal' }, () => { state.cal = false; renderStage(true); }); return;
      }
      if (a === 'menu') { const o = !state.menu; M.run('pop', { name: 'menu', open: o, el, key, sel: '.pop.menu' }, () => { closePops(); state.menu = o; renderPaneOnly(true); }); return; }
      if (a === 'fold') {
        const fold = !state.fold;
        M.run('fold', { fold, el, key }, () => {
          const hadPop = state.srcPop; state.fold = fold; state.srcPop = false;
          const f = $('.feed');
          /* 没有浮层开着时只换类名,列宽过渡才接得上;有浮层就整段重画 */
          if (f && !hadPop) { f.classList.toggle('fold', fold); history.replaceState(null, '', location.pathname + location.search + location.hash.replace('.fold', '') + (fold ? '.fold' : '')); } else renderStage(true);
        });
        return;
      }
      if (a === 'search') { M.run('search', { open: !state.search, el, key }, () => { state.search = !state.search; state.sq = ''; renderStage(true); }); return; }
      if (a === 'favonly') { M.run('filter', { group: 'favonly', value: !state.favOnly, el, key }, () => { state.favOnly = !state.favOnly; renderStage(true); }); return; }
      if (a === 'markall') {
        const before = new Set(state.read);
        M.run('markall', { el, key }, () => { feedItems().forEach((x) => state.read.add(x.id)); renderStage(true); });
        toast('已全部标为已读', () => M.run('markall', { undo: true }, () => { state.read = before; renderStage(true); })); return;
      }
      if (a === 'unreadone') { M.run('unread', { id: state.sel, el, key }, () => { state.read.delete(state.sel); renderStage(true); }); toast('已标为未读'); return; }
      if (a === 'back') { const to = state.from === 'brief' ? 'brief' : 'rank'; M.run('back', { to, id: state.sel, el, key }, () => { state.view = to; state.from = null; render(); }); return; }
      if (a === 'evback') { M.run('event', { open: false, id: state.event, el, key }, () => { state.event = null; render(); }); return; }
      if (a === 'qa') { const o = !state.qa; M.run('qa', { open: o, el: $('.rail [data-act="qa"]') || el, key, sel: '.qa' }, () => { state.qa = o; renderQaSlot(); }); return; }
      if (a === 'qaask') { M.run('qaask', { el, key }, () => { state.qaAsked = true; const b = $('.qa-b'); if (b) b.innerHTML = qaBody(); M.emit('rendered', 'qa-body'); }); return; }
      if (a === 'play') {
        const start = state.playing !== state.sel;
        M.run('dock', { phase: start ? 'in' : 'toggle', el, key }, () => { if (start) { state.playing = state.sel; state.paused = false; } else state.paused = !state.paused; renderStage(true); }); return;
      }
      if (a === 'pause') { M.run('dock', { phase: 'toggle', el, key }, () => { state.paused = !state.paused; renderStage(true); }); return; }
      if (a === 'stop') { M.run('dock', { phase: 'out', el, key }, () => { state.playing = null; renderStage(true); }); return; }
      if (a === 'why') { const o = !state.why; M.run('why', { open: o, el, key, sel: '.ai-why' }, () => { state.why = o; renderPaneOnly(true); }); return; }
      if (a === 'unclip') { const p = el.closest('.post'); M.run('unclip', { el, post: p }, () => { $('.post-t', p).classList.remove('clip'); el.remove(); }); return; }
      return;
    }
    if ((el = q('[data-nav]'))) {
      ev.preventDefault(); const n = el.dataset.nav; const from = state.event ? 'event' : navKey();
      M.run('nav', { from, to: n, dy: ORDER.indexOf(n) - ORDER.indexOf(navKey()), el, key }, () => {
        closePops(); state.event = null; state.from = null; state.search = false; state.sq = ''; state.preview = false;
        if (n === 'article' || n === 'podcast') {
          if (state.shape !== n) { state.sel = null; state.src = null; state.kind = 'all'; state.scope = 'sub'; }
          state.view = 'feed'; state.shape = n;
          if (!state.sel) { const f = firstPick(n); if (f) { state.sel = f.id; state.read.add(f.id); } }
        } else state.view = n;
        render();
      });
      return;
    }
    const filter = (group, value, mutate) => M.run('filter', { group, value, el, key }, mutate);
    if ((el = q('[data-scope]'))) { const v = el.dataset.scope; filter('scope', v, () => { if (state.scope === v) { state.src = null; state.preview = false; } state.scope = v; state.srcPop = false; renderStage(); }); return; }
    if ((el = q('[data-src]'))) { const v = el.dataset.src || null; filter('src', v, () => { state.src = v; state.srcPop = false; state.preview = false; renderStage(); }); return; }
    if ((el = q('[data-kind]'))) { const v = el.dataset.kind; filter('kind', v, () => { state.kind = v; renderStage(); }); return; }
    if ((el = q('[data-unread]'))) { const v = el.dataset.unread === '1'; filter('unread', v, () => { state.unread = v; renderStage(true); }); return; }
    if ((el = q('[data-podtab]'))) { const v = el.dataset.podtab; filter('podtab', v, () => { state.podTab = v; renderPaneOnly(true); }); return; }
    if ((el = q('[data-ranktab]'))) { const v = el.dataset.ranktab; filter('ranktab', v, () => { state.rankTab = v; renderStage(); }); return; }
    if ((el = q('[data-rankwin]'))) { const v = el.dataset.rankwin; filter('rankwin', v, () => { state.rankWin = v; renderStage(); }); return; }
    if ((el = q('[data-disctab]'))) { const v = el.dataset.disctab; filter('disctab', v, () => { state.discTab = v; renderStage(); }); return; }
    if ((el = q('[data-discshape]'))) { const v = el.dataset.discshape; filter('discshape', v, () => { state.discShape = v; renderStage(); }); return; }
    if ((el = q('[data-toc]'))) { ev.preventDefault(); const sec = $(`[data-sec="${el.dataset.toc}"]`); if (sec) sec.scrollIntoView({ behavior: M.reduced() ? 'auto' : 'smooth', block: 'start' }); return; }
    if ((el = q('[data-event]'))) {
      ev.preventDefault(); const id = Number(el.dataset.event);
      M.run('event', { open: true, id, el, key }, () => { state.view = 'rank'; state.rankTab = 'events'; state.event = id; render(); }); return;
    }
    if ((el = q('[data-preview]'))) {
      const id = el.dataset.preview; const s = D.sources[id];
      const has = (s.shape === 'podcast' ? D.pods : D.articles).some((x) => x.src === id);
      if (s.shape === 'social' || !has) { toast('样页快照里没有带这个来源的内容'); return; }
      M.run('open', { id: null, from: 'discover', el, key }, () => {
        state.view = 'feed'; state.shape = s.shape; state.scope = 'sub'; state.src = id; state.kind = 'all'; state.preview = !s.on;
        const f = feedItems()[0]; state.sel = f ? f.id : null; if (f) state.read.add(f.id);
        render();
      });
      return;
    }
    if ((el = q('[data-open]'))) {
      const id = el.dataset.open; const from = el.dataset.from; const shape = el.dataset.shape;
      M.run('open', { id, from, el, key, inQa: !!q('.qa') }, () => { open(id, from, shape); state.qa = false; render(); }); return;
    }
    if ((el = q('[data-go]')) || (el = q('.row[data-id]'))) {
      const id = el.dataset.go || el.dataset.id; if (id === state.sel) return;
      M.run('open', { id, prev: state.sel, from: el.dataset.go ? 'go' : 'list', el, key, sel: `.row[data-id="${CSS.escape(id)}"]` }, () => {
        state.sel = id; state.read.add(id); state.from = null; state.menu = false;
        $$('.row.on').forEach((r) => r.classList.remove('on'));
        const r = $(`.row[data-id="${CSS.escape(id)}"]`); if (r) { r.classList.add('on', 'read'); r.scrollIntoView({ block: 'nearest' }); }
        renderPaneOnly(false);
      });
      return;
    }
    if (!q('[data-keep]')) { const name = openPop(); if (name) M.run('pop', { name, open: false, sel: `.pop.${name}` }, () => { closePops(); if (name === 'menu') renderPaneOnly(true); else renderStage(true); }); }
  });
  document.addEventListener('input', (ev) => {
    const t = ev.target;
    if (t.matches('[data-q]')) { state.q = t.value; const pos = t.selectionStart; renderStage(true); const i = $('[data-q]'); i.focus(); i.setSelectionRange(pos, pos); }
    if (t.matches('[data-sq]')) { state.sq = t.value; renderStage(true); }
  });
  document.addEventListener('keydown', (ev) => {
    const typing = ev.target.matches('input, textarea');
    if (ev.key === 'Escape') {
      if (state.qa) { M.run('qa', { open: false, el: $('.rail [data-act="qa"]'), key: true, sel: '.qa' }, () => { state.qa = false; renderQaSlot(); }); return; }
      if (state.search) { M.run('search', { open: false, key: true }, () => { state.search = false; state.sq = ''; renderStage(true); }); return; }
      const name = openPop(); if (name) M.run('pop', { name, open: false, key: true, sel: `.pop.${name}` }, () => { closePops(); if (name === 'menu') renderPaneOnly(true); else renderStage(true); });
      return;
    }
    if (ev.key === 'Enter' && ev.target.matches('[data-qain]') && !ev.shiftKey) { ev.preventDefault(); M.run('qaask', { key: true }, () => { state.qaAsked = true; const b = $('.qa-b'); if (b) b.innerHTML = qaBody(); M.emit('rendered', 'qa-body'); }); return; }
    if (ev.key === 'Enter' && ev.target.matches('[tabindex="0"]')) ev.target.click();
    if (state.view === 'feed' && (ev.key === 'j' || ev.key === 'k') && !typing) {
      const rows = $$('.row[data-id]'); const i = rows.findIndex((r) => r.dataset.id === state.sel);
      const n = rows[Math.max(0, Math.min(rows.length - 1, i + (ev.key === 'j' ? 1 : -1)))];
      if (n) n.click();
    }
  });
  /* 滚动:阅读窗进度线;早报目录跟读 */
  document.addEventListener('scroll', (ev) => {
    const el = ev.target; if (!el.classList) return;
    if (el.classList.contains('pane')) { const bar = $('.pane-bar', el); if (bar) bar.style.setProperty('--prog', Math.min(1, el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight)).toFixed(3)); }
    if (el.classList.contains('brief')) {
      const top = el.getBoundingClientRect().top; let cur = '';
      $$('.sec', el).forEach((s) => { if (s.getBoundingClientRect().top - top < 120) cur = s.dataset.sec; });
      $$('.toc a', el).forEach((a) => a.classList.toggle('on', a.dataset.toc === cur));
    }
    M.emit('scroll', el);
  }, true);

  /* ── 样页开关(不属于产品界面)── */
  function lab(el) {
    const [k, v] = el.dataset.lab.split(':');
    if (k === 'dir') { M.setDir(v); try { localStorage.setItem('motion-dir', v); } catch { /* 存不了就算了 */ } render(true); return; }
    if (k === 'speed') { M.setSpeed(Number(v)); syncLab(); return; }
    if (k === 'motion') { if (v === 'reduce') root.dataset.motion = 'reduce'; else delete root.dataset.motion; syncLab(); return; }
    if (k === 'p') { root.classList.toggle('p-' + v); M.emit('presence', v); syncLab(); return; }
    if (k === 'replay') { reboot(); return; }
    state[k] = v === 'true' ? true : v === 'false' ? false : v; render(true);
  }
  function syncLab() {
    $$('#lab [data-lab]').forEach((b) => {
      const [k, v] = b.dataset.lab.split(':');
      const on = k === 'dir' ? M.dir === v : k === 'speed' ? String(M.speed) === v : k === 'motion' ? (root.dataset.motion || 'full') === v : k === 'p' ? root.classList.contains('p-' + v) : String(state[k]) === v;
      b.classList.toggle('on', on);
    });
  }
  function initState(hash) {
    Object.assign(state, fresh());
    const h = String(hash || '').replace('#', '').split('.').filter(Boolean);
    const has = (k) => h.includes(k);
    root.classList.toggle('bare', has('bare'));
    if (has('dark')) state.theme = 'dark';
    if (has('graphite')) state.palette = 'graphite';
    if (has('paper')) state.palette = 'paper';
    if (has('fold')) state.fold = true;
    if (has('even')) state.weight = 'off';
    const v = ['brief', 'article', 'podcast', 'social', 'rank', 'discover'].find(has) || 'brief';
    if (v === 'article' || v === 'podcast') { state.view = 'feed'; state.shape = v; const f = firstPick(v); if (f) { state.sel = f.id; state.read.add(f.id); } } else state.view = v;
    if (has('event')) { state.view = 'rank'; state.event = D.events.week[0].id; }
    if (has('deep')) state.rankTab = 'deep';
    if (has('pods')) state.rankTab = 'pods';
    if (has('qa')) { state.qa = true; state.qaAsked = has('asked'); }
    if (has('playing') && state.shape === 'podcast') state.playing = state.sel;
    if (has('cal')) state.cal = true;
    if (has('search')) state.search = true;
  }
  /* 到场:第一次画出界面。方向可以在这里编排「今天第一次打开」的那一下 */
  function reboot(hash) {
    if (hash != null) initState(hash);
    clearTimeout(toastTimer); toastHtml = ''; toastUndo = null;
    $('#app').innerHTML = '';
    M.run('boot', { view: state.view, first: true }, () => render());
  }
  function boot() {
    const qs = new URLSearchParams(location.search);
    let saved = null; try { saved = localStorage.getItem('motion-dir'); } catch { /* 读不到就用默认 */ }
    if (qs.get('speed')) M.setSpeed(Number(qs.get('speed')));
    if (qs.get('motion') === 'reduce') root.dataset.motion = 'reduce';
    (qs.get('p') || '').split(',').filter(Boolean).forEach((p) => root.classList.add('p-' + p));
    M.setDir(M.dirs[qs.get('dir')] ? qs.get('dir') : M.dirs[saved] ? saved : 'a');
    reboot(location.hash);
    if (qs.get('demo')) M.demo.play(qs.get('demo'), { loop: qs.get('loop') !== '0' });
  }
  window.__proto = { state, render, renderStage, toast, reboot, initState };
  boot();
})();
