/* 第五轮:泳道只留收起态;点一行,在别处打开这个流程的详情。
   ?det=drawer|modal|page  详情用哪种容器(右侧抽屉 / 弹窗 / 独立页;默认抽屉)
   ?open=none|brief|picks  打开谁的详情(默认不开)
   ?dw=wide                抽屉加宽到 640(只对抽屉有效)
   ?state=ok|run|bad|new   ?scope=all|custom   ?theme=light|dark   ?bare
   行上只留两样即时的操作:开关、立即生成。cron 在行上只读,和取材、旋钮一起在详情的「配置」里改,由「保存配置」提交(与现网相同)。
   详情的题条里另有一个立即生成(抽屉 / 弹窗开着时行被遮住,点不到);开关只在行上(独立页没有行,题条里放一份)。 */
shellReady.then(({ paper, parts }) => {
  const { FLOWS, SOURCES, h, $ } = PF;
  PF.init(parts);
  const det = ['drawer', 'modal', 'page'].includes(PF.Q.get('det')) ? PF.Q.get('det') : 'drawer';
  const openParam = FLOWS[PF.Q.get('open')] ? PF.Q.get('open') : 'none';
  const wide = PF.Q.get('dw') === 'wide'; // 抽屉加宽到 640(现网抽屉只有 470 一种宽度)

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

  /* ── 详情里的两个框(三种容器共用)──────────────────────────────────── */
  const seg = `<div class="mini-seg" role="tablist" aria-label="日报源范围"><button type="button" class="mini-seg-btn${PF.SCOPE === 'all' ? ' is-on' : ''}" role="tab" aria-selected="${PF.SCOPE === 'all'}">全部来源</button><button type="button" class="mini-seg-btn${PF.SCOPE === 'custom' ? ' is-on' : ''}" role="tab" aria-selected="${PF.SCOPE === 'custom'}">自定名单</button></div>`;
  // 自定名单:现网 .brief-scope-list / .brief-scope-row 的标记原样(勾选框、名字、篇数与默认类型),下面是已选个数
  const scopeList = () => `<div class="brief-scope-list" role="group" aria-label="日报来源名单">${SOURCES.map((s) => `<label class="brief-scope-row"><input type="checkbox" class="h-3.5 w-3.5"${s.on ? ' checked' : ''}><span class="brief-scope-name">${s.name}</span><span class="tiny-meta shrink-0">${s.count} 篇${s.kind ? ` · ${s.kind}` : ''}</span></label>`).join('')}</div><p class="tiny-meta mt-1">已选 ${SOURCES.filter((s) => s.on).length} 个</p>`;
  const configBox = (f) => {
    const rows = [PF.param('定时', `<input class="inspector-param-input gf-cron-input" value="${f.cron}" aria-label="定时(cron 5 段)">`)];
    if (f.key === 'brief') {
      rows.push(PF.SCOPE === 'custom'
        ? `<div class="inspector-param is-block"><div class="gf-param-line"><label class="inspector-param-label">取材</label>${seg}</div>${scopeList()}</div>`
        : PF.param('取材', seg));
    } else {
      rows.push(PF.param('取材', `<button type="button" class="inspector-metabtn" title="去源管理看这个源">${f.input}</button>`));
    }
    rows.push(PF.knobs(f, parts));
    if (f.key === 'brief') rows.push(PF.param('增量游标', `<code class="brief-cursor-val" title="${parts.cursor}">${parts.cursor}</code><button type="button" class="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）">重置</button>`));
    const box = h(`<section class="acq-panel gf-box gf-config"><div class="acq-panel-head"><span class="tools-title">配置</span></div><div class="acq-panel-body"><div class="inspector-params">${rows.join('')}</div><div class="gf-save"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs">保存配置</button></div></div></section>`);
    if (f.key === 'brief') $('.gf-save', box).before(parts.scope.cloneNode(true)); // 现网的「取材与去重口径」
    return box;
  };
  const periodsBox = (f) => {
    const box = h('<section class="acq-panel gf-box gf-periods"><div class="acq-panel-head"><span class="tools-title">往期</span></div><div class="acq-panel-body"></div></section>');
    // 生成中:阶段条与当前这一步出现在往期的顶上(现网也是出现在「近期日报」栏头下面)
    if (f.running) $('.acq-panel-body', box).append(h(`<div class="gf-running">${PF.stepsHTML(f)}<div class="pipeline-note">${f.stepNote}</div></div>`));
    $('.acq-panel-body', box).append(PF.periodList(f));
    return box;
  };

  /* ── 三种容器 ─────────────────────────────────────────────────────── */
  let closeCurrent = null;
  const closeBtn = `<button type="button" class="icon-button gf-close" aria-label="关闭">${PF.ICON.close}</button>`;
  const mark = (key) => scroll.querySelectorAll('.gf-row').forEach((row) => row.classList.toggle('is-on', row.dataset.flow === key));

  const openers = {
    // 右侧抽屉:现网 .ledger-scrim + .ledger-drawer(管理面的表格点一行基本都是它)
    drawer(f) {
      const scrim = h('<div class="ledger-scrim is-open"></div>');
      const drawer = h(`<aside class="ledger-drawer is-open gf-drawer${wide ? ' is-wide' : ''}" role="dialog" aria-modal="true" aria-label="${f.name}">
        <div class="ledger-drawer-head"><div class="ledger-drawer-title">${f.name}<span class="drawer-title-sub font-mono">${f.sid}</span></div>${PF.genIcon(f)}${closeBtn}</div>
        <div class="ledger-drawer-body"></div>
      </aside>`);
      $('.ledger-drawer-body', drawer).append(configBox(f), periodsBox(f));
      document.body.append(scrim, drawer);
      mark(f.key);
      scrim.addEventListener('click', () => closeCurrent());
      return () => { scrim.remove(); drawer.remove(); mark(''); };
    },
    // 弹窗:现网 Modal 的 .modal-overlay + .modal-panel(max-w-5xl),头部沿用 .form-sheet-head
    modal(f) {
      const overlay = h(`<div class="modal-overlay" data-modal-layer=""><div class="modal-panel max-w-5xl gf-modal" role="dialog" aria-modal="true" aria-label="${f.name}">
        <div class="form-sheet-head"><h3 class="card-title">${f.name}<span class="run-obj-sub">${f.sid}</span></h3><span class="gf-acts">${PF.genIcon(f)}${closeBtn}</span></div>
        <div class="gf-modal-body"><div class="gf-side"></div><div class="gf-rest"></div></div>
      </div></div>`);
      $('.gf-side', overlay).append(configBox(f));
      $('.gf-rest', overlay).append(periodsBox(f));
      document.body.append(overlay);
      mark(f.key);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) closeCurrent(); });
      return () => { overlay.remove(); mark(''); };
    },
    // 独立页:纸里换成这一个流程。泳道不在了,所以题条里要再放一份 定时 / 开关 / 立即生成
    page(f) {
      const page = h(`<div class="gf-page">
        <div class="gf-page-head"><button type="button" class="icon-button pf-icon gf-back gf-close" title="返回" aria-label="返回生成流程">${PF.CHEV}</button>${idHTML(f)}${whenHTML(f)}${PF.genIcon(f)}</div>
        <div class="gf-page-body"><div class="gf-side"></div><div class="gf-rest"></div></div>
      </div>`);
      $('.gf-side', page).append(configBox(f));
      $('.gf-rest', page).append(periodsBox(f));
      scroll.hidden = true;
      paper.append(page);
      return () => { page.remove(); scroll.hidden = false; };
    },
  };
  const open = (key) => {
    if (closeCurrent) closeCurrent();
    const close = openers[det](FLOWS[key]);
    closeCurrent = () => { close(); closeCurrent = null; };
    document.querySelectorAll('.gf-close').forEach((btn) => btn.addEventListener('click', () => closeCurrent && closeCurrent()));
  };
  scroll.addEventListener('click', (e) => {
    const row = e.target.closest('.gf-row');
    if (!row || e.target.closest('.ledger-switch, .icon-button')) return; // 行上的开关与生成钮自己用,不打开详情
    open(row.dataset.flow);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && closeCurrent) closeCurrent(); });
  if (openParam !== 'none') open(openParam);

  PF.bar([
    ['点开用', 'det', det, [['drawer', '抽屉'], ['modal', '弹窗'], ['page', '独立页']]],
    ['点开', 'open', openParam, [['none', '不点开'], ['brief', '日报'], ['picks', '精读']]],
    ['抽屉宽', 'dw', wide ? 'wide' : 'std', [['std', '470'], ['wide', '640']]],
    PF.laneStateGroup,
    PF.scopeGroup,
    PF.themeGroup,
  ]);
});
