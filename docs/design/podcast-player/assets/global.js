/* 全局播放条样页(issue #199 · 方案 C「随行」升级,第二轮:条位 × 配色)。
   一个页面两种身份:不带 device 参数 = 对照外壳(主题 / 动效 / 形变路线 / 条位 / 配色 / 容器 / 宽度切换);
   带 ?device=desktop|mobile = 阅读器画框本体。

   结构要点:
   · 播放器是全局的一层(.ppg-player),挂在阅读器根上、在所有滚动容器之外——换文章 / 切容器 / push 正文页都不卸载它,
     播放与进度跨页延续。现网实现时它对应 App 层的一个 Provider + 唯一的 <audio>(见报告)。
   · 三种形态:none(未播放,只有标题区入口)→ bar(底部条)⇄ mini(右下角小胶囊);关闭回到 none。
   · 条位(?layout=):pane 阅读窗浮条(第一轮,默认)| dock 内容区贴底(视图轨右缘到窗口右缘,占位、顶一条细线)
     | float 内容区浮层(同宽,留边距的浮层卡)| full 全宽贴底(含视图轨)。宽条用三区布局。
     贴底占位的「让出 / 收回空间」不做逐帧改布局:形变期间条以覆盖层出现,落地那一帧才把网格底行撑开(此刻它正好被条盖住);
     收回时先把底行收掉(内容此刻在条下面),再让条滑走——只有滚到底的栏会因为可视区变高被浏览器往下推,用 FLIP 把这一推变成跟手的下滑。
   · 配色(?tone=):neutral 中性(默认)| cover 封面取色 | blur 封面模糊 | inverse 反色 | brand 品牌淡底。
     动态颜色只有一个运行时变量 --ppg-cover(JS 从封面取主色、按主题夹住明度彩度后写入),底色由 CSS 用 color-mix 与表面 token 混出;
     文字 / 图标永远是 token 派生色,对比度由 PPG.audit() 逐封面核算。
   · 形变(?morph=waapi 默认 | vt 对照):WAAPI 版把被点的胶囊拆成两层——底板从胶囊长成底部条
     (translate + clip-path inset(round),全程可合成、圆角不变形),胶囊的 accent 内核收拢成条上的播放钮,
     ▶ 作为共享元素飞过去变成 ⏸;真实条的内容裁在底板当前矩形里错峰淡入(宽条的两端不会先于底板出现)。
   · 正文与音轨解耦:有导读稿的节目,正文区自带「节目简介 | 导读稿」视图切换,与正在播放哪条音轨无关。 */
(function () {
  'use strict';
  const q = new URLSearchParams(location.search);
  const DEVICE = q.get('device');
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const latin = (s) => /[A-Za-z]/.test(s) && !/[一-鿿]/.test(String(s).slice(0, 24));
  const pick = (v, list, dflt) => (list.includes(v) ? v : dflt);
  const LAYOUTS = ['pane', 'dock', 'float', 'full'];
  const TONES = ['neutral', 'cover', 'blur', 'inverse', 'brand'];
  const CONTAINERS = ['podcast', 'article', 'social', 'discover'];

  const svg = (inner, cls = '') => `<svg ${cls ? `class="${cls}"` : ''} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  const I = Object.assign({}, PP.icons, {
    checkCheck: svg('<path d="M18 6 7 17l-5-5"/><path d="m22 10-7.5 7.5L13 16"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>'),
    plus: svg('<path d="M5 12h14"/><path d="M12 5v14"/>'),
    repeat: svg('<path d="m2 9 3-3 3 3"/><path d="M13 18H7a2 2 0 0 1-2-2V6"/><path d="m22 15-3 3-3-3"/><path d="M11 6h6a2 2 0 0 1 2 2v10"/>'),
    search: svg('<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'),
    menu: svg('<line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="18" y2="18"/>'),
    fileText: svg('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'),
    podcast: svg('<path d="M16.85 18.58a9 9 0 1 0-9.7 0"/><path d="M8 14a5 5 0 1 1 8 0"/><circle cx="12" cy="11" r="1"/><path d="M13 17a1 1 0 1 0-2 0l.5 4.5a.5.5 0 1 0 1 0Z"/>'),
    atSign: svg('<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>'),
    barChart: svg('<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>'),
    user: svg('<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>'),
    compass: svg('<path d="m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z"/><circle cx="12" cy="12" r="10"/>'),
    newspaper: svg('<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>'),
    sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>'),
    moon: svg('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>'),
    settings: svg('<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'),
  });

  /* ── 弹簧缓动:阻尼振子阶跃响应采样成 CSS linear()(WAAPI 的 easing 也认) ──
     zeta 阻尼比,response 是「响应时间」(无阻尼周期,秒),dur 是动画时长(毫秒)。末点钉到 1。 */
  // linear() 需要 Safari 17.2+ / Chrome 113+;WAAPI 遇到不认识的 easing 会直接抛错,旧浏览器退回近似的三次贝塞尔
  const LINEAR_OK = typeof CSS !== 'undefined' && CSS.supports?.('animation-timing-function', 'linear(0, 0.5, 1)');
  // kick:初速度(以 ω0 为单位,0 = 从静止起步)。两段式形变的第二段用它「一落地就铺开」,不在拐角处停顿
  function spring({ zeta, response, dur, samples = 56, kick = 0 }) {
    if (!LINEAR_OK) return 'cubic-bezier(.2, .85, .25, 1)';
    const w0 = (2 * Math.PI) / response;
    const v0 = kick * w0;
    const pts = [];
    for (let i = 0; i <= samples; i += 1) {
      const t = (i / samples) * (dur / 1000);
      let x;
      if (zeta >= 1) {
        // 临界阻尼:没有过冲(贴底条的边就是外框的边,不能越过视图轨 / 窗口边再弹回来)
        x = 1 - (1 + (w0 - v0) * t) * Math.exp(-w0 * t);
      } else {
        const wd = w0 * Math.sqrt(1 - zeta * zeta);
        x = 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0 - v0) / wd) * Math.sin(wd * t));
      }
      pts.push(i === samples ? 1 : Math.round(x * 10000) / 10000);
    }
    return `linear(${pts.join(', ')})`;
  }
  const MOTION = {
    morphDur: 560,
    morph: spring({ zeta: 0.84, response: 0.46, dur: 560 }),      // 形变:几乎不回弹(最大过冲 ≈0.6%)
    settleDur: 420,
    settle: spring({ zeta: 0.9, response: 0.36, dur: 420 }),      // 小位移(浮钮避让、推页、条位切换)
    out: 'cubic-bezier(.4, 0, 1, 1)',
    fade: 'cubic-bezier(.2, 0, 0, 1)',
  };

  /* ── 数据:global-data 的真实条目 + 样页补充的点播 / 导读能力位(示意,标在报告里) ── */
  const EXTRA = {
    podcast_interconnects_2201ca715e09ca60: {
      guideAudio: PP.episode.guideDurationSeconds, guideText: PP.episode.guide, ondemand: 'done',
      summary: PP.episode.summary, basis: PP.episode.basisNote, score: PP.episode.score, tier: PP.episode.scoreTier, tags: PP.episode.tags,
      showNotes: PP.episode.showNotes,
    },
    podcast_cognitive_revolution_22cb16d55f19e8ab: { ondemand: 'available' },
    podcast_42chapter_acce1dfad36f4e4f: { ondemand: 'off' },
    podcast_asi_pill_3a71b5400ab8b2a2: { ondemand: 'generating' },
    podcast_talk_python_d9f67c9c130ecd5c: {
      ondemand: 'audio',
      guideText: [
        ['h3', '重新选一遍 Python 工具链'],
        ['p', '主持人请四位嘉宾回答同一个问题：如果今天从零开始，你还会选现在手里的这些 Python 工具吗？过去答案很稳定，AI 让几乎每一项选择都重新打开了。'],
        ['h3', '嘉宾'],
        ['p', 'Six Feet Up 的 CTO Calvin Hendryx-Parker、Hibu 的 DevOps 工程师 Pat Decker、培训 Python 开发者 15 年的 Reuven Lerner，以及 LangChain 负责开源产品的 Sydney Runkle。其中一位已经几乎不打开编辑器。'],
      ],
    },
    podcast_latent_space_5f41d4c676275069: { ondemand: 'available' },
    web_ithome_ai_bffbb55b2c73f8de: { narration: 188 },
    web_ithome_ai_6590b727f04a877b: { narrationOndemand: 'available' },
    web_ithome_ai_c8a6288ba96c5d92: {},
  };
  const ENTRIES = PP.entries.map((e) => ({ ...e, ...(EXTRA[e.id] || {}) }));
  const byId = (id) => ENTRIES.find((e) => e.id === id);
  const listOf = (container) => ENTRIES.filter((e) => (container === 'podcast' ? e.kind === 'podcast' : e.kind === 'article'));
  const SOCIAL = PP.social || { accounts: [], posts: [] };
  const DISCOVER = PP.discover || [];

  const fmt = {
    clock(sec) {
      const s = Math.max(0, Math.round(sec));
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const ss = String(s % 60).padStart(2, '0');
      return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${String(m).padStart(2, '0')}:${ss}`;
    },
    minutes(sec) {
      const m = Math.max(1, Math.round(sec / 60));
      return m >= 60 ? `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分钟` : ''}` : `${m} 分钟`;
    },
    date(v) { return v.includes('T') ? new Date(v) : new Date(v.replace(' ', 'T')); },
    rel(v) {
      // 样页「今天」= 2026-10-19;7 天内写相对时间,更早写日期(conventions §1)
      const d = fmt.date(v);
      const days = Math.round((new Date('2026-10-19T12:00') - d) / 86400000);
      if (days <= 0) return '今天';
      if (days <= 7) return `${days} 天前`;
      return `${d.getMonth() + 1}月${d.getDate()}日`;
    },
    day(v) {
      const d = fmt.date(v);
      return `${d.getMonth() + 1}月${d.getDate()}日 周${'日一二三四五六'[d.getDay()]}`;
    },
  };

  // 正文:节目简介 / 文章摘录(数据是截断的开头,末尾补省略号);去掉外链图片与 markdown 链接语法
  function paragraphs(text) {
    const clean = String(text || '')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();
    const sentences = clean.split(/(?<=[。！？.!?])\s+/);
    const paras = [];
    let cur = '';
    for (const s of sentences) {
      cur = cur ? `${cur} ${s}` : s;
      if (cur.length > 150) { paras.push(cur); cur = ''; }
    }
    if (cur) paras.push(cur);
    return paras.map((p, i) => {
      const html = esc(i === paras.length - 1 ? `${p}…` : p).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      return `<p>${html}</p>`;
    }).join('');
  }
  const readMinutes = (e) => Math.max(1, Math.round(String(e.excerpt || '').length / (e.kind === 'podcast' && latin(e.excerpt) ? 900 : 420) * 6));

  /* ════════════════ 颜色:sRGB ⇄ OKLCH、WCAG 对比度(取色与审计共用) ════════════════ */
  const C = {
    lin(c) { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; },
    oklch(r, g, b) {
      const R = C.lin(r); const G = C.lin(g); const B = C.lin(b);
      const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
      const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
      const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
      const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
      const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
      const Bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
      let H = (Math.atan2(Bb, A) * 180) / Math.PI;
      if (H < 0) H += 360;
      return { L, C: Math.hypot(A, Bb), H };
    },
    lum([r, g, b]) { return 0.2126 * C.lin(r) + 0.7152 * C.lin(g) + 0.0722 * C.lin(b); },
    contrast(a, b) { const x = C.lum(a); const y = C.lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); },
  };
  // 任意 CSS 颜色串(含 color-mix 解析后的 oklch / color(srgb) 等)→ 叠在 under 上的 sRGB:交给 canvas 换算,不自己解析
  const probe = (() => {
    let ctx = null;
    return (color, under = [255, 255, 255]) => {
      if (!ctx) {
        const cv = document.createElement('canvas'); cv.width = 1; cv.height = 1;
        ctx = cv.getContext('2d', { willReadFrequently: true });
      }
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = `rgb(${under[0]}, ${under[1]}, ${under[2]})`;
      ctx.fillRect(0, 0, 1, 1);
      ctx.fillStyle = '#010203';
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2]];
    };
  })();
  // 颜色表达式(color-mix 嵌套等,已代换掉 var())→ 计算色:交给一个隐藏元素解析,再交给 probe 换成 sRGB。
  // canvas 认不出的写法会留在哨兵色 #010203 上,调用方据此退回默认值
  function resolveColor(str) {
    let s = resolveColor.el;
    if (!s || !s.isConnected) {
      s = document.createElement('span');
      s.hidden = true;
      document.body.append(s);
      resolveColor.el = s;
    }
    s.style.color = '';
    s.style.color = String(str || '').trim();
    return getComputedStyle(s).color;
  }
  const SENTINEL = '1,2,3';

  /* ════════════════ 对照外壳 ════════════════ */
  if (!DEVICE) { host(); return; }

  /* ════════════════ 阅读器画框 ════════════════ */
  const root = document.documentElement;
  root.dataset.theme = q.get('theme') === 'light' ? 'light' : 'dark';
  if (root.dataset.theme === 'light') delete root.dataset.theme;
  if (q.get('motion') === 'reduce') root.dataset.motion = 'reduce';
  root.dataset.device = DEVICE;
  // 弹簧曲线交给 CSS(VT 伪元素、推页与条位过渡也用同一条)
  root.style.setProperty('--ppg-morph-ease', MOTION.morph);
  root.style.setProperty('--ppg-settle', MOTION.settle);
  root.style.setProperty('--ppg-settle-dur', `${MOTION.settleDur}ms`);
  const MORPH = q.get('morph') === 'vt' ? 'vt' : 'waapi';
  const motionOff = () => root.dataset.motion === 'reduce';
  // 输入方式:只在键盘触发时程序化移焦点(焦点随形变进条 / 迷你 / 新页面)。指针触发不移——
  // Safari 点按钮本就不给焦点,程序化移焦会被它当成「该显示焦点环」,鼠标 / 触屏用户会看到一圈多余的环
  let lastInput = 'pointer';
  window.addEventListener('keydown', (e) => { if (!e.metaKey && !e.ctrlKey) lastInput = 'key'; }, true);
  window.addEventListener('pointerdown', () => { lastInput = 'pointer'; }, true);
  const kbd = () => lastInput === 'key';

  const S = {
    device: DEVICE,
    container: pick(q.get('container'), CONTAINERS, 'podcast'),
    layout: pick(q.get('layout'), LAYOUTS, 'pane'),
    tone: pick(q.get('tone'), TONES, 'neutral'),
    activeId: 'podcast_interconnects_2201ca715e09ca60',
    page: DEVICE === 'mobile' ? 'list' : 'read',
    readView: 'guide',          // 有导读稿时默认看导读稿;读者切换后在本次会话里记住(与播放哪条音轨无关)
    ondemand: {},               // 画框内点播后的运行时状态
    readingContainer: 'podcast',// 发现页沿用上一个阅读容器的源栏
  };
  if (S.container === 'article') S.activeId = 'web_ithome_ai_bffbb55b2c73f8de';
  if (S.container === 'podcast' || S.container === 'article') S.readingContainer = S.container;
  const P = { item: null, t: 0, playing: false, rate: 0, form: 'none', pos: new Map(), raf: 0, last: 0 };
  const RATES = [1, 1.25, 1.5, 2];
  let fxRunning = [];           // 当前形变的动画(测试钩子用)
  const isDesktop = () => S.device === 'desktop';
  const docks = () => isDesktop() && (S.layout === 'dock' || S.layout === 'full');   // 贴底占位的两种条位
  const $app = () => document.querySelector('.ppg-app');
  const $p = () => document.querySelector('.ppg-player');

  /* ════════════════ 配色引擎 ════════════════
     每张封面算一次(启动时预热,按 URL 缓存):
       tone  = 主色 → oklch(0.62 C H),C 夹到 ≤0.14;近灰(C < 0.04)记为中性——写进 --ppg-cover,底色由 CSS 与表面 token 混出
       blur  = 16×16 缩略后放大到 64×64 再做三遍盒式模糊的静态位图(data URL)——不用 backdrop-filter,也不用运行时 filter
     没有封面的 AI 音轨(文章导读):不取色、不铺 AI 渐变(conventions:渐变只上标记不铺底),回到中性底,AI 身份由 ✦ 与「AI 生成」承担 */
  const TONE_CACHE = new Map();
  function analyzeCover(url) {
    if (!url) return Promise.resolve(null);
    if (TONE_CACHE.has(url)) return TONE_CACHE.get(url);
    const job = new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        try {
          const t0 = performance.now();
          const N = 24;
          const cv = document.createElement('canvas'); cv.width = N; cv.height = N;
          const cx = cv.getContext('2d', { willReadFrequently: true });
          cx.drawImage(img, 0, 0, N, N);
          const px = cx.getImageData(0, 0, N, N).data;
          // 色相直方图(24 格):权重 = 彩度 × 偏向中等明度;取最重的一格及左右邻格做加权圆平均
          const bins = new Array(24).fill(0).map(() => ({ w: 0, x: 0, y: 0, c: 0 }));
          let total = 0;
          for (let i = 0; i < px.length; i += 4) {
            const o = C.oklch(px[i], px[i + 1], px[i + 2]);
            const w = o.C * Math.max(0, 1 - Math.abs(o.L - 0.62) / 0.5);
            if (w <= 0) continue;
            const b = bins[Math.floor(o.H / 15) % 24];
            b.w += w; b.x += w * Math.cos((o.H * Math.PI) / 180); b.y += w * Math.sin((o.H * Math.PI) / 180); b.c += w * o.C;
            total += w;
          }
          let best = 0;
          bins.forEach((b, i) => { const s = b.w + bins[(i + 23) % 24].w * 0.5 + bins[(i + 1) % 24].w * 0.5; if (s > (bins[best].w + bins[(best + 23) % 24].w * 0.5 + bins[(best + 1) % 24].w * 0.5)) best = i; });
          const near = [bins[(best + 23) % 24], bins[best], bins[(best + 1) % 24]];
          const W = near.reduce((s, b) => s + b.w, 0);
          const hue = ((Math.atan2(near.reduce((s, b) => s + b.y, 0), near.reduce((s, b) => s + b.x, 0)) * 180) / Math.PI + 360) % 360;
          const chroma = W ? near.reduce((s, b) => s + b.c, 0) / W : 0;
          const neutral = !total || chroma < 0.04;
          const tone = neutral ? null : { L: 0.62, C: Math.min(0.14, chroma * 1.1), H: hue };
          // 静态模糊位图
          const s16 = document.createElement('canvas'); s16.width = 16; s16.height = 16;
          s16.getContext('2d').drawImage(img, 0, 0, 16, 16);
          const B = 64;
          const big = document.createElement('canvas'); big.width = B; big.height = B;
          const bc = big.getContext('2d', { willReadFrequently: true });
          bc.imageSmoothingEnabled = true; bc.imageSmoothingQuality = 'high';
          bc.drawImage(s16, 0, 0, B, B);
          const im = bc.getImageData(0, 0, B, B);
          const d = im.data;
          const tmp = new Uint8ClampedArray(d.length);
          const R = 4;
          for (let pass = 0; pass < 3; pass += 1) {
            for (let y = 0; y < B; y += 1) for (let x = 0; x < B; x += 1) {
              for (let k = 0; k < 3; k += 1) {
                let sum = 0; let n = 0;
                for (let dx = -R; dx <= R; dx += 1) { const xx = Math.min(B - 1, Math.max(0, x + dx)); sum += d[(y * B + xx) * 4 + k]; n += 1; }
                tmp[(y * B + x) * 4 + k] = sum / n;
              }
              tmp[(y * B + x) * 4 + 3] = 255;
            }
            for (let y = 0; y < B; y += 1) for (let x = 0; x < B; x += 1) {
              for (let k = 0; k < 3; k += 1) {
                let sum = 0; let n = 0;
                for (let dy = -R; dy <= R; dy += 1) { const yy = Math.min(B - 1, Math.max(0, y + dy)); sum += tmp[(yy * B + x) * 4 + k]; n += 1; }
                d[(y * B + x) * 4 + k] = sum / n;
              }
            }
          }
          const tTone = performance.now();
          bc.putImageData(im, 0, 0);
          const blur = big.toDataURL('image/png');
          // 计时(测试钩子读):取色 / 取色 + 模糊位图;位图字节数
          resolve({ tone, blur, pixels: d, ms: { all: +(performance.now() - t0).toFixed(2), untilBlurEncode: +(tTone - t0).toFixed(2) }, blurBytes: blur.length });
        } catch {
          resolve(null);
        }
      };
      img.onerror = () => resolve(null);
      img.src = url;
    });
    TONE_CACHE.set(url, job);
    return job;
  }
  const toneSync = new Map();   // url → 已算好的结果(同步取用)
  function prepareTones() {
    ENTRIES.filter((e) => e.cover).forEach((e) => analyzeCover(e.cover).then((r) => { if (r) toneSync.set(e.cover, r); }));
  }
  /* 封面模糊的遮罩浓度:模糊位图里最差的那个像素叠上遮罩后,副行文字 / 图标 / 「AI 生成」也要 ≥ 4.6(留 0.1 余量)。
     多数封面停在基础浓度(亮 70% / 暗 64%),明亮或高饱和的封面自动加浓;写进 --ppg-veil-p,CSS 用它混出遮罩色。
     浏览器在 sRGB 编码空间里做叠加:结果 = 遮罩 × a + 像素 × (1 − a),这里按同一口径算 */
  function veilFor(r) {
    const pl = $p();
    const dark = root.dataset.theme === 'dark';
    const lo = dark ? 0.64 : 0.70;
    if (!r || !r.pixels) return lo;
    const cs = getComputedStyle(pl);
    const base = probe(resolveColor(cs.getPropertyValue('--ppg-base')));
    const inks = ['--ppg-fg-2', '--ppg-ai-ink'].map((v) => probe(resolveColor(cs.getPropertyValue(v))));
    if ([base, ...inks].some((c) => c.join() === SENTINEL)) return lo;
    const px = r.pixels;
    const ok = (a) => {
      for (let i = 0; i < px.length; i += 4) {
        const bg = [0, 1, 2].map((k) => base[k] * a + px[i + k] * (1 - a));
        for (const ink of inks) if (C.contrast(ink, bg) < 4.6) return false;
      }
      return true;
    };
    if (ok(lo)) return lo;
    let a = lo; let b = 0.96;
    for (let n = 0; n < 7; n += 1) { const m = (a + b) / 2; if (ok(m)) b = m; else a = m; }
    return Math.ceil(b * 100) / 100;
  }
  // 把条目的封面色写进播放器(--ppg-cover / --ppg-blur / --ppg-veil-p);fade=true 时旧底色冻结成一层再淡出(只动 opacity)
  function applyTone(item, { fade = false } = {}) {
    const pl = $p();
    if (!pl) return;
    const set = () => {
      const r = item && item.cover ? toneSync.get(item.cover) : null;
      if (r && r.tone) pl.style.setProperty('--ppg-cover', `oklch(${r.tone.L} ${r.tone.C.toFixed(3)} ${r.tone.H.toFixed(1)})`);
      else pl.style.removeProperty('--ppg-cover');
      if (r && r.blur) pl.style.setProperty('--ppg-blur', `url("${r.blur}")`);
      else pl.style.removeProperty('--ppg-blur');
      if (S.tone === 'blur' && r && r.blur) pl.style.setProperty('--ppg-veil-p', `${Math.round(veilFor(r) * 100)}%`);
      else pl.style.removeProperty('--ppg-veil-p');
      pl.dataset.toneSrc = r ? (r.tone ? 'cover' : 'neutral-cover') : (item && item.ai ? 'ai' : 'none');
    };
    const itemTone = S.tone === 'cover' || S.tone === 'blur';
    if (fade && itemTone && !motionOff()) {
      ['.ppg-bar', '.ppg-mini'].forEach((sel) => {
        const host = pl.querySelector(sel);
        if (!host || host.hidden) return;
        const cs = getComputedStyle(host);
        // 封面模糊的位图画在条的 ::before 那一层上(见 CSS),冻结旧底色时要连它一起抄
        const pre = getComputedStyle(host, '::before');
        const old = document.createElement('span');
        old.className = 'ppg-tone-old';
        old.style.backgroundColor = cs.backgroundColor;
        old.style.backgroundImage = pre.content !== 'none' && pre.backgroundImage !== 'none' ? pre.backgroundImage : cs.backgroundImage;
        host.prepend(old);
        const a = old.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 420, easing: MOTION.fade, fill: 'forwards' });
        a.finished.then(() => old.remove()).catch(() => old.remove());
      });
    }
    set();
    if (item && item.cover && !toneSync.has(item.cover)) {
      // 封面还没算完:算完补一次(同样带淡入)
      analyzeCover(item.cover).then((r) => { if (r) toneSync.set(item.cover, r); if (P.item && P.item.cover === item.cover) applyTone(item, { fade: true }); });
    }
  }

  /* ── 条目行 ── */
  function eqHtml(ai, live) {
    return `<span class="ppg-eq${ai ? ' is-ai' : ''}${live ? ' is-live' : ''}" aria-hidden="true"><i></i><i></i><i></i></span>`;
  }
  const sparkHtml = (extra = '') => `<span class="ppg-spark${extra}" aria-hidden="true"></span>`;
  function rowHtml(e) {
    const active = e.id === S.activeId && (isDesktop() || S.page === 'read');
    const live = P.item && P.item.entryId === e.id;
    if (e.kind === 'podcast') {
      return `<button type="button" class="reader-entry is-podcast ${active ? 'is-active' : ''} ${live ? 'ppg-row-live' : ''}" data-act="open" data-id="${e.id}">
        <span class="reader-podcast-layout">
          <img class="reader-podcast-cover" src="${e.cover}" alt="">
          <span class="reader-podcast-copy">
            <span class="reader-entry-top"><span class="reader-entry-src">${esc(e.source)}</span><span class="reader-entry-time">${fmt.rel(e.date)}</span></span>
            <span class="reader-entry-titlerow"><span class="reader-entry-title" ${latin(e.title) ? 'lang="en"' : ''}>${esc(e.title)}</span></span>
            <span class="reader-podcast-meta">${live ? `<span class="ppg-row-now">${eqHtml(P.item.ai, P.playing)}<span>${P.playing ? '播放中' : '已暂停'}</span></span>` : ''}<span>${fmt.minutes(e.durationSeconds)}</span></span>
          </span>
        </span>
      </button>`;
    }
    return `<button type="button" class="reader-entry ${active ? 'is-active' : ''} ${live ? 'ppg-row-live' : ''}" data-act="open" data-id="${e.id}">
      <span class="reader-entry-top"><span class="ppg-logo" aria-hidden="true">IT</span><span class="reader-entry-src">${esc(e.source)}</span><span class="reader-entry-time">${fmt.rel(e.date)}</span>${live ? `<span class="ppg-row-now">${eqHtml(true, P.playing)}<span>${P.playing ? '播放中' : '已暂停'}</span></span>` : ''}</span>
      <span class="reader-entry-titlerow"><span class="reader-entry-title">${esc(e.title)}</span></span>
      <span class="reader-entry-excerpt">${esc(paragraphs(e.excerpt).replace(/<[^>]+>/g, ' ').slice(0, 120))}</span>
    </button>`;
  }
  function listHtml() {
    const rows = listOf(S.readingContainer);
    let out = '';
    let lastDay = '';
    rows.forEach((e) => {
      const day = fmt.day(e.date);
      if (day !== lastDay) { out += `<div class="reader-date-label">${day}</div>`; lastDay = day; }
      out += rowHtml(e);
    });
    return out;
  }

  /* ── 社交流(真实推文;卡片结构与现网 SocialFlow 同构,样式按现网值复写为 ppg-soc-*) ── */
  function avatarHtml(src, name, cls = 'ppg-soc-ava') {
    return src
      ? `<img class="${cls}" src="${src}" alt="">`
      : `<span class="${cls} ppg-soc-initial" aria-hidden="true">${esc((name || '?').slice(0, 1).toUpperCase())}</span>`;
  }
  function postHtml(p) {
    const long = p.text.length > 360;
    const media = p.media.length
      ? `<div class="ppg-soc-media n${Math.min(4, p.media.length)}">${p.media.map((m) => `<span class="ppg-soc-media-item"><img src="${m}" alt=""></span>`).join('')}</div>`
      : '';
    const quoted = p.quoted
      ? `<a class="ppg-soc-quote" href="${esc(p.quoted.url)}" target="_blank" rel="noreferrer noopener" onclick="return false"><span class="ppg-soc-quote-head"><span class="ppg-soc-quote-name">${esc(p.quoted.author_name)}</span><span class="ppg-soc-handle">@${esc(p.quoted.handle)}</span></span><span class="ppg-soc-quote-text">${esc(p.quoted.text)}</span></a>`
      : '';
    return `<article class="ppg-soc-post">
      ${p.reposter ? `<p class="ppg-soc-repost">${I.repeat}${esc(p.reposter)} 转推</p>` : ''}
      <div class="ppg-soc-body">
        ${avatarHtml(p.avatar, p.author_name)}
        <div class="ppg-soc-main">
          <div class="ppg-soc-head"><span class="ppg-soc-name">${esc(p.author_name)}</span><span class="ppg-soc-handle">@${esc(p.handle)}</span><span class="ppg-soc-handle">·</span><a class="ppg-soc-time" href="${esc(p.url)}" onclick="return false">${fmt.rel(p.date)}</a></div>
          <p class="ppg-soc-text${long ? ' is-clamped' : ''}" lang="en">${esc(p.text)}</p>
          ${long ? '<button type="button" class="ppg-soc-more" data-act="soc-more">显示更多</button>' : ''}
          ${media}${quoted}
        </div>
      </div>
    </article>`;
  }
  function socialHtml() {
    let out = '';
    let lastDay = '';
    SOCIAL.posts.forEach((p) => {
      const day = fmt.day(p.date);
      if (day !== lastDay) { out += `<div class="reader-social-day">${day}</div>`; lastDay = day; }
      out += postHtml(p);
    });
    return `${out}<p class="reader-social-end">已到底部</p>`;
  }

  /* ── 发现页(真实来源目录:名称 / 简介 / 订阅人数 / 订阅态) ── */
  function discoverHtml() {
    const groups = [['podcast', '播客'], ['social', '社交'], ['article', '文章']];
    return groups.map(([shape, label]) => {
      const list = DISCOVER.filter((d) => d.shape === shape);
      if (!list.length) return '';
      return `<div class="reader-disc-grp"><span class="reader-disc-grp-label">${label}</span><span class="reader-disc-grp-n">${list.length}</span></div>
        <div class="reader-disc-grid">${list.map((d) => `<div class="reader-disc-card">
          <div class="reader-disc-card-main">
            ${d.cover ? `<img class="reader-disc-avatar${shape === 'podcast' ? ' ppg-disc-cover' : ''}" src="${d.cover}" alt="">`
              : shape === 'social' ? avatarHtml('', (SOCIAL.accounts.find((a) => a.source_id === d.source_id) || {}).author_name || d.name, 'reader-disc-avatar')   // 头像失效:与源栏同一套首字母兜底
                : '<span class="ppg-logo is-disc" aria-hidden="true">IT</span>'}
            <div class="reader-disc-card-mid">
              <div class="reader-disc-name-row"><button type="button" class="reader-disc-name reader-disc-open">${esc(d.name)}</button>${shape === 'podcast' ? '<span class="reader-shape-chip">播客</span>' : ''}</div>
              <p class="reader-disc-desc">${esc(d.description)}</p>
              <p class="reader-disc-meta">${d.subscribers > 0 ? `${d.subscribers} 人订阅` : '暂无订阅'}</p>
            </div>
            <div class="reader-disc-card-side"><button type="button" class="reader-disc-sub ${d.subscribed ? 'is-subbed' : ''}">${d.subscribed ? `<span class="t-on">${I.check}已订阅</span><span class="t-off">取消订阅</span>` : `${I.plus}订阅`}</button></div>
          </div>
        </div>`).join('')}</div>`;
    }).join('');
  }

  /* ── 标题区收听入口(方案 C):原节目实心主钮;精品导读 / 听导读带 ✦ ── */
  function entryState(e, track) {
    const key = `${e.id}:${track}`;
    const cur = P.item && P.item.key === key && P.form !== 'none';
    return { cur, playing: cur && P.playing };
  }
  function listenHtml(e) {
    const out = [];
    const ondemand = S.ondemand[e.id] || e.ondemand;
    if (e.kind === 'podcast') {
      const o = entryState(e, 'original');
      // 主钮:这一期没在播时是实心;在播时退成「选中」并显示播放态
      out.push(`<button type="button" class="ppg-entry ${o.cur ? 'is-current' : 'is-primary'}" data-act="listen" data-id="${e.id}" data-track="original" aria-label="收听原节目，${fmt.minutes(e.durationSeconds)}" aria-pressed="${o.playing}">${o.cur ? eqHtml(false, o.playing) : `<span class="ppg-e-ico">${I.play}</span>`}<span class="ppg-e-label">收听原节目</span><span class="ppg-len">${fmt.minutes(e.durationSeconds)}</span></button>`);
      if (e.guideAudio) {
        const g = entryState(e, 'digest');
        out.push(`<button type="button" class="ppg-entry ${g.cur ? 'is-current' : ''}" data-act="listen" data-id="${e.id}" data-track="digest" aria-label="收听精品导读，${fmt.minutes(e.guideAudio)}，AI 生成" aria-pressed="${g.playing}">${g.cur ? eqHtml(true, g.playing) : `<span class="ppg-e-ico">${sparkHtml()}</span>`}<span class="ppg-e-label">精品导读</span><span class="ppg-len">${fmt.minutes(e.guideAudio)}</span></button>`);
      } else if (ondemand === 'generating') {
        out.push(`<button type="button" class="ppg-entry is-busy" aria-disabled="true">${sparkHtml(' is-breathing')}<span role="status">${e.guideText ? '导读音频生成中…' : '导读生成中…'}</span></button>`);
      } else if (ondemand === 'available') {
        out.push(`<button type="button" class="ppg-entry" data-act="request" data-id="${e.id}">${sparkHtml()}<span>点播精品导读</span></button>`);
      } else if (ondemand === 'audio') {
        out.push(`<button type="button" class="ppg-entry" data-act="request" data-id="${e.id}">${sparkHtml()}<span>生成导读音频</span></button>`);
      }
    } else if (e.narration) {
      const n = entryState(e, 'narration');
      out.push(`<button type="button" class="ppg-entry ${n.cur ? 'is-current' : ''}" data-act="listen" data-id="${e.id}" data-track="narration" aria-label="听导读，${fmt.minutes(e.narration)}，AI 生成" aria-pressed="${n.playing}">${n.cur ? eqHtml(true, n.playing) : `<span class="ppg-e-ico">${sparkHtml()}</span>`}<span class="ppg-e-label">听导读</span><span class="ppg-len">${fmt.minutes(e.narration)}</span></button>`);
    } else if ((S.ondemand[e.id] || e.narrationOndemand) === 'available') {
      out.push(`<button type="button" class="ppg-entry" data-act="request" data-id="${e.id}">${sparkHtml()}<span>听导读</span></button>`);
    } else if ((S.ondemand[e.id] || e.narrationOndemand) === 'generating') {
      out.push(`<button type="button" class="ppg-entry is-busy" aria-disabled="true">${sparkHtml(' is-breathing')}<span role="status">导读生成中…</span></button>`);
    }
    return out.length ? `<div class="ppg-entries" role="group" aria-label="收听">${out.join('')}</div>` : '';
  }

  /* ── 阅读窗 ── */
  function headHtml(e) {
    const meta = [e.date, `${e.kind === 'podcast' ? '简介阅读约' : '阅读约'} ${readMinutes(e)} 分钟`];
    if (e.id.startsWith('podcast_interconnects')) meta.push('阅读量 1');
    const tags = (e.tags || []).map((t) => `<button type="button" class="reader-tag-chip is-actionable" aria-label="检索「${esc(t)}」">${esc(t)}</button>`).join('');
    const listen = listenHtml(e);
    const tools = `<div class="reader-tr-seg" role="group" aria-label="正文语言"><button type="button" class="reader-tr-seg-btn is-on" aria-pressed="true">原语言</button><button type="button" class="reader-tr-seg-btn" aria-pressed="false">${I.sparkles}<span>译为中文</span></button></div><a class="reader-pane-act" href="#" onclick="return false">${I.external}查看原文</a>`;
    const desktop = isDesktop();
    return `<header class="reader-pane-head">
      <div class="reader-kicker">${esc(e.source)}</div>
      <h1 class="reader-pane-title" ${latin(e.title) ? 'lang="en"' : ''}>${esc(e.title)}</h1>
      <div class="reader-pane-byline"><div class="reader-pane-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join('')}</div></div>
      ${!desktop && listen ? `<div class="ppg-head-listen">${listen}</div>` : ''}
      ${tags || desktop ? `<div class="reader-pane-foot">${tags ? `<div class="reader-pane-tags">${tags}</div>` : ''}${desktop ? `<div class="reader-pane-actions">${listen}${tools}</div>` : ''}</div>` : ''}
    </header>`;
  }
  function aiCardHtml(e) {
    if (!e.summary) return '';
    const DIG = '0123456789'.split('');
    const fig = e.score.split('').map((ch) => (ch === '.'
      ? `<span class="reader-ai-odo-dot ai-grad-text ${e.tier}">.</span>`
      : `<span class="reader-ai-odo-cell"><span class="reader-ai-odo-strip" style="transform:translateY(${-Number(ch) * 10}%)">${DIG.map((d) => `<span class="reader-ai-odo-digit ai-grad-text ${e.tier}">${d}</span>`).join('')}</span></span>`)).join('');
    return `<div class="reader-ai-summary"><div class="reader-ai-card-head"><span class="reader-ai-layer-title">简介导读</span><div class="reader-ai-summary-side"><button type="button" class="reader-ai-score-btn" aria-label="内容价值 ${e.score}"><span class="reader-ai-odo" aria-hidden="true">${fig}</span></button><span class="reader-ai-score-dim" aria-hidden="true">内容价值</span></div></div>
      <div class="reader-ai-summary-main"><div class="reader-ai-layer reader-ai-layer-summary"><p class="reader-ai-layer-text">${esc(e.summary)}</p><span class="reader-ai-layer-note">${esc(e.basis)}</span></div></div></div>`;
  }
  function bodyHtml(e) {
    const notes = e.showNotes ? e.showNotes.map((p) => `<p>${esc(p)}</p>`).join('') : paragraphs(e.excerpt);
    const notesLang = latin(e.excerpt) ? 'lang="en"' : '';
    if (e.kind === 'article') {
      return `<div class="reader-article-copy markdown-body">${notes}</div><p class="reader-pane-origin"><a href="#" onclick="return false">查看原文 ↗</a><span class="reader-pane-origin-host"> · ithome.com</span></p>`;
    }
    // 解耦后的正文:有导读稿时,正文区自带「节目简介 | 导读稿」视图切换,与播放哪条音轨无关
    const view = e.guideText ? S.readView : 'notes';
    const head = e.guideText
      ? `<div class="ppg-view-head"><div class="reader-tr-seg ppg-view-seg" role="tablist" aria-label="正文">
          <button type="button" role="tab" class="reader-tr-seg-btn ${view === 'notes' ? 'is-on' : ''}" aria-selected="${view === 'notes'}" data-act="view" data-view="notes">节目简介</button>
          <button type="button" role="tab" class="reader-tr-seg-btn ${view === 'guide' ? 'is-on is-ai' : ''}" aria-selected="${view === 'guide'}" data-act="view" data-view="guide">${sparkHtml()}<span>导读稿</span></button>
        </div><span class="ppg-view-note">${view === 'guide' ? '中文 · AI 整理' : '来源方提供'}</span></div>`
      : '<div class="podcast-show-notes-head"><h2 class="section-title">节目简介</h2><span>来源方提供</span></div>';
    const content = view === 'guide'
      ? `<div class="podcast-guide-body ppg-guide"><div class="markdown-body">${e.guideText.map(([tag, text]) => `<${tag}>${esc(text)}</${tag}>`).join('')}</div></div>`
      : `<div class="reader-article-copy markdown-body" ${notesLang}>${notes}</div><p class="reader-pane-origin"><a href="#" onclick="return false">查看原文 ↗</a><span class="reader-pane-origin-host"> · ${esc(e.source)}</span></p>`;
    return `${aiCardHtml(e)}${head}<div class="ppg-view-body">${content}</div>`;
  }
  function paneHtml(e) {
    return `${headHtml(e)}<div class="${isDesktop() ? 'reader-pane-body' : 'm-read-body'}">${bodyHtml(e)}</div>`;
  }

  /* ── 全局播放器(条 + 迷你):挂在阅读器根上,在所有页面 / 滚动容器之外 ── */
  function playerHtml() {
    // 层序:渐隐 → 形变底层(底板 / 影子)→ 条与迷你 → 形变上层(accent 内核 / 图标 / 副本)。
    // 渐隐必须在底板之下:放进条的容器里会被容器的层叠上下文整体抬到底板之上,把飞行中的底板盖掉
    return `<div class="ppg-player" data-form="none">
      <div class="ppg-scrim" aria-hidden="true"></div>
      <div class="ppg-fx is-under" aria-hidden="true"></div>
      <div class="ppg-dock">
      <div class="ppg-bar" role="region" aria-label="正在播放" hidden>
        <div class="ppg-bar-in">
          <button type="button" class="ppg-info" data-pact="goto"><span class="ppg-art"><span class="ppg-art-slot"></span></span><span class="ppg-info-slot"></span></button>
          <div class="ppg-ctrl">
            <button type="button" class="ppg-btn ppg-skip" data-pact="back" aria-label="后退 15 秒">${I.back15}<span>15</span></button>
            <button type="button" class="ppg-play" data-pact="toggle" aria-label="暂停"><span class="ppg-play-ico"><span class="ppg-g-play">${I.play}</span><span class="ppg-g-pause">${I.pause}</span></span></button>
            <button type="button" class="ppg-btn ppg-skip" data-pact="fwd" aria-label="前进 15 秒"><span>15</span>${I.fwd15}</button>
          </div>
          <div class="ppg-row2">
            <div class="ppg-seek">
              <span class="ppg-time ppg-cur">00:00</span>
              <div class="ppg-track"><div class="ppg-track-in" role="slider" tabindex="0" aria-label="播放进度" aria-valuemin="0"><span class="ppg-rail"><span class="ppg-fill"></span></span><span class="ppg-thumb"></span></div></div>
              <span class="ppg-time ppg-dur">00:00</span>
            </div>
            <div class="ppg-tail">
              <button type="button" class="ppg-btn ppg-rate" data-pact="rate" aria-label="播放速度 1 倍">1×</button>
              <button type="button" class="ppg-btn ppg-min" data-pact="min" aria-label="缩小播放条" title="缩小">${I.chevronDown}</button>
              <button type="button" class="ppg-btn ppg-close" data-pact="close" aria-label="关闭播放条（停止播放）" title="关闭">${I.x}</button>
            </div>
          </div>
        </div>
      </div>
      <div class="ppg-mini" role="region" aria-label="正在播放（已缩小）" hidden>
        <button type="button" class="ppg-mini-art" data-pact="expand" aria-label="展开播放条">
          <svg class="ppg-ring" viewBox="0 0 40 40" aria-hidden="true"><circle class="ppg-ring-track" cx="20" cy="20" r="18.75"/><circle class="ppg-ring-fill" cx="20" cy="20" r="18.75" pathLength="100"/></svg>
          <span class="ppg-art-slot"></span>
        </button>
        <button type="button" class="ppg-play ppg-mini-play" data-pact="toggle" aria-label="暂停"><span class="ppg-play-ico"><span class="ppg-g-play">${I.play}</span><span class="ppg-g-pause">${I.pause}</span></span></button>
      </div>
      </div>
      <div class="ppg-fx is-over" aria-hidden="true"></div>
      <div class="sr-only" aria-live="polite" data-live></div>
    </div>`;
  }

  /* ════════════════ 桌面 ════════════════ */
  function desktopHtml() {
    const rail = [['article', '文章', I.fileText], ['podcast', '播客', I.podcast], ['social', '社交', I.atSign]];
    return `<div class="ppg-app is-desktop" data-layout="${S.layout}" data-tone="${S.tone}" data-container="${S.container}">
      <div class="reader-shell ppg-shell">
        <nav class="reader-vrail" aria-label="阅读视图">
          <div class="reader-vrail-brand" aria-hidden="true">${svg('<path d="M12 3c-3.6 0-6 2.6-6 6v4l-1.8 3h15.6L18 13V9c0-3.4-2.4-6-6-6z"/><path d="M10 19a2 2 0 0 0 4 0"/>')}</div>
          <button type="button" class="reader-vrail-btn" aria-label="我的早报">${I.newspaper}<span class="reader-vrail-label">早报</span></button>
          <span class="reader-vrail-divider" aria-hidden="true"></span>
          ${rail.map(([id, label, icon]) => `<button type="button" class="reader-vrail-btn" data-act="container" data-container="${id}" aria-label="${label}">${icon}<span class="reader-vrail-label">${label}</span></button>`).join('')}
          <span class="reader-vrail-divider" aria-hidden="true"></span>
          <button type="button" class="reader-vrail-btn" data-act="container" data-container="discover" aria-label="发现">${I.compass}<span class="reader-vrail-label">发现</span></button>
          <div class="reader-vrail-spring"></div>
          <div class="ppg-rail-foot">
            <button type="button" class="reader-vrail-btn" data-act="theme" aria-label="切换主题">${root.dataset.theme === 'dark' ? I.sun : I.moon}<span class="reader-vrail-label">主题</span></button>
            <button type="button" class="reader-vrail-btn" aria-label="设置">${I.settings}<span class="reader-vrail-label">设置</span></button>
            <button type="button" class="reader-vrail-avatar ppg-rail-avatar" aria-label="退出登录">E</button>
          </div>
        </nav>
        <aside class="reader-col reader-col-sources"><div class="reader-sources-inner" data-slot="sources"></div></aside>
        <section class="reader-col reader-col-list" data-ppg-view="reading"><div class="reader-list-inner">
          <div class="reader-list-head"><span class="reader-list-title" data-slot="list-title"></span>
            <div class="reader-seg" role="tablist" aria-label="未读筛选"><button type="button" role="tab" class="reader-seg-btn is-on" aria-selected="true">全部</button><button type="button" role="tab" class="reader-seg-btn" aria-selected="false">未读</button></div>
            <button type="button" class="reader-unread-icon" aria-label="只看收藏">${I.star}</button>
            <button type="button" class="reader-unread-icon" aria-label="全部标为已读">${I.checkCheck}</button>
          </div>
          <div class="reader-list-scroll" data-slot="list"></div>
        </div></section>
        <section class="reader-col reader-col-read" data-ppg-view="reading">
          <div class="reader-pane-bar"><div class="reader-crumb" data-slot="crumb"></div>
            <button type="button" class="reader-pane-iconbtn" aria-label="收藏">${I.star}</button>
            <button type="button" class="reader-pane-iconbtn" aria-label="标为已读">${I.checkCheck}</button>
            <button type="button" class="reader-pane-iconbtn" aria-label="分享">${I.share}</button>
          </div>
          <article class="reader-pane" data-slot="pane"></article>
        </section>
        <section class="reader-col reader-social" data-ppg-view="social" aria-label="社交媒体" hidden>
          <header class="reader-social-head"><span class="reader-social-title">社交媒体</span>
            <div class="reader-social-actions">
              <div class="reader-seg" role="group" aria-label="未读过滤"><button type="button" class="reader-seg-btn is-on">全部</button><button type="button" class="reader-seg-btn">未读</button></div>
              <button type="button" class="reader-unread-icon" aria-label="全部标为已读">${I.checkCheck}</button>
              <button type="button" class="reader-unread-icon" aria-label="只看收藏">${I.star}</button>
              <button type="button" class="reader-search-icon" aria-label="搜索">${I.search}</button>
            </div>
          </header>
          <div class="reader-scrollwrap"><div class="reader-social-scroll" data-slot="social"><div class="reader-social-col">${socialHtml()}</div></div></div>
        </section>
        <main class="reader-disc" data-ppg-view="discover" aria-label="发现" hidden>
          <div class="reader-disc-head"><div class="reader-disc-head-inner">
            <div class="reader-disc-title-row"><span class="reader-disc-title">发现</span><span class="reader-disc-hint">浏览全站收录的来源,一键订阅到你的阅读器</span></div>
          </div></div>
          <div class="reader-disc-scroll" data-slot="discover"><div class="reader-disc-body">${discoverHtml()}</div></div>
        </main>
        <div class="ppg-fab-slot"><button type="button" class="reader-ai-fab" aria-label="问问哆啦美">${svg('<path d="M12 2c.6 5.4 2.6 7.4 10 10-7.4 2.6-9.4 4.6-10 10-.6-5.4-2.6-7.4-10-10 7.4-2.6 9.4-4.6 10-10Z" fill="currentColor" stroke="none"/>')}<span class="reader-ai-fab-label">问问哆啦美</span></button></div>
        ${playerHtml()}
      </div>
    </div>`;
  }
  function sourcesHtml() {
    if (S.container === 'social') {
      const off = SOCIAL.accounts.filter((a) => ['x_openai', 'x_alibaba_qwen', 'x_moonshot_ai'].includes(a.source_id));
      const per = SOCIAL.accounts.filter((a) => !off.includes(a));
      const rows = (list) => list.map((a) => `<div class="reader-source-row has-unread" role="button" tabindex="0">${avatarHtml(a.avatar, a.author_name, 'reader-src-avatar')}<p class="reader-source-name">${esc(a.name)}</p></div>`).join('');
      return `<div class="reader-src-head"><span class="reader-src-title">社交媒体</span></div>
        <div class="reader-source-scroll"><section class="reader-subs"><div class="reader-src-label">官方</div><div class="reader-group-body">${rows(off)}</div></section>
        <section class="reader-subs"><div class="reader-src-label">个人</div><div class="reader-group-body">${rows(per)}</div></section></div>`;
    }
    if (S.readingContainer === 'podcast') {
      return `<div class="reader-src-head"><span class="reader-src-title">播客</span></div>
        <div class="reader-source-scroll"><section class="reader-subs"><div class="reader-src-label">个人</div><div class="reader-group-body">
        ${listOf('podcast').map((e) => `<div class="reader-source-row has-unread" role="button" tabindex="0"><img class="reader-src-avatar ppg-src-cover" src="${e.cover}" alt=""><p class="reader-source-name">${esc(e.source)}</p></div>`).join('')}
        </div></section></div>`;
    }
    return `<div class="reader-src-head"><span class="reader-src-title">文章</span></div>
      <div class="reader-source-scroll"><section class="reader-subs"><div class="reader-src-label">媒体</div><div class="reader-group-body">
        <div class="reader-source-row has-unread" role="button" tabindex="0"><span class="ppg-logo is-row" aria-hidden="true">IT</span><p class="reader-source-name">IT之家</p></div>
      </div></section></div>`;
  }
  function renderDesktop() {
    const app = document.getElementById('ppg-root');
    app.innerHTML = desktopHtml();
    refreshDesktopChrome();
    renderPane();
    bindPlayer(app);
  }
  function refreshDesktopChrome() {
    const app = document.getElementById('ppg-root');
    app.querySelectorAll('.reader-vrail-btn[data-container]').forEach((b) => {
      const on = b.dataset.container === S.container;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', String(on));
    });
    $app().dataset.container = S.container;
    // 容器视图用 data-ppg-view(不能叫 data-view:正文里「节目简介 | 导读稿」切换钮已经用 data-view 标自己的视图)
    app.querySelectorAll('[data-ppg-view]').forEach((el) => {
      const v = el.dataset.ppgView;
      el.hidden = !(v === 'reading' ? (S.container === 'podcast' || S.container === 'article') : v === S.container);
    });
    app.querySelector('[data-slot="sources"]').innerHTML = sourcesHtml();
    app.querySelector('[data-slot="list-title"]').textContent = S.readingContainer === 'podcast' ? '全部播客' : '全部文章';
    refreshList();
  }
  function refreshList() {
    const slot = document.querySelector('[data-slot="list"]') || document.querySelector('.m-list');
    if (!slot) return;
    if (!isDesktop() && S.container === 'social') { slot.innerHTML = `<div class="ppg-m-social">${socialHtml()}</div>`; return; }
    slot.innerHTML = listHtml();
  }
  function renderPane({ keepScroll = false } = {}) {
    const e = byId(S.activeId);
    if (isDesktop()) {
      const pane = document.querySelector('[data-slot="pane"]');
      const top = pane.scrollTop;
      pane.innerHTML = paneHtml(e);
      pane.scrollTop = keepScroll ? top : 0;
      document.querySelector('[data-slot="crumb"]').innerHTML = `${e.cover ? `<img class="ppg-crumb-cover" src="${e.cover}" alt="">` : '<span class="ppg-logo" aria-hidden="true">IT</span>'}<span class="reader-crumb-name">${esc(e.source)}</span>`;
    } else {
      const scroll = document.querySelector('.m-read-scroll');
      const top = scroll.scrollTop;
      scroll.innerHTML = paneHtml(e);
      scroll.scrollTop = keepScroll ? top : 0;
    }
  }

  /* ════════════════ 手机 ════════════════ */
  function mobileHtml() {
    const tabs = [['article', '文章', I.fileText], ['podcast', '播客', I.podcast], ['social', '社交', I.atSign], ['rankings', '榜单', I.barChart], ['me', '我的', I.user]];
    return `<div class="ppg-app is-mobile m-root" data-tone="${S.tone}" data-container="${S.container}">
      <div class="m-shell ppg-mshell">
        <div class="m-topbar">
          <button type="button" class="m-iconbtn" aria-label="订阅源">${I.menu}</button>
          <span class="m-title" data-slot="m-title"></span>
          <div class="m-topbar-sp"></div>
          <div class="mini-seg" role="tablist" aria-label="未读筛选"><button type="button" role="tab" class="mini-seg-btn is-on" aria-selected="true">全部</button><button type="button" role="tab" class="mini-seg-btn" aria-selected="false">未读</button></div>
          <button type="button" class="m-iconbtn" aria-label="只看收藏">${I.star}</button>
          <button type="button" class="m-iconbtn" aria-label="搜索">${I.search}</button>
        </div>
        <div class="m-content"><div class="m-list"></div></div>
        <nav class="m-tabbar" aria-label="阅读视图">
          ${tabs.map(([id, label, icon]) => `<button type="button" class="m-tab" data-act="container" data-container="${id}" aria-label="${label}"><span class="m-tab-ic">${icon}</span><span class="m-tab-label">${label}</span></button>`).join('')}
        </nav>
      </div>
      <div class="m-read ppg-mread" hidden>
        <div class="m-topbar on-pane">
          <button type="button" class="m-iconbtn" data-act="back" aria-label="返回">${I.arrowLeft}</button>
          <span class="m-topbar-sp"></span>
          <button type="button" class="m-iconbtn" aria-label="收藏">${I.star}</button>
          <button type="button" class="m-iconbtn" aria-label="分享">${I.share}</button>
          <button type="button" class="m-iconbtn" aria-label="将正文译为中文"><span class="reader-tr-glyph" aria-hidden="true">译</span></button>
          <button type="button" class="m-iconbtn" aria-label="更多操作">${I.more}</button>
        </div>
        <div class="m-read-scroll"></div>
      </div>
      ${playerHtml()}
    </div>`;
  }
  function renderMobile() {
    const app = document.getElementById('ppg-root');
    if (S.container === 'discover') S.container = 'podcast';   // 手机没有发现页一级入口(在「我的」里),落回播客
    app.innerHTML = mobileHtml();
    refreshMobileChrome();
    bindPlayer(app);
  }
  function refreshMobileChrome() {
    document.querySelectorAll('.m-tab').forEach((b) => {
      const on = b.dataset.container === S.container;
      b.classList.toggle('is-on', on);
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    $app().dataset.container = S.container;
    document.querySelector('[data-slot="m-title"]').textContent = S.container === 'social' ? '社交' : S.container === 'podcast' ? '播客' : '文章';
    refreshList();
  }
  // push / pop 正文页:正文页从右滑入,列表页轻推向左(iOS 语法);播放条留在原处,只按页面换底边
  function pushRead(id) {
    S.activeId = id;
    renderPane();
    const read = document.querySelector('.ppg-mread');
    const list = document.querySelector('.ppg-mshell');
    read.hidden = false;
    list.inert = true;          // 被盖住的列表页不再接焦点 / 读屏
    S.page = 'read';
    syncPlayerOffset();
    refreshList();
    if (!motionOff()) {
      read.animate([{ transform: 'translateX(100%)' }, { transform: 'none' }], { duration: MOTION.settleDur, easing: MOTION.settle });
      list.animate([{ transform: 'none' }, { transform: 'translateX(-22%)', opacity: 0.85 }], { duration: MOTION.settleDur, easing: MOTION.settle });
    }
    if (kbd()) read.querySelector('[data-act="back"]').focus({ preventScroll: true });
  }
  function popRead() {
    const read = document.querySelector('.ppg-mread');
    const list = document.querySelector('.ppg-mshell');
    S.page = 'list';
    list.inert = false;
    syncPlayerOffset();
    refreshList();
    const done = () => {
      if (S.page !== 'list') return;
      read.hidden = true;
      // 焦点回到刚才打开的那一行
      if (kbd()) document.querySelector(`.m-list .reader-entry[data-id="${S.activeId}"]`)?.focus({ preventScroll: true });
    };
    if (motionOff()) { done(); return; }
    const a = read.animate([{ transform: 'none' }, { transform: 'translateX(100%)' }], { duration: 300, easing: MOTION.fade, fill: 'forwards' });
    list.animate([{ transform: 'translateX(-22%)', opacity: 0.85 }, { transform: 'none', opacity: 1 }], { duration: 300, easing: MOTION.fade });
    a.finished.then(() => { done(); a.cancel(); });
  }

  /* ════════════════ 播放器状态与时钟 ════════════════ */
  function makeItem(entryId, track) {
    const e = byId(entryId);
    const seconds = track === 'digest' ? e.guideAudio : track === 'narration' ? e.narration : e.durationSeconds;
    return {
      key: `${entryId}:${track}`, entryId, track, seconds,
      ai: track !== 'original',
      title: e.title,
      sub: track === 'digest' ? '精品导读' : track === 'narration' ? '文章导读' : e.source,
      cover: e.cover || '',
      lang: latin(e.title),
    };
  }
  function artHtml(item) {
    return item.cover
      ? `<img class="ppg-cover" src="${item.cover}" alt="">`
      : `<span class="ppg-cover ppg-cover-ai" aria-hidden="true">${sparkHtml()}</span>`;
  }
  function infoHtml(item) {
    return `<span class="ppg-title" ${item.lang ? 'lang="en"' : ''}>${esc(item.title)}</span>
      <span class="ppg-sub">${item.ai ? `${sparkHtml()}<span>${esc(item.sub)}</span><span class="ppg-ai">AI 生成</span>` : `<span>${esc(item.sub)}</span>`}</span>`;
  }
  function fillItem(item) {
    const pl = $p();
    pl.querySelectorAll('.ppg-art-slot').forEach((s) => { s.innerHTML = artHtml(item); });
    pl.querySelector('.ppg-info-slot').innerHTML = infoHtml(item);
    pl.querySelector('.ppg-info').setAttribute('aria-label', `打开：${item.title}`);
    pl.querySelector('.ppg-dur').textContent = fmt.clock(item.seconds);
    pl.querySelector('.ppg-track-in').setAttribute('aria-valuemax', String(item.seconds));
    pl.querySelector('.ppg-bar').setAttribute('aria-label', `正在播放：${item.title}${item.ai ? '（AI 生成）' : ''}`);
    pl.dataset.ai = item.ai ? '1' : '';
  }
  // 进度显示 4 Hz 刷新就够(12 分钟的节目在 170px 的轨上每 250ms 只走 0.06px);拖动 / 跳转时立即刷新。
  // 不逐帧写样式:形变进行时主线程保持空闲
  function paintTime(force = false) {
    const pl = $p();
    if (!pl || !P.item) return;
    const now = performance.now();
    if (force || now - (paintTime.last || 0) > 240) {
      paintTime.last = now;
      // 进度变量只写在用它的两小块上(进度轨 / 迷你进度环),不写在播放器根上:写在根上每次刷新都会让整棵子树(含封面模糊位图那一层)
      // 重算样式、重绘——软件合成下这就是稳态光栅成本的大头
      const v = Math.min(1, P.t / P.item.seconds).toFixed(4);
      pl.querySelectorAll('.ppg-seek, .ppg-ring').forEach((el) => el.style.setProperty('--ppg-p', v));
      pl.querySelector('.ppg-cur').textContent = fmt.clock(P.t);
      const tr = pl.querySelector('.ppg-track-in');
      tr.setAttribute('aria-valuenow', String(Math.round(P.t)));
      tr.setAttribute('aria-valuetext', `${fmt.clock(P.t)} / ${fmt.clock(P.item.seconds)}`);
    }
  }
  function paintPlaying() {
    const pl = $p();
    pl.toggleAttribute('data-playing', P.playing);
    pl.querySelectorAll('[data-pact="toggle"]').forEach((b) => b.setAttribute('aria-label', P.playing ? '暂停' : '播放'));
    // 标题区入口与条目行的播放态(只换 class / 标签,不重建,焦点不丢)
    document.querySelectorAll('.ppg-entry.is-current .ppg-eq').forEach((x) => x.classList.toggle('is-live', P.playing));
    document.querySelectorAll('.ppg-entry.is-current').forEach((b) => b.setAttribute('aria-pressed', String(P.playing)));
    document.querySelectorAll('.ppg-row-now').forEach((x) => {
      x.querySelector('.ppg-eq')?.classList.toggle('is-live', P.playing);
      x.lastElementChild.textContent = P.playing ? '播放中' : '已暂停';
    });
  }
  function tick(now) {
    const dt = P.last ? (now - P.last) / 1000 : 0;
    P.last = now;
    if (P.playing && P.item) {
      P.t = Math.min(P.item.seconds, P.t + dt * RATES[P.rate]);
      if (P.t >= P.item.seconds) { P.playing = false; paintPlaying(); }
      paintTime();
    }
    P.raf = P.playing ? requestAnimationFrame(tick) : 0;
    if (!P.playing) P.last = 0;
  }
  function setPlaying(on) {
    P.playing = on;
    paintPlaying();
    if (on && !P.raf) { P.last = 0; P.raf = requestAnimationFrame(tick); }
  }
  function seek(sec) {
    if (!P.item) return;
    P.t = Math.max(0, Math.min(P.item.seconds, sec));
    paintTime(true);
  }
  function announce(text) {
    const live = $p()?.querySelector('[data-live]');
    if (live) { live.textContent = ''; setTimeout(() => { live.textContent = text; }, 30); }
  }

  /* ── 形态:none / bar / mini。只改 data-form 与显隐;动画由各 morph 负责 ── */
  function setForm(form) {
    P.form = form;
    const pl = $p();
    pl.dataset.form = form;
    $app().dataset.form = form;
    pl.querySelector('.ppg-bar').hidden = form !== 'bar';
    pl.querySelector('.ppg-mini').hidden = form !== 'mini';
    syncPlayerOffset();
  }
  // 手机:列表页条在底部 Tab 栏之上,正文页贴安全区之上;用 transform 平移(可合成),推页时随页面一起过渡
  function syncPlayerOffset() {
    const app = $app();
    if (!app) return;
    if (!isDesktop()) {
      const tab = document.querySelector('.m-tabbar');
      app.style.setProperty('--ppg-tabbar', `${tab ? tab.offsetHeight : 0}px`);
      app.dataset.page = S.page;
    }
  }
  function refreshEntriesAndRows() {
    const host = isDesktop() ? document.querySelector('[data-slot="pane"]') : document.querySelector('.m-read-scroll');
    const box = host?.querySelector('.ppg-entries');
    const e = byId(S.activeId);
    if (box && e) {
      const focused = [...box.children].indexOf(document.activeElement);
      const html = listenHtml(e);
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      box.replaceWith(tmp.firstElementChild);
      if (focused >= 0) host.querySelector('.ppg-entries')?.children[focused]?.focus({ preventScroll: true });
    }
    if (!(isDesktop() && S.container === 'social') && !(!isDesktop() && S.container === 'social')) refreshList();
  }

  /* ════════════════ 贴底占位:让出 / 收回空间(不做逐帧改布局) ════════════════
     on=true :在形变落地那一帧调用——网格底行撑开,各栏变矮;变矮的那一截此刻正被条盖住,看不出变化。
     on=false:在收起 / 关闭开始时调用——底行收掉,各栏变高(多出来的部分此刻在条下面);
               已经滚到底的栏会被浏览器往下推一截,FLIP 成跟手的下滑;全宽贴底时轨底钮同样 FLIP。 */
  const SCROLLERS = '.reader-pane, .reader-list-scroll, .reader-source-scroll, .reader-social-scroll, .reader-disc-scroll';
  function setDocked(on, { flip = false, dur = 300 } = {}) {
    const app = $app();
    if (!app || !isDesktop()) return;
    // data-landed:条已落地(所有条位;内容区浮层据此给各栏底边上 mask,落地这一帧才上,形变途中不逐帧重绘 mask)
    app.toggleAttribute('data-landed', !!on);
    const want = on && docks();
    if (want === app.hasAttribute('data-docked')) return;
    const scrollers = [...document.querySelectorAll(SCROLLERS)].filter((s) => s.offsetParent);
    const before = scrollers.map((s) => s.scrollTop);
    const foot = document.querySelector('.ppg-rail-foot');
    const footTop = foot ? foot.getBoundingClientRect().top : 0;
    app.toggleAttribute('data-docked', want);
    if (!flip || motionOff()) return;
    scrollers.forEach((s, i) => {
      const d = before[i] - s.scrollTop;
      if (d < 0.5) return;
      [...s.children].slice(0, 60).forEach((c) => c.animate([{ transform: `translateY(${-d}px)` }, { transform: 'none' }], { duration: dur, easing: MOTION.settle }));
    });
    if (foot) {
      const d = footTop - foot.getBoundingClientRect().top;
      if (Math.abs(d) > 0.5) foot.animate([{ transform: `translateY(${d}px)` }, { transform: 'none' }], { duration: dur, easing: MOTION.settle });
    }
  }

  /* ════════════════ 动作 ════════════════ */
  function listen(entryId, track, srcBtn) {
    const item = makeItem(entryId, track);
    if (P.item && P.item.key === item.key && P.form !== 'none') { setPlaying(!P.playing); return; }
    if (P.item) P.pos.set(P.item.key, P.t);
    const first = P.form === 'none';
    P.item = item;
    P.t = P.pos.get(item.key) || 0;
    if (first) {
      fillItem(item);
      applyTone(item);
      paintTime(true);
      openBar(srcBtn);
    } else {
      swapItem(item, srcBtn);
    }
    announce(`正在播放：${item.title}${item.ai ? '，AI 生成' : ''}`);
  }
  function closePlayer() {
    if (P.item) P.pos.set(P.item.key, P.t);
    setPlaying(false);
    const from = P.form;
    const pl = $p();
    const el = pl.querySelector(from === 'mini' ? '.ppg-mini' : '.ppg-bar');
    const finish = () => {
      setForm('none');
      P.item = null;
      el.getAnimations().forEach((a) => a.cancel());
      refreshEntriesAndRows();
      // 焦点回到标题区这一期的入口(如果在场),不掉进 body
      if (kbd()) document.querySelector('.ppg-entries .ppg-entry')?.focus({ preventScroll: true });
    };
    const docked = from === 'bar' && docks();
    const dur = docked ? 280 : 200;
    setDocked(false, { flip: true, dur });
    moveFab('none');
    pl.dataset.leaving = '';
    if (motionOff()) { delete pl.dataset.leaving; finish(); return; }
    // 贴底条整条向下滑走;浮条淡出并轻微下沉
    const frames = docked
      ? [{ transform: 'none' }, { transform: 'translateY(100%)' }]
      : [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(18px) scale(.98)' }];
    const a = el.animate(frames, { duration: dur, easing: MOTION.out, fill: 'forwards' });
    a.finished.then(() => { delete pl.dataset.leaving; finish(); });
  }

  /* ════════════════ 形变 ════════════════ */
  // 形变两端的矩形对齐到设备像素:125% / 150% 缩放下 getBoundingClientRect 是小数,形变层停在半个设备像素上会被双线性采样糊掉,
  // 交接给真实元素(布局绘制时按设备像素取整)的那一帧就会「抖一下变清楚」。四条边各自取整,宽高由取整后的边算
  const snap = (v) => Math.round(v * (window.devicePixelRatio || 1)) / (window.devicePixelRatio || 1);
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    const x = snap(r.left); const y = snap(r.top);
    return { x, y, w: snap(r.right) - x, h: snap(r.bottom) - y };
  };
  const radiusOf = (el) => parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
  const shadowOf = (el) => getComputedStyle(el).boxShadow !== 'none';
  function finishFx() {
    fxRunning.forEach((a) => { try { a.finish(); } catch { /* 已结束 */ } });
    fxRunning = [];
    $p()?.querySelectorAll('.ppg-fx').forEach((l) => l.replaceChildren());
  }
  const fxLayer = (under) => $p().querySelector(under ? '.ppg-fx.is-under' : '.ppg-fx.is-over');
  function track(anims) { fxRunning.push(...anims.filter(Boolean)); return anims; }
  // 一块「形变层」:盒子取两端矩形的最大宽高,translate 放到矩形左上角,clip-path inset(round)裁出当前矩形——
  // 位置与尺寸同步插值、圆角每一帧都是真圆角,内容不缩放就不会被拉伸
  function morphBox(cls, from, to, { radiusFrom, radiusTo, dur, easing, delay = 0, extra = [], under = false }) {
    const W = Math.max(from.w, to.w);
    const H = Math.max(from.h, to.h);
    const el = document.createElement('span');
    el.className = `ppg-fx-box ${cls}`;
    el.style.width = `${W}px`;
    el.style.height = `${H}px`;
    fxLayer(under).append(el);
    const kf = (r, rad) => ({
      transform: `translate(${r.x}px, ${r.y}px)`,
      clipPath: `inset(0px ${W - r.w}px ${H - r.h}px 0px round ${rad}px)`,
    });
    const a = el.animate([{ ...kf(from, radiusFrom), ...(extra[0] || {}) }, { ...kf(to, radiusTo), ...(extra[1] || {}) }],
      { duration: dur, easing, delay, fill: 'both' });
    return { el, a };
  }
  // 让一个静止的元素(位于 at 矩形)只露出「正在形变的底板」那一块:inset 按 from → to 同一缓动插值(可以为负:起点在元素框外),
  // 内容永远不会画到底板外面——宽条两端的内容不会先于底板出现,条 → 迷你时淡出中的文字也不压在正文上
  function clipFollow(el, at, from, to, { radiusFrom, radiusTo, dur, easing }) {
    const ins = (r, rad) => `inset(${r.y - at.y}px ${at.x + at.w - (r.x + r.w)}px ${at.y + at.h - (r.y + r.h)}px ${r.x - at.x}px round ${rad}px)`;
    return el.animate([{ clipPath: ins(from, radiusFrom) }, { clipPath: ins(to, radiusTo) }], { duration: dur, easing, fill: 'both', composite: 'replace' });
  }
  // 影子单独一层:clip-path 会把外投影一起裁掉,所以影子用 translate + scale 跟随(柔边,缩放看不出变形)
  function morphShadow(from, to, { dur, easing, radiusTo = 16, endOpacity = 1 }) {
    const el = document.createElement('span');
    el.className = 'ppg-fx-shadow';
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.style.borderRadius = `${radiusTo}px`;
    fxLayer(true).append(el);
    // 第 0 帧没有影子(与按下前一致),起飞后迅速出现;浮条落地与真条的影子重合,贴底条落地时影子退掉(贴底条没有影子)
    const a = el.animate([
      { transform: `translate(${from.x}px, ${from.y}px) scale(${from.w / to.w}, ${from.h / to.h})`, opacity: 0 },
      { transform: `translate(${(from.x + to.x) / 2}px, ${(from.y + to.y) / 2}px) scale(${(from.w / to.w + 1) / 2}, ${(from.h / to.h + 1) / 2})`, opacity: endOpacity ? 1 : 0.7, offset: 0.5 },
      { transform: `translate(${to.x}px, ${to.y}px) scale(1, 1)`, opacity: endOpacity },
    ], { duration: dur, easing, fill: 'both' });
    return { el, a };
  }
  function fly(node, from, to, { dur, easing, delay = 0, fade = null }) {
    const el = document.createElement('span');
    el.className = 'ppg-fx-fly';
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.append(node);
    fxLayer(false).append(el);
    const s = from.w / to.w;
    const frames = [
      { transform: `translate(${from.x + (from.w - to.w) / 2}px, ${from.y + (from.h - to.h) / 2}px) scale(${s})`, opacity: fade ? fade[0] : 1 },
      { transform: `translate(${to.x}px, ${to.y}px) scale(1)`, opacity: fade ? fade[1] : 1 },
    ];
    const a = el.animate(frames, { duration: dur, easing, delay, fill: 'both' });
    return { el, a };
  }
  // accent 内核 → 条上的播放钮。播放钮颜色随配色而定(中性 / 品牌 = accent,封面 / 模糊 / 反色 = 墨色):
  // 颜色不同时叠两层同轨迹的内核交叉淡变(只动 opacity),不做逐帧的背景色插值
  function morphCore(from, to, { radiusFrom, radiusTo, dur, easing, startOpacity }) {
    const playBg = getComputedStyle($p()).getPropertyValue('--ppg-play-bg');
    const accent = getComputedStyle(root).getPropertyValue('--dorami-blue');
    const out = [morphBox('ppg-fx-core', from, to, { radiusFrom, radiusTo, dur, easing, extra: [{ opacity: startOpacity }, { opacity: 1 }] })];
    const same = probe(playBg).join() === probe(accent).join();
    if (!same) {
      const b = morphBox('ppg-fx-core is-tone', from, to, { radiusFrom, radiusTo, dur, easing });
      b.el.animate([{ opacity: 0 }, { opacity: 0, offset: 0.35 }, { opacity: 1, offset: 0.8 }, { opacity: 1 }], { duration: dur, fill: 'both' });
      out.push(b);
    }
    return out;
  }

  /* ── 多段路径(宽条用):stops = [{ r, rad, at, ease, extra }]。at 是这一站在时间轴上的位置(0–1),
     ease 是从这一站走到下一站的缓动(逐段弹簧,整体时间轴是 linear)——两段各自从静止起步、在静止收尾,拐角处不会有速度突变 ── */
  function pathBox(cls, stops, { dur, under = false }) {
    const W = Math.max(...stops.map((s) => s.r.w));
    const H = Math.max(...stops.map((s) => s.r.h));
    const el = document.createElement('span');
    el.className = `ppg-fx-box ${cls}`;
    el.style.width = `${W}px`;
    el.style.height = `${H}px`;
    fxLayer(under).append(el);
    const frames = stops.map((s) => ({
      offset: s.at,
      easing: s.ease || 'linear',
      transform: `translate(${s.r.x}px, ${s.r.y}px)`,
      clipPath: `inset(0px ${W - s.r.w}px ${H - s.r.h}px 0px round ${s.rad}px)`,
      ...(s.extra || {}),
    }));
    return { el, a: el.animate(frames, { duration: dur, easing: 'linear', fill: 'both' }) };
  }
  function pathClip(el, at, stops, { dur }) {
    const ins = (r, rad) => `inset(${r.y - at.y}px ${at.x + at.w - (r.x + r.w)}px ${at.y + at.h - (r.y + r.h)}px ${r.x - at.x}px round ${rad}px)`;
    return el.animate(stops.map((s) => ({ offset: s.at, easing: s.ease || 'linear', clipPath: ins(s.r, s.rad) })), { duration: dur, easing: 'linear', fill: 'both', composite: 'replace' });
  }
  function pathShadow(stops, { dur, radiusTo, endOpacity }) {
    const to = stops[stops.length - 1].r;
    const el = document.createElement('span');
    el.className = 'ppg-fx-shadow';
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.style.borderRadius = `${radiusTo}px`;
    fxLayer(true).append(el);
    const frames = stops.map((s, i) => ({
      offset: s.at,
      easing: s.ease || 'linear',
      transform: `translate(${s.r.x}px, ${s.r.y}px) scale(${s.r.w / to.w}, ${s.r.h / to.h})`,
      opacity: i === 0 ? 0 : i === stops.length - 1 ? endOpacity : 1,
    }));
    return { el, a: el.animate(frames, { duration: dur, easing: 'linear', fill: 'both' }) };
  }
  function pathFly(node, stops, { dur }) {
    const to = stops[stops.length - 1].r;
    const el = document.createElement('span');
    el.className = 'ppg-fx-fly';
    el.style.width = `${to.w}px`;
    el.style.height = `${to.h}px`;
    el.append(node);
    fxLayer(false).append(el);
    const frames = stops.map((s) => ({
      offset: s.at,
      easing: s.ease || 'linear',
      transform: `translate(${s.r.x + (s.r.w - to.w) / 2}px, ${s.r.y + (s.r.h - to.h) / 2}px) scale(${s.r.w / to.w})`,
    }));
    return { el, a: el.animate(frames, { duration: dur, easing: 'linear', fill: 'both' }) };
  }

  /* 宽条的打开(内容区贴底 / 内容区浮层 / 全宽贴底):先「落」再「铺」。
     一步插值的话,胶囊(160 × 30)到宽条(1376 × 72)中途是一根细长的横条,斜着扫过整个阅读窗和条目列;
     改成两段:① 落——胶囊收成条的高度,落到条的中区(播放钮所在),accent 内核同时收成播放钮;
     ② 铺——落稳后向两侧铺开到全宽,真实条的内容随底板从中间往两边露出。贴底条位的「铺」用临界阻尼(边就是外框的边,不过冲) */
  function openWide(srcBtn, m) {
    const { pl, bar, src, icoR, primary, label, card, barR, playBtn, play, playIco } = m;
    const dur = MOTION.morphDur + 60;
    const k = 0.42;
    const drop = spring({ zeta: 0.9, response: 0.26, dur: dur * k });
    const spread = spring({ zeta: docks() ? 1 : 0.92, response: 0.3, dur: dur * (1 - k), kick: 0.6 });
    const pc = play.x + play.w / 2;
    const vw = Math.min(card.w, Math.max(src.w + 48, 232));
    const via = { x: snap(Math.max(card.x, Math.min(card.x + card.w - vw, pc - vw / 2))), y: card.y, w: vw, h: card.h };
    const fx = [];
    const three = (a, b, c, extra = [{}, {}, {}]) => [
      { r: a.r, rad: a.rad, at: 0, ease: drop, extra: extra[0] },
      { r: b.r, rad: b.rad, at: k, ease: spread, extra: extra[1] },
      { r: c.r, rad: c.rad, at: 1, extra: extra[2] },
    ];
    const plateStops = three({ r: src, rad: src.h / 2 }, { r: via, rad: via.h / 2 }, { r: card, rad: barR });
    fx.push(pathShadow(plateStops, { dur, radiusTo: barR, endOpacity: shadowOf(bar) ? 1 : 0 }));
    fx.push(pathBox('ppg-fx-plate', plateStops, { dur, under: true }));
    // 真实条的内容:「落」的那一段整条藏着(各部分透明度为 0),「铺」的那一段裁在底板当前矩形里
    fx.push({ el: null, a: pathClip(bar, card, three({ r: via, rad: via.h / 2 }, { r: via, rad: via.h / 2 }, { r: card, rad: barR }), { dur }) });
    // accent 内核:落的那一段收成播放钮,之后停在原位(颜色不同时与墨色内核交叉淡变)
    const coreFrom = primary ? src : { x: icoR.x - 3, y: icoR.y - 3, w: icoR.w + 6, h: icoR.h + 6 };
    const coreStops = three({ r: coreFrom, rad: coreFrom.h / 2 }, { r: play, rad: play.h / 2 }, { r: play, rad: play.h / 2 },
      [{ opacity: primary ? 1 : 0 }, { opacity: 1 }, { opacity: 1 }]);
    fx.push(pathBox('ppg-fx-core', coreStops, { dur }));
    const playBg = getComputedStyle(pl).getPropertyValue('--ppg-play-bg');
    if (probe(playBg).join() !== probe(getComputedStyle(root).getPropertyValue('--dorami-blue')).join()) {
      const t = pathBox('ppg-fx-core is-tone', coreStops.map((st) => ({ ...st, extra: {} })), { dur });
      t.el.animate([{ opacity: 0 }, { opacity: 0, offset: k * 0.35 }, { opacity: 1, offset: k * 0.95 }, { opacity: 1 }], { duration: dur, fill: 'both' });
      fx.push(t);
    }
    if (label) fx.push({ el: label, a: label.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: MOTION.out, fill: 'both' }) });
    const glyph = document.createElement('span');
    glyph.className = 'ppg-fx-glyph';
    glyph.innerHTML = `<span class="ppg-g-play">${I.play}</span><span class="ppg-g-pause">${I.pause}</span>`;
    const icoBox = { x: icoR.x, y: icoR.y, w: icoR.w, h: icoR.h };
    fx.push(pathFly(glyph, [{ r: icoBox, at: 0, ease: drop }, { r: playIco, at: k, ease: 'linear' }, { r: playIco, at: 1 }], { dur }));
    fx.push({ el: null, a: glyph.querySelector('.ppg-g-play').animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, delay: dur * k - 60, fill: 'both' }) });
    fx.push({ el: null, a: glyph.querySelector('.ppg-g-pause').animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, delay: dur * k - 60, fill: 'both' }) });
    // 条内内容:从中区往两端依次淡入(与底板铺开的方向一致)
    const parts = [...bar.querySelectorAll('.ppg-skip'), bar.querySelector('.ppg-seek'), bar.querySelector('.ppg-art'), bar.querySelector('.ppg-info-slot'), bar.querySelector('.ppg-rate'), bar.querySelector('.ppg-min'), bar.querySelector('.ppg-close')];
    parts.forEach((el, i) => {
      if (el) fx.push({ el: null, a: el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, delay: dur * k - 20 + i * 16, easing: MOTION.fade, fill: 'both' }) });
    });
    return fx;
  }

  /* none → bar:被点的胶囊展开成底部条 */
  function openBar(srcBtn) {
    finishFx();
    const pl = $p();
    const bar = pl.querySelector('.ppg-bar');
    if (motionOff() || !srcBtn || !srcBtn.isConnected) {
      setForm('bar'); setPlaying(true); setDocked(true); moveFab('bar'); refreshEntriesAndRows();
      if (!motionOff()) bar.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: MOTION.fade });
      else bar.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 120 });
      if (kbd()) bar.querySelector('.ppg-play').focus({ preventScroll: true });
      return;
    }
    if (MORPH === 'vt' && document.startViewTransition) { openBarVT(srcBtn); return; }
    // 先量源:胶囊、它的图标、是不是主钮(实心 accent)
    const src = rectOf(srcBtn);
    const srcIco = srcBtn.querySelector('.ppg-e-ico');
    const icoR = srcIco ? rectOf(srcIco) : { x: src.x + 12, y: src.y + src.h / 2 - 6, w: 12, h: 12 };
    const primary = srcBtn.classList.contains('is-primary');
    // getComputedStyle 是活对象:入口马上会被重建(脱离文档后读出来全是空串),先抄下要用的值
    const cs = getComputedStyle(srcBtn);
    const srcStyle = { fontFamily: cs.fontFamily, fontSize: cs.fontSize, fontWeight: cs.fontWeight, lineHeight: cs.lineHeight };
    const srcText = { label: srcBtn.querySelector('.ppg-e-label')?.textContent || '', len: srcBtn.querySelector('.ppg-len')?.textContent || '' };
    // 再落地终态(本帧内不绘制):条就位但「装配中」,入口换成播放态。贴底条此刻还是覆盖层,落地那一帧才占位
    setForm('bar');
    pl.dataset.assembling = 'open';
    setPlaying(true);
    refreshEntriesAndRows();
    const card = rectOf(bar);
    const barR = radiusOf(bar);
    const playBtn = bar.querySelector('.ppg-play');
    const play = rectOf(playBtn);
    const playIco = rectOf(playBtn.querySelector('.ppg-play-ico'));
    const dur = MOTION.morphDur;
    const ease = MOTION.morph;
    const mkLabel = () => {
      if (!primary) return null;
      const label = document.createElement('span');
      label.className = 'ppg-fx-label';
      // 字与时长一起复刻(第 0 帧与按下前逐像素一致)
      label.innerHTML = `<span>${esc(srcText.label)}</span><span class="ppg-len">${esc(srcText.len)}</span>`;
      // font 简写在按钮上会取到空串(Chrome),逐项拷贝
      label.style.cssText = `transform:translate(${src.x}px, ${src.y}px);width:${src.w}px;height:${src.h}px;font-family:${srcStyle.fontFamily};font-size:${srcStyle.fontSize};font-weight:${srcStyle.fontWeight};line-height:${srcStyle.lineHeight};padding-left:${(icoR.x - src.x) + icoR.w + 7}px`;
      fxLayer(false).append(label);
      return label;
    };
    if (isDesktop() && S.layout !== 'pane') {
      const fx = openWide(srcBtn, { pl, bar, src, icoR, primary, label: mkLabel(), card, barR, playBtn, play, playIco });
      landOpen(pl, fx, playBtn);
      return;
    }
    const fx = [];
    // ① 影子:跟着底板同一条路径,起飞时浅、浮条落地与真条的影子重合
    fx.push(morphShadow(src, card, { dur, easing: ease, radiusTo: barR, endOpacity: shadowOf(bar) ? 1 : 0 }));
    // ② 底板:胶囊 → 底部条(在条内容之下,内容在它上面错峰淡入)
    fx.push(morphBox('ppg-fx-plate', src, card, { radiusFrom: src.h / 2, radiusTo: barR, dur, easing: ease, under: true }));
    // ③ 真实条的内容裁在底板当前矩形里:宽条两端的内容不会先于底板出现
    fx.push({ el: null, a: clipFollow(bar, card, src, card, { radiusFrom: src.h / 2, radiusTo: barR, dur, easing: ease }) });
    // ④ accent 内核:主钮整颗实心收拢成条上的播放钮;次钮从图标位置长出
    const coreFrom = primary ? src : { x: icoR.x - 3, y: icoR.y - 3, w: icoR.w + 6, h: icoR.h + 6 };
    morphCore(coreFrom, play, { radiusFrom: coreFrom.h / 2, radiusTo: play.h / 2, dur, easing: ease, startOpacity: primary ? 1 : 0 }).forEach((c) => fx.push(c));
    // ⑤ 胶囊原来的字:随起飞迅速淡出(只在主钮上——次钮的字留在原地,入口已就地换成播放态)
    const label = mkLabel();
    if (label) fx.push({ el: label, a: label.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: MOTION.out, fill: 'both' }) });
    // ⑥ ▶ 共享元素:从胶囊飞到条上的播放钮,落地前换成 ⏸
    const glyph = document.createElement('span');
    glyph.className = 'ppg-fx-glyph';
    glyph.innerHTML = `<span class="ppg-g-play">${I.play}</span><span class="ppg-g-pause">${I.pause}</span>`;
    const g = fly(glyph, { x: icoR.x, y: icoR.y, w: icoR.w, h: icoR.h }, playIco, { dur, easing: ease });
    fx.push(g);
    fx.push({ el: null, a: glyph.querySelector('.ppg-g-play').animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, delay: dur * 0.5, fill: 'both' }) });
    fx.push({ el: null, a: glyph.querySelector('.ppg-g-pause').animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, delay: dur * 0.5, fill: 'both' }) });
    // ⑦ 条内内容错峰淡入(封面 → 文字 → 控制 → 进度 → 尾部)
    const parts = [bar.querySelector('.ppg-art'), bar.querySelector('.ppg-info-slot'), bar.querySelector('.ppg-skip'), bar.querySelectorAll('.ppg-skip')[1], bar.querySelector('.ppg-seek'), bar.querySelector('.ppg-rate'), bar.querySelector('.ppg-min'), bar.querySelector('.ppg-close')];
    parts.forEach((el, i) => {
      if (!el) return;
      fx.push({ el: null, a: el.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 220, delay: dur * 0.38 + i * 18, easing: MOTION.fade, fill: 'both' }) });
    });
    landOpen(pl, fx, playBtn);
  }
  // 打开形变收尾:全部动画结束的那一帧撤掉形变层、换回真实条,贴底条位此刻才占位(各栏变矮的那一截正被条盖住)
  function landOpen(pl, fx, playBtn) {
    track(fx.map((f) => f.a));
    moveFab('bar');
    Promise.all(fx.map((f) => f.a.finished)).then(() => {
      delete pl.dataset.assembling;
      fx.forEach((f) => { f.el?.remove(); f.a.cancel(); });
      fxRunning = [];
      setDocked(true, { flip: true, dur: 300 });
      if (kbd() && (pl.contains(document.activeElement) || document.activeElement === document.body || !document.activeElement?.isConnected || document.activeElement?.closest('.ppg-entries'))) {
        playBtn.focus({ preventScroll: true });
      }
    }).catch(() => {});
  }

  /* View Transitions 版(对照用,?morph=vt):胶囊与条用同名 view-transition-name 做共享元素形变 */
  function openBarVT(srcBtn) {
    const pl = $p();
    const bar = pl.querySelector('.ppg-bar');
    const icon = srcBtn.querySelector('.ppg-e-ico');
    srcBtn.style.viewTransitionName = 'ppg-plate';
    if (icon) icon.style.viewTransitionName = 'ppg-glyph';
    root.classList.add('ppg-vt');
    const vt = document.startViewTransition(() => {
      setForm('bar');
      setPlaying(true);
      setDocked(true);
      refreshEntriesAndRows();
      moveFab('bar', { instant: true });
      bar.style.viewTransitionName = 'ppg-plate';
      bar.querySelector('.ppg-play-ico').style.viewTransitionName = 'ppg-glyph';
    });
    window.PPG.vtReady = vt.ready;
    const t0 = performance.now();
    vt.ready.then(() => { window.PPG.vtReadyMs = performance.now() - t0; }).catch(() => {});
    vt.finished.finally(() => {
      bar.style.viewTransitionName = '';
      bar.querySelector('.ppg-play-ico').style.viewTransitionName = '';
      root.classList.remove('ppg-vt');
      if (kbd()) bar.querySelector('.ppg-play').focus({ preventScroll: true });
    });
  }

  /* bar → mini:条收拢成右下角小胶囊;封面与播放钮是共享元素。贴底条在开始时就把空间还给各栏 */
  function toMini() {
    finishFx();
    const pl = $p();
    const bar = pl.querySelector('.ppg-bar');
    const mini = pl.querySelector('.ppg-mini');
    if (motionOff()) { setDocked(false); setForm('mini'); moveFab('mini'); if (kbd()) mini.querySelector('.ppg-mini-art').focus({ preventScroll: true }); return; }
    const from = rectOf(bar);
    const barR = radiusOf(bar);
    const artFrom = rectOf(bar.querySelector('.ppg-cover'));
    const playFrom = rectOf(bar.querySelector('.ppg-play'));
    const icoFrom = rectOf(bar.querySelector('.ppg-play-ico'));      // 必须在条隐藏之前量
    const dur = 460;
    const ease = spring({ zeta: 0.86, response: 0.4, dur });
    setDocked(false, { flip: true, dur });
    setForm('mini');
    pl.dataset.assembling = 'mini';
    const to = rectOf(mini);
    const artTo = rectOf(mini.querySelector('.ppg-cover'));
    const playTo = rectOf(mini.querySelector('.ppg-play'));
    const fx = [];
    fx.push(morphShadow(from, to, { dur, easing: ease, radiusTo: to.h / 2 }));
    fx.push(morphBox('ppg-fx-plate', from, to, { radiusFrom: barR, radiusTo: to.h / 2, dur, easing: ease, under: true }));
    // 条上其它内容在原位快速淡出(副本),只留封面与播放钮飞过去
    const ghost = bar.querySelector('.ppg-bar-in').cloneNode(true);
    ghost.classList.add('ppg-fx-ghost');
    ghost.querySelectorAll('.ppg-art, .ppg-play').forEach((n) => { n.style.visibility = 'hidden'; });
    ghost.querySelectorAll('[aria-label], [role], [tabindex]').forEach((n) => { n.removeAttribute('role'); n.removeAttribute('tabindex'); });
    ghost.style.cssText = `transform:translate(${from.x}px, ${from.y}px);width:${from.w}px;height:${from.h}px`;
    // 副本要和真条吃同一条容器查询(一行 / 两行 / 三区),所以包一层与播放条容器同宽的查询容器
    const cq = document.createElement('div');
    cq.className = 'ppg-fx-cq';
    cq.style.width = `${pl.querySelector('.ppg-dock').offsetWidth}px`;
    cq.append(ghost);
    fxLayer(false).append(cq);
    fx.push({ el: ghost, a: ghost.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: MOTION.out, fill: 'both' }) });
    fx.push({ el: null, a: clipFollow(ghost, from, from, to, { radiusFrom: barR, radiusTo: to.h / 2, dur, easing: ease }) });
    fx.push(morphBox('ppg-fx-art', artFrom, artTo, { radiusFrom: 6, radiusTo: artTo.h / 2, dur, easing: ease }));
    fx[fx.length - 1].el.innerHTML = artHtml(P.item);
    fx.push(morphBox('ppg-fx-core is-tone', playFrom, playTo, { radiusFrom: playFrom.h / 2, radiusTo: playTo.h / 2, dur, easing: ease }));
    const glyph = document.createElement('span');
    glyph.className = 'ppg-fx-glyph is-tone';
    glyph.innerHTML = P.playing ? I.pause : I.play;
    fx.push(fly(glyph, icoFrom, rectOf(mini.querySelector('.ppg-play-ico')), { dur, easing: ease }));
    mini.querySelectorAll('.ppg-ring').forEach((r) => fx.push({ el: null, a: r.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, delay: dur * 0.55, fill: 'both' }) }));
    track(fx.map((f) => f.a));
    moveFab('mini');
    const focusMini = kbd() && (bar.contains(document.activeElement) || document.activeElement === document.body);
    Promise.all(fx.map((f) => f.a.finished)).then(() => {
      delete pl.dataset.assembling;
      fx.forEach((f) => { f.el?.remove(); f.a.cancel(); });
      cq.remove();
      fxRunning = [];
      if (focusMini) mini.querySelector('.ppg-mini-art').focus({ preventScroll: true });
    }).catch(() => {});
  }

  /* mini → bar:反向展开;贴底条落地那一帧才占位 */
  function toBar() {
    finishFx();
    const pl = $p();
    const bar = pl.querySelector('.ppg-bar');
    const mini = pl.querySelector('.ppg-mini');
    if (motionOff()) { setForm('bar'); setDocked(true); moveFab('bar'); if (kbd()) bar.querySelector('.ppg-play').focus({ preventScroll: true }); return; }
    const from = rectOf(mini);
    const artFrom = rectOf(mini.querySelector('.ppg-cover'));
    const playFrom = rectOf(mini.querySelector('.ppg-play'));
    const icoFrom = rectOf(mini.querySelector('.ppg-play-ico'));
    setForm('bar');
    pl.dataset.assembling = 'bar';
    const to = rectOf(bar);
    const barR = radiusOf(bar);
    const artTo = rectOf(bar.querySelector('.ppg-cover'));
    const playTo = rectOf(bar.querySelector('.ppg-play'));
    const dur = 500;
    // 贴底条位:临界阻尼——条的左缘就是视图轨的边,过冲会压到轨上再弹回
    const ease = spring({ zeta: docks() ? 1 : 0.84, response: docks() ? 0.36 : 0.42, dur });
    const fx = [];
    fx.push(morphShadow(from, to, { dur, easing: ease, radiusTo: barR, endOpacity: shadowOf(bar) ? 1 : 0 }));
    fx.push(morphBox('ppg-fx-plate', from, to, { radiusFrom: from.h / 2, radiusTo: barR, dur, easing: ease, under: true }));
    fx.push({ el: null, a: clipFollow(bar, to, from, to, { radiusFrom: from.h / 2, radiusTo: barR, dur, easing: ease }) });
    fx.push(morphBox('ppg-fx-art', artFrom, artTo, { radiusFrom: artFrom.h / 2, radiusTo: 6, dur, easing: ease }));
    fx[fx.length - 1].el.innerHTML = artHtml(P.item);
    fx.push(morphBox('ppg-fx-core is-tone', playFrom, playTo, { radiusFrom: playFrom.h / 2, radiusTo: playTo.h / 2, dur, easing: ease }));
    const glyph = document.createElement('span');
    glyph.className = 'ppg-fx-glyph is-tone';
    glyph.innerHTML = P.playing ? I.pause : I.play;
    fx.push(fly(glyph, icoFrom, rectOf(bar.querySelector('.ppg-play-ico')), { dur, easing: ease }));
    const parts = [bar.querySelector('.ppg-info-slot'), ...bar.querySelectorAll('.ppg-skip'), bar.querySelector('.ppg-seek'), bar.querySelector('.ppg-rate'), bar.querySelector('.ppg-min'), bar.querySelector('.ppg-close')];
    parts.forEach((el, i) => {
      // 等飞行中的封面基本落位(弹簧进度 ≈95%)再淡入文字,封面不压字
      if (el) fx.push({ el: null, a: el.animate([{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], { duration: 220, delay: dur * 0.5 + i * 18, easing: MOTION.fade, fill: 'both' }) });
    });
    track(fx.map((f) => f.a));
    moveFab('bar');
    Promise.all(fx.map((f) => f.a.finished)).then(() => {
      delete pl.dataset.assembling;
      fx.forEach((f) => { f.el?.remove(); f.a.cancel(); });
      fxRunning = [];
      setDocked(true, { flip: true, dur: 300 });
      if (kbd()) bar.querySelector('.ppg-play').focus({ preventScroll: true });
    }).catch(() => {});
  }

  /* 换台:条不消失,条内内容竖向轮换;▶ 从被点的入口飞进条上的播放钮,把动作和结果连起来;封面色交叉淡变 */
  function swapItem(item, srcBtn) {
    finishFx();
    const pl = $p();
    const host = pl.querySelector(P.form === 'mini' ? '.ppg-mini' : '.ppg-bar');
    // 入口马上要重建,先量它的 ▶(脱离文档后量出来是 0×0)
    const srcIco = srcBtn?.querySelector('.ppg-e-ico');
    const icoFrom = srcIco ? rectOf(srcIco) : null;
    const oldInfo = pl.querySelector('.ppg-info-slot').innerHTML;
    const oldArt = host.querySelector('.ppg-art-slot').innerHTML;
    applyTone(item, { fade: true });
    fillItem(item);
    paintTime(true);
    setPlaying(true);
    refreshEntriesAndRows();
    if (motionOff()) return;
    const fx = [];
    const ticker = (slot, oldHtml, dy) => {
      if (!slot) return;
      const r = rectOf(slot);
      const ghost = document.createElement('span');
      ghost.className = `ppg-fx-ticker ${slot.className}`;
      ghost.innerHTML = oldHtml;
      ghost.style.cssText = `transform:translate(${r.x}px, ${r.y}px);width:${r.w}px;height:${r.h}px`;
      fxLayer(false).append(ghost);
      fx.push({ el: ghost, a: ghost.animate([{ transform: `translate(${r.x}px, ${r.y}px)`, opacity: 1 }, { transform: `translate(${r.x}px, ${r.y - dy}px)`, opacity: 0 }], { duration: 180, easing: MOTION.out, fill: 'both' }) });
      fx.push({ el: null, a: slot.animate([{ transform: `translateY(${dy}px)`, opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 300, delay: 70, easing: MOTION.settle, fill: 'both' }) });
    };
    if (P.form === 'bar') ticker(pl.querySelector('.ppg-info-slot'), oldInfo, 10);
    ticker(host.querySelector('.ppg-art-slot'), oldArt, 10);
    // ▶ 小飞行(只有入口在视口里才飞)
    if (icoFrom && icoFrom.w) {
      const from = icoFrom;
      const target = rectOf(host.querySelector('.ppg-play-ico'));
      if (from.y + from.h > 0 && from.y < innerHeight) {
        const glyph = document.createElement('span');
        glyph.className = 'ppg-fx-glyph is-token';
        glyph.innerHTML = I.play;
        const f = fly(glyph, from, target, { dur: 420, easing: spring({ zeta: 0.9, response: 0.36, dur: 420 }), fade: [1, 0.55] });
        fx.push(f);
        fx.push({ el: null, a: host.querySelector('.ppg-play').animate([{ transform: 'none' }, { transform: 'scale(1.12)' }, { transform: 'none' }], { duration: 260, delay: 300, easing: MOTION.fade }) });
      }
    }
    track(fx.map((f) => f.a));
    Promise.all(fx.map((f) => f.a.finished)).then(() => {
      fx.forEach((f) => { f.el?.remove(); f.a.cancel(); });
      fxRunning = [];
    }).catch(() => {});
  }

  /* 问哆啦美浮钮避让(只动 transform):按条 / 迷你的实际矩形算——条与浮钮横向重叠就让到条顶之上 12px;迷你时让到迷你左边、中线对齐 */
  function fabTarget(form) {
    const slot = document.querySelector('.ppg-fab-slot');
    const pl = $p();
    const saved = slot.style.transform;
    slot.style.transform = 'none';
    const fab = slot.getBoundingClientRect();
    slot.style.transform = saved;
    if (form === 'bar') {
      const r = pl.querySelector('.ppg-bar').getBoundingClientRect();
      if (r.height && r.left < fab.right && r.right > fab.left) return { x: 0, y: Math.round(r.top - 12 - fab.bottom) };
    } else if (form === 'mini') {
      const r = pl.querySelector('.ppg-mini').getBoundingClientRect();
      if (r.width) return { x: Math.round(r.left - 10 - fab.right), y: Math.round(r.top + r.height / 2 - (fab.top + fab.height / 2)) };
    }
    return { x: 0, y: 0 };
  }
  // 浮钮挪到静止的指针下面时(典型:点 ✕ 关闭后浮钮落回右下角,正好在指针下),不让它因此「被悬停」展开成带字胶囊:
  // 落点盖住指针就先不接指针事件,等指针真的动了再恢复
  const ptr = { x: -1, y: -1 };
  window.addEventListener('pointermove', (e) => { ptr.x = e.clientX; ptr.y = e.clientY; }, { passive: true, capture: true });
  window.addEventListener('pointerdown', (e) => { ptr.x = e.clientX; ptr.y = e.clientY; }, { passive: true, capture: true });
  function settleFab(slot) {
    const r = slot.getBoundingClientRect();
    if (!(ptr.x >= r.left && ptr.x <= r.right && ptr.y >= r.top && ptr.y <= r.bottom)) return;
    slot.classList.add('is-settling');
    const at = { x: ptr.x, y: ptr.y };
    const off = (e) => {
      if (Math.abs(e.clientX - at.x) + Math.abs(e.clientY - at.y) < 2) return;
      slot.classList.remove('is-settling');
      window.removeEventListener('pointermove', off, true);
    };
    window.addEventListener('pointermove', off, true);
  }
  function moveFab(form, { instant = false } = {}) {
    const slot = document.querySelector('.ppg-fab-slot');
    if (!slot) return;
    // 发现页不挂浮钮(display:none):量不出位置,记住「藏过」,回来时直接落位不补动画
    if (!slot.offsetParent) { slot.dataset.hidden = '1'; return; }
    const wasHidden = slot.dataset.hidden === '1';
    delete slot.dataset.hidden;
    const prev = { x: Number(slot.dataset.fx || 0), y: Number(slot.dataset.fy || 0) };
    const prevForm = slot.dataset.form || 'none';
    const to = fabTarget(form);
    const tf = (p) => (p.x || p.y ? `translate(${p.x}px, ${p.y}px)` : 'none');
    slot.style.transform = tf(to);
    slot.dataset.fx = to.x; slot.dataset.fy = to.y; slot.dataset.form = form;
    settleFab(slot);
    if (instant || wasHidden || motionOff() || (prev.x === to.x && prev.y === to.y)) return;
    if ((prevForm === 'bar' && form === 'mini') || (prevForm === 'mini' && form === 'bar')) {
      // 条 ⇄ 迷你:走直角路径(先横后竖 / 先竖后横),不斜穿正在成形的迷你
      const corner = prevForm === 'bar' ? { x: to.x, y: prev.y } : { x: prev.x, y: to.y };
      slot.animate([{ transform: tf(prev) }, { transform: tf(corner), offset: 0.5 }, { transform: tf(to) }], { duration: 520, easing: 'cubic-bezier(.3, .1, .2, 1)' });
    } else {
      // 带延时的动画要 fill: backwards,否则延时期间先露出终点、再跳回起点开始走
      slot.animate([{ transform: tf(prev) }, { transform: tf(to) }], { duration: MOTION.settleDur, easing: MOTION.settle, delay: form === 'none' ? 80 : 120, fill: 'backwards' });
    }
  }

  /* 条位 / 配色 / 容器的运行时切换(外壳切换时不重载画框,播放不中断) */
  function setLayout(layout) {
    if (!isDesktop() || !LAYOUTS.includes(layout) || layout === S.layout) return;
    finishFx();
    S.layout = layout;
    $app().dataset.layout = layout;
    setDocked(P.form === 'bar');
    moveFab(P.form, { instant: true });
    if (P.item) applyTone(P.item);   // 底色基准随条位变(贴底 = 外框色,浮层 = 抬起面),遮罩浓度要重算
  }
  function setTone(tone) {
    if (!TONES.includes(tone)) return;
    S.tone = tone;
    $app().dataset.tone = tone;
    if (P.item) applyTone(P.item);
  }
  function setContainer(c) {
    if (!CONTAINERS.includes(c)) return;
    if (!isDesktop() && !['podcast', 'article', 'social'].includes(c)) return;
    // 阅读窗浮条锚在「当前主区域」:换到社交 / 发现时主区域变了,条横向滑过去(FLIP,只动 transform)
    const pl = $p();
    const dock = pl.querySelector('.ppg-dock');
    const before = dock.getBoundingClientRect().left;
    S.container = c;
    if (c === 'podcast' || c === 'article') {
      if (S.readingContainer !== c) {
        S.readingContainer = c;
        S.activeId = listOf(c)[0].id;
        if (c === 'article') S.activeId = 'web_ithome_ai_bffbb55b2c73f8de';
        if (c === 'podcast') S.activeId = 'podcast_interconnects_2201ca715e09ca60';
        if (isDesktop()) renderPane();
      }
    }
    if (isDesktop()) refreshDesktopChrome(); else refreshMobileChrome();
    const dx = before - dock.getBoundingClientRect().left;
    // 浮钮按条的「新位置」算避让:必须在条的 FLIP 动画开始之前量(动画里的 transform 会算进矩形)
    moveFab(P.form);
    if (Math.abs(dx) > 0.5 && !motionOff() && P.form !== 'none') {
      [dock, pl.querySelector('.ppg-scrim')].forEach((el) => el.animate([{ transform: `translateX(${dx}px)` }, { transform: 'none' }], { duration: MOTION.settleDur, easing: MOTION.settle }));
    }
  }

  function syncThemeIcon() {
    const btn = document.querySelector('[data-act="theme"]');
    if (!btn) return;
    const dark = root.dataset.theme === 'dark';
    btn.innerHTML = `${dark ? I.sun : I.moon}<span class="reader-vrail-label">主题</span>`;
    btn.setAttribute('aria-label', dark ? '切换到亮色' : '切换到暗色');
  }

  /* ════════════════ 事件 ════════════════ */
  function bindPlayer(app) {
    app.addEventListener('click', (ev) => {
      const t = ev.target.closest('[data-act], [data-pact]');
      if (!t) return;
      const act = t.dataset.act;
      if (act === 'open') {
        const id = t.dataset.id;
        if (isDesktop()) { S.activeId = id; renderPane(); refreshList(); }
        else pushRead(id);
      } else if (act === 'back') {
        popRead();
      } else if (act === 'container') {
        setContainer(t.dataset.container);
      } else if (act === 'listen') {
        listen(t.dataset.id, t.dataset.track, t);
      } else if (act === 'request') {
        S.ondemand[t.dataset.id] = 'generating';
        refreshEntriesAndRows();
      } else if (act === 'view') {
        S.readView = t.dataset.view;
        const host = isDesktop() ? document.querySelector('[data-slot="pane"]') : document.querySelector('.m-read-scroll');
        const body = host.querySelector('.reader-pane-body, .m-read-body');
        body.innerHTML = bodyHtml(byId(S.activeId));
        body.querySelector(`[data-view="${S.readView}"]`)?.focus({ preventScroll: true });
      } else if (act === 'soc-more') {
        const p = t.previousElementSibling;
        const open = p.classList.toggle('is-clamped');
        t.textContent = open ? '显示更多' : '收起';
      } else if (act === 'theme') {
        if (root.dataset.theme === 'dark') delete root.dataset.theme; else root.dataset.theme = 'dark';
        syncThemeIcon();
        if (P.item) applyTone(P.item);
      }
      const pact = t.dataset.pact;
      if (!pact || !P.item) return;
      if (pact === 'toggle') setPlaying(!P.playing);
      else if (pact === 'back') seek(P.t - 15);
      else if (pact === 'fwd') seek(P.t + 15);
      else if (pact === 'rate') {
        P.rate = (P.rate + 1) % RATES.length;
        app.querySelectorAll('[data-pact="rate"]').forEach((b) => { b.textContent = `${RATES[P.rate]}×`; b.setAttribute('aria-label', `播放速度 ${RATES[P.rate]} 倍`); b.classList.toggle('is-on', P.rate > 0); });
      } else if (pact === 'min') toMini();
      else if (pact === 'expand') toBar();
      else if (pact === 'close') closePlayer();
      else if (pact === 'goto') {
        S.activeId = P.item.entryId;
        const c = byId(P.item.entryId).kind === 'podcast' ? 'podcast' : 'article';
        S.readingContainer = c;
        if (isDesktop()) { setContainer(c); renderPane(); refreshList(); }
        else { S.container = c; refreshMobileChrome(); if (S.page === 'read') renderPane(); else pushRead(P.item.entryId); }
      }
    });
    // 进度条:点击 / 拖动定位,← → 各 5 秒,空格 / 回车播放暂停
    const tr = app.querySelector('.ppg-track-in');
    const seekTo = (x) => { const r = tr.getBoundingClientRect(); seek(Math.max(0, Math.min(1, (x - r.left) / r.width)) * P.item.seconds); };
    tr.addEventListener('pointerdown', (e) => {
      if (!P.item) return;
      tr.setPointerCapture(e.pointerId); seekTo(e.clientX); tr.dataset.dragging = '';
      const move = (ev) => seekTo(ev.clientX);
      const up = () => { tr.removeEventListener('pointermove', move); delete tr.dataset.dragging; };
      tr.addEventListener('pointermove', move);
      tr.addEventListener('pointerup', up, { once: true });
      tr.addEventListener('pointercancel', up, { once: true });
    });
    tr.addEventListener('keydown', (e) => {
      if (!P.item) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); seek(P.t + (e.key === 'ArrowRight' ? 5 : -5)); }
      else if (e.key === 'Home') { e.preventDefault(); seek(0); }
      else if (e.key === 'End') { e.preventDefault(); seek(P.item.seconds); }
      else if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); setPlaying(!P.playing); }
    });
    // 手机:在条上往下拖 = 缩小(可选手势;⌄ 按钮始终在)。条跟手(阻尼 0.6),拖过 36px 松手就从拖到的位置形变成迷你,
    // 不够就弹回;从封面 / 标题起拖也行(拖动后吞掉那一次点击,不误触「打开这一期」)
    if (!isDesktop()) {
      const bar = app.querySelector('.ppg-bar');
      let g = null;
      bar.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || e.target.closest('.ppg-track-in, .ppg-ctrl button, .ppg-row2 button')) return;
        g = { y0: e.clientY, dy: 0, id: e.pointerId };
      });
      window.addEventListener('pointermove', (e) => {
        if (!g || e.pointerId !== g.id) return;
        g.dy = Math.max(0, e.clientY - g.y0);
        if (g.dy > 4) bar.style.transform = `translateY(${(g.dy * 0.6).toFixed(1)}px)`;
      });
      const end = (e) => {
        if (!g || (e && e.pointerId !== g.id)) return;
        const { dy } = g;
        g = null;
        if (dy > 8) {
          const eat = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
          window.addEventListener('click', eat, { capture: true, once: true });
          setTimeout(() => window.removeEventListener('click', eat, { capture: true }), 350);
        }
        if (dy > 36) { toMini(); bar.style.transform = ''; return; }
        if (!bar.style.transform) return;
        const from = bar.style.transform;
        bar.style.transform = '';
        if (!motionOff()) bar.animate([{ transform: from }, { transform: 'none' }], { duration: 320, easing: MOTION.settle });
      };
      window.addEventListener('pointerup', end);
      window.addEventListener('pointercancel', end);
    }
    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type === 'ppg-theme') { if (d.theme === 'dark') root.dataset.theme = 'dark'; else delete root.dataset.theme; syncThemeIcon(); if (P.item) applyTone(P.item); }
      if (d.type === 'ppg-motion') { if (d.motion === 'reduce') root.dataset.motion = 'reduce'; else delete root.dataset.motion; }
      if (d.type === 'ppg-layout') setLayout(d.layout);
      if (d.type === 'ppg-tone') setTone(d.tone);
      if (d.type === 'ppg-container') setContainer(d.container);
    });
    window.addEventListener('resize', () => { syncPlayerOffset(); if (P.form !== 'none') moveFab(P.form, { instant: true }); });
    syncPlayerOffset();
    // 渐隐与占位跟着条的实际高度走(一行 60 / 两行约 90 / 宽条 72)
    const barEl = app.querySelector('.ppg-bar');
    new ResizeObserver(() => { if (barEl.offsetHeight) $app().style.setProperty('--ppg-bar-h', `${barEl.offsetHeight}px`); }).observe(barEl);
  }

  /* ── 对比度审计:逐配色 × 逐封面(6 档播客原节目 + 文章导读),读真实计算色,交给 canvas 换算成 sRGB 再算 WCAG 对比度 ──
     文字(标题 / 副行 / 时间 / AI 生成)≥ 4.5,图标(±15 / 倍速 / 缩小 / 关闭)与播放钮外缘、进度已播段 ≥ 3。
     封面模糊:取模糊位图逐像素叠上遮罩后的最差像素(保守口径)。 */
  async function audit() {
    const pl = $p();
    const bar = pl.querySelector('.ppg-bar');
    const keep = { item: P.item, tone: S.tone, form: P.form };
    if (P.form !== 'bar') { setForm('bar'); }
    await Promise.all(ENTRIES.filter((e) => e.cover).map((e) => analyzeCover(e.cover).then((r) => { if (r) toneSync.set(e.cover, r); })));
    const items = ENTRIES.filter((e) => e.kind === 'podcast').map((e) => makeItem(e.id, 'original'));
    items.push(makeItem('web_ithome_ai_bffbb55b2c73f8de', 'narration'));
    items.push(makeItem('podcast_interconnects_2201ca715e09ca60', 'digest'));   // 有封面的 AI 音轨:「AI 生成」压在封面色上
    const out = {};
    for (const tone of TONES) {
      setTone(tone);
      // 播放钮 / 图标的颜色带 150ms 过渡:等过渡走完再读计算色
      await new Promise((r) => setTimeout(r, 360));
      out[tone] = [];
      for (const item of items) {
        fillItem(item);
        applyTone(item);
        await new Promise((r) => requestAnimationFrame(() => r()));
        const g = (sel, prop = 'color') => getComputedStyle(pl.querySelector(sel))[prop];
        const bgColor = getComputedStyle(bar).backgroundColor;
        const base = probe(bgColor);
        let bgs = [base];
        const r = item.cover ? toneSync.get(item.cover) : null;
        if (tone === 'blur' && r) {
          const veil = resolveColor(getComputedStyle(pl).getPropertyValue('--ppg-veil'));
          bgs = [];
          for (let i = 0; i < r.pixels.length; i += 4) bgs.push(probe(veil, [r.pixels[i], r.pixels[i + 1], r.pixels[i + 2]]));
        }
        const worst = (color) => Math.min(...bgs.map((b) => C.contrast(probe(color, b), b)));
        const playBg = getComputedStyle(pl.querySelector('.ppg-bar .ppg-play')).backgroundColor;
        const row = {
          item: item.ai ? `${item.sub}(${item.track === 'digest' ? byId(item.entryId).source : '文章'})` : byId(item.entryId).source,
          src: pl.dataset.toneSrc,
          title: worst(g('.ppg-title')),
          sub: worst(g('.ppg-sub')),
          time: worst(g('.ppg-cur')),
          icon: worst(g('.ppg-bar .ppg-close')),
          playEdge: Math.min(...bgs.map((b) => C.contrast(probe(playBg, b), b))),
          // 播放钮描边(只有暗色品牌淡底有):外缘取「实心 / 描边」两者中对比更高的一层
          ring: (() => { const v = getComputedStyle(pl).getPropertyValue('--ppg-play-ring').trim(); return v ? Math.min(...bgs.map((b) => C.contrast(probe(resolveColor(v), b), b))) : null; })(),
          glyph: C.contrast(probe(g('.ppg-bar .ppg-play'), probe(playBg)), probe(playBg)),
          fill: worst(getComputedStyle(pl.querySelector('.ppg-bar .ppg-fill')).backgroundColor),
          ai: item.ai ? worst(g('.ppg-bar .ppg-ai')) : null,
          bg: bgColor,
          veil: getComputedStyle(pl).getPropertyValue('--ppg-veil-p').trim() || null,
        };
        Object.keys(row).forEach((k) => { if (typeof row[k] === 'number') row[k] = Math.round(row[k] * 100) / 100; });
        out[tone].push(row);
      }
    }
    // 复原
    setTone(keep.tone);
    if (keep.item) { fillItem(keep.item); applyTone(keep.item); paintTime(true); }
    setForm(keep.form);
    return out;
  }

  /* ── 封面取色的全色相扫描:取色结果被夹在 L = 0.62、0.04 ≤ C ≤ 0.14 的一圈上,所以把这一圈(每 5° 一档 × 三档彩度)
     逐档写进 --ppg-cover 算一遍,就覆盖了任意封面的最坏情况,不只是样页里这 6 张 ── */
  async function auditSweep() {
    const pl = $p();
    const keep = { tone: S.tone, form: P.form, cover: pl.style.getPropertyValue('--ppg-cover') };
    if (P.form !== 'bar') setForm('bar');
    setTone('cover');
    await new Promise((r) => setTimeout(r, 360));
    const g = (sel) => probe(resolveColor(getComputedStyle(pl.querySelector(sel)).color));
    const worst = {};
    const keys = { title: '.ppg-title', sub: '.ppg-sub', time: '.ppg-cur', icon: '.ppg-bar .ppg-close' };
    for (const c of [0.04, 0.09, 0.14]) for (let h = 0; h < 360; h += 5) {
      pl.style.setProperty('--ppg-cover', `oklch(0.62 ${c} ${h})`);
      const bg = probe(resolveColor(getComputedStyle(pl.querySelector('.ppg-bar')).backgroundColor));
      const play = probe(resolveColor(getComputedStyle(pl.querySelector('.ppg-bar .ppg-play')).backgroundColor));
      const fill = probe(resolveColor(getComputedStyle(pl.querySelector('.ppg-bar .ppg-fill')).backgroundColor));
      const vals = { playEdge: C.contrast(play, bg), fill: C.contrast(fill, bg) };
      Object.entries(keys).forEach(([k, sel]) => { vals[k] = C.contrast(g(sel), bg); });
      Object.entries(vals).forEach(([k, v]) => { if (!worst[k] || v < worst[k].v) worst[k] = { v: Math.round(v * 100) / 100, h, c }; });
    }
    if (keep.cover) pl.style.setProperty('--ppg-cover', keep.cover); else pl.style.removeProperty('--ppg-cover');
    setTone(keep.tone);
    setForm(keep.form);
    return worst;
  }

  /* ── 测试钩子:逐帧截图时冻结 / 定位当前形变 ── */
  window.PPG = {
    morph: MORPH,
    state: () => ({ form: P.form, item: P.item && P.item.key, playing: P.playing, t: Math.round(P.t * 10) / 10, rate: RATES[P.rate], active: S.activeId, page: S.page, container: S.container, readView: S.readView, layout: S.layout, tone: S.tone, docked: !!$app()?.hasAttribute('data-docked') }),
    pauseFx() { document.getAnimations().forEach((a) => a.pause()); },
    seekFx(ms) { document.getAnimations().forEach((a) => { a.currentTime = ms; }); },
    resumeFx() { document.getAnimations().forEach((a) => a.play()); },
    setLayout, setTone, setContainer, audit, auditSweep,
    tones: () => [...toneSync.entries()].map(([k, v]) => ({ cover: k, tone: v.tone, ms: v.ms, blurBytes: v.blurBytes })),
    vtReady: null,
  };

  prepareTones();
  if (DEVICE === 'desktop') renderDesktop(); else renderMobile();

  /* ════════════════ 对照外壳 ════════════════ */
  function host() {
    document.body.classList.add('ppg-host');
    const state = {
      theme: q.get('theme') === 'light' ? 'light' : 'dark',
      motion: q.get('motion') === 'reduce' ? 'reduce' : 'full',
      morph: q.get('morph') === 'vt' ? 'vt' : 'waapi',
      layout: pick(q.get('layout'), LAYOUTS, 'pane'),
      tone: pick(q.get('tone'), TONES, 'neutral'),
      container: pick(q.get('container'), CONTAINERS, 'podcast'),
      width: pick(q.get('width'), ['1280', '1440', '1920'], '1440'),
    };
    const qs = (device) => `device=${device}&theme=${state.theme}&layout=${state.layout}&tone=${state.tone}&container=${state.container}${state.motion === 'reduce' ? '&motion=reduce' : ''}${state.morph === 'vt' ? '&morph=vt' : ''}`;
    const seg = (k, label, opts) => `<span><span class="ppg-host-label">${label}</span><span class="ppg-host-seg" data-k="${k}">${opts.map(([v, t]) => `<button data-v="${v}">${t}</button>`).join('')}</span></span>`;
    document.getElementById('ppg-root').innerHTML = `<div class="ppg-host-wrap">
      <div class="ppg-host-head"><h1>全局播放条</h1><span>issue #199 · 方案 C「随行」升级:标题区入口 → 底部条 → 迷你,跨页持续播放</span></div>
      <div class="ppg-host-bar">
        ${seg('layout', '条位', [['pane', '阅读窗浮条'], ['dock', '内容区贴底'], ['float', '内容区浮层'], ['full', '全宽贴底']])}
        ${seg('tone', '配色', [['neutral', '中性'], ['cover', '封面取色'], ['blur', '封面模糊'], ['inverse', '反色'], ['brand', '品牌淡底']])}
      </div>
      <div class="ppg-host-bar">
        ${seg('container', '容器', [['podcast', '播客'], ['article', '文章'], ['social', '社交'], ['discover', '发现']])}
        ${seg('width', '宽度', [['1280', '1280'], ['1440', '1440'], ['1920', '1920']])}
        ${seg('theme', '主题', [['dark', '暗'], ['light', '亮']])}
        ${seg('motion', '动效', [['full', '完整'], ['reduce', '减少']])}
        ${seg('morph', '形变路线', [['waapi', 'WAAPI'], ['vt', 'View Transitions']])}
        <button type="button" class="ppg-host-reset">重置两个画框</button>
      </div>
      <ul class="ppg-host-notes">
        <li>点标题区「收听原节目」看形变;播放中点别的条目、切「文章」「社交」「发现」,条一直在;条上 ⌄ 缩成迷你,点迷你封面展开,✕ 关闭。条位、配色、容器、宽度切换不重载画框。</li>
        <li>Interconnects 有「节目简介 | 导读稿」视图切换,与播放哪条音轨无关;IT之家「阶跃发布 StepAudio 3」演示文章「听导读」走同一条。社交流与发现页是沙箱库里的真实推文与来源目录。</li>
      </ul>
      <div class="ppg-host-stage">
        <div class="ppg-host-frame is-desktop"><span data-slot="dlabel">桌面</span><div class="ppg-host-scale"><iframe id="ppg-f-desktop" title="桌面阅读器" height="900"></iframe></div></div>
        <div class="ppg-host-frame is-mobile"><span>手机 · 390 × 844</span><iframe id="ppg-f-mobile" title="手机阅读器" width="390" height="844"></iframe></div>
      </div>
    </div>`;
    const fD = document.getElementById('ppg-f-desktop');
    const fM = document.getElementById('ppg-f-mobile');
    const post = (msg) => [fD, fM].forEach((f) => f.contentWindow?.postMessage(msg, '*'));
    const fit = () => {
      const w = Number(state.width);
      fD.width = String(w);
      document.querySelector('[data-slot="dlabel"]').textContent = `桌面 · ${w} × 900`;
      const stage = document.querySelector('.ppg-host-stage');
      const avail = stage.clientWidth;
      const side = avail >= w * 0.5 + 390 + 24 ? avail - 390 - 24 : avail;
      const s = Math.min(1, side / w);
      const sc = document.querySelector('.ppg-host-scale');
      sc.style.setProperty('--s', s.toFixed(4));
      sc.style.setProperty('--w', `${w}px`);
    };
    const sync = (reload) => {
      if (state.theme === 'dark') document.documentElement.dataset.theme = 'dark'; else delete document.documentElement.dataset.theme;
      document.querySelectorAll('.ppg-host-seg').forEach((sg) => sg.querySelectorAll('button').forEach((b) => {
        const on = b.dataset.v === state[sg.dataset.k];
        b.classList.toggle('is-on', on); b.setAttribute('aria-pressed', String(on));
      }));
      if (reload) { fD.src = `global.html?${qs('desktop')}`; fM.src = `global.html?${qs('mobile')}`; }
      fit();
      history.replaceState(null, '', `?${qs('').replace(/^device=&/, '')}&width=${state.width}`);
    };
    document.querySelectorAll('.ppg-host-seg').forEach((sg) => sg.addEventListener('click', (e) => {
      const v = e.target.closest('button')?.dataset.v;
      if (!v) return;
      const k = sg.dataset.k;
      state[k] = v;
      if (k === 'theme') post({ type: 'ppg-theme', theme: v });
      else if (k === 'motion') post({ type: 'ppg-motion', motion: v === 'reduce' ? 'reduce' : '' });
      else if (k === 'layout') post({ type: 'ppg-layout', layout: v });
      else if (k === 'tone') post({ type: 'ppg-tone', tone: v });
      else if (k === 'container') post({ type: 'ppg-container', container: v });
      sync(k === 'morph');
    }));
    document.querySelector('.ppg-host-reset').addEventListener('click', () => sync(true));
    window.addEventListener('resize', fit);
    sync(true);
  }
})();
