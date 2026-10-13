/* 样页渲染器:把 window.DM(真实样本数据)渲染成各个样页。
   只做静态呈现 + 少量本地切换,不模拟任何产品尚未实现的行为。 */
(function () {
  const P = {
    rss: '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r=".6"/>',
    star: '<path d="M12 2.8l2.85 5.78 6.38.93-4.62 4.5 1.09 6.35L12 17.36l-5.7 3 1.09-6.35-4.62-4.5 6.38-.93z"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>',
    checks: '<path d="M18 6 7 17l-5-5"/><path d="m22 10-7.5 7.5L13 16"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4"/><path d="m15.4 6.5-6.8 4"/>',
    ext: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    paper: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8z"/>',
    podcast: '<path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/>',
    at: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
    compass: '<circle cx="12" cy="12" r="10"/><path d="m16.2 7.8-2.1 6.3-6.3 2.1 2.1-6.3z"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
    msg: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    gear: '<path d="M12.2 2h-.4a2 2 0 0 0-2 2v.2a2 2 0 0 1-1 1.7l-.4.3a2 2 0 0 1-2 0l-.2-.1a2 2 0 0 0-2.7.7l-.2.4a2 2 0 0 0 .7 2.7l.2.1a2 2 0 0 1 1 1.7v.5a2 2 0 0 1-1 1.8l-.2.1a2 2 0 0 0-.7 2.7l.2.4a2 2 0 0 0 2.7.7l.2-.1a2 2 0 0 1 2 0l.4.3a2 2 0 0 1 1 1.7v.2a2 2 0 0 0 2 2h.4a2 2 0 0 0 2-2v-.2a2 2 0 0 1 1-1.7l.4-.3a2 2 0 0 1 2 0l.2.1a2 2 0 0 0 2.7-.7l.2-.4a2 2 0 0 0-.7-2.7l-.2-.1a2 2 0 0 1-1-1.8v-.5a2 2 0 0 1 1-1.7l.2-.1a2 2 0 0 0 .7-2.7l-.2-.4a2 2 0 0 0-2.7-.7l-.2.1a2 2 0 0 1-2 0l-.4-.3a2 2 0 0 1-1-1.7V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    trend: '<path d="M22 7 13.5 15.5l-5-5L2 17"/><path d="M16 7h6v6"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    chevR: '<path d="m9 18 6-6-6-6"/>',
    chevD: '<path d="m6 9 6 6 6-6"/>',
    arrowUR: '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    db: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    cloud: '<path d="M12 13v8l-4-4"/><path d="m12 21 4-4"/><path d="M4.4 15.3A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.4 8.3"/>',
    history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
    chart: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    calendar: '<rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M16 3v3M8 3v3M3 9.5h18"/>',
    arrowR: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    arrowD: '<path d="M12 5v14"/><path d="m6 13 6 6 6-6"/>',
    monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  };
  const ic = (n, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[n] || ''}</svg>`;
  const AI = `<svg class="ai-mark" viewBox="0 0 24 24" aria-hidden="true"><path fill="url(#dm-ai-g)" d="M12 2c.6 5.4 2.6 7.4 10 10-7.4 2.6-9.4 4.6-10 10-.6-5.4-2.6-7.4-10-10 7.4-2.6 9.4-4.6 10-10Z"/></svg>`;
  function ensureDefs() {
    if (document.getElementById('dm-ai-defs')) return;
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.id = 'dm-ai-defs'; s.setAttribute('width', '0'); s.setAttribute('height', '0'); s.style.position = 'absolute';
    s.innerHTML = '<defs><linearGradient id="dm-ai-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3f7bf6"/><stop offset="1" stop-color="#8b52f4"/></linearGradient></defs>';
    document.body.prepend(s);
  }
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const tier = (v) => (v == null ? '' : v < 6 ? 't0' : 't' + Math.min(4, Math.floor(v - 6) + 1));
  const sc = (v) => (v == null ? '' : Number(v).toFixed(1));
  const score = (v, cls = '') => (v == null ? '' : `<span class="score num ${tier(v)} ${cls}">${sc(v)}</span>`);
  function mk(src, size = '') {
    if (!src) return '';
    if (src.logo) return `<span class="mk ${size} ${/fav-|qbitai|ycombinator/.test(src.logo) ? '' : 'pad'}"><img src="${src.logo}" alt="" loading="lazy"></span>`;
    return `<span class="mk lt ${size}" style="--h:${src.hue}">${esc(src.letter)}</span>`;
  }
  const tagsHtml = (labels, types = []) => (labels || []).filter(Boolean).map((t, i) => `<span class="${types[i] === 'extracted' ? 'x' : ''}">${esc(t)}</span>`).join('');

  /* ———————— 阅读器 ———————— */
  const UNREAD = new Set([0, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]);
  function row(a, i, sel) {
    const kind = a.kind && a.kind !== '资讯' ? `<span class="kind">${esc(a.kind)}</span><span class="dot"></span>` : '';
    const title = a.title_zh || a.title;
    return `<div class="row ${UNREAD.has(i) && !sel ? 'unread' : ''} ${sel ? 'on' : ''}">
      <div class="row-score">${score(a.score)}</div>
      <div class="row-meta">${mk(a, 'm16')}<span class="src">${esc(a.source)}</span><span class="dot"></span>${kind}<span class="rel">${esc(a.rel)}</span>${score(a.score)}</div>
      <div class="row-title">${esc(title)}</div>
      ${a.summary ? `<div class="row-sum">${esc(a.summary)}</div>` : ''}
    </div>`;
  }
  function rail(active) {
    const b = (n, k) => `<div class="rail-btn ${active === k ? 'on' : ''}">${ic(n, 's20')}</div>`;
    return `<nav class="rail"><div class="rail-logo"><img src="assets/logos/dorami.png" alt=""></div>
      ${b('paper', 'brief')}<div class="rail-sep"></div>${b('file', 'article')}${b('podcast', 'podcast')}${b('at', 'social')}<div class="rail-sep"></div>${b('compass', 'discover')}
      <div class="rail-sp"></div>${b('moon', 'x1')}${b('msg', 'x2')}${b('gear', 'x3')}<div class="rail-av">U</div></nav>`;
  }
  function side(D) {
    let h = `<aside class="side"><div class="side-head">文章</div>
      <div class="side-axis"><div class="seg full"><button class="on">${ic('rss', 's14')}订阅</button><button>${ic('tag', 's14')}兴趣</button></div></div>
      <div class="side-scroll"><div class="nav-row"><span class="ico">${ic('trend', 's16')}</span><span class="nm">文章榜</span><span class="aux">近 7 天</span></div>`;
    for (const g of D.sources) {
      h += `<div class="side-label">${esc(g.label)}</div>`;
      for (const s of g.items) h += `<div class="nav-row">${mk(s)}<span class="nm">${esc(s.name)}</span></div>`;
    }
    return h + '</div></aside>';
  }
  function article(A) {
    const tags = tagsHtml(A.tags.map((t) => t.label), A.tags.map((t) => t.type));
    return `<div class="art">
      <div class="kicker">${esc(A.source)}<i>·</i>${esc(A.event)}</div>
      <h1 class="art-title">${esc(A.title)}</h1>
      <div class="byline num"><span>${esc(A.date_cn)}</span><span class="sep">·</span><span>约 ${A.minutes} 分钟</span><span class="sep">·</span><span>阅读 ${A.reads}</span><a>${ic('ext', 's14')}查看原文</a></div>
      <div class="tags art-tags">${tags}</div>
      <section class="ai"><div class="ai-head">${AI}<b>AI 速读</b><span class="ai-dim">新闻价值 ${score(A.score)}</span></div>
        <div class="ai-text">${esc(A.summary)}</div></section>
      <div class="body">${A.body.join('')}<div class="cut">样页截取正文前段，原文后续还有 14 张图</div></div>
    </div>`;
  }
  function reader(el, o = {}) {
    ensureDefs();
    const D = window.DM;
    const sel = o.selectedId || D.article.id;
    const rows = D.articles.slice(0, o.rows || 14);
    const listHtml = `<section class="list"><div class="list-head"><span class="list-title">订阅</span>
        <div class="seg"><button class="on">全部</button><button>未读</button></div>
        <button class="ibtn" title="只看收藏">${ic('star', 's16')}</button><button class="ibtn" title="全部标为已读">${ic('checks', 's16')}</button><button class="ibtn" title="搜索">${ic('search', 's16')}</button></div>
      <div class="kinds"><button class="on">全部</button><button>论文</button><button>教程</button><button>观点</button><button>版本更新</button><button>项目</button><button>汇编</button></div>
      <div class="list-scroll"><div class="day num">9月15日<span>周二</span></div>${rows.map((a, i) => row(a, i, a.id === sel)).join('')}</div></section>`;
    const A = D.article;
    const paneHtml = `<section class="pane"><div class="pane-bar">${mk(A, 'm20')}<span class="src">${esc(A.source)}</span><span class="dom">${esc(A.domain)}</span>
        <div class="acts"><button class="ibtn" title="收藏">${ic('star', 's16')}</button><button class="ibtn" title="分享">${ic('share', 's16')}</button><button class="ibtn" title="更多">${ic('more', 's16')}</button></div></div>
      <div class="pane-scroll">${article(A)}</div></section>`;
    el.innerHTML = rail('article') + side(D) + listHtml + paneHtml;
    el.classList.add('dm', 'app');
  }

  /* ———————— 早报 · 夜航版式 ———————— */
  // 头条:有「重大事件」取其第一条,否则取全期新闻价值最高的一条;其余按板块固定顺序、板块内保持原序
  function briefOrder(B) {
    const all = [];
    B.sections.forEach((s) => s.items.forEach((it) => all.push({ ...it, section: s.name })));
    const head = all.find((x) => x.section === '重大事件') || all.reduce((a, b) => ((b.score || 0) > (a.score || 0) ? b : a), all[0]);
    const groups = B.sections.map((s) => ({ name: s.name, items: s.items.filter((x) => x.id !== head.id) })).filter((g) => g.items.length);
    return { head, groups, total: all.length };
  }
  function popHtml(B) {
    return `<div class="bp-pop" role="menu" aria-label="往期早报"><p class="bp-pop-title">往期早报</p>${B.history.map((h) => `<button class="bp-pop-item" role="menuitem" aria-current="${h.today ? 'true' : 'false'}">${ic('calendar', 's16')}${esc(h.label)} ${esc(h.wd)}${h.today ? '<span class="aux">今天</span>' : ''}</button>`).join('')}</div>`;
  }
  function posterHtml(B, o = {}) {
    const { head: h, total } = briefOrder(B);
    const orig = h.title_orig ? `<p class="bp-orig" lang="en">${esc(h.title_orig)}</p>` : '';
    const today = B.history.find((x) => x.today);
    return `<section class="bp-poster"><div class="bp-light" aria-hidden="true"></div><div class="bp-wrap">
      <div class="bp-top"><p class="bp-eyebrow"><b>早报</b><i></i><span class="num">${esc(B.date_cn)} ${esc(today ? today.wd : '')}</span></p>
        <div class="bp-top-r"><span class="note num">${esc(B.generated)} 编排 · ${total} 条</span>
          <button class="lbtn" aria-haspopup="menu" aria-expanded="${o.pop ? 'true' : 'false'}">${ic('calendar', 's16')}往期</button>
          <button class="lbtn" title="重新编排今日早报">${ic('refresh', 's16')}重新编排</button>${o.pop ? popHtml(B) : ''}</div></div>
      <p class="bp-kicker">头条 · ${esc(h.section)}</p>
      <h1 class="bp-title">${esc(h.title)}</h1>${orig}
      <div class="bp-lower"><div>
          <p class="bp-meta"><span class="src">${mk(h, 'm16')}${esc(h.source)}</span><span class="dot"></span><span class="num">${esc(h.rel)}</span>${h.title_orig ? '<span class="dot"></span><span>英文</span>' : ''}</p>
          <p class="bp-sum">${esc(h.summary)}</p>
          <div class="bp-actions"><button class="btn btn-primary btn-lg">阅读全文${ic('arrowR', 's16')}</button><button class="btn btn-outline btn-lg">${ic('star', 's16')}收藏</button></div></div>
        <aside class="bp-verdict" aria-label="新闻价值"><p class="bp-verdict-label">新闻价值</p>${score(h.score)}<p class="bp-verdict-reason">${esc(h.reason || '')}</p><p class="bp-verdict-why">${esc(h.why || '')}</p></aside></div>
      <span class="bp-cue">${ic('arrowD', 's16')}还有 ${total - 1} 条</span></div></section>`;
  }
  function itemHtml(it) {
    const lane = it.lane === 'interest' ? `<span class="bp-hit">${ic('tag', 's14')}兴趣</span>` : '';
    return `<article class="bp-item"><h3 class="bp-item-title">${esc(it.title)}</h3>${it.title_orig ? `<p class="bp-item-orig" lang="en">${esc(it.title_orig)}</p>` : ''}
      <p class="bp-item-sum">${esc(it.summary)}</p>
      <div class="bp-item-meta"><span class="src">${mk(it, 'm16')}${esc(it.source)}</span><span class="dot"></span><span class="num">${esc(it.rel)}</span><span class="dot"></span>${score(it.score)}${lane}<button class="ibtn" title="收藏">${ic('star', 's16')}</button></div></article>`;
  }
  function actsHtml(B, limit) {
    const { groups } = briefOrder(B);
    return groups.slice(0, limit || groups.length).map((g) => `<section class="bp-act"><div class="bp-act-head"><h2 class="bp-act-name">${esc(g.name)}</h2><p class="bp-act-count num">${g.items.length} 条</p></div>
      <div class="bp-items">${g.items.map(itemHtml).join('')}</div></section>`).join('');
  }
  function creditsHtml(B) {
    const past = B.history.filter((x) => !x.today);
    return `<section class="bp-credits"><h2>本期编排</h2><dl>
      <dt>编排</dt><dd><b class="num">${esc(B.date_cn)} ${esc(B.generated)}</b></dd>
      <dt>范围</dt><dd>你订阅的来源 · 近 <span class="num">${B.window_hours}</span> 小时的新内容</dd>
      <dt>头条</dt><dd>有重大事件时取重大事件，否则取本期新闻价值最高的一条；其余按板块排列</dd>
      <dt>入选</dt><dd>从 <b class="num">${B.candidates}</b> 篇里选出 <b class="num">${B.selected}</b> 篇，都是你订阅来源里的高质量内容；板块顺序固定，板块内按新闻价值</dd>
      ${B.sync_stale ? '<dt>说明</dt><dd>部分来源更新较晚，个别内容可能没赶上这一期</dd>' : ''}
      <dt>往期</dt><dd><span class="bp-chips">${past.map((x) => `<button class="chip num">${esc(x.label)}</button>`).join('')}</span></dd></dl></section>`;
  }
  function brief(el, o = {}) {
    ensureDefs();
    const B = window.DM.brief;
    el.innerHTML = rail('brief') + `<main class="bp">${posterHtml(B, o)}<div class="bp-wrap">${actsHtml(B, o.acts)}${o.acts ? '' : creditsHtml(B)}</div></main>`;
    el.classList.add('dm', 'brief-app');
  }
  function phone(el, o = {}) {
    ensureDefs();
    const B = window.DM.brief;
    const tab = (n, t, on) => `<div class="${on ? 'on' : ''}">${ic(n)}${t}</div>`;
    el.innerHTML = `<div class="ph-body">${posterHtml(B, o)}<div class="bp-wrap">${actsHtml(B, 1)}</div></div>
      <nav class="ph-tabs">${tab('paper', '早报', 1)}${tab('file', '文章')}${tab('podcast', '播客')}${tab('at', '社交')}${tab('chart', '榜单')}${tab('user', '我的')}</nav>`;
    el.classList.add('dm', 'phone');
  }

  /* ———————— 设置 · 外观 ———————— */
  const PALS = {
    porcelain: { name: '瓷', tone: '冷调', light: ['#f4f6f9', '#ffffff', '#161922', '#a9acb3'], dark: ['#0d0f13', '#14161a', '#e8e9ee', '#5b5e66'] },
    graphite: { name: '墨', tone: '中性', light: ['#f5f4f4', '#ffffff', '#171717', '#ababab'], dark: ['#0e0e0e', '#151515', '#e8e8e8', '#5e5e5e'] },
    paper: { name: '宣', tone: '暖调', light: ['#f5f2ed', '#fdfcf9', '#201b16', '#b1aba4'], dark: ['#13100d', '#1a1613', '#eae5dd', '#615c55'] },
  };
  function palCards(cur, mode) {
    return Object.entries(PALS).map(([k, p]) => {
      const [chrome, surf, ink, muted] = p[mode === 'dark' ? 'dark' : 'light'];
      return `<button class="st-pal" role="radio" aria-checked="${k === cur}" data-pal-pick="${k}"><span class="st-pal-sw" style="--ink-s:${ink};--muted-s:${muted}"><i style="background:${chrome}"></i><i style="background:${surf}"></i></span><span class="st-pal-name"><span>${p.name}</span><span style="color:var(--c-faint);font-weight:400">${p.tone}</span></span></button>`;
    }).join('');
  }
  function settings(el) {
    const cur = el.dataset.pal || 'porcelain', mode = el.dataset.mode || 'light';
    const nav = (n, t, on) => `<div class="st-nav-item ${on ? 'on' : ''}">${ic(n, 's16')}${t}</div>`;
    el.innerHTML = `<nav class="st-nav"><div class="st-nav-label">通用</div>${nav('user', '账户')}${nav('sun', '外观', 1)}${nav('msg', '反馈与建议')}
        <div class="st-nav-label">接入集成</div>${nav('rss', '聚合接口')}${nav('link', 'MCP 接入')}${nav('book', 'Agent 技能包')}<div class="st-nav-label">关于</div>${nav('clock', '关于')}</nav>
      <main class="st-main"><h3>外观</h3>
        <div class="st-row"><div class="st-id"><div class="st-lbl">主题</div><div class="st-sub">跟随系统时，随操作系统亮暗自动切换</div></div>
          <div class="st-ctl"><div class="seg"><button class="${mode === 'light' ? 'on' : ''}">亮色</button><button class="${mode === 'dark' ? 'on' : ''}">暗色</button><button>跟随系统</button></div></div></div>
        <div class="st-row"><div class="st-id"><div class="st-lbl">中性色</div><div class="st-sub">界面底色与文字的色调，亮色和暗色下都生效</div></div>
          <div class="st-ctl"><div class="st-pals" role="radiogroup" aria-label="中性色">${palCards(cur, mode)}</div></div></div>
        <div class="st-row"><div class="st-id"><div class="st-lbl">减少动效</div><div class="st-sub">开启后界面过渡与循环提示即时完成，不播放动画</div></div><div class="st-ctl"><span class="switch"></span></div></div>
      </main>`;
    el.classList.add('dm', 'sett');
  }

  /* ———————— 发现卡(样本取自目录接口,订阅态即 user 账号的真实订阅) ———————— */
  const DISC = [
    { name: 'X · OpenAI', logo: 'assets/logos/openai.png', desc: 'OpenAI 官方账号;作为 X 与官网新闻时效/重复率对照组。', subs: 7, last: '07-24', on: true },
    { name: 'X · Qwen', logo: 'assets/logos/qwen.png', desc: '通义千问官方模型、开源与产品动态。', subs: 0, last: '07-24', on: false },
    { name: 'X · Moonshot AI', letter: 'K', hue: 260, desc: 'Moonshot AI / Kimi 官方模型与开源动态。', subs: 2, last: '07-23', on: false },
    { name: 'X · DeepSeek', logo: 'assets/logos/deepseek.png', desc: 'DeepSeek 官方模型、论文与服务动态。', subs: 0, last: '07-21', on: false },
  ];
  function discover(el) {
    el.innerHTML = `<div class="disc-grid">${DISC.map((d) => `<div class="dcard">${mk(d, 'm36')}<div style="min-width:0"><h4>${esc(d.name)}</h4><p>${esc(d.desc)}</p>
      <div class="dmeta num"><span>${d.subs ? d.subs + ' 人订阅' : '暂无订阅'}</span><span>·</span><span>最近 ${d.last}</span></div></div>
      <div class="dact">${d.on ? `<button class="btn-sub done">${ic('check', 's14')}已订阅</button>` : `<button class="btn-sub">${ic('plus', 's14')}订阅</button>`}</div></div>`).join('')}</div>`;
    el.classList.add('dm');
  }


  /* ———————— 局部样页:单独的条目列 / 源栏 / 阅读窗标题区 ———————— */
  function listOnly(el, o = {}) {
    ensureDefs();
    const D = window.DM;
    const rows = D.articles.slice(o.from || 0, (o.from || 0) + (o.rows || 6));
    el.innerHTML = `<section class="list" style="border:0;height:auto"><div class="list-scroll" style="padding-top:8px">${o.day === false ? '' : '<div class="day num">9月15日<span>周二</span></div>'}${rows.map((a, i) => row(a, i + (o.from || 0), a.id === o.sel)).join('')}</div></section>`;
    el.classList.add('dm');
  }
  function sideOnly(el) {
    const D = window.DM;
    el.innerHTML = rail('article') + side(D);
    el.classList.add('dm');
    el.style.display = 'grid'; el.style.gridTemplateColumns = '64px 240px'; el.style.height = el.dataset.h || '560px'; el.style.overflow = 'hidden';
  }
  function paneOnly(el, o = {}) {
    ensureDefs();
    const A = window.DM.article;
    el.innerHTML = article(A);
    el.classList.add('dm');
    if (o.body === false) { const b = el.querySelector('.body'); if (b) b.remove(); }
  }

  /* ———————— 管理面 · 运维看板(数字来自本地库副本的 /api/admin/* 接口) ———————— */
  const DAY0 = new Date('2026-09-14T00:00:00+08:00');
  function days(n, endIso) {
    const [y, m, d] = endIso.split('-').map(Number); const end = Date.UTC(y, m - 1, d); const out = [];
    for (let i = n - 1; i >= 0; i--) out.push(new Date(end - i * 86400000).toISOString().slice(0, 10));
    return out;
  }
  const niceTop = (m) => { const p = Math.pow(10, Math.floor(Math.log10(m))); const k = m / p; const st = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => s >= k); return st * p; };
  const showTick = (i, n) => (n - 1 - i) % 7 === 0 || (i === 0 && (n - 1) % 7 > 2);
  function spark(values, w = 72, h = 26) {
    const max = Math.max(1, ...values); const n = values.length;
    const pts = values.map((v, i) => [(i / (n - 1)) * w, h - 2 - (v / max) * (h - 6)]);
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const last = pts[pts.length - 1];
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${line} L ${w} ${h} L 0 ${h} Z" fill="var(--c-wash)"/><path d="${line}" fill="none" stroke="var(--c-accent)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="2.2" fill="var(--c-accent)"/></svg>`;
  }
  function barsSvg(series, o = {}) {
    // series: [{day, v}] 30 天,稀疏数据也画满刻度:无数据日画一条底线短刻度,而不是空白
    const W = o.w || 560, H = o.h || 140, padL = 36, padB = 22, padT = 8;
    const max = Math.max(1, ...series.map((s) => s.v));
    const top = niceTop(max); const n = series.length; const bw = (W - padL) / n;
    let g = '';
    [0, 0.5, 1].forEach((t) => {
      const y = padT + (H - padT - padB) * (1 - t);
      g += `<line x1="${padL}" x2="${W}" y1="${y}" y2="${y}" stroke="var(--c-line-soft)"/><text x="${padL - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="var(--c-faint)">${Math.round(top * t)}</text>`;
    });
    series.forEach((s, i) => {
      const x = padL + i * bw + bw / 2;
      if (s.v > 0) {
        const hh = Math.max(2, (H - padT - padB) * (s.v / top));
        g += `<rect x="${(x - 3).toFixed(1)}" y="${(H - padB - hh).toFixed(1)}" width="6" height="${hh.toFixed(1)}" rx="3" fill="var(--c-accent)"><title>${s.day} · ${s.v}</title></rect>`;
      } else {
        g += `<rect x="${(x - 1).toFixed(1)}" y="${H - padB - 2}" width="2" height="2" rx="1" fill="var(--c-line)"/>`;
      }
      if (showTick(i, n)) g += `<text x="${x}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--c-faint)">${s.day.slice(5).replace('-', '/')}</text>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;font-family:var(--f-sans);font-variant-numeric:tabular-nums" aria-hidden="true">${g}</svg>`;
  }
  function console_(el) {
    const A = window.DM.admin; const sm = A.accounts.summary;
    const end = A.growth.as_of_day || '2026-10-13';
    const ds = days(30, end);
    const aiByDay = Object.fromEntries((A.ai.by_day || []).map((d) => [d.day, d.calls]));
    const aiSeries = ds.map((d) => ({ day: d, v: aiByDay[d] || 0 }));
    // 账户累计:按创建日回溯(与 /api/admin/account-growth 同口径)
    const grow = {}; (A.growth.series || []).forEach((s) => { grow[s.day] = s.new; });
    let base = 0; Object.entries(grow).forEach(([d, v]) => { if (d < ds[0]) base += v; });
    let acc = base; const cum = ds.map((d) => { acc += grow[d] || 0; return acc; });
    const newIn = cum[cum.length - 1] - base;
    const r = (n, k) => `<button class="rail-btn ${k ? 'on' : ''}">${ic(n, 's20')}</button>`;
    const railH = `<nav class="rail"><div class="rail-logo"><img src="assets/logos/dorami.png" alt=""></div>${r('db')}${r('cloud')}${r('history')}${r('paper')}${r('shield', 1)}<div class="rail-sp"></div>${r('book')}${r('moon')}${r('gear')}<div class="rail-av" style="color:oklch(0.42 0.1 300);background:oklch(0.94 0.035 300)">A</div></nav>`;
    const kpi = (lab, val, sub, sp) => `<div class="kpi"><div class="lab">${lab}</div><div class="val">${val}</div>${sp || '<span></span>'}<div class="sub num">${sub}</div></div>`;
    const loginsBy = ds.map((d) => 0);
    const rows = [...A.accounts.items].sort((a, b) => (b.reads + b.logins) - (a.reads + a.logins));
    const fmtDay = (iso) => (iso ? iso.slice(5, 10).replace('-', '/') + ' ' + iso.slice(11, 16) : '—');
    const hue = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
    const tr = rows.map((u) => `<tr><td><div class="who"><span class="av" style="--h:${hue(u.username)}">${esc(u.username.slice(0, 1).toUpperCase())}</span>${esc(u.username)}</div></td>
      <td>${u.role === 'admin' ? '管理员' : '读者'}</td><td><span class="st ${u.is_active ? '' : 'off'}">${u.is_active ? '启用' : '停用'}</span></td>
      <td class="num">${fmtDay(u.last_login_at)}</td><td class="r">${u.logins || '<span class="muted">—</span>'}</td><td class="r">${u.reads || '<span class="muted">—</span>'}</td><td class="r">${u.ai_calls || '<span class="muted">—</span>'}</td><td class="r">${u.subscription_count}</td></tr>`).join('');
    const topReads = sm.top_reads || []; const maxR = Math.max(1, ...topReads.map((t) => t.value));
    const rank = topReads.map((t) => `<div style="display:grid;grid-template-columns:72px minmax(0,1fr) 28px;align-items:center;gap:12px;height:30px"><span style="font-size:13px;color:var(--c-ink2)">${esc(t.username)}</span><span style="height:6px;border-radius:3px;background:var(--c-sunken);position:relative;overflow:hidden"><i style="position:absolute;inset:0 auto 0 0;width:${(t.value / maxR) * 100}%;background:var(--c-accent);border-radius:3px"></i></span><span class="num" style="font-size:13px;text-align:right;color:var(--c-ink)">${t.value}</span></div>`).join('');
    // 账户累计线:窗口前的存量是起点,新增那天抬一阶
    const W = 520, H = 110, n = cum.length; const lo = Math.min(base, ...cum) - 1, hi = Math.max(...cum) + 1;
    const X = (i) => ((i + 0.5) / n) * W; const Y = (v) => 14 + (H - 34) * (1 - (v - lo) / (hi - lo));
    let step = `M0 ${Y(base).toFixed(1)}`; let prev = base;
    cum.forEach((v, i) => { if (v !== prev) { step += ` H${X(i).toFixed(1)} V${Y(v).toFixed(1)}`; prev = v; } });
    step += ` H${W}`;
    const ev = ds.map((d, i) => (grow[d] ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(cum[i]).toFixed(1)}" r="3.5" fill="var(--c-surface)" stroke="var(--c-accent)" stroke-width="1.6"/><text x="${(X(i) + 8).toFixed(1)}" y="${(Y(cum[i]) - 8).toFixed(1)}" font-size="11" fill="var(--c-ink2)">+${grow[d]}</text>` : '')).join('');
    const growthSvg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;font-family:var(--f-sans);font-variant-numeric:tabular-nums" aria-hidden="true"><line x1="0" x2="${W}" y1="${H - 20}" y2="${H - 20}" stroke="var(--c-line-soft)"/><path d="${step}" fill="none" stroke="var(--c-accent)" stroke-width="1.6"/>${ev}<text x="${W}" y="${(Y(cum[n - 1]) - 8).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--c-ink2)">${cum[n - 1]}</text><text x="0" y="${H - 4}" font-size="11" fill="var(--c-faint)">${ds[0].slice(5).replace('-', '/')}</text><text x="${W}" y="${H - 4}" font-size="11" fill="var(--c-faint)" text-anchor="end">${ds[n - 1].slice(5).replace('-', '/')}</text></svg>`;
    el.innerHTML = railH + `<main class="con"><div class="con-head"><h1>运维管理</h1>
        <div class="seg"><button>7 天</button><button>14 天</button><button class="on">30 天</button><button>90 天</button></div>
        <div class="seg"><button class="on">用户</button><button>内容</button><button>AI</button><button>分析与标签</button><button>消息</button></div></div>
      <div class="panel kpis">${kpi('账户', sm.accounts, `管理员 ${sm.admins}`)}${kpi('近 30 天活跃', sm.logged_in_window, '登录过 ≥1 次')}${kpi('登录次数', sm.logins, `日均 ${(sm.logins / 30).toFixed(1)}`)}${kpi('阅读次数', sm.reads, `日均 ${(sm.reads / 30).toFixed(1)}`)}${kpi('AI 调用', sm.ai_calls, `tokens ${(sm.ai_tokens / 1000).toFixed(1)}k`)}</div>
      <div style="display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:16px;margin-top:16px">
        <div class="panel"><div class="p-head"><h3>账户增长</h3><span class="note2 num">${sm.accounts} 个 · 近 30 天新增 ${newIn}</span></div><div style="padding:14px 20px 14px">${growthSvg}</div></div>
        <div class="panel"><div class="p-head"><h3>活跃用户 Top</h3><div class="seg"><button class="on">阅读</button><button>登录</button></div></div><div style="padding:12px 20px 16px">${rank}</div></div>
      </div>
      <div class="panel" style="margin-top:16px"><div class="p-head" style="padding-bottom:6px"><h3>账户管理</h3><button class="btn btn-outline">${ic('plus', 's14')}新建账户</button></div>
        <table class="tbl"><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>最近登录</th><th class="r">登录</th><th class="r">阅读</th><th class="r">AI 调用</th><th class="r">订阅</th></tr></thead><tbody>${tr}</tbody></table></div>
    </main>`;
    el.classList.add('dm', 'console');
  }

  function aiDaily(el) {
    const A = window.DM.admin; const ds = days(30, A.growth.as_of_day || '2026-10-13');
    const P = [['article_analysis', '文章分析', 'var(--c-s1)'], ['image_insight', '图片理解', 'var(--c-s2)'], ['ask', '读者问答', 'var(--c-s3)'], ['personal_digest_title', '早报标题', 'var(--c-s4)']];
    const m = {}; (A.ai.by_day_purpose || []).forEach((r) => { (m[r.day] = m[r.day] || {})[r.purpose] = r.calls; });
    const tot = ds.map((d) => P.reduce((s, [k]) => s + ((m[d] || {})[k] || 0), 0));
    const top = niceTop(Math.max(1, ...tot)); const W = 640, H = 220, padL = 36, padB = 24, padT = 10, n = ds.length; const bw = (W - padL) / n; const ih = H - padT - padB;
    let g = '';
    [0, 0.5, 1].forEach((t) => { const y = padT + ih * (1 - t); g += `<line x1="${padL}" x2="${W}" y1="${y}" y2="${y}" stroke="var(--c-line-soft)"/><text x="${padL - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="var(--c-faint)">${Math.round(top * t)}</text>`; });
    ds.forEach((d, i) => {
      const x = padL + i * bw + bw / 2; let y = padT + ih;
      if (!tot[i]) { g += `<rect x="${(x - 1).toFixed(1)}" y="${y - 2}" width="2" height="2" rx="1" fill="var(--c-line)"/>`; }
      P.forEach(([k, , c]) => { const v = (m[d] || {})[k] || 0; if (!v) return; const h = ih * v / top; y -= h; g += `<rect x="${(x - 4).toFixed(1)}" y="${y.toFixed(1)}" width="8" height="${Math.max(1, h - 1).toFixed(1)}" rx="1.5" fill="${c}"><title>${d} · ${v}</title></rect>`; });
      if (showTick(i, n)) g += `<text x="${i === n - 1 ? W : x}" y="${H - 6}" text-anchor="${i === n - 1 ? 'end' : 'middle'}" font-size="11" fill="var(--c-faint)">${d.slice(5).replace('-', '/')}</text>`;
    });
    const sums = P.map(([k]) => (A.ai.by_purpose.find((r) => r.purpose === k) || {}).calls || 0);
    const legend = P.map(([, lab, c], i) => `<span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--c-ink2)"><i style="width:8px;height:8px;border-radius:2px;background:${c}"></i>${lab}<b class="num" style="font-weight:400;color:var(--c-muted)">${sums[i]}</b></span>`).join('');
    el.innerHTML = `<div class="panel"><div class="p-head"><h3>每日调用次数</h3><div class="seg"><button class="on">按用途</button><button>按用户</button></div></div>
      <div style="display:flex;flex-wrap:wrap;gap:6px 16px;padding:10px 20px 0">${legend}</div>
      <div style="padding:8px 20px 14px"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;font-family:var(--f-sans);font-variant-numeric:tabular-nums" aria-hidden="true">${g}</svg></div></div>`;
    el.classList.add('dm');
  }

  window.DMR = { ic, AI, mk, esc, score, tier, sc, tagsHtml, row, reader, brief, phone, settings, discover, ensureDefs, rail, briefOrder, listOnly, sideOnly, paneOnly, console: console_, spark, barsSvg, aiDaily };
})();
