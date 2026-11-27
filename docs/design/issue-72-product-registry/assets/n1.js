/* 泳道(定稿方向):流程 × 阶段。?open=none|brief|picks|all 控制哪一行点开(默认都收起);
   ?state=ok|run|bad|new(new:精读刚上线,还没生成过,定时关着);?scope=all|custom(日报的取材范围)。
   ?title= / ?rail= 换页面标题与导轨短名(在 shell.js),?obj= 换第一列的列头(默认「流程」)——比较叫法时用。
   行上的三样(cron、开关、立即生成)各自即时生效;点开后「取材」「加工」两列里改的东西由横跨这两列的「保存配置」提交。 */
shellReady.then(({ paper, parts }) => {
  const { FLOWS, h, $ } = PF;
  PF.init(parts);
  const openParam = PF.Q.get('open') || 'none';

  paper.classList.add('lane-paper');
  const wrap = h('<div class="lane-wrap"></div>');
  wrap.append(h('<div class="lane-band lane-grid"><span>流程</span><span class="is-flow">取材</span><span class="is-flow">加工</span><span>产出</span><span>定时</span><span>生成</span></div>'));
  if (PF.Q.get('obj')) $('.lane-band span', wrap).textContent = PF.Q.get('obj');

  Object.values(FLOWS).forEach((f) => {
    const [cls, line] = PF.todayLine(f);
    const lane = h(`<section class="lane-lane" data-flow="${f.key}">
      <div class="lane-row lane-grid" role="button" tabindex="0" aria-expanded="false">
        <div class="lane-cell lane-id"><span class="lane-caret">${PF.CHEV}</span>${PF.lastMark(f)}<div class="run-obj-txt"><div class="run-obj-name">${f.name}</div><div class="run-obj-sub">${f.sid}</div></div></div>
        <div class="lane-cell"><div class="lane-main">${f.input}</div><div class="lane-sub"><span class="run-obj-sub${f.inputWarn ? ' is-warn' : ''}">${f.inputWarn || f.inputSub}</span></div></div>
        <div class="lane-cell">${PF.stepsHTML(f)}${f.running ? `<div class="pipeline-note">${f.stepNote}</div>` : ''}</div>
        <div class="lane-cell"><div class="lane-main ${cls}">${line}</div><div class="lane-sub">${PF.dots(f)}<span class="run-obj-sub">${f.total} 期</span></div></div>
        <div class="lane-cell"><div class="pf-rhythm">${PF.cron(f)}${PF.toggle(`定时生成 ${f.name}`, f.on)}</div><div class="run-obj-sub">${f.on ? `距下次 ${f.next}` : '未开启'}</div></div>
        <div class="lane-cell">${PF.genIcon(f)}</div>
      </div>
      <div class="lane-more lane-grid" hidden>
        <div class="lane-cell"></div>
        <div class="lane-cell lane-stack">${PF.inputControls(f, parts)}</div>
        <div class="lane-cell lane-stack"><div class="inspector-params">${PF.knobs(f, parts)}</div></div>
        <div class="lane-cell lane-list"></div>
        <div class="lane-save"><button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs">保存配置</button></div>
      </div>
    </section>`);
    if (f.key === 'brief') $('.lane-more .lane-stack', lane).append(parts.scope);
    $('.lane-list', lane).append(PF.periodList(f));

    const row = $('.lane-row', lane);
    const setOpen = (open) => {
      lane.classList.toggle('is-open', open);
      row.setAttribute('aria-expanded', open);
      $('.lane-more', lane).hidden = !open;
    };
    row.addEventListener('click', (e) => {
      if (e.target.closest('input, .ledger-switch, .icon-button')) return; // 行上的控件自己用,不触发展开
      setOpen(!lane.classList.contains('is-open'));
    });
    setOpen(openParam === 'all' || openParam === f.key);
    wrap.append(lane);
  });
  paper.append(wrap);

  PF.bar([
    ['点开', 'open', openParam, [['none', '都收起'], ['brief', '日报'], ['picks', '精读'], ['all', '都点开']]],
    PF.laneStateGroup,
    PF.scopeGroup,
    PF.themeGroup,
  ]);
});
