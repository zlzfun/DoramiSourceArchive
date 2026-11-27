/* issue #72 样页第二轮(基于方向 B「名册 + 所选流程」):示意数据 + 对现网 DOM 的改动 + 样页开关。
   底页是现网(主干 b4836bac)的 DOM;?dir=now 时除了导轨能点,什么都不改。
   参数:dir=b|now  lay=left|tiles|table(名册放哪)  det=cols|pipe(所选流程怎么排)
        state=ok|run|bad  flow=brief|picks  page=brief|runs|fetch  theme=light|dark  bare */
(() => {
  'use strict';

  const Q = new URLSearchParams(location.search);
  const pick = (key, allowed) => (allowed.includes(Q.get(key)) ? Q.get(key) : allowed[0]);
  const DIR = pick('dir', ['b', 'now']);
  const LAY = pick('lay', ['left', 'tiles', 'table']);
  const DET = pick('det', ['cols', 'pipe']);
  const HIST = pick('hist', ['list', 'fold']); // 往期:列出来 / 收起(只在三段排法里有区别)
  const STATE = pick('state', ['ok', 'run', 'bad']);
  const THEME = pick('theme', ['light', 'dark']);
  let FLOW = pick('flow', ['brief', 'picks']);
  let PAGE = pick('page', ['brief', 'runs', 'fetch']);

  const root = document.documentElement;
  root.dataset.theme = THEME;
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
  // 日报的历史行、cron 与各旋钮的取值来自底页(开发库副本,日期平移到今天);
  // 精读这个流程还不存在,它的数字全是示意。
  const FLOWS = {
    brief: {
      name: '哆啦美·AI资讯日报', sid: 'dorami_daily_brief', cron: '30 8 * * *', next: '16:18:19', total: 41,
      dots: ['ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok'], last: 'ok', running: false, note: null,
      input: '全部来源', steps: ['收集', '评分', '精选', '点评', '成稿'], listTitle: '近期日报',
    },
    picks: {
      name: 'GitHub Trending 精读', sid: 'dorami_trending_picks', cron: '30 9 * * *', next: '17:18:19', total: 12,
      dots: ['ok', 'ok', 'ok', 'ok', '', 'ok', 'ok'], last: 'ok', running: false, note: null,
      input: 'GitHub Trending 日榜', fresh: ['', '11-26 07:00'], steps: ['读榜', '挑选', '成稿'], listTitle: '近期精读',
      history: ['2026-11-26', '2026-11-25', '2026-11-23', '2026-11-22', '2026-11-21', '2026-11-20', '2026-11-19', '2026-11-18', '2026-11-17', '2026-11-16'],
    },
  };
  if (STATE === 'run') {
    Object.assign(FLOWS.picks, { running: true, last: 'run', stepNow: 1, stepNote: '从 25 个仓库里挑 6 个' });
  }
  if (STATE === 'bad') {
    Object.assign(FLOWS.brief, { last: 'bad', total: 40, note: ['is-err', '最近一次生成失败：LLM 请求超时'] });
    FLOWS.brief.dots[6] = 'bad';
    Object.assign(FLOWS.picks, { last: 'warn', total: 11, fresh: ['is-warn', '当日未入库'], note: ['is-warn', '今天没有生成：当日榜单未入库'] });
    FLOWS.picks.dots[6] = '';
    FLOWS.picks.history = FLOWS.picks.history.slice(1);
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
  const genButton = (f) => (f.running
    ? `<button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs" disabled>${SPIN}生成中…</button>`
    : `<button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs">${PLAY}立即生成</button>`);
  const genIcon = (f) => `<button type="button" class="icon-button pf-icon" title="${f.running ? '生成中' : '立即生成'}" aria-label="立即生成 ${f.name}"${f.running ? ' disabled' : ''}>${f.running ? SPIN : PLAY}</button>`;
  const noteHTML = (f) => (f.note ? `<p class="pipeline-note ${f.note[0]}">${f.note[1]}</p>` : '');
  // 阶段条:现网只在生成时出现;三段排法里它一直在「加工」里,生成时就是进度
  const stepsHTML = (f, live) => `<div class="ledger-pipeline" aria-label="生成阶段">${f.steps.map((s, i) => `<span class="ledger-pipeline-step${live && i < f.stepNow ? ' is-done' : ''}${live && i === f.stepNow ? ' is-now' : ''}">${s}</span>`).join('')}</div>`;
  const param = (label, control) => `<div class="inspector-param"><label class="inspector-param-label">${label}</label>${control}</div>`;
  const number = (value, min, max) => `<input class="inspector-param-input" type="number" value="${value}" min="${min}" max="${max}" title="${min}–${max}">`;
  const section = (html) => h(`<section class="pf-sec">${html}</section>`);
  // 历史行末尾:现网每一行都是同一个「已生成」徽标,这里换成那一期的生成时刻(日报的时刻是副本库里的真实值)
  const BRIEF = window.PF_DATA.briefPeriods.slice(STATE === 'bad' ? 1 : 0);
  const WHEN = BRIEF.slice(0, 10).map((p) => p.time);
  const stampToWhen = (row, text) => $('.stamp', row).replaceWith(h(`<time class="pf-when">${text}</time>`));

  /* ── 名册:三种放法,一行里的东西相同 ────────────────────────────────── */
  const rowInner = (f) => `
      <div class="tt-row-top"><span class="tt-name">${f.name}</span>${lastMark(f)}</div>
      <div class="tt-row-mid">${f.total} 期<span class="tt-cron">${f.cron}</span></div>
      <div class="tt-row-bot">${dots(f)}${next(f)}</div>`;
  function roster() {
    const keys = Object.keys(FLOWS);
    if (LAY === 'left') {
      return h(`<aside class="timetable" aria-label="流程名册">
        <div class="tt-head"><span class="tt-head-title">流程</span><span class="tt-head-n">${keys.length}</span><span class="tt-clock">16:11:41</span></div>
        <div class="tt-list">${keys.map((key) => `<div class="tt-item" data-flow="${key}"><button type="button" class="tt-row">${rowInner(FLOWS[key])}</button></div>`).join('')}</div>
      </aside>`);
    }
    if (LAY === 'tiles') {
      return h(`<div class="pf-tiles" role="tablist" aria-label="流程名册">${keys.map((key) => `<button type="button" class="pf-tile" role="tab" data-flow="${key}">${rowInner(FLOWS[key])}</button>`).join('')}</div>`);
    }
    return h(`<div class="pf-table-wrap"><table class="flow-table pf-table">
      <thead><tr>
        <th class="flow-th">流程</th>
        <th class="flow-th" style="width: 220px;">取材</th>
        <th class="flow-th" style="width: 96px;">近 7 日</th>
        <th class="flow-th is-num" style="width: 72px;">累计</th>
        <th class="flow-th" style="width: 108px;">距下次</th>
        <th class="flow-th" style="width: 172px;">定时</th>
        <th class="flow-th" style="width: 64px;">生成</th>
      </tr></thead>
      <tbody>${keys.map((key) => {
    const f = FLOWS[key];
    return `<tr class="run-row" data-flow="${key}">
          <td><div class="run-obj">${lastMark(f)}<div class="run-obj-txt"><div class="run-obj-name">${f.name}</div><div class="run-obj-sub">${f.sid}</div></div></div></td>
          <td><span class="pf-cell">${f.input}</span></td>
          <td>${dots(f)}</td>
          <td><span class="run-n">${f.total}</span></td>
          <td><span class="tt-next">${f.next}</span></td>
          <td><span class="pf-rhythm">${cron(f)}${toggle(`定时生成 ${f.name}`)}</span></td>
          <td>${genIcon(f)}</td>
        </tr>`;
  }).join('')}</tbody>
    </table></div>`);
  }

  /* ── 所选流程 ───────────────────────────────────────────────────────── */
  // 题条:上次结果 · 名称 · 产出源 id ｜ cron · 开关 · 立即生成。
  // 名册是表格时这几样都在行上(定时列里直接改 cron、拨开关,生成列里点生成),不再有题条
  const headHTML = (key) => {
    if (LAY === 'table') return '';
    const f = FLOWS[key];
    return `<header class="pf-head">
      ${lastMark(f)}<h2 class="pf-name">${f.name}</h2>
      <button type="button" class="pf-sid" title="去源管理看这个源">${f.sid}</button>
      ${cron(f)}
      ${toggle(`定时生成 ${f.name}`)}${genButton(f)}
    </header>`;
  };
  // 区头:一行列头(与表头同一副字),正文各区在它下面自己滚
  const bandHTML = (cells) => `<div class="pf-band pf-grid ${DET === 'pipe' ? 'is-pipe' : 'is-cols'}">${cells.map((cell) => `<span>${cell}</span>`).join('')}</div>`;
  function picksRun(date, i) {
    const row = RUN_TPL.cloneNode(true);
    $('.brief-run-date', row).textContent = date;
    $('.brief-run-meta', row).textContent = '选 6 个 · 榜单 25 个';
    stampToWhen(row, ['09:30', '09:30', '09:31', '09:30', '09:30', '09:31', '09:30', '09:30', '09:30', '09:31'][i]);
    return row;
  }
  const picksPager = (f) => h(`<div class="brief-pager-row"><span class="tiny-meta">共 ${f.total} 期</span><div class="pager"><button type="button" class="pager-btn" disabled aria-label="上一页">«</button><button type="button" class="pager-btn is-on" aria-current="page">1</button><button type="button" class="pager-btn ">2</button><button type="button" class="pager-btn" aria-label="下一页">»</button></div></div>`);

  function pane(key, vals, parts) {
    const f = FLOWS[key];
    const isBrief = key === 'brief';
    const count = `<i class="pf-count">${f.total} 期</i>`;
    const el = h(`<div class="pf-pane" data-flow="${key}">${headHTML(key)}${bandHTML(DET === 'pipe' ? ['取材', '加工', HIST === 'fold' ? '产出' : `产出${count}`] : ['配置', `${f.listTitle}${count}`])}<div class="pf-body pf-grid ${DET === 'pipe' ? 'is-pipe' : 'is-cols'}"></div></div>`);
    const body = $('.pf-body', el);
    if (isBrief) parts.runs.forEach((row, i) => stampToWhen(row, WHEN[i]));
    const rows = isBrief ? parts.runs : f.history.map(picksRun);
    const pager = isBrief ? parts.pager : picksPager(f);
    const list = h('<div class="pf-list"></div>');
    list.append(...rows);
    const knobs = isBrief
      ? [param('每期条数', number(vals[1], 1, 50)), param('入选门槛', number(vals[2], 0, 10)), param('正文保底条数', number(vals[3], 0, 50)), param('论文门槛', number(vals[4], 0, 10))]
      : [param('挑选个数', number(6, 1, 10)), param('只看 AI 相关', toggle('只看 AI 相关'))];
    const scopeSeg = (fluid) => `<div class="mini-seg${fluid ? ' is-fluid' : ''}" role="tablist" aria-label="日报源范围"><button type="button" class="mini-seg-btn is-on" role="tab" aria-selected="true">全部来源</button><button type="button" class="mini-seg-btn" role="tab" aria-selected="false">自定名单</button></div>`;
    const sourceChip = `<button type="button" class="inspector-metabtn pf-src" title="去源管理看这个源">${f.input}</button>`;
    const cursor = `<code class="brief-cursor-val" title="${parts.cursor}">${parts.cursor}</code><button type="button" class="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）">重置</button>`;
    const save = '<div class="pf-save"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs w-full justify-center">保存配置</button></div>';

    if (DET === 'cols') {
      // 两栏:配置 ｜ 近期
      const config = [param('取材', isBrief ? scopeSeg(false) : sourceChip), ...knobs];
      if (isBrief) config.push(param('增量游标', cursor));
      const left = section(`<div class="inspector-params">${config.join('')}</div>${save}`);
      const right = section(`${f.running ? stepsHTML(f, true) + `<div class="pipeline-note">${f.stepNote}</div>` : ''}${noteHTML(f)}`);
      if (isBrief) right.append(parts.scope);
      right.append(list, pager);
      body.append(left, right);
    } else {
      // 三段:取材 → 加工 → 产出
      const input = isBrief
        ? section(`${scopeSeg(true)}<div class="pf-cursor"><span class="tiny-meta shrink-0">增量游标</span>${cursor}</div>`)
        : section(`<div class="pf-input">${sourceChip}<time class="${f.fresh[0]}">${f.fresh[1]}</time></div>`);
      if (isBrief) input.append(parts.scope);
      const work = section(`${stepsHTML(f, f.running)}${f.running ? `<div class="pipeline-note">${f.stepNote}</div>` : ''}<div class="inspector-params">${knobs.join('')}</div>${save}`);
      // 精读跳过的原因已经写在「取材」里(当日未入库),产出这一段不再重复
      const output = section(isBrief ? noteHTML(f) : '');
      if (HIST === 'fold') {
        // 往期收起:这一段平时只放最新一期的内容,往期点开才出现
        const latest = isBrief ? BRIEF[0] : { date: f.history[0], time: '09:30', meta: '选 6 个 · 榜单 25 个' };
        const facts = isBrief ? `${latest.time} 生成 · 收录 ${latest.count} 条 · ${latest.model}` : `${latest.time} 生成 · ${latest.meta}`;
        const items = isBrief
          ? latest.items.slice(0, 8).map((it) => `<div class="pm-item"><span class="pm-score">${Number(it.score).toFixed(1)}</span><span class="pm-title" title="${it.title}">${it.title}</span><span class="pm-kind">${it.kind}</span></div>`).join('')
          : ['shiyu-coder/Kronos', 'diegosouzapw/OmniRoute', 'ComposioHQ/awesome-claude-skills', 'earthtojake/text-to-cad', 'block/buzz', 'Pumpkin-MC/Pumpkin'].map((repo) => `<div class="pm-item"><span class="pm-title is-slot"><b>${repo}</b>一句中文说明;为什么值得看</span></div>`).join('');
        const fold = h(`<details class="scope-note pf-fold"><summary>往期<i class="pf-count">${f.total} 期</i></summary></details>`);
        fold.append(list, pager);
        fold.open = Q.has('histopen'); // 样页截图用:地址带 histopen 时往期是点开的
        output.append(h(`<div class="pf-latest"><div class="pf-latest-head"><span class="pm-date">${latest.date}</span><span class="pm-facts">${facts}</span></div>${items}</div>`), fold);
      } else {
        output.append(list, pager);
      }
      body.append(input, work, output);
    }
    return el;
  }

  function buildB() {
    const old = $('.integ-page', briefPanel);
    const pageHead = $('.page-head', old);
    $('.page-title', pageHead).textContent = 'AI 产出';
    const card = $('section.brief-card', old);
    const [left, right] = $$(':scope > .brief-col', card);
    const vals = $$('.brief-field input', left).map((el) => el.value); // cron、每期条数、入选门槛、正文保底、论文门槛
    FLOWS.brief.cron = vals[0];
    const parts = {
      cursor: $('.brief-cursor-val', left).textContent,
      scope: $('details.scope-note', right),
      runs: $$('.brief-run', right),
      pager: $('.brief-pager-row', right),
    };
    if (STATE === 'bad') {
      parts.runs.shift();
      $('.tiny-meta', parts.pager).textContent = '共 40 期';
    }

    const shell = h(`<div class="runs-shell"><div class="runs-work"><div class="runs-paper pf-paper${LAY === 'left' ? '' : ' is-top'}"></div></div></div>`);
    shell.prepend(pageHead);
    old.replaceWith(shell);
    const paper = $('.pf-paper', shell);
    const detail = h('<main class="pf-detail"></main>');
    paper.append(roster(), detail);
    Object.keys(FLOWS).forEach((key) => detail.append(pane(key, vals, parts)));

    const selClass = LAY === 'tiles' ? 'is-on' : 'is-sel';
    const entries = $$('[data-flow]', paper).filter((el) => !el.classList.contains('pf-pane'));
    const select = (key) => {
      FLOW = key;
      entries.forEach((el) => el.classList.toggle(selClass, el.dataset.flow === key));
      $$('.pf-pane', paper).forEach((el) => { el.hidden = el.dataset.flow !== key; });
      syncUrl();
    };
    entries.forEach((el) => el.addEventListener('click', () => select(el.dataset.flow)));
    $$('.pf-sid, .pf-src', paper).forEach((el) => el.addEventListener('click', () => show('fetch')));
    select(FLOW);
  }

  // 现网的「今天失败」:现网本来就有这一行,这里照现网的标记写出来,并去掉今天那一期
  function nowBad() {
    const right = $$('section.brief-card > .brief-col', briefPanel)[1];
    $('.brief-col-head', right).after(h(`<p class="pipeline-note is-err mt-3">${FLOWS.brief.note[1]}</p>`));
    $('.brief-run', right).remove();
    $('.brief-pager-row .tiny-meta', right).textContent = '共 40 期';
  }

  /* ── 「源管理」页:系统源多一行(产出即源),其余照现网 ─────────────── */
  let refreshFetchSelection = () => {};
  function fetchSystem() {
    const p = panel('fetch');
    const briefRow = $('.board-node', p);
    const picksRow = briefRow.cloneNode(true);
    picksRow.classList.remove('board-node-sel');
    const rows = { brief: briefRow, picks: picksRow };
    Object.entries(rows).forEach(([key, row]) => {
      const f = FLOWS[key];
      const name = $('.board-node-name', row);
      name.title = f.name;
      $('.board-node-name-text', row).textContent = f.name;
      const sid = $('.board-node-sid', row);
      sid.title = f.sid;
      sid.textContent = f.sid;
      $('.board-node-last-res', row).textContent = `无采集 · 累计 ${f.total} 篇`;
    });
    briefRow.after(picksRow);
    $('.node-shape-count', p).textContent = '68';
    $('.board-group-count', p).textContent = '2 个源';
    const setStat = (label, n) => {
      const btn = $$('.signal-stat', p).find((el) => $('.signal-stat-label', el).textContent.trim() === label);
      $('.signal-stat-n', btn).textContent = n;
    };
    ['全部', '系统', '未运行'].forEach((label) => setStat(label, 2));

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
      syncUrl();
    };
    Object.entries(rows).forEach(([key, row]) => row.addEventListener('click', () => select(key)));
    select(FLOW);
    refreshFetchSelection = () => select(FLOW);
  }

  /* ── 导轨:三个页签能点;样页里「AI 日报」改名 ─────────────────────── */
  const RAIL = { fetch: '源管理', runs: '任务与运行', brief: 'AI 日报' };
  const railBtn = (name) => $(`.app-rail nav button[data-tab="${name}"]`);
  function show(name) {
    PAGE = name;
    Object.keys(RAIL).forEach((key) => {
      panel(key).classList.toggle('is-off', key !== name);
      const btn = railBtn(key);
      btn.classList.toggle('is-on', key === name);
      if (key === name) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
    });
    if (name === 'fetch') refreshFetchSelection();
    window.scrollTo(0, 0);
    syncUrl();
  }
  function wireRail() {
    Object.entries(RAIL).forEach(([key, label]) => {
      const btn = $(`.app-rail nav button[aria-label="${label}"]`);
      btn.dataset.tab = key;
      btn.addEventListener('click', () => show(key));
    });
    if (DIR === 'b') {
      const brief = railBtn('brief');
      brief.setAttribute('aria-label', 'AI 产出');
      $('.reader-vrail-label', brief).textContent = '产出';
      $('.reader-vrail-tip', brief).textContent = 'AI 产出';
    }
    $('.app-rail button[aria-label="切换到暗色"]').addEventListener('click', () => { location.search = link({ theme: THEME === 'dark' ? 'light' : 'dark' }); });
  }

  /* ── 样页开关条 ─────────────────────────────────────────────────────── */
  function link(over) {
    const q = new URLSearchParams({ dir: DIR, lay: LAY, det: DET, hist: HIST, state: STATE, flow: FLOW, page: PAGE, theme: THEME, ...over });
    if (Q.has('bare')) q.set('bare', '');
    return `?${q}`;
  }
  function syncUrl() {
    history.replaceState(null, '', link({}));
    $$('.px-bar a').forEach((a) => { a.href = link(JSON.parse(a.dataset.over)); });
  }
  function bar() {
    const group = (label, items) => `<span class="px-grp"><span class="px-lbl">${label}</span>${items.map(([on, text, over]) => `<a class="${on ? 'is-on' : ''}" data-over='${JSON.stringify(over)}' href="#">${text}</a>`).join('')}</span>`;
    const b = DIR === 'b';
    document.body.append(h(`<div class="px-bar" role="group" aria-label="样页开关">
      ${group('', [[!b, '现网', { dir: 'now' }]])}
      ${group('名册', [['left', '左栏'], ['tiles', '顶部磁贴'], ['table', '顶部表格']].map(([v, t]) => [b && LAY === v, t, { dir: 'b', lay: v, page: 'brief' }]))}
      ${group('详情', [['cols', '两栏'], ['pipe', '取材 → 加工 → 产出']].map(([v, t]) => [b && DET === v, t, { dir: 'b', det: v, page: 'brief' }]))}
      ${group('往期', [['list', '列出'], ['fold', '收起']].map(([v, t]) => [b && DET === 'pipe' && HIST === v, t, { dir: 'b', det: 'pipe', hist: v, page: 'brief' }]))}
      ${group('状态', [['ok', '正常'], ['run', '生成中'], ['bad', '失败与跳过']].map(([v, t]) => [STATE === v, t, { state: v }]))}
      ${group('主题', [['light', '亮'], ['dark', '暗']].map(([v, t]) => [THEME === v, t, { theme: v }]))}
    </div>`));
  }

  /* ── 开工 ───────────────────────────────────────────────────────────── */
  wireRail();
  if (DIR === 'now' && STATE === 'bad') nowBad();
  if (DIR === 'b') {
    buildB();
    fetchSystem();
  }
  bar();
  show(PAGE);
})();
