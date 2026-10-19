/* 方案 a「同框」(保守精修):播客区收成一张与「简介导读」AI 卡同族的细线卡。
   首行:封面 + 音轨(文字档位:原节目 / ✦ 精品导读)+ 行尾动作位(点播 → 生成中 → 就绪后并入音轨;选中导读时写「AI 生成」);
   次行:自绘播放器(圆钮 + 细进度 + 当前 / 总时长 + ±15 + 倍速),交互由 pane.js 的模拟播放器经 data-pp-* 钩子驱动。
   播放器原语只认 .ppa-player 一行,文章「听导读」可原样复用(首行换成「✦ 听导读 …… AI 生成」)。 */
(function () {
  const SPARK = '<span class="ppa-spark" aria-hidden="true"></span>';

  /* 首行左:音轨。只有原节目时是标签(没有可选项就不画档位);导读音频就绪后才是两个档位 */
  function tracksHtml(st, ep, fmt) {
    const digestOn = st.variant === 'digest' && st.guideAudio;
    if (!st.guideAudio) {
      return '<div class="ppa-tracks"><span class="ppa-track is-solo"><span class="ppa-track-text" data-label="原节目">原节目</span></span></div>';
    }
    const tab = (on, go, label, title, spark = '') => `<button type="button" class="ppa-track ${on ? 'is-on' : ''}" aria-pressed="${on}" ${on ? '' : `data-ppa-go="${go}"`} title="${title}">${spark}<span class="ppa-track-text" data-label="${label}">${label}</span></button>`;
    return `<div class="ppa-tracks" role="group" aria-label="音轨">
      ${tab(!digestOn, 's3', '原节目', `原节目 · ${fmt.minutes(ep.durationSeconds)}`)}
      ${tab(digestOn, 's4', '精品导读', `精品导读 · ${fmt.minutes(ep.guideDurationSeconds)} · AI 生成`, SPARK)}
    </div>`;
  }

  /* 首行右:动作位。动作长成描边按钮;生成中原位变状态(不让请求凭空消失);选中导读时这里写「AI 生成」 */
  function sideHtml(st, icons) {
    const digestOn = st.variant === 'digest' && st.guideAudio;
    if (digestOn) return '<span class="ppa-note">AI 生成</span>';
    if (st.guideAudio || st.ondemand === 'off') return '';
    if (st.ondemand === 'generating') {
      return `<span class="ppa-status" role="status">${icons.loader.replace('class="animate-spin"', 'class="animate-spin ppa-spin"')}精品导读生成中…</span>`;
    }
    const label = st.ondemand === 'audio' ? '生成导读音频' : '点播精品导读';
    return `<button type="button" class="ppa-act" data-ppa-act="${st.ondemand}">${SPARK}<span>${label}</span></button>`;
  }

  function playerHtml(st, ep, icons, fmt) {
    const digestOn = st.variant === 'digest' && st.guideAudio;
    const dur = digestOn ? ep.guideDurationSeconds : ep.durationSeconds;
    const name = digestOn ? '精品导读' : '原节目';
    return `<div class="ppa-player" data-pp-player data-duration="${dur}" data-start="0">
      <button type="button" class="ppa-play" data-pp-toggle aria-label="播放"><span class="ppa-play-icon" data-pp-icon>${icons.play}</span></button>
      <span class="ppa-time ppa-cur" data-pp-cur>00:00</span>
      <div class="ppa-bar" data-pp-track aria-label="${name}播放进度"><span class="ppa-fill" data-pp-fill></span><span class="ppa-thumb" data-pp-thumb></span></div>
      <span class="ppa-time ppa-dur" data-pp-dur>${fmt.clock(dur)}</span>
      <div class="ppa-ctrls">
        <button type="button" class="ppa-ctrl" data-pp-skip="-15" aria-label="后退 15 秒">${icons.back15}<span aria-hidden="true">15</span></button>
        <button type="button" class="ppa-ctrl" data-pp-skip="15" aria-label="前进 15 秒"><span aria-hidden="true">15</span>${icons.fwd15}</button>
        <button type="button" class="ppa-ctrl ppa-rate" data-pp-rate aria-label="播放速度 1 倍">1×</button>
      </div>
    </div>`;
  }

  /* 导读稿:标题沿用「节目简介 · 来源方提供」同一语法(中文导读 · AI 整理——列表里同一个词),
     不再写「精品导读」:那是音轨档位的名字,就在上方一行;正文用阅读窗长文排印 */
  function guideHtml(ep, esc) {
    const body = ep.guide.map(([tag, text]) => `<${tag}>${esc(text)}</${tag}>`).join('');
    return `<section class="ppa-guide" aria-label="中文导读">
      <div class="podcast-show-notes-head"><h2 class="section-title">中文导读</h2><span>AI 整理</span></div>
      <div class="reader-article-copy markdown-body">${body}</div>
    </section>`;
  }

  PP.options.a = {
    id: 'a',
    order: 1,
    name: '同框',
    idea: '播客区收成一张与「简介导读」同族的细线卡：首行封面、音轨与点播，次行自绘播放器。',
    points: [
      '两张卡同一套语法（外框色底、最弱细线、同圆角、同左内边距），上下是兄弟',
      '点播是行尾的描边按钮，生成中原位变状态；音频就绪后「✦ 精品导读」才进音轨档位，选中时行尾写「AI 生成」',
      '删去「播客节目」、卡内节目名、状态章与重复时长，署名行「原节目 12 分钟」也删（总时长在播放器里）；代价：选中导读时看不到原节目时长',
      '导读稿标题改「中文导读 · AI 整理」，与「节目简介 · 来源方提供」同语法，不重复上面的音轨名',
      '播放器原语（圆钮 / 细进度 / 时间 / ±15 / 倍速）可原样给文章「听导读」用；手机上进度条另起一行，卡片比桌面高一截',
      '代价：往下读正文时播放器随页面滚走，没有吸底条',
    ],
    render(ctx) {
      const { state: st, ep, icons, fmt, esc, bodyMode } = ctx;
      const card = `<section class="ppa-card" aria-label="播客音频">
        <div class="ppa-head">
          <img class="ppa-cover" src="${ep.cover}" alt="">
          ${tracksHtml(st, ep, fmt)}
          <div class="ppa-side">${sideHtml(st, icons)}</div>
        </div>
        ${playerHtml(st, ep, icons, fmt)}
      </section>`;
      return {
        top: card + (bodyMode === 'guide' ? guideHtml(ep, esc) : ''),
        guideHandled: true,
        meta: [ep.date, `简介阅读约 ${ep.readMinutes} 分钟`, `阅读量 ${ep.readCount}`],
      };
    },
    mount(root, ctx) {
      root.querySelectorAll('[data-ppa-go]').forEach((b) => {
        b.addEventListener('click', () => ctx.setState(b.dataset.ppaGo));
      });
      root.querySelectorAll('[data-ppa-act]').forEach((b) => {
        b.addEventListener('click', () => {
          if (b.dataset.ppaAct === 'available') { ctx.setState('s2'); return; }
          // 导读稿已在、只差音频:原位变状态,正文的导读稿保持不动(样页状态表没有「s5 + 生成中」,就地演示)
          const side = b.closest('.ppa-side');
          side.innerHTML = `<span class="ppa-status" role="status">${ctx.icons.loader.replace('class="animate-spin"', 'class="animate-spin ppa-spin"')}导读音频生成中…</span>`;
        });
      });
    },
  };
})();
