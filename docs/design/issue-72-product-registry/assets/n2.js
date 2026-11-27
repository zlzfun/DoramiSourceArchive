/* 台账:流程 × 日期。一格一天;选中一格,下面是那一期。
   ?flow=brief|picks  &date=YYYY-MM-DD(默认今天)  &cfg=none|brief|picks(配置栏) */
shellReady.then(({ paper, parts }) => {
  const { FLOWS, TODAY, h, $, $$ } = PF;
  PF.init(parts);
  let sel = { flow: FLOWS[PF.Q.get('flow')] ? PF.Q.get('flow') : 'brief', date: PF.Q.get('date') || TODAY };
  let cfg = FLOWS[PF.Q.get('cfg')] ? PF.Q.get('cfg') : null;

  paper.classList.add('pm-paper');
  paper.append(
    h('<div class="pm-band pm-grid"><span>流程</span><div class="pm-days pm-axis"></div><span>累计</span><span>定时</span><span>配置</span><span>生成</span></div>'),
    ...Object.values(FLOWS).map((f) => h(`<div class="pm-row pm-grid" data-flow="${f.key}">
      <div class="pm-id">${PF.lastMark(f)}<div class="run-obj-txt"><div class="run-obj-name">${f.name}</div><div class="run-obj-sub">${f.sid}</div></div></div>
      <div class="pm-days"></div>
      <div><span class="run-n">${f.total}</span></div>
      <div><span class="pf-rhythm">${PF.cron(f)}${PF.toggle(`定时生成 ${f.name}`)}</span></div>
      <div><button type="button" class="icon-button pf-icon pm-gear" title="配置" aria-label="配置 ${f.name}">${PF.ICON.gear}</button></div>
      <div>${PF.genIcon(f)}</div>
    </div>`)),
    h(`<div class="pm-issue">
      <section class="pm-main"><header class="pm-head"></header><div class="pm-body"></div></section>
      <aside class="pm-config" aria-label="流程配置" hidden></aside>
    </div>`),
  );

  /* ── 日期轴与格子:放得下几天就画几天(一格 26px,最多 35 天) ───────────── */
  const count = Math.max(14, Math.min(35, Math.floor($('.pm-axis', paper).clientWidth / 26)));
  const days = Array.from({ length: count }, (_, i) => PF.addDays(TODAY, i - count + 1));
  $('.pm-axis', paper).innerHTML = days.map((d) => {
    const day = d.slice(8);
    const cls = d === TODAY ? 'is-today' : day === '01' ? 'is-month' : '';
    return `<span class="${cls}" title="${d}">${day === '01' ? `${Number(d.slice(5, 7))}月` : day}</span>`;
  }).join('');
  const LABEL = { ok: '已生成', bad: '失败', skip: '跳过', run: '生成中' };
  $$('.pm-row', paper).forEach((row) => {
    const f = FLOWS[row.dataset.flow];
    $('.pm-days', row).innerHTML = days.map((d) => {
      let st = PF.dayStatus(f, d);
      if (f.running && d === TODAY) st = 'run';
      if (st === 'none') return `<span class="pm-day none" title="${d}"><i></i></span>`;
      const reason = f.issues[d] ? ` · ${f.issues[d][1]}` : '';
      return `<button type="button" class="pm-day ${st}" data-date="${d}" title="${PF.md(d)} ${LABEL[st]}${reason}"><i></i></button>`;
    }).join('');
  });

  /* ── 这一期 ─────────────────────────────────────────────────────────── */
  const AI = ['shiyu-coder/Kronos', 'diegosouzapw/OmniRoute', 'ComposioHQ/awesome-claude-skills', 'earthtojake/text-to-cad'];
  const TOOLS = ['block/buzz', 'Pumpkin-MC/Pumpkin'];
  const picksBody = () => {
    const slot = (repo) => `<div class="pm-item"><span class="pm-title is-slot"><b>${repo}</b>一句中文说明;为什么值得看</span></div>`;
    return `<div class="pm-group">AI 相关</div>${AI.map(slot).join('')}<div class="pm-group">通用工具</div>${TOOLS.map(slot).join('')}`;
  };
  const briefBody = (p) => p.items.map((it) => `<div class="pm-item"><span class="pm-score">${Number(it.score).toFixed(1)}</span><span class="pm-title" title="${it.title}">${it.title}</span><span class="pm-kind">${it.kind}</span><span class="pm-source">${it.source}</span></div>`).join('');
  function renderIssue() {
    const f = FLOWS[sel.flow];
    const p = f.byDate[sel.date];
    const issue = f.issues[sel.date];
    const running = f.running && sel.date === TODAY;
    const dated = f.periods.map((x) => x.date); // 新 → 旧
    const at = dated.indexOf(sel.date);
    const older = at >= 0 ? dated[at + 1] : dated.find((d) => d < sel.date);
    const newer = at > 0 ? dated[at - 1] : [...dated].reverse().find((d) => d > sel.date);
    let facts = '';
    let factsCls = '';
    if (running) { facts = `生成中 · ${f.steps[f.stepNow]}`; factsCls = ''; }
    else if (issue) { facts = `${issue[0] === 'bad' ? '失败' : '跳过'} · ${issue[1]}`; factsCls = issue[0] === 'bad' ? 'is-err' : 'is-warn'; }
    else if (p) facts = `${p.time} 生成 · ${p.meta}${p.model ? ` · ${p.model}` : ''}`;
    $('.pm-head', paper).innerHTML = `
      <h2 class="pf-name">${f.name}</h2><span class="pm-date">${sel.date}</span><span class="pm-facts ${factsCls}">${facts}</span>
      <span class="pm-acts">
        <button type="button" class="icon-button pf-icon pm-prev" title="上一期" aria-label="上一期"${older ? '' : ' disabled'} data-date="${older || ''}">${PF.CHEV}</button>
        <button type="button" class="icon-button pf-icon pm-next" title="下一期" aria-label="下一期"${newer ? '' : ' disabled'} data-date="${newer || ''}">${PF.CHEV}</button>
        ${p ? `<button type="button" class="icon-button pf-icon" title="删除这一期" aria-label="删除这一期">${PF.ICON.trash}</button>` : ''}
      </span>`;
    const body = $('.pm-body', paper);
    if (p) {
      body.innerHTML = `${running ? `${PF.stepsHTML(f)}<div class="pipeline-note">${f.stepNote}</div>` : ''}${f.key === 'brief' ? briefBody(p) : picksBody()}`;
    } else {
      // 这一天没有产出:失败或跳过。日报的生成接口本来就能指定日期,所以给一个补生成
      body.innerHTML = `<div class="pm-empty"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs">${PF.PLAY}补生成这一期</button></div>`;
    }
    body.scrollTop = 0;
    $$('.pm-day', paper).forEach((el) => el.classList.toggle('is-sel', el.dataset.date === sel.date && el.closest('.pm-row').dataset.flow === sel.flow));
    $$('.pm-prev, .pm-next', paper).forEach((btn) => btn.addEventListener('click', () => { sel = { flow: sel.flow, date: btn.dataset.date }; renderIssue(); }));
  }
  $$('button.pm-day', paper).forEach((el) => el.addEventListener('click', () => {
    sel = { flow: el.closest('.pm-row').dataset.flow, date: el.dataset.date };
    renderIssue();
  }));

  /* ── 配置栏:点一行的配置钮唤出,再点收起 ────────────────────────────── */
  function renderConfig() {
    const box = $('.pm-config', paper);
    $$('.pm-gear', paper).forEach((btn) => btn.classList.toggle('is-on', btn.closest('.pm-row').dataset.flow === cfg));
    box.hidden = !cfg;
    if (!cfg) return;
    const f = FLOWS[cfg];
    box.innerHTML = `<header class="pm-head"><h3 class="tools-title">${f.name}</h3><button type="button" class="icon-button pf-icon pm-close" title="收起" aria-label="收起配置">${PF.ICON.close}</button></header>
      <div class="pm-config-body">
        <div class="pm-sec-label">取材</div>${PF.inputControls(f, parts)}
        <div class="pm-sec-label">加工</div><div class="inspector-params">${PF.knobs(f, parts)}</div>${PF.save}
      </div>`;
    if (f.key === 'brief') $('.pm-sec-label + *', box).parentElement.insertBefore(parts.scope, $$('.pm-sec-label', box)[1]);
    $('.pm-close', box).addEventListener('click', () => { cfg = null; renderConfig(); });
  }
  $$('.pm-gear', paper).forEach((btn) => btn.addEventListener('click', () => {
    const key = btn.closest('.pm-row').dataset.flow;
    cfg = cfg === key ? null : key;
    renderConfig();
  }));

  renderIssue();
  renderConfig();
  PF.bar([
    ['选中', 'date', sel.date, [[TODAY, '今天'], ['2026-11-24', '11-24']]],
    ['流程', 'flow', sel.flow, [['brief', '日报'], ['picks', '精读']]],
    ['配置栏', 'cfg', cfg || 'none', [['none', '收起'], ['brief', '日报'], ['picks', '精读']]],
    PF.stateGroup,
    PF.themeGroup,
  ]);
});
