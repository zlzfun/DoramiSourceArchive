/* issue #72 样页:示意数据 + 各方向对现网 DOM 的改动 + 样页开关。
   底页是现网(主干 b4836bac)的 DOM;?dir=now 时除了导轨能点,什么都不改。
   参数:dir=now|a|b|c  page=brief|runs|fetch  state=ok|run|bad  flow=brief|picks  theme=light|dark  bare */
(() => {
  'use strict';

  const Q = new URLSearchParams(location.search);
  const pick = (key, allowed) => (allowed.includes(Q.get(key)) ? Q.get(key) : allowed[0]);
  const DIR = pick('dir', ['now', 'a', 'b', 'c']);
  const STATE = pick('state', ['ok', 'run', 'bad']);
  const THEME = pick('theme', ['light', 'dark']);
  let FLOW = pick('flow', ['brief', 'picks']);
  let PAGE = pick('page', DIR === 'c' ? ['runs', 'fetch'] : ['brief', 'runs', 'fetch']);

  const root = document.documentElement;
  root.dataset.theme = THEME;
  root.dataset.dir = DIR;
  if (Q.has('bare')) root.dataset.bare = '';

  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
  const h = (html) => {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  };
  const panel = (name) => $(`.tab-panel[data-tab="${name}"]`);

  /* ── 示意数据 ───────────────────────────────────────────────────────── */
  // 日报的历史行是开发库副本里的真实记录(日期平移到今天);精读这一个流程还不存在,它的数字全是示意。
  const FLOWS = {
    brief: {
      name: '哆啦美·AI资讯日报', sid: 'dorami_daily_brief', cron: '30 8 * * *', next: '16:18:19', total: 41,
      dots: ['ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok'], last: 'ok', running: false, note: null,
      runs: [['emerald', '成功', '+1', '11-26'], ['emerald', '成功', '+1', '11-25'], ['emerald', '成功', '+1', '11-24'], ['emerald', '成功', '+1', '11-23']],
    },
    picks: {
      name: 'GitHub Trending 精读', sid: 'dorami_trending_picks', cron: '30 9 * * *', next: '17:18:19', total: 12,
      dots: ['ok', 'ok', 'ok', 'ok', '', 'ok', 'ok'], last: 'ok', running: false, note: null,
      runs: [['emerald', '成功', '+1', '11-26'], ['emerald', '成功', '+1', '11-25'], ['slate', '跳过', '+0', '11-24'], ['emerald', '成功', '+1', '11-23']],
      history: ['2026-11-26', '2026-11-25', '2026-11-23', '2026-11-22', '2026-11-21', '2026-11-20', '2026-11-19', '2026-11-18', '2026-11-17', '2026-11-16'],
    },
  };
  // 「任务与运行」里今天多出来的行(方向 C)
  let TODAY_RUNS = [
    { t: '09:30', flow: 'picks', stamp: 'stamp-ok', label: '成功', sub: '选 6 个 · 榜单 25 个', trig: '定时', saved: '1', dur: '21.3 s' },
    { t: '08:30', flow: 'brief', stamp: 'stamp-ok', label: '成功', sub: '收录 33 条 · deepseek-chat', trig: '定时', saved: '1', dur: '198.4 s' },
  ];
  if (STATE === 'run') {
    Object.assign(FLOWS.picks, { running: true, last: 'run' });
    FLOWS.picks.runs.unshift(['accent', '运行中', '', '16:11']);
    TODAY_RUNS.unshift({ t: '16:11', flow: 'picks', stamp: 'stamp-run', label: '运行中', sub: '挑选 · 从 25 个仓库里挑 6 个', trig: '手动', saved: '–', dur: '–' });
  }
  if (STATE === 'bad') {
    Object.assign(FLOWS.brief, { last: 'bad', total: 40, next: '16:18:19', note: ['is-err', '最近一次生成失败：LLM 请求超时'] });
    FLOWS.brief.dots[6] = 'bad';
    FLOWS.brief.runs[0] = ['red', '失败', '+0', '11-26'];
    Object.assign(FLOWS.picks, { last: 'warn', total: 11, note: ['is-warn', '今天没有生成：当日榜单未入库'] });
    FLOWS.picks.dots[6] = '';
    FLOWS.picks.runs[0] = ['slate', '跳过', '+0', '11-26'];
    FLOWS.picks.history = FLOWS.picks.history.slice(1);
    TODAY_RUNS = [
      { t: '09:30', flow: 'picks', stamp: 'is-hollow', label: '跳过', sub: '当日榜单未入库', trig: '定时', saved: '0', dur: '0.2 s' },
      { t: '08:30', flow: 'brief', stamp: 'stamp-bad', label: '失败', sub: 'LLM 请求超时', trig: '定时', saved: '–', dur: '120.6 s' },
    ];
  }
  const LAST_LABEL = { ok: '成功', bad: '失败', warn: '跳过', run: '运行中' };

  /* ── 现网里现成的图标与行,克隆来用 ──────────────────────────────────── */
  const briefPanel = panel('brief');
  const PLAY = $('.brief-col-head .action-button svg', briefPanel).outerHTML;
  const SPIN = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide h-4 w-4 animate-spin" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>';
  const RUN_TPL = $('.brief-run', briefPanel).cloneNode(true);

  /* ── 共用的小块 ─────────────────────────────────────────────────────── */
  const dots = (f) => `<span class="tt-dots" title="近 7 日结果">${f.dots.map((d) => `<i class="${d}"></i>`).join('')}</span>`;
  const lastMark = (f) => `<span class="tt-last ${f.last}" title="上次结果:${LAST_LABEL[f.last]}"></span>`;
  const next = (f) => `<span class="tt-next"><span class="u">距下次</span> ${f.next}</span>`;
  const cron = (f) => `<input class="form-input font-mono pf-cron" value="${f.cron}" aria-label="定时(cron 5 段)">`;
  const toggle = (label, on = true) => `<button type="button" class="ledger-switch${on ? ' is-on' : ''}" role="switch" aria-checked="${on}" aria-label="${label}"></button>`;
  const genButton = (f, cls = 'min-h-[32px] px-3 text-xs') => (f.running
    ? `<button type="button" class="action-button action-button-primary ${cls}" disabled>${SPIN}生成中…</button>`
    : `<button type="button" class="action-button action-button-primary ${cls}">${PLAY}立即生成</button>`);
  const noteHTML = (f) => (f.note ? `<p class="pipeline-note ${f.note[0]}">${f.note[1]}</p>` : '');
  const pipelineHTML = () => `
    <div class="ledger-pipeline" aria-label="生成阶段">
      <span class="ledger-pipeline-step is-done">读榜</span><span class="ledger-pipeline-step is-now">挑选</span><span class="ledger-pipeline-step">成稿</span>
    </div>
    <div class="pipeline-note">从 25 个仓库里挑 6 个</div>`;

  // 题条。withIdentity:带上次结果、名称;withRhythm:带近 7 日与距下次(名册里已经有的方向不重复)
  const barHTML = (key, { identity = true, rhythm = true } = {}) => {
    const f = FLOWS[key];
    return `<header class="pf-bar" data-flow="${key}">
      ${identity ? `${lastMark(f)}<h2 class="pf-name">${f.name}</h2>` : ''}
      <span class="pf-sid" title="产出写进这个源">${f.sid}</span>
      ${rhythm ? dots(f) : ''}
      ${cron(f)}
      ${rhythm ? next(f) : ''}
      ${toggle(`定时生成 ${f.name}`)}
      ${genButton(f)}
    </header>`;
  };

  // 名册里的一行(方向 B 的左栏、方向 C 的「任务与运行」左栏)
  const rosterItem = (key) => {
    const f = FLOWS[key];
    return h(`<div class="tt-item" data-flow="${key}"><button type="button" class="tt-row">
      <div class="tt-row-top"><span class="tt-name">${f.name}</span>${lastMark(f)}</div>
      <div class="tt-row-mid">${f.total} 期<span class="tt-cron">${f.cron}</span></div>
      <div class="tt-row-bot">${dots(f)}${next(f)}</div>
    </button></div>`);
  };

  /* ── 「AI 日报」页:现网日报卡的拆改,与精读卡 ───────────────────────── */
  // 现网日报卡里归外壳管的三样(开关、cron、立即生成)挪进题条,其余原样
  function stripBriefCard(card) {
    const [left, right] = $$(':scope > .brief-col', card);
    $('.brief-col-title', left).remove();
    $('.brief-switch-row', left).remove();
    $('.brief-field', left).remove();
    $('.brief-col-head .action-button', right).remove();
  }
  // 日报「今天失败」:现网本来就有这一行,这里照现网的标记写出来,并去掉今天那一期
  function applyBriefState(card) {
    if (STATE !== 'bad') return;
    const right = $$(':scope > .brief-col', card)[1];
    $('.brief-col-head', right).after(h(`<p class="pipeline-note is-err mt-3">${FLOWS.brief.note[1]}</p>`));
    $('.brief-run', right).remove();
    $('.brief-pager-row .tiny-meta', right).textContent = '共 40 期';
  }
  function picksRun(date) {
    const row = RUN_TPL.cloneNode(true);
    $('.brief-run-date', row).textContent = date;
    $('.brief-run-meta', row).textContent = '选 6 个 · 榜单 25 个';
    return row;
  }
  function picksCard() {
    const f = FLOWS.picks;
    const card = h(`<section class="surface-card brief-card">
      <div class="brief-col">
        <div class="brief-field"><span class="form-label">取材</span><button type="button" class="inspector-metabtn pf-src" title="去源管理看这个源">GitHub Trending 日榜</button></div>
        <div class="brief-field"><label class="form-label">挑选个数（1–10）</label><input class="form-input" type="number" value="6"></div>
        <div class="brief-switch-row"><span class="brief-switch-main">${toggle('只看 AI 相关')}只看 AI 相关</span></div>
        <div class="brief-save-row"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs w-full justify-center">保存配置</button></div>
      </div>
      <div class="brief-col">
        <div class="brief-col-head"><div class="brief-col-title">近期精读</div></div>
        ${f.running ? pipelineHTML() : ''}${noteHTML(f)}
        <div class="pf-runs"></div>
        <div class="brief-pager-row"><span class="tiny-meta">共 ${f.total} 期</span><div class="pager"><button type="button" class="pager-btn" disabled aria-label="上一页">«</button><button type="button" class="pager-btn is-on" aria-current="page">1</button><button type="button" class="pager-btn ">2</button><button type="button" class="pager-btn" aria-label="下一页">»</button></div></div>
      </div>
    </section>`);
    $('.pf-runs', card).replaceWith(...f.history.map(picksRun));
    return card;
  }

  function dirA() {
    const page = $('.integ-page', briefPanel);
    $('.page-title', page).textContent = 'AI 产出';
    const brief = $('section.brief-card', page);
    stripBriefCard(brief);
    applyBriefState(brief);
    const picks = picksCard();
    [[brief, 'brief'], [picks, 'picks']].forEach(([card, key]) => {
      card.classList.add('pf-card');
      card.prepend(h(barHTML(key)));
    });
    brief.after(picks);
  }

  function dirB() {
    const page = $('.integ-page', briefPanel);
    $('.page-title', page).textContent = 'AI 产出';
    const brief = $('section.brief-card', page);
    stripBriefCard(brief);
    applyBriefState(brief);
    const paper = h(`<div class="runs-paper pf-paper">
      <aside class="timetable" aria-label="流程名册">
        <div class="tt-head"><span class="tt-head-title">流程</span><span class="tt-head-n">2</span><span class="tt-clock">16:11:41</span></div>
        <div class="tt-list"></div>
      </aside>
      <main class="pf-detail"></main>
    </div>`);
    brief.replaceWith(paper);
    const cards = { brief, picks: picksCard() };
    Object.keys(cards).forEach((key) => {
      $('.tt-list', paper).append(rosterItem(key));
      const pane = h(`<div class="pf-pane" data-flow="${key}"></div>`);
      pane.append(h(barHTML(key, { identity: false, rhythm: false })), cards[key]);
      $('.pf-detail', paper).append(pane);
    });
    const select = (key) => {
      FLOW = key;
      $$('.tt-item', paper).forEach((el) => el.classList.toggle('is-sel', el.dataset.flow === key));
      $$('.pf-pane', paper).forEach((el) => { el.hidden = el.dataset.flow !== key; });
      syncUrl();
    };
    $$('.tt-item', paper).forEach((el) => el.addEventListener('click', () => select(el.dataset.flow)));
    select(FLOW);
  }

  /* ── 「任务与运行」页(方向 C):名册多一组,运行表多几行 ─────────────── */
  function runsC() {
    const p = panel('runs');
    const list = $('.tt-list', p);
    $('.tt-off-head', list).before(h('<div class="tt-off-head">生成</div>'), rosterItem('brief'), rosterItem('picks'));
    $$('.tt-item[data-flow]', list).forEach((el) => el.addEventListener('click', () => { FLOW = el.dataset.flow; show('fetch'); }));

    const tpl = $('tr.run-row', p);
    const todayRows = [];
    for (let tr = $('tr.day-row', p).nextElementSibling; tr && !tr.classList.contains('day-row'); tr = tr.nextElementSibling) todayRows.push(tr);
    TODAY_RUNS.forEach((r) => {
      const tr = tpl.cloneNode(true);
      tr.classList.remove('is-expandable');
      $('.run-time', tr).textContent = r.t;
      const stamp = $('.stamp', tr);
      stamp.className = `stamp ${r.stamp}`;
      stamp.textContent = r.label;
      $('.run-caret', tr).style.visibility = 'hidden';
      $('.run-obj-name', tr).textContent = FLOWS[r.flow].name;
      $('.run-obj-sub', tr).textContent = `生成 · ${r.sub}`;
      const trig = $('.run-trigger', tr);
      trig.textContent = r.trig;
      trig.classList.toggle('is-sched', r.trig === '定时');
      const nums = $$('.run-n', tr);
      nums[0].textContent = '–';
      nums[1].textContent = r.saved;
      nums[2].textContent = '–';
      $('.run-dur', tr).textContent = r.dur;
      const before = todayRows.find((row) => $('.run-time', row).textContent < r.t);
      before.before(tr);
    });

    // 统计条与当日小计跟着多出来的行走
    const stat = (label) => $$('.flow-stat', p).find((el) => $('.flow-stat-lbl', el).textContent.trim() === label);
    const num = (el) => parseInt($('.flow-stat-num', el).textContent, 10);
    const count = (cls) => TODAY_RUNS.filter((r) => r.stamp === cls).length;
    const saved = TODAY_RUNS.reduce((n, r) => n + (parseInt(r.saved, 10) || 0), 0);
    const all = num(stat('全部运行')) + TODAY_RUNS.length;
    $('.flow-stat-num', stat('全部运行')).textContent = all;
    $('.flow-stat-sub', stat('全部运行')).textContent = `近 30 天 · 今日 ${12 + TODAY_RUNS.length}`;
    $('.flow-stat-num', stat('运行中')).textContent = count('stamp-run');
    [['成功', 'stamp-ok'], ['部分失败', ''], ['失败', 'stamp-bad']].forEach(([label, cls]) => {
      const el = stat(label);
      const n = num(el) + (cls ? count(cls) : 0);
      $('.flow-stat-num', el).textContent = n;
      $('.flow-stat-sub', el).textContent = `${(n / all * 100).toFixed(1)}%`;
    });
    $('.flow-stat-num', stat('新增入库')).textContent = num(stat('新增入库')) + saved;
    $('.day-row .sum', p).innerHTML = `${12 + TODAY_RUNS.length} 次 · 新增 <b>${199 + saved}</b> · 失败 <b>${1 + count('stamp-bad')}</b>`;
  }

  /* ── 「源管理」页:系统源多一行;方向 C 再把获取方式写成「生成」 ──────── */
  function fetchSystem() {
    const p = panel('fetch');
    const asGen = DIR === 'c';
    const briefRow = $('.board-node', p);
    const picksRow = briefRow.cloneNode(true);
    picksRow.classList.remove('board-node-sel');
    const rows = { brief: briefRow, picks: picksRow };
    Object.entries(rows).forEach(([key, row]) => {
      const f = FLOWS[key];
      row.dataset.flow = key;
      const name = $('.board-node-name', row);
      name.title = f.name;
      $('.board-node-name-text', row).textContent = f.name;
      const sid = $('.board-node-sid', row);
      sid.title = f.sid;
      sid.textContent = f.sid;
      if (!asGen) {
        $('.board-node-last-res', row).textContent = `无采集 · 累计 ${f.total} 篇`;
        return;
      }
      $('.unbound-pill', row).remove();
      row.title = '系统 · 生成';
      const type = $('.board-node-type', row);
      type.textContent = '生成';
      type.title = '获取方式：生成';
      const tone = f.running ? 'running' : { ok: 'ok', bad: 'fail', warn: 'idle' }[f.last];
      const dot = $(':scope > .signal-dot', row);
      dot.className = `signal-dot signal-dot-${tone}`;
      dot.title = LAST_LABEL[f.last];
      $('.board-node-last-time', row).textContent = f.runs.find((r) => r[1] === '成功')[3];
      $('.board-node-last-res', row).textContent = `${f.running ? '生成中' : { ok: '已生成', bad: '生成失败', warn: '今天跳过' }[f.last]} · 累计 ${f.total} 期`;
    });
    briefRow.after(picksRow);

    // 计数:系统 1 → 2;方向 C 里两个源都有运行结果了
    $('.node-shape-count', p).textContent = '68';
    $('.board-group-count', p).textContent = '2 个源';
    // 健康四项为零时现网是置灰(disabled)的,数字变了要跟着改
    const setStat = (label, n) => {
      const btn = $$('.signal-stat', p).find((el) => $('.signal-stat-label', el).textContent.trim() === label);
      $('.signal-stat-n', btn).textContent = n;
      if (['正常', '失败', '运行中', '未运行'].includes(label)) btn.disabled = n === 0;
    };
    setStat('全部', 2);
    setStat('系统', 2);
    if (asGen) {
      const tones = Object.values(FLOWS).map((f) => (f.running ? 'run' : f.last));
      setStat('正常', tones.filter((t) => t === 'ok').length);
      setStat('失败', tones.filter((t) => t === 'bad').length);
      setStat('运行中', tones.filter((t) => t === 'run').length);
      setStat('未运行', tones.filter((t) => t === 'warn').length);
    } else {
      setStat('未运行', 2);
    }

    const insp = $('.board-inspector', p);
    const select = (key) => {
      FLOW = key;
      const f = FLOWS[key];
      Object.entries(rows).forEach(([k, row]) => row.classList.toggle('board-node-sel', k === key));
      const title = $('.inspector-title', insp);
      title.textContent = f.name;
      title.title = f.name;
      const sid = $('.inspector-sid .font-mono', insp);
      sid.textContent = f.sid;
      sid.title = f.sid;
      $('.inspector-metabtn b', insp).textContent = f.total;
      if (asGen) {
        const tone = f.running ? 'running' : { ok: 'ok', bad: 'fail', warn: 'idle' }[f.last];
        $('.inspector-head-top .signal-dot', insp).className = `signal-dot signal-dot-${tone}`;
        $('.inspector-sid-dot', insp).textContent = '文章 · 系统 · 生成';
        $('.inspector-hint', insp)?.remove();
        $('.acq-panel', insp)?.remove();
        $('.inspector-body', insp).append(genPanel(key));
      }
      syncUrl();
    };
    Object.entries(rows).forEach(([key, row]) => row.addEventListener('click', () => select(key)));
    select(FLOW);
    refreshFetchSelection = () => select(FLOW);
  }
  let refreshFetchSelection = () => {};

  // 检视器里的「生成」框:与 PR #350 的「抓取」「推送」框同一副框架
  function genPanel(key) {
    const f = FLOWS[key];
    const param = (label, control) => `<div class="inspector-param"><label class="inspector-param-label">${label}</label>${control}</div>`;
    const number = (value) => `<input class="inspector-param-input" type="number" value="${value}">`;
    const params = key === 'brief'
      ? [
        param('定时', cron(f) + toggle('定时生成')),
        param('每期条数', number(12)),
        param('入选门槛', number(6)),
        param('正文保底条数', number(8)),
        param('论文门槛', number(7)),
        param('取材', '<div class="mini-seg" role="tablist" aria-label="日报源范围"><button type="button" class="mini-seg-btn is-on" role="tab" aria-selected="true">全部来源</button><button type="button" class="mini-seg-btn" role="tab" aria-selected="false">自定名单</button></div>'),
        param('增量游标', '<code>2026-06-06T21:51:37</code><button type="button" class="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）">重置</button>'),
      ]
      : [
        param('定时', cron(f) + toggle('定时生成')),
        param('取材', '<button type="button" class="inspector-metabtn pf-src" title="去看这个源">GitHub Trending 日榜</button>'),
        param('挑选个数', number(6)),
        param('只看 AI 相关', toggle('只看 AI 相关')),
      ];
    const runRows = f.runs.map(([tone, label, savedN, when]) => `<button type="button" class="inspector-runrow" title="查看运行历史"><span class="inspector-run-dot inspector-run-${tone}"></span><b>${label}</b>${savedN ? `<span class="inspector-run-saved">${savedN}</span>` : ''}<time>${when}</time></button>`).join('');
    return h(`<section class="acq-panel">
      <div class="acq-panel-head"><h3 class="tools-title">生成</h3></div>
      <div class="acq-panel-body">
        <div class="inspector-params">${params.join('')}</div>
        ${f.running ? pipelineHTML() : ''}${noteHTML(f)}
        <div class="inspector-actions">${genButton(f, 'inspector-act')}</div>
        <div class="acq-sec"><h4 class="micro-label">近期运行</h4><div class="inspector-runlist">${runRows}</div></div>
      </div>
    </section>`);
  }

  /* ── 导轨:三个页签能点;方向 A / B 改名,方向 C 没有这个页签 ─────────── */
  const RAIL = { fetch: '源管理', runs: '任务与运行', brief: 'AI 日报' };
  const railBtn = (name) => $(`.app-rail nav button[aria-label="${RAIL[name]}"], .app-rail nav button[data-tab="${name}"]`);
  function show(name) {
    PAGE = name;
    Object.keys(RAIL).forEach((key) => {
      panel(key).classList.toggle('is-off', key !== name);
      const btn = railBtn(key);
      if (!btn) return;
      btn.classList.toggle('is-on', key === name);
      if (key === name) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
    });
    if (name === 'fetch') refreshFetchSelection();
    window.scrollTo(0, 0);
    syncUrl();
  }
  function wireRail() {
    Object.keys(RAIL).forEach((key) => {
      const btn = railBtn(key);
      btn.dataset.tab = key;
      btn.addEventListener('click', () => show(key));
    });
    const brief = railBtn('brief');
    if (DIR === 'a' || DIR === 'b') {
      brief.setAttribute('aria-label', 'AI 产出');
      $('.reader-vrail-label', brief).textContent = '产出';
      $('.reader-vrail-tip', brief).textContent = 'AI 产出';
    }
    if (DIR === 'c') brief.remove();
    $('.app-rail button[aria-label="切换到暗色"]').addEventListener('click', () => { location.search = link({ theme: THEME === 'dark' ? 'light' : 'dark' }); });
  }

  /* ── 样页开关条 ─────────────────────────────────────────────────────── */
  function link(over) {
    const q = new URLSearchParams({ dir: DIR, page: PAGE, state: STATE, flow: FLOW, theme: THEME, ...over });
    if (q.get('dir') === 'c' && q.get('page') === 'brief') q.set('page', 'runs');
    if (Q.has('bare')) q.set('bare', '');
    return `?${q}`;
  }
  function syncUrl() {
    history.replaceState(null, '', link({}));
    $$('.px-bar a').forEach((a) => { a.href = link(JSON.parse(a.dataset.over)); });
  }
  function bar() {
    const group = (label, key, current, items) => `<span class="px-grp"><span class="px-lbl">${label}</span>${items.map(([value, text]) => `<a class="${value === current ? 'is-on' : ''}" data-over='${JSON.stringify({ [key]: value })}' href="#">${text}</a>`).join('')}</span>`;
    document.body.append(h(`<div class="px-bar" role="group" aria-label="样页开关">
      ${group('方向', 'dir', DIR, [['now', '现网'], ['a', 'A 一物一卡'], ['b', 'B 名册'], ['c', 'C 并入源与任务']])}
      ${group('状态', 'state', STATE, [['ok', '正常'], ['run', '生成中'], ['bad', '失败与跳过']])}
      ${group('主题', 'theme', THEME, [['light', '亮'], ['dark', '暗']])}
    </div>`));
  }

  /* ── 开工 ───────────────────────────────────────────────────────────── */
  wireRail();
  if (DIR === 'now') applyBriefState($('section.brief-card', briefPanel));
  if (DIR === 'a') dirA();
  if (DIR === 'b') dirB();
  if (DIR !== 'now') fetchSystem();
  if (DIR === 'c') runsC();
  bar();
  show(PAGE);
})();
