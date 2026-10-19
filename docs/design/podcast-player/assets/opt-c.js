/* 方案 c「随行」(issue #199):标题区与 AI 卡之间不再有播客块。
   ① 收听入口进标题区——桌面在尾行动作区(与「原语言 | 译为中文」「查看原文」同一行,收听在最前),
      移动端没有动作行,放署名行下。原节目是这一屏唯一的实心主钮;精品导读是带 ✦ 的次钮,
      点播 / 生成中 / 生成导读音频 / 就绪后直接收听,都在同一个位置变化。
   ② 开始收听后,播放条吸附在阅读窗滚动容器底部(sticky),往下读简介导读、节目简介、逐字稿时一直在;
      收起 = 暂停并收起,入口回到标题区。没开始听时播放条不出现——没有可控的东西,常驻只会重复标题区的入口。
   ③ 两条音轨都在时,播放条里点音轨名切换;听精品导读时音轨名旁写明「AI 生成」。
   画框内的迁移(开始收听 / 点播 / 换音轨)就地更新、不整页重绘:播放不中断、滚动位置不丢;
   状态迁移经 pp-state 通知对照外壳,外壳换状态(pp-set-state)才整页重绘并重置会话。
   播放条只依赖 data-pp-* 钩子,结构不认播客:文章页「听导读」可原样复用(无封面时换 ✦ 方块)。 */
(function () {
  const svg = (inner) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  const ICON = {
    swap: svg('<path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/>'),
  };

  // 会话状态:只活在本画框里。外壳换状态时整页重绘,render() 重置它
  const C = {
    st: null,          // 当前状态的可变副本(画框内点播会改 ondemand)
    variant: 'original',
    open: false,       // 播放条是否展开(= 本次收听会话进行中)
    pos: { original: 0, digest: 0 },  // 各音轨的位置(切换音轨各自续播,对应现网按音轨记忆进度)
    audioBusy: false,  // s5 点了「生成导读音频」之后(骨架没有「导读稿可读 · 音频生成中」状态,就地表现)
    ctx: null, root: null, dock: null, body: null, scroller: null, entries: null,
  };

  const track = (key) => {
    const ep = C.ctx.ep;
    return key === 'digest'
      ? { key, name: '精品导读', seconds: ep.guideDurationSeconds, ai: true }
      : { key, name: '原节目', seconds: ep.durationSeconds, ai: false };
  };
  const isPlaying = () => Boolean(C.dock?.hasAttribute('data-playing'));
  const guideBody = () => Boolean(C.st.guideText && (!C.st.guideAudio || C.variant === 'digest'));
  const motionOff = () => document.documentElement.dataset.motion === 'reduce';

  function post(stateId) {
    try { parent.postMessage({ type: 'pp-state', state: stateId }, '*'); } catch { /* 独立打开时无父页 */ }
  }

  /* ── 标题区入口 ── */
  const eq = (ai, live) => `<span class="ppc-eq${ai ? ' is-ai' : ''}${live ? ' is-live' : ''}" aria-hidden="true"><i></i><i></i><i></i></span>`;
  const spark = (extra = '') => `<span class="ppc-spark${extra}" aria-hidden="true"></span>`;

  function entriesInner() {
    const { icons: I, fmt } = C.ctx;
    const st = C.st;
    const playing = isPlaying();
    const out = [];

    // 原节目:没在听时是这一屏的主钮;在听时退成「选中」并显示播放态
    const o = track('original');
    const oCur = C.open && C.variant === 'original';
    const oPrimary = !C.open && C.variant === 'original';
    // 可访问名始终含可见字(语音控制按看到的字点);播放态交给 aria-pressed:按下 = 这条音轨正在播放
    out.push(`<button type="button" class="ppc-entry ${oCur ? 'is-current' : oPrimary ? 'is-primary' : ''}" data-ppc="original" aria-label="收听原节目，${fmt.minutes(o.seconds)}" aria-pressed="${oCur && playing}">${oCur ? eq(false, playing) : `<span class="ppc-ico ppc-ico-play">${I.play}</span>`}<span>收听原节目</span><span class="ppc-len">${fmt.minutes(o.seconds)}</span></button>`);

    // 精品导读:同一个位置承接点播 / 生成中 / 生成导读音频 / 收听
    if (st.guideAudio) {
      const g = track('digest');
      const gSel = C.variant === 'digest';
      const gLive = gSel && C.open;
      out.push(`<button type="button" class="ppc-entry ppc-entry-ai ${gSel ? 'is-current' : ''}" data-ppc="digest" aria-label="收听精品导读，${fmt.minutes(g.seconds)}，AI 生成" aria-pressed="${gLive && playing}">${gLive ? eq(true, playing) : spark()}<span>精品导读</span><span class="ppc-len">${fmt.minutes(g.seconds)}</span></button>`);
    } else if (st.ondemand === 'generating' || (st.guideText && C.audioBusy)) {
      const label = st.guideText ? '导读音频生成中…' : '导读生成中…';
      out.push(`<button type="button" class="ppc-entry ppc-entry-ai is-busy" data-ppc="busy" aria-disabled="true">${spark(' is-breathing')}<span role="status">${label}</span></button>`);
    } else if (st.ondemand === 'available') {
      out.push(`<button type="button" class="ppc-entry ppc-entry-ai" data-ppc="request">${spark()}<span>点播精品导读</span></button>`);
    } else if (st.ondemand === 'audio') {
      out.push(`<button type="button" class="ppc-entry ppc-entry-ai" data-ppc="request-audio">${spark()}<span>生成导读音频</span></button>`);
    }
    // ondemand === 'off'(AI 关 / 本部署不能点播):只有原节目,不画任何「没有什么」的说明
    return out.join('');
  }

  function syncEntries() {
    const box = C.entries;
    if (!box) return;
    const focusedIndex = [...box.children].indexOf(document.activeElement);
    box.innerHTML = entriesInner();
    // 重建后把焦点还给同一位置的入口(点播 → 生成中用 aria-disabled 保持可聚焦,焦点不掉到 body)
    if (focusedIndex >= 0) (box.children[focusedIndex] || box.children[0])?.focus();
  }

  /* ── 播放条 ── */
  function trackBlock() {
    const fmt = C.ctx.fmt;
    const t = track(C.variant);
    const other = C.st.guideAudio ? track(C.variant === 'digest' ? 'original' : 'digest') : null;
    const name = `<span class="ppc-name">${t.ai ? spark() : ''}<span class="ppc-name-text">${t.name}</span>${t.ai ? '<span class="ppc-ai">AI 生成</span>' : ''}</span>`;
    if (!other) return `<div class="ppc-track">${name}</div>`;
    const label = `改听${other.name}，${fmt.minutes(other.seconds)}${other.ai ? '，AI 生成' : ''}`;
    return `<button type="button" class="ppc-track is-switch" data-ppc="swap" aria-label="${label}">${name}<span class="ppc-swap">${ICON.swap}改听${other.name}</span></button>`;
  }

  function regionLabel() {
    const t = track(C.variant);
    return `播放器：${t.name}${t.ai ? '（AI 生成）' : ''}`;
  }

  function dockHtml() {
    const { icons: I, fmt, ep } = C.ctx;
    const t = track(C.variant);
    return `<div class="ppc-dock${C.open ? ' is-shown' : ''}" ${C.open ? '' : 'hidden'} data-pp-player data-duration="${t.seconds}" data-start="${C.pos[C.variant]}">
      <div class="ppc-card" role="region" aria-label="${regionLabel()}">
        <img class="ppc-cover" src="${ep.cover}" alt="">
        <div class="ppc-track-slot">${trackBlock()}</div>
        <div class="ppc-ctrl">
          <button type="button" class="ppc-btn ppc-skip" data-pp-skip="-15" aria-label="后退 15 秒">${I.back15}<span>15</span></button>
          <button type="button" class="ppc-play" data-pp-toggle aria-label="播放"><span class="ppc-play-ico" data-pp-icon>${I.play}</span></button>
          <button type="button" class="ppc-btn ppc-skip" data-pp-skip="15" aria-label="前进 15 秒"><span>15</span>${I.fwd15}</button>
        </div>
        <div class="ppc-row2">
          <div class="ppc-seek">
            <span class="ppc-time ppc-cur" data-pp-cur>${fmt.clock(C.pos[C.variant])}</span>
            <div class="ppc-bar"><div class="ppc-bar-in" data-pp-track aria-label="播放进度"><span class="ppc-rail"><span class="ppc-fill" data-pp-fill></span></span><span class="ppc-thumb" data-pp-thumb></span></div></div>
            <span class="ppc-time" data-pp-dur>${fmt.clock(t.seconds)}</span>
          </div>
          <button type="button" class="ppc-btn ppc-rate" data-pp-rate>1×</button>
          <button type="button" class="ppc-btn ppc-close" data-ppc="close" aria-label="收起播放条（暂停）" title="收起">${I.chevronDown}</button>
        </div>
      </div>
    </div>`;
  }

  function syncDockTrack() {
    const slot = C.dock.querySelector('.ppc-track-slot');
    const hadFocus = slot.contains(document.activeElement);
    slot.innerHTML = trackBlock();
    C.dock.querySelector('.ppc-card').setAttribute('aria-label', regionLabel());
    // 在条内换音轨时切换钮被重建:焦点留在新的切换钮上,键盘可以连按来回切
    if (hadFocus) slot.querySelector('button')?.focus();
  }

  function openDock() {
    if (C.open) return;
    C.open = true;
    const dock = C.dock;
    dock.hidden = false;
    if (motionOff()) { dock.classList.add('is-shown'); return; }
    void dock.offsetHeight;            // 先以收起态落一帧,再过渡到展开
    dock.classList.add('is-shown');
  }

  function closeDock() {
    if (!C.open) return;
    const p = PP.playerOf(C.dock);
    if (p) { PP.play(C.dock, false); C.pos[C.variant] = p.t; }
    C.open = false;
    const dock = C.dock;
    // 焦点在播放条里时交还给标题区当前音轨的入口,不让它掉进 body
    const hadFocus = dock.contains(document.activeElement);
    dock.classList.remove('is-shown');
    const done = () => { if (!C.open) dock.hidden = true; };
    if (motionOff()) done(); else setTimeout(done, 240);
    syncEntries();
    if (hadFocus) C.entries?.querySelector(`[data-ppc="${C.variant}"]`)?.focus();
  }

  /* ── 正文:导读稿与原节目材料同在 DOM,按当前音轨显示(现网口径:选中精品导读 = 正文换成导读稿) ── */
  function applyBody({ keepReading = false } = {}) {
    if (!C.body) return;
    const before = C.body.dataset.ppcBody;
    const mode = guideBody() ? 'guide' : 'normal';
    C.body.dataset.ppcBody = mode;
    // 读到正文中段时换了正文:回到新正文开头,而不是停在新正文的随机位置
    if (keepReading && before && before !== mode && C.scroller) {
      const top = C.scroller.scrollTop + C.body.getBoundingClientRect().top - C.scroller.getBoundingClientRect().top - 16;
      if (C.scroller.scrollTop > top) C.scroller.scrollTop = Math.max(0, top);
    }
  }

  function select(key, { play = true } = {}) {
    const p = PP.playerOf(C.dock);
    if (C.variant !== key) {
      if (p) C.pos[C.variant] = p.t;
      C.variant = key;
      const t = track(key);
      if (p) { p.duration = t.seconds; p.t = Math.min(C.pos[key] || 0, t.seconds); }
      C.dock.dataset.duration = String(t.seconds);
      C.dock.querySelector('[data-pp-track]')?.setAttribute('aria-valuemax', String(t.seconds));
      syncDockTrack();
      applyBody({ keepReading: true });
      if (C.st.guideAudio) post(key === 'digest' ? 's4' : 's3');
    }
    openDock();
    PP.play(C.dock, play);   // play(false) 也会重绘时间与进度
    syncEntries();
  }

  function onAction(e) {
    const btn = e.target.closest('[data-ppc]');
    if (!btn) return;
    const act = btn.dataset.ppc;
    if (act === 'original' || act === 'digest') {
      if (C.open && C.variant === act) PP.play(C.dock, !isPlaying());
      else select(act, { play: true });
    } else if (act === 'request') {
      C.st.ondemand = 'generating';
      syncEntries();
      post('s2');
    } else if (act === 'request-audio') {
      C.audioBusy = true;
      syncEntries();
    } else if (act === 'swap') {
      select(C.variant === 'digest' ? 'original' : 'digest', { play: isPlaying() });
    } else if (act === 'close') {
      closeDock();
    }
  }

  PP.options.c = {
    id: 'c',
    order: 3,
    name: '随行',
    idea: '收听入口并进标题区；开始收听后，播放条吸附在阅读窗底部，边读边听。',
    points: [
      '标题与 AI 卡之间不再有播客块；「播客节目」小标签、卡内节目名、「仅提供原节目」与重复时长都删掉',
      '「收听原节目」是这一屏唯一的实心主钮；精品导读的点播、生成中、收听都在它旁边同一个位置',
      '开始收听后播放条才出现，读到哪都在；收起即暂停',
      '两条音轨在条内点音轨名切换，听精品导读时写明「AI 生成」',
      '代价：读到中段才想听要回标题区；播放条盖住正文底部一截（手机两行，约 90px）',
      '播放条与内容类型无关，文章页「听导读」可原样复用',
    ],

    render(ctx) {
      const { state, ep, html, device } = ctx;
      // 外壳换状态 / 首次打开:按状态重置会话。s4 = 已选中精品导读 → 播放条载入导读、停在开头
      C.ctx = ctx;
      C.st = { ...state };
      C.variant = state.variant === 'digest' && state.guideAudio ? 'digest' : 'original';
      C.open = C.variant === 'digest';
      C.pos = { original: 0, digest: 0 };
      C.audioBusy = false;

      const entries = `<div class="ppc-entries" role="group" aria-label="收听">${entriesInner()}</div>`;
      const parts = {
        // 署名行只放事实;原节目时长已在入口上,不再重复
        meta: [ep.date, `简介阅读约 ${ep.readMinutes} 分钟`, `阅读量 ${ep.readCount}`],
        dock: dockHtml(),
      };
      if (device === 'desktop') parts.headActions = entries;
      else parts.headBelowMeta = `<div class="ppc-head-listen">${entries}</div>`;

      const guide = state.guideText ? `<div class="ppc-guide">${html.guide()}</div>` : '';
      if (ctx.bodyMode === 'guide') {
        // 导读稿态由本方案自己画正文:原节目材料一并放进 DOM(隐藏),换回原节目时就地显示
        parts.guideHandled = true;
        parts.top = `${guide}${html.aiCard()}${html.showNotes()}`;
      } else {
        parts.top = guide;
      }
      return parts;
    },

    mount(root) {
      C.root = root;
      C.dock = root.querySelector('.ppc-dock');
      C.body = root.querySelector('.reader-pane-body, .m-read-body');
      C.scroller = root.querySelector('#pp-scroll');
      C.entries = root.querySelector('.ppc-entries');
      // 桌面:入口是动作行的第一项(DOM 顺序 = 视觉顺序,Tab 先到收听)
      const actions = root.querySelector('.reader-pane-actions');
      if (actions && C.entries?.parentElement === actions) actions.prepend(C.entries);
      applyBody();
      // 渐隐的底色必须等于阅读窗实际底色(现网 = surface;样页暗色 body 落在 canvas):沿祖先取第一个不透明底
      if (C.dock) {
        for (let el = C.scroller; el; el = el.parentElement) {
          const bg = getComputedStyle(el).backgroundColor;
          if (bg && bg !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(bg)) { C.dock.style.setProperty('--ppc-bg', bg); break; }
        }
      }
      // 监听挂在本次渲染新建的方案根上(#pp-root 跨渲染复用,挂在它上面会越积越多)
      root.querySelector('[data-opt="c"]')?.addEventListener('click', onAction);
      // 播放 / 暂停 / 播完都会改 data-playing:标题区入口跟着换播放态
      if (C.dock) new MutationObserver(() => syncEntries()).observe(C.dock, { attributes: true, attributeFilter: ['data-playing'] });
    },
  };
})();
