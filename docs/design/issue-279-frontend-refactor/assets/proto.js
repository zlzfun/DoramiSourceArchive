/* 哆啦美 · 前端重构样页（2026-11，第二轮：只留 B「夜航 · 续」）——渲染与交互。
   数据：assets/data.js（生产游客接口只读快照，见 tools/build_data.py）；不调用任何接口。 */
(() => {
  const D = window.D;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const NOW = new Date(D.now).getTime();
  const TZ = 8 * 3600e3; // 样页固定按东八区排日期，截图不随机器时区变

  const state = {
    theme: 'light', palette: 'porcelain', fold: false, rail: 'on', weight: 'on',
    view: 'brief', shape: 'article', scope: 'sub', sel: null, src: null, kind: 'all', unread: false, favOnly: false, from: null, preview: false,
    rankTab: 'events', rankWin: 'week', event: null,
    discShape: 'article', discTab: 'sources', q: '',
    read: new Set(), fav: new Set(), podTab: 'guide',
    srcPop: false, cal: false, menu: false, why: false, search: false, sq: '', qa: false, qaAsked: false, playing: null, paused: false,
  };

  /* ── 时间：列表里 7 天内写相对时间，署名行写「11月10日 07:14」，全站不出现 2026-11-10 这种写法 ── */
  const local = (iso) => new Date(new Date(iso).getTime() + TZ);
  const WK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const pad = (n) => String(n).padStart(2, '0');
  function rel(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    const m = Math.max(0, Math.round((NOW - t) / 60e3));
    if (m < 1) return '刚刚';
    if (m < 60) return `${m} 分钟前`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h} 小时前`;
    const d = Math.round(h / 24);
    if (d <= 7) return `${d} 天前`;
    const l = local(iso);
    return `${l.getUTCMonth() + 1}月${l.getUTCDate()}日`;
  }
  const dayKey = (iso) => { const l = local(iso); return `${l.getUTCFullYear()}-${pad(l.getUTCMonth() + 1)}-${pad(l.getUTCDate())}`; };
  function dayLabel(iso) {
    const l = local(iso); const today = dayKey(D.now);
    const y = dayKey(new Date(NOW - 864e5).toISOString());
    const md = `${l.getUTCMonth() + 1}月${l.getUTCDate()}日`;
    const k = dayKey(iso);
    if (k === today) return `今天 · ${md}`;
    if (k === y) return `昨天 · ${md}`;
    return `${md} ${WK[l.getUTCDay()]}`;
  }
  const clock = (iso) => { const l = local(iso); return `${pad(l.getUTCHours())}:${pad(l.getUTCMinutes())}`; };
  const fullDate = (iso) => { const l = local(iso); return `${l.getUTCMonth() + 1}月${l.getUTCDate()}日 ${clock(iso)}`; };
  const mins = (sec) => (sec ? `${Math.round(sec / 60)} 分钟` : '');
  const hms = (sec) => { const s = Math.max(0, Math.round(sec)); return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`; };

  /* ── 排印 ──
     标题按词断行：SEG 是数据打包时用分词器预先算好的「短语块」，块之间放 <wbr>，配合
     word-break: keep-all，行只在词与词之间折。大字标题另做标点挤压（句读后半个字宽收掉）。 */
  const LATIN = /[A-Za-z]/g; const CJK = /[一-鿿]/g;
  const isLatin = (s) => { const a = (s.match(LATIN) || []).length; const c = (s.match(CJK) || []).length; return a > 0 && c * 2 < a; };
  const isCjk = (ch) => ch >= '一' && ch <= '鿿';
  const isAlnum = (ch) => /[A-Za-z0-9]/.test(ch);
  function squeeze(html) {
    return html.replace(/[，。、；：！？]/g, (c) => `<span class="pz">${c}</span>`).replace(/[「『（《“‘]/g, (c) => `<span class="pzh">${c}</span>`);
  }
  function T(text, opt = {}) {
    const s = String(text || '');
    if (isLatin(s)) return esc(s);
    const chunks = (D.seg && D.seg[s]) || null;
    let html = esc(s);
    if (chunks) {
      html = '';
      chunks.forEach((c, i) => {
        if (i) {
          const a = chunks[i - 1].slice(-1); const b = c[0];
          const mixed = (isCjk(a) && isAlnum(b)) || (isAlnum(a) && isCjk(b));
          html += mixed ? '<i class="as"></i><wbr>' : '<wbr>';
        }
        html += esc(c);
      });
    }
    if (opt.display) html = squeeze(html);
    return html;
  }
  const langOf = (s) => (isLatin(String(s || '')) ? ' lang="en"' : '');
  const titleOf = (a) => a.zh || a.title;
  const origOf = (a) => (a.zh && a.title && a.zh !== a.title && isLatin(a.title) ? a.title : '');

  /* ── 图标（24 网格；尺寸只取 14 / 16 / 18 三档，线宽 1.75；形制取自 Lucide，ISC）── */
  const P = {
    brief: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8M15 18h-5M10 6h8v4h-8z"/>',
    tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
    article: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M10 9H8M16 13H8M16 17H8"/>',
    podcast: '<path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0z"/>',
    social: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
    rank: '<path d="M3 3v16a2 2 0 0 0 2 2h16M13 17V9M18 17V5M8 17v-3"/>',
    discover: '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-1.8 5.4a2 2 0 0 1-1.27 1.27l-5.4 1.8 1.8-5.4a2 2 0 0 1 1.27-1.27z"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    feedback: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    star: '<path d="M11.53 2.3a.53.53 0 0 1 .95 0l2.3 4.68a2.12 2.12 0 0 0 1.6 1.16l5.16.75a.53.53 0 0 1 .3.9l-3.74 3.64a2.12 2.12 0 0 0-.61 1.88l.88 5.14a.53.53 0 0 1-.77.56l-4.62-2.43a2.12 2.12 0 0 0-1.97 0L6.4 21.01a.53.53 0 0 1-.77-.56l.88-5.14a2.12 2.12 0 0 0-.61-1.88L2.16 9.8a.53.53 0 0 1 .3-.91l5.16-.75a2.12 2.12 0 0 0 1.6-1.16z"/>',
    checks: '<path d="M18 6 7 17l-5-5M22 10l-7.5 7.5L13 16"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.59 13.51 6.83 3.98M15.41 6.51l-6.82 3.98"/>',
    ext: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    down: '<path d="m6 9 6 6 6-6"/>', left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>',
    arrow: '<path d="M5 12h14M12 5l7 7-7 7"/>', arrowl: '<path d="M19 12H5M12 19l-7-7 7-7"/>', up: '<path d="M12 19V5M5 12l7-7 7 7"/>', dn: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    play: '<path d="M6 4.5v15a1 1 0 0 0 1.5.86l13-7.5a1 1 0 0 0 0-1.72l-13-7.5A1 1 0 0 0 6 4.5Z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/>',
    back15: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    fwd15: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    spark: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z" fill="currentColor" stroke="none"/>',
    cal: '<path d="M8 2v4M16 2v4M3 10h18"/><rect x="3" y="4" width="18" height="18" rx="2"/>',
    plus: '<path d="M5 12h14M12 5v14"/>', check: '<path d="M20 6 9 17l-5-5"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>',
    fold: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18M16 15l-3-3 3-3"/>',
    unfold: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18M14 9l3 3-3 3"/>',
    rss: '<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    head: '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>',
    book: '<path d="M12 7v14M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14M7 22l-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    translate: '<path d="m5 8 6 6M4 14l6-6 2-3M2 5h12M7 2h1M22 22l-5-10-5 10M14 18h6"/>',
    unread: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  };
  const I = (n, s = 16) => `<svg class="ic" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n] || ''}</svg>`;

  /* ── 小件 ── */
  const S = (id) => D.sources[id] || { name: id, role: 'media' };
  function logo(id, size = 16, name) {
    const s = D.sources[id];
    const inner = s && s.logo ? `<img src="${s.logo}" alt="" loading="lazy">` : `<b>${esc(((s && s.name) || name || '?').slice(0, 1))}</b>`;
    return `<span class="lg" style="--s:${size}px">${inner}</span>`;
  }
  const tier = (v) => (v == null ? -1 : v >= 9 ? 4 : v >= 8 ? 3 : v >= 7 ? 2 : v >= 6 ? 1 : 0);
  const sc = (v) => (v == null ? '' : `<span class="sc t${tier(v)}">${Number(v).toFixed(1)}</span>`);
  const dot = '<i class="dotsep">·</i>';
  const DIM = { news: '新闻价值', content: '内容价值' };
  const ROLE = { official: '官方', media: '媒体', personal: '个人', leaderboard: '榜单', custom: '自定源' };
  const ROLE_ORDER = ['official', 'media', 'personal', 'leaderboard', 'custom'];
  const pool = () => (state.shape === 'podcast' ? D.pods : D.articles);

  /* ── 外壳：左导轨（标签常驻）+ 工作区；问哆啦美从右下浮钮改进导轨 ── */
  const NAV = [['brief', '早报', 'brief'], null, ['article', '文章', 'article'], ['podcast', '播客', 'podcast'], ['social', '社交', 'social'], null, ['rank', '榜单', 'rank'], ['discover', '发现', 'discover']];
  const navKey = () => (state.view === 'feed' ? state.shape : state.view);
  function shell() {
    const k = navKey();
    const nav = NAV.map((n) => (n ? `<a href="#" data-nav="${n[0]}" class="${k === n[0] ? 'on' : ''}">${I(n[2], 18)}<span>${n[1]}</span></a>` : '<i class="rail-sep"></i>')).join('');
    return `
<nav class="rail" aria-label="阅读视图">
  <a class="rail-logo" href="#" data-nav="brief"><img src="../../../frontend/public/brand/dorami-logo-128.png" alt="哆啦美"></a>
  <div class="rail-nav">${nav}</div>
  <div class="rail-tools">
    <a href="#" data-act="qa" class="${state.qa ? 'on' : ''}"><span class="spark">${I('spark', 18)}</span><span>问答</span></a>
    <i class="rail-sep"></i>
    <a href="#" data-act="theme">${I(state.theme === 'dark' ? 'sun' : 'moon', 18)}<span>主题</span></a>
    <a href="#" data-act="noop" data-msg="反馈与建议：这块本轮没有重画">${I('feedback', 18)}<span>反馈</span></a>
    <a href="#" data-act="noop" data-msg="设置柜本轮没有重画，形制沿用现网">${I('settings', 18)}<span>设置</span></a>
    <span class="avatar">读</span>
  </div>
</nav>
<main class="stage"><div class="stage-b" id="stage"></div><div id="dock"></div><div id="over"></div></main>`;
  }

  /* ── 早报 ── */
  const SECTION_ORDER = ['重大事件', '模型发布', '行业资讯', '学术论文', '深度阅读', '播客精选'];
  function briefItem(it) {
    const o = it.zh && isLatin(it.title) ? it.title : '';
    const t = it.zh || it.title;
    return `
<article class="bi" data-open="${it.id}" data-from="brief" tabindex="0">
  <h3 class="kw"${langOf(t)}>${T(t)}</h3>
  ${o ? `<p class="orig" lang="en">${esc(o)}</p>` : ''}
  <p class="bi-sum">${esc(it.sum)}</p>
  <div class="bi-meta">${logo(it.src, 16, it.srcName)}<span>${esc(it.srcName)}</span>${dot}<span>${rel(it.at)}</span>${it.related.length ? `${dot}<span>另见 ${it.related.length} 篇</span>` : ''}${sc(it.score)}<button class="bi-fav ${state.fav.has(it.id) ? 'faved' : ''}" data-fav="${it.id}" aria-label="收藏">${I('star', 14)}</button></div>
</article>`;
  }
  function calendar() {
    const l = local(D.now); const y = l.getUTCFullYear(); const m = l.getUTCMonth(); const today = l.getUTCDate();
    const first = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7; const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    let g = ['一', '二', '三', '四', '五', '六', '日'].map((d) => `<span>${d}</span>`).join('') + '<i></i>'.repeat(first).replace(/<i><\/i>/g, '<i style="visibility:hidden"></i>');
    for (let d = 1; d <= days; d++) g += d <= today ? `<button class="${d === today ? 'on today' : ''}" data-act="calday">${d}</button>` : `<i>${d}</i>`;
    return `<div class="pop cal" data-keep><div class="cal-h"><b>${y} 年 ${m + 1} 月</b><span><button class="tool" aria-label="上个月">${I('left')}</button><button class="tool" aria-label="下个月" disabled style="opacity:.35">${I('right')}</button></span></div><div class="cal-g">${g}</div></div>`;
  }
  function renderBrief() {
    const b = D.brief; const head = b.items[0]; const rest = b.items.slice(1);
    const l = local(b.date + 'T04:00:00Z');
    const groups = SECTION_ORDER.map((name) => [name, rest.filter((i) => i.section === name)]).filter((g) => g[1].length);
    const ht = head.zh || head.title; const ho = head.zh && isLatin(head.title) ? head.title : '';
    return `
<div class="page brief" data-scroll>
  <div class="wrap">
    <header class="bf-top">
      <div class="bf-id"><h1>早报</h1><i></i><span class="bf-date">${l.getUTCMonth() + 1}月${l.getUTCDate()}日 ${WK[l.getUTCDay()]}${dot}今天</span></div>
      <div class="bf-ed"><span>${clock(b.gen)} 编排</span><button class="btn" data-act="cal" aria-expanded="${state.cal}">${I('cal', 14)}往期${I('down', 14)}</button>${state.cal ? calendar() : ''}</div>
    </header>
    <section class="hero" data-open="${head.id}" data-from="brief">
      <div class="hero-glow" aria-hidden="true"></div>
      <div class="hero-main">
        <div class="kick"><span>${esc(head.section)}</span>${head.related.length ? `${dot}<span>${head.related.length + 1} 家来源</span>` : ''}</div>
        <h2 class="hero-title kw bal"${langOf(ht)}>${T(ht, { display: true })}</h2>
        ${ho ? `<p class="orig hero-orig" lang="en">${esc(ho)}</p>` : ''}
        <div class="hero-by">${logo(head.src, 18, head.srcName)}<span>${esc(head.srcName)}</span>${dot}<span>${rel(head.at)}</span></div>
        <p class="hero-sum">${esc(head.sum)}</p>
        <div class="hero-act"><button class="btn lg primary">阅读全文${I('arrow')}</button><button class="btn lg ${state.fav.has(head.id) ? 'faved' : ''}" data-fav="${head.id}">${I('star')}收藏</button></div>
      </div>
      <aside class="hero-side">
        <div class="lab">${DIM[head.dim] || '新闻价值'}</div>
        <div class="hero-score">${sc(head.score)}</div>
        <p class="hero-why">${esc(head.why)}</p>
        ${head.related.length ? `<div class="lab lab-gap">同一事件</div><ul class="also-list">${head.related.slice(0, 4).map((r) => `<li>${logo(r.src, 16, r.name)}<div><b>${esc(r.name)}</b><span${langOf(r.title)}>${esc(r.title)}</span></div></li>`).join('')}</ul>` : ''}
      </aside>
    </section>
    <nav class="toc">${groups.map((g) => `<a href="#" data-toc="${esc(g[0])}">${esc(g[0])}<b>${g[1].length}</b></a>`).join('')}</nav>
    ${groups.map(([name, items]) => `
    <section class="sec" data-sec="${esc(name)}">
      <header class="sec-h"><h2>${esc(name)}</h2></header>
      <div class="sec-b">${items.map(briefItem).join('')}</div>
    </section>`).join('')}
  </div>
</div>`;
  }

  /* ── 列表 + 阅读窗（文章 / 播客）── */
  function feedItems() {
    let items = pool();
    if (state.src) items = items.filter((a) => a.src === state.src);
    if (state.kind !== 'all') items = items.filter((a) => a.kind === state.kind);
    if (state.unread) items = items.filter((a) => !state.read.has(a.id) || a.id === state.sel);
    if (state.favOnly) items = items.filter((a) => state.fav.has(a.id));
    const q = state.sq.trim().toLowerCase();
    if (state.search && q) items = items.filter((a) => `${a.zh || ''}${a.title}${a.sum || ''}`.toLowerCase().includes(q));
    return items;
  }
  function row(a) {
    const t = titleOf(a); const pod = state.shape === 'podcast';
    const cls = ['row', state.sel === a.id ? 'on' : '', state.read.has(a.id) ? 'read' : '', !pod && a.score != null && a.score < 6 ? 'low' : '', !pod && a.score != null && a.score >= 6 && a.score < 8 ? 'mid' : '', pod ? 'pod' : ''].join(' ');
    const s = S(a.src);
    const kind = a.kindl && a.kind !== 'news' ? `${dot}<span>${esc(a.kindl)}</span>` : '';
    const podBits = pod ? `<div class="row-pod">${a.dur ? `<span>${mins(a.dur)}</span>` : ''}${a.premium ? '<span class="badge">优质播客</span>' : ''}${a.ready ? `<span class="hint">${I('book', 14)}中文导读</span>` : ''}</div>` : '';
    return `
<article class="${cls}" data-id="${a.id}" tabindex="0">
  ${pod ? `<span class="cover">${D.covers[a.src] ? `<img src="${D.covers[a.src]}" alt="">` : ''}</span>` : ''}
  <div class="row-meta">${pod ? '' : logo(a.src, 14)}<span class="row-src">${esc(pod ? a.show || s.name : s.name)}</span>${kind}${dot}<span>${rel(a.at)}</span></div>
  ${sc(a.score)}
  <h3${langOf(t)}>${T(t)}</h3>
  ${pod || !a.sum ? '' : `<p class="row-sum fade">${esc(a.sum)}</p>`}
  ${podBits}
</article>`;
  }
  function srcGroups(shape) {
    const ids = D.subscribed.filter((id) => D.sources[id] && D.sources[id].shape === shape);
    const present = new Set((shape === 'podcast' ? D.pods : D.articles).map((a) => a.src));
    const all = shape === 'podcast' ? [...present].filter((id) => D.sources[id]) : ids;
    return ROLE_ORDER.map((r) => [r, all.filter((id) => D.sources[id].role === r)]).filter((g) => g[1].length);
  }
  const hasUnread = (id) => pool().some((a) => a.src === id && !state.read.has(a.id));
  function srcList(shape) {
    return `
<button class="src ${state.src ? '' : 'on'}" data-src="">${I(shape === 'podcast' ? 'podcast' : 'article')}<span>全部订阅</span></button>
${srcGroups(shape).map(([r, ids]) => `<div class="src-g">${ROLE[r]}</div>${ids.map((id) => `<button class="src ${state.src === id ? 'on' : ''} ${hasUnread(id) ? 'unread' : ''}" data-src="${id}">${logo(id, 18)}<span>${esc(D.sources[id].name)}</span></button>`).join('')}`).join('')}
<button class="src more" data-nav="discover">${I('discover')}<span>发现更多来源</span></button>`;
  }
  const axisSeg = (interest) => `<div class="seg mini" role="radiogroup" aria-label="按什么切分"><button class="${interest ? '' : 'on'}" data-scope="sub">订阅</button><button class="${interest ? 'on' : ''}" data-scope="interest">兴趣</button></div>`;
  function renderFeed() {
    const pod = state.shape === 'podcast';
    const interest = state.scope === 'interest';
    const items = feedItems();
    let last = ''; let rows = '';
    for (const a of items) {
      const k = dayKey(a.at);
      if (k !== last) { rows += `<div class="day">${dayLabel(a.at)}</div>`; last = k; }
      rows += row(a);
    }
    const scope = state.src ? S(state.src).name : '全部订阅';
    const kinds = pod ? '' : `<div class="kinds">${[['all', '全部类型'], ...D.facets.filter((f) => f.kind !== 'news').map((f) => [f.kind, f.label])].map(([k, l]) => `<button class="${state.kind === k ? 'on' : ''}" data-kind="${k}">${esc(l)}</button>`).join('')}</div>`;
    const emptyMsg = state.search && state.sq ? [`没有找到「${esc(state.sq)}」`, ''] : state.favOnly ? ['还没有收藏', ''] : state.unread ? ['都读完了', '<button class="btn" data-unread="0">显示全部</button>'] : state.kind !== 'all' ? ['这个类型下还没有内容', '<button class="btn" data-kind="all">显示全部类型</button>'] : ['这里还没有内容', ''];
    const head = state.search
      ? `<div class="list-find"><label class="find">${I('search')}<input placeholder="搜索我的阅读…" value="${esc(state.sq)}" data-sq autofocus></label><button class="tool" data-act="search" aria-label="关闭搜索">${I('x', 18)}</button></div>`
      : `<button class="scope" data-act="srcpop" aria-expanded="${state.srcPop}"><span>${esc(scope)}</span>${I('down')}</button>
         <div class="list-tools">
           <div class="seg mini"><button class="${state.unread ? '' : 'on'}" data-unread="0">全部</button><button class="${state.unread ? 'on' : ''}" data-unread="1">未读</button></div>
           <button class="tool ${state.favOnly ? 'on' : ''}" data-act="favonly" data-tip="只看收藏">${I('star', 18)}</button><button class="tool" data-act="markall" data-tip="全部标为已读">${I('checks', 18)}</button><button class="tool" data-act="search" data-tip="搜索" data-tip-side="left">${I('search', 18)}</button>
         </div>`;
    const main = interest ? `
  <section class="page" style="grid-column: 2 / -1" data-scroll><div class="wrap" style="max-width:920px">
    <header class="pg-h"><h1>我的兴趣</h1></header>
    <div class="note">这一页要用登录读者自己的兴趣数据。样页用的是游客快照，游客在现网点「兴趣」会被直接带去登录，所以这里没有画。落地时它沿用同一个版框与页头，报头写法不变。</div>
  </div></section>` : `
  <section class="list">
    <header class="col-h">${head}${state.srcPop ? `<div class="pop srcpop" data-keep><div class="pop-h"><b>${pod ? '播客' : '文章'}</b>${axisSeg(interest)}</div>${srcList(state.shape)}<div class="pop-f"><button data-act="fold">${I('unfold')}固定为来源栏</button></div></div>` : ''}</header>
    ${state.preview && state.src ? `<div class="list-sub"><button class="btn sm" data-sub="${state.src}">${I('plus', 14)}订阅</button></div>` : kinds}
    <div class="list-b" data-scroll>${rows || `<div class="empty"><b>${emptyMsg[0]}</b>${emptyMsg[1]}</div>`}</div>
  </section>
  <section class="pane" id="pane" data-scroll>${renderPane()}</section>`;
    return `
<div class="feed ${state.fold && !interest ? 'fold' : ''}">
  <aside class="srcbar"><div class="srcbar-in">
    <header class="col-h"><h2>${pod ? '播客' : '文章'}</h2><div class="list-tools">${axisSeg(interest)}<button class="tool" data-act="fold" data-tip="收起来源栏" data-tip-side="left">${I('fold', 18)}</button></div></header>
    <div class="srcbar-b">${interest ? '<div class="note" style="margin:8px 2px">兴趣主题列表（未画）</div>' : srcList(state.shape)}</div>
  </div></aside>${main}
</div>`;
  }

  /* 极简 Markdown：样页只需要标题 / 段落 / 粗斜体 / 链接 / 图片 / 引用 / 列表 / 代码。 */
  function inline(s) {
    return esc(s)
      .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img src="$2" alt="$1" loading="lazy">')
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
      .replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em style="font-style:italic">$2</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }
  function md(src) {
    const out = []; const lines = String(src || '').replace(/\r/g, '').split('\n');
    let i = 0; let para = [];
    const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(''))}</p>`); para = []; } };
    while (i < lines.length) {
      const ln = lines[i];
      if (/^```/.test(ln)) { flush(); const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`); continue; }
      if (!ln.trim()) { flush(); i++; continue; }
      let m;
      if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) { flush(); const lv = Math.min(4, Math.max(2, m[1].length + 1)); out.push(`<h${lv}${langOf(m[2])}>${inline(m[2])}</h${lv}>`); i++; continue; }
      if (/^>\s?/.test(ln)) { flush(); const buf = []; while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, '')); out.push(`<blockquote>${buf.filter(Boolean).map((b) => `<p>${inline(b)}</p>`).join('')}</blockquote>`); continue; }
      if (/^\s*([-*]|\d+\.)\s+/.test(ln)) { flush(); const ord = /^\s*\d+\./.test(ln); const buf = []; while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, '')); out.push(`<${ord ? 'ol' : 'ul'}>${buf.map((b) => `<li>${inline(b)}</li>`).join('')}</${ord ? 'ol' : 'ul'}>`); continue; }
      if (/^-{3,}$/.test(ln.trim())) { flush(); out.push('<hr>'); i++; continue; }
      if (/^!\[/.test(ln.trim())) { flush(); out.push(`<figure>${inline(ln.trim())}</figure>`); i++; continue; }
      para.push(ln.trim()); i++;
    }
    flush();
    return out.join('\n');
  }
  /* 速读正文：一句一段。只按句号切，不改一个字——五句话各讲一件事，排成一堵墙就没人读第三句。 */
  function sentences(text) {
    const parts = String(text || '').match(/[^。！？]+[。！？]+[”」』）]?|[^。！？]+$/g) || [String(text || '')];
    const out = [];
    for (const p of parts) { if (out.length && (p.trim().length < 12 || out[out.length - 1].length < 16)) out[out.length - 1] += p; else out.push(p); }
    return out.map((p) => `<p>${esc(p.trim())}</p>`).join('');
  }

  /* 页边：关于这篇的话（两个维度的分与依据、标签、同一事件）。窗口窄时它收成速读卡的卡脚。 */
  function scoreParts(a) {
    const first = a.dim === 'content' ? ['content', a.cv, a.cvr] : ['news', a.nv, a.nvr];
    const second = a.dim === 'content' ? ['news', a.nv, a.nvr] : ['content', a.cv, a.cvr];
    const ev = D.brief.items.find((b) => b.id === a.id && b.related.length);
    const tags = a.tags && a.tags.length ? `<div class="tags">${a.tags.map((t) => `<a href="#" class="${t.c === 'canonical' ? '' : 'soft'}" data-act="noop" data-msg="按标签检索：沿用现网，样页未接">${esc(t.l)}</a>`).join('')}</div>` : '';
    const evLink = ev ? `<a href="#" class="side-link" data-event="${ev.event}">同一事件${dot}${ev.related.length + 1} 家来源${I('right', 14)}</a>` : '';
    return { first, second, tags, evLink };
  }
  function paneSide(a) {
    const { first, second, tags, evLink } = scoreParts(a);
    if (first[1] == null && !tags) return '';
    return `
<aside class="side">
  ${first[1] != null ? `<div class="side-blk"><div class="lab">${DIM[first[0]]}</div><div class="side-score">${sc(first[1])}</div><p>${esc(first[2] || '')}</p></div>` : ''}
  ${second[1] != null ? `<div class="side-blk minor"><div class="lab">${DIM[second[0]]}${sc(second[1])}</div><p>${esc(second[2] || '')}</p></div>` : ''}
  ${tags ? `<div class="side-blk">${tags}</div>` : ''}
  ${evLink ? `<div class="side-blk">${evLink}</div>` : ''}
</aside>`;
  }
  /* AI 速读卡。窗口够宽：卡里只有速读，分数 / 依据 / 标签在页边。窗口不够：它们收进卡头与卡脚。 */
  function aiCard(a, pod) {
    const { first, second, tags, evLink } = scoreParts(a);
    const has = first[1] != null;
    const scores = has ? `<button class="ai-scores" data-act="why" aria-expanded="${state.why}"><span>${DIM[first[0]]}</span>${sc(first[1])}${second[1] != null ? `<span>${DIM[second[0]]}</span>${sc(second[1])}` : ''}${I('down', 14)}</button>` : '';
    const why = has && state.why ? `<div class="ai-why"><div><div class="lab">${DIM[first[0]]}</div><p>${esc(first[2] || '')}</p></div>${second[1] != null ? `<div><div class="lab">${DIM[second[0]]}</div><p>${esc(second[2] || '')}</p></div>` : ''}</div>` : '';
    const foot = tags || evLink ? `<footer class="ai-foot">${tags}${evLink ? `<span class="grow"></span>${evLink}` : ''}</footer>` : '';
    return `
<section class="ai">
  <header><span class="ai-h"><span class="spark">${I('spark', 14)}</span>AI 速读</span><span class="grow"></span>${scores}${pod ? '' : `<button class="btn sm" data-act="noop" data-msg="听导读会调用模型，样页不调">${I('head', 14)}听导读</button>`}</header>
  ${why}
  <div class="ai-body">${a.sum ? sentences(a.sum) : `<p>${esc(first[2] || '')}</p>`}</div>
  ${foot}
</section>`;
  }
  function renderPane() {
    const id = state.sel;
    if (!id) return '';
    const list = feedItems(); const idx = list.findIndex((x) => x.id === id);
    const base = pool().find((x) => x.id === id) || {};
    const a = { ...base, ...(D.details[id] || {}) };
    const s = S(a.src); const t = titleOf(a); const o = origOf(a);
    const back = state.from === 'brief' ? '早报' : state.from === 'event' ? '事件' : state.from === 'rank' ? '榜单' : '';
    /* 来源名一屏只写一次：吸顶栏有来源时，眉题只写类别；吸顶栏被「返回」占用时，眉题才带来源 */
    const kick = [back ? (a.pod ? a.pod.show || s.name : s.name) : '', a.kind !== 'news' ? a.kindl : [a.ev, a.genre].find((x) => x && x !== '其他')].filter(Boolean).join(' · ');
    const readMin = a.chars ? Math.max(1, Math.round(a.chars / 450)) : 0;
    const pod = a.pod; const foreign = pod ? state.podTab === 'notes' : isLatin(a.title || '');
    const body = pod
      ? `<div class="tabs" style="border-bottom:1px solid var(--dorami-border-soft);margin-bottom:24px"><button class="${state.podTab === 'guide' ? 'on' : ''}" data-podtab="guide"><span class="spark">${I('spark', 14)}</span>导读稿</button><button class="${state.podTab === 'notes' ? 'on' : ''}" data-podtab="notes">节目简介</button></div>
         <div class="prose"${state.podTab === 'notes' ? ' lang="en"' : ''}>${md(state.podTab === 'guide' ? D.podText.digest_blog_zh || '' : a.md)}</div>`
      : a.md
        ? `<div class="prose"${langOf(a.md.slice(0, 200))}>${md(a.md)}</div>`
        : '<p class="note">这篇的正文没有随样页打包，列表里靠前的 20 余篇与早报 12 篇可以点开看全文排版。</p>';
    const prev = idx > 0 ? list[idx - 1] : null; const next = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : null;
    const faved = state.fav.has(id);
    return `
<div class="pane-bar">
  ${back ? `<button class="back" data-act="back">${I('left')}返回${back}</button>` : `<span class="pane-src">${logo(a.src, 16)}<span>${esc(s.name)}</span></span>`}
  <span class="grow"></span>
  ${foreign ? `<div class="seg mini"><button class="on">原文</button><button data-act="noop" data-msg="译文沿用现网的翻译，样页没有带">译文</button></div>` : ''}
  <a class="pane-link" href="${esc(a.url)}" target="_blank" rel="noreferrer">查看原文${I('ext', 14)}</a>
  <button class="tool ${faved ? 'faved' : ''}" data-fav="${id}" data-tip="${faved ? '取消收藏' : '收藏'}">${I('star', 18)}</button><button class="tool" data-act="unreadone" data-tip="标为未读">${I('unread', 18)}</button><button class="tool ${state.menu ? 'on' : ''}" data-act="menu" data-tip="分享" data-tip-side="left">${I('share', 18)}</button>
  ${state.menu ? `<div class="pop menu" data-keep><button data-act="noop" data-msg="链接已复制（示意）">${I('link')}复制站内链接</button></div>` : ''}
</div>
<article class="doc ${pod ? 'is-pod' : ''}">
  <header class="doc-h">
    ${pod && D.covers[a.src] ? `<img class="doc-cover" src="${D.covers[a.src]}" alt="">` : ''}
    ${kick || (pod && pod.premium) ? `<div class="kick">${kick ? `<span>${esc(kick)}</span>` : ''}${pod && pod.premium ? '<span class="badge">优质播客</span>' : ''}</div>` : ''}
    <h1 class="kw bal"${langOf(t)}>${T(t, { display: true })}</h1>
    ${o ? `<p class="orig" lang="en">${esc(o)}</p>` : ''}
    <div class="doc-by"><span>${fullDate(a.at)}</span>${readMin && !pod ? `${dot}<span>阅读约 ${readMin} 分钟</span>` : ''}${a.reads ? `${dot}<span>阅读量 ${a.reads}</span>` : ''}</div>
    ${pod ? `<div class="doc-act"><button class="btn primary" data-act="play">${I(state.playing === id && !state.paused ? 'pause' : 'play', 14)}${state.playing === id && !state.paused ? '正在播放' : '收听原节目'}<em>${mins(pod.dur)}</em></button><button class="btn" data-act="noop" data-msg="生成导读音频会调用模型，样页不调"><span class="spark">${I('spark', 14)}</span>生成导读音频</button></div>` : ''}
  </header>
  ${a.sum || a.nv != null || a.cv != null ? aiCard(a, pod) : ''}
  ${paneSide(a)}
  ${body}
  <footer class="doc-end">
    <p class="doc-origin"><a href="${esc(a.url)}" target="_blank" rel="noreferrer">查看原文${I('ext', 14)}</a>${s.host ? ` · ${esc(s.host)}` : ''}</p>
    ${prev || next ? `<nav class="doc-nav">${prev ? `<button data-go="${prev.id}" aria-label="上一篇">${I('arrowl')}<span>${esc(titleOf(prev))}</span></button>` : '<span></span>'}${next ? `<button data-go="${next.id}" aria-label="下一篇"><span>${esc(titleOf(next))}</span>${I('arrow')}</button>` : ''}</nav>` : ''}
  </footer>
</article>`;
  }

  /* ── 社交 ── */
  function post(p) {
    const r = p.repost; const who = r || p; const text = r ? r.text : p.text;
    const long = text.length > 420 || text.split('\n').length > 9;
    const n = p.media.length;
    return `
<article class="post">
  ${r ? `<div class="post-rt">${I('repeat', 14)}${esc(p.name)} 转发</div>` : ''}
  <span class="post-av">${who.avatar ? `<img src="${who.avatar}" alt="">` : esc((who.name || '?').slice(0, 1))}</span>
  <div class="post-b">
    <div class="post-h"><b>${esc(who.name)}</b><span>@${esc(who.handle)}</span>${dot}<span>${rel(p.at)}</span></div>
    <div class="post-t ${long ? 'clip' : ''}"${p.lang === 'en' ? ' lang="en"' : ''}>${esc(text).replace(/\n/g, '<br>').replace(/(https?:\/\/\S+)/g, '<a href="$1" target="_blank" rel="noreferrer">$1</a>')}</div>
    ${n ? `<div class="post-m n${Math.min(n, 4)}">${p.media.slice(0, 4).map((m) => `<img src="${m}" alt="" loading="lazy">`).join('')}</div>` : ''}
    <div class="post-f">${long ? `<button class="btn sm quiet" data-act="unclip">${I('down', 14)}展开全文</button>` : ''}<button class="btn sm quiet" data-act="noop" data-msg="翻译会调用模型，样页不调">${I('translate', 14)}译为中文</button><button class="btn sm quiet" data-act="noop" data-msg="已收藏（示意）">${I('star', 14)}收藏</button><a class="btn sm quiet" href="${esc(p.url)}" target="_blank" rel="noreferrer">${I('ext', 14)}原帖</a></div>
  </div>
</article>`;
  }
  function renderSocial() {
    let last = ''; let h = '';
    for (const p of D.social) { const k = dayKey(p.at); if (k !== last) { h += `<div class="day">${dayLabel(p.at)}</div>`; last = k; } h += post(p); }
    const accts = D.subscribed.filter((id) => D.sources[id] && D.sources[id].shape === 'social');
    return `
<div class="page social" data-scroll><div class="wrap">
  <header class="pg-h"><h1>社交</h1><div class="pg-tools"><div class="seg"><button class="on">全部</button><button>未读</button></div><button class="tool" data-tip="只看收藏">${I('star', 18)}</button><button class="tool" data-tip="全部标为已读">${I('checks', 18)}</button><button class="tool" data-tip="搜索" data-tip-side="left">${I('search', 18)}</button></div></header>
  <div class="pg-bar" style="height:1px"></div>
  <div class="split">
    <div class="split-main">${h}</div>
    <aside class="split-side">
      <button class="src on">${I('social')}<span>全部账号</span></button>
      ${accts.map((id) => `<button class="src">${logo(id, 18)}<span>${esc(D.sources[id].name.replace(/^X\s*·\s*/, ''))}</span></button>`).join('')}
      <button class="src more" data-nav="discover">${I('discover')}<span>发现更多账号</span></button>
    </aside>
  </div>
</div></div>`;
  }

  /* ── 榜单 / 事件页 ── */
  function change(e) {
    if (e.prev == null) return '<span class="badge">新上榜</span>';
    const d = e.prev - e.rank;
    if (d === 0) return '';
    return d > 0 ? `<span class="chg upx">${I('up', 14)}${d}</span>` : `<span class="chg">${I('dn', 14)}${-d}</span>`;
  }
  function eventRow(e, i) {
    const more = e.publishers.length > 3 ? `<span class="pubs-more">+${e.publishers.length - 3}</span>` : '';
    return `
<article class="rk ${i === 0 ? 'lead' : ''}" data-event="${e.id}" tabindex="0">
  <div class="rk-n"><b>${e.rank}</b>${change(e)}</div>
  <div class="rk-b">
    <h3 class="kw"${langOf(e.title)}>${T(e.title, { display: i === 0 })}</h3>
    <p>${esc(e.sum)}</p>
    <div class="rk-meta">${e.recent ? '<span class="badge">近 2 天</span>' : ''}<span>最新 ${esc(e.latest.name || '')}</span>${dot}<span>${rel(e.latest.at || e.latestAt)}</span></div>
  </div>
  <div class="rk-s"><div class="pubs">${e.publishers.slice(0, 3).map((p) => logo(p.src, 20, p.name)).join('')}${more}</div><div class="rk-c"><b>${e.pubs}</b> 家报道</div></div>
</article>`;
  }
  function pickRow(p, i, pod) {
    const t = p.zh || p.title; const o = p.zh && isLatin(p.title) ? p.title : '';
    return `
<article class="rk pick ${i === 0 ? 'lead' : ''}" tabindex="0" ${D.details[p.id] ? `data-open="${p.id}" data-from="rank" data-shape="${pod ? 'podcast' : 'article'}"` : ''}>
  <div class="rk-n"><b>${i + 1}</b></div>
  <div class="rk-b">
    <h3 class="kw"${langOf(t)}>${T(t, { display: i === 0 })}</h3>
    ${o ? `<p class="orig" lang="en">${esc(o)}</p>` : ''}
    <p>${esc(p.sum)}</p>
    <div class="rk-meta">${logo(p.src, 16, p.name)}<span>${esc(p.name)}</span>${p.kindl && !pod ? `${dot}<span>${esc(p.kindl)}</span>` : ''}${dot}<span>${rel(p.at)}</span></div>
  </div>
  <div class="rk-s"><div class="rk-v">${sc(p.cv)}</div></div>
</article>`;
  }
  function renderRank() {
    if (state.event) return renderEvent();
    const tab = state.rankTab;
    const ev = state.rankWin === 'week' ? D.events.week : D.events.d2;
    let body = '';
    if (tab === 'events') body = ev.map(eventRow).join('');
    if (tab === 'deep') body = D.picks.article.map((p, i) => pickRow(p, i, false)).join('');
    if (tab === 'pods') body = D.picks.podcast.map((p, i) => pickRow(p, i, true)).join('');
    const TABS = [['events', '热点事件'], ['deep', '深度阅读'], ['pods', '播客精选']];
    return `
<div class="page rank" data-scroll><div class="wrap">
  <header class="pg-h"><h1>榜单</h1></header>
  <div class="pg-bar">
    <div class="tabs">${TABS.map(([k, l]) => `<button class="${tab === k ? 'on' : ''}" data-ranktab="${k}">${l}</button>`).join('')}</div>
    ${tab === 'events' ? `<div class="seg mini"><button class="${state.rankWin === 'week' ? 'on' : ''}" data-rankwin="week">本周</button><button class="${state.rankWin === 'd2' ? 'on' : ''}" data-rankwin="d2">近 2 天</button></div>` : '<span class="lab">内容价值</span>'}
  </div>
  <div class="rk-list">${body || '<div class="empty"><b>近 2 天还没有上榜的事件</b><button class="btn" data-rankwin="week">看本周</button></div>'}</div>
</div></div>`;
  }
  function renderEvent() {
    const e = D.eventDetails[state.event];
    if (!e) { const b = D.events.week.find((x) => x.id === state.event) || {}; return `<div class="page" data-scroll><div class="wrap"><div class="ev-top"><button class="back" data-act="evback">${I('left')}返回榜单</button></div><header class="ev-h"><h1 class="kw">${T(b.title || '', { display: true })}</h1></header><p class="note">这件事的时间线没有随样页打包，榜单前 4 件可以点开。</p></div></div>`; }
    const rep = e.members.find((m) => m.id === e.rep.id) || e.members[0];
    let last = ''; let tl = '';
    for (const m of e.members) {
      const k = dayKey(m.at);
      if (k !== last) { tl += `<li class="tl-day">${dayLabel(m.at)}</li>`; last = k; }
      const t = m.zh || m.title;
      tl += `<li class="tl-i ${m.id === rep.id ? 'rep' : ''}" ${D.details[m.id] ? `data-open="${m.id}" data-from="event"` : ''}><time>${clock(m.at)}</time><div><div class="tl-src">${logo(m.src, 16, m.name)}<span>${esc(m.name)}</span><em>${ROLE[m.role] || ''}</em></div><h4 class="kw"${langOf(t)}>${T(t)}</h4></div></li>`;
    }
    const rt = rep.zh || rep.title;
    return `
<div class="page event" data-scroll><div class="wrap">
  <div class="ev-top"><button class="back" data-act="evback">${I('left')}返回榜单</button></div>
  <header class="ev-h">
    <div class="kick"><span>本周第 ${e.rank} 件</span></div>
    <h1 class="kw bal"${langOf(e.title)}>${T(e.title, { display: true })}</h1>
    <div class="doc-by"><span>${e.pubs} 家来源报道</span>${dot}<span>${e.n} 篇</span>${dot}<span>首报 ${local(e.first).getUTCMonth() + 1}月${local(e.first).getUTCDate()}日</span></div>
  </header>
  <div class="ev-grid">
    <section class="ev-rep">
      <div class="lab rep-lab">代表报道</div>
      <div class="tl-src">${logo(rep.src, 18, rep.name)}<span>${esc(rep.name)}</span><em>${ROLE[rep.role] || ''}</em></div>
      <h2 class="kw"${langOf(rt)}>${T(rt)}</h2>
      ${rep.zh && isLatin(rep.title) ? `<p class="orig" lang="en">${esc(rep.title)}</p>` : ''}
      <p class="ev-sum">${esc(rep.sum || e.sum)}</p>
      <a class="btn" href="${esc(e.rep.url || '#')}" target="_blank" rel="noreferrer">${rep.role === 'official' ? '官方原文' : '阅读原文'}${I('ext', 14)}</a>
    </section>
    <section class="ev-tl"><ol class="tl">${tl}</ol></section>
  </div>
</div></div>`;
  }

  /* ── 发现 ── */
  function renderDisc() {
    const q = state.q.trim().toLowerCase();
    const all = Object.values(D.sources).filter((s) => (state.discShape === 'all' || s.shape === state.discShape) && (!q || (s.name + s.desc).toLowerCase().includes(q)));
    const today = dayKey(D.now);
    const fresh = (s) => (!s.last ? '' : dayKey(s.last + 'Z') === today ? '今天有更新' : `${rel(s.last + 'Z')}更新`);
    const card = (s) => `
<article class="sc-card" data-preview="${s.id}" tabindex="0">
  ${logo(s.id, 36)}
  <div class="sc-b"><h4><span>${esc(s.name)}</span>${s.kind && s.kind !== '资讯' ? `<em>${esc(s.kind)}</em>` : ''}</h4><p>${esc(s.desc)}</p><div class="sc-m">${s.last && dayKey(s.last + 'Z') === today ? '<i class="dot"></i>' : ''}<span>${fresh(s)}</span>${s.subs > 1 ? `${dot}<span>${s.subs} 人订阅</span>` : ''}</div></div>
  <button class="btn sm sub ${s.on ? 'on' : ''}" data-sub="${s.id}">${s.on ? `${I('check', 14)}<span class="t-on">已订阅</span><span class="t-off">取消订阅</span>` : `${I('plus', 14)}订阅`}</button>
</article>`;
    const groups = ROLE_ORDER.map((r) => [r, all.filter((s) => s.role === r)]).filter((g) => g[1].length);
    const colls = D.collections.map((c) => `
<article class="coll"><h4>${esc(c.name)}</h4><p>${esc(c.desc)}</p><div class="coll-l">${c.ids.slice(0, 7).map((id) => logo(id, 22)).join('')}<span>${c.ids.length} 个来源</span></div><button class="btn sm" data-act="noop" data-msg="已订阅合集（示意）">${I('plus', 14)}订阅合集</button></article>`).join('');
    const SH = [['all', '全部'], ['article', '文章'], ['podcast', '播客'], ['social', '社交']];
    return `
<div class="page disc" data-scroll><div class="wrap">
  <header class="pg-h"><h1>发现</h1>
    <div class="pg-tools"><label class="find">${I('search')}<input placeholder="筛选来源名称或简介…" value="${esc(state.q)}" data-q></label><button class="btn" data-act="noop" data-msg="添加自定源：沿用现网弹窗，样页未画">${I('plus', 14)}添加文章源</button></div></header>
  <div class="pg-bar">
    <div class="tabs">${[['sources', '来源'], ['collections', '合集'], ['interest', '兴趣']].map(([k, l]) => `<button class="${state.discTab === k ? 'on' : ''}" data-disctab="${k}">${l}</button>`).join('')}</div>
    ${state.discTab === 'sources' ? `<div class="seg mini">${SH.map(([k, l]) => `<button class="${state.discShape === k ? 'on' : ''}" data-discshape="${k}">${l}</button>`).join('')}</div>` : ''}
  </div>
  ${state.discTab === 'interest' ? '<div class="note">兴趣主题的挑选要用登录读者的数据，样页没有画。</div>' : state.discTab === 'collections' ? `<div class="colls">${colls}</div>` : groups.map(([r, list]) => `
  <section class="dg"><header class="grp-h"><h2>${ROLE[r]}</h2><span>${list.length} 个</span></header><div class="dg-b">${list.map(card).join('')}</div></section>`).join('') || `<div class="empty"><b>没有找到「${esc(state.q)}」</b></div>`}
</div></div>`;
  }

  /* ── 问哆啦美（面板形制；回答是示意，样页不调模型）── */
  function renderQa() {
    const top = D.brief.items.slice(0, 3);
    const ans = state.qaAsked ? `
      <div class="qa-q">今天有哪些值得看的？</div>
      <div class="qa-a">
        <p>今天的早报里有三件事排在前面：</p>
        ${top.map((it, i) => `<p>${esc((it.zh || it.title).replace(/[！。]$/, ''))}<sup data-open="${it.id}" data-from="brief">${i + 1}</sup></p>`).join('')}
        <div class="qa-src">${top.map((it, i) => `<button data-open="${it.id}" data-from="brief"><b>${i + 1}</b>${logo(it.src, 16, it.srcName)}<span>${esc(it.zh || it.title)}</span></button>`).join('')}</div>
        <p class="qa-demo">示意：这段回答是用今天早报的前三条拼的，用来看版式；样页不调用模型。</p>
      </div>` : `
      <div class="qa-sug">${['今天有哪些值得看的？', '这周 Anthropic 发了什么？', '帮我找讲智能体评测的文章'].map((q) => `<button data-act="qaask"><span>${q}</span>${I('arrow', 14)}</button>`).join('')}</div>`;
    return `
<aside class="qa" data-keep>
  <header class="qa-h"><span class="spark">${I('spark', 18)}</span><b>问哆啦美</b><span class="grow"></span><button class="tool" data-act="qa" aria-label="关闭">${I('x', 18)}</button></header>
  <div class="qa-b">${ans}</div>
  <footer class="qa-f"><div class="qa-in"><textarea rows="1" placeholder="问问你订阅的内容…" data-qain></textarea><button class="btn primary" data-act="qaask" aria-label="发送">${I('up')}</button></div></footer>
</aside>`;
  }
  function renderDock() {
    if (!state.playing) return '';
    const a = { ...(D.pods.find((x) => x.id === state.playing) || {}), ...(D.details[state.playing] || {}) };
    const dur = (a.pod && a.pod.dur) || a.dur || 1600;
    return `
<div class="dock">
  ${D.covers[a.src] ? `<img class="dock-bg" src="${D.covers[a.src]}" alt="">` : ''}
  <div class="dock-l">${D.covers[a.src] ? `<img src="${D.covers[a.src]}" alt="">` : ''}<div><b>${esc(titleOf(a))}</b><span>${esc((a.pod && a.pod.show) || S(a.src).name)}</span></div></div>
  <div class="dock-c"><button class="tool" data-tip="后退 15 秒" data-tip-side="top">${I('back15', 18)}</button><button class="dock-play" data-act="pause" aria-label="${state.paused ? '继续' : '暂停'}">${I(state.paused ? 'play' : 'pause')}</button><button class="tool" data-tip="前进 15 秒" data-tip-side="top">${I('fwd15', 18)}</button></div>
  <div class="dock-r"><time>${hms(dur * 0.31)} / ${hms(dur)}</time><button class="btn sm quiet">1×</button><button class="tool" data-act="stop" aria-label="关闭播放">${I('x', 18)}</button></div>
  <i class="dock-prog"></i>
</div>`;
  }

  /* ── 调度 ── */
  let toastTimer = 0; let toastHtml = '';
  function toast(msg, undo) {
    toastHtml = `<div class="toast ${undo ? '' : 'plain'}" role="status"><span>${esc(msg)}</span>${undo ? '<button data-act="undo">撤销</button>' : ''}</div>`;
    toastUndo = undo || null; renderOver();
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { toastHtml = ''; toastUndo = null; renderOver(); }, 3200);
  }
  let toastUndo = null;
  function renderOver() { const o = $('#over'); if (o) o.innerHTML = (state.qa ? renderQa() : '') + toastHtml; }
  function renderStage(keepScroll) {
    const st = $('#stage'); if (!st) return;
    const keep = keepScroll ? $$('[data-scroll]', st).map((el) => el.scrollTop) : null;
    const v = state.view;
    st.innerHTML = v === 'brief' ? renderBrief() : v === 'feed' ? renderFeed() : v === 'social' ? renderSocial() : v === 'rank' ? renderRank() : renderDisc();
    st.dataset.view = v;
    if (keep) $$('[data-scroll]', st).forEach((el, i) => { if (keep[i] != null) el.scrollTop = keep[i]; });
    $('#dock').innerHTML = renderDock();
    const sq = $('[data-sq]'); if (sq && state.search) { sq.focus(); sq.setSelectionRange(sq.value.length, sq.value.length); }
  }
  function render(keepScroll) {
    const root = document.documentElement;
    root.dataset.theme = state.theme; root.dataset.rail = state.rail; root.dataset.weight = state.weight;
    if (state.palette === 'porcelain') delete root.dataset.palette; else root.dataset.palette = state.palette;
    $('#app').innerHTML = shell();
    renderStage(keepScroll); renderOver(); syncLab();
    const h = `#${state.theme}.${navKey()}${state.fold ? '.fold' : ''}${state.palette !== 'porcelain' ? '.' + state.palette : ''}`;
    if (location.hash.split('.bare')[0] !== h) history.replaceState(null, '', h + (root.classList.contains('bare') ? '.bare' : ''));
  }
  function open(id, from, shape) {
    state.view = 'feed'; state.shape = shape || 'article'; state.scope = 'sub'; state.sel = id; state.from = from || null; state.read.add(id);
    state.srcPop = false; state.search = false; state.sq = ''; state.favOnly = false; state.unread = false; state.preview = false;
    if (from) { state.src = null; state.kind = 'all'; }
  }
  const firstPick = (shape) => (shape === 'podcast' ? feedItems().find((x) => D.details[x.id]) || feedItems()[0] : feedItems()[1] || feedItems()[0]);
  const closePops = () => { const had = state.srcPop || state.cal || state.menu; state.srcPop = false; state.cal = false; state.menu = false; return had; };

  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const q = (sel) => t.closest(sel);
    let el;
    if ((el = q('[data-lab]'))) { const [k, v] = el.dataset.lab.split(':'); state[k] = v === 'true' ? true : v === 'false' ? false : v; render(true); return; }
    if ((el = q('[data-fav]'))) {
      ev.preventDefault(); ev.stopPropagation(); const id = el.dataset.fav; const on = !state.fav.has(id);
      if (on) state.fav.add(id); else state.fav.delete(id);
      renderStage(true); toast(on ? '已收藏' : '已取消收藏'); return;
    }
    if ((el = q('[data-sub]'))) {
      ev.stopPropagation(); const s = D.sources[el.dataset.sub]; s.on = !s.on;
      if (s.on && !D.subscribed.includes(s.id)) D.subscribed.push(s.id);
      if (!s.on) { const i = D.subscribed.indexOf(s.id); if (i >= 0) D.subscribed.splice(i, 1); }
      if (s.on) state.preview = false;
      renderStage(true); toast(s.on ? `已订阅「${s.name}」` : `已取消订阅「${s.name}」`, () => { s.on = !s.on; renderStage(true); }); return;
    }
    if ((el = q('[data-act]'))) {
      ev.preventDefault(); const a = el.dataset.act;
      if (a === 'noop') { toast(el.dataset.msg || '样页未接'); return; }
      if (a === 'undo') { if (toastUndo) toastUndo(); toastHtml = ''; renderOver(); return; }
      if (a === 'theme') state.theme = state.theme === 'dark' ? 'light' : 'dark';
      if (a === 'srcpop') { if (!state.fold) return; const v = !state.srcPop; closePops(); state.srcPop = v; }
      if (a === 'cal') { ev.stopPropagation(); const v = !state.cal; closePops(); state.cal = v; }
      if (a === 'calday') { if (!el.classList.contains('today')) { toast('样页只带了今天这一期'); return; } state.cal = false; }
      if (a === 'menu') { const v = !state.menu; closePops(); state.menu = v; $('#pane').innerHTML = renderPane(); return; }
      if (a === 'fold') { state.fold = !state.fold; state.srcPop = false; }
      if (a === 'search') { state.search = !state.search; state.sq = ''; }
      if (a === 'favonly') state.favOnly = !state.favOnly;
      if (a === 'markall') { const before = new Set(state.read); feedItems().forEach((x) => state.read.add(x.id)); renderStage(true); toast('已全部标为已读', () => { state.read = before; renderStage(true); }); return; }
      if (a === 'unreadone') { state.read.delete(state.sel); renderStage(true); toast('已标为未读'); return; }
      if (a === 'back') { state.view = state.from === 'brief' ? 'brief' : 'rank'; state.from = null; }
      if (a === 'evback') state.event = null;
      if (a === 'qa') { state.qa = !state.qa; render(true); return; }
      if (a === 'qaask') { state.qaAsked = true; renderOver(); return; }
      if (a === 'play') { if (state.playing === state.sel) state.paused = !state.paused; else { state.playing = state.sel; state.paused = false; } renderStage(true); return; }
      if (a === 'pause') { state.paused = !state.paused; renderStage(true); return; }
      if (a === 'stop') { state.playing = null; renderStage(true); return; }
      if (a === 'why') { state.why = !state.why; const sp = $('#pane').scrollTop; $('#pane').innerHTML = renderPane(); $('#pane').scrollTop = sp; return; }
      if (a === 'unclip') { const p = el.closest('.post'); $('.post-t', p).classList.remove('clip'); el.remove(); return; }
      render(a !== 'back' && a !== 'evback'); return;
    }
    if ((el = q('[data-nav]'))) {
      ev.preventDefault(); const n = el.dataset.nav; closePops(); state.event = null; state.from = null; state.search = false; state.sq = ''; state.preview = false;
      if (n === 'article' || n === 'podcast') { if (state.shape !== n) { state.sel = null; state.src = null; state.kind = 'all'; state.scope = 'sub'; } state.view = 'feed'; state.shape = n; if (!state.sel) { const f = firstPick(n); if (f) { state.sel = f.id; state.read.add(f.id); } } } else state.view = n;
      render(); return;
    }
    if ((el = q('[data-scope]'))) { if (state.scope === el.dataset.scope) { state.src = null; state.preview = false; } state.scope = el.dataset.scope; state.srcPop = false; renderStage(); return; }
    if ((el = q('[data-src]'))) { state.src = el.dataset.src || null; state.srcPop = false; state.preview = false; renderStage(); return; }
    if ((el = q('[data-kind]'))) { state.kind = el.dataset.kind; renderStage(); return; }
    if ((el = q('[data-unread]'))) { state.unread = el.dataset.unread === '1'; renderStage(true); return; }
    if ((el = q('[data-podtab]'))) { state.podTab = el.dataset.podtab; $('#pane').innerHTML = renderPane(); return; }
    if ((el = q('[data-ranktab]'))) { state.rankTab = el.dataset.ranktab; renderStage(); return; }
    if ((el = q('[data-rankwin]'))) { state.rankWin = el.dataset.rankwin; renderStage(); return; }
    if ((el = q('[data-disctab]'))) { state.discTab = el.dataset.disctab; renderStage(); return; }
    if ((el = q('[data-discshape]'))) { state.discShape = el.dataset.discshape; renderStage(); return; }
    if ((el = q('[data-toc]'))) { ev.preventDefault(); const sec = $(`[data-sec="${el.dataset.toc}"]`); if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    if ((el = q('[data-event]'))) { ev.preventDefault(); state.view = 'rank'; state.rankTab = 'events'; state.event = Number(el.dataset.event); render(); return; }
    if ((el = q('[data-preview]'))) {
      const id = el.dataset.preview; const s = D.sources[id];
      const has = (s.shape === 'podcast' ? D.pods : D.articles).some((x) => x.src === id);
      if (s.shape === 'social' || !has) { toast('样页快照里没有带这个来源的内容'); return; }
      state.view = 'feed'; state.shape = s.shape; state.scope = 'sub'; state.src = id; state.kind = 'all'; state.preview = !s.on; const f = feedItems()[0]; state.sel = f ? f.id : null; if (f) state.read.add(f.id);
      render(); return;
    }
    if ((el = q('[data-open]'))) { open(el.dataset.open, el.dataset.from, el.dataset.shape); state.qa = false; render(); return; }
    if ((el = q('[data-go]')) || (el = q('.row[data-id]'))) {
      state.sel = el.dataset.go || el.dataset.id; state.read.add(state.sel); state.from = null; state.menu = false;
      $$('.row.on').forEach((r) => r.classList.remove('on'));
      const r = $(`.row[data-id="${CSS.escape(state.sel)}"]`); if (r) { r.classList.add('on', 'read'); r.scrollIntoView({ block: 'nearest' }); }
      const p = $('#pane'); p.innerHTML = renderPane(); p.scrollTop = 0; return;
    }
    if (!q('[data-keep]') && closePops()) renderStage(true);
  });
  document.addEventListener('input', (ev) => {
    const t = ev.target;
    if (t.matches('[data-q]')) { state.q = t.value; const pos = t.selectionStart; renderStage(true); const i = $('[data-q]'); i.focus(); i.setSelectionRange(pos, pos); }
    if (t.matches('[data-sq]')) { state.sq = t.value; renderStage(true); }
  });
  document.addEventListener('keydown', (ev) => {
    const typing = ev.target.matches('input, textarea');
    if (ev.key === 'Escape') { if (state.qa) { state.qa = false; render(true); return; } if (state.search) { state.search = false; state.sq = ''; renderStage(true); return; } if (closePops()) renderStage(true); return; }
    if (ev.key === 'Enter' && ev.target.matches('[data-qain]') && !ev.shiftKey) { ev.preventDefault(); state.qaAsked = true; renderOver(); return; }
    if (ev.key === 'Enter' && ev.target.matches('[tabindex="0"]')) ev.target.click();
    if (state.view === 'feed' && (ev.key === 'j' || ev.key === 'k') && !typing) {
      const rows = $$('.row[data-id]'); const i = rows.findIndex((r) => r.dataset.id === state.sel);
      const n = rows[Math.max(0, Math.min(rows.length - 1, i + (ev.key === 'j' ? 1 : -1)))];
      if (n) n.click();
    }
  });
  /* 滚动：阅读窗进度线；早报目录跟读 */
  document.addEventListener('scroll', (ev) => {
    const el = ev.target; if (!el.classList) return;
    if (el.classList.contains('pane')) { const bar = $('.pane-bar', el); if (bar) bar.style.setProperty('--prog', Math.min(1, el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight)).toFixed(3)); }
    if (el.classList.contains('brief')) {
      const top = el.getBoundingClientRect().top; let cur = '';
      $$('.sec', el).forEach((s) => { if (s.getBoundingClientRect().top - top < 120) cur = s.dataset.sec; });
      $$('.toc a', el).forEach((a) => a.classList.toggle('on', a.dataset.toc === cur));
    }
  }, true);

  /* ── 样页开关（不属于产品界面）── */
  function syncLab() {
    $$('#lab [data-lab]').forEach((b) => { const [k, v] = b.dataset.lab.split(':'); b.classList.toggle('on', String(state[k]) === v); });
  }
  function boot() {
    const h = location.hash.replace('#', '').split('.').filter((x) => x && x !== 'a' && x !== 'b');
    const has = (k) => h.includes(k);
    if (has('bare')) document.documentElement.classList.add('bare');
    if (has('dark')) state.theme = 'dark';
    if (has('graphite')) state.palette = 'graphite';
    if (has('paper')) state.palette = 'paper';
    if (has('fold') || has('cols2')) state.fold = true;
    if (has('railhover')) state.rail = 'hover';
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
    render();
  }
  window.__proto = { state, render, toast };
  boot();
})();
