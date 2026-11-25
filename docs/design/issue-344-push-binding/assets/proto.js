/* issue #344 样页:示意数据 + 新增部分的渲染 + 样页开关。底页是现网 DOM,这里只在 state=new 时往上加东西。 */
(function () {
  const q = new URLSearchParams(location.search);
  const root = document.documentElement;
  const page = root.dataset.page; // tokens | sources | sources-x
  const state = q.get('state') === 'now' ? 'now' : 'new';
  const open = q.get('open') || '';
  const self = q.get('self') === '1';
  root.dataset.theme = q.get('theme') === 'dark' ? 'dark' : 'light';
  if (q.has('bare')) root.dataset.bare = '';

  const svg = (paths) => `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const IC = {
    plus: svg('<path d="M5 12h14"/><path d="M12 5v14"/>'),
    x: svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
    key: svg('<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/>'),
  };
  const BTN = 'action-button min-h-[32px] px-3 text-xs';

  // ── 示意数据:哪个令牌绑定了哪些来源 ──
  const TOKENS = {
    'grok-bot': { preview: '...kqLicw', active: true },
    'research-agent': { preview: '...0rnE1s', active: true },
    'old-crawler': { preview: '...k2Gve8', active: false },
  };
  const SOURCES = {
    push_grok_picks: 'Grok 推荐',
    x_nova_labs: 'X · Nova Labs',
    x_agentweekly: 'X · Agent Weekly',
    x_openai: 'X · OpenAI',
    web_blog_fieldnotes_example: 'Field Notes',
  };
  const BINDINGS = [
    ['grok-bot', 'push_grok_picks', '12 分钟前'],
    ['grok-bot', 'x_nova_labs', '30 分钟前'],
    ['grok-bot', 'x_agentweekly', '1 小时前'],
    ['grok-bot', 'x_openai', '15 分钟前'],
    ['research-agent', 'web_blog_fieldnotes_example', '2 小时前'],
  ];
  const ofToken = (actor) => BINDINGS.filter((b) => b[0] === actor);
  const ofSource = (sid) => BINDINGS.filter((b) => b[1] === sid);

  const bindRow = (name, mono, time) => `
    <div class="bind-row">
      <span class="bind-main"><span class="acct-name" title="${name}">${name}</span><span class="acct-mono">${mono}</span></span>
      <span class="acct-mono bind-time">${time}</span>
      <button type="button" class="rowact-btn is-danger" title="解绑 ${name}" aria-label="解绑 ${name}">${IC.x}</button>
    </div>`;

  // ── 抽屉(现网 SyncDrawerShell 的标记) ──
  function closeDrawer() {
    document.querySelectorAll('[data-proto-drawer]').forEach((n) => n.remove());
  }
  function openDrawer({ icon, title, small = '', stamp = '', label, body, foot = '' }) {
    closeDrawer();
    const wrap = document.createElement('div');
    wrap.dataset.protoDrawer = '';
    wrap.innerHTML = `
      <div class="ledger-scrim is-open" aria-hidden="true"></div>
      <aside class="ledger-drawer sync-drawer is-open" role="dialog" aria-modal="true" aria-label="${label}" tabindex="-1">
        <div class="ledger-drawer-head">
          <span class="sync-drawer-ic">${icon}</span>
          <span class="ledger-drawer-title">${title}${small ? `<small>${small}</small>` : ''}</span>
          ${stamp}
          <div class="drawer-acts"><span class="ai-divider"></span><button type="button" class="rowact-btn" title="关闭" aria-label="关闭">${IC.x}</button></div>
        </div>
        <div class="ledger-drawer-body">${body}</div>
        ${foot ? `<div class="ledger-drawer-foot">${foot}</div>` : ''}
      </aside>`;
    document.body.appendChild(wrap);
    wrap.querySelector('.ledger-scrim').addEventListener('click', closeDrawer);
    wrap.querySelector('.drawer-acts .rowact-btn').addEventListener('click', closeDrawer);
    return wrap;
  }
  const switchBtn = (on, label) => `<button type="button" class="inspector-visibility-toggle" role="switch" aria-checked="${on}" aria-label="${label}"><span class="ledger-switch ${on ? 'is-on' : ''}" aria-hidden="true"></span></button>`;

  // ── 运维 → 自定源与收录:令牌表加「来源」列,点一行开抽屉 ──
  function tokenDrawer(actor) {
    const token = TOKENS[actor];
    const rows = ofToken(actor).map(([, sid, time]) => bindRow(SOURCES[sid], sid, time)).join('');
    const options = Object.entries(SOURCES)
      .filter(([sid]) => !ofToken(actor).some((b) => b[1] === sid))
      .map(([sid, name]) => `<option value="${sid}">${name}</option>`).join('');
    const add = token.active ? `
      <div class="bind-add">
        <select class="form-input form-input-inline" aria-label="来源"><option value="">来源</option>${options}</select>
        <button type="button" class="${BTN} action-button-secondary">绑定</button>
      </div>` : '';
    const selfRow = self && token.active ? `
      <section><div class="reg-switch"><span>自助登记来源</span>${switchBtn(actor === 'grok-bot', '自助登记来源')}</div></section>` : '';
    openDrawer({
      icon: IC.key,
      title: actor,
      small: token.preview,
      stamp: token.active ? '<span class="stamp stamp-ok">有效</span>' : '<span class="stamp stamp-idle">已吊销</span>',
      label: `${actor} · 来源`,
      body: `<section><div class="drawer-sec-title">来源</div><div class="bind-list">${rows}</div>${add}</section>${selfRow}`,
    });
  }
  function tokensPage() {
    const table = document.querySelector('section[aria-label="收录令牌列表"] table');
    const headRow = table.querySelector('thead tr');
    const th = document.createElement('th');
    th.className = 'acct-th';
    th.textContent = '来源';
    headRow.insertBefore(th, headRow.children[2]);
    [160, 110, null, 80, 140, 140, 90, 56].forEach((w, i) => { headRow.children[i].style.width = w ? `${w}px` : ''; });
    table.querySelectorAll('tbody tr').forEach((tr) => {
      const actor = tr.querySelector('.acct-name').textContent.trim();
      const names = ofToken(actor).map((b) => SOURCES[b[1]]);
      const td = document.createElement('td');
      td.innerHTML = names.length
        ? `<span class="tok-sources" title="${names.join(' · ')}">${names.join(' · ')}</span>`
        : '<span class="tok-sources is-none">–</span>';
      tr.insertBefore(td, tr.children[2]);
      tr.classList.remove('is-static');
      tr.tabIndex = 0;
      tr.addEventListener('click', (event) => { if (!event.target.closest('.rowacts')) tokenDrawer(actor); });
    });
    if (open === 'token') tokenDrawer(q.get('actor') || 'grok-bot');
  }

  // ── 源管理:行上的获取方式(抓取 / 推送)、检视器的「抓取」「推送」两区、登记来源 ──
  const CHANNEL = { x_user_timeline: 'X 时间线' };
  const watchPill = '<span class="tier-pill incubating-pill" title="观察期">观察</span>';
  const tempPill = '<span class="tier-pill provisional-pill" title="临时源:未列入目录">临时</span>';
  const allRows = () => Array.from(document.querySelectorAll('.board-node'));
  const rowOf = (sid) => allRows().find((n) => n.querySelector('.board-node-sid')?.textContent.trim() === sid);
  const pushersOf = (sid) => ofSource(sid).map((b) => b[0]).join('、');

  // 现网这一格写的是内容形态(与上面的页签重复),title 里带着采集通道;样页改成获取方式。
  function fetcherLabelOf(node) {
    const raw = (node.querySelector('.board-node-type').title.split('采集通道：')[1] || '').trim();
    return CHANNEL[raw] || raw;
  }
  function setAcq(node, kind, title) {
    const chip = node.querySelector('.board-node-type');
    chip.textContent = { fetch: '抓取', push: '推送', both: '抓取 · 推送', none: '无抓取' }[kind];
    chip.title = title;
    chip.classList.toggle('acq-none', kind === 'none');
  }
  function setRow(node, { pills, time, res, listed }) {
    const name = node.querySelector('.board-node-name');
    name.querySelectorAll('.tier-pill').forEach((p) => p.remove());
    name.insertAdjacentHTML('beforeend', pills.join(''));
    if (time) node.querySelector('.board-node-last-time').textContent = time;
    if (res) node.querySelector('.board-node-last-res').textContent = res;
    if (listed) node.querySelectorAll('.board-node-act').forEach((b) => b.remove());
  }
  function trend(values) {
    const max = Math.max(...values, 1);
    return values.map((v, i) => {
      const h = v ? Math.max(3, Math.round((v / max) * 14)) : 2;
      const fill = v ? 'fill="var(--dorami-blue)" fill-opacity="0.85"' : 'fill="var(--dorami-border-strong)" fill-opacity="0.6"';
      return `<rect x="${i * 7}" y="${14 - h}" width="5" height="${h}" rx="1" ${fill}></rect>`;
    }).join('');
  }
  function pushSection(sid) {
    const rows = ofSource(sid).map(([actor, , time]) => bindRow(actor, TOKENS[actor].preview, time)).join('');
    const options = Object.entries(TOKENS)
      .filter(([actor, t]) => t.active && !ofSource(sid).some((b) => b[0] === actor))
      .map(([actor, t]) => `<option>${actor} · ${t.preview}</option>`).join('');
    return `
      <section class="acq-panel" data-proto="push">
        <div class="acq-panel-head"><h3 class="tools-title">推送</h3></div>
        <div class="acq-panel-body is-list">
          <div class="bind-list">${rows}</div>
          <div class="bind-add">
            <select class="node-param-input inspector-param-select" aria-label="令牌"><option value="">令牌</option>${options}</select>
            <button type="button" class="${BTN} action-button-quiet">绑定</button>
          </div>
        </div>
      </section>`;
  }

  const REG = { kind: 'social', scope: 'open' };
  function registerDrawer() {
    const seg = (name, options, value) => `
      <div><span class="form-label">${name}</span>
        <div class="segmented-control" data-seg="${name}">
          ${options.map(([k, label]) => `<button type="button" data-k="${k}" class="segmented-option ${k === value ? 'segmented-option-active' : ''}">${label}</button>`).join('')}
        </div>
      </div>`;
    const field = (label, attrs) => `<label><span class="form-label">${label}</span><input class="form-input form-input-inline" ${attrs}></label>`;
    const scopes = REG.kind === 'social' ? [['one', '一个账号'], ['open', '不限']] : REG.kind === 'article' ? [['one', '一个站点'], ['open', '不限']] : null;
    const scope = scopes ? REG.scope : 'one';
    const sample = { social: 'Grok 推荐', article: 'Grok 读到的文章', podcast: '' }[REG.kind];
    const identity = REG.kind === 'podcast'
      ? field('feed', 'placeholder="https://" aria-label="feed"')
      : scope === 'open'
        ? `<label><span class="form-label">源 id</span><span class="reg-id"><span>push_</span><input class="form-input form-input-inline" value="${REG.kind === 'social' ? 'grok_picks' : 'grok_reads'}" aria-label="源 id"></span></label>`
        : REG.kind === 'social'
          ? field('账号', 'placeholder="@handle" aria-label="账号"')
          : field('站点或 feed', 'placeholder="https://" aria-label="站点或 feed"');
    const tokens = Object.entries(TOKENS).filter(([, t]) => t.active)
      .map(([actor, t]) => `<option>${actor} · ${t.preview}</option>`).join('');
    const wrap = openDrawer({
      icon: IC.plus,
      title: '登记来源',
      label: '登记来源',
      body: `
        <div class="reg-form">
          ${seg('类型', [['social', '推文'], ['article', '文章'], ['podcast', '播客']], REG.kind)}
          ${scopes ? seg('出处', scopes, scope) : ''}
          ${field('名称', `value="${scope === 'open' ? sample : ''}" aria-label="名称"`)}
          ${identity}
          ${field('头像', 'placeholder="https://" aria-label="头像"')}
          <label><span class="form-label">令牌</span><select class="form-input form-input-inline" aria-label="令牌">${tokens}<option>不绑定</option></select></label>
          <div class="reg-switch"><span>进发现页</span>${switchBtn(true, '进发现页')}</div>
        </div>`,
      foot: `<span class="flex-1"></span><button type="button" class="${BTN} action-button-primary">登记</button>`,
    });
    wrap.querySelectorAll('[data-seg] button').forEach((button) => {
      button.addEventListener('click', () => {
        if (button.parentElement.dataset.seg === '类型') REG.kind = button.dataset.k;
        else REG.scope = button.dataset.k;
        registerDrawer();
      });
    });
    wrap.querySelectorAll('[role=switch]').forEach((button) => {
      button.addEventListener('click', () => {
        const on = button.getAttribute('aria-checked') !== 'true';
        button.setAttribute('aria-checked', String(on));
        button.firstElementChild.classList.toggle('is-on', on);
      });
    });
  }

  function sourcesPage() {
    // 页头:登记来源
    const acts = document.querySelector('.page-head-actions');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `${BTN} action-button-quiet`;
    button.innerHTML = `${IC.plus} 登记来源`;
    button.addEventListener('click', registerDrawer);
    acts.insertBefore(button, acts.firstChild);

    // 「收录」组多一行不限出处的源(从现网的一行克隆,再改内容)
    const nova = rowOf('x_nova_labs');
    const grokWrap = nova.parentElement.cloneNode(true);
    const grok = grokWrap.querySelector('.board-node');
    grok.classList.remove('board-node-sel');
    grok.querySelector('.board-node-name').title = 'Grok 推荐';
    grok.querySelector('.board-node-name-text').textContent = 'Grok 推荐';
    const sid = grok.querySelector('.board-node-sid');
    sid.textContent = 'push_grok_picks';
    sid.title = 'push_grok_picks';
    grok.querySelector('.board-node-trend svg').innerHTML = trend([3, 5, 2, 6, 4, 7, 10]);
    const group = nova.closest('.board-group');
    group.insertBefore(grokWrap, group.querySelector('.board-group-head').nextSibling);

    // 每一行:形态签改成获取方式。有抓取器的写「抓取」,没有的写「无抓取」(原来名字后面的「无采集」徽标拿掉)。
    allRows().forEach((node) => {
      const unbound = node.querySelector('.unbound-pill');
      if (!unbound) { setAcq(node, 'fetch', `抓取器:${fetcherLabelOf(node)}`); return; }
      unbound.remove();
      setAcq(node, 'none', '没有抓取器,也没有推送');
      const res = node.querySelector('.board-node-last-res');
      res.textContent = res.textContent.replace('无采集 · ', '');
      node.title = node.title.replace('无采集绑定', '无抓取');
    });
    // 有推送绑定的四行
    setRow(grok, { pills: [watchPill], time: '12 分钟前', res: '累计 37 则', listed: true });
    setRow(nova, { pills: [watchPill], time: '30 分钟前', res: '累计 2 则', listed: true });
    setRow(rowOf('x_agentweekly'), { pills: [tempPill], time: '1 小时前', res: '累计 1 则' });
    ['push_grok_picks', 'x_nova_labs', 'x_agentweekly'].forEach((id) => {
      const node = rowOf(id);
      setAcq(node, 'push', `推送:${pushersOf(id)}`);
      node.title = `收录 · 推送:${pushersOf(id)}`;
    });
    const openai = rowOf('x_openai');
    setAcq(openai, 'both', `抓取器:X 时间线 · 推送:${pushersOf('x_openai')}`);

    // 计数跟着多出来的一行
    const bump = (el) => { if (el) el.textContent = String(Number(el.textContent) + 1); };
    bump(document.querySelector('.node-shape-tab.is-active .node-shape-count'));
    document.querySelectorAll('.signal-stat').forEach((stat) => {
      const label = stat.querySelector('.signal-stat-label')?.textContent.trim();
      if (['全部', '未运行', '收录'].includes(label)) bump(stat.querySelector('.signal-stat-n'));
    });
    group.querySelector('.board-group-count').textContent = `${group.querySelectorAll('.board-node').length} 个源`;

    // 检视器:「抓取」「推送」并列两区
    const inspector = document.querySelector('.board-inspector');
    const dot = inspector.querySelector('.inspector-sid-dot');
    if (page === 'sources') {
      inspector.querySelector('.inspector-stamp').textContent = '收录';
      inspector.querySelector('.inspector-head .signal-dot').title = '推送';
      dot.textContent = '社交 · 收录';
      const list = Array.from(inspector.querySelectorAll('.inspector-act')).find((b) => b.textContent.includes('列入'));
      if (list) list.remove();
      inspector.querySelector('.inspector-hint').outerHTML = pushSection('x_nova_labs');
    } else {
      dot.textContent = '社交 · X 时间线';
      const sections = Array.from(inspector.querySelectorAll('.inspector-section'));
      const titled = (text) => sections.find((sec) => sec.querySelector('.micro-label')?.textContent.trim() === text);
      // 「抓取」一个框:现网的「运行参数」区、动作钮、「近期运行」区都收进去;「近期运行」降成框内小节
      const params = titled('运行参数');
      const runs = titled('近期运行');
      const panel = document.createElement('section');
      panel.className = 'acq-panel';
      panel.innerHTML = '<div class="acq-panel-head"><h3 class="tools-title">抓取</h3><span class="acq-fetcher">X 时间线</span></div><div class="acq-panel-body"></div>';
      const body = panel.querySelector('.acq-panel-body');
      params.before(panel);
      params.querySelector('.inspector-section-head').remove();
      body.append(...params.childNodes);
      params.remove();
      body.append(...inspector.querySelectorAll('.inspector-body > .inspector-actions, .inspector-body > .inspector-preview'));
      runs.className = 'acq-sec';
      body.append(runs);
      const review = titled('源审查');
      review.querySelector('.micro-label').className = 'tools-title';
      review.insertAdjacentHTML('beforebegin', pushSection('x_openai'));
    }
    if (open === 'register') {
      if (q.get('reg') === 'one') REG.scope = 'one';
      if (q.get('kind')) REG.kind = q.get('kind');
      registerDrawer();
    }
  }

  if (state === 'new') {
    if (page === 'tokens') tokensPage();
    else sourcesPage();
  }

  // ── 样页开关(底部深色条,不属于产品界面) ──
  const link = (label, changes, on) => {
    const next = new URLSearchParams(q);
    Object.entries(changes).forEach(([k, v]) => { if (v === null) next.delete(k); else next.set(k, v); });
    const query = next.toString();
    return `<a class="${on ? 'on' : ''}" href="${location.pathname}${query ? `?${query}` : ''}">${label}</a>`;
  };
  const pageLink = (label, file) => `<a class="${location.pathname.endsWith(`/${file}`) ? 'on' : ''}" href="${file}${location.search}">${label}</a>`;
  const opens = page === 'tokens'
    ? link('关', { open: null }, !open) + link('令牌的来源', { open: 'token' }, open === 'token')
    : link('关', { open: null }, !open) + link('登记来源', { open: 'register' }, open === 'register');
  const bar = document.createElement('div');
  bar.className = 'px-bar';
  bar.innerHTML = `
    <span><b>看哪里</b>${pageLink('收录令牌', 'tokens.html')}${pageLink('源管理 · 只推送', 'sources.html')}${pageLink('源管理 · 又抓又推', 'sources-x.html')}</span>
    <span><b>版本</b>${link('现网', { state: 'now', open: null }, state === 'now')}${link('样页', { state: null }, state === 'new')}</span>
    ${state === 'new' ? `<span><b>抽屉</b>${opens}</span>` : ''}
    ${state === 'new' && page === 'tokens' ? `<span><b>自助登记</b>${link('不含', { self: null }, !self)}${link('含', { self: '1' }, self)}</span>` : ''}
    <span><b>明暗</b>${link('明', { theme: null }, root.dataset.theme === 'light')}${link('暗', { theme: 'dark' }, root.dataset.theme === 'dark')}</span>`;
  document.body.appendChild(bar);
})();
