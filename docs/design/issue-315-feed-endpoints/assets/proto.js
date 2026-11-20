/* issue #315 样页:设置 → 聚合接口。
 * 底页是现网弹窗的 DOM(tools/build.py 生成);这里按地址参数把卡片正文换成方向 A / B,并挂上样页自己的开关条。
 * 不属于产品代码。地址参数:
 *   dir=now|a|b      现网 / 方向 A(分组列全)/ 方向 B(一组一行)
 *   role=reader|admin
 *   token=used|none  令牌用过 / 还没签发
 *   mcp=1|0          卡头通往「MCP 接入」的链接
 *   theme=light|dark
 *   open=<接口>      A:点开的那一行(articles、articles.md、articles.xml、events、events/{event_id};不带 = 全收起)
 *   fmt=<接口> ev=<接口>   B:两组各自选中的那一个
 *   params=articles|events|both   B / 现网:打开哪一组的「接口参数」
 *   scroll=bottom    正文滚到底(总览页截下半截用)
 *   bar=0            不显示开关条
 */
(() => {
  'use strict';

  const qs = new URLSearchParams(location.search);
  const pick = (key, allowed, fallback) => (allowed.includes(qs.get(key)) ? qs.get(key) : fallback);

  const ROOT = 'https://dorami.example/api';
  const curl = (path) => `curl -H "Authorization: Bearer $DORAMI_TOKEN" \\\n  "${ROOT}${path}"`;

  /* 参数说明:照后端现状写(src/api/routers/subscriptions.py、events.py),逐接口列它真正认的那几个。 */
  const P = {
    dates: () => ['publish_date_start / _end', '发布时间窗口（YYYY-MM-DD），生成日报最常用'],
    types: () => ['content_types', '逗号分隔的内容类型，如 rss_article,web_article'],
    sources: (role) => ['source_ids', role === 'admin' ? '逗号分隔的来源' : '逗号分隔的来源；仅取与你已订阅来源的交集'],
    search: () => ['search', '标题关键词过滤'],
    content: () => ['include_content', '是否下发正文，默认 true；传 false 仅取元数据'],
    page: (max) => () => ['skip / limit', `分页偏移与条数，limit 上限 ${max}`],
    window: () => ['window_hours', '榜单窗口（小时），默认 168 即近 7 天，48 即近 2 天；范围 1–336'],
    limit: () => ['limit', '返回几件事，1–10，默认 10'],
    members: () => ['include_members', '传 true 时每件事带上它的全部报道，默认 false'],
  };

  /* 这枚令牌能调的全部 HTTP 接口。 */
  const GROUPS = [
    {
      key: 'articles', label: '文章', segLabel: '返回格式',
      scope: (role) => (role === 'admin' ? '全站公开内容' : '已订阅来源'),
      endpoints: [
        { key: 'articles', path: '/public/feed/articles', fmt: 'JSON', seg: 'JSON', example: curl('/public/feed/articles?limit=20'),
          params: [P.dates, P.types, P.sources, P.search, P.content, P.page(500)] },
        { key: 'articles.md', path: '/public/feed/articles.md', fmt: 'Markdown', seg: 'Markdown', example: curl('/public/feed/articles.md?limit=20'),
          params: [P.dates, P.types, P.sources, P.search, P.page(200)] },
        { key: 'articles.xml', path: '/public/feed/articles.xml', fmt: 'Atom', seg: 'Atom', example: `${ROOT}/public/feed/articles.xml?token=dfeed_你的令牌`,
          params: [P.dates, P.types, P.sources, P.search, P.content, P.page(200)] },
      ],
    },
    {
      key: 'events', label: '热点事件', segLabel: '取哪一份',
      scope: () => '全站公开内容',
      endpoints: [
        { key: 'events', path: '/public/feed/events', fmt: 'JSON', seg: '榜单', example: curl('/public/feed/events?include_members=true'),
          params: [P.window, P.limit, P.members] },
        { key: 'events/{event_id}', path: '/public/feed/events/{event_id}', fmt: 'JSON', seg: '单个事件', example: curl('/public/feed/events/128'),
          params: [P.window] },
      ],
    },
  ];
  const ALL = GROUPS.flatMap((g) => g.endpoints.map((e) => e.key));

  const state = {
    dir: pick('dir', ['now', 'a', 'b'], 'a'),
    role: pick('role', ['reader', 'admin'], 'reader'),
    token: pick('token', ['used', 'none'], 'used'),
    mcp: qs.get('mcp') !== '0',
    theme: pick('theme', ['light', 'dark'], 'light'),
    open: ALL.includes(qs.get('open')) ? qs.get('open') : '',
    fmt: pick('fmt', GROUPS[0].endpoints.map((e) => e.key), 'articles'),
    ev: pick('ev', GROUPS[1].endpoints.map((e) => e.key), 'events'),
    params: pick('params', ['articles', 'events', 'both'], ''),
    scroll: qs.get('scroll') || '',
    bar: qs.get('bar') !== '0',
  };

  const scroller = document.querySelector('.sett-scroll');
  const host = scroller.firstElementChild;
  const ORIGINAL = host.innerHTML;

  /* 图标:能从现网 DOM 里取的就取(眼睛 / 复制 / 重新生成),其余三枚照 lucide 的路径写。 */
  const svgOf = (sel) => document.querySelector(sel).outerHTML;
  const lucide = (name, body, cls = '') =>
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-${name} ${cls}" aria-hidden="true">${body}</svg>`;
  const ICON = {
    eye: svgOf('.token-row .copybtn[title="显示"] svg'),
    copy: svgOf('.token-row .copybtn[title="复制令牌"] svg'),
    rotate: svgOf('.token-row .copybtn[title^="重新生成"] svg'),
    check: lucide('check', '<path d="M20 6 9 17l-5-5"></path>'),
    chevron: lucide('chevron-right', '<path d="m9 18 6-6-6-6"></path>', 'h-3.5 w-3.5'),
    key: lucide('key-round', '<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"></path><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"></circle>', 'h-3.5 w-3.5'),
  };

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const copyBtn = (title, text) => `<button type="button" class="copybtn" title="${esc(title)}" data-copy="${esc(text)}">${ICON.copy}</button>`;

  const tokenBlock = () => (state.token === 'none'
    ? `<button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs">${ICON.key} 签发令牌</button>`
    : `<div class="token-row"><span class="token-val">...Qm7xK2</span>`
      + `<button type="button" class="copybtn" title="显示">${ICON.eye}</button>`
      + `<button type="button" class="copybtn" title="复制令牌">${ICON.copy}</button>`
      + `<button type="button" class="copybtn" title="重新生成（旧令牌立即失效）">${ICON.rotate}</button></div>`
      + `<div class="token-meta">签发于 2026-06-17 · 最近调用 11-20 09:12 · 近 30 天 214 次</div>`);

  const head = () => `<div class="card-head"><span class="sett-lbl">个人聚合令牌</span>${state.mcp
    ? `<button type="button" class="sett-link inline-flex items-center gap-0.5 text-xs" style="margin-left:auto">MCP 接入 ${ICON.chevron}</button>` : ''}</div>`;

  const groupHead = (g, tail = '') => `<div class="ep-group"><span class="sett-lbl">${g.label}</span><span class="target-chip">${g.scope(state.role)}</span>${tail}</div>`;

  const paramLines = (e) => e.params.map((make) => make(state.role))
    .map(([name, desc]) => `<p><code class="font-mono">${name}</code>：${desc}</p>`).join('');
  const paramsFold = (g, e) => `<details class="scope-note" data-params="${g.key}"${state.params === g.key || state.params === 'both' ? ' open' : ''}><summary>接口参数</summary>${paramLines(e)}</details>`;

  /* 方向 A:五个接口全部列出,一行一个;点开一行是它的用法(调用示例 + 它认的参数) */
  const renderA = () => head() + tokenBlock() + GROUPS.map((g) => groupHead(g)
    + g.endpoints.map((e) => `<details class="ep" data-ep="${esc(e.key)}"${state.open === e.key ? ' open' : ''}>`
      + `<summary class="endpoint"><span class="endpoint-method">GET</span>`
      + `<span class="endpoint-url" title="${ROOT}${esc(e.path)}">${ROOT}${esc(e.path)}</span>`
      + `<span class="endpoint-fmt">${e.fmt}</span>${copyBtn('复制接口地址', ROOT + e.path)}</summary>`
      + `<div class="ep-body"><div class="ep-code">${esc(e.example)}${copyBtn('复制示例', e.example)}</div>`
      + `<div class="ep-params">${paramLines(e)}</div></div></details>`).join('')).join('');

  /* 方向 B:一组一行,行尾的分段切换这一组里的哪一个 */
  const renderB = () => head() + tokenBlock() + GROUPS.map((g) => {
    const stateKey = g.key === 'articles' ? 'fmt' : 'ev';
    const cur = g.endpoints.find((e) => e.key === state[stateKey]);
    const seg = `<span class="mini-seg" style="margin-left:auto" role="group" aria-label="${g.segLabel}">${g.endpoints.map((e) =>
      `<button type="button" class="mini-seg-btn ${e.key === cur.key ? 'is-on' : ''}" data-seg="${stateKey}" data-val="${esc(e.key)}">${e.seg}</button>`).join('')}</span>`;
    return groupHead(g, seg)
      + `<div class="endpoint"><span class="endpoint-method">GET</span><span class="endpoint-url" title="${ROOT}${esc(cur.path)}">${ROOT}${esc(cur.path)}</span>${copyBtn('复制接口地址', ROOT + cur.path)}</div>`
      + `<div class="codeblock">${esc(cur.example)}${copyBtn('复制示例', cur.example)}</div>`
      + paramsFold(g, cur);
  }).join('');

  /* 现网:快照原样,只按身份换范围签、按令牌状态换令牌槽(与线上逻辑一致) */
  const renderNow = () => {
    const box = document.createElement('div');
    box.innerHTML = ORIGINAL;
    box.querySelector('.target-chip').textContent = state.role === 'admin' ? '全站公开内容' : '已订阅来源';
    if (state.role !== 'admin') {
      const p = Array.from(box.querySelectorAll('.scope-note p')).find((el) => el.textContent.startsWith('source_ids'));
      if (p) p.lastChild.textContent = '：逗号分隔的来源；仅取与你已订阅来源的交集';
    }
    if (state.token === 'none') {
      box.querySelector('.token-meta').remove();
      box.querySelector('.token-row').outerHTML = `<button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs">${ICON.key} 签发令牌</button>`;
    }
    if (state.params) box.querySelector('.scope-note').setAttribute('open', '');
    return box.innerHTML;
  };

  const render = () => {
    document.documentElement.dataset.theme = state.theme;
    host.innerHTML = state.dir === 'a' ? renderA() : state.dir === 'b' ? renderB() : renderNow();
    host.dataset.dir = state.dir;
    scroller.scrollTop = state.scroll === 'bottom' ? scroller.scrollHeight : 0;
  };

  /* 交互:复制(换成对勾 1.6 秒)、A 的点开(同时只开一行)、B 的分段、参数折叠 */
  host.addEventListener('click', (event) => {
    const copy = event.target.closest('[data-copy]');
    if (copy) {
      event.preventDefault();
      event.stopPropagation();
      try { navigator.clipboard?.writeText(copy.dataset.copy)?.catch(() => {}); } catch { /* 样页:复制不了也照常给反馈 */ }
      copy.innerHTML = ICON.check;
      setTimeout(() => { copy.innerHTML = ICON.copy; }, 1600);
      return;
    }
    const seg = event.target.closest('[data-seg]');
    if (seg) {
      state[seg.dataset.seg] = seg.dataset.val;
      const top = scroller.scrollTop;
      syncUrl();
      render();
      scroller.scrollTop = top;
    }
  });
  host.addEventListener('toggle', (event) => {
    const el = event.target;
    if (el.matches('.ep')) {
      if (el.open) {
        host.querySelectorAll('.ep[open]').forEach((other) => { if (other !== el) other.open = false; });
        state.open = el.dataset.ep;
        el.scrollIntoView({ block: 'nearest' });
      } else if (state.open === el.dataset.ep) {
        state.open = '';
      }
      syncUrl();
    }
  }, true);

  function syncUrl() {
    const next = new URLSearchParams();
    next.set('dir', state.dir);
    if (state.role !== 'reader') next.set('role', state.role);
    if (state.token !== 'used') next.set('token', state.token);
    if (!state.mcp) next.set('mcp', '0');
    if (state.theme !== 'light') next.set('theme', state.theme);
    if (state.dir === 'a' && state.open) next.set('open', state.open);
    if (state.dir === 'b' && state.fmt !== 'articles') next.set('fmt', state.fmt);
    if (state.dir === 'b' && state.ev !== 'events') next.set('ev', state.ev);
    if (!state.bar) next.set('bar', '0');
    history.replaceState(null, '', `?${next}`);
  }

  /* 样页开关条 */
  const BAR = [
    ['方向', 'dir', [['now', '现网'], ['a', 'A 分组列全'], ['b', 'B 一组一行']]],
    ['身份', 'role', [['reader', '读者'], ['admin', '管理员']]],
    ['令牌', 'token', [['used', '用过'], ['none', '未签发']]],
    ['卡头链接', 'mcp', [[true, '有'], [false, '无']]],
    ['明暗', 'theme', [['light', '亮'], ['dark', '暗']]],
  ];
  const bar = document.createElement('div');
  bar.className = 'px-bar';
  bar.hidden = !state.bar;
  const drawBar = () => {
    bar.innerHTML = BAR.map(([label, key, options]) => `<span class="px-grp"><span class="px-lbl">${label}</span>${
      options.map(([value, text]) => `<button type="button" class="px-btn ${state[key] === value ? 'is-on' : ''}" data-key="${key}" data-val="${value}">${text}</button>`).join('')}</span>`)
      .join('<span class="px-sep"></span>');
  };
  bar.addEventListener('click', (event) => {
    const btn = event.target.closest('.px-btn');
    if (!btn) return;
    const { key, val } = btn.dataset;
    state[key] = key === 'mcp' ? val === 'true' : val;
    state.scroll = '';
    syncUrl();
    drawBar();
    render();
  });
  document.body.appendChild(bar);

  drawBar();
  render();
})();
