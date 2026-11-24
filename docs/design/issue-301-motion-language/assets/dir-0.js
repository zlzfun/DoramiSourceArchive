/* 现网 —— 对照基线(取值出处见 dir-0.css 顶部)。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  let odoFor = null;
  function odometer(el, play) {
    const txt = el.textContent.trim();
    if (!/^\d+\.\d$/.test(txt)) return;
    el.classList.add('odo');
    const strip = (ch, i) => {
      const st = play ? `transition-delay:${i === txt.length - 1 ? 220 : 0}ms` : `transition:none;transform:translateY(-${ch * 1.2}em)`;
      return `<span class="odo-cell"><span class="odo-strip" data-d="${ch}" style="${st}">${'0123456789'.split('').map((d) => `<span>${d}</span>`).join('')}</span></span>`;
    };
    el.innerHTML = [...txt].map((ch, i) => (ch === '.' ? '<span class="odo-dot">.</span>' : strip(ch, i))).join('');
    if (play) requestAnimationFrame(() => requestAnimationFrame(() => $$('.odo-strip', el).forEach((s) => { s.style.transform = `translateY(-${s.dataset.d * 1.2}em)`; })));
  }
  /* 现网的入场是「按 key 重挂载时播一次」:这里在对应的交互之后给新画出来的元素挂一次类名 */
  const enter = (sel) => { const el = $(sel); if (el) el.classList.add('d0-enter'); };
  M.register('0', {
    name: '现网',
    init() { odoFor = null; },
    rendered() {
      /* 速读卡的主分数:换了一篇才滚,同一篇重画(收藏、标读)直接落在分值上 */
      const el = $('#pane .side-blk:not(.minor) .side-score .sc') || $('#pane .ai-scores .sc');
      if (!el || el.classList.contains('odo')) return;
      const id = window.__proto && window.__proto.state.sel;
      odometer(el, id !== odoFor && !M.reduced()); odoFor = id;
    },
    on: {
      /* 键盘 j / k 在现网同样会重挂载阅读窗,所以这里不看 ctx.key */
      open(ctx, mutate) { mutate(); enter('#pane'); },
      nav(ctx, mutate) { mutate(); if (ctx.to === 'article' || ctx.to === 'podcast') enter('#pane'); },
      filter(ctx, mutate) { mutate(); if (['src', 'kind', 'unread', 'favonly', 'scope'].includes(ctx.group)) enter('.list-b'); },
      qa(ctx, mutate) {
        if (!ctx.open) M.exit($('.qa'), [{ opacity: 1, transform: 'translateY(0) scale(1)' }, { opacity: 0, transform: 'translateY(10px) scale(0.97)' }], { duration: 200, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' });
        mutate(); if (ctx.open) enter('.qa');
      },
      qaask(ctx, mutate) { mutate(); enter('.qa-b'); },
      search(ctx, mutate) { mutate(); if (ctx.open) enter('.list-find'); },
      toast(ctx, mutate) {
        if (ctx.phase === 'out') M.exit($('.toast'), [{ opacity: 1, transform: 'translateY(0) scale(1)' }, { opacity: 0, transform: 'translateY(14px) scale(0.97)' }], { duration: 240, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' });
        mutate();
      },
    },
    tokens: [
      { name: '--motion-fast', value: '150ms', use: '悬停、开关、小过渡(引用 250 次)' },
      { name: '--motion-medium', value: '240ms', use: '浮层、卡片(50 次)' },
      { name: '--motion-slow', value: '300ms', use: '面板、模态(13 次)' },
      { name: '--motion-ease', value: 'cubic-bezier(0.22, 1, 0.36, 1)', use: '几乎所有过渡(344 次)' },
      { name: '--motion-spring', value: 'cubic-bezier(0.34, 1.56, 0.64, 1)', use: '提示、模态入场(15 次)' },
      { name: '--motion-sheet', value: '420ms + linear() 弹簧', use: '手机壳的底页、推入页(具名例外)' },
    ],
    notes: {
      press: '悬停变底 150ms;按下没有反馈',
      ind: '选中态直接换;列表 130ms 淡入',
      pop: '入 150ms(上移 6px,0.97 → 1);出没有',
      open: '阅读窗淡入 + 上移 6px,180ms;键盘同样',
      nav: '直接换',
      fold: '列宽 240ms',
      qa: '入 300ms(右下角浮起,带过冲);出 200ms',
      brief: '直接出现',
      ai: '分数里程表 2200ms',
      toast: '入 300ms(回弹曲线);出 240ms',
      theme: '直接换',
      scroll: '进度线跟随;其余不动',
      event: '直接换',
      sub: '按钮直接换字',
    },
  });
})();
