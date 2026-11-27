/* 定稿原型:泳道 + 右侧抽屉。点一行,从右侧打开这个流程的抽屉(现网的 .ledger-drawer,470 宽),配置一列。
   ?open=none|brief|picks  打开谁的抽屉(默认不开)
   ?cfg=one|two  ?dw=470|640   配置框排一列还是两列、抽屉多宽(细节样页 details/config.html 比较时用;定的是一列、470)
   ?state=ok|run|bad|new   ?scope=all|custom   ?theme=light|dark   ?bare
   行上只留两样即时的操作:开关、立即生成。cron 在行上只读,和取材、旋钮一起在抽屉的「配置」里改,由「保存配置」提交(与现网相同)。
   抽屉的题条里另有一个立即生成(抽屉开着时行被遮住,点不到);开关只在行上。
   弹窗、独立页两种容器在第五轮的存档 r5/lane.html 里。 */
shellReady.then(({ paper, parts }) => {
  const { FLOWS, SOURCES, h, $ } = PF;
  PF.init(parts);
  const openParam = FLOWS[PF.Q.get('open')] ? PF.Q.get('open') : 'none';
  const twoCol = PF.Q.get('cfg') === 'two';
  const wide = PF.Q.get('dw') === '640';

  /* ── 泳道 ─────────────────────────────────────────────────────────── */
  const idHTML = (f) => `<div class="gf-id">${PF.lastMark(f)}<div class="run-obj-txt"><div class="run-obj-name">${f.name}</div><span class="run-obj-sub">${f.sid}</span></div></div>`;
  const whenHTML = (f) => `<div class="gf-when${f.on ? '' : ' is-off'}"><div><div class="gf-cron">${f.cron}</div><div class="gf-sub"><span class="run-obj-sub">${f.on ? `距下次 ${f.next}` : '未开启'}</span></div></div>${PF.toggle(`定时生成 ${f.name}`, f.on)}</div>`;
  const rowHTML = (f) => {
    const [cls, line] = PF.todayLine(f);
    const sub = f.inputWarn || f.inputSub;
    return `<div class="gf-row gf-grid" data-flow="${f.key}" role="button" tabindex="0" aria-haspopup="dialog">
      <div class="gf-cell">${idHTML(f)}</div>
      <div class="gf-cell"><div class="gf-main">${f.input}</div>${sub ? `<div class="gf-sub"><span class="run-obj-sub${f.inputWarn ? ' is-warn' : ''}">${sub}</span></div>` : ''}</div>
      <div class="gf-cell">${PF.stepsHTML(f)}${f.running ? `<div class="pipeline-note">${f.stepNote}</div>` : ''}</div>
      <div class="gf-cell"><div class="gf-main ${cls}">${line}</div><div class="gf-sub">${PF.dots(f)}<span class="run-obj-sub">${f.total} 期</span></div></div>
      <div class="gf-cell">${whenHTML(f)}</div>
      <div class="gf-cell">${PF.genIcon(f)}</div>
    </div>`;
  };

  paper.classList.add('gf-paper');
  const scroll = h('<div class="gf-scroll"></div>');
  scroll.append(h('<div class="gf-band gf-grid"><span>流程</span><span class="is-flow">取材</span><span class="is-flow">加工</span><span>产出</span><span>定时</span><span>生成</span></div>'));
  Object.values(FLOWS).forEach((f) => scroll.append(h(rowHTML(f))));
  paper.append(scroll);

  /* ── 抽屉里的两个框:配置、往期 ─────────────────────────────────────── */
  const seg = `<div class="mini-seg" role="tablist" aria-label="日报源范围"><button type="button" class="mini-seg-btn${PF.SCOPE === 'all' ? ' is-on' : ''}" role="tab" aria-selected="${PF.SCOPE === 'all'}">全部来源</button><button type="button" class="mini-seg-btn${PF.SCOPE === 'custom' ? ' is-on' : ''}" role="tab" aria-selected="${PF.SCOPE === 'custom'}">自定名单</button></div>`;
  // 自定名单:现网 .brief-scope-list / .brief-scope-row 的标记原样(勾选框、名字、篇数与默认类型),下面是已选个数
  const scopeList = () => `<div class="brief-scope-list" role="group" aria-label="日报来源名单">${SOURCES.map((s) => `<label class="brief-scope-row"><input type="checkbox" class="h-3.5 w-3.5"${s.on ? ' checked' : ''}><span class="brief-scope-name">${s.name}</span><span class="tiny-meta shrink-0">${s.count} 篇${s.kind ? ` · ${s.kind}` : ''}</span></label>`).join('')}</div><p class="tiny-meta mt-1">已选 ${SOURCES.filter((s) => s.on).length} 个</p>`;
  // 配置:取材 → 定时 → 旋钮 → 游标,一行一个参数(现网的参数行:名称在左、控件在右)。输入框同宽、字居中。
  // (比较用的两列排法里,日报的「取材」下面可能带名单,所以占整行。)
  const configBox = (f) => {
    const cron = PF.param('定时', `<input class="inspector-param-input" value="${f.cron}" aria-label="定时(cron 5 段)">`);
    const rows = [];
    if (f.key === 'brief') {
      rows.push(PF.SCOPE === 'custom'
        ? `<div class="inspector-param is-wide is-block"><div class="gf-param-line"><label class="inspector-param-label">取材</label>${seg}</div>${scopeList()}</div>`
        : PF.param('取材', seg).replace('class="inspector-param"', 'class="inspector-param is-wide"'));
    } else {
      rows.push(PF.param('取材', `<button type="button" class="inspector-metabtn" title="去源管理看这个源">${f.input}</button>`));
    }
    rows.push(cron, PF.knobs(f, parts));
    if (f.key === 'brief') rows.push(PF.param('增量游标', `<code class="brief-cursor-val" title="${parts.cursor}">${parts.cursor}</code><button type="button" class="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）">重置</button>`));
    const box = h(`<section class="acq-panel gf-box gf-config"><div class="acq-panel-head"><span class="tools-title">配置</span></div><div class="acq-panel-body"><div class="gf-params${twoCol ? ' is-two' : ''}">${rows.join('')}</div><div class="gf-save"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs">保存配置</button></div></div></section>`);
    if (f.key === 'brief') $('.gf-save', box).before(parts.scope.cloneNode(true)); // 现网的「取材与去重口径」
    return box;
  };
  const periodsBox = (f) => {
    const box = h('<section class="acq-panel gf-box gf-periods"><div class="acq-panel-head"><span class="tools-title">往期</span></div><div class="acq-panel-body"></div></section>');
    // 生成中:阶段条与当前这一步出现在往期的顶上(现网也是出现在「近期日报」栏头下面)
    if (f.running && !f.phases) $('.acq-panel-body', box).append(h(`<div class="gf-running">${PF.stepsHTML(f)}<div class="pipeline-note">${f.stepNote}</div></div>`));
    $('.acq-panel-body', box).append(PF.periodList(f));
    return box;
  };

  /* ── 抽屉:现网 .ledger-scrim + .ledger-drawer(管理面的表格点一行基本都是它)──────────── */
  let closeCurrent = null;
  const mark = (key) => scroll.querySelectorAll('.gf-row').forEach((row) => row.classList.toggle('is-on', row.dataset.flow === key));
  const open = (key) => {
    if (closeCurrent) closeCurrent();
    const f = FLOWS[key];
    const scrim = h('<div class="ledger-scrim is-open"></div>');
    const drawer = h(`<aside class="ledger-drawer is-open gf-drawer${wide ? ' is-640' : ''}" role="dialog" aria-modal="true" aria-label="${f.name}">
      <div class="ledger-drawer-head"><div class="ledger-drawer-title">${f.name}<span class="drawer-title-sub font-mono">${f.sid}</span></div>${PF.genIcon(f)}<button type="button" class="icon-button" aria-label="关闭">${PF.ICON.close}</button></div>
      <div class="ledger-drawer-body"></div>
    </aside>`);
    $('.ledger-drawer-body', drawer).append(configBox(f), ...(PF.extraBoxes ? PF.extraBoxes(f) : []), periodsBox(f));
    document.body.append(scrim, drawer);
    mark(key);
    closeCurrent = () => { scrim.remove(); drawer.remove(); mark(''); closeCurrent = null; };
    scrim.addEventListener('click', () => closeCurrent());
    $('.icon-button[aria-label="关闭"]', drawer).addEventListener('click', () => closeCurrent());
  };
  scroll.addEventListener('click', (e) => {
    const row = e.target.closest('.gf-row');
    if (!row || e.target.closest('.ledger-switch, .icon-button')) return; // 行上的开关与生成钮自己用,不打开抽屉
    open(row.dataset.flow);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && closeCurrent) closeCurrent(); });
  if (openParam !== 'none') open(openParam);

  PF.bar([
    ['抽屉', 'open', openParam, [['none', '不开'], ['brief', '日报'], ['picks', '精读']]],
    PF.laneStateGroup,
    PF.scopeGroup,
    PF.themeGroup,
  ]);
});
