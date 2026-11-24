/* 哆啦美 · 动效样页 —— 在场与退场(样式与规矩见 presence.css)。与方向无关,四个选项可以和任一方向叠着看。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement;
  const has = (p) => root.classList.contains('p-' + p);
  const svg = (d, s = 16) => `<svg class="ic" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const STAR = '<path d="M11.53 2.3a.53.53 0 0 1 .95 0l2.3 4.68a2.12 2.12 0 0 0 1.6 1.16l5.16.75a.53.53 0 0 1 .3.9l-3.74 3.64a2.12 2.12 0 0 0-.61 1.88l.88 5.14a.53.53 0 0 1-.77.56l-4.62-2.43a2.12 2.12 0 0 0-1.97 0L6.4 21.01a.53.53 0 0 1-.77-.56l.88-5.14a2.12 2.12 0 0 0-.61-1.88L2.16 9.8a.53.53 0 0 1 .3-.91l5.16-.75a2.12 2.12 0 0 0 1.6-1.16z"/>';
  const CHECK = '<path d="M20 6 9 17l-5-5"/>';
  const UNREAD = '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>';
  const S = () => window.__proto.state;

  /* ── ① 沉浸阅读 / ④ 吸顶栏收放:都看阅读窗的滚动方向 ── */
  let lastTop = 0; let down = 0; let up = 0;
  const wake = () => { root.classList.remove('reading'); down = 0; };
  M.on('scroll', (el) => {
    if (!el.classList.contains('pane')) return;
    const t = el.scrollTop; const d = t - lastTop; lastTop = t;
    if (d > 0) { down += d; up = 0; } else if (d < 0) { up -= d; down = 0; }
    if (has('immersive')) {
      if (t > 240 && down > 160) root.classList.add('reading');
      if (t < 120 || up > 24) wake();
    }
    if (has('bar')) {
      if (t > 200 && down > 60) root.classList.add('bar-away');
      if (t < 80 || up > 16) root.classList.remove('bar-away');
    }
  });
  document.addEventListener('pointerover', (e) => {
    if (root.classList.contains('reading') && e.target.closest && e.target.closest('.rail, .srcbar, .list')) wake();
  }, true);
  document.addEventListener('pointermove', (e) => {
    if (!root.classList.contains('bar-away')) return;
    const p = $('#pane'); if (!p) return;
    const b = p.getBoundingClientRect();
    if (e.clientX > b.left && e.clientY < b.top + 56) root.classList.remove('bar-away');
  }, true);
  document.addEventListener('keydown', wake, true);

  /* ── ② 行内动作 ── */
  function rowActs() {
    $$('.row-act, .row-star').forEach((e) => e.parentNode.removeChild(e));
    if (!has('rowact')) return;
    const st = S();
    $$('.row[data-id]').forEach((row) => {
      const id = row.dataset.id; const faved = st.fav.has(id); const read = st.read.has(id);
      const box = document.createElement('span'); box.className = 'row-act';
      box.innerHTML = `<button class="tool ${faved ? 'faved' : ''}" data-fav="${id}" aria-label="${faved ? '取消收藏' : '收藏'}">${svg(STAR)}</button><button class="tool" data-rowread="${id}" aria-label="${read ? '标为未读' : '标为已读'}">${svg(read ? UNREAD : CHECK)}</button>`;
      row.appendChild(box);
      if (faved) { const s = document.createElement('span'); s.className = 'row-star'; s.innerHTML = svg(STAR, 14); row.appendChild(s); }
    });
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-rowread]'); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const st = S(); const id = b.dataset.rowread; const read = st.read.has(id);
    M.run('unread', { id, on: !read, el: b, row: true, sel: `[data-rowread="${CSS.escape(id)}"]` }, () => { if (read) st.read.delete(id); else st.read.add(id); window.__proto.renderStage(true); });
    window.__proto.toast(read ? '已标为未读' : '已标为已读');
  }, true);

  /* ── ③ 来源栏探出 ── */
  let peekIn = 0; let peekOut = 0; let armT = 0;
  const peek = (on) => { clearTimeout(peekIn); clearTimeout(peekOut); root.classList.toggle('peeking', on); };
  function armPeek() {
    clearTimeout(armT);
    const feed = $('.feed');
    const folded = has('peek') && feed && feed.classList.contains('fold');
    if (!folded) { root.classList.remove('peek-armed', 'peeking'); return; }
    if (!$('.peek-edge', feed)) { const e = document.createElement('i'); e.className = 'peek-edge'; e.setAttribute('aria-hidden', 'true'); feed.appendChild(e); }
    /* 等收起的列宽过渡走完再把栏内层换成浮层定位,免得收起的那一下跳 */
    armT = setTimeout(() => root.classList.add('peek-armed'), root.classList.contains('peek-armed') ? 0 : 460);
  }
  document.addEventListener('pointerover', (e) => {
    if (!root.classList.contains('peek-armed')) return;
    const t = e.target; if (!t.closest) return;
    if (t.closest('.peek-edge')) { clearTimeout(peekOut); if (!root.classList.contains('peeking')) { clearTimeout(peekIn); peekIn = setTimeout(() => peek(true), 140); } return; }
    if (t.closest('.srcbar-in')) { clearTimeout(peekOut); return; }
    clearTimeout(peekIn);
    if (root.classList.contains('peeking')) { clearTimeout(peekOut); peekOut = setTimeout(() => peek(false), 320); }
  }, true);
  document.addEventListener('click', (e) => {
    const t = e.target; if (!t.closest) return;
    /* 收起 / 展开只换类名、不重画,而且各方向接手后换类名的时刻不一样,所以隔几拍各看一次 */
    if (t.closest('[data-act="fold"]')) { root.classList.remove('peek-armed', 'peeking'); [30, 320, 720].forEach((ms) => setTimeout(armPeek, ms)); }
  }, true);

  /* 对照页用的演示脚本(和 motion-core.js 里的场景同一套写法) */
  const R = (n) => `.list-b article:nth-of-type(${n})`;
  Object.assign(M.scenes, {
    'p-immersive': { title: '沉浸阅读', view: 'article', crop: [0, 0, 1440, 810], steps: [['hover', '.doc h1'], ['scroll', '#pane', 1000, 2600], ['wait', 1800], ['hover', R(3)], ['wait', 1300], ['hover', '.doc-by'], ['scroll', '#pane', 0, 900], ['wait', 700]] },
    'p-rowact': { title: '行内动作', view: 'article', crop: [296, 0, 420, 560], steps: [['hover', R(2)], ['wait', 800], ['hover', R(3)], ['wait', 800], ['hover', R(4)], ['wait', 500], ['click', `${R(4)} .row-act [data-fav]`], ['wait', 1500], ['hover', R(6)], ['wait', 1100], ['hover', R(4)], ['wait', 500], ['click', `${R(4)} .row-act [data-fav]`], ['wait', 900]] },
    'p-peek': { title: '来源栏探出', view: 'article.fold', crop: [0, 0, 960, 810], steps: [['hover', '.doc h1'], ['wait', 500], ['hover', '.peek-edge'], ['wait', 1100], ['hover', '.srcbar-b .src:nth-of-type(4)'], ['wait', 500], ['click', '.srcbar-b .src:nth-of-type(4)'], ['wait', 1100], ['hover', '.doc h1'], ['wait', 1500]] },
    'p-bar': { title: '吸顶栏收放', view: 'article.fold', crop: [436, 0, 1004, 520], steps: [['hover', '.doc-by'], ['scroll', '#pane', 900, 2200], ['wait', 900], ['scroll', '#pane', 620, 900], ['wait', 1100], ['scroll', '#pane', 0, 900], ['wait', 600]] },
  });
  M.presenceScenes = ['p-immersive', 'p-rowact', 'p-peek', 'p-bar'];

  M.on('rendered', (scope) => {
    if (scope === 'qa' || scope === 'qa-body') return;
    if (scope !== 'pane') { rowActs(); armPeek(); }
    if (scope === 'pane' || scope === 'app') { lastTop = 0; down = 0; up = 0; root.classList.remove('reading', 'bar-away'); }
  });
  M.on('presence', () => { rowActs(); armPeek(); if (!has('immersive')) root.classList.remove('reading'); if (!has('bar')) root.classList.remove('bar-away'); });
})();
