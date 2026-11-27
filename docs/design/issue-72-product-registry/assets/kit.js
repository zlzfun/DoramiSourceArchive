/* issue #72 样页共用(泳道定稿与第三轮的台账):两个流程的数据、逐期台账与小块的写法。
   依赖 assets/data.js(日报逐期的真实数据)与 assets/shell.js(现网外壳)。 */
window.PF = (() => {
  'use strict';
  const Q = new URLSearchParams(location.search);
  const STATE = ['ok', 'run', 'bad', 'new'].includes(Q.get('state')) ? Q.get('state') : 'ok';
  const SCOPE = Q.get('scope') === 'custom' ? 'custom' : 'all'; // 日报的取材范围:全部来源 / 自定名单
  const TODAY = '2026-11-26'; // 样页里的「今天」:底页抓取的那一天
  const h = (html) => {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  };
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
  const addDays = (iso, n) => {
    const d = new Date(`${iso}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  };
  const md = (iso) => iso.slice(5); // 11-26

  /* ── 两个流程 ───────────────────────────────────────────────────────── */
  // 日报的逐期是开发库副本里的真实记录(日期平移到今天);精读这个流程还不存在,它的一切都是示意。
  const briefPeriods = window.PF_DATA.briefPeriods.map((p) => ({ ...p, meta: `收录 ${p.count} 条` }));
  const picksDates = [];
  for (let d = '2026-11-14'; d <= TODAY; d = addDays(d, 1)) if (d !== '2026-11-24') picksDates.push(d);
  const picksPeriods = picksDates.reverse().map((date, i) => ({ date, time: i % 3 === 2 ? '09:31' : '09:30', meta: '选 6 个 · 榜单 25 个', model: '' }));

  // 自定名单里列的来源:副本库里篇数最多的公共源(名字、篇数、默认类型是真实的;勾选是示意)
  const SOURCES = [
    ['IT之家 AI', 755, '', true], ['Hugging Face Daily Papers', 691, '论文', false], ['新智元', 509, '', true], ['量子位', 474, '', true],
    ['Hacker News · AI', 472, '', false], ['OpenAI 新闻', 125, '', true], ['Claude Code Changelog', 110, '版本更新', false], ['Codex Changelog', 103, '版本更新', false],
    ['Claude 博客', 80, '', true], ['Anthropic 新闻', 59, '', true], ['OpenCode Releases', 58, '版本更新', false],
  ].map(([name, count, kind, on]) => ({ name, count, kind, on }));
  const FLOWS = {
    brief: {
      key: 'brief', name: '哆啦美·AI资讯日报', sid: 'dorami_daily_brief', cron: '30 8 * * *', next: '16:18:19',
      on: true, last: 'ok', running: false, input: '全部来源', inputSub: '', steps: ['收集', '评分', '精选', '点评', '成稿'],
      periods: briefPeriods, since: '2026-10-18', issues: {},
    },
    picks: {
      key: 'picks', name: 'GitHub Trending 精读', sid: 'dorami_trending_picks', cron: '30 9 * * *', next: '17:18:19',
      on: true, last: 'ok', running: false, input: 'GitHub Trending 日榜', inputSub: '11-26 07:00', steps: ['读榜', '挑选', '成稿'],
      periods: picksPeriods, since: '2026-11-14', issues: { '2026-11-24': ['skip', '没有新榜单'] },
    },
  };
  if (STATE === 'run') Object.assign(FLOWS.picks, { running: true, last: 'run', stepNow: 1, stepNote: '从 25 个仓库里挑 6 个' });
  if (STATE === 'bad') {
    FLOWS.brief.last = 'bad';
    FLOWS.brief.periods = FLOWS.brief.periods.slice(1);
    FLOWS.brief.issues[TODAY] = ['bad', 'LLM 请求超时'];
    FLOWS.picks.last = 'warn';
    FLOWS.picks.periods = FLOWS.picks.periods.slice(1);
    FLOWS.picks.issues[TODAY] = ['skip', '没有新榜单'];
    FLOWS.picks.inputWarn = '没有新榜单';
  }
  // 新流程:刚上线,还没生成过,定时默认关
  if (STATE === 'new') Object.assign(FLOWS.picks, { on: false, last: 'none', periods: [], since: addDays(TODAY, 1), issues: {} });
  if (SCOPE === 'custom') Object.assign(FLOWS.brief, { input: '自定名单', inputSub: `${SOURCES.filter((s) => s.on).length} 个来源` });
  Object.values(FLOWS).forEach((f) => {
    f.byDate = Object.fromEntries(f.periods.map((p) => [p.date, p]));
    f.total = f.periods.length;
  });
  // 某一天的结果:ok 有产出 / bad 失败 / skip 跳过 / none 那天还没有这个流程
  const dayStatus = (f, date) => {
    if (f.issues[date]) return f.issues[date][0];
    if (f.byDate[date]) return 'ok';
    return date < f.since ? 'none' : 'skip';
  };
  const dotClass = { ok: 'ok', bad: 'bad', skip: '', none: '' };
  const LAST_LABEL = { ok: '成功', bad: '失败', warn: '跳过', run: '运行中', none: '还没有运行过' };
  // 今天这一期,一句话
  const todayLine = (f) => {
    if (f.running) return ['is-run', '生成中'];
    const issue = f.issues[TODAY];
    if (issue) return issue[0] === 'bad' ? ['is-err', `今天失败 · ${issue[1]}`] : ['is-warn', '今天跳过'];
    const p = f.byDate[TODAY];
    if (!p) return ['is-none', '还没有生成过'];
    return ['', `今天 ${p.time} · ${p.meta}`];
  };

  /* ── 小块(图标在 init 时从现网节点里取)─────────────────────────────── */
  let PLAY = '';
  let SPIN = '';
  let CHEV = '';
  let ROW = null;
  const init = (parts) => {
    PLAY = parts.playIcon;
    SPIN = parts.spinIcon;
    ROW = parts.rows[0].cloneNode(true); // 现网往期行,当模板
    CHEV = $('svg', ROW).outerHTML;
    ICON.trash = $('.brief-run-del svg', ROW).outerHTML;
    ICON.gear = $('.app-rail button[aria-label="设置"] svg').outerHTML;
  };
  const ICON = {
    trash: '',
    gear: '',
    close: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide h-4 w-4" aria-hidden="true"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>',
  };
  // 一个流程的往期(前 10 期)与分页条,沿用现网 .brief-run / .pager 的标记
  const periodList = (f) => {
    const list = h('<div class="pf-list"></div>');
    if (!f.total) {
      list.append(h('<p class="brief-empty">还没有往期</p>'));
      return list;
    }
    list.append(...f.periods.slice(0, 10).map((p) => periodRow(p, ROW)));
    const pages = Math.ceil(f.total / 10);
    const nums = pages <= 3
      ? Array.from({ length: pages }, (_, i) => `<button type="button" class="pager-btn ${i === 0 ? 'is-on' : ''}">${i + 1}</button>`).join('')
      : `<button type="button" class="pager-btn is-on" aria-current="page">1</button><button type="button" class="pager-btn ">2</button><span class="pager-ellipsis">…</span><button type="button" class="pager-btn ">${pages}</button>`;
    list.append(h(`<div class="brief-pager-row"><span class="tiny-meta">共 ${f.total} 期</span><div class="pager"><button type="button" class="pager-btn" disabled aria-label="上一页">«</button>${nums}<button type="button" class="pager-btn" aria-label="下一页">»</button></div></div>`));
    return list;
  };
  const dots = (f, n = 7) => {
    const days = Array.from({ length: n }, (_, i) => addDays(TODAY, i - n + 1));
    return `<span class="tt-dots" title="近 ${n} 日结果">${days.map((d) => `<i class="${dotClass[dayStatus(f, d)]}"></i>`).join('')}</span>`;
  };
  const lastMark = (f) => `<span class="tt-last ${f.last}" title="上次结果:${LAST_LABEL[f.last]}"></span>`;
  const cron = (f) => `<input class="form-input font-mono pf-cron" value="${f.cron}" aria-label="定时(cron 5 段)">`;
  const toggle = (label, on = true) => `<button type="button" class="ledger-switch${on ? ' is-on' : ''}" role="switch" aria-checked="${on}" aria-label="${label}"></button>`;
  const genIcon = (f) => `<button type="button" class="icon-button pf-icon" title="${f.running ? '生成中' : '立即生成'}" aria-label="立即生成 ${f.name}"${f.running ? ' disabled' : ''}>${f.running ? SPIN : PLAY}</button>`;
  const stepsHTML = (f) => `<div class="ledger-pipeline" aria-label="生成阶段">${f.steps.map((s, i) => `<span class="ledger-pipeline-step${f.running && i < f.stepNow ? ' is-done' : ''}${f.running && i === f.stepNow ? ' is-now' : ''}">${s}</span>`).join('')}</div>`;
  const param = (label, control) => `<div class="inspector-param"><label class="inspector-param-label">${label}</label>${control}</div>`;
  const number = (value, min, max) => `<input class="inspector-param-input" type="number" value="${value}" min="${min}" max="${max}" title="${min}–${max}">`;
  const save = '<div class="pf-save"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs w-full justify-center">保存配置</button></div>';
  // 取材的控件、旋钮(与上一轮同一份内容)
  const inputControls = (f, parts) => (f.key === 'brief'
    ? `<div class="mini-seg is-fluid" role="tablist" aria-label="日报源范围"><button type="button" class="mini-seg-btn${SCOPE === 'all' ? ' is-on' : ''}" role="tab" aria-selected="${SCOPE === 'all'}">全部来源</button><button type="button" class="mini-seg-btn${SCOPE === 'custom' ? ' is-on' : ''}" role="tab" aria-selected="${SCOPE === 'custom'}">自定名单</button></div>
       ${SCOPE === 'custom' ? scopeList() : ''}
       <div class="pf-cursor"><span class="tiny-meta shrink-0">增量游标</span><code class="brief-cursor-val" title="${parts.cursor}">${parts.cursor}</code><button type="button" class="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）">重置</button></div>`
    : `<div class="pf-input"><button type="button" class="inspector-metabtn pf-src" title="去源管理看这个源">${f.input}</button><time class="${f.inputWarn ? 'is-warn' : ''}">${f.inputWarn || f.inputSub}</time></div>`);
  // 自定名单:沿用现网 .brief-scope-list / .brief-scope-row 的标记(勾选框、名字、篇数),下面是已选个数。
  // 现网每行末尾还写默认类型(「691 篇 · 论文」);这一列只有 200–240px,名字会被挤得只剩几个字,所以类型挪进悬浮提示
  const scopeList = () => `<div class="pf-scope"><div class="brief-scope-list" role="group" aria-label="日报来源名单">${SOURCES.map((s) => `<label class="brief-scope-row"><input type="checkbox" class="h-3.5 w-3.5"${s.on ? ' checked' : ''}><span class="brief-scope-name" title="${s.name}">${s.name}</span><span class="tiny-meta shrink-0"${s.kind ? ` title="${s.kind}"` : ''}>${s.count} 篇</span></label>`).join('')}</div><p class="tiny-meta mt-1">已选 ${SOURCES.filter((s) => s.on).length} 个</p></div>`;
  const knobs = (f, parts) => (f.key === 'brief'
    ? [param('每期条数', number(parts.values.topN, 1, 50)), param('入选门槛', number(parts.values.minScore, 0, 10)), param('正文保底条数', number(parts.values.minItems, 0, 50)), param('论文门槛', number(parts.values.paperMin, 0, 10))]
    : [param('挑选个数', number(6, 1, 10)), param('只看 AI 相关', toggle('只看 AI 相关'))]).join('');
  // 往期的一行(日期 · 概况 · 生成时刻),沿用现网 .brief-run 的标记
  const periodRow = (p, tpl) => {
    const row = tpl.cloneNode(true);
    $('.brief-run-date', row).textContent = p.date;
    $('.brief-run-meta', row).textContent = p.model ? `${p.meta} · ${p.model}` : p.meta;
    const tail = $('.stamp, .pf-when', row);
    tail.replaceWith(h(`<time class="pf-when">${p.time}</time>`));
    return row;
  };
  // 样页开关条
  const bar = (groups) => {
    const link = (over) => {
      const q = new URLSearchParams(location.search);
      Object.entries(over).forEach(([k, v]) => (v === null ? q.delete(k) : q.set(k, v)));
      return `?${q}`;
    };
    document.body.append(h(`<div class="px-bar" role="group" aria-label="样页开关">${groups.map(([label, key, current, items]) => `<span class="px-grp"><span class="px-lbl">${label}</span>${items.map(([value, text]) => `<a class="${value === current ? 'is-on' : ''}" href="${link({ [key]: value })}">${text}</a>`).join('')}</span>`).join('')}</div>`));
  };
  const stateGroup = ['状态', 'state', STATE, [['ok', '正常'], ['run', '生成中'], ['bad', '失败与跳过']]];
  const laneStateGroup = ['状态', 'state', STATE, [...stateGroup[3], ['new', '新流程']]]; // 「新流程」只有泳道画了
  const scopeGroup = ['日报取材', 'scope', SCOPE, [['all', '全部来源'], ['custom', '自定名单']]];
  const themeGroup = ['主题', 'theme', Q.get('theme') === 'dark' ? 'dark' : 'light', [['light', '亮'], ['dark', '暗']]];

  return { Q, STATE, SCOPE, TODAY, FLOWS, SOURCES, h, $, $$, addDays, md, dayStatus, todayLine, init, dots, lastMark, cron, toggle, genIcon, stepsHTML, param, number, save, inputControls, knobs, periodRow, periodList, bar, stateGroup, laneStateGroup, scopeGroup, themeGroup, ICON, get PLAY() { return PLAY; }, get SPIN() { return SPIN; }, get CHEV() { return CHEV; } };
})();
