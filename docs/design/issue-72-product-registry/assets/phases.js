/* 细节样页 details/phases.html 用:加一个**假想的**复杂流程,看非线性的加工在泳道与抽屉里怎么表现。
   平台上没有这个流程,也没有计划做它;它只是用来试版面的。夹在 kit.js 与 lane.js 之间载入。
   ?ph=a|b|c   泳道「加工」一格的三种画法:a 阶段条 + 记号 / b 小结构图 / c 只写概况
   ?open=weekly 打开假想流程的抽屉(里面多一个「流程」框:按阶段分列的结构图)
   ?state=run   假想流程正在跑第二个阶段(三路并行,完成了两路) */
(() => {
  const { FLOWS, h, STATE, TODAY } = PF;
  const ph = ['a', 'b', 'c'].includes(PF.Q.get('ph')) ? PF.Q.get('ph') : 'a';

  // 阶段(有序)与每个阶段里的节点;同一阶段里的多个节点是并行的
  const PHASES = [
    { name: '归并', nodes: ['按事件归并'] },
    { name: '撰写', nodes: ['事件小结', '深度阅读', '数据核对'] },
    { name: '汇编', nodes: ['汇编'] },
    { name: '配图', nodes: ['配图'], optional: true },
    { name: '成稿', nodes: ['成稿'] },
  ];
  const running = STATE === 'run';
  const weekly = {
    key: 'weekly', name: '专题周报(假想)', sid: 'dorami_weekly_topics', cron: '0 9 * * 1', next: '4 天 16:48',
    on: true, last: running ? 'run' : 'ok', running, input: '近 7 期日报', inputSub: '11-26 08:31',
    steps: PHASES.map((p) => p.name), phases: PHASES,
    stepNow: 1, nodeDone: 2, stepNote: '撰写 2/3',
    periods: ['2026-11-23', '2026-11-16', '2026-11-09', '2026-11-02'].map((date, i) => ({ date, time: i % 2 ? '09:03' : '09:02', meta: '8 个事件', model: '' })),
    since: '2026-11-02', issues: {},
  };
  weekly.byDate = Object.fromEntries(weekly.periods.map((p) => [p.date, p]));
  weekly.total = weekly.periods.length;
  FLOWS.weekly = weekly;
  // 演示时不让另外两个流程也显示成「生成中」
  if (running) Object.assign(FLOWS.picks, { running: false, last: 'ok' });

  // 这个流程不是每天一期:产出一格写「最近一期」,圆点是近 7 期(现有两个流程都是每天一期,写的是「今天」与近 7 日)
  const todayLine = PF.todayLine;
  PF.todayLine = (f) => (f.key !== 'weekly' ? todayLine(f) : (f.running ? ['is-run', '生成中'] : ['', `11-23 ${f.periods[0].time} · ${f.periods[0].meta}`]));
  const dots = PF.dots;
  PF.dots = (f) => (f.key !== 'weekly' ? dots(f) : `<span class="tt-dots" title="近 7 期">${[0, 0, 0, 1, 1, 1, 1].map((ok) => `<i class="${ok ? 'ok' : ''}"></i>`).join('')}</span>`);
  const knobs = PF.knobs;
  PF.knobs = (f, parts) => (f.key !== 'weekly' ? knobs(f, parts) : [PF.param('事件个数', PF.number(8, 3, 12)), PF.param('配图', PF.toggle('配图'))].join(''));

  /* ── 某个节点 / 阶段现在的状态 ── */
  const phaseState = (f, i) => (!f.running ? '' : i < f.stepNow ? ' is-done' : i === f.stepNow ? ' is-now' : '');
  const nodeState = (f, i, k) => (!f.running ? '' : i < f.stepNow ? ' is-done' : i > f.stepNow ? '' : k < f.nodeDone ? ' is-done' : ' is-now');

  /* ── a:仍是一行阶段块。并行的阶段在名字后面标路数,可能跳过的阶段用虚线 ── */
  const strip = (f) => `<div class="ledger-pipeline" aria-label="生成阶段">${f.phases.map((p, i) => `<span class="ledger-pipeline-step${p.optional ? ' is-optional' : ''}${phaseState(f, i)}" title="${p.nodes.join(' · ')}${p.optional ? '(可能跳过)' : ''}">${p.name}${p.nodes.length > 1 ? `<span class="gf-fan">×${p.nodes.length}</span>` : ''}</span>`).join('')}</div>`;
  /* ── b / 抽屉:按阶段分列的结构图。一列一个阶段,并行的节点上下排;连线画在后面的 svg 上 ── */
  // 泳道里地方小:只有一个节点的阶段直接写阶段名
  const graph = (f, withHeads) => `<div class="gf-graph${withHeads ? ' has-heads' : ''}"><svg class="gf-graph-wires" aria-hidden="true"></svg>${f.phases.map((p, i) => `<div class="gf-graph-col">${withHeads ? `<span class="gf-graph-head">${p.name}</span>` : ''}<div class="gf-graph-nodes">${p.nodes.map((n, k) => `<span class="gf-node${p.optional ? ' is-optional' : ''}${nodeState(f, i, k)}">${!withHeads && p.nodes.length === 1 ? p.name : n}</span>`).join('')}</div></div>`).join('')}</div>`;
  /* ── c:只写概况 ── */
  const gist = (f) => {
    const fan = f.phases ? Math.max(...f.phases.map((p) => p.nodes.length)) : 1;
    const text = `${f.steps.length} 个阶段${fan > 1 ? ` · ${fan} 路并行` : ''}`;
    return `<div class="gf-main">${f.running ? `${f.steps[f.stepNow]} · 第 ${f.stepNow + 1} / ${f.steps.length} 阶段` : text}</div>`;
  };

  const stepsHTML = PF.stepsHTML;
  // 线性的流程(日报、精读)在 a、b 两种画法里都还是原来那一行;c 把所有流程都换成一句话
  PF.stepsHTML = (f) => {
    if (ph === 'c') return gist(f);
    if (!f.phases) return stepsHTML(f);
    return ph === 'b' ? graph(f, false) : strip(f);
  };
  // 抽屉里多一个「流程」框(只有声明了结构的流程才有)
  PF.extraBoxes = (f) => (f.phases ? [h(`<section class="acq-panel gf-box gf-flowbox"><div class="acq-panel-head"><span class="tools-title">流程</span></div><div class="acq-panel-body">${graph(f, true)}</div></section>`)] : []);

  /* ── 连线:每一列的每个节点连到下一列的每个节点(这个例子里相邻两列都是全连) ── */
  const wire = (root) => {
    const svg = root.querySelector('.gf-graph-wires');
    const box = root.getBoundingClientRect();
    const cols = Array.from(root.querySelectorAll('.gf-graph-nodes')).map((col) => Array.from(col.children).map((n) => n.getBoundingClientRect()));
    let d = '';
    for (let i = 0; i + 1 < cols.length; i += 1) {
      cols[i].forEach((a) => cols[i + 1].forEach((b) => {
        const x1 = a.right - box.left; const y1 = a.top + a.height / 2 - box.top;
        const x2 = b.left - box.left; const y2 = b.top + b.height / 2 - box.top;
        const mid = (x1 + x2) / 2;
        d += `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2} `;
      }));
    }
    svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
    svg.innerHTML = `<path d="${d}"></path>`;
  };
  const wireAll = () => document.querySelectorAll('.gf-graph').forEach(wire);
  // lane.js 画完泳道、开完抽屉之后再连线(字体载入会挪动节点,所以等字体就绪再画一遍)
  shellReady.then(() => {
    requestAnimationFrame(() => { document.querySelectorAll('.gf-row').forEach((row) => row.classList.toggle('is-tall', ph === 'b' && row.dataset.flow === 'weekly')); wireAll(); });
    document.fonts.ready.then(wireAll);
    new MutationObserver(() => requestAnimationFrame(wireAll)).observe(document.body, { childList: true });
    window.addEventListener('resize', wireAll);
  });
})();
