shellReady.then(({ paper, shell, parts, state, theme }) => {
  const dir = document.documentElement.dataset.g || '1';
  const urlParams = new URLSearchParams(window.location.search);
  const initView = urlParams.get('view') || '';

  const ICONS = {
    sliders: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>',
    prev: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>',
    next: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
    trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>',
    caretRight: '<svg class="gx-vault-caret" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>'
  };

  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // 构造真实与模拟数据集
  const rawBriefs = (window.PF_DATA && Array.isArray(window.PF_DATA.briefPeriods))
    ? window.PF_DATA.briefPeriods
    : [];
  const rawRepos = (window.PF_DATA && Array.isArray(window.PF_DATA.trendingRepos))
    ? window.PF_DATA.trendingRepos
    : [
        { repo: 'block/buzz', lang: 'Rust', stars: 6744, today: '2,460' },
        { repo: 'anthropics/mcp-registry', lang: 'TypeScript', stars: 9412, today: '1,820' },
        { repo: 'deepseek-ai/Janus-Flow', lang: 'Python', stars: 15320, today: '1,540' },
        { repo: 'vercel/ai-elements', lang: 'TypeScript', stars: 4890, today: '980' },
        { repo: 'ggml-org/llama.cpp', lang: 'C++', stars: 76400, today: '740' },
        { repo: 'astral-sh/ty', lang: 'Rust', stars: 8210, today: '690' }
      ];

  function buildBriefPeriods() {
    const list = rawBriefs.map((p, idx) => ({
      uid: 'brief-' + p.date,
      flowId: 'brief',
      flowShort: '日报',
      flowName: '哆啦美·AI资讯日报',
      date: p.date,
      time: p.time || '08:31',
      count: p.count || (p.items ? p.items.length : 30),
      model: p.model || 'deepseek-chat',
      metaText: `收录 ${p.count || (p.items ? p.items.length : 30)} 条 · ${p.model || 'deepseek-chat'}`,
      shortMeta: `${p.count || 30} 条`,
      items: Array.isArray(p.items) ? p.items : []
    }));
    if (state === 'bad') {
      return list.filter((p) => p.date !== '2026-11-26');
    }
    return list;
  }

  function buildTrendingPeriods() {
    const dates = [
      '2026-11-26', '2026-11-25', '2026-11-23', '2026-11-22',
      '2026-11-21', '2026-11-20', '2026-11-19', '2026-11-18',
      '2026-11-17', '2026-11-16', '2026-11-15', '2026-11-14'
    ];
    const filtered = state === 'bad' ? dates.filter((d) => d !== '2026-11-26') : dates;
    return filtered.map((d, idx) => {
      const repos = [];
      for (let i = 0; i < 6; i++) {
        repos.push(rawRepos[(idx * 2 + i) % rawRepos.length]);
      }
      const minute = (idx % 3 === 1) ? '09:31' : '09:30';
      return {
        uid: 'trending-' + d,
        flowId: 'trending',
        flowShort: '精读',
        flowName: 'GitHub Trending 精读',
        date: d,
        time: minute,
        count: 6,
        poolCount: 25,
        metaText: '选 6 个 · 榜单 25 个',
        shortMeta: '6 个',
        repos
      };
    });
  }

  const WEEK_DATES = [
    '2026-11-20', '2026-11-21', '2026-11-22', '2026-11-23',
    '2026-11-24', '2026-11-25', '2026-11-26'
  ];

  const store = {
    cursor: (parts && parts.cursor) || '2026-06-06T21:51:37',
    flows: {
      brief: {
        id: 'brief',
        shortName: '日报',
        name: '哆啦美·AI资讯日报',
        feedId: 'dorami_daily_brief',
        enabled: true,
        cron: (parts && parts.values && parts.values.cron) || '30 8 * * *',
        clock: '08:30',
        hourFrac: (8 + 30 / 60) / 24,
        nextIn: '16:18:19',
        sourceMode: 'all',
        knobs: {
          topN: (parts && parts.values && parts.values.topN) || '12',
          minScore: (parts && parts.values && parts.values.minScore) || '6',
          minItems: (parts && parts.values && parts.values.minItems) || '8',
          paperMin: (parts && parts.values && parts.values.paperMin) || '7'
        },
        stages: ['收集', '评分', '精选', '点评', '成稿'],
        lastStatus: state === 'bad' ? 'bad' : 'ok',
        stampClass: state === 'bad' ? 'stamp-bad' : 'stamp-ok',
        stampText: state === 'bad' ? '失败' : '已生成',
        reason: state === 'bad' ? 'LLM 请求超时' : '',
        activeStageIdx: state === 'bad' ? 3 : 4,
        week: WEEK_DATES.map((d) => ({
          date: d,
          status: (d === '2026-11-26' && state === 'bad') ? 'bad' : 'ok'
        })),
        periods: buildBriefPeriods()
      },
      trending: {
        id: 'trending',
        shortName: '精读',
        name: 'GitHub Trending 精读',
        feedId: 'dorami_trending_picks',
        enabled: true,
        cron: '30 9 * * *',
        clock: '09:30',
        hourFrac: (9 + 30 / 60) / 24,
        nextIn: '17:18:19',
        lastIngest: state === 'bad' ? '11-25 07:00' : '11-26 07:00',
        knobs: {
          pickN: '6',
          aiOnly: true
        },
        stages: ['读榜', '挑选', '成稿'],
        lastStatus: state === 'run' ? 'run' : (state === 'bad' ? 'warn' : 'ok'),
        stampClass: state === 'run' ? 'stamp-run' : (state === 'bad' ? 'stamp-warn' : 'stamp-ok'),
        stampText: state === 'run' ? '生成中' : (state === 'bad' ? '已跳过' : '已生成'),
        reason: state === 'run'
          ? '从 25 个仓库里挑 6 个'
          : (state === 'bad' ? '当日榜单未入库' : ''),
        activeStageIdx: state === 'run' ? 1 : (state === 'bad' ? 0 : 2),
        week: WEEK_DATES.map((d) => {
          if (d === '2026-11-24') return { date: d, status: 'warn' };
          if (d === '2026-11-26' && state === 'bad') return { date: d, status: 'warn' };
          if (d === '2026-11-26' && state === 'run') return { date: d, status: 'run' };
          return { date: d, status: 'ok' };
        }),
        periods: buildTrendingPeriods()
      }
    }
  };

  function cronToClock(cronStr, fallback) {
    const segs = String(cronStr || '').trim().split(/\s+/);
    if (segs.length >= 2 && /^\d+$/.test(segs[0]) && /^\d+$/.test(segs[1])) {
      const m = segs[0].padStart(2, '0');
      const h = segs[1].padStart(2, '0');
      return `${h}:${m}`;
    }
    return fallback;
  }

  // 构建阶段流水线 HTML
  function renderPipelineHTML(flow) {
    const steps = flow.stages.map((label, idx) => {
      let cls = 'ledger-pipeline-step';
      if (flow.lastStatus === 'ok') {
        cls += ' is-done';
      } else if (flow.lastStatus === 'run') {
        if (idx < flow.activeStageIdx) cls += ' is-done';
        else if (idx === flow.activeStageIdx) cls += ' is-now';
      } else if (flow.lastStatus === 'bad') {
        if (idx < flow.activeStageIdx) cls += ' is-done';
        else if (idx === flow.activeStageIdx) cls += ' is-now is-err';
      } else if (flow.lastStatus === 'warn') {
        if (idx === flow.activeStageIdx) cls += ' is-now';
      }
      return `<span class="${cls}">${esc(label)}</span>`;
    }).join('');
    return `<div class="ledger-pipeline">${steps}</div>`;
  }

  // 构建单期正文 DOM (日报条目或精读仓库)
  function createPeriodContentEl(period) {
    const wrap = document.createElement('div');
    if (period.flowId === 'brief') {
      wrap.className = 'gx-article-list';
      const items = period.items && period.items.length ? period.items : [];
      items.forEach((it) => {
        const row = document.createElement('div');
        row.className = 'gx-article-row';
        const numScore = Number(it.score || 0);
        const scoreCls = numScore >= 8.5 ? 'gx-article-score' : 'gx-article-score is-mid';
        row.innerHTML = `
          <span class="${scoreCls}">${numScore ? numScore.toFixed(1) : '—'}</span>
          <span class="gx-article-title">${esc(it.title)}</span>
          <span class="gx-article-tag">${esc(it.kind || '')}${it.kind && it.source ? ' · ' : ''}${esc(it.source || '')}</span>
        `;
        wrap.appendChild(row);
      });
    } else {
      wrap.className = 'gx-repo-list';
      (period.repos || []).forEach((r) => {
        const row = document.createElement('div');
        row.className = 'gx-repo-row';
        row.innerHTML = `
          <div class="gx-repo-top">
            <div class="gx-repo-name-wrap">
              <span class="gx-repo-name">${esc(r.repo)}</span>
              <span class="gx-repo-lang">${esc(r.lang)}</span>
            </div>
            <span class="gx-repo-stars">★ ${Number(r.stars || 0).toLocaleString()}<em>+${esc(r.today)}</em></span>
          </div>
          <div class="gx-repo-placeholder">一句中文说明；为什么值得看</div>
        `;
        wrap.appendChild(row);
      });
    }
    return wrap;
  }

  // 使用现网 parts.rows 克隆创建往期行
  function createBriefRunNode(period, opts = {}) {
    const { showFlowBadge = false, expanded = false, isViewing = false, onSelect, onToggleExpand, onDelete } = opts;
    let node;
    if (parts && parts.rows && parts.rows[0]) {
      node = parts.rows[0].cloneNode(true);
      const extra = Array.from(node.children).filter((ch) => !ch.classList.contains('brief-run-line'));
      extra.forEach((el) => el.remove());
    } else {
      node = document.createElement('div');
      node.className = 'brief-run';
      node.innerHTML = `
        <div class="brief-run-line">
          <button type="button" class="brief-run-head">
            <svg class="brief-run-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
            <span class="brief-run-date"></span>
            <span class="brief-run-meta"></span>
            <span class="stamp stamp-ok">已生成</span>
          </button>
          <button type="button" class="brief-run-del">${ICONS.trash}</button>
        </div>
      `;
    }

    node.classList.toggle('is-viewing', Boolean(isViewing));
    node.classList.toggle('gx-open', Boolean(expanded));

    const headBtn = node.querySelector('.brief-run-head');
    const dateEl = node.querySelector('.brief-run-date');
    const metaEl = node.querySelector('.brief-run-meta');
    const stampEl = node.querySelector('.stamp');
    const delBtn = node.querySelector('.brief-run-del');

    const oldBadge = node.querySelector('.gx-flow-badge');
    if (oldBadge) oldBadge.remove();

    if (dateEl) {
      dateEl.textContent = showFlowBadge ? `${period.date} ${period.time}` : period.date;
      if (showFlowBadge && headBtn) {
        const badge = document.createElement('span');
        badge.className = 'gx-flow-badge';
        badge.textContent = period.flowShort;
        headBtn.insertBefore(badge, dateEl);
      }
    }
    if (metaEl) {
      metaEl.textContent = showFlowBadge ? period.metaText : `${period.time} · ${period.metaText}`;
    }
    if (stampEl) {
      stampEl.className = 'stamp stamp-ok';
      stampEl.textContent = '已生成';
    }

    if (expanded) {
      const expandBox = document.createElement('div');
      expandBox.className = 'gx-run-expand';
      expandBox.appendChild(createPeriodContentEl(period));
      node.appendChild(expandBox);
    }

    if (headBtn) {
      headBtn.addEventListener('click', () => {
        if (onSelect) onSelect(period);
        else if (onToggleExpand) onToggleExpand(period);
      });
    }
    if (delBtn) {
      delBtn.title = '删除该期';
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (onDelete) onDelete(period);
      });
    }
    return node;
  }

  // 使用现网 parts.pager 克隆创建分页条
  function createPagerNode(page, totalItems, pageSize, onPage) {
    const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
    let row;
    if (parts && parts.pager) {
      row = parts.pager.cloneNode(true);
    } else {
      row = document.createElement('div');
      row.className = 'brief-pager-row';
      row.innerHTML = '<span class="tiny-meta"></span><div class="pager"></div>';
    }

    const meta = row.querySelector('.tiny-meta') || row.firstElementChild;
    let pager = row.querySelector('.pager');
    if (!pager) {
      pager = document.createElement('div');
      pager.className = 'pager';
      row.appendChild(pager);
    }
    if (meta && meta !== pager) {
      const start = totalItems === 0 ? 0 : (page - 1) * pageSize + 1;
      const end = Math.min(totalItems, page * pageSize);
      meta.textContent = `${start}–${end} / 共 ${totalItems} 期`;
    }

    pager.innerHTML = '';
    for (let p = 1; p <= totalPages; p++) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pager-btn' + (p === page ? ' is-on' : '');
      btn.textContent = String(p);
      btn.addEventListener('click', () => onPage(p));
      pager.appendChild(btn);
    }
    return row;
  }

  // 构建流程配置面板 (复用在三个方向各自的容器内)
  function buildFlowConfigBlocks(flow, onSave, onCancel) {
    const frag = document.createDocumentFragment();
    let draftSourceMode = flow.sourceMode || 'all';
    let draftAiOnly = Boolean(flow.knobs.aiOnly);

    // 1. 定时
    const cronSec = document.createElement('div');
    cronSec.className = 'gx-cfg-section';
    cronSec.innerHTML = `
      <div class="gx-cfg-sec-head">
        <span>定时</span>
        <span class="gx-cfg-sec-sub gx-mono">距下次 ${esc(flow.nextIn)}</span>
      </div>
      <div class="gx-cfg-sec-body">
        <div class="gx-cfg-kv">
          <span class="gx-cfg-k">Cron 表达式</span>
          <input type="text" class="form-input font-mono pf-cron gx-input-cron" value="${esc(flow.cron)}" />
        </div>
      </div>
    `;
    frag.appendChild(cronSec);

    // 2. 取材
    const srcSec = document.createElement('div');
    srcSec.className = 'gx-cfg-section';
    if (flow.id === 'brief') {
      srcSec.innerHTML = `
        <div class="gx-cfg-sec-head"><span>取材</span></div>
        <div class="gx-cfg-sec-body">
          <div class="mini-seg is-fluid">
            <button type="button" class="mini-seg-btn ${draftSourceMode === 'all' ? 'is-on' : ''}" data-mode="all">全部来源</button>
            <button type="button" class="mini-seg-btn ${draftSourceMode === 'custom' ? 'is-on' : ''}" data-mode="custom">自定名单</button>
          </div>
          <div class="gx-cursor-box">
            <span class="gx-cursor-val">增量游标 ${esc(store.cursor)}</span>
            <button type="button" class="inspector-metabtn gx-reset-cursor">重置</button>
          </div>
          <div class="gx-scope-slot"></div>
        </div>
      `;
      srcSec.querySelectorAll('.mini-seg-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          draftSourceMode = btn.dataset.mode;
          srcSec.querySelectorAll('.mini-seg-btn').forEach((b) => b.classList.toggle('is-on', b === btn));
        });
      });
      const resetBtn = srcSec.querySelector('.gx-reset-cursor');
      const cursorVal = srcSec.querySelector('.gx-cursor-val');
      resetBtn.addEventListener('click', () => {
        store.cursor = '2026-11-26T16:11:00';
        cursorVal.textContent = '增量游标 ' + store.cursor;
      });
      if (parts && parts.scope) {
        srcSec.querySelector('.gx-scope-slot').appendChild(parts.scope);
      }
    } else {
      const warnCls = state === 'bad' ? 'stamp stamp-warn' : 'tiny-meta gx-mono';
      srcSec.innerHTML = `
        <div class="gx-cfg-sec-head"><span>取材</span></div>
        <div class="gx-cfg-sec-body">
          <div class="gx-cfg-kv">
            <span class="gx-cfg-k">GitHub Trending 日榜</span>
            <span class="${warnCls}">最近入库 ${esc(flow.lastIngest)}</span>
          </div>
        </div>
      `;
    }
    frag.appendChild(srcSec);

    // 3. 加工旋钮
    const knobSec = document.createElement('div');
    knobSec.className = 'gx-cfg-section';
    if (flow.id === 'brief') {
      knobSec.innerHTML = `
        <div class="gx-cfg-sec-head">
          <span>旋钮</span>
          <span class="gx-cfg-sec-sub">${esc(flow.stages.join(' · '))}</span>
        </div>
        <div class="gx-cfg-sec-body is-flush">
          <div class="inspector-params">
            <div class="inspector-param">
              <label class="inspector-param-label">每期条数 (1–50)</label>
              <input type="number" min="1" max="50" class="inspector-param-input" data-k="topN" value="${esc(flow.knobs.topN)}" />
            </div>
            <div class="inspector-param">
              <label class="inspector-param-label">入选门槛 (0–10)</label>
              <input type="number" min="0" max="10" class="inspector-param-input" data-k="minScore" value="${esc(flow.knobs.minScore)}" />
            </div>
            <div class="inspector-param">
              <label class="inspector-param-label">正文保底条数 (0–50)</label>
              <input type="number" min="0" max="50" class="inspector-param-input" data-k="minItems" value="${esc(flow.knobs.minItems)}" />
            </div>
            <div class="inspector-param">
              <label class="inspector-param-label">论文门槛 (0–10)</label>
              <input type="number" min="0" max="10" class="inspector-param-input" data-k="paperMin" value="${esc(flow.knobs.paperMin)}" />
            </div>
          </div>
        </div>
      `;
    } else {
      knobSec.innerHTML = `
        <div class="gx-cfg-sec-head">
          <span>旋钮</span>
          <span class="gx-cfg-sec-sub">${esc(flow.stages.join(' · '))}</span>
        </div>
        <div class="gx-cfg-sec-body is-flush">
          <div class="inspector-params">
            <div class="inspector-param">
              <label class="inspector-param-label">挑选个数 (1–10)</label>
              <input type="number" min="1" max="10" class="inspector-param-input" data-k="pickN" value="${esc(flow.knobs.pickN)}" />
            </div>
          </div>
          <div class="gx-param-switch-row">
            <span class="inspector-param-label">只看 AI 相关</span>
            <button type="button" class="ledger-switch ${draftAiOnly ? 'is-on' : ''} gx-ai-switch" aria-label="只看 AI 相关"></button>
          </div>
        </div>
      `;
      const sw = knobSec.querySelector('.gx-ai-switch');
      sw.addEventListener('click', () => {
        draftAiOnly = !draftAiOnly;
        sw.classList.toggle('is-on', draftAiOnly);
      });
    }
    frag.appendChild(knobSec);

    // 底部保存区
    const saveBar = document.createElement('div');
    saveBar.className = 'pf-save';
    saveBar.style.display = 'flex';
    saveBar.style.justifyContent = 'flex-end';
    saveBar.style.gap = '8px';
    saveBar.style.padding = '10px 0';
    saveBar.innerHTML = `
      <button type="button" class="action-button action-button-secondary min-h-[32px] px-3 text-xs gx-cancel-btn">取消</button>
      <button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs gx-save-btn">保存</button>
    `;
    saveBar.querySelector('.gx-cancel-btn').addEventListener('click', () => {
      if (onCancel) onCancel();
    });
    saveBar.querySelector('.gx-save-btn').addEventListener('click', () => {
      const cronInput = cronSec.querySelector('.gx-input-cron');
      if (cronInput && cronInput.value.trim()) {
        flow.cron = cronInput.value.trim();
        flow.clock = cronToClock(flow.cron, flow.clock);
      }
      if (flow.id === 'brief') {
        flow.sourceMode = draftSourceMode;
      } else {
        flow.knobs.aiOnly = draftAiOnly;
      }
      knobSec.querySelectorAll('.inspector-param-input').forEach((inp) => {
        const key = inp.dataset.k;
        if (key) flow.knobs[key] = inp.value;
      });
      if (onSave) onSave();
    });
    frag.appendChild(saveBar);

    return { frag, cronInput: cronSec.querySelector('.gx-input-cron') };
  }

  function deletePeriodById(flowId, uid) {
    const flow = store.flows[flowId];
    if (!flow) return;
    flow.periods = flow.periods.filter((p) => p.uid !== uid);
  }

  function triggerManualRun(flow, rerender) {
    if (flow.lastStatus === 'run') return;
    flow.lastStatus = 'run';
    flow.stampClass = 'stamp-run';
    flow.stampText = '生成中';
    flow.activeStageIdx = 1;
    flow.reason = flow.id === 'brief' ? '正在评分与精选新归档文章' : '从 25 个仓库里挑 6 个';
    rerender();
  }

  /* ========================================================================
     方向 1: 刊席 (双栏并立当期印样，原地翻期与翻面调参)
     ======================================================================== */
  function mountDirection1() {
    paper.className = 'runs-paper gx-g1';
    const ui = {
      brief: {
        mode: initView === 'cfg' ? 'cfg' : 'read',
        histOpen: initView === 'hist',
        idx: 0,
        page: 1,
        focusCron: false
      },
      trending: {
        mode: 'read',
        histOpen: false,
        idx: 0,
        page: 1,
        focusCron: false
      }
    };

    function render() {
      if (parts && parts.scope && parts.scope.parentNode) {
        parts.scope.parentNode.removeChild(parts.scope);
      }
      paper.innerHTML = '';
      ['brief', 'trending'].forEach((fid) => {
        paper.appendChild(renderDesk(store.flows[fid], ui[fid]));
      });
    }

    function renderDesk(flow, dState) {
      if (dState.idx >= flow.periods.length) {
        dState.idx = Math.max(0, flow.periods.length - 1);
      }
      const curPeriod = flow.periods[dState.idx] || null;
      const desk = document.createElement('section');
      desk.className = 'gx-desk' + (flow.enabled ? '' : ' is-off');

      // 1. 席头
      const head = document.createElement('header');
      head.className = 'gx-desk-head';
      const isRun = flow.lastStatus === 'run';
      head.innerHTML = `
        <div class="gx-desk-title-grp">
          <span class="gx-desk-title">${esc(flow.name)}</span>
          <span class="gx-desk-id">${esc(flow.feedId)}</span>
        </div>
        <div class="gx-desk-tools">
          <button type="button" class="icon-button pf-icon gx-run-btn" title="立即生成">${isRun ? parts.spinIcon : parts.playIcon}</button>
          <button type="button" class="icon-button pf-icon gx-cfg-btn ${dState.mode === 'cfg' ? 'gx-btn-active' : ''}" title="参数与取材">${ICONS.sliders}</button>
          <button type="button" class="ledger-switch ${flow.enabled ? 'is-on' : ''} gx-enable-sw" aria-label="流程开关"></button>
        </div>
      `;
      head.querySelector('.gx-run-btn').addEventListener('click', () => triggerManualRun(flow, render));
      head.querySelector('.gx-cfg-btn').addEventListener('click', () => {
        dState.mode = dState.mode === 'cfg' ? 'read' : 'cfg';
        dState.histOpen = false;
        render();
      });
      head.querySelector('.gx-enable-sw').addEventListener('click', () => {
        flow.enabled = !flow.enabled;
        render();
      });
      desk.appendChild(head);

      // 2. 脉搏栏
      const pulse = document.createElement('div');
      pulse.className = 'gx-desk-pulse';
      const dotsHTML = flow.week.map((w) => `<i class="${esc(w.status)}" data-date="${esc(w.date)}" title="${esc(w.date)}"></i>`).join('');
      pulse.innerHTML = `
        <div class="gx-pulse-l">
          <span class="stamp ${esc(flow.stampClass)}">${esc(flow.stampText)}</span>
          <button type="button" class="gx-cron-chip" title="修改定时">${esc(flow.cron)}</button>
          <span class="gx-next-meta">距下次 <b>${esc(flow.nextIn)}</b></span>
        </div>
        <div class="gx-pulse-r">
          <span class="tt-dots gx-dots-btn">${dotsHTML}</span>
        </div>
      `;
      pulse.querySelector('.gx-cron-chip').addEventListener('click', () => {
        dState.mode = 'cfg';
        dState.histOpen = false;
        dState.focusCron = true;
        render();
      });
      pulse.querySelectorAll('.tt-dots i').forEach((dot) => {
        dot.addEventListener('click', () => {
          const targetDate = dot.dataset.date;
          const foundIdx = flow.periods.findIndex((p) => p.date === targetDate);
          if (foundIdx >= 0) {
            dState.mode = 'read';
            dState.histOpen = false;
            dState.idx = foundIdx;
            render();
          }
        });
      });
      desk.appendChild(pulse);

      // 3. 眉栏 + 主视口
      if (dState.mode === 'cfg') {
        const subbar = document.createElement('div');
        subbar.className = 'gx-desk-subbar is-cfg';
        subbar.innerHTML = `
          <span class="gx-subbar-title">参数与取材</span>
          <button type="button" class="inspector-metabtn gx-back-read">返回刊面</button>
        `;
        subbar.querySelector('.gx-back-read').addEventListener('click', () => {
          dState.mode = 'read';
          render();
        });
        desk.appendChild(subbar);

        const body = document.createElement('div');
        body.className = 'gx-desk-body';
        const cfgScroll = document.createElement('div');
        cfgScroll.className = 'gx-cfg-scroll';
        const { frag, cronInput } = buildFlowConfigBlocks(
          flow,
          () => { dState.mode = 'read'; render(); },
          () => { dState.mode = 'read'; render(); }
        );
        cfgScroll.appendChild(frag);
        body.appendChild(cfgScroll);
        desk.appendChild(body);
        if (dState.focusCron && cronInput) {
          dState.focusCron = false;
          requestAnimationFrame(() => cronInput.focus());
        }
      } else {
        const subbar = document.createElement('div');
        subbar.className = 'gx-desk-subbar';
        const isFallbackPrev = curPeriod && curPeriod.date !== '2026-11-26' && dState.idx === 0;
        subbar.innerHTML = `
          <div class="gx-issue-stepper">
            <button type="button" class="icon-button pf-icon gx-icon-sm gx-prev-issue" title="上一期" ${dState.idx >= flow.periods.length - 1 ? 'disabled' : ''}>${ICONS.prev}</button>
            <span class="gx-issue-date">${curPeriod ? esc(curPeriod.date) : '暂无刊期'}</span>
            ${isFallbackPrev ? '<span class="stamp is-hollow">上期</span>' : ''}
            <button type="button" class="icon-button pf-icon gx-icon-sm gx-next-issue" title="下一期" ${dState.idx <= 0 ? 'disabled' : ''}>${ICONS.next}</button>
            <span class="gx-issue-summary">${curPeriod ? esc(`${curPeriod.time} · ${curPeriod.metaText}`) : ''}</span>
          </div>
          <div class="gx-issue-ops">
            ${curPeriod ? `<button type="button" class="icon-button pf-icon gx-icon-sm gx-del-cur" title="删除当期">${ICONS.trash}</button>` : ''}
            <button type="button" class="inspector-metabtn gx-toggle-hist ${dState.histOpen ? 'gx-btn-active' : ''}">${flow.periods.length} 期 ▾</button>
          </div>
        `;
        subbar.querySelector('.gx-prev-issue').addEventListener('click', () => {
          if (dState.idx < flow.periods.length - 1) {
            dState.idx++;
            dState.histOpen = false;
            render();
          }
        });
        subbar.querySelector('.gx-next-issue').addEventListener('click', () => {
          if (dState.idx > 0) {
            dState.idx--;
            dState.histOpen = false;
            render();
          }
        });
        const delCur = subbar.querySelector('.gx-del-cur');
        if (delCur && curPeriod) {
          delCur.addEventListener('click', () => {
            deletePeriodById(flow.id, curPeriod.uid);
            render();
          });
        }
        subbar.querySelector('.gx-toggle-hist').addEventListener('click', () => {
          dState.histOpen = !dState.histOpen;
          render();
        });
        desk.appendChild(subbar);

        const body = document.createElement('div');
        body.className = 'gx-desk-body';
        const scroll = document.createElement('div');
        scroll.className = 'gx-desk-scroll';

        // 现场进度或异常诊断卡 (置于滚动区顶部以保持跨席三条横线绝对对齐)
        if (flow.lastStatus !== 'ok') {
          const diag = document.createElement('div');
          const tone = flow.lastStatus === 'run' ? 'is-run' : (flow.lastStatus === 'bad' ? 'is-bad' : 'is-warn');
          const noteTone = flow.lastStatus === 'bad' ? 'is-err' : (flow.lastStatus === 'warn' ? 'is-warn' : '');
          diag.className = `gx-diag-card ${tone}`;
          diag.innerHTML = `
            ${renderPipelineHTML(flow)}
            ${flow.reason ? `<div class="pipeline-note ${noteTone}">${esc(flow.reason)}</div>` : ''}
          `;
          scroll.appendChild(diag);
        }

        if (curPeriod) {
          scroll.appendChild(createPeriodContentEl(curPeriod));
        }
        body.appendChild(scroll);

        // 席内往期目录叠层
        if (dState.histOpen) {
          const layer = document.createElement('div');
          layer.className = 'gx-desk-hist-layer';
          const listWrap = document.createElement('div');
          listWrap.className = 'gx-desk-hist-list';
          const pageSize = 10;
          const totalPages = Math.max(1, Math.ceil(flow.periods.length / pageSize));
          if (dState.page > totalPages) dState.page = totalPages;
          const slice = flow.periods.slice((dState.page - 1) * pageSize, dState.page * pageSize);

          slice.forEach((p) => {
            const realIdx = flow.periods.indexOf(p);
            listWrap.appendChild(createBriefRunNode(p, {
              isViewing: realIdx === dState.idx,
              onSelect: () => {
                dState.idx = realIdx;
                dState.histOpen = false;
                render();
              },
              onDelete: (target) => {
                deletePeriodById(flow.id, target.uid);
                render();
              }
            }));
          });
          layer.appendChild(listWrap);
          layer.appendChild(createPagerNode(dState.page, flow.periods.length, pageSize, (nextPage) => {
            dState.page = nextPage;
            render();
          }));
          body.appendChild(layer);
        }

        desk.appendChild(body);
      }

      return desk;
    }

    render();
    mountPxBar([
      { label: '刊面', val: 'read' },
      { label: '期目', val: 'hist' },
      { label: '配置', val: 'cfg' }
    ], initView || 'read');
  }

  /* ========================================================================
     方向 2: 时序 (24h 刻度标尺 + 此刻 16:11 上下分界时刻表)
     ======================================================================== */
  function mountDirection2() {
    paper.className = 'runs-paper gx-g2';
    const allDates = Array.from(new Set([
      ...WEEK_DATES,
      ...store.flows.brief.periods.map((p) => p.date),
      ...store.flows.trending.periods.map((p) => p.date)
    ])).sort();

    const ui = {
      selectedDate: initView === 'past' ? '2026-11-24' : '2026-11-26',
      expandedToday: new Set(initView === 'open' ? ['brief'] : []),
      openCfgFlow: initView === 'open' ? 'brief' : '',
      archOpen: initView === 'arch',
      archFilter: 'all',
      archPage: 1,
      archExpandedUids: new Set()
    };

    function getAllUnifiedPeriods() {
      const merged = [
        ...store.flows.brief.periods,
        ...store.flows.trending.periods
      ];
      return merged.sort((a, b) => (`${b.date} ${b.time}`).localeCompare(`${a.date} ${a.time}`));
    }

    function render() {
      if (parts && parts.scope && parts.scope.parentNode) {
        parts.scope.parentNode.removeChild(parts.scope);
      }
      paper.innerHTML = '';

      const isToday = ui.selectedDate === '2026-11-26';
      const unified = getAllUnifiedPeriods();
      const dateIdx = allDates.indexOf(ui.selectedDate);

      // 1. 顶坞: 日期穿梭 + 7日双轨胶囊 + 24h 全景刻度标尺
      const head = document.createElement('header');
      head.className = 'gx-chrono-head';

      const weekPillsHTML = WEEK_DATES.map((d) => {
        const bDot = store.flows.brief.week.find((w) => w.date === d)?.status || '';
        const tDot = store.flows.trending.week.find((w) => w.date === d)?.status || '';
        const dayNum = d.slice(8);
        return `
          <button type="button" class="gx-week-pill ${ui.selectedDate === d ? 'is-on' : ''}" data-date="${esc(d)}" title="${esc(d)}">
            <span>11-${esc(dayNum)}</span>
            <span class="gx-pill-pair">
              <i class="gx-pill-dot ${esc(bDot)}"></i>
              <i class="gx-pill-dot ${esc(tDot)}"></i>
            </span>
          </button>
        `;
      }).join('');

      const bar = document.createElement('div');
      bar.className = 'gx-chrono-bar';
      bar.innerHTML = `
        <div class="gx-day-nav">
          <button type="button" class="icon-button pf-icon gx-icon-sm gx-day-prev" ${dateIdx <= 0 ? 'disabled' : ''} title="前一天">${ICONS.prev}</button>
          <span class="gx-day-label">${esc(ui.selectedDate)}</span>
          <span class="stamp ${isToday ? 'stamp-ok' : 'is-hollow'}">${isToday ? '今天' : '历史'}</span>
          <button type="button" class="icon-button pf-icon gx-icon-sm gx-day-next" ${dateIdx >= allDates.length - 1 ? 'disabled' : ''} title="后一天">${ICONS.next}</button>
          ${!isToday ? '<button type="button" class="inspector-metabtn gx-go-today">回今天</button>' : ''}
        </div>
        <div class="gx-week-pills">${weekPillsHTML}</div>
        <div>
          <button type="button" class="inspector-metabtn gx-arch-btn ${ui.archOpen ? 'gx-btn-active' : ''}">全部归档 · ${unified.length} 期 ▾</button>
        </div>
      `;

      bar.querySelector('.gx-day-prev').addEventListener('click', () => {
        if (dateIdx > 0) {
          ui.selectedDate = allDates[dateIdx - 1];
          ui.archOpen = false;
          render();
        }
      });
      bar.querySelector('.gx-day-next').addEventListener('click', () => {
        if (dateIdx < allDates.length - 1) {
          ui.selectedDate = allDates[dateIdx + 1];
          ui.archOpen = false;
          render();
        }
      });
      const goToday = bar.querySelector('.gx-go-today');
      if (goToday) {
        goToday.addEventListener('click', () => {
          ui.selectedDate = '2026-11-26';
          ui.archOpen = false;
          render();
        });
      }
      bar.querySelectorAll('.gx-week-pill').forEach((pill) => {
        pill.addEventListener('click', () => {
          ui.selectedDate = pill.dataset.date;
          ui.archOpen = false;
          render();
        });
      });
      bar.querySelector('.gx-arch-btn').addEventListener('click', () => {
        ui.archOpen = !ui.archOpen;
        render();
      });
      head.appendChild(bar);

      // 24h 标尺带
      const ruler = document.createElement('div');
      ruler.className = 'gx-ruler-band';
      const nowPct = ((16 + 11 / 60) / 24 * 100).toFixed(2);
      const bPct = (store.flows.brief.hourFrac * 100).toFixed(2);
      const tPct = (store.flows.trending.hourFrac * 100).toFixed(2);
      let ticksHTML = '';
      for (let h = 0; h <= 24; h += 2) {
        const major = h % 4 === 0;
        const pct = (h / 24 * 100).toFixed(2);
        ticksHTML += `<div class="gx-ruler-tick ${major ? 'is-major' : ''}" style="left:${pct}%">${major ? `<span>${String(h).padStart(2, '0')}</span>` : ''}</div>`;
      }
      const bDayStatus = getFlowDayStatus(store.flows.brief, ui.selectedDate);
      const tDayStatus = getFlowDayStatus(store.flows.trending, ui.selectedDate);

      ruler.innerHTML = `
        <div class="gx-ruler-track">
          ${isToday ? `<div class="gx-ruler-past" style="width:${nowPct}%"></div>` : ''}
          <div class="gx-ruler-line"></div>
          ${ticksHTML}
          <div class="gx-ruler-pin is-left" style="left:${bPct}%">
            <span>${esc(store.flows.brief.clock)} 日报</span>
            <span class="tt-last ${esc(bDayStatus)}"></span>
          </div>
          <div class="gx-ruler-pin is-right" style="left:${tPct}%">
            <span class="tt-last ${esc(tDayStatus)}"></span>
            <span>${esc(store.flows.trending.clock)} 精读</span>
          </div>
          ${isToday ? `<div class="gx-ruler-now" style="left:${nowPct}%"><span class="gx-ruler-now-tag">16:11</span></div>` : ''}
        </div>
      `;
      head.appendChild(ruler);
      paper.appendChild(head);

      // 2. 主体区
      const body = document.createElement('div');
      body.className = 'gx-chrono-body';
      const scroll = document.createElement('div');
      scroll.className = 'gx-chrono-scroll';

      // 当日已运行区块
      const pastStratum = document.createElement('section');
      pastStratum.className = 'gx-stratum';
      pastStratum.innerHTML = `
        <div class="gx-stratum-head">
          <span class="gx-stratum-title">${isToday ? '今日已执勤' : `${esc(ui.selectedDate)} 执勤记录`}</span>
          <span class="gx-stratum-meta">${isToday ? '00:00 — 16:11' : '00:00 — 24:00'}</span>
        </div>
      `;
      ['brief', 'trending'].forEach((fid) => {
        pastStratum.appendChild(renderDayExecutedRow(store.flows[fid], ui.selectedDate, isToday));
      });
      scroll.appendChild(pastStratum);

      if (isToday) {
        // 此刻 16:11 分界线 或 实时运行横舱
        const runningFlows = ['brief', 'trending'].map((k) => store.flows[k]).filter((f) => f.lastStatus === 'run');
        if (runningFlows.length > 0) {
          runningFlows.forEach((rf) => {
            const livePod = document.createElement('section');
            livePod.className = 'gx-now-live-pod';
            livePod.innerHTML = `
              <div class="gx-slot-main">
                <div class="gx-slot-time">
                  <span class="gx-slot-clock">16:11</span>
                  <span class="gx-slot-subclock">此刻手动</span>
                </div>
                <div class="gx-slot-flow">
                  <div class="gx-slot-name">${esc(rf.name)}</div>
                  <div class="gx-slot-feed">${esc(rf.feedId)}</div>
                </div>
                <div class="gx-slot-mid">
                  <span class="stamp stamp-run">生成中</span>
                  ${renderPipelineHTML(rf)}
                  <span class="gx-slot-reason is-run">${esc(rf.reason)}</span>
                </div>
                <div class="gx-slot-ops">
                  <button type="button" class="icon-button pf-icon" disabled>${parts.spinIcon}</button>
                </div>
              </div>
            `;
            scroll.appendChild(livePod);
          });
        } else {
          const seam = document.createElement('div');
          seam.className = 'gx-now-seam';
          seam.innerHTML = `
            <span class="gx-now-seam-badge">此刻 16:11</span>
            <span class="gx-now-seam-rule"></span>
            <span class="gx-now-seam-countdown">距下一班 ${esc(store.flows.brief.nextIn)}</span>
          `;
          scroll.appendChild(seam);
        }

        // 待发排期与总控区块
        const nextStratum = document.createElement('section');
        nextStratum.className = 'gx-stratum';
        nextStratum.innerHTML = `
          <div class="gx-stratum-head">
            <span class="gx-stratum-title">待发排期与控制</span>
            <span class="gx-stratum-meta">明日 2026-11-27</span>
          </div>
        `;
        ['brief', 'trending'].forEach((fid) => {
          nextStratum.appendChild(renderUpcomingRow(store.flows[fid]));
        });
        scroll.appendChild(nextStratum);
      }

      body.appendChild(scroll);

      // 全站归档覆盖层
      if (ui.archOpen) {
        body.appendChild(renderArchiveSheet(unified));
      }

      paper.appendChild(body);
    }

    function getFlowDayStatus(flow, dateStr) {
      if (dateStr === '2026-11-26') return flow.lastStatus;
      if (flow.id === 'trending' && dateStr === '2026-11-24') return 'warn';
      const hasPeriod = flow.periods.some((p) => p.date === dateStr);
      return hasPeriod ? 'ok' : '';
    }

    function renderDayExecutedRow(flow, dateStr, isToday) {
      const row = document.createElement('div');
      row.className = 'gx-slot-row';
      const period = flow.periods.find((p) => p.date === dateStr) || null;
      const isSkipped24 = flow.id === 'trending' && dateStr === '2026-11-24';
      const isBadToday = isToday && (flow.lastStatus === 'bad' || flow.lastStatus === 'warn');

      let midHTML = '';
      let opsHTML = '';
      let subClock = period ? `${period.time} 成稿` : '';

      if (isBadToday) {
        subClock = flow.id === 'brief' ? '08:32 中断' : '09:30 跳过';
        const rCls = flow.lastStatus === 'bad' ? 'is-err' : 'is-warn';
        midHTML = `
          <span class="stamp ${esc(flow.stampClass)}">${esc(flow.stampText)}</span>
          ${renderPipelineHTML(flow)}
          <span class="gx-slot-reason ${rCls}">${esc(flow.reason)}</span>
        `;
        opsHTML = `<button type="button" class="icon-button pf-icon gx-retry-btn" title="立即生成">${parts.playIcon}</button>`;
      } else if (isSkipped24) {
        subClock = '09:30 跳过';
        midHTML = `
          <span class="stamp stamp-warn">已跳过</span>
          <div class="ledger-pipeline">
            <span class="ledger-pipeline-step is-now">读榜</span>
            <span class="ledger-pipeline-step">挑选</span>
            <span class="ledger-pipeline-step">成稿</span>
          </div>
          <span class="gx-slot-reason is-warn">当日榜单未入库</span>
        `;
      } else if (period) {
        const stepsDone = flow.stages.map((s) => `<span class="ledger-pipeline-step is-done">${esc(s)}</span>`).join('');
        midHTML = `
          <span class="stamp stamp-ok">已生成</span>
          <div class="ledger-pipeline">${stepsDone}</div>
        `;
        const isExp = ui.expandedToday.has(period.uid) || (isToday && ui.expandedToday.has(flow.id));
        opsHTML = `
          <button type="button" class="inspector-metabtn gx-exp-period ${isExp ? 'gx-btn-active' : ''}">${esc(period.metaText)} ${isExp ? '▴' : '▾'}</button>
          <button type="button" class="icon-button pf-icon gx-icon-sm gx-del-period" title="删除本期">${ICONS.trash}</button>
        `;
      } else {
        subClock = '无记录';
        midHTML = '<span class="stamp is-hollow">未排期</span>';
      }

      const main = document.createElement('div');
      main.className = 'gx-slot-main';
      main.innerHTML = `
        <div class="gx-slot-time">
          <span class="gx-slot-clock">${esc(flow.clock)}</span>
          <span class="gx-slot-subclock">${esc(subClock)}</span>
        </div>
        <div class="gx-slot-flow">
          <div class="gx-slot-name">${esc(flow.name)}</div>
          <div class="gx-slot-feed">${esc(flow.feedId)}</div>
        </div>
        <div class="gx-slot-mid">${midHTML}</div>
        <div class="gx-slot-ops">${opsHTML}</div>
      `;

      const retryBtn = main.querySelector('.gx-retry-btn');
      if (retryBtn) {
        retryBtn.addEventListener('click', () => triggerManualRun(flow, render));
      }

      if (period) {
        const key = isToday ? flow.id : period.uid;
        const isExp = ui.expandedToday.has(key);
        const expBtn = main.querySelector('.gx-exp-period');
        const delBtn = main.querySelector('.gx-del-period');
        if (expBtn) {
          expBtn.addEventListener('click', () => {
            if (ui.expandedToday.has(key)) ui.expandedToday.delete(key);
            else ui.expandedToday.add(key);
            render();
          });
        }
        if (delBtn) {
          delBtn.addEventListener('click', () => {
            deletePeriodById(flow.id, period.uid);
            ui.expandedToday.delete(key);
            render();
          });
        }
        row.appendChild(main);
        if (isExp) {
          const drawer = document.createElement('div');
          drawer.className = 'gx-slot-drawer';
          drawer.appendChild(createPeriodContentEl(period));
          row.appendChild(drawer);
        }
      } else {
        row.appendChild(main);
      }

      return row;
    }

    function renderUpcomingRow(flow) {
      const row = document.createElement('div');
      row.className = 'gx-slot-row' + (flow.enabled ? '' : ' is-off');
      const cfgOpen = ui.openCfgFlow === flow.id;
      const isRun = flow.lastStatus === 'run';

      const specs = flow.id === 'brief'
        ? [
            flow.sourceMode === 'all' ? '全部来源' : '自定名单',
            `每期 ${flow.knobs.topN} 条`,
            `门槛 ${flow.knobs.minScore}`
          ]
        : [
            'Trending 日榜',
            `挑 ${flow.knobs.pickN} 个`,
            flow.knobs.aiOnly ? '只看 AI' : '不限领域'
          ];

      const main = document.createElement('div');
      main.className = 'gx-slot-main';
      main.innerHTML = `
        <div class="gx-slot-time">
          <span class="gx-slot-clock">${flow.enabled ? esc(flow.clock) : '已停用'}</span>
          <span class="gx-slot-subclock">${flow.enabled ? esc(flow.cron) : '暂停定时'}</span>
        </div>
        <div class="gx-slot-flow">
          <div class="gx-slot-name">${esc(flow.name)}</div>
          <div class="gx-slot-feed">累计 ${flow.periods.length} 期</div>
        </div>
        <div class="gx-slot-mid">
          <span class="gx-next-meta">距下次 <b>${flow.enabled ? esc(flow.nextIn) : '—'}</b></span>
          <div class="gx-spec-pills">
            ${specs.map((s) => `<span class="gx-spec-pill">${esc(s)}</span>`).join('')}
          </div>
        </div>
        <div class="gx-slot-ops">
          <button type="button" class="icon-button pf-icon gx-up-run" title="立即生成">${isRun ? parts.spinIcon : parts.playIcon}</button>
          <button type="button" class="icon-button pf-icon gx-up-cfg ${cfgOpen ? 'gx-btn-active' : ''}" title="参数与取材">${ICONS.sliders}</button>
          <button type="button" class="ledger-switch ${flow.enabled ? 'is-on' : ''} gx-up-sw" aria-label="流程开关"></button>
        </div>
      `;

      main.querySelector('.gx-up-run').addEventListener('click', () => triggerManualRun(flow, render));
      main.querySelector('.gx-up-cfg').addEventListener('click', () => {
        ui.openCfgFlow = cfgOpen ? '' : flow.id;
        render();
      });
      main.querySelector('.gx-up-sw').addEventListener('click', () => {
        flow.enabled = !flow.enabled;
        render();
      });
      row.appendChild(main);

      if (cfgOpen) {
        const inlineCfg = document.createElement('div');
        inlineCfg.className = 'gx-inline-cfg';
        const { frag } = buildFlowConfigBlocks(
          flow,
          () => { ui.openCfgFlow = ''; render(); },
          () => { ui.openCfgFlow = ''; render(); }
        );
        const sections = Array.from(frag.querySelectorAll('.gx-cfg-section'));
        const saveBar = frag.querySelector('.pf-save');
        const grid = document.createElement('div');
        grid.className = 'gx-inline-cfg-grid';
        sections.forEach((s) => grid.appendChild(s));
        inlineCfg.appendChild(grid);
        if (saveBar) inlineCfg.appendChild(saveBar);
        row.appendChild(inlineCfg);
      }

      return row;
    }

    function renderArchiveSheet(unified) {
      const sheet = document.createElement('div');
      sheet.className = 'gx-arch-sheet';
      const filtered = ui.archFilter === 'all'
        ? unified
        : unified.filter((p) => p.flowId === ui.archFilter);

      const head = document.createElement('div');
      head.className = 'gx-arch-sheet-head';
      head.innerHTML = `
        <div class="gx-vault-head-l">
          <span class="gx-subbar-title">全部往期归档</span>
          <div class="mini-seg">
            <button type="button" class="mini-seg-btn ${ui.archFilter === 'all' ? 'is-on' : ''}" data-f="all">全部 ${unified.length}</button>
            <button type="button" class="mini-seg-btn ${ui.archFilter === 'brief' ? 'is-on' : ''}" data-f="brief">日报 ${store.flows.brief.periods.length}</button>
            <button type="button" class="mini-seg-btn ${ui.archFilter === 'trending' ? 'is-on' : ''}" data-f="trending">精读 ${store.flows.trending.periods.length}</button>
          </div>
        </div>
        <button type="button" class="inspector-metabtn gx-close-arch">收起归档</button>
      `;
      head.querySelectorAll('.mini-seg-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          ui.archFilter = btn.dataset.f;
          ui.archPage = 1;
          render();
        });
      });
      head.querySelector('.gx-close-arch').addEventListener('click', () => {
        ui.archOpen = false;
        render();
      });
      sheet.appendChild(head);

      const listEl = document.createElement('div');
      listEl.className = 'gx-arch-sheet-list';
      const pageSize = 10;
      const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
      if (ui.archPage > totalPages) ui.archPage = totalPages;
      const slice = filtered.slice((ui.archPage - 1) * pageSize, ui.archPage * pageSize);

      slice.forEach((p) => {
        const exp = ui.archExpandedUids.has(p.uid);
        listEl.appendChild(createBriefRunNode(p, {
          showFlowBadge: true,
          expanded: exp,
          onToggleExpand: () => {
            if (exp) ui.archExpandedUids.delete(p.uid);
            else ui.archExpandedUids.add(p.uid);
            render();
          },
          onDelete: (target) => {
            deletePeriodById(target.flowId, target.uid);
            ui.archExpandedUids.delete(target.uid);
            render();
          }
        }));
      });
      sheet.appendChild(listEl);
      sheet.appendChild(createPagerNode(ui.archPage, filtered.length, pageSize, (p) => {
        ui.archPage = p;
        render();
      }));
      return sheet;
    }

    render();
    mountPxBar([
      { label: '今日', val: 'today' },
      { label: '展开', val: 'open' },
      { label: '11-24', val: 'past' },
      { label: '归档', val: 'arch' }
    ], initView || 'today');
  }

  /* ========================================================================
     方向 3: 机匣 (凹槽桌面模块机匣 + 常态隐藏的统一口径合订匣)
     ======================================================================== */
  function mountDirection3() {
    paper.className = 'runs-paper gx-g3';

    function getMergedPeriods() {
      return [
        ...store.flows.brief.periods,
        ...store.flows.trending.periods
      ].sort((a, b) => (`${b.date} ${b.time}`).localeCompare(`${a.date} ${a.time}`));
    }

    const initialMerged = getMergedPeriods();
    const ui = {
      openCfg: initView === 'cfg' ? 'brief' : '',
      focusCron: false,
      vaultOpen: initView === 'vault',
      vaultFilter: 'all',
      vaultPage: 1,
      expandedUids: new Set(initView === 'vault' && initialMerged[0] ? [initialMerged[0].uid] : [])
    };

    function openPeriodInVault(period) {
      if (!period) return;
      ui.vaultOpen = true;
      ui.vaultFilter = 'all';
      const all = getMergedPeriods();
      const idx = all.findIndex((p) => p.uid === period.uid);
      ui.vaultPage = idx >= 0 ? Math.floor(idx / 10) + 1 : 1;
      ui.expandedUids.add(period.uid);
      render();
    }

    function render() {
      if (parts && parts.scope && parts.scope.parentNode) {
        parts.scope.parentNode.removeChild(parts.scope);
      }
      paper.innerHTML = '';

      // 1. 双列流程机匣网格
      const grid = document.createElement('div');
      grid.className = 'gx-pod-grid';
      ['brief', 'trending'].forEach((fid) => {
        grid.appendChild(renderPod(store.flows[fid]));
      });
      paper.appendChild(grid);

      // 2. 统一口径合订底匣
      paper.appendChild(renderVault());
    }

    function renderPod(flow) {
      const pod = document.createElement('section');
      pod.className = 'gx-pod' + (flow.enabled ? '' : ' is-off');
      const isRun = flow.lastStatus === 'run';
      const isCfg = ui.openCfg === flow.id;
      const latest = flow.periods[0] || null;

      // 匣首
      const head = document.createElement('header');
      head.className = 'gx-pod-head';
      head.innerHTML = `
        <div class="gx-pod-id">
          <span class="tt-last ${esc(flow.lastStatus)}"></span>
          <span class="gx-pod-name">${esc(flow.name)}</span>
          <span class="gx-pod-feed">${esc(flow.feedId)}</span>
        </div>
        <div class="gx-pod-tools">
          <button type="button" class="icon-button pf-icon gx-pod-run" title="立即生成">${isRun ? parts.spinIcon : parts.playIcon}</button>
          <button type="button" class="icon-button pf-icon gx-pod-cfg-btn ${isCfg ? 'gx-btn-active' : ''}" title="参数与取材">${ICONS.sliders}</button>
          <button type="button" class="ledger-switch ${flow.enabled ? 'is-on' : ''} gx-pod-sw" aria-label="流程开关"></button>
        </div>
      `;
      head.querySelector('.gx-pod-run').addEventListener('click', () => triggerManualRun(flow, render));
      head.querySelector('.gx-pod-cfg-btn').addEventListener('click', () => {
        ui.openCfg = isCfg ? '' : flow.id;
        render();
      });
      head.querySelector('.gx-pod-sw').addEventListener('click', () => {
        flow.enabled = !flow.enabled;
        render();
      });
      pod.appendChild(head);

      // 匣芯 (状态 + 异常原因 + 最新一期跳转 + 阶段链)
      const coreTone = flow.lastStatus === 'run'
        ? ' is-run'
        : (flow.lastStatus === 'bad' ? ' is-bad' : (flow.lastStatus === 'warn' ? ' is-warn' : ''));
      const reasonCls = flow.lastStatus === 'bad'
        ? 'is-err'
        : (flow.lastStatus === 'warn' ? 'is-warn' : 'is-run');
      const core = document.createElement('div');
      core.className = 'gx-pod-core' + coreTone;
      const latestLabel = latest
        ? `${latest.date === '2026-11-26' ? '今日' : latest.date.slice(5)} ${latest.time} · ${latest.shortMeta} ▸`
        : '';

      core.innerHTML = `
        <div class="gx-pod-status-line">
          <div class="gx-pod-status-l">
            <span class="stamp ${esc(flow.stampClass)}">${esc(flow.stampText)}</span>
            ${flow.reason ? `<span class="gx-slot-reason ${reasonCls}">${esc(flow.reason)}</span>` : ''}
          </div>
          ${latest ? `<button type="button" class="gx-pod-latest-btn">${esc(latestLabel)}</button>` : ''}
        </div>
        ${renderPipelineHTML(flow)}
      `;
      const latestBtn = core.querySelector('.gx-pod-latest-btn');
      if (latestBtn && latest) {
        latestBtn.addEventListener('click', () => openPeriodInVault(latest));
      }
      pod.appendChild(core);

      // 匣底 (cron + 距下次 + 近7日点阵 + 累计期数)
      const foot = document.createElement('div');
      foot.className = 'gx-pod-foot';
      const dotsHTML = flow.week.map((w) => `<i class="${esc(w.status)}" data-date="${esc(w.date)}" title="${esc(w.date)}"></i>`).join('');
      foot.innerHTML = `
        <div class="gx-pod-foot-l">
          <button type="button" class="gx-cron-chip" title="修改定时">${esc(flow.cron)}</button>
          <span class="gx-next-meta">距下次 <b>${flow.enabled ? esc(flow.nextIn) : '—'}</b></span>
        </div>
        <div class="gx-pod-foot-r">
          <span class="tt-dots gx-dots-btn">${dotsHTML}</span>
          <span class="gx-pod-total">${flow.periods.length} 期</span>
        </div>
      `;
      foot.querySelector('.gx-cron-chip').addEventListener('click', () => {
        ui.openCfg = flow.id;
        ui.focusCron = true;
        render();
      });
      foot.querySelectorAll('.tt-dots i').forEach((dot) => {
        dot.addEventListener('click', () => {
          const p = flow.periods.find((item) => item.date === dot.dataset.date);
          if (p) openPeriodInVault(p);
        });
      });
      pod.appendChild(foot);

      // 按需展开的匣内配置抽屉
      if (isCfg) {
        const cfgWrap = document.createElement('div');
        cfgWrap.className = 'gx-pod-cfg';
        const { frag, cronInput } = buildFlowConfigBlocks(
          flow,
          () => { ui.openCfg = ''; render(); },
          () => { ui.openCfg = ''; render(); }
        );
        cfgWrap.appendChild(frag);
        pod.appendChild(cfgWrap);
        if (ui.focusCron && cronInput) {
          ui.focusCron = false;
          requestAnimationFrame(() => cronInput.focus());
        }
      }

      return pod;
    }

    function renderVault() {
      const merged = getMergedPeriods();
      const filtered = ui.vaultFilter === 'all'
        ? merged
        : merged.filter((p) => p.flowId === ui.vaultFilter);

      const vault = document.createElement('section');
      vault.className = 'gx-vault' + (ui.vaultOpen ? ' is-open' : '');

      const head = document.createElement('div');
      head.className = 'gx-vault-head';
      head.innerHTML = `
        <div class="gx-vault-head-l">
          <button type="button" class="gx-vault-title-btn">
            ${ICONS.caretRight}
            <span>近期产出</span>
          </button>
          <div class="mini-seg">
            <button type="button" class="mini-seg-btn ${ui.vaultFilter === 'all' ? 'is-on' : ''}" data-f="all">全部 ${merged.length}</button>
            <button type="button" class="mini-seg-btn ${ui.vaultFilter === 'brief' ? 'is-on' : ''}" data-f="brief">日报 ${store.flows.brief.periods.length}</button>
            <button type="button" class="mini-seg-btn ${ui.vaultFilter === 'trending' ? 'is-on' : ''}" data-f="trending">精读 ${store.flows.trending.periods.length}</button>
          </div>
        </div>
        <button type="button" class="inspector-metabtn gx-vault-toggle">${ui.vaultOpen ? '收起列表 ▴' : '展开全部 ▾'}</button>
      `;

      head.querySelector('.gx-vault-title-btn').addEventListener('click', () => {
        ui.vaultOpen = !ui.vaultOpen;
        render();
      });
      head.querySelector('.gx-vault-toggle').addEventListener('click', () => {
        ui.vaultOpen = !ui.vaultOpen;
        render();
      });
      head.querySelectorAll('.mini-seg-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          ui.vaultFilter = btn.dataset.f;
          ui.vaultPage = 1;
          ui.vaultOpen = true;
          render();
        });
      });
      vault.appendChild(head);

      if (!ui.vaultOpen) {
        // 折叠态速览条: 展示最近 3 期微缩丸
        const peek = document.createElement('div');
        peek.className = 'gx-vault-peek';
        filtered.slice(0, 3).forEach((p) => {
          const chip = document.createElement('button');
          chip.type = 'button';
          chip.className = 'gx-peek-chip';
          chip.innerHTML = `
            <code>${esc(p.date.slice(5))} ${esc(p.time)}</code>
            <span>${esc(p.flowShort)} · ${esc(p.shortMeta)}</span>
          `;
          chip.addEventListener('click', () => openPeriodInVault(p));
          peek.appendChild(chip);
        });
        vault.appendChild(peek);
      } else {
        const body = document.createElement('div');
        body.className = 'gx-vault-body';
        const listEl = document.createElement('div');
        listEl.className = 'gx-vault-list';

        const pageSize = 10;
        const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
        if (ui.vaultPage > totalPages) ui.vaultPage = totalPages;
        const slice = filtered.slice((ui.vaultPage - 1) * pageSize, ui.vaultPage * pageSize);

        slice.forEach((p) => {
          const isExp = ui.expandedUids.has(p.uid);
          listEl.appendChild(createBriefRunNode(p, {
            showFlowBadge: true,
            expanded: isExp,
            onToggleExpand: () => {
              if (isExp) ui.expandedUids.delete(p.uid);
              else ui.expandedUids.add(p.uid);
              render();
            },
            onDelete: (target) => {
              deletePeriodById(target.flowId, target.uid);
              ui.expandedUids.delete(target.uid);
              render();
            }
          }));
        });
        body.appendChild(listEl);
        body.appendChild(createPagerNode(ui.vaultPage, filtered.length, pageSize, (p) => {
          ui.vaultPage = p;
          render();
        }));
        vault.appendChild(body);
      }

      return vault;
    }

    render();
    mountPxBar([
      { label: '收拢', val: 'shut' },
      { label: '合订展开', val: 'vault' },
      { label: '匣内配置', val: 'cfg' }
    ], initView || 'shut');
  }

  /* ========================================================================
     底部样页开关条 (.px-bar)
     ======================================================================== */
  function buildQueryHref(patch) {
    const p = new URLSearchParams(window.location.search);
    Object.keys(patch).forEach((k) => {
      if (patch[k] == null || patch[k] === '') p.delete(k);
      else p.set(k, patch[k]);
    });
    const qs = p.toString();
    return qs ? `?${qs}` : window.location.pathname;
  }

  function mountPxBar(viewOptions, activeView) {
    const existing = document.querySelector('.px-bar');
    if (existing) existing.remove();

    const bar = document.createElement('div');
    bar.className = 'px-bar';

    const stateGrp = document.createElement('span');
    stateGrp.className = 'px-grp';
    stateGrp.innerHTML = '<span class="px-lbl">状态</span>';
    [
      { label: 'ok', val: 'ok' },
      { label: 'run', val: 'run' },
      { label: 'bad', val: 'bad' }
    ].forEach((item) => {
      const a = document.createElement('a');
      a.href = buildQueryHref({ state: item.val });
      a.textContent = item.label;
      if (state === item.val) a.className = 'is-on';
      stateGrp.appendChild(a);
    });
    bar.appendChild(stateGrp);

    const themeGrp = document.createElement('span');
    themeGrp.className = 'px-grp';
    themeGrp.innerHTML = '<span class="px-lbl">主题</span>';
    [
      { label: '亮', val: 'light' },
      { label: '暗', val: 'dark' }
    ].forEach((item) => {
      const a = document.createElement('a');
      a.href = buildQueryHref({ theme: item.val });
      a.textContent = item.label;
      if (theme === item.val) a.className = 'is-on';
      themeGrp.appendChild(a);
    });
    bar.appendChild(themeGrp);

    if (viewOptions && viewOptions.length) {
      const viewGrp = document.createElement('span');
      viewGrp.className = 'px-grp';
      viewGrp.innerHTML = '<span class="px-lbl">视图</span>';
      viewOptions.forEach((item) => {
        const a = document.createElement('a');
        a.href = buildQueryHref({ view: item.val });
        a.textContent = item.label;
        if (activeView === item.val) a.className = 'is-on';
        viewGrp.appendChild(a);
      });
      bar.appendChild(viewGrp);
    }

    document.body.appendChild(bar);
  }

  if (dir === '2') {
    mountDirection2();
  } else if (dir === '3') {
    mountDirection3();
  } else {
    mountDirection1();
  }
});
