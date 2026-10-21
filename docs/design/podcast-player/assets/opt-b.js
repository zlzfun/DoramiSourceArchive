/* 方案 b「音轨」:一期节目的音频当成一张音轨清单。
   原节目 / ✦ 精品导读各占一行,顺序固定;选中的一行就地展开成播放器(两行:标题 + 控件 / 进度),
   其余行一行高。精品导读还没有时那一行照样在,行尾就是点播——点播 = 给清单补一条音轨,
   不再和「切换模式」长在一起。外框与下方简介导读卡同一种细线卡,✦ 标题同一种写法。
   播放器原语(.ppb-player)不认识播客:文章页「听导读」= 只有一行 ✦ 音轨的同一张卡。 */
(function () {
  const I = PP.icons;
  const pos = { original: 0, guide: 0 };   // 切换音轨时各自记住听到哪(现网按 variant 存 localStorage)
  let autoplay = false;                     // 点行切换后,在新一次渲染里接着播

  const TRACK = {
    original: { label: '原节目', seconds: () => PP.episode.durationSeconds },
    guide: { label: '精品导读', seconds: () => PP.episode.guideDurationSeconds },
  };

  function spoken(sec) {
    const s = Math.round(sec);
    return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  }

  function title(kind) {
    return kind === 'guide'
      ? '<span class="ppb-name"><span class="ppb-spark" aria-hidden="true"></span>精品导读</span><span class="ppb-meta">AI 生成</span>'
      : '<span class="ppb-name">原节目</span>';
  }

  // 选中行:就地展开的播放器。钩子按 pane.js 模拟播放器约定。
  // bare = 清单只剩这一条且不是 AI 音轨:标题行只为「区分音轨 / 标明 AI」而存在,两者都不需要时退成一行播放条。
  function player(kind, bare = false) {
    const t = TRACK[kind];
    return `<li class="ppb-track is-open" data-kind="${kind}" aria-current="true">
      <div class="ppb-player ${bare ? 'is-bare' : ''}" data-pp-player data-duration="${t.seconds()}" data-start="${pos[kind] || 0}">
        <button type="button" class="ppb-play" data-pp-toggle aria-label="播放"${bare ? '' : ` aria-describedby="ppb-name-${kind}"`}><span class="ppb-play-icon" data-pp-icon>${I.play}</span></button>
        <div class="ppb-head">
          ${bare ? '' : `<div class="ppb-title" id="ppb-name-${kind}">${title(kind)}</div>`}
          <div class="ppb-ctrls">
            <button type="button" class="ppb-btn ppb-skip" data-pp-skip="-15" aria-label="后退 15 秒" title="后退 15 秒">${I.back15}<span>15</span></button>
            <button type="button" class="ppb-btn ppb-skip" data-pp-skip="15" aria-label="前进 15 秒" title="前进 15 秒"><span>15</span>${I.fwd15}</button>
            <button type="button" class="ppb-btn ppb-rate" data-pp-rate>1×</button>
          </div>
        </div>
        <div class="ppb-scrub">
          <span class="ppb-time" data-pp-cur>00:00</span>
          <div class="ppb-bar" data-pp-track aria-label="${t.label}播放进度"><span class="ppb-fill" data-pp-fill></span><span class="ppb-thumb" data-pp-thumb></span></div>
          <span class="ppb-time is-remain" data-pp-left></span>
        </div>
      </div>
    </li>`;
  }

  // 未选中、可播放的行:整行是按钮,点了切过去并接着播
  function playableRow(kind, go) {
    const t = TRACK[kind];
    const name = kind === 'guide' ? '精品导读（AI 生成）' : '原节目';
    return `<li class="ppb-track" data-kind="${kind}">
      <button type="button" class="ppb-row is-playable" data-ppb-go="${go}" data-ppb-from="${kind === 'guide' ? 'original' : 'guide'}" aria-label="播放${name}，${spoken(t.seconds())}">
        <span class="ppb-lead" aria-hidden="true">${I.play}</span>
        <span class="ppb-title">${title(kind)}</span>
        <span class="ppb-dur">${PP.fmt.clock(t.seconds())}</span>
      </button>
    </li>`;
  }

  // 还没有的精品导读:行照样在,行尾是点播 / 生成中
  function missingRow(st) {
    const end = st.ondemand === 'generating'
      ? `<span class="ppb-busy" role="status" aria-live="polite">${I.loader}<span>生成中…</span></span>`
      : '<button type="button" class="ppb-get" data-ppb-request aria-label="点播精品导读">点播</button>';
    return `<li class="ppb-track is-missing" data-kind="guide">
      <div class="ppb-row">
        <span class="ppb-lead" aria-hidden="true"></span>
        <span class="ppb-title">${title('guide')}</span>
        ${end}
      </div>
    </li>`;
  }

  function guideText(ep, esc) {
    const body = ep.guide.map(([tag, text]) => `<${tag}>${esc(text)}</${tag}>`).join('');
    return `<section class="ppb-guide" aria-label="导读稿">
      <div class="podcast-show-notes-head"><h2 class="section-title">导读稿</h2><span>AI 整理</span></div>
      <div class="reader-article-copy markdown-body">${body}</div>
    </section>`;
  }

  PP.options.b = {
    id: 'b',
    order: 2,
    name: '音轨',
    idea: '一期节目的音频是一张音轨清单：原节目、✦ 精品导读各占一行，选中的一行就地展开成播放器；精品导读还没有时那一行照样在，行尾就是点播。',
    points: [
      '点播 = 给清单补一条音轨：行尾从「点播」到「生成中…」再到时长，不再是模式切换的一半',
      '时长只在音轨行出现一次；封面、「播客节目」、卡内节目名、「仅提供原节目」和署名行的时长都去掉',
      '自绘播放器：墨色圆钮、细进度条、当前 / 剩余、±15、倍速，各浏览器一个样；文章页「听导读」就是只有一条 ✦ 音轨的同一张卡',
      '外框与简介导读卡同一种细线卡，「✦ 精品导读」与「✦ 简介导读」同一种写法',
      '只有原节目时退成一行播放条，不留空槽',
      '代价：阅读窗里没有节目封面；点精品导读那一行就开始播放，只想看导读稿也会先出声；窄屏播放器要三行',
    ],
    render(ctx) {
      const { state: st, ep, bodyMode, esc } = ctx;
      const digestOn = st.variant === 'digest' && st.guideAudio;
      const rows = [];
      if (digestOn) {
        rows.push(playableRow('original', 's3'), player('guide'));
      } else {
        const single = !st.guideAudio && st.ondemand === 'off';
        rows.push(player('original', single));
        if (st.guideAudio) rows.push(playableRow('guide', 's4'));
        else if (!single) rows.push(missingRow(st));
      }
      const card = `<section class="ppb" aria-label="收听">
        <ul class="ppb-list" role="list">${rows.join('')}</ul>
      </section>`;
      return {
        top: `${card}${bodyMode === 'guide' ? guideText(ep, esc) : ''}`,
        meta: [ep.date, `简介阅读约 ${ep.readMinutes} 分钟`, `阅读量 ${ep.readCount}`],
        guideHandled: true,
      };
    },
    mount(root, ctx) {
      root.querySelectorAll('[data-ppb-go]').forEach((b) => b.addEventListener('click', () => {
        const open = root.querySelector('.ppb-track.is-open [data-pp-player]');
        const p = open && PP.playerOf(open);
        if (p) pos[b.dataset.ppbFrom] = p.t;
        autoplay = true;
        ctx.setState(b.dataset.ppbGo);
      }));
      root.querySelectorAll('[data-ppb-request]').forEach((b) => b.addEventListener('click', () => {
        b.disabled = true;
        b.textContent = '提交中…';
        setTimeout(() => ctx.setState('s2'), 450);
      }));
      if (autoplay) {
        autoplay = false;
        const el = root.querySelector('.ppb-track.is-open [data-pp-player]');
        if (el) PP.play(el, true);
      }
    },
  };
})();
