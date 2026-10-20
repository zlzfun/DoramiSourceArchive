/* 现状:逐字复刻 PodcastAudioPanel 的 DOM 与 class(样式取自编译产物),播放器是浏览器原生 <audio controls>。 */
PP.options.current = {
  id: 'current',
  order: 0,
  name: '现状',
  idea: '节目卡 + 模式切换 + 原生播放器,三层嵌套在一张色底卡里。',
  points: [
    '节目名在眉头与卡里各出现一次；「原节目 12 分钟」在署名行、切换钮、音轨头重复三次',
    '「仅提供原节目」状态章说的是「没有什么」',
    '「点播精品导读」是一个动作，却长成模式切换的一半',
    '原生播放器随浏览器变样（Safari 带隔空播放与倍速，Chrome 是灰条）',
    '色底卡与下方细线 AI 卡两种卡语法并排',
  ],
  render(ctx) {
    const { state: st, ep, fmt, esc, bodyMode } = ctx;
    const orig = fmt.minutes(ep.durationSeconds);
    const guideDur = fmt.minutes(ep.guideDurationSeconds);
    const status = st.guideAudio ? { label: '精品导读音频已就绪', tone: 'ok' }
      : st.guideText ? { label: '中文导读可阅读', tone: 'idle' }
      : st.ondemand === 'generating' ? { label: '精品导读生成中…', tone: 'run' }
      : { label: '仅提供原节目', tone: 'idle' };
    const digestOn = st.variant === 'digest' && st.guideAudio;
    const ondemandVisible = st.ondemand !== 'off';
    let seg = '';
    if (st.guideAudio || ondemandVisible) {
      const second = st.guideAudio
        ? `<button type="button" class="mini-seg-btn ${digestOn ? 'is-on' : ''}" data-go="${digestOn ? '' : 's4'}">精品导读 · ${guideDur}</button>`
        : `<button type="button" class="mini-seg-btn podcast-ondemand-seg" ${st.ondemand === 'generating' ? 'disabled aria-busy="true"' : 'data-go="s2"'}>${st.ondemand === 'generating' ? '生成中…' : st.ondemand === 'audio' ? '生成导读音频' : '点播精品导读'}</button>`;
      seg = `<div class="mini-seg podcast-mode-switch" role="group" aria-label="播客播放模式">
        <button type="button" class="mini-seg-btn ${digestOn ? '' : 'is-on'}" data-go="${digestOn ? 's3' : ''}">原节目 · ${orig}</button>${second}</div>`;
    }
    const track = digestOn
      ? `<div class="podcast-audio-track-head"><span class="podcast-audio-label">精品导读</span><span>${guideDur}</span><span class="podcast-audio-ai">AI 生成</span></div>`
      : `<div class="podcast-audio-track-head"><span class="podcast-audio-label">原节目</span><span>${orig}</span></div>`;
    const panel = `<section class="podcast-player-panel" aria-label="播客音频">
      <div class="podcast-player-summary">
        <img class="podcast-player-cover" src="${ep.cover}" alt="">
        <div class="podcast-player-copy">
          <span class="podcast-player-kicker">播客节目</span>
          <strong>${esc(ep.source)}</strong>
          <span class="podcast-status is-${status.tone}">${status.label}</span>
        </div>
      </div>
      ${seg}
      <div class="podcast-audio-track">${track}<audio controls preload="none" src="https://pscrb.fm/rss/p/prfx.byspotify.com/e/claritaspod.com/measure/api.substack.com/feed/podcast/220749685/cef802d73b37fbdee58a57c96b0307f3.mp3"></audio></div>
    </section>`;
    const guide = bodyMode === 'guide' ? ctx.html.guide() : '';
    return {
      top: `<div class="podcast-experience-panel ${bodyMode === 'guide' ? 'has-guide' : ''}">${panel}${guide}</div>`,
      guideHandled: true,
    };
  },
  mount(root, ctx) {
    root.querySelectorAll('[data-go]').forEach((b) => {
      if (b.dataset.go) b.addEventListener('click', () => ctx.setState(b.dataset.go));
    });
  },
};
