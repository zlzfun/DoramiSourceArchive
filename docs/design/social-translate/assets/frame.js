/* 社交流翻译样页(issue #84)· 画面渲染与交互
   卡片 / 外壳的结构与类名照抄现网(SocialFlow.jsx / ReaderTab.jsx / MobileReader.jsx / ActionSheet.jsx / ContextMenu.jsx),
   样式来自前端编译产物 assets/app.css;本波新增的只有 assets/social-tr.css 里的 .social-tr* / .social-quote-tr / .social-act-glyph。
   参数:device=desktop|mobile  entry=foot|act|head  actvis=hover|always(仅 entry=act)  show=below|replace
        state=idle|loading|done|error|quota|menu|aioff  theme=dark|light  palette=porcelain|graphite|paper */
(function () {
  const q = new URLSearchParams(location.search);
  const S = {
    device: q.get('device') === 'mobile' ? 'mobile' : 'desktop',
    entry: ['foot', 'act', 'head'].includes(q.get('entry')) ? q.get('entry') : 'foot',
    actvis: q.get('actvis') === 'always' ? 'always' : 'hover',
    show: q.get('show') === 'replace' ? 'replace' : 'below',
    state: q.get('state') || 'idle',
    headOn: false,
  };
  const NOW = Date.parse(ST.now);
  const LATENCY = 1100;   // 模拟一次翻译请求的耗时;再次展开命中缓存,立即显示

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const svg = (paths, cls = '') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const P = {
    sparkles: '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
    loader: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    star: '<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/>',
    starOff: '<path d="M8.34 8.34 2 9.27l5 4.87L5.82 21 12 17.77 18.18 21l-.59-3.43"/><path d="M18.42 12.76 22 9.27l-6.91-1L12 2l-1.44 2.91"/><line x1="2" x2="22" y1="2" y2="22"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    circle: '<circle cx="12" cy="12" r="10"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>',
    repeat: '<path d="m2 9 3-3 3 3"/><path d="M13 18H7a2 2 0 0 1-2-2V6"/><path d="m22 15-3 3-3-3"/><path d="M11 6h6a2 2 0 0 1 2 2v10"/>',
    checkCheck: '<path d="M18 6 7 17l-5-5"/><path d="m22 10-7.5 7.5L13 16"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    menu: '<line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="18" y2="18"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    fileText: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    podcast: '<path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/>',
    atSign: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
    compass: '<path d="m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z"/><circle cx="12" cy="12" r="10"/>',
    newspaper: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    barChart: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    user: '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
  };
  const ic = (name, cls = '') => svg(P[name], cls);
  const spin = (cls) => ic('loader', `animate-spin ${cls}`);

  /* ── 时间:与现网 formatRelativeTime / dayLabelOf 同规则,「现在」取 ST.now ── */
  const pad = (n) => String(n).padStart(2, '0');
  const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  function relTime(iso) {
    const diff = Math.floor((NOW - Date.parse(iso)) / 1000);
    if (diff < 60) return '刚刚';
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
    const d = new Date(iso);
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function dayLabel(iso) {
    const d = new Date(iso);
    const now = new Date(NOW);
    const md = `${d.getMonth() + 1}月${d.getDate()}日`;
    if (dayKey(d) === dayKey(now)) return `今天 · ${md}`;
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (dayKey(d) === dayKey(y)) return `昨天 · ${md}`;
    return `${md} 周${'日一二三四五六'[d.getDay()]}`;
  }
  const fullTime = (iso) => { const d = new Date(iso); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

  /* ── 状态 ── */
  const account = (sid) => ST.accounts.find((a) => a.source_id === sid) || { name: sid };
  const aiOn = () => S.state !== 'aioff';
  // 可译 = 主推有可译文字,或引用推有可译文字(样页数据里以 text_zh 是否存在表示判定结果)
  const canTr = (p) => aiOn() && Boolean(p.text_zh || p.quoted?.text_zh);
  const T = {};   // id → { tr: idle|loading|done|error|quota, cached, expanded, unread, fav }
  const PRESET = { idle: 'idle', loading: 'loading', done: 'done', error: 'error', quota: 'quota', menu: 'idle', aioff: 'idle' };
  ST.posts.forEach((p) => {
    T[p.id] = { tr: canTr(p) ? (PRESET[S.state] || 'idle') : 'idle', cached: false, expanded: false, unread: p.unread, fav: Boolean(p.favorite) };
  });
  if (S.entry === 'head' && ['loading', 'done', 'error', 'quota'].includes(S.state)) S.headOn = true;
  let menu = null;   // { id, x, y }

  /* ── 推文卡(结构同 SocialPost) ── */
  function trRowParts(p, st) {
    // 卡片最后一行(媒体与引用推之后)的翻译部件。方案 A 的入口与开合都在这一行,位置不随状态移动;
    // 方案 B / C 的入口不在这里,这一行只承载失败说明与替换模式的标记。
    const out = [];
    const foot = S.entry === 'foot';
    if (st.tr === 'error') {
      out.push('<span class="social-tr-err"><span class="social-tr-note">翻译失败</span><button type="button" class="social-tr-retry" data-act="tr">重试</button></span>');
      return out;
    }
    if (st.tr === 'quota') {
      out.push('<span class="social-tr-note">今日 AI 翻译次数已用完，明天再试</span>');
      return out;
    }
    if (st.tr === 'done') {
      const replace = S.show === 'replace';
      if (foot) {
        out.push(`<span class="social-tr-mark">${ic('sparkles')}<span>${replace ? '由 AI 译为中文' : '已译为中文'}</span><button type="button" class="social-tr-hide" data-act="untr">${replace ? '显示原文' : '收起'}</button></span>`);
      } else if (replace) {
        out.push(`<span class="social-tr-mark">${ic('sparkles')}<span>由 AI 译为中文</span></span>`);
      }
      return out;
    }
    if (!foot) return out;
    if (st.tr === 'loading') {
      out.push(`<button type="button" class="social-tr-btn" disabled>${spin('')}翻译中…</button>`);
    } else {
      out.push(`<button type="button" class="social-tr-btn" data-act="tr" title="将这条推文${p.quoted?.text_zh ? '和引用推' : ''}译为中文">${ic('sparkles')}译为中文</button>`);
    }
    return out;
  }

  function trBlock(p, st) {
    if (!canTr(p) || S.show !== 'below') return '';
    if (st.tr === 'loading' && S.entry === 'head') {
      return `<div class="social-tr"><div class="social-tr-head">${spin('')}<span>翻译中…</span></div></div>`;
    }
    if (st.tr !== 'done' || !p.text_zh) return '';
    return `<div class="social-tr" lang="zh-CN">
      <div class="social-tr-head">${ic('sparkles')}<span class="social-tr-label ai-grad-text">译文</span></div>
      <p class="social-tr-text">${esc(p.text_zh)}</p>
    </div>`;
  }

  function quoteHtml(p, st) {
    const qd = p.quoted;
    if (!qd) return '';
    const done = canTr(p) && st.tr === 'done' && qd.text_zh;
    const text = done && S.show === 'replace' ? qd.text_zh : qd.text;
    const avatar = qd.avatar
      ? `<img class="social-quote-avatar" src="${qd.avatar}" alt="">`
      : '<span class="social-quote-avatar" aria-hidden="true"></span>';   // 上游 404:现网 img 加载失败只剩灰色圆底
    return `<a class="social-quote" href="${esc(qd.url)}" target="_blank" rel="noreferrer noopener" data-act="noop">
      <span class="social-quote-head">${avatar}<span class="social-quote-name">${esc(qd.author_name)}</span><span class="social-quote-meta">@${esc(qd.handle)}</span></span>
      <span class="social-quote-text"${done && S.show === 'replace' ? ' lang="zh-CN"' : ''}>${esc(text)}</span>
      ${done && S.show === 'below' ? `<span class="social-quote-tr" lang="zh-CN">${esc(qd.text_zh)}</span>` : ''}
    </a>`;
  }

  function actTrBtn(p, st) {
    if (S.entry !== 'act' || !canTr(p)) return '';
    const on = st.tr === 'done';
    const busy = st.tr === 'loading';
    const label = on ? (S.show === 'replace' ? '显示原文' : '收起译文') : '将推文和引用推译为中文';
    return `<button type="button" class="social-act ${on ? 'is-tr' : ''} ${S.actvis === 'always' ? 'is-pinned' : ''}" aria-label="${label}" title="${label}" aria-pressed="${on}" data-act="act-tr" ${busy ? 'disabled' : ''}>
      ${busy ? spin('h-[15px] w-[15px]') : '<svg class="social-act-glyph h-[15px] w-[15px]" viewBox="0 0 15 15" aria-hidden="true"><text x="7.5" y="12" text-anchor="middle">译</text></svg>'}
    </button>`;
  }

  function postHtml(p) {
    const st = T[p.id];
    const acc = account(p.source_id);
    const done = canTr(p) && st.tr === 'done';
    const replaceMain = done && S.show === 'replace' && p.text_zh;
    const text = replaceMain ? p.text_zh : p.text;
    const long = text.length > 360;               // CLAMP_CHARS,与现网同
    const clamped = long && !st.expanded;
    const row = canTr(p) ? trRowParts(p, st) : [];
    const ctx = menu && menu.id === p.id ? ' is-ctx-anchor' : '';
    return `<article class="social-post ${st.unread ? '' : 'is-read'}${ctx}" data-id="${p.id}">
      ${p.reposted ? `<p class="social-repost">${ic('repeat', 'h-[13px] w-[13px]')}${esc(acc.name)} 转推</p>` : ''}
      <div class="social-body">
        <span class="social-avatar-wrap"><img class="social-avatar-img" src="${p.avatar}" alt=""></span>
        <div class="min-w-0">
          <div class="social-post-head">
            <span class="social-name">${esc(p.author_name)}</span>
            <span class="social-handle">@${esc(p.handle)}</span>
            <span class="social-dot">·</span>
            <a class="social-time" href="${esc(p.url)}" target="_blank" rel="noreferrer noopener" title="在原平台打开 · ${fullTime(p.date)}" data-act="noop">${relTime(p.date)}</a>
            <span class="social-head-sp"></span>
            <span class="social-acts">
              <button type="button" class="social-act ${st.fav ? 'is-fav' : ''}" aria-label="${st.fav ? '取消收藏' : '收藏'}" title="${st.fav ? '取消收藏' : '收藏'}" data-act="fav">${svg(P.star, 'h-[15px] w-[15px]').replace('fill="none"', `fill="${st.fav ? 'currentColor' : 'none'}"`)}</button>
              <button type="button" class="social-act" aria-label="${st.unread ? '标为已读' : '标为未读'}" title="${st.unread ? '标为已读' : '标为未读'}" data-act="read">${ic(st.unread ? 'check' : 'circle', 'h-[15px] w-[15px]')}</button>
              ${actTrBtn(p, st)}
            </span>
          </div>
          <p class="social-text ${clamped ? 'is-clamped' : ''}"${replaceMain ? ' lang="zh-CN"' : ''}>${esc(text)}</p>
          ${long ? `<button type="button" class="social-more" data-act="more">${st.expanded ? '收起' : '显示更多'}</button>` : ''}
          ${trBlock(p, st)}
          ${quoteHtml(p, st)}
          ${row.length ? `<div class="social-tr-row">${row.join('')}</div>` : ''}
        </div>
      </div>
    </article>`;
  }

  function flowHtml() {
    let out = '';
    let last = '';
    ST.posts.forEach((p) => {
      const label = dayLabel(p.date);
      if (label !== last) { out += `<div class="reader-social-day">${label}</div>`; last = label; }
      out += postHtml(p);
    });
    return `${out}<p class="reader-social-end">已到底部</p>`;
  }

  /* ── 方案 C 的流头开关:复用文章阅读窗的语言二段 ── */
  function headSeg() {
    if (S.entry !== 'head' || !aiOn()) return '';
    const busy = S.headOn && ST.posts.some((p) => canTr(p) && T[p.id].tr === 'loading');
    return `<div class="reader-tr-seg" role="group" aria-label="推文语言">
      <button type="button" class="reader-tr-seg-btn ${S.headOn ? '' : 'is-on'}" aria-pressed="${!S.headOn}" data-act="head-off">原语言</button>
      <button type="button" class="reader-tr-seg-btn ${S.headOn ? 'is-on is-ai' : ''}" aria-pressed="${S.headOn}" data-act="head-on" title="${S.headOn ? '当前显示中文译文' : '把这一屏推文译为中文'}">
        ${busy ? spin('h-3.5 w-3.5') : ic('sparkles', 'h-3.5 w-3.5')}<span class="${S.headOn ? 'ai-grad-text' : ''}">${S.headOn ? '中文' : '译为中文'}</span>
      </button>
    </div>`;
  }

  /* ── 右键菜单 / 长按动作单:与 buildSocialMenuItems 同源,新增「译为中文 / 收起译文」一项 ── */
  function menuItems(p) {
    const st = T[p.id];
    const items = [
      { key: 'read', label: st.unread ? '标为已读' : '标为未读', icon: st.unread ? 'check' : 'undo' },
      { key: 'fav', label: st.fav ? '取消收藏' : '收藏推文', icon: st.fav ? 'starOff' : 'star' },
    ];
    if (canTr(p) && S.entry !== 'head') {
      const on = st.tr === 'done';
      items.push({ key: on ? 'untr' : 'tr', label: on ? (S.show === 'replace' ? '显示原文' : '收起译文') : '译为中文', icon: 'sparkles', disabled: st.tr === 'loading' || st.tr === 'quota' });
    }
    items.push({ type: 'sep' },
      { key: 'copy', label: '复制推文链接', icon: 'link' },
      { key: 'open', label: '打开原推', icon: 'external' });
    return items;
  }
  function menuHtml() {
    if (!menu) return '';
    const p = ST.posts.find((x) => x.id === menu.id);
    const items = menuItems(p);
    if (S.device === 'mobile') {
      return `<div class="m-sheet-layer" role="presentation"><div class="m-dim" data-act="menu-close" aria-hidden="true"></div>
        <div class="m-sheet" role="menu" aria-label="操作"><div class="m-sheet-grab" aria-hidden="true"></div>
          <div class="m-sheet-obj">${esc(p.text.split('\n')[0].slice(0, 80))}</div>
          ${items.map((it) => it.type === 'sep' ? '<div class="m-sheet-sep" role="separator"></div>'
            : `<button type="button" role="menuitem" class="m-sheet-item" data-menu="${it.key}" ${it.disabled ? 'aria-disabled="true"' : ''}>${ic(it.icon)}<span>${it.label}</span></button>`).join('')}
        </div></div>`;
    }
    return `<div class="ctx-menu" role="menu" style="left:${menu.x}px;top:${menu.y}px">
      ${items.map((it) => it.type === 'sep' ? '<div class="ctx-sep" role="separator"></div>'
        : `<button type="button" role="menuitem" class="ctx-item" data-menu="${it.key}" ${it.disabled ? 'disabled aria-disabled="true"' : ''}>${ic(it.icon)}${it.label}</button>`).join('')}
    </div>`;
  }

  /* ── 外壳 ── */
  function desktopHtml() {
    const dark = document.documentElement.dataset.theme === 'dark';
    const rail = [['article', '文章', 'fileText'], ['podcast', '播客', 'podcast'], ['social', '社交', 'atSign']];
    const groups = ['官方', '个人'].map((role) => {
      const rows = ST.accounts.filter((a) => a.role === role)
        .map((a) => `<div class="reader-source-row" role="button" tabindex="0"><img class="reader-src-avatar" src="${a.avatar}" alt=""><p class="reader-source-name">${esc(a.name)}</p></div>`).join('');
      return `<section class="reader-subs"><div class="reader-src-label">${role}</div><div class="reader-group-body">${rows}</div></section>`;
    }).join('');
    return `<div class="st-desktop">
      <div class="reader-shell">
        <nav class="reader-vrail" aria-label="阅读视图">
          <img class="reader-vrail-brand-img" src="../../../frontend/public/brand/dorami-logo-128.png" width="32" height="32" alt="哆啦美">
          <button type="button" class="reader-vrail-btn" aria-label="我的早报">${ic('newspaper', 'h-[18px] w-[18px]')}<span class="reader-vrail-label">早报</span></button>
          <span class="reader-vrail-divider" aria-hidden="true"></span>
          ${rail.map(([id, label, icon]) => `<button type="button" class="reader-vrail-btn ${id === 'social' ? 'is-on' : ''}" aria-label="${label}" aria-pressed="${id === 'social'}">${ic(icon, 'h-[18px] w-[18px]')}<span class="reader-vrail-label">${label}</span></button>`).join('')}
          <span class="reader-vrail-divider" aria-hidden="true"></span>
          <button type="button" class="reader-vrail-btn" aria-label="发现">${ic('compass', 'h-[18px] w-[18px]')}<span class="reader-vrail-label">发现</span></button>
          <div class="reader-vrail-spring"></div>
          <div class="st-rail-foot">
            <button type="button" class="reader-vrail-btn" aria-label="切换主题">${ic(dark ? 'sun' : 'moon', 'h-[18px] w-[18px]')}<span class="reader-vrail-label">主题</span></button>
            <button type="button" class="reader-vrail-btn" aria-label="反馈与建议">${ic('message', 'h-[18px] w-[18px]')}<span class="reader-vrail-label">反馈</span></button>
            <button type="button" class="reader-vrail-btn" aria-label="设置">${ic('settings', 'h-[18px] w-[18px]')}<span class="reader-vrail-label">设置</span></button>
            <button type="button" class="reader-vrail-avatar st-rail-avatar" aria-label="退出登录">E</button>
          </div>
        </nav>
        <aside class="reader-col reader-col-sources"><div class="reader-sources-inner">
          <div class="reader-src-head"><span class="reader-src-title">社交媒体</span></div>
          <div class="reader-source-scroll">${groups}</div>
        </div></aside>
        <section class="reader-col reader-social" aria-label="社交媒体">
          <header class="reader-social-head">
            <span class="reader-social-title">社交媒体</span>
            <div class="reader-social-actions">
              ${headSeg()}
              <div class="reader-seg" role="group" aria-label="未读过滤"><button type="button" class="reader-seg-btn is-on" aria-pressed="true">全部</button><button type="button" class="reader-seg-btn" aria-pressed="false">未读</button></div>
              <button type="button" class="reader-unread-icon" aria-label="本容器全部标为已读">${ic('checkCheck', 'h-4 w-4')}</button>
              <button type="button" class="reader-unread-icon reader-fav-toggle" aria-label="只看收藏">${ic('star', 'h-4 w-4')}</button>
              <button type="button" class="reader-search-icon" aria-label="搜索">${ic('search', 'h-4 w-4')}</button>
            </div>
          </header>
          <div class="reader-scrollwrap"><div class="reader-social-scroll" data-slot="scroll"><div class="reader-social-col" data-slot="flow">${flowHtml()}</div></div></div>
        </section>
      </div>
      <div data-slot="menu">${menuHtml()}</div>
    </div>`;
  }

  function mobileHtml() {
    const tabs = [['article', '文章', 'fileText'], ['podcast', '播客', 'podcast'], ['social', '社交', 'atSign'], ['rankings', '榜单', 'barChart'], ['me', '我的', 'user']];
    const headBtn = S.entry === 'head' && aiOn()
      ? `<button type="button" class="m-iconbtn ${S.headOn ? 'is-ai' : ''}" aria-pressed="${S.headOn}" aria-label="${S.headOn ? '显示原语言' : '把推文译为中文'}" data-act="${S.headOn ? 'head-off' : 'head-on'}"><span class="reader-tr-glyph" aria-hidden="true">译</span></button>`
      : '';
    return `<div class="st-mobile">
      <div class="m-shell font-sans">
        <div class="m-topbar">
          <button type="button" class="m-iconbtn" aria-label="订阅源">${ic('menu')}</button>
          <span class="m-title">订阅</span>
          <div class="m-topbar-sp"></div>
          <div class="mini-seg" role="tablist" aria-label="未读筛选"><button type="button" role="tab" class="mini-seg-btn is-on" aria-selected="true">全部</button><button type="button" role="tab" class="mini-seg-btn" aria-selected="false">未读</button></div>
          ${headBtn}
          <button type="button" class="m-iconbtn" aria-label="只看收藏">${ic('star')}</button>
          <button type="button" class="m-iconbtn" aria-label="本容器全部标为已读">${ic('checkCheck')}</button>
          <button type="button" class="m-iconbtn" aria-label="搜索">${ic('search')}</button>
        </div>
        <div class="m-social-host">
          <section class="reader-col reader-social" aria-label="社交媒体">
            <div class="reader-scrollwrap"><div class="reader-social-scroll" data-slot="scroll"><div class="reader-social-col" data-slot="flow">${flowHtml()}</div></div></div>
          </section>
        </div>
        <nav class="m-tabbar" aria-label="阅读视图">
          ${tabs.map(([id, label, icon]) => `<button type="button" class="m-tab ${id === 'social' ? 'is-on' : ''}" aria-label="${label}"${id === 'social' ? ' aria-current="page"' : ''}><span class="m-tab-ic">${ic(icon)}</span><span class="m-tab-label">${label}</span></button>`).join('')}
        </nav>
        <div data-slot="menu">${menuHtml()}</div>
      </div>
    </div>`;
  }

  /* ── 渲染与交互 ── */
  const root = document.getElementById('root');
  function render() {
    root.innerHTML = S.device === 'mobile' ? mobileHtml() : desktopHtml();
  }
  function refresh() {
    const scroll = root.querySelector('[data-slot="scroll"]');
    const top = scroll ? scroll.scrollTop : 0;
    render();
    const next = root.querySelector('[data-slot="scroll"]');
    if (next) next.scrollTop = top;
  }

  function startTr(id) {
    const st = T[id];
    if (st.cached) { st.tr = 'done'; refresh(); return; }
    st.tr = 'loading';
    refresh();
    setTimeout(() => {
      if (T[id].tr !== 'loading') return;
      T[id].tr = 'done';
      T[id].cached = true;
      refresh();
    }, LATENCY);
  }
  function stopTr(id) { T[id].tr = 'idle'; refresh(); }

  function setHead(on) {
    S.headOn = on;
    const ids = ST.posts.filter(canTr).map((p) => p.id);
    if (!on) { ids.forEach((id) => { T[id].tr = 'idle'; }); refresh(); return; }
    // 开着时逐条请求(进入视口才发,这里按顺序模拟);命中缓存的立即显示
    ids.forEach((id, i) => {
      if (T[id].cached) { T[id].tr = 'done'; return; }
      T[id].tr = 'loading';
      setTimeout(() => {
        if (!S.headOn || T[id].tr !== 'loading') return;
        T[id].tr = 'done';
        T[id].cached = true;
        refresh();
      }, LATENCY + i * 350);
    });
    refresh();
  }

  root.addEventListener('click', (e) => {
    const menuBtn = e.target.closest('[data-menu]');
    if (menuBtn) {
      const id = menu.id;
      const key = menuBtn.dataset.menu;
      if (menuBtn.getAttribute('aria-disabled') === 'true') return;
      menu = null;
      if (key === 'tr') { startTr(id); return; }
      if (key === 'untr') { stopTr(id); return; }
      if (key === 'read') T[id].unread = !T[id].unread;
      if (key === 'fav') T[id].fav = !T[id].fav;
      refresh();
      return;
    }
    const el = e.target.closest('[data-act]');
    if (menu && !e.target.closest('.ctx-menu, .m-sheet')) { menu = null; refresh(); if (!el || el.dataset.act === 'menu-close') return; }
    if (!el) return;
    const act = el.dataset.act;
    const post = el.closest('.social-post');
    const id = post?.dataset.id;
    if (act === 'noop') { e.preventDefault(); return; }
    if (act === 'more') { T[id].expanded = !T[id].expanded; refresh(); return; }
    if (act === 'tr') { startTr(id); return; }
    if (act === 'untr') { stopTr(id); return; }
    if (act === 'act-tr') { if (T[id].tr === 'done') stopTr(id); else startTr(id); return; }
    if (act === 'fav') { T[id].fav = !T[id].fav; refresh(); return; }
    if (act === 'read') { T[id].unread = !T[id].unread; refresh(); return; }
    if (act === 'head-on') { if (!S.headOn) setHead(true); return; }
    if (act === 'head-off') { if (S.headOn) setHead(false); return; }
  });

  // 右键(桌面)/ 长按(手机,浏览器把长按派发为 contextmenu)
  root.addEventListener('contextmenu', (e) => {
    const post = e.target.closest('.social-post');
    if (!post) return;
    e.preventDefault();
    menu = { id: post.dataset.id, x: Math.min(e.clientX, window.innerWidth - 200), y: Math.min(e.clientY, window.innerHeight - 230) };
    refresh();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && menu) { menu = null; refresh(); } });

  render();
  // 「菜单」状态:直接展开第一条可译推文的菜单
  if (S.state === 'menu') {
    const first = ST.posts.find(canTr);
    const card = root.querySelector(`.social-post[data-id="${first.id}"]`);
    const box = card.getBoundingClientRect();
    menu = { id: first.id, x: Math.round(box.left + box.width * 0.42), y: Math.round(box.top + 64) };
    refresh();
  }
  window.STF = { S, T, refresh };   // 截图 / 自查用
})();
