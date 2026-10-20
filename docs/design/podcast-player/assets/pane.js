/* 播客区样页(issue #199)阅读窗渲染器:标题区 / AI 卡 / 节目简介 / 导读稿与现网同一套 class(样式取自编译产物),
   只有「播客区」交给各方案的 render() 决定。URL 参数:opt / state / device(desktop|mobile)/ theme(light|dark)。

   方案模块约定(assets/opt-*.js):
     PP.options[id] = {
       id, order, name, idea, points: [...],
       render(ctx) → {
         top,             // 标题区之下、正文之前的 HTML(播客区主体)
         headBelowMeta,   // 署名行之下、标签行之上(可选)
         headActions,     // 桌面标题区尾行动作区末尾追加(可选;移动端无此行)
         dock,            // 阅读窗底部常驻区(可选;position: sticky 于滚动容器底)
         meta,            // 覆盖署名行条目(数组,可选)
         guideHandled,    // true = 方案自己在 top 里画了导读稿,渲染器不再画
       },
       mount(root, ctx),  // 可选:绑定交互
     }
   ctx = { state, device, theme, ep, icons, fmt, bodyMode, html: { aiCard, guide }, setState(id) } */
(function () {
  const q = new URLSearchParams(location.search);
  const ctxBase = {
    optId: q.get('opt') || 'current',
    stateId: q.get('state') || 's1',
    device: q.get('device') === 'mobile' ? 'mobile' : 'desktop',
    theme: q.get('theme') === 'light' ? 'light' : 'dark',
  };
  if (ctxBase.theme === 'dark') document.documentElement.dataset.theme = 'dark';
  document.documentElement.dataset.device = ctxBase.device;

  const ep = PP.episode;
  const I = PP.icons;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const DIGITS = '0123456789'.split('');
  function scoreFigure(score, tier) {
    const cells = score.split('').map((ch) => {
      if (ch === '.') return `<span class="reader-ai-odo-dot ai-grad-text ${tier}">.</span>`;
      const d = Number(ch);
      return `<span class="reader-ai-odo-cell"><span class="reader-ai-odo-strip" style="transform:translateY(${-d * 10}%)">${DIGITS.map((x) => `<span class="reader-ai-odo-digit ai-grad-text ${tier}">${x}</span>`).join('')}</span></span>`;
    }).join('');
    return `<button type="button" class="reader-ai-score-btn" aria-label="内容价值 ${score}"><span class="reader-ai-odo" aria-hidden="true">${cells}</span></button>`;
  }

  const html = {
    aiCard() {
      return `<div class="reader-ai-summary">
        <div class="reader-ai-card-head">
          <span class="reader-ai-layer-title">简介导读</span>
          <div class="reader-ai-summary-side">${scoreFigure(ep.score, ep.scoreTier)}<span class="reader-ai-score-dim" aria-hidden="true">内容价值</span></div>
        </div>
        <div class="reader-ai-summary-main">
          <div class="reader-ai-layer reader-ai-layer-summary">
            <p class="reader-ai-layer-text">${esc(ep.summary)}</p>
            <span class="reader-ai-layer-note">${esc(ep.basisNote)}</span>
          </div>
        </div>
      </div>`;
    },
    showNotes() {
      return `<div class="podcast-show-notes-head"><h2 class="section-title">节目简介</h2><span>来源方提供</span></div>
        <div class="reader-article-copy markdown-body" lang="en">${ep.showNotes.map((p) => `<p>${esc(p)}</p>`).join('')}</div>
        <p class="reader-pane-origin"><a href="${ep.sourceUrl}" target="_blank" rel="noreferrer">查看原文 ↗</a><span class="reader-pane-origin-host"> · ${ep.host}</span></p>`;
    },
    // 现网 PodcastTextPanel 导读稿段(digest_blog_zh)
    guide(title = '精品导读') {
      const body = ep.guide.map(([tag, text]) => `<${tag}>${esc(text)}</${tag}>`).join('');
      return `<section class="podcast-text-panel" aria-label="播客文字内容"><div class="podcast-text-digest">
        <div class="podcast-text-heading"><h2>${esc(title)}</h2><span>中文博客 · AI 整理</span></div>
        <div class="podcast-guide-body"><div class="markdown-body">${body}</div></div>
      </div></section>`;
    },
  };

  const defaultMeta = () => [ep.date, `简介阅读约 ${ep.readMinutes} 分钟`, `原节目 ${PP.fmt.minutes(ep.durationSeconds)}`, `阅读量 ${ep.readCount}`];

  function headerHtml(parts) {
    const meta = (parts.meta || defaultMeta()).map((m) => `<span>${m}</span>`).join('');
    const tags = ep.tags.map((t) => `<button type="button" class="reader-tag-chip is-actionable" aria-label="检索「${esc(t)}」">${esc(t)}</button>`).join('');
    const actions = ctxBase.device === 'desktop'
      ? `<div class="reader-pane-actions">
          <div class="reader-tr-seg" role="group" aria-label="正文语言">
            <button type="button" class="reader-tr-seg-btn is-on" aria-pressed="true">原语言</button>
            <button type="button" class="reader-tr-seg-btn" aria-pressed="false">${I.sparkles}<span>译为中文</span></button>
          </div>
          <a class="reader-pane-act" href="${ep.sourceUrl}" target="_blank" rel="noreferrer">${I.external}查看原文</a>
          ${parts.headActions || ''}
        </div>`
      : '';
    return `<header class="reader-pane-head">
      <div class="reader-kicker">${esc(ep.source)}</div>
      <h1 class="reader-pane-title" lang="en">${esc(ep.title)}</h1>
      <div class="reader-pane-byline"><div class="reader-pane-meta">${meta}</div></div>
      ${parts.headBelowMeta || ''}
      <div class="reader-pane-foot"><div class="reader-pane-tags">${tags}</div>${actions}</div>
    </header>`;
  }

  function bodyHtml(parts, bodyMode) {
    const rest = bodyMode === 'guide'
      ? (parts.guideHandled ? '' : html.guide())
      : `${html.aiCard()}${html.showNotes()}`;
    return `${parts.top || ''}${rest}`;
  }

  function mobileTopbar() {
    return `<div class="m-topbar on-pane">
      <button type="button" class="m-iconbtn" aria-label="返回">${I.arrowLeft}</button>
      <span class="m-topbar-sp"></span>
      <button type="button" class="m-iconbtn" aria-label="收藏">${I.star}</button>
      <button type="button" class="m-iconbtn" aria-label="分享">${I.share}</button>
      <button type="button" class="m-iconbtn" aria-label="将节目简介和逐字稿译为中文"><span class="reader-tr-glyph" aria-hidden="true">译</span></button>
      <button type="button" class="m-iconbtn" aria-label="更多操作">${I.more}</button>
    </div>`;
  }

  function render(stateId) {
    const root = document.getElementById('pp-root');
    ctxBase.stateId = stateId;
    const state = PP.stateById(stateId);
    const opt = PP.options[ctxBase.optId] || PP.options.current;
    const bodyMode = PP.bodyMode(state);
    const ctx = {
      ...ctxBase, state, ep, icons: I, fmt: PP.fmt, bodyMode, html, esc,
      setState(id) {
        render(id);
        try { parent.postMessage({ type: 'pp-state', state: id }, '*'); } catch { /* 独立打开时无父页 */ }
      },
    };
    const parts = opt.render(ctx) || {};
    PP.stopPlayers();
    if (ctxBase.device === 'mobile') {
      root.innerHTML = `<div class="m-read pp-mobile" data-opt="${opt.id}">
        ${mobileTopbar()}
        <div class="m-read-scroll" id="pp-scroll">${headerHtml(parts)}<div class="m-read-body">${bodyHtml(parts, bodyMode)}</div>${parts.dock || ''}</div>
      </div>`;
    } else {
      root.innerHTML = `<article class="reader-pane pp-desktop" id="pp-scroll" data-opt="${opt.id}">
        ${headerHtml(parts)}
        <div class="reader-pane-body">${bodyHtml(parts, bodyMode)}</div>
        ${parts.dock || ''}
      </article>`;
    }
    PP.wirePlayers(root);
    opt.mount?.(root, ctx);
  }

  window.addEventListener('message', (e) => {
    if (e.data?.type === 'pp-set-state') render(e.data.state);
  });
  // 等方案脚本都注册完再渲染
  window.addEventListener('DOMContentLoaded', () => render(ctxBase.stateId));
})();

/* ── 模拟播放器:方案只需按约定挂 data-pp-* 钩子,交互(播放 / 拖动 / 键盘 / 跳转 / 倍速)由这里统一驱动 ──
   [data-pp-player][data-duration][data-start]  播放器根;播放中挂 data-playing
   [data-pp-toggle]        播放 / 暂停按钮(内含 [data-pp-icon] 时自动换 play/pause 图标)
   [data-pp-track]         进度条(role=slider;点击 / 拖动定位,← → 各 5 秒)
   [data-pp-fill]          已播放部分(宽度 %)   [data-pp-thumb] 拖柄(left %)
   [data-pp-cur] 当前时间  [data-pp-dur] 总时长  [data-pp-left] 剩余(-mm:ss)
   [data-pp-skip="-15"]    跳转秒数
   [data-pp-rate]          倍速循环 1× → 1.25× → 1.5× → 2× */
(function () {
  const RATES = [1, 1.25, 1.5, 2];
  let players = [];
  let raf = 0;
  let last = 0;

  function paint(p) {
    const pct = p.duration ? (p.t / p.duration) * 100 : 0;
    p.el.querySelectorAll('[data-pp-fill]').forEach((n) => { n.style.width = `${pct}%`; });
    p.el.querySelectorAll('[data-pp-thumb]').forEach((n) => { n.style.left = `${pct}%`; });
    p.el.querySelectorAll('[data-pp-cur]').forEach((n) => { n.textContent = PP.fmt.clock(p.t); });
    p.el.querySelectorAll('[data-pp-dur]').forEach((n) => { n.textContent = PP.fmt.clock(p.duration); });
    p.el.querySelectorAll('[data-pp-left]').forEach((n) => { n.textContent = `-${PP.fmt.clock(p.duration - p.t)}`; });
    p.el.querySelectorAll('[data-pp-track]').forEach((n) => {
      n.setAttribute('aria-valuenow', String(Math.round(p.t)));
      n.setAttribute('aria-valuetext', `${PP.fmt.clock(p.t)} / ${PP.fmt.clock(p.duration)}`);
    });
    p.el.toggleAttribute('data-playing', p.playing);
    p.el.querySelectorAll('[data-pp-toggle]').forEach((b) => {
      b.setAttribute('aria-label', p.playing ? '暂停' : '播放');
      const icon = b.querySelector('[data-pp-icon]');
      if (icon) icon.innerHTML = p.playing ? PP.icons.pause : PP.icons.play;
    });
    p.el.querySelectorAll('[data-pp-rate]').forEach((b) => {
      b.textContent = `${RATES[p.rate]}×`;
      b.setAttribute('aria-label', `播放速度 ${RATES[p.rate]} 倍`);
    });
  }

  function tick(now) {
    const dt = last ? (now - last) / 1000 : 0;
    last = now;
    let any = false;
    for (const p of players) {
      if (!p.playing) continue;
      any = true;
      p.t = Math.min(p.duration, p.t + dt * RATES[p.rate]);
      if (p.t >= p.duration) p.playing = false;
      paint(p);
    }
    raf = any ? requestAnimationFrame(tick) : 0;
    if (!any) last = 0;
  }

  function play(p, on) {
    if (on) players.forEach((o) => { if (o !== p && o.playing) { o.playing = false; paint(o); } });
    p.playing = on;
    paint(p);
    if (on && !raf) { last = 0; raf = requestAnimationFrame(tick); }
    p.el.dispatchEvent(new CustomEvent('pp-play', { bubbles: true, detail: { playing: on } }));
  }

  PP.stopPlayers = () => { players.forEach((p) => { p.playing = false; }); players = []; cancelAnimationFrame(raf); raf = 0; };

  PP.wirePlayers = (root) => {
    root.querySelectorAll('[data-pp-player]').forEach((el) => {
      const p = { el, duration: Number(el.dataset.duration) || 600, t: Number(el.dataset.start) || 0, rate: 0, playing: false };
      players.push(p);
      el.querySelectorAll('[data-pp-toggle]').forEach((b) => b.addEventListener('click', () => play(p, !p.playing)));
      el.querySelectorAll('[data-pp-skip]').forEach((b) => b.addEventListener('click', () => {
        p.t = Math.max(0, Math.min(p.duration, p.t + Number(b.dataset.ppSkip))); paint(p);
      }));
      el.querySelectorAll('[data-pp-rate]').forEach((b) => b.addEventListener('click', () => {
        p.rate = (p.rate + 1) % RATES.length; paint(p);
      }));
      el.querySelectorAll('[data-pp-track]').forEach((tr) => {
        tr.setAttribute('role', 'slider');
        tr.setAttribute('tabindex', '0');
        tr.setAttribute('aria-valuemin', '0');
        tr.setAttribute('aria-valuemax', String(p.duration));
        if (!tr.hasAttribute('aria-label')) tr.setAttribute('aria-label', '播放进度');
        const seek = (clientX) => {
          const r = tr.getBoundingClientRect();
          p.t = Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * p.duration; paint(p);
        };
        tr.addEventListener('pointerdown', (e) => {
          tr.setPointerCapture(e.pointerId); seek(e.clientX); tr.dataset.dragging = '';
          const move = (ev) => seek(ev.clientX);
          const up = () => { tr.removeEventListener('pointermove', move); delete tr.dataset.dragging; };
          tr.addEventListener('pointermove', move);
          tr.addEventListener('pointerup', up, { once: true });
          tr.addEventListener('pointercancel', up, { once: true });
        });
        tr.addEventListener('keydown', (e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            e.preventDefault();
            p.t = Math.max(0, Math.min(p.duration, p.t + (e.key === 'ArrowRight' ? 5 : -5))); paint(p);
          } else if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); play(p, !p.playing); }
        });
      });
      paint(p);
    });
  };
  PP.playerOf = (el) => players.find((p) => p.el === el);
  PP.play = (el, on = true) => { const p = PP.playerOf(el); if (p) play(p, on); };
})();
