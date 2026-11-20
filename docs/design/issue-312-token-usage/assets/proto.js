/* issue #312 样页:令牌交付计量。
 * 底页(app.html)是 v3.69.0 现网「运维 → 账户」的 DOM 快照;本脚本把新增部分挂上去,并用示意数据重画带账号名的地方。
 * 数字全部是示意:由下面的令牌清单 + 固定种子的随机数生成,各处(KPI / 榜 / 表 / 台账 / 抽屉 / 设置卡)同源。
 * 地址参数:dir=a|b  view=page|drawer|settings  user=<账号>  card=now|a|b  state=live|empty  theme=light|dark  focus=api|table|top  top=reads|logins|api  bare */
(() => {
  'use strict';

  const Q = new URLSearchParams(location.search);
  const S = {
    dir: Q.get('dir') === 'a' ? 'a' : 'b',
    view: ['drawer', 'settings'].includes(Q.get('view')) ? Q.get('view') : 'page',
    user: Q.get('user') || 'brief-pipeline',
    card: ['now', 'b'].includes(Q.get('card')) ? Q.get('card') : 'a',
    data: Q.get('state') === 'empty' ? 'empty' : 'live',
    theme: Q.get('theme') === 'dark' ? 'dark' : 'light',
    focus: Q.get('focus') || '',
    bare: Q.has('bare'),
    top: ['logins', 'api'].includes(Q.get('top')) ? Q.get('top') : 'reads',
    measure: 'calls',       // 板块:调用 | 条数
    dMeasure: 'calls',      // 抽屉里的每日图
    sort: { k: 'username', o: 'asc' },
    lsort: { k: 'calls', o: 'desc' },
    lfilter: '',
    flashTok: '',
  };

  // ───────── 示意数据 ─────────
  const DAYS = 30;
  const END = new Date(2026, 10, 19);
  const pad = (n) => String(n).padStart(2, '0');
  const dayOf = (i) => { const d = new Date(END); d.setDate(d.getDate() - (DAYS - 1 - i)); return d; };
  const stamp = (i, hm) => `${pad(dayOf(i).getMonth() + 1)}-${pad(dayOf(i).getDate())} ${hm}`;
  const tick = (i) => `${dayOf(i).getMonth() + 1}-${dayOf(i).getDate()}`;

  function rng(seed) {
    let a = seed >>> 0;
    return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  // 交付面(接口)→ 所属的一组;组决定颜色
  const FAM = { art: ['文章', 'var(--chart-1)'], evt: ['热点事件', 'var(--chart-2)'], sub: ['单订阅', 'var(--chart-3)'], mcp: ['MCP', 'var(--chart-5)'] };
  const FAM_KEYS = Object.keys(FAM);
  const CH = {
    'feed/articles': 'art', 'feed/articles.md': 'art', 'feed/articles.xml': 'art',
    'feed/events': 'evt', 'feed/events/{id}': 'evt',
    'subscriptions/{id}/articles': 'sub', 'subscriptions/{id}/vector/search': 'sub',
    'mcp · browse_articles': 'mcp', 'mcp · get_article': 'mcp', 'mcp · search_articles': 'mcp', 'mcp · get_rag_context': 'mcp',
  };

  // use: [接口, 日均次数, 每次条数, 起始日, 结束日, 'wd' = 只在工作日 | 'mon' = 只在周一]
  const ACCOUNTS = [
    { u: 'admin', role: 'admin', ai: true, last: '11-19 20:12', logins: 27, reads: 150, ai_calls: 96, subs: 26, tokens: [
      { kind: 'feed', prev: '...Qm7xK2', hm: '20:02', use: [['feed/articles', 0.9, 20, 0, 29], ['feed/events', 3, 10, 28, 29], ['feed/events/{id}', 1.5, 1, 28, 29], ['mcp · search_articles', 0.5, 8, 0, 29]] },
    ] },
    { u: 'brief-pipeline', role: 'user', ai: false, last: '11-15 10:50', logins: 2, reads: 0, ai_calls: 0, subs: 5, tokens: [
      { kind: 'feed', prev: '...Lw92Rb', hm: '07:00', use: [['feed/articles.md', 1, 60, 16, 29, 'fix'], ['feed/articles', 3, 100, 16, 29], ['feed/events', 1, 10, 23, 29, 'fix']] },
      { kind: 'feed', prev: '...a81Qzx', hm: '07:00', retired: 16, rejected: 12, use: [['feed/articles.md', 1, 60, 0, 15, 'fix'], ['feed/articles', 3, 100, 0, 15]] },
    ] },
    { u: 'he.jing', role: 'user', ai: false, last: '11-18 22:51', logins: 23, reads: 163, ai_calls: 89, subs: 7, tokens: [
      { kind: 'feed', prev: '...pX8w1c', hm: '09:30', use: [['feed/articles', 2, 100, 0, 29, 'mon']] },
    ] },
    { u: 'lin.qiao', role: 'user', ai: true, last: '11-17 20:51', logins: 18, reads: 146, ai_calls: 89, subs: 9, tokens: [
      { kind: 'feed', prev: '...9cTqYe', hm: '19:00', use: [['feed/articles.xml', 24, 50, 0, 29, 'fix']] },
    ] },
    { u: 'newsroom-bot', role: 'user', ai: false, last: '11-18 12:57', logins: 4, reads: 37, ai_calls: 37, subs: 7, tokens: [
      { kind: 'feed', prev: '...h3VtZa', hm: '18:46', use: [['mcp · search_articles', 18, 8, 0, 29, 'wd'], ['mcp · get_article', 9, 1, 0, 29, 'wd'], ['mcp · get_rag_context', 4, 6, 0, 29, 'wd'], ['mcp · browse_articles', 2, 20, 0, 29, 'wd']] },
    ] },
    { u: 'reader-demo', role: 'user', ai: false, last: '11-19 19:52', logins: 9, reads: 38, ai_calls: 19, subs: 42, tokens: [] },
    { u: 'wu.tong', role: 'user', ai: true, last: '11-17 21:49', logins: 3, reads: 16, ai_calls: 21, subs: 9, tokens: [
      { kind: 'feed', prev: '...Zr5eUo', hm: '', use: [] },
    ] },
    { u: 'zhou.yan', role: 'user', ai: true, last: '11-14 11:49', logins: 6, reads: 17, ai_calls: 11, subs: 6, tokens: [
      { kind: 'sub', sub: 'OpenAI 博客', prev: '...k2Lm9P', hm: '08:15', use: [['subscriptions/{id}/articles', 1, 20, 0, 29, 'wd']] },
    ] },
  ];

  function build() {
    const rand = rng(312);
    const empty = S.data === 'empty';
    for (const acc of ACCOUNTS) {
      acc.active = true;
      acc.api = { calls: 0, items: 0 };
      acc.shown = [];
      for (const t of acc.tokens) {
        t.owner = acc.u;
        t.id = `${acc.u}|${t.prev}`;
        t.ch = {}; t.byFam = {}; t.calls = 0; t.items = 0; t.lastDay = -1;
        for (const f of FAM_KEYS) t.byFam[f] = { calls: Array(DAYS).fill(0), items: Array(DAYS).fill(0) };
        for (const [name, rate, per, from, to, mode] of t.use) {
          const row = { name, fam: CH[name], calls: 0, items: 0 };
          for (let i = from; i <= to; i++) {
            const dow = dayOf(i).getDay();
            const r = rand();   // 每格都取一次,保证「尚无调用」与常态之间其余数字不动
            let n;
            if (mode === 'fix') n = rate;
            else if (mode === 'mon') n = dow === 1 ? rate : 0;
            else if (mode === 'wd' && (dow === 0 || dow === 6)) n = 0;
            else n = rate < 1 ? (r < rate ? 1 : 0) : Math.round(rate * (0.55 + r * 0.9));
            if (empty) n = 0;
            if (!n) continue;
            const it = name.endsWith('get_article') || name.endsWith('{id}') ? n : Math.round(n * per * (0.8 + rand() * 0.4));
            row.calls += n; row.items += it;
            t.byFam[row.fam].calls[i] += n; t.byFam[row.fam].items[i] += it;
            if (i > t.lastDay) t.lastDay = i;
          }
          if (row.calls) { t.ch[name] = row; t.calls += row.calls; t.items += row.items; }
        }
        t.chs = Object.values(t.ch).sort((a, b) => b.calls - a.calls);
        t.last = t.lastDay >= 0 ? stamp(t.lastDay, t.hm) : '';
        t.gone = t.retired != null;
        // 列出的令牌:现用的聚合令牌(没调过也列)+ 调过的任何令牌(含已作废、订阅令牌)
        if (t.calls > 0 || (!t.gone && t.kind === 'feed')) acc.shown.push(t);
        acc.api.calls += t.calls; acc.api.items += t.items;
      }
      acc.shown.sort((a, b) => (a.gone - b.gone) || (b.calls - a.calls));
    }
  }

  const allTokens = () => ACCOUNTS.flatMap((a) => a.shown);
  const famSeries = (tokens, key) => {
    const out = {};
    for (const f of FAM_KEYS) out[f] = Array(DAYS).fill(0);
    for (const t of tokens) for (const f of FAM_KEYS) for (let i = 0; i < DAYS; i++) out[f][i] += t.byFam[f][key][i];
    return out;
  };

  // ───────── 小工具 ─────────
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtNum = (n) => { const v = Number(n || 0); if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`; if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`; return String(v); };
  const avatarHue = (name) => Math.round((name.trim().charAt(0).toUpperCase().charCodeAt(0) * 137.508) % 360);
  const avatar = (name) => `<span class="acct-avatar avatar-letter" style="--avatar-h: ${avatarHue(name)};">${esc(name.charAt(0).toUpperCase())}</span>`;
  const truncLabel = (s) => (s.length > 9 ? `${s.slice(0, 8)}…` : s);
  const kindLabel = (t) => (t.kind === 'feed' ? '聚合令牌' : `订阅令牌 · ${t.sub}`);
  const tokId = (t) => `<span class="tok-id"><span class="tok-kind">${esc(kindLabel(t))}</span><span class="tok-prev">${esc(t.prev)}</span>${t.gone ? '<span class="stamp stamp-idle">已作废</span>' : ''}</span>`;
  const legend = (fams) => `<div class="mb-2 flex flex-wrap gap-x-3 gap-y-1">${fams.map((f) => `<span class="inline-flex items-center gap-1 tiny-meta text-slate-500"><span class="h-2 w-2 rounded-sm" style="background: ${FAM[f][1]};"></span>${FAM[f][0]}</span>`).join('')}</div>`;
  const ZAP = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-zap" aria-hidden="true"><path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"></path></svg>';
  const ZAP_OFF = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-zap-off" aria-hidden="true"><path d="M10.513 4.856 13.12 2.17a.5.5 0 0 1 .86.46l-1.377 4.317"></path><path d="M15.656 10H20a1 1 0 0 1 .78 1.63l-1.72 1.773"></path><path d="M16.273 16.273 10.88 21.83a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14H4a1 1 0 0 1-.78-1.63l4.507-4.643"></path><path d="m2 2 20 20"></path></svg>';
  const SHIELD = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-shield-check h-3 w-3" aria-hidden="true"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path><path d="m9 12 2 2 4-4"></path></svg>';

  // ───────── 图:与现网 recharts 输出同一套取值(刻度 12px faint、网格 border、条 4px 带 surface 描边) ─────────
  function stackedBars(el, series, height, emptyHint) {
    const W = Math.max(280, Math.floor(el.clientWidth || 388));
    const H = height, L = 40, R = 16, T = 4, B = 30;
    const totals = Array.from({ length: DAYS }, (_, i) => FAM_KEYS.reduce((s, f) => s + series[f][i], 0));
    const peak = Math.max(...totals);
    if (!peak) { el.innerHTML = `<p class="tiny-meta" style="height:${H}px;display:grid;place-items:center;">${emptyHint}</p>`; return; }
    const mag = 10 ** Math.floor(Math.log10(peak));
    const top = [1, 2, 4, 5, 8, 10].map((k) => k * mag).find((v) => v >= peak);
    const ph = H - T - B, pw = W - L - R, slot = pw / DAYS;
    const bw = Math.max(4, Math.min(14, Math.round(slot * 0.34)));
    const y = (v) => T + ph - (v / top) * ph;
    let g = '';
    for (const v of [0, top / 2, top]) {
      g += `<line stroke="var(--dorami-border)" fill="none" x1="${L}" y1="${y(v)}" x2="${W - R}" y2="${y(v)}"></line>`;
      g += `<text font-size="12" x="${L - 8}" y="${y(v)}" text-anchor="end" fill="var(--dorami-faint)"><tspan dy="0.355em">${fmtNum(v)}</tspan></text>`;
    }
    for (let i = 0; i < DAYS; i++) {
      let acc = 0;
      const x = L + slot * i + (slot - bw) / 2;
      for (const f of FAM_KEYS) {
        const v = series[f][i];
        if (!v) continue;
        const y1 = y(acc + v), h = y(acc) - y1;
        g += `<path fill="${FAM[f][1]}" stroke="var(--dorami-surface)" stroke-width="1" d="M ${x.toFixed(2)},${y1.toFixed(2)} h ${bw} v ${h.toFixed(2)} h -${bw} Z"><title>${tick(i)} · ${FAM[f][0]} ${v}</title></path>`;
        acc += v;
      }
      if (i % 7 === 0 || i === DAYS - 1) {
        if (i === DAYS - 1 || DAYS - 1 - i > 3) g += `<text font-size="12" x="${(x + bw / 2).toFixed(2)}" y="${H - B + 8}" text-anchor="middle" fill="var(--dorami-faint)"><tspan dy="0.71em">${tick(i)}</tspan></text>`;
      }
    }
    el.innerHTML = `<div style="height:${H}px;"><svg class="recharts-surface" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="width:100%;height:100%;display:block;">${g}</svg></div>`;
  }

  function rankBars(el, rows, emptyHint) {
    if (!rows.length) { el.innerHTML = `<p class="tiny-meta" style="height:112px;display:grid;place-items:center;">${emptyHint}</p>`; return; }
    const W = Math.max(400, Math.floor(el.clientWidth || 1267));
    const pitch = 26, H = Math.max(112, rows.length * pitch);
    const step = H / rows.length, x0 = 104, span = W - x0 - 146, peak = rows[0].value;
    let g = '';
    rows.forEach((r, i) => {
      const cy = step * i + step / 2, w = Math.max(3, (r.value / peak) * span), y1 = cy - 4, x1 = x0 + w;
      g += `<text font-size="12" x="96" y="${cy}" text-anchor="end" fill="var(--dorami-faint)"><tspan dy="0.355em">${esc(truncLabel(r.name))}</tspan></text>`;
      g += `<path fill="var(--dorami-blue)" d="M${x0},${y1}L ${x1 - 3},${y1}A 3,3,0,0,1,${x1},${y1 + 3}L ${x1},${y1 + 5}A 3,3,0,0,1,${x1 - 3},${y1 + 8}L ${x0},${y1 + 8}Z"></path>`;
      g += `<text font-size="12" x="${x1 + 8}" y="${cy}" text-anchor="start" fill="var(--dorami-ink-2)"><tspan dy="0.355em">${r.value}</tspan></text>`;
    });
    el.innerHTML = `<div style="height:${H}px;"><svg class="recharts-surface" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="width:100%;height:100%;display:block;">${g}</svg></div>`;
  }

  // ───────── 账户页 ─────────
  function renderKpi() {
    const strip = $('section.kpi-strip[aria-label="窗口活跃概览"]');
    $('[data-new="kpi"]', strip)?.remove();
    const calls = ACCOUNTS.reduce((s, a) => s + a.api.calls, 0);
    const inUse = allTokens().filter((t) => t.calls > 0).length;
    strip.insertAdjacentHTML('beforeend', `<div class="kpi" data-new="kpi"><span class="kpi-num">${fmtNum(calls)}</span><span class="kpi-lbl">接口调用</span><span class="kpi-sub">${inUse} 枚令牌在用</span></div>`);
  }

  function renderTop() {
    const sec = $('section[aria-label="活跃用户 Top"]');
    const seg = $('.mini-seg', sec);
    seg.innerHTML = [['reads', '按阅读'], ['logins', '按登录'], ['api', '按接口']]
      .map(([k, l]) => `<button type="button" class="mini-seg-btn ${S.top === k ? 'is-on' : ''}" data-top="${k}">${l}</button>`).join('');
    const val = (a) => (S.top === 'api' ? a.api.calls : a[S.top]);
    const rows = ACCOUNTS.map((a) => ({ name: a.u, value: val(a) })).filter((r) => r.value > 0).sort((a, b) => b.value - a.value);
    rankBars($('[data-proto="top-chart"]', sec), rows, S.top === 'api' ? '窗口内还没有接口调用' : '窗口内还没有记录');
  }

  const SORTS = {
    username: (a) => a.u.toLowerCase(), last_login: (a) => a.last, logins: (a) => a.logins, reads: (a) => a.reads,
    ai_calls: (a) => a.ai_calls, api: (a) => a.api.calls, subscriptions: (a) => a.subs,
  };
  const TH_KEYS = [null, 'username', null, null, null, 'last_login', 'logins', 'reads', 'ai_calls', 'api', 'subscriptions'];

  function renderTable() {
    const table = $('#ops-accounts-table .acct-table');
    const head = $('thead tr', table);
    if (!$('[data-new="th-api"]', head)) {
      head.children[8].insertAdjacentHTML('afterend', '<th class="acct-th is-num" data-new="th-api" style="width: 76px;"><button type="button" class="acct-sortbtn" title="按接口排序">接口<span class="acct-sort-arrow" aria-hidden="true"></span></button></th>');
      Array.from(head.children).forEach((th, i) => { if (TH_KEYS[i] && i !== 1) th.dataset.sort = TH_KEYS[i]; });
    }
    $$('th[data-sort]', head).forEach((th) => {
      const on = th.dataset.sort === S.sort.k;
      th.classList.toggle('is-sorted', on);
      $('.acct-sort-arrow', th).textContent = on ? (S.sort.o === 'asc' ? '▲' : '▼') : '';
    });
    const get = SORTS[S.sort.k];
    const rows = [...ACCOUNTS].sort((a, b) => a.u.localeCompare(b.u)).sort((a, b) => { const x = get(a), y = get(b); return (x > y ? 1 : x < y ? -1 : 0) * (S.sort.o === 'asc' ? 1 : -1); });
    const n = (v, main) => `<td class="acct-n ${main ? 'is-main' : ''} ${v ? '' : 'is-zero'}">${v || '–'}</td>`;
    $('tbody', table).innerHTML = rows.map((a) => `
      <tr class="acct-row ${S.view === 'drawer' && S.user === a.u ? 'is-sel' : ''}" role="button" tabindex="0" aria-label="${esc(a.u)} 活动详情" data-user="${esc(a.u)}">
        <td><input aria-label="选择账户 ${esc(a.u)}" class="h-4 w-4 cursor-pointer rounded align-middle" type="checkbox"></td>
        <td><span class="acct-user">${avatar(a.u)}<span class="acct-name">${esc(a.u)}</span></span></td>
        <td>${a.role === 'admin' ? `<span class="sett-role-chip" style="color: var(--dorami-accent-ink);">${SHIELD} 管理员</span>` : '<span class="sett-role-chip">读者</span>'}</td>
        <td><span class="stamp stamp-ok">启用</span></td>
        <td>${a.ai ? `<span class="acct-ai-flag is-on" title="AI 已开启">${ZAP}</span>` : `<span class="acct-ai-flag" title="AI 未开启">${ZAP_OFF}</span>`}</td>
        <td><span class="acct-mono">${a.last}</span></td>
        ${n(a.logins)}${n(a.reads, true)}${n(a.ai_calls)}${n(a.api.calls)}
        <td class="acct-n">${a.subs}</td>
      </tr>`).join('');
  }

  // 方向 B:新板块「接口调用」= 每日走势 + 令牌台账(账户 → 令牌 → 接口 → 量,列序即关系)
  const LSORTS = { last: (t) => t.last, calls: (t) => t.calls, items: (t) => t.items };
  const LFILTERS = [['', '令牌'], ['feed', '聚合令牌'], ['sub', '订阅令牌'], ['gone', '已作废']];

  function renderBlock() {
    $('#ops-accounts-api')?.remove();
    if (S.dir !== 'b') return;
    const anchor = $('#ops-accounts-table');
    anchor.insertAdjacentHTML('beforebegin', `
      <section id="ops-accounts-api" class="ops-block" tabindex="-1" aria-label="接口调用">
        <div class="zone-head">
          <span class="zone-title">接口调用</span><span class="zone-hint">近 30 天</span>
          <span class="zone-acts mini-seg" role="group" aria-label="统计维度">
            ${[['calls', '调用'], ['items', '条数']].map(([k, l]) => `<button type="button" class="mini-seg-btn ${S.measure === k ? 'is-on' : ''}" data-measure="${k}">${l}</button>`).join('')}
          </span>
        </div>
        <section class="surface-card card-pad rounded-[var(--r-card)]">${legend(FAM_KEYS)}<div data-proto="api-chart"></div></section>
        <section class="surface-card rounded-[var(--r-card)] overflow-hidden" style="margin-top: 12px;" data-proto="api-ledger"></section>
      </section>`);
    stackedBars($('[data-proto="api-chart"]'), famSeries(allTokens(), S.measure), 200, '窗口内还没有接口调用');

    let rows = allTokens().filter((t) => (S.lfilter === 'gone' ? t.gone : S.lfilter ? t.kind === S.lfilter && !t.gone : true));
    const get = LSORTS[S.lsort.k];
    rows = rows.sort((a, b) => a.owner.localeCompare(b.owner)).sort((a, b) => { const x = get(a), y = get(b); return (x > y ? 1 : x < y ? -1 : 0) * (S.lsort.o === 'asc' ? 1 : -1); });
    const sortTh = (k, label, w, num) => `<th class="acct-th ${num ? 'is-num' : ''} ${S.lsort.k === k ? 'is-sorted' : ''}" style="width: ${w}px;" data-lsort="${k}"><button type="button" class="acct-sortbtn" title="按${label}排序">${label}<span class="acct-sort-arrow" aria-hidden="true">${S.lsort.k === k ? (S.lsort.o === 'asc' ? '▲' : '▼') : ''}</span></button></th>`;
    const fLabel = LFILTERS.find(([v]) => v === S.lfilter)[1];
    const mix = (t) => {
      if (!t.calls) return '<span class="acct-n is-zero">–</span>';
      const fams = FAM_KEYS.map((f) => [f, t.chs.filter((c) => c.fam === f).reduce((s, c) => s + c.calls, 0)]).filter(([, v]) => v);
      const tip = t.chs.map((c) => `${c.name} ${c.calls}`).join('\n');
      return `<div class="tok-mix" title="${esc(tip)}"><div class="tok-mix-top"><span class="tok-ch-name">${esc(t.chs[0].name)}</span>${t.chs.length > 1 ? `<span class="tok-mix-more">+${t.chs.length - 1}</span>` : ''}</div><div class="seg-bar">${fams.map(([f, v]) => `<i style="flex: ${v} 1 0; background: ${FAM[f][1]};"></i>`).join('')}</div></div>`;
    };
    $('[data-proto="api-ledger"]').innerHTML = rows.length ? `
      <div class="acct-scroll"><table class="acct-table is-fixed">
        <thead><tr>
          <th class="acct-th"><button type="button" class="acct-sortbtn" title="搜索用户名">账户<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-search h-2.5 w-2.5" aria-hidden="true"><path d="m21 21-4.34-4.34"></path><circle cx="11" cy="11" r="8"></circle></svg></button></th>
          <th class="acct-th ${S.lfilter ? 'is-filtered' : ''}" style="width: 300px;" data-lfilter><button type="button" class="acct-sortbtn" data-widest="聚合令牌" title="点击轮换筛选：令牌 → 聚合令牌 → 订阅令牌 → 已作废">${fLabel}</button></th>
          <th class="acct-th" style="width: 300px;">接口</th>
          ${sortTh('last', '最近调用', 124)}${sortTh('calls', '调用', 88, true)}${sortTh('items', '条数', 96, true)}
        </tr></thead>
        <tbody>${rows.map((t) => `
          <tr class="acct-row" role="button" tabindex="0" aria-label="${esc(t.owner)} · ${esc(kindLabel(t))} ${esc(t.prev)}" data-user="${esc(t.owner)}" data-tok="${esc(t.id)}">
            <td><span class="acct-user">${avatar(t.owner)}<span class="acct-name">${esc(t.owner)}</span></span></td>
            <td>${tokId(t)}</td>
            <td>${mix(t)}</td>
            <td><span class="acct-mono">${t.last || '–'}</span></td>
            <td class="acct-n is-main ${t.calls ? '' : 'is-zero'}">${t.calls || '–'}</td>
            <td class="acct-n ${t.items ? '' : 'is-zero'}">${t.items || '–'}</td>
          </tr>`).join('')}</tbody>
      </table></div>` : '<p class="p-6 text-center tiny-meta">没有匹配当前筛选的令牌。</p>';
  }

  // ───────── 单用户抽屉 ─────────
  let drawerStatic = null;
  function renderDrawer() {
    const aside = $('[data-proto="drawer"]');
    const scrim = aside.previousElementSibling;
    const open = S.view === 'drawer';
    aside.classList.toggle('is-open', open); scrim.classList.toggle('is-open', open);
    aside.setAttribute('aria-hidden', String(!open));
    if (!open) return;
    const acc = ACCOUNTS.find((a) => a.u === S.user) || ACCOUNTS[0];
    const body = $('.ledger-drawer-body', aside);
    if (!drawerStatic) {
      // 快照里原有的四块:数字格 / 登录 / 各源阅读收藏 / 每日 AI 用量。后两块是 admin 的真实画面,各账号共用(样页的简化)。
      drawerStatic = { login: $('.login-card', body).outerHTML, engage: body.children[2].outerHTML, ai: body.children[3].outerHTML };
    }
    aside.setAttribute('aria-label', `${acc.u} · 活动详情`);
    const head = $('.ledger-drawer-head', aside);
    $('.acct-avatar', head).outerHTML = avatar(acc.u);
    $('.ledger-drawer-title', head).textContent = acc.u;

    const toks = acc.shown;
    let api = '';
    if (toks.length) {
      const title = `接口调用${acc.api.calls ? ` · 近 30 天 ${acc.api.calls} 次` : ''}${toks.length > 1 ? ` · ${toks.length} 枚令牌` : ''}`;
      api = `<div data-new="drawer-api"><div class="drawer-sec-title">${title}</div><div class="tok-list">${toks.map((t, i) => {
        const peak = t.chs[0]?.calls || 1;
        const meta = t.calls ? `最近调用 ${t.last}${t.gone && t.rejected ? ` · 作废后仍被调 ${t.rejected} 次` : ''}` : '未调用';
        const chs = t.chs.map((c) => `
          <div class="rounded-[var(--r-sm)] px-1.5 py-1.5">
            <div class="flex items-center justify-between gap-2"><span class="tok-ch-name" title="${esc(c.name)}">${esc(c.name)}</span><span class="shrink-0 tiny-meta tabular-nums text-slate-500">${c.calls} 次 · ${c.items} 条</span></div>
            <div class="mt-1"><div class="h-1.5 overflow-hidden rounded-r-full bg-[var(--dorami-well)]"><div class="h-full rounded-r-full" style="width: ${(c.calls / peak) * 100}%; background: ${FAM[c.fam][1]};"></div></div></div>
          </div>`).join('');
        const openAttr = (S.flashTok ? S.flashTok === t.id : i === 0) && t.calls ? 'open' : '';
        return `<details class="tok-card ${S.flashTok === t.id ? 'is-flash' : ''}" ${openAttr} data-tok="${esc(t.id)}">
          <summary>${tokId(t)}<span class="tok-n ${t.calls ? '' : 'is-zero'}">${t.calls || '–'}</span><span class="tok-meta">${meta}</span><span class="tok-items">${t.items ? `${t.items} 条` : ''}</span></summary>
          ${chs ? `<div class="tok-chs">${chs}</div>` : ''}
        </details>`;
      }).join('')}</div></div>`;
      if (acc.api.calls) {
        const fams = FAM_KEYS.filter((f) => toks.some((t) => t.byFam[f].calls.some(Boolean)));
        api += `<div class="surface-card card-pad rounded-[var(--r-card)]" data-new="drawer-api-chart">
          <div class="card-head"><span class="card-title">每日接口调用</span><span class="mini-seg" role="group" aria-label="统计维度" style="margin-left: auto;">${[['calls', '调用'], ['items', '条数']].map(([k, l]) => `<button type="button" class="mini-seg-btn ${S.dMeasure === k ? 'is-on' : ''}" data-dmeasure="${k}">${l}</button>`).join('')}</span></div>
          ${legend(fams)}<div data-proto="drawer-chart"></div></div>`;
      }
    }
    const quiet = !acc.reads && !acc.ai_calls;
    const login = drawerStatic.login
      .replace(/(<span class="tile-num"[^>]*>)\d+/, `$1${acc.logins}`)
      .replace(/最近 [\d\- :]+/, `最近 ${acc.last}`);
    body.innerHTML = `
      <div class="tiles">
        <div class="tile"><div class="tile-num">${fmtNum(acc.reads)}</div><div class="tile-lbl">近 30 天阅读</div></div>
        <div class="tile"><div class="tile-num">${fmtNum(acc.ai_calls)}</div><div class="tile-lbl">近 30 天 AI 调用</div></div>
        <div class="tile"><div class="tile-num">${fmtNum(acc.subs)}</div><div class="tile-lbl">订阅来源</div></div>
      </div>
      ${login}${api}
      ${quiet ? (api ? '' : '<p class="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">近 30 天该用户没有阅读 / 收藏 / AI 调用记录。</p>') : drawerStatic.engage + drawerStatic.ai}`;
    const chart = $('[data-proto="drawer-chart"]', body);
    if (chart) stackedBars(chart, famSeries(toks, S.dMeasure), 200, '');
    const flash = S.flashTok && $(`.tok-card[data-tok="${CSS.escape(S.flashTok)}"]`, body);
    if (flash) flash.scrollIntoView({ block: 'center' });
  }

  // ───────── 设置 → 聚合接口 ─────────
  let settingsNow = null;
  function renderSettings() {
    const overlay = $('[data-proto="settings"]');
    overlay.hidden = S.view !== 'settings';
    if (overlay.hidden) return;
    const box = $('.sett-scroll > div', overlay);
    if (settingsNow == null) settingsNow = box.innerHTML;
    box.innerHTML = settingsNow;
    if (S.card === 'now') return;
    const me = ACCOUNTS.find((a) => a.u === 'admin');
    const t = me.shown.find((x) => !x.gone);
    const used = t && t.calls ? ` · 最近调用 ${t.last} · 近 30 天 ${t.calls} 次` : '';
    $('.token-meta', box).textContent = `签发于 2026-06-17${used}`;
    if (S.card !== 'b') return;
    const n = (name) => { const v = t?.ch[name]?.calls || 0; return `<span class="endpoint-n ${v ? '' : 'is-zero'}" title="近 30 天调用">${v ? `${v} 次` : '–'}</span>`; };
    const eps = $$('.endpoint', box);
    eps[0].querySelector('.copybtn').insertAdjacentHTML('beforebegin', n('feed/articles'));
    eps[1].querySelector('.copybtn').insertAdjacentHTML('beforebegin', n('feed/articles.md'));
    const chip = $('.card-head .target-chip', box);
    const scope = chip.textContent; chip.remove();
    eps[0].insertAdjacentHTML('beforebegin', `<div class="endpoint-group"><span class="sett-lbl">文章</span><span class="target-chip">${scope}</span></div>`);
    const evt = eps[1].cloneNode(true);
    $('.endpoint-url', evt).textContent = 'https://dorami.example/api/public/feed/events';
    $('.endpoint-url', evt).title = 'https://dorami.example/api/public/feed/events';
    $('.endpoint-n', evt).outerHTML = n('feed/events');
    $('.copybtn', evt).title = '复制热点事件接口地址';
    eps[1].insertAdjacentHTML('afterend', '<div class="endpoint-group"><span class="sett-lbl">热点事件</span><span class="target-chip">全站</span></div>');
    eps[1].nextElementSibling.insertAdjacentElement('afterend', evt);
  }

  // ───────── 样页开关 ─────────
  function renderBar() {
    $('.px-bar')?.remove();
    if (S.bare) return;
    const grp = (lbl, key, opts) => `<span class="px-grp"><span class="px-lbl">${lbl}</span>${opts.map(([v, l]) => `<button type="button" class="px-btn ${S[key] === v ? 'is-on' : ''}" data-px="${key}:${v}">${l}</button>`).join('')}</span>`;
    document.body.insertAdjacentHTML('beforeend', `<div class="px-bar" role="group" aria-label="样页开关(不属于产品界面)">
      ${grp('方向', 'dir', [['a', 'A 并入现有'], ['b', 'B 加令牌台账']])}<span class="px-sep"></span>
      ${grp('看', 'view', [['page', '账户页'], ['drawer', '账户抽屉'], ['settings', '设置卡']])}
      ${S.view === 'settings' ? `<span class="px-sep"></span>${grp('设置卡', 'card', [['now', '现网'], ['a', 'A'], ['b', 'B']])}` : ''}<span class="px-sep"></span>
      ${grp('数据', 'data', [['live', '常态'], ['empty', '尚无调用']])}<span class="px-sep"></span>
      ${grp('', 'theme', [['light', '亮'], ['dark', '暗']])}
    </div>`);
  }

  function syncUrl() {
    if (S.bare) return;
    const p = new URLSearchParams({ dir: S.dir, view: S.view, state: S.data, theme: S.theme });
    if (S.view === 'drawer') p.set('user', S.user);
    if (S.view === 'settings') p.set('card', S.card);
    history.replaceState(null, '', `?${p}`);
  }

  function render() {
    document.documentElement.dataset.theme = S.theme;
    build();
    renderKpi(); renderTop(); renderTable(); renderBlock(); renderDrawer(); renderSettings(); renderBar(); syncUrl();
  }

  // ───────── 事件 ─────────
  document.addEventListener('click', (e) => {
    const t = e.target;
    const px = t.closest('[data-px]');
    if (px) { const [k, v] = px.dataset.px.split(':'); S[k] = v; S.flashTok = ''; render(); return; }
    if (t.closest('.px-bar')) return;
    const top = t.closest('[data-top]'); if (top) { S.top = top.dataset.top; renderTop(); return; }
    const m = t.closest('[data-measure]'); if (m) { S.measure = m.dataset.measure; renderBlock(); return; }
    const dm = t.closest('[data-dmeasure]'); if (dm) { S.dMeasure = dm.dataset.dmeasure; renderDrawer(); return; }
    const lf = t.closest('[data-lfilter]'); if (lf) { const i = LFILTERS.findIndex(([v]) => v === S.lfilter); S.lfilter = LFILTERS[(i + 1) % LFILTERS.length][0]; renderBlock(); return; }
    const ls = t.closest('[data-lsort]'); if (ls) { const k = ls.dataset.lsort; S.lsort = { k, o: S.lsort.k === k && S.lsort.o === 'desc' ? 'asc' : 'desc' }; renderBlock(); return; }
    const th = t.closest('th[data-sort]'); if (th) { const k = th.dataset.sort; S.sort = { k, o: S.sort.k === k ? (S.sort.o === 'asc' ? 'desc' : 'asc') : 'desc' }; renderTable(); return; }
    if (t.closest('input[type="checkbox"]')) return;
    const row = t.closest('tr[data-user]');
    if (row) { S.view = 'drawer'; S.user = row.dataset.user; S.flashTok = row.dataset.tok || ''; render(); return; }
    if (t.closest('[aria-label="关闭详情"]') || t.classList.contains('ledger-scrim')) { S.view = 'page'; S.flashTok = ''; render(); return; }
    if (t.closest('[aria-label="关闭设置"]') || t === $('[data-proto="settings"]')) { S.view = 'page'; render(); return; }
    if (t.closest('.app-rail [aria-label="设置"]')) { S.view = 'settings'; render(); }
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.view !== 'page') { S.view = 'page'; S.flashTok = ''; render(); } });
  let rz = 0;
  window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { renderTop(); renderBlock(); renderDrawer(); }, 120); });

  render();
  if (S.focus) {
    const target = { api: '#ops-accounts-api', table: '#ops-accounts-table', top: '#ops-accounts-top' }[S.focus];
    const el = target && $(target);
    if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 18);
  }
})();
