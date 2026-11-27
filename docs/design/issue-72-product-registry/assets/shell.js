/* 载入现网外壳,给独立的方向原型当底页用(issue #72 样页)。
   做的事:取 ../app.html 里的现网 DOM(导轨 + 页头 + 三个页签的面板),只亮出「AI 日报」那一页,
   把页签改名「生成流程」(导轨短名「生成」;2026-11-27 定),把页面骨架换成「页头 + 一张铺到底的纸」(.runs-shell / .runs-work / .runs-paper,
   与「任务与运行」同一副),再把现网日报卡里可复用的真实节点与取值交给调用方。

   用法(在你自己的 gN.html 里):
     <script src="assets/shell.js"></script>
     <script>
       shellReady.then(({ paper, shell, parts, state, theme }) => {
         // paper:空的 .runs-paper(display:flex,铺满页头以下、overflow:hidden);往里放你的版面
         // shell:.runs-shell 整体;想自己重排页头以下的一切,可以直接改它
         // parts.rows:现网「近期日报」的 10 行真实节点(.brief-run);parts.pager:分页条;parts.scope:<details>取材与去重口径
         // parts.values:{ cron, topN, minScore, minItems, paperMin };parts.cursor:增量游标字符串
         // parts.playIcon / parts.spinIcon:图标的 HTML 字符串
         // state:'ok' | 'run' | 'bad' | 'new'(来自 ?state=);theme:'light' | 'dark'(来自 ?theme=)
       });
     </script>
   地址参数:?state=ok|run|bad  &theme=light|dark  &bare(嵌进总览页时用,你的样页开关条要在它存在时隐藏)。 */
(() => {
  'use strict';
  const base = new URL('../app.html', document.currentScript.src);
  const q = new URLSearchParams(location.search);
  const root = document.documentElement;
  const theme = q.get('theme') === 'dark' ? 'dark' : 'light';
  const state = ['ok', 'run', 'bad', 'new'].includes(q.get('state')) ? q.get('state') : 'ok';
  root.lang = 'zh-CN';
  root.dataset.theme = theme;
  root.dataset.palette = 'porcelain';
  if (q.has('bare')) root.dataset.bare = '';
  // 页面标题与导轨短名:定的是「生成流程」/「生成」。可以用地址参数换(细节样页 details/title.html 比较叫法、r3/ 存档按当时的「AI 产出」显示时用)
  const pageTitle = q.get('title') || '生成流程';
  const railName = q.get('rail') || '生成';
  // 导轨上的两字短名平时不显示,鼠标移上导轨才出来;带 railhover 时让它一直显示,方便在细节样页里看
  if (q.has('railhover')) {
    const style = document.createElement('style');
    // 与现网 .app-rail:hover 的两条规则同值:短名显出来,图标上移 7px
    style.textContent = '.app-rail .reader-vrail-label { opacity: 1; transform: none; } .app-rail .reader-vrail-btn > svg { transform: translateY(-7px); }';
    document.head.append(style);
  }

  window.shellReady = (async () => {
    const text = await (await fetch(base)).text();
    const doc = new DOMParser().parseFromString(text, 'text/html');
    const app = document.importNode(doc.getElementById('root'), true);
    document.body.prepend(app);

    // 只亮出「AI 日报」一页;导轨上它是当前页,改名
    app.querySelectorAll('.tab-panel').forEach((el) => el.classList.toggle('is-off', el.dataset.tab !== 'brief'));
    app.querySelectorAll('.app-rail nav button').forEach((btn) => {
      const on = btn.getAttribute('aria-label') === 'AI 日报';
      btn.classList.toggle('is-on', on);
      if (on) {
        btn.setAttribute('aria-current', 'page');
        btn.setAttribute('aria-label', pageTitle);
        btn.querySelector('.reader-vrail-label').textContent = railName;
        btn.querySelector('.reader-vrail-tip').textContent = pageTitle;
      } else {
        btn.removeAttribute('aria-current');
      }
    });

    // 现网日报卡里可复用的东西
    const panel = app.querySelector('.tab-panel[data-tab="brief"]');
    const old = panel.querySelector('.integ-page');
    const card = old.querySelector('section.brief-card');
    const [left, right] = card.querySelectorAll(':scope > .brief-col');
    const v = Array.from(left.querySelectorAll('.brief-field input')).map((el) => el.value);
    const parts = {
      values: { cron: v[0], topN: v[1], minScore: v[2], minItems: v[3], paperMin: v[4] },
      cursor: left.querySelector('.brief-cursor-val').textContent,
      scope: right.querySelector('details.scope-note'),
      rows: Array.from(right.querySelectorAll('.brief-run')),
      pager: right.querySelector('.brief-pager-row'),
      playIcon: right.querySelector('.brief-col-head .action-button svg').outerHTML,
      spinIcon: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide h-4 w-4 animate-spin" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>',
    };

    // 骨架:页头 + 一张铺到底的纸
    const pageHead = old.querySelector('.page-head');
    pageHead.querySelector('.page-title').textContent = pageTitle;
    const shell = document.createElement('div');
    shell.className = 'runs-shell';
    shell.innerHTML = '<div class="runs-work"><div class="runs-paper"></div></div>';
    shell.prepend(pageHead);
    old.replaceWith(shell);
    return { paper: shell.querySelector('.runs-paper'), shell, panel, parts, state, theme };
  })();
})();
