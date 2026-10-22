/* 哆啦美登录页 · 方向 C「像素」样页脚本
 *
 * 一张低分辨率 Canvas(每格 = 整数设备像素)承载整个场景:Bayer 抖动夜空、网格星点、
 * 像素吉祥物(5 帧精灵 + 代码驱动的目镜扫光 / 进度 / 摇头)、两层城市剪影、水面月光带。
 * 界面层(标题、表单)是矢量 DOM,开场用「像素对焦」与「抖动显影」接入同一网格。
 * 动效档位读 <html data-motion>,监听 sample:motion;document.hidden 时停帧。
 */
(() => {
  'use strict';

  // ───────────────────────── 工具 ─────────────────────────
  const $ = (s) => document.querySelector(s);
  const root = document.documentElement;
  const reduced = () => root.dataset.motion === 'reduce';
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const prog = (t, a, b) => clamp((t - a) / (b - a), 0, 1);
  const easeOut = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
  const easeInOut = (t) => { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; };
  const now = () => performance.now();

  function mulberry(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function bayer(n) {
    let m = [[0]];
    while (m.length < n) {
      const s = m.length;
      const r = [];
      for (let y = 0; y < s * 2; y++) {
        const row = [];
        for (let x = 0; x < s * 2; x++) {
          const q = y < s ? (x < s ? 0 : 2) : (x < s ? 3 : 1);
          row.push(m[y % s][x % s] * 4 + q);
        }
        r.push(row);
      }
      m = r;
    }
    return Float32Array.from(m.flat(), (v) => (v + 0.5) / (n * n));
  }
  const B4 = bayer(4);
  const B8 = bayer(8);
  const b4 = (x, y) => B4[((y & 3) << 2) | (x & 3)];
  const b8 = (x, y) => B8[((y & 7) << 3) | (x & 7)];

  // '#rrggbb' → 小端 RGBA 打包的 uint32
  const C = (h) => {
    const n = parseInt(h.slice(1), 16);
    return ((255 << 24) | ((n & 255) << 16) | (((n >> 8) & 255) << 8) | (n >> 16)) >>> 0;
  };
  const unpack = (c) => [c & 255, (c >>> 8) & 255, (c >>> 16) & 255];
  const pack = (r, g, b) => ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0;
  const mix = (c1, c2, t) => {
    const a = unpack(c1), b = unpack(c2);
    return pack(Math.round(a[0] + (b[0] - a[0]) * t), Math.round(a[1] + (b[1] - a[1]) * t), Math.round(a[2] + (b[2] - a[2]) * t));
  };

  // ───────────────────────── 调色板 ─────────────────────────
  // 夜空 11 阶:顶部近黑 → 地平线墨靛;最后两阶只给吉祥物身后的光晕用(品牌靛 #5252c5 的暗部)
  const SKY = ['#05041a', '#080723', '#0c0a2c', '#100e36', '#151241', '#1b174e', '#221d5c', '#2a246b', '#322b7a', '#3a3287', '#433a95'].map(C);
  const INK = SKY[0];
  const P = {
    star: [C('#4b4796'), C('#7a76c4'), C('#bdb9f2'), C('#f6f4ff')],
    cyan: C('#7deeff'), magenta: C('#ff86e4'),
    gold: C('#ffd36e'), goldDim: C('#a7863f'), ice: C('#c9f6ff'), iceDim: C('#5f8fb3'),
    far: C('#17143f'), farRim: C('#272163'),
    near: C('#0c0a29'), nearRim: C('#18154a'),
    winWarm: C('#f2c46c'), winWarmHi: C('#ffe3a3'), winCool: C('#a19df7'),
    winFar: C('#8a7457'), winFarCool: C('#5e5aa8'),
    beacon: C('#ff5d73'), beaconDim: C('#6b2448'),
    shore: C('#2a246b'), water: [C('#0c0a2b'), C('#090823'), C('#06051c'), C('#05041a')],
    path: [C('#433a95'), C('#322b7a'), C('#241f5f')],
    rim: C('#3f3896'),
    glint: C('#f4d9ff'), glintCore: C('#ffffff'),
    hudFill: C('#52effc'),
    reader: C('#f4f6f9'),
  };

  // ───────────────────────── 精灵 ─────────────────────────
  const SW = 73, SH = 90, HEAD_ROWS = 70;
  const FRAME = { idle: 0, blink: 1, happy: 2, cover: 3, oops: 4 };
  const frames = [];            // Uint32Array(SW*SH) × 5
  let visorMask = null;         // 目镜玻璃格(扫光用)
  const sprite = new Image();
  sprite.decoding = 'async';
  const spriteReady = new Promise((res) => {
    sprite.onload = () => {
      const c = document.createElement('canvas');
      c.width = sprite.width; c.height = sprite.height;
      const x = c.getContext('2d', { willReadFrequently: true });
      x.drawImage(sprite, 0, 0);
      const all = new Uint32Array(x.getImageData(0, 0, c.width, c.height).data.buffer);
      const n = Math.round(sprite.width / SW);
      for (let f = 0; f < n; f++) {
        const buf = new Uint32Array(SW * SH);
        for (let y = 0; y < SH; y++) {
          for (let xx = 0; xx < SW; xx++) {
            const v = all[y * c.width + f * SW + xx];
            buf[y * SW + xx] = (v >>> 24) > 127 ? (v | 0xff000000) >>> 0 : 0;
          }
        }
        frames.push(buf);
      }
      // 目镜玻璃:x 37–57、y 36–54 内的浅紫 / 品红格(不含眼睛的深色格)
      visorMask = new Uint8Array(SW * SH);
      const idle = frames[0];
      for (let y = 36; y <= 54; y++) {
        for (let xx = 37; xx <= 58; xx++) {
          const v = idle[y * SW + xx];
          if (!v) continue;
          const [r, g, b] = unpack(v);
          if (b > 200 && r > 150 && g < 200) visorMask[y * SW + xx] = 1;
        }
      }
      res(true);
    };
    sprite.onerror = () => res(false);
  });
  sprite.src = 'assets/dorami-sprite.png';

  // ───────────────────────── DOM ─────────────────────────
  const scene = $('#scene');
  const ctx = scene.getContext('2d', { alpha: false });
  const lower = $('#lower');
  const titleEl = $('#title');
  const taglineEl = $('#tagline');
  const form = $('#login');
  const userEl = $('#u');
  const passEl = $('#p');
  const goBtn = $('#go');
  const goLabel = $('#go-label');
  const errEl = $('#err');
  const capsEl = $('#caps');
  const handoff = $('#handoff');
  const srStatus = $('#sr-status');
  const hudMark = $('#hud-mark');
  const hudClock = $('#hud-clock');
  const fUser = $('#f-user'), fPass = $('#f-pass'), fBtn = $('#f-btn');

  // ───────────────────────── 场景状态 ─────────────────────────
  let L = null;                 // 当前布局
  let back = null;              // 静态夜空(含光晕)
  let frame = null, img = null; // 帧缓冲
  let stars = [], cityFar = [], cityNear = [], windows = [], beacons = [];
  let waterBase = null, pathRows = [], reflections = [];
  let userSlots = [];           // 打字点星的候选位置

  const S = {
    introT0: null, introDone: false,
    successT0: null,
    ui: 'idle',                 // idle | loading | error | success
    passFocus: false,
    mascot: { frame: FRAME.idle, from: FRAME.idle, xfT0: -1e9, xfDur: 160, shakeT0: -1e9, hopT0: -1e9,
      blinkT0: -1e9, nextBlink: 0, glintT0: -1e9, nextGlint: 0, oopsUntil: 0, perkT0: -1e9, nextPerk: 0 },
    shoot: null, nextShoot: 0,
    stats: { n: 0, ms: 0 },
    loadT0: -1e9, loadEnd: null,
    typed: { u: [], p: [] },    // { slot, t0, out }
    mosaic: 1, light: 0,
  };

  // ───────────────────────── 布局 ─────────────────────────
  function computeLayout() {
    const vw = window.innerWidth;
    const vv = window.visualViewport;
    const vh = Math.round(vv ? vv.height : window.innerHeight);
    const dpr = window.devicePixelRatio || 1;
    const mobile = vw < 720;
    const kb = mobile && !!vv && vv.height < window.innerHeight * 0.78;
    const compact = kb || vh < (mobile ? 640 : 600);
    root.classList.toggle('kb', kb);
    root.classList.toggle('compact', compact);
    root.style.setProperty('--vv-top', (vv ? Math.round(vv.offsetTop) : 0) + 'px');
    const lowerH = lower.offsetHeight;
    const topPad = mobile ? (compact ? 40 : 56) : (compact ? 52 : 72);
    const botPad = compact ? 16 : mobile ? 44 : 64;
    const gap = mobile ? 14 : 22;
    const need = SH + 2;
    let ud = Math.floor(((vh - topPad - botPad - lowerH - gap) * dpr) / need);
    ud = Math.min(ud, Math.floor((0.56 * vh * dpr) / SH), Math.floor(((mobile ? 0.86 : 0.42) * vw * dpr) / SW));
    ud = Math.max(ud, mobile ? 2 : 3);
    const U = ud / dpr;
    const cols = Math.ceil(vw / U) + 1;
    const rows = Math.ceil(vh / U) + 1;
    const compH = need * U + gap + lowerH;
    const freeTop = vh - compH - botPad;
    const topPx = Math.max(topPad, Math.round(topPad + (freeTop - topPad) * 0.42));
    const spriteTop = Math.round(topPx / U);
    const groundY = spriteTop + SH + 2;
    const sx = Math.round(vw / U / 2 - SW / 2);
    return { vw, vh, dpr, ud, U, cols, rows, mobile, kb, spriteTop, groundY, sx,
      lowTop: groundY * U + gap, hx: sx + SW / 2, hy: spriteTop + 42, R: SW * (mobile ? 1.0 : 1.22) };
  }

  function applyLayout() {
    L = computeLayout();
    scene.width = L.cols;
    scene.height = L.rows;
    scene.style.width = (L.cols * L.U) + 'px';
    scene.style.height = (L.rows * L.U) + 'px';
    root.style.setProperty('--u', L.U + 'px');
    root.style.setProperty('--low-top', L.lowTop + 'px');
    img = ctx.createImageData(L.cols, L.rows);
    frame = new Uint32Array(img.data.buffer);
    buildWorld();
    drawHud(true);
  }

  // ───────────────────────── 世界生成 ─────────────────────────
  function skyIndex(x, y, haloK) {
    const g = Math.pow(clamp(y / L.groundY, 0, 1), 1.55) * 7.4;
    let h = 0;
    if (haloK > 0) {
      const dx = (x - L.hx) / (L.R * haloK), dy = (y - L.hy) / (L.R * haloK * 0.92);
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1) h = Math.pow(1 - d, 1.25) * 5.6 * haloK;
    }
    let v = clamp(g + h, 0, SKY.length - 1.001);
    // 色带压平:每阶大部分是纯色,只在带间留抖动过渡(手工像素画的做法)
    const f = v - Math.floor(v);
    v = Math.floor(v) + clamp((f - 0.5) * 1.9 + 0.5, 0, 1);
    return Math.min(SKY.length - 1, Math.floor(v + b4(x, y) - 0.0001));
  }

  function buildSky(haloK) {
    const { cols, rows, groundY } = L;
    const out = new Uint32Array(cols * rows);
    for (let y = 0; y < Math.min(rows, groundY); y++) {
      for (let x = 0; x < cols; x++) out[y * cols + x] = SKY[skyIndex(x, y, haloK)];
    }
    return out;
  }

  function buildWorld() {
    const { cols, rows, groundY, sx, spriteTop } = L;
    const rnd = mulberry(20261022 + cols * 7 + rows);
    back = buildSky(1);

    // ── 星点:抖动网格采样,避免成团 ──
    stars = [];
    const cell = L.mobile ? 8 : 9;
    const skyLimit = groundY - 10;
    const hudY = Math.round(60 / L.U), hudL = Math.round(150 / L.U), hudR = cols - Math.round(230 / L.U);
    const inHud = (x, y) => y < hudY && (x < hudL || x > hudR);
    const nearMascot = (x, y, m) => x > sx - m && x < sx + SW + m && y > spriteTop - m && y < spriteTop + SH;
    for (let by = 1; by < skyLimit; by += cell) {
      for (let bx = 0; bx < cols; bx += cell) {
        const fall = 1 - 0.55 * (by / skyLimit);          // 越近地平线越稀(城市光把星吃掉)
        if (rnd() > 0.5 * fall) continue;
        const x = bx + Math.floor(rnd() * cell), y = by + Math.floor(rnd() * cell);
        if (y >= skyLimit || x >= cols || inHud(x, y) || nearMascot(x, y, 3)) continue;
        const kind = rnd() < 0.05 ? 'glow' : 'dot';
        stars.push({ x, y, kind,
          lvl: kind === 'dot' ? (rnd() < 0.5 ? 1 : rnd() < 0.6 ? 2 : 0) : 3,
          tw: rnd() < (kind === 'dot' ? 0.4 : 1) ? 1800 + rnd() * 3600 : 0,
          ph: rnd(), appear: 220 + rnd() * 900, tint: 0 });
      }
    }
    // 主星:少数几颗十字星,错落在构图的三分位附近;一颗冰蓝、一颗品红呼应目镜
    const heroes = L.mobile ? [[0.14, 0.2], [0.86, 0.36]] : [[0.12, 0.16], [0.27, 0.42], [0.74, 0.12], [0.9, 0.38]];
    heroes.forEach(([fx, fy], k) => {
      const x = Math.round(fx * (cols - 1)), y = Math.round(fy * skyLimit);
      if (inHud(x, y) || nearMascot(x, y, 6)) return;
      stars.push({ x, y, kind: 'spark', lvl: 3, tw: 2400 + k * 700, ph: rnd(), appear: 500 + k * 160,
        tint: k === 1 ? P.cyan : k === 2 ? P.magenta : 0 });
    });

    // ── 城市:远景高、近景低;吉祥物胸前一段压低,让项圈和铃铛露出来 ──
    const span0 = sx - 4, span1 = sx + SW + 4;
    const capFar = groundY - (spriteTop + 81);
    const capNear = groundY - (spriteTop + 86);
    windows = []; beacons = [];
    function layer(minW, maxW, hiMin, hiMax, cap, fill, rim, winP, winCols, isFar) {
      const cells = [];
      let x = -Math.floor(rnd() * 4);
      while (x < cols) {
        const w = minW + Math.floor(rnd() * (maxW - minW + 1));
        const center = x + w / 2;
        const edge = Math.min(1, Math.abs(center - L.hx) / (cols / 2));
        let h = Math.round(hiMin + (hiMax - hiMin) * (0.35 + 0.65 * edge) * (0.55 + rnd() * 0.45));
        const overlaps = x + w > span0 && x < span1;
        if (overlaps) h = cap + (rnd() < 0.5 ? 0 : 1);   // 胸前一段连续压低,刚好遮到项圈铃铛之下
        else {
          // 离开胸前一段后天际线缓缓抬高(山谷形),不是一刀切的断崖
          const dist = x + w <= span0 ? span0 - (x + w) : x - span1;
          h = Math.min(h, cap + 2 + Math.round(dist * (isFar ? 1.1 : 0.7)));
        }
        h = Math.max(h, 3);
        const top = groundY - h;
        const setback = !overlaps && h > 14 && rnd() < 0.45 ? 2 + Math.floor(rnd() * 3) : 0;
        for (let yy = top; yy < groundY; yy++) {
          for (let xx = x; xx < x + w; xx++) {
            if (xx < 0 || xx >= cols || yy < 0) continue;
            if (setback && yy < top + setback && (xx === x || xx === x + w - 1)) continue;
            // 朝向光晕的一侧屋顶留一格亮边
            const onRim = (yy === top + (setback && (xx === x || xx === x + w - 1) ? setback : 0)) && edge < 0.55;
            cells.push(yy * cols + xx, onRim ? rim : fill);
          }
        }
        // 天线 + 航标灯
        if (!overlaps && h > 16 && rnd() < 0.3) {
          const ax = x + Math.floor(w / 2), ah = 2 + Math.floor(rnd() * 4);
          for (let k = 1; k <= ah; k++) if (ax >= 0 && ax < cols && top - k >= 0) cells.push((top - k) * cols + ax, fill);
          if (isFar && beacons.length < 3 && top - ah - 1 >= 0) beacons.push({ i: (top - ah - 1) * cols + ax, ph: rnd() });
        }
        // 窗格
        if (h > 5) {
          for (let yy = top + 2 + setback; yy < groundY - 1; yy += 2) {
            for (let xx = x + 1; xx < x + w - 1; xx += 2) {
              if (xx < 0 || xx >= cols) continue;
              if (rnd() < winP) {
                const c = winCols[Math.floor(rnd() * winCols.length)];
                windows.push({ i: yy * cols + xx, x: xx, y: yy, c, on: true, far: isFar, flick: rnd() < 0.08 ? 4000 + rnd() * 9000 : 0, ph: rnd() });
              }
            }
          }
        }
        x += w + (!overlaps && rnd() < 0.3 ? 1 : 0);
      }
      return Uint32Array.from(cells);
    }
    const hs = L.mobile ? 0.7 : 1;
    cityFar = layer(4, 9, 10 * hs, 40 * hs, capFar, P.far, P.farRim, 0.11, [P.winFar, P.winFar, P.winFarCool], true);
    cityNear = layer(5, 13, 5 * hs, 21 * hs, capNear, P.near, P.nearRim, 0.13, [P.winWarm, P.winWarm, P.winWarmHi, P.winCool], false);

    // ── 水面:岸线一格亮、往下渐暗;窗光倒影 ──
    waterBase = new Uint32Array(Math.max(0, rows - groundY) * cols);
    for (let y = groundY; y < rows; y++) {
      const d = y - groundY;
      for (let x = 0; x < cols; x++) {
        let c;
        if (d === 0) c = P.shore;
        else {
          const v = clamp(d / (L.mobile ? 30 : 46), 0, 1) * 3;
          const k = Math.floor(v + b4(x, y) * 0.999 - 0.0001);
          c = P.water[clamp(k, 0, 3)];
        }
        waterBase[d * cols + x] = c;
      }
    }
    // 窗光倒影:只取近景、只落在岸下几行(不进文字区),定时左右错一格 → 水波
    reflections = [];
    const reach = L.mobile ? 5 : 7;
    for (const w of windows) {
      if (w.far || rnd() < 0.4) continue;
      const ry = groundY + 1 + Math.round((groundY - w.y) * 0.45);
      if (ry >= rows || ry - groundY > reach) continue;
      const fade = (ry - groundY) / (reach + 1);
      reflections.push({ x: w.x - 1, y: ry, len: 2 + (rnd() < 0.4 ? 1 : 0), c: mix(w.c, P.water[0], 0.55 + fade * 0.3), ph: rnd() });
    }
    // 月光带:吉祥物光晕在水面上的碎影(逐行短划,定时重排 → 粼光);只铺到标题上沿
    pathRows = [];
    const pathLen = L.mobile ? 8 : 11;
    for (let y = groundY + 2; y < Math.min(rows, groundY + pathLen); y += 2) {
      const d = (y - groundY) / pathLen;
      pathRows.push({ y, half: Math.round(SW * (0.12 + d * 0.16)), tone: d < 0.4 ? 0 : d < 0.75 ? 1 : 2, seed: Math.floor(rnd() * 1e6) });
    }

    // ── 打字点星的候选格:天空上半部、避开吉祥物与 HUD,按种子洗牌 ──
    userSlots = [];
    const rs = mulberry(77);
    const yMax = Math.max(8, Math.round(groundY * 0.62));
    for (let tries = 0; tries < 2000 && userSlots.length < 48; tries++) {
      const x = 4 + Math.floor(rs() * (cols - 8));
      const y = 5 + Math.floor(rs() * (yMax - 5));
      if (x > sx - 6 && x < sx + SW + 6 && y > spriteTop - 6) continue;
      if (y < Math.round(64 / L.U) && (x < Math.round(220 / L.U) || x > cols - Math.round(260 / L.U))) continue;
      if (userSlots.some((s) => Math.abs(s.x - x) + Math.abs(s.y - y) < 9)) continue;
      userSlots.push({ x, y });
    }
  }

  // ───────────────────────── 帧合成 ─────────────────────────
  const spriteBuf = new Uint32Array(SW * SH);

  function plot(x, y, c) {
    if (x < 0 || y < 0 || x >= L.cols || y >= L.groundY) return;
    frame[y * L.cols + x] = c;
  }

  function starLevel(s, t) {
    if (!s.tw || reduced()) return s.lvl;
    const v = Math.sin(((t / s.tw) + s.ph) * Math.PI * 2);
    return clamp(s.lvl + (v > 0.72 ? 1 : v < -0.86 ? -1 : 0), 0, 3);
  }

  const SPARK = [0, 1, 2, 2, 1, 1, 0, 0, 0, 0, 0, 1];   // 十字星臂长的逐格帧

  function drawStar(s, t, intro) {
    if (intro != null) {
      if (intro < s.appear) return;
      const a = intro - s.appear;
      if (a < 90) { plot(s.x, s.y, P.star[1]); return; }
      if (a < 200 && s.kind !== 'dot') { plot(s.x, s.y, P.star[3]); plot(s.x - 1, s.y, P.star[1]); plot(s.x + 1, s.y, P.star[1]); plot(s.x, s.y - 1, P.star[1]); plot(s.x, s.y + 1, P.star[1]); return; }
    }
    const lv = starLevel(s, t);
    if (s.kind === 'dot') { plot(s.x, s.y, P.star[lv]); return; }
    const core = s.tint || P.star[3];
    if (s.kind === 'glow') {
      plot(s.x, s.y, lv >= 3 ? core : P.star[2]);
      if (lv >= 3) { const d = P.star[0]; plot(s.x - 1, s.y, d); plot(s.x + 1, s.y, d); plot(s.x, s.y - 1, d); plot(s.x, s.y + 1, d); }
      return;
    }
    const arm = reduced() ? 1 : SPARK[Math.floor((((t / 3400) + s.ph) % 1) * SPARK.length)];
    plot(s.x, s.y, core);
    for (let k = 1; k <= arm; k++) {
      const c = k === 1 ? (s.tint ? mix(s.tint, SKY[2], 0.35) : P.star[2]) : P.star[0];
      plot(s.x - k, s.y, c); plot(s.x + k, s.y, c); plot(s.x, s.y - k, c); plot(s.x, s.y + k, c);
    }
  }

  // 用户每敲一键点亮一颗星:账号 = 头罩金,密码 = 目镜冰蓝
  function drawTyped(t) {
    const draw = (list, hi, lo) => {
      for (const s of list) {
        const slot = userSlots[s.slot % userSlots.length];
        if (!slot) continue;
        const a = t - s.t0;
        let arm;
        if (s.out) { if (a > 160) continue; arm = a < 80 ? 1 : 0; }
        else arm = reduced() ? 0 : a < 70 ? 0 : a < 150 ? 1 : a < 260 ? 2 : a < 380 ? 1 : 0;
        const core = s.out && a > 80 ? lo : hi;
        plot(slot.x, slot.y, core);
        for (let k = 1; k <= arm; k++) {
          const c = k === 1 ? lo : mix(lo, SKY[2], 0.5);
          plot(slot.x - k, slot.y, c); plot(slot.x + k, slot.y, c); plot(slot.x, slot.y - k, c); plot(slot.x, slot.y + k, c);
        }
        if (!s.out && arm === 0 && a >= 380) {
          const d = mix(lo, SKY[3], 0.6);
          plot(slot.x - 1, slot.y, d); plot(slot.x + 1, slot.y, d); plot(slot.x, slot.y - 1, d); plot(slot.x, slot.y + 1, d);
        }
      }
    };
    draw(S.typed.u, P.gold, P.goldDim);
    draw(S.typed.p, P.ice, P.iceDim);
  }

  function mascotFrameAt(t) {
    const m = S.mascot;
    let target = m.frame;
    if (target === FRAME.idle && t - m.blinkT0 < 130) target = FRAME.blink;
    return target;
  }

  function composeSprite(t, reveal) {
    const m = S.mascot;
    const A = frames[m.from] || frames[0];
    const Bf = frames[mascotFrameAt(t)] || frames[0];
    const xf = prog(t, m.xfT0, m.xfT0 + m.xfDur);
    const steps = 4;
    const xq = reduced() ? 1 : Math.floor(xf * steps) / steps;
    // 摇头:只平移项圈以上的行,按格跳
    const SHAKE = [2, -2, 2, -1, 1, 0];
    const si = Math.floor((t - m.shakeT0) / 58);
    const hdx = !reduced() && si >= 0 && si < SHAKE.length ? SHAKE[si] : 0;
    // 蝴蝶结一翘:最上 10 行整体读下一行 → 两只蝴蝶结向上伸一格,停 140ms
    const perk = !reduced() && t - m.perkT0 >= 0 && t - m.perkT0 < 140;
    for (let y = 0; y < SH; y++) {
      const dx = y < HEAD_ROWS ? hdx : 0;
      const sy = perk && y < 10 ? y + 1 : y;
      for (let x = 0; x < SW; x++) {
        const sx = x - dx;
        if (sx < 0 || sx >= SW) { spriteBuf[y * SW + x] = 0; continue; }
        const useB = xq >= 1 || B8[((y & 7) << 3) | (sx & 7)] < xq;
        let v = (useB ? Bf : A)[sy * SW + sx];
        if (reveal < 1 && v && B8[((y & 7) << 3) | (x & 7)] >= reveal) v = 0;
        spriteBuf[y * SW + x] = v;
      }
    }
    const fNow = mascotFrameAt(t);
    const visorOpen = xq >= 1 && fNow !== FRAME.cover;
    // 目镜扫光:一道两格宽的斜光从左扫到右
    if (visorOpen && visorMask && !reduced()) {
      const g = (t - m.glintT0) / 420;
      if (g >= 0 && g <= 1) {
        const pos = Math.floor(-8 + g * 34);
        for (let y = 36; y <= 54; y++) {
          for (let x = 37; x <= 57; x++) {
            const sxx = x - (y < HEAD_ROWS ? hdx : 0);
            if (sxx < 0 || !visorMask[y * SW + sxx]) continue;
            const d = (x - 37) - Math.floor((y - 36) * 0.6);
            if (d >= pos && d < pos + 2) spriteBuf[y * SW + x] = P.glintCore;
            else if (d === pos - 1 || d === pos + 2) spriteBuf[y * SW + x] = P.glint;
          }
        }
      }
    }
    // 目镜里跑进度:玻璃从左到右一格格被冰蓝光充满,前沿是一道亮边
    if (visorOpen && visorMask && (S.ui === 'loading' || (S.ui === 'success' && t - S.loadEnd < 700))) {
      const el = t - S.loadT0;
      const p = S.ui === 'success' ? 1 : reduced() ? 0.55 : Math.min(1, (el % 1250) / 1100);
      const edge = Math.round(p * 23);
      for (let y = 36; y <= 54; y++) {
        for (let x = 37; x <= 58; x++) {
          if (!visorMask[y * SW + x]) continue;
          const col = x - 37 + Math.floor((54 - y) * 0.35);
          const i = y * SW + x;
          if (col < edge - 1) spriteBuf[i] = mix(spriteBuf[i] || P.glint, P.hudFill, 0.6);
          else if (col < edge + 1 && p < 1) spriteBuf[i] = P.glint;
        }
      }
    }
  }

  function drawSprite(t, reveal, dy) {
    composeSprite(t, reveal);
    const { cols, groundY } = L;
    const ox = L.sx, oy = L.spriteTop + dy;
    // 一格月色描边:让深紫轮廓在夜空上立得住
    if (reveal >= 1) {
      for (let y = -1; y <= SH; y++) {
        const gy = oy + y;
        if (gy < 0 || gy >= groundY) continue;
        for (let x = -1; x <= SW; x++) {
          const inside = x >= 0 && y >= 0 && x < SW && y < SH && spriteBuf[y * SW + x];
          if (inside) continue;
          const n = (x > 0 && y >= 0 && y < SH && spriteBuf[y * SW + x - 1]) ||
            (x < SW - 1 && y >= 0 && y < SH && spriteBuf[y * SW + x + 1]) ||
            (y > 0 && x >= 0 && x < SW && spriteBuf[(y - 1) * SW + x]) ||
            (y < SH - 1 && x >= 0 && x < SW && spriteBuf[(y + 1) * SW + x]);
          const gx = ox + x;
          if (n && gx >= 0 && gx < cols) frame[gy * cols + gx] = P.rim;
        }
      }
    }
    for (let y = 0; y < SH; y++) {
      const gy = oy + y;
      if (gy < 0 || gy >= groundY) continue;
      for (let x = 0; x < SW; x++) {
        const v = spriteBuf[y * SW + x];
        const gx = ox + x;
        if (v && gx >= 0 && gx < cols) frame[gy * cols + gx] = v;
      }
    }
  }

  function drawCity(list, dy) {
    const { cols, groundY } = L;
    const lim = groundY * cols;
    const off = dy * cols;
    for (let k = 0; k < list.length; k += 2) {
      const i = list[k] + off;
      if (i >= 0 && i < lim) frame[i] = list[k + 1];
    }
  }

  function drawWindows(t, dyFar, dyNear) {
    const { cols, groundY } = L;
    const lim = groundY * cols;
    const amb = !reduced();
    for (const w of windows) {
      let on = w.on;
      if (amb && w.flick) on = ((t / w.flick + w.ph) % 1) > 0.12;
      if (!on) continue;
      const i = w.i + (w.far ? dyFar : dyNear) * cols;
      if (i >= 0 && i < lim) frame[i] = w.c;
    }
    for (const b of beacons) {
      const i = b.i + dyFar * cols;
      if (i < 0 || i >= lim) continue;
      const lit = reduced() || ((t / 2600 + b.ph) % 1) < 0.18;
      frame[i] = lit ? P.beacon : P.beaconDim;
    }
  }

  function drawWater(t, reveal) {
    const { cols, rows, groundY } = L;
    if (groundY >= rows) return;
    const base = groundY * cols;
    if (reveal >= 1) frame.set(waterBase, base);
    else {
      for (let i = 0; i < waterBase.length; i++) {
        const x = i % cols, y = (i / cols) | 0;
        frame[base + i] = b8(x, y) < reveal ? waterBase[i] : INK;
      }
    }
    if (reveal < 0.5) return;
    const wave = reduced() ? 0 : Math.floor(t / 640);
    for (const r of reflections) {
      const h = Math.sin((r.ph * 977 + wave) * 12.9898) * 43758.5453;
      const dx = reduced() ? 0 : (h - Math.floor(h) > 0.8 ? 1 : 0);
      for (let k = 0; k < r.len; k++) {
        const xx = r.x + k + dx;
        if (xx >= 0 && xx < cols) frame[r.y * cols + xx] = r.c;
      }
    }
    const step = reduced() ? 0 : Math.floor(t / 320);
    for (const r of pathRows) {
      if (r.y >= rows) continue;
      const rr = mulberry(r.seed + step * 131);
      let x = Math.round(L.hx) - r.half;
      const x1 = Math.round(L.hx) + r.half;
      while (x < x1) {
        const len = 1 + Math.floor(rr() * 4);
        const gapn = 1 + Math.floor(rr() * 3);
        const edge = Math.abs((x + len / 2) - L.hx) / r.half;
        if (rr() > edge * 0.85) {
          const c = P.path[Math.min(2, r.tone + (edge > 0.6 ? 1 : 0))];
          for (let k = 0; k < len && x + k < x1; k++) if (x + k >= 0 && x + k < cols) frame[r.y * cols + x + k] = c;
        }
        x += len + gapn;
      }
    }
  }

  function mosaic(m) {
    const { cols, rows } = L;
    for (let by = 0; by < rows; by += m) {
      for (let bx = 0; bx < cols; bx += m) {
        let r = 0, g = 0, b = 0, n = 0;
        const yEnd = Math.min(rows, by + m), xEnd = Math.min(cols, bx + m);
        for (let y = by; y < yEnd; y++) {
          for (let x = bx; x < xEnd; x++) {
            const v = frame[y * cols + x];
            r += v & 255; g += (v >>> 8) & 255; b += (v >>> 16) & 255; n++;
          }
        }
        const c = pack(Math.round(r / n), Math.round(g / n), Math.round(b / n));
        for (let y = by; y < yEnd; y++) frame.fill(c, y * cols + bx, y * cols + xEnd);
      }
    }
  }

  // 阅读器底色从吉祥物脸上向外铺开:距离 + Bayer 阈值 → 一道抖动的光前沿
  function lightDissolve(p) {
    const { cols, rows } = L;
    const blk = 3;
    const cx = L.hx, cy = L.hy;
    const maxd = Math.hypot(Math.max(cx, cols - cx), Math.max(cy, rows - cy));
    for (let by = 0; by < rows; by += blk) {
      for (let bx = 0; bx < cols; bx += blk) {
        const d = Math.hypot(bx + blk / 2 - cx, (by + blk / 2 - cy) * 1.15) / maxd;
        const th = d * 0.72 + B8[(((by / blk) & 7) << 3) | ((bx / blk) & 7)] * 0.28;
        if (th >= p) continue;
        const yEnd = Math.min(rows, by + blk), xEnd = Math.min(cols, bx + blk);
        for (let y = by; y < yEnd; y++) frame.fill(P.reader, y * cols + bx, y * cols + xEnd);
      }
    }
  }

  // ───────────────────────── 开场时间轴(ms) ─────────────────────────
  const T = {
    sky: [0, 900],           // 夜空按 Bayer 阈值逐级显影
    stars: 220,              // 星点 220–1120 逐颗点亮
    far: [480, 1260],        // 远景城市逐格升起
    near: [640, 1380],       // 近景城市
    water: [560, 1300],      // 水面显影
    halo: [1050, 2250],      // 光晕一环环亮起
    rise: [1000, 2150],      // 吉祥物从城市背后升起
    reveal: [1000, 1750],    // 同时按抖动阈值成形
    title: 1900,             // 标题「像素对焦」(块 6u → 1u → 矢量,每级 72ms)
    tagline: 2350,
    form: [2520, 2620, 2720], // 账号 / 密码 / 按键 抖动显影
    hud: 2900,
    blink: 3350,
    done: 3500,
  };

  function render(t) {
    if (!L || !frames.length) return;
    const { cols, rows, groundY } = L;
    const it = S.introDone ? null : (S.introT0 == null ? 0 : t - S.introT0);

    // 夜空
    if (it == null) frame.set(back);
    else {
      const ps = easeInOut(prog(it, T.sky[0], T.sky[1]));
      const hk = easeOut(prog(it, T.halo[0], T.halo[1]));
      const sky = hk >= 1 ? back : buildSkyCached(hk);
      for (let y = 0; y < Math.min(rows, groundY); y++) {
        for (let x = 0; x < cols; x++) {
          const i = y * cols + x;
          frame[i] = B8[((y & 7) << 3) | (x & 7)] < ps ? sky[i] : INK;
        }
      }
    }
    // 星
    for (const s of stars) drawStar(s, t, it);
    drawTyped(t);
    if (S.shoot) {
      const sh = S.shoot, a = (t - sh.t0) / sh.dur;
      if (a >= 1 || reduced()) S.shoot = null;
      else {
        const head = Math.floor(a * sh.len);
        const tail = [P.star[3], P.star[2], P.star[2], P.star[1], P.star[1], P.star[0]];
        for (let k = 0; k < tail.length; k++) {
          const st = head - k;
          if (st < 0) break;
          if (a > 0.75 && k < Math.floor((a - 0.75) * 24)) continue;   // 末段从头部开始熄灭
          plot(sh.x0 + sh.dir * st, sh.y0 + Math.floor(st * 0.42), tail[k]);
        }
      }
    }
    // 吉祥物
    let dy = 0, reveal = 1;
    if (it != null) {
      const pr = easeOut(prog(it, T.rise[0], T.rise[1]));
      dy = Math.round((1 - pr) * 44);
      reveal = prog(it, T.reveal[0], T.reveal[1]);
    }
    const hop = [-1, -2, -2, -1, 0];
    const hi = Math.floor((t - S.mascot.hopT0) / 70);
    if (!reduced() && hi >= 0 && hi < hop.length) dy += hop[hi];
    if (reveal > 0) drawSprite(t, reveal, dy);
    // 城市
    let dyFar = 0, dyNear = 0, wr = 1;
    if (it != null) {
      dyFar = Math.round((1 - easeOut(prog(it, T.far[0], T.far[1]))) * 42);
      dyNear = Math.round((1 - easeOut(prog(it, T.near[0], T.near[1]))) * 26);
      wr = easeInOut(prog(it, T.water[0], T.water[1]));
    }
    drawCity(cityFar, dyFar);
    drawCity(cityNear, dyNear);
    drawWindows(t, dyFar, dyNear);
    drawWater(t, wr);
    // 离场
    if (S.mosaic > 1) mosaic(S.mosaic);
    if (S.light > 0) lightDissolve(S.light);
    ctx.putImageData(img, 0, 0);
  }

  let skyCache = { k: -1, buf: null };
  function buildSkyCached(k) {
    const q = Math.round(k * 24) / 24;   // 光晕按 24 级步进(也正好是像素画的「一环一环」)
    if (skyCache.k !== q) skyCache = { k: q, buf: buildSky(q) };
    return skyCache.buf;
  }

  // ───────────────────────── DOM:抖动显影与像素对焦 ─────────────────────────
  const maskCache = new Map();
  function ditherMask(level, ud) {
    const key = level + ':' + ud;
    if (maskCache.has(key)) return maskCache.get(key);
    const c = document.createElement('canvas');
    c.width = c.height = 4 * ud;
    const x = c.getContext('2d');
    x.fillStyle = '#000';
    for (let yy = 0; yy < 4; yy++) for (let xx = 0; xx < 4; xx++) if (B4[yy * 4 + xx] < level / 16) x.fillRect(xx * ud, yy * ud, ud, ud);
    const url = 'url(' + c.toDataURL() + ')';
    maskCache.set(key, url);
    return url;
  }

  const fades = new Map();   // el → { t0, dur, out, last }
  function setMask(el, level) {
    if (level >= 16) {
      el.style.removeProperty('mask-image'); el.style.removeProperty('-webkit-mask-image');
      el.style.visibility = '';
      return;
    }
    if (level <= 0) { el.style.visibility = 'hidden'; return; }
    const r = el.getBoundingClientRect();
    const tile = 4 * L.U;
    const ox = -(((r.left % tile) + tile) % tile), oy = -(((r.top % tile) + tile) % tile);
    const m = ditherMask(level, L.ud);
    el.style.visibility = '';
    for (const p of ['mask', '-webkit-mask']) {
      el.style.setProperty(p + '-image', m);
      el.style.setProperty(p + '-size', tile + 'px ' + tile + 'px');
      el.style.setProperty(p + '-position', ox + 'px ' + oy + 'px');
      el.style.setProperty(p + '-repeat', 'repeat');
    }
  }
  function dither(el, t0, dur, out) { fades.set(el, { t0, dur, out: !!out, last: -1 }); setMask(el, out ? 16 : 0); }
  function stepFades(t) {
    for (const [el, f] of fades) {
      const p = prog(t, f.t0, f.t0 + f.dur);
      const lv = Math.round((f.out ? 1 - p : p) * 16);
      if (lv !== f.last) { f.last = lv; setMask(el, lv); }
      if (p >= 1) fades.delete(el);
    }
  }
  function clearFades() {
    for (const el of fades.keys()) setMask(el, 16);
    fades.clear();
  }

  // 像素对焦:把标题栅格化到 Canvas,块大小 8u → 1u 逐级变细,最后换回矢量字
  const veils = [];
  function pixelFocus(el, t0, out) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const pad = Math.round(L.U * 4);
    const w = Math.ceil(r.width + pad * 2), h = Math.ceil(r.height + pad * 2);
    const full = document.createElement('canvas');
    full.width = Math.round(w * dpr); full.height = Math.round(h * dpr);
    const fx = full.getContext('2d');
    fx.scale(dpr, dpr);
    fx.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    if ('letterSpacing' in fx) fx.letterSpacing = cs.letterSpacing;
    fx.fillStyle = cs.color;
    fx.textBaseline = 'alphabetic';
    const tm = fx.measureText(el.textContent);
    const asc = tm.fontBoundingBoxAscent || parseFloat(cs.fontSize) * 0.88;
    const desc = tm.fontBoundingBoxDescent || parseFloat(cs.fontSize) * 0.12;
    const lh = parseFloat(cs.lineHeight) || r.height;
    fx.fillText(el.textContent, pad, pad + (lh - (asc + desc)) / 2 + asc);
    const cv = document.createElement('canvas');
    cv.className = 'px-veil';
    cv.width = full.width; cv.height = full.height;
    cv.style.left = (r.left - pad) + 'px'; cv.style.top = (r.top - pad) + 'px';
    cv.style.width = w + 'px'; cv.style.height = h + 'px';
    document.body.appendChild(cv);
    const v = { el, cv, full, t0, out: !!out, last: -1, x: cv.getContext('2d') };
    veils.push(v);
    el.style.visibility = 'hidden';
    return v;
  }
  const FOCUS = [6, 4.5, 3, 2, 1.5, 1];   // 以格为单位的块大小(6u → 1u,再换回矢量字)
  function stepVeils(t) {
    for (let k = veils.length - 1; k >= 0; k--) {
      const v = veils[k];
      const per = 72;
      let i = Math.floor((t - v.t0) / per);
      if (i < 0) { v.cv.style.visibility = 'hidden'; continue; }
      v.cv.style.visibility = '';
      const n = FOCUS.length;
      if (i >= n) {
        v.cv.remove(); veils.splice(k, 1);
        v.el.style.visibility = v.out ? 'hidden' : '';
        continue;
      }
      if (i === v.last) continue;
      v.last = i;
      const blk = (v.out ? FOCUS[n - 1 - i] : FOCUS[i]) * L.U * Math.min(2, window.devicePixelRatio || 1);
      const sw = Math.max(1, Math.round(v.full.width / blk)), sh = Math.max(1, Math.round(v.full.height / blk));
      const tmp = pixelFocus.tmp || (pixelFocus.tmp = document.createElement('canvas'));
      tmp.width = sw; tmp.height = sh;
      const tx = tmp.getContext('2d', { willReadFrequently: true });
      tx.imageSmoothingEnabled = true;
      tx.imageSmoothingQuality = 'high';
      tx.clearRect(0, 0, sw, sh);
      tx.drawImage(v.full, 0, 0, sw, sh);
      // 透明度按 Bayer 阈值二值化:边缘是抖动格而不是半透明糊边
      const d = tx.getImageData(0, 0, sw, sh);
      const a = d.data;
      const fade = v.out ? 1 - (i / (n - 1)) * 0.94 : 0.38 + (i / (n - 1)) * 0.62;
      for (let y = 0; y < sh; y++) {
        for (let x = 0; x < sw; x++) {
          const o = (y * sw + x) * 4 + 3;
          const al = (a[o] / 255) * fade * 1.25;
          a[o] = al > B4[((y & 3) << 2) | (x & 3)] ? 255 : 0;
        }
      }
      tx.putImageData(d, 0, 0);
      v.x.imageSmoothingEnabled = false;
      v.x.clearRect(0, 0, v.cv.width, v.cv.height);
      v.x.drawImage(tmp, 0, 0, sw, sh, 0, 0, v.cv.width, v.cv.height);
    }
  }
  function clearVeils() { for (const v of veils) { v.cv.remove(); v.el.style.visibility = ''; } veils.length = 0; }

  // ───────────────────────── HUD(点阵小字,只用本地时间) ─────────────────────────
  const GLYPH = {
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
    '3': ['11111', '00010', '00100', '00010', '00001', '10001', '01110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
    '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
    'A': ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
    'D': ['11100', '10010', '10001', '10001', '10001', '10010', '11100'],
    'E': ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
    'F': ['11111', '10000', '10000', '11110', '10000', '10000', '10000'],
    'H': ['10001', '10001', '10001', '11111', '10001', '10001', '10001'],
    'I': ['111', '010', '010', '010', '010', '010', '111'],
    'M': ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
    'N': ['10001', '10001', '11001', '10101', '10011', '10001', '10001'],
    'O': ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
    'R': ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
    'S': ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
    'T': ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
    'U': ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
    'W': ['10001', '10001', '10001', '10101', '10101', '10101', '01010'],
    ':': ['0', '0', '1', '0', '1', '0', '0'],
    '.': ['0', '0', '0', '0', '0', '0', '1'],
    ' ': ['00', '00', '00', '00', '00', '00', '00'],
  };
  function textWidth(s) { let w = 0; for (const ch of s) w += (GLYPH[ch] || GLYPH[' '])[0].length + 1; return Math.max(0, w - 1); }
  function drawDots(cv, text, color, colonOn, typed) {
    const dpr = window.devicePixelRatio || 1;
    const dot = Math.max(1, Math.round((window.innerWidth < 720 ? 1.5 : 2) * dpr));   // 每点 2 CSS px(手机 1.5),取整到设备像素
    const w = textWidth(text), h = 7;
    cv.width = w * dot; cv.height = h * dot;
    cv.style.width = (w * dot / dpr) + 'px'; cv.style.height = (h * dot / dpr) + 'px';
    const x = cv.getContext('2d');
    x.clearRect(0, 0, cv.width, cv.height);
    x.fillStyle = color;
    let cx = 0, n = 0;
    for (const ch of text) {
      const g = GLYPH[ch] || GLYPH[' '];
      const show = typed == null || n < typed;
      if (show && !(ch === ':' && !colonOn)) {
        for (let yy = 0; yy < 7; yy++) for (let xx = 0; xx < g[yy].length; xx++) if (g[yy][xx] === '1') x.fillRect((cx + xx) * dot, yy * dot, dot, dot);
      }
      cx += g[0].length + 1;
      if (ch !== ' ') n++;
    }
  }
  const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  let hudTyped = null;
  function clockText(d) {
    const p2 = (n) => String(n).padStart(2, '0');
    return DAYS[d.getDay()] + ' ' + p2(d.getMonth() + 1) + '.' + p2(d.getDate()) + '  ' + p2(d.getHours()) + ':' + p2(d.getMinutes());
  }
  function drawHud() {
    const d = new Date();
    const colon = reduced() || d.getSeconds() % 2 === 0;
    drawDots(hudMark, 'DORAMI', '#8f8bd6', true, hudTyped);
    drawDots(hudClock, clockText(d), '#8f8bd6', colon, hudTyped);
  }

  // ───────────────────────── 主循环 ─────────────────────────
  let raf = 0, lastDraw = 0, lastHud = 0;
  function wantsLoop() {
    if (document.hidden) return false;
    if (!S.introDone || S.successT0 != null || fades.size || veils.length) return true;
    if (S.ui === 'loading') return true;
    return !reduced();
  }
  function tick(ts) {
    raf = 0;
    if (!L) return;
    const t = now();
    const busy = !S.introDone || S.successT0 != null || fades.size || veils.length || S.shoot || t - S.mascot.xfT0 < 400 || t - S.mascot.shakeT0 < 500;
    const fps = busy ? 30 : 10;
    if (t - lastDraw >= 1000 / fps - 3) {
      lastDraw = t;
      stepIntro(t);
      stepAmbient(t);
      stepSuccess(t);
      stepFades(t);
      stepVeils(t);
      const r0 = now();
      render(t);
      S.stats.n++; S.stats.ms += now() - r0;
      if (t - lastHud > 500) { lastHud = t; drawHud(); }
    }
    if (wantsLoop()) raf = requestAnimationFrame(tick);
  }
  function kick() { if (!raf && !document.hidden) raf = requestAnimationFrame(tick); }
  function redraw() { const t = now(); stepFades(t); render(t); drawHud(); kick(); }

  function stepAmbient(t) {
    const m = S.mascot;
    if (reduced() || !S.introDone || S.successT0 != null) return;
    if (m.frame === FRAME.idle && t > m.nextBlink) {
      m.blinkT0 = t;
      m.nextBlink = t + 2600 + Math.random() * 4200;
      if (Math.random() < 0.18) m.nextBlink = t + 260;   // 偶尔连眨两下
    }
    if (t > m.nextGlint) { m.glintT0 = t; m.nextGlint = t + 7000 + Math.random() * 3000; }
    if (t > m.nextPerk) { m.perkT0 = t; m.nextPerk = t + 6500 + Math.random() * 5500; }
    if (!S.shoot && t > S.nextShoot) {
      // 流星:很少出现(14–26s 一颗),从上半天空斜划下来,逐格步进
      const dir = Math.random() < 0.5 ? -1 : 1;
      const x0 = dir > 0 ? Math.round(L.cols * (0.12 + Math.random() * 0.2)) : Math.round(L.cols * (0.68 + Math.random() * 0.2));
      const y0 = Math.round(80 / L.U) + Math.round(Math.random() * L.groundY * 0.16);   // 避开顶部 HUD
      S.shoot = { t0: t, x0, y0, dir, len: 26 + Math.round(Math.random() * 14), dur: 640 };
      S.nextShoot = t + 14000 + Math.random() * 12000;
    }
    if (S.ui === 'error' && t > m.oopsUntil) { S.ui = 'idle'; updateMascot(t); }
  }

  // ───────────────────────── 开场 ─────────────────────────
  let introStarted = false;
  function startIntro() {
    clearFades(); clearVeils();
    handoff.classList.remove('on');
    S.successT0 = null; S.mosaic = 1; S.light = 0;
    S.introDone = false;
    S.introT0 = now();
    hudTyped = 0;
    for (const el of [titleEl, taglineEl, fUser, fPass, fBtn, errEl, capsEl, hudMark, hudClock]) setMask(el, 0);
    root.classList.remove('intro-pending');
    S.introSteps = { title: false, tagline: false, form: false, hud: false };
    introStarted = true;
    kick();
  }
  function stepIntro(t) {
    if (S.introDone || S.introT0 == null) return;
    const it = t - S.introT0;
    const st = S.introSteps;
    if (!st.title && it >= T.title) { st.title = true; pixelFocus(titleEl, t, false); }
    if (!st.tagline && it >= T.tagline) { st.tagline = true; dither(taglineEl, t, 420); }
    if (!st.form && it >= T.form[0]) {
      st.form = true;
      dither(fUser, S.introT0 + T.form[0], 360);
      dither(fPass, S.introT0 + T.form[1], 360);
      dither(fBtn, S.introT0 + T.form[2], 360);
      setMask(errEl, 16);
    }
    if (!st.hud && it >= T.hud) {
      st.hud = true;
      dither(capsEl, t, 500);
      setMask(hudMark, 16); setMask(hudClock, 16);
    }
    if (st.hud) {
      const n = Math.floor((it - T.hud) / 34);
      if (n !== hudTyped) { hudTyped = n; drawHud(); }
    }
    if (it >= T.blink && S.mascot.blinkT0 < S.introT0 + T.blink) S.mascot.blinkT0 = t;
    if (it >= T.done) finishIntro();
  }
  function finishIntro() {
    S.introDone = true;
    hudTyped = null;
    clearVeils();
    for (const el of [titleEl, taglineEl, fUser, fPass, fBtn, errEl, capsEl, hudMark, hudClock]) if (!fades.has(el)) setMask(el, 16);
    S.mascot.nextBlink = now() + 2400;
    S.mascot.nextGlint = now() + 1800;
    S.mascot.nextPerk = now() + 4200;
    S.nextShoot = now() + 6000;
    drawHud();
  }
  function skipIntro() {
    if (!introStarted) { root.classList.remove('intro-pending'); }
    S.introT0 = now() - T.done;
    clearFades(); clearVeils();
    finishIntro();
    redraw();
  }

  // 开场期间任意点击 / 按键即跳过(控制条内的事件除外)
  function onSkip(e) {
    if (!booted || S.introDone) return;
    if (e.target && e.target.closest && e.target.closest('[data-sample-kit]')) return;
    skipIntro();
  }
  window.addEventListener('pointerdown', onSkip, true);
  window.addEventListener('keydown', onSkip, true);

  // ───────────────────────── 吉祥物情绪 ─────────────────────────
  function setFrame(f, t) {
    const m = S.mascot;
    if (m.frame === f) return;
    m.from = m.frame;
    m.frame = f;
    m.xfT0 = t;
    m.xfDur = 170;
  }
  function updateMascot(t = now()) {
    let f = FRAME.idle;
    if (S.ui === 'success') f = FRAME.happy;
    else if (S.ui === 'error') f = FRAME.oops;
    else if (S.ui === 'loading') f = FRAME.idle;
    else if (S.passFocus) f = FRAME.cover;
    setFrame(f, t);
    kick();
  }

  // ───────────────────────── 表单 ─────────────────────────
  const LOAD_MS = 900;
  let loadTimer = 0;
  function setErr(msg) {
    errEl.textContent = msg || '';
    const bad = !!msg;
    userEl.setAttribute('aria-invalid', bad && (!userEl.value || msg === '账号或密码错误') ? 'true' : 'false');
    passEl.setAttribute('aria-invalid', bad && (!passEl.value || msg === '账号或密码错误') ? 'true' : 'false');
    if (bad) dither(errEl, now(), reduced() ? 0 : 200);
  }
  function setLoading(on) {
    form.classList.toggle('is-loading', on);
    goLabel.textContent = on ? '登录中' : '登录';
    goBtn.setAttribute('aria-disabled', on ? 'true' : 'false');
    form.setAttribute('aria-busy', on ? 'true' : 'false');
    userEl.readOnly = on; passEl.readOnly = on;
  }

  function showError(msg) {
    const t = now();
    setLoading(false);
    S.ui = 'error';
    S.loadEnd = null;
    S.mascot.shakeT0 = t;
    S.mascot.oopsUntil = t + 2600;
    setErr(msg);
    updateMascot(t);
    kick();
  }
  function showLoading() {
    const t = now();
    S.ui = 'loading';
    S.loadT0 = t; S.loadEnd = null;
    setErr('');
    setLoading(true);
    srStatus.textContent = '正在登录';
    updateMascot(t);
    kick();
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (S.ui === 'loading' || S.ui === 'success') return;
    if (!S.introDone) skipIntro();
    const u = userEl.value.trim(), p = passEl.value;
    if (!u || !p) { showError('请输入账号和密码'); (u ? passEl : userEl).focus(); return; }
    showLoading();
    clearTimeout(loadTimer);
    loadTimer = setTimeout(() => {
      if (u === 'admin' && p === 'admin') startSuccess();
      else { showError('账号或密码错误'); passEl.select(); }
    }, LOAD_MS);
  });

  function syncTyped(key, el) {
    const t = now();
    const list = S.typed[key];
    const live = list.filter((s) => !s.out);
    const want = Math.min(el.value.length, 24);
    const base = key === 'u' ? 0 : 24;
    if (want > live.length) {
      for (let k = live.length; k < want; k++) list.push({ slot: base + k, t0: t + (k - live.length) * 40, out: false });
    } else if (want < live.length) {
      for (let k = live.length - 1; k >= want; k--) { live[k].out = true; live[k].t0 = t; }
    }
    S.typed[key] = list.filter((s) => !(s.out && t - s.t0 > 200));
    kick();
  }
  userEl.addEventListener('input', () => { syncTyped('u', userEl); if (S.ui === 'error') { S.ui = 'idle'; setErr(''); updateMascot(); } });
  passEl.addEventListener('input', () => { syncTyped('p', passEl); if (S.ui === 'error') { S.ui = 'idle'; setErr(''); updateMascot(); } });
  passEl.addEventListener('focus', () => { S.passFocus = true; if (S.ui !== 'error') updateMascot(); });
  passEl.addEventListener('blur', () => { S.passFocus = false; updateMascot(); });

  // ───────────────────────── 成功离场 ─────────────────────────
  // 0 开心帧 + 小跳 + 目镜满格 → 200 界面按像素对焦 / 抖动退场 → 700 场景逐级马赛克
  // → 1000 阅读器底色从她脸上按 Bayer 前沿铺开 → 1700 落幅
  function startSuccess() {
    const t = now();
    clearTimeout(loadTimer);
    if (!S.introDone) { S.introT0 = t - T.done; finishIntro(); }
    S.loadEnd = t;
    setLoading(true);
    goLabel.textContent = '登录中';
    S.ui = 'success';
    srStatus.textContent = '登录成功,正在进入阅读器';
    updateMascot(t);
    if (reduced()) {
      S.successT0 = null;
      handoff.classList.add('on');
      redraw();
      return;
    }
    S.mascot.hopT0 = t + 60;
    S.successT0 = t;
    S.succSteps = { ui: false };
    kick();
  }
  const MOSAIC = [2, 3, 4, 6, 8, 12, 16, 24];
  function stepSuccess(t) {
    if (S.successT0 == null) return;
    const a = t - S.successT0;
    if (!S.succSteps.ui && a >= 200) {
      S.succSteps.ui = true;
      pixelFocus(titleEl, t, true);
      dither(taglineEl, t, 300, true);
      dither(fUser, t + 40, 300, true);
      dither(fPass, t + 80, 300, true);
      dither(fBtn, t + 120, 300, true);
      dither(errEl, t, 200, true);
      dither(capsEl, t, 300, true);
      dither(hudMark, t, 300, true);
      dither(hudClock, t, 300, true);
    }
    const mi = Math.floor((a - 700) / 62);
    S.mosaic = mi < 0 ? 1 : MOSAIC[Math.min(MOSAIC.length - 1, mi)];
    S.light = prog(a, 1000, 1650) * 1.02;
    if (a >= 1700) {
      handoff.classList.add('on');
      S.successT0 = null;
    }
  }

  // ───────────────────────── 样页契约 ─────────────────────────
  function resetUi() {
    clearTimeout(loadTimer);
    setLoading(false);
    setErr('');
    S.ui = 'idle';
    S.loadEnd = null;
    S.successT0 = null;
    S.mosaic = 1; S.light = 0;
    handoff.classList.remove('on');
    S.mascot.frame = S.mascot.from = S.passFocus ? FRAME.cover : FRAME.idle;
    S.mascot.xfT0 = -1e9;
  }

  let booted = false;
  const bootQ = [];
  const whenReady = (fn) => (...args) => { if (booted) fn(...args); else bootQ.push(() => fn(...args)); };

  window.__cPixelStats = () => ({ frames: S.stats.n, avgRenderMs: S.stats.n ? +(S.stats.ms / S.stats.n).toFixed(3) : 0, grid: L ? L.cols + '×' + L.rows : null, unit: L ? L.U : null });

  window.LoginSample = {
    replay: whenReady(() => { resetUi(); if (reduced()) { skipIntro(); return; } startIntro(); }),
    skipIntro: whenReady(() => skipIntro()),
    setState: whenReady((name) => {
      if (!S.introDone) skipIntro();
      if (name === 'success') { resetUi(); startSuccess(); return; }
      resetUi();
      clearFades(); clearVeils();
      for (const el of [titleEl, taglineEl, fUser, fPass, fBtn, errEl, capsEl, hudMark, hudClock]) setMask(el, 16);
      if (name === 'loading') showLoading();
      else if (name === 'error') showError('账号或密码错误');
      else updateMascot();
      redraw();
    }),
    extraControls: [
      { label: '捂眼', run() { passEl.focus(); } },
      { label: '眨眼', run() { S.mascot.blinkT0 = now(); kick(); } },
      { label: '蝴蝶结', run() { S.mascot.perkT0 = now(); kick(); } },
      { label: '目镜扫光', run() { S.mascot.glintT0 = now(); kick(); } },
      { label: '流星', run() { S.nextShoot = 0; S.shoot = null; kick(); } },
    ],
  };

  // ───────────────────────── 启动 ─────────────────────────
  window.addEventListener('sample:motion', (e) => {
    if (e.detail && e.detail.reduced) { if (!S.introDone) skipIntro(); }
    clearFades();
    redraw();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { lastDraw = 0; kick(); } });
  let rzTimer = 0;
  function onResize() {
    clearTimeout(rzTimer);
    rzTimer = setTimeout(() => { applyLayout(); skyCache.k = -1; redraw(); }, 60);
  }
  window.addEventListener('resize', onResize);
  // DPR 变化(拖到另一块屏、Windows 缩放档切换)也要重算整数设备像素格
  (function watchDpr() {
    if (!window.matchMedia) return;
    const mq = window.matchMedia('(resolution: ' + (window.devicePixelRatio || 1) + 'dppx)');
    const h = () => { onResize(); mq.removeEventListener('change', h); watchDpr(); };
    mq.addEventListener('change', h);
  })();
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', onResize);
    window.visualViewport.addEventListener('scroll', () => root.style.setProperty('--vv-top', Math.round(window.visualViewport.offsetTop) + 'px'));
  }

  const params = new URLSearchParams(location.search);
  const fontsReady = document.fonts && document.fonts.load
    ? Promise.race([Promise.all([document.fonts.load('900 64px "Dorami Serif"', '哆啦美'), document.fonts.load('600 17px "Dorami Serif"', taglineEl.textContent)]), new Promise((r) => setTimeout(r, 1200))])
    : Promise.resolve();

  Promise.all([spriteReady, fontsReady]).then(() => {
    applyLayout();
    booted = true;
    if (reduced() || params.get('intro') === 'skip' || params.get('state')) skipIntro();
    else startIntro();
    for (const f of bootQ.splice(0)) f();
  });
})();
