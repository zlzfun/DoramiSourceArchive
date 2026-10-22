/* 哆啦美登录页 · 方向 B「信号」样页脚本
 * 全屏 Canvas 2D:数十条层叠脊线(后排被前排遮挡),中部是峰群;
 * 鼠标 = 调谐器(附近的线去噪、变得干净有序,带惯性与余波);
 * 按键沿线发脉冲;密码聚焦 = 信号静默;登录中 = 相干波;出错 = 静电;成功 = 收束成一条亮线再展开成光幕。
 */
(function () {
  'use strict';

  // ---------- DOM ----------
  const root = document.documentElement;
  const body = document.body;
  const cv = document.getElementById('field');
  const ctx = cv.getContext('2d');
  const form = document.getElementById('login');
  const inAcc = document.getElementById('account');
  const inPw = document.getElementById('password');
  const fAcc = document.getElementById('f-account');
  const fPw = document.getElementById('f-password');
  const btn = document.getElementById('submit');
  const btnLabel = btn.querySelector('.go-label');
  const errBox = document.getElementById('err');
  const errText = document.getElementById('err-text');
  const statusEl = document.getElementById('status');
  const statusText = statusEl.querySelector('.st-text');
  const clockEl = document.getElementById('clock');
  const handoff = document.getElementById('handoff');
  const band = document.querySelector('.band');
  const srLive = document.getElementById('sr-live');
  const rvEls = Array.from(document.querySelectorAll('[data-rv]'));

  const BG = '#06070a';
  const LINE = '230,233,242';
  const TINT = '168,164,255';
  const READER = '#f4f6f9';

  // ---------- 工具 ----------
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const sstep = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOutQuart = (t) => (t < 0.5 ? 8 * t * t * t * t : 1 - Math.pow(-2 * t + 2, 4) / 2);
  const easeSine = (t) => 0.5 - 0.5 * Math.cos(Math.PI * t);
  const isReduced = () => root.dataset.motion === 'reduce';
  const nowS = () => performance.now() / 1000;

  function mulberry(seed) {
    let s = seed >>> 0;
    return function () {
      s = (s + 0x6d2b79f5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(r) {
    let u = r();
    while (u <= 1e-9) u = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(6.283185 * r());
  }
  function hash(ix, iy) {
    let h = (Math.imul(ix, 374761393) + Math.imul(iy, 668265263)) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    h ^= h >>> 16;
    return (h & 0xffff) / 32767.5 - 1;
  }
  function vnoise(x, y) {
    const ix = Math.floor(x), iy = Math.floor(y);
    const fx = x - ix, fy = y - iy;
    const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
    const a = hash(ix, iy), b = hash(ix + 1, iy), c = hash(ix, iy + 1), d = hash(ix + 1, iy + 1);
    const top = a + (b - a) * ux, bot = c + (d - c) * ux;
    return top + (bot - top) * uy;
  }

  // ---------- 吉祥物高度场(codex 生成的深度图,112×122 灰度) ----------
  let hm = null;
  (function loadHeightmap() {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement('canvas');
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        const g = c.getContext('2d', { willReadFrequently: true });
        g.drawImage(img, 0, 0);
        const d = g.getImageData(0, 0, c.width, c.height).data;
        const f = new Float32Array(c.width * c.height);
        for (let i = 0; i < f.length; i++) f[i] = d[i * 4] / 255;
        hm = { w: c.width, h: c.height, data: f };
      } catch (e) { hm = null; }
    };
    img.src = 'assets/mascot-height.png';
  })();
  function hmSample(u, v) {
    if (!hm || u <= 0 || u >= 1 || v <= 0 || v >= 1) return 0;
    const x = u * (hm.w - 1), y = v * (hm.h - 1);
    const ix = x | 0, iy = y | 0, fx = x - ix, fy = y - iy;
    const w = hm.w, d = hm.data, i = iy * w + ix;
    const a = d[i], b = d[i + 1], c = d[i + w], e = d[i + w + 1];
    return (a + (b - a) * fx) * (1 - fy) + (c + (e - c) * fx) * fy;
  }

  // ---------- 布局 ----------
  const L = {
    W: 0, H: 0, dpr: 1, mobile: false,
    xs: new Float32Array(0), env: new Float32Array(0), envC: new Float32Array(0), M: 0,
    x0: 0, step: 3, kA: 0, kW: 0,
    fadeA: 0, fadeB: 0, fadeC: 0, fadeD: 0, wireX: 0,
    xc: 0, hw: 1, gap: 12, top: 0, bottom: 0, yMid: 0,
    accRow: -1, pwRow: -1,
    rel: { x: 0, y: 0, w: 1, h: 1, amp: 0 },
    inputLeft: 0,
  };
  let rows = [];

  // 脉冲星式的「分量」:所有行共享几根分量中心(相对峰群中心,以半宽计),
  // 每行在分量附近长出一个小丘 + 1–3 根尖峰,行间轻微抖动 → 峰群纵向连贯、又行行不同。
  const COMP = [
    { c: -0.42, p: 0.4, a: 0.44, s: 0.055 },
    { c: -0.13, p: 0.96, a: 1.0, s: 0.048 },
    { c: 0.14, p: 0.82, a: 0.8, s: 0.05 },
    { c: 0.44, p: 0.36, a: 0.42, s: 0.055 },
  ];

  function makeRow(y, i, N, rnd) {
    const rr = i / Math.max(1, N - 1);
    const V = 0.42 + 0.58 * Math.exp(-Math.pow((rr - 0.52) / 0.3, 2));
    const maxH = L.gap * (L.mobile ? 6.4 : 8.6);
    const amp = maxH * V * (0.62 + 0.46 * rnd());
    const sc = L.hw / 250;
    const sp = [];
    for (const cp of COMP) {
      if (rnd() > cp.p) continue;
      const cc = cp.c + gauss(rnd) * 0.042;
      const g = L.xc + cp.c * L.hw;
      sp.push({ c: L.xc + cc * L.hw, a: cp.a * (0.2 + 0.16 * rnd()), w: L.hw * (0.065 + 0.05 * rnd()), g, hill: 1 });
      const n = 1 + Math.floor(rnd() * 3);
      for (let s = 0; s < n; s++) {
        sp.push({
          c: L.xc + (cc + gauss(rnd) * cp.s) * L.hw,
          a: cp.a * (0.32 + 0.68 * Math.pow(rnd(), 0.9)) / Math.sqrt(n),
          w: (2.4 + 7.6 * Math.pow(rnd(), 1.6)) * sc, g, hill: 0,
        });
      }
    }
    if (rnd() < 0.42 || sp.length < 2) {
      sp.push({ c: L.xc + clamp(gauss(rnd) * 0.42, -0.8, 0.8) * L.hw, a: 0.16 + 0.28 * rnd(), w: (3 + 9 * rnd()) * sc, g: NaN, hill: 0 });
    }
    const np = sp.length;
    const r = {
      y, i, amp, V, np, yNow: y,
      pc: new Float32Array(np), pa: new Float32Array(np), pw: new Float32Array(np), pg: new Float32Array(np),
      dA: new Float32Array(np), dF: new Float32Array(np), dP: new Float32Array(np),
      bF: new Float32Array(np), bP: new Float32Array(np),
      cNow: new Float32Array(np), aNow: new Float32Array(np),
      bc: L.xc + gauss(rnd) * 0.1 * L.hw, bw: L.hw * (0.42 + 0.2 * rnd()), ba: 0.07 + 0.1 * rnd(),
      o1: rnd() * 900, o2: rnd() * 900, o3: rnd() * 900, o4: rnd() * 900,
      p1: rnd() * 60, p2: rnd() * 60, p3: rnd() * 60,
      alpha: 0.56 + 0.4 * rr,
      ys: new Float32Array(L.M), cl: new Float32Array(L.M), clMax: 0,
      m: 0, mTarget: 0, dx: 0, flick: 1, wire: 0, conv: 0,
      plucks: [],
    };
    for (let j = 0; j < np; j++) {
      const s = sp[j];
      r.pc[j] = s.c;
      r.pa[j] = s.a;
      r.pw[j] = 1 / (s.w * s.w);
      r.pg[j] = s.g;
      r.dA[j] = (s.hill ? 1 + rnd() * 3 : 2 + rnd() * 7) * sc;
      r.dF[j] = 0.08 + rnd() * 0.26;
      r.dP[j] = rnd() * 6.283;
      r.bF[j] = 0.05 + rnd() * 0.22;
      r.bP[j] = rnd() * 6.283;
    }
    // 归一:整行轮廓最高处 = 1(再乘行幅度)
    let mx = r.ba;
    for (let q = 0; q <= 240; q++) {
      const x = L.xc + (q / 120 - 1) * L.hw;
      let s = r.ba * Math.exp(-(x - r.bc) * (x - r.bc) / (r.bw * r.bw));
      for (let j = 0; j < np; j++) { const d = x - r.pc[j]; s += r.pa[j] * Math.exp(-d * d * r.pw[j]); }
      if (s > mx) mx = s;
    }
    r.ba /= mx;
    for (let j = 0; j < np; j++) r.pa[j] /= mx;
    return r;
  }

  function layout() {
    const W = root.clientWidth || window.innerWidth;
    const mobile = W <= 760;
    const H = mobile ? Math.max(window.innerHeight, body.scrollHeight) : window.innerHeight;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    Object.assign(L, { W, H, dpr, mobile });
    cv.style.width = W + 'px';
    cv.style.height = H + 'px';
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);

    const sy = window.scrollY || 0;
    const ra = fAcc.getBoundingClientRect();
    const rp = fPw.getBoundingClientRect();
    const ys = [];
    let gap, yA = -1, yP = -1;
    if (!mobile) {
      L.inputLeft = ra.left;
      L.wireX = Math.round(ra.right);
      L.fadeA = L.wireX + 56;
      L.fadeB = L.fadeA + Math.min(250, W * 0.16);
      L.fadeD = W - Math.max(24, W * 0.025);
      L.fadeC = L.fadeD - Math.min(230, W * 0.14);
      const span = L.fadeD - L.fadeA;
      L.xc = L.fadeA + span * 0.54;
      L.hw = span * 0.34;
      yA = ra.bottom - 0.5 + sy;
      yP = rp.bottom - 0.5 + sy;
      const k = Math.max(3, Math.round((yP - yA) / 11.6));
      gap = (yP - yA) / k;
      const top = Math.max(124, H * 0.15);
      const bottom = H - Math.max(84, H * 0.1);
      const first = yA - Math.floor((yA - top) / gap) * gap;
      for (let y = first; y <= bottom + 0.01; y += gap) ys.push(y);
      L.x0 = Math.floor(ra.left);
    } else {
      const rb = band.getBoundingClientRect();
      L.inputLeft = ra.left;
      L.wireX = 0;
      L.fadeA = 0;
      L.fadeB = Math.min(70, W * 0.18);
      L.fadeD = W;
      L.fadeC = W - L.fadeB;
      L.xc = W * 0.54;
      L.hw = W * 0.31;
      gap = 9;
      const top = rb.top + sy + gap * 5.2;
      const bottom = rb.bottom + sy - gap * 1.2;
      for (let y = top; y <= bottom + 0.01; y += gap) ys.push(y);
      L.x0 = 0;
    }
    L.gap = gap;
    L.top = ys[0] || 0;
    L.bottom = ys[ys.length - 1] || 0;
    L.yMid = mobile ? (L.top + L.bottom) / 2 : H / 2;

    const step = 3;
    const M = Math.max(2, Math.ceil((L.fadeD - L.x0) / step) + 1);
    L.step = step;
    L.M = M;
    L.xs = new Float32Array(M);
    L.env = new Float32Array(M);
    L.envC = new Float32Array(M);
    for (let k = 0; k < M; k++) {
      const x = Math.min(L.fadeD, L.x0 + k * step);
      L.xs[k] = x;
      const u = (x - L.xc) / L.hw;
      L.env[k] = 1 / (1 + Math.pow(Math.abs(u) / 0.86, 6));
      L.envC[k] = Math.exp(-u * u / 2.4) * sstep(L.fadeA, L.fadeB, x) * (1 - sstep(L.fadeC, L.fadeD, x));
    }
    L.kA = mobile ? 0 : clamp(Math.floor((L.fadeA - L.x0) / step), 0, M - 1);
    L.kW = mobile ? 0 : clamp(Math.floor((L.wireX - L.x0) / step), 0, M - 1);

    const rnd = mulberry(20261022);
    const N = ys.length;
    const prev = rows;
    rows = ys.map((y, i) => makeRow(y, i, N, rnd));
    L.accRow = mobile ? N - 1 : ys.findIndex((y) => Math.abs(y - yA) < 0.6);
    L.pwRow = mobile ? N - 1 : ys.findIndex((y) => Math.abs(y - yP) < 0.6);
    if (!mobile) {
      if (L.accRow >= 0) rows[L.accRow].wire = 1;
      if (L.pwRow >= 0) rows[L.pwRow].wire = 2;
    }
    // 保留静默状态,避免缩放窗口时闪一下
    if (prev.length) for (const r of rows) { r.m = r.mTarget = prev[0].mTarget; }

    // 浮现区域:吉祥物浮雕的盒子
    const span = L.bottom - L.top;
    const relH = mobile ? span * 1.05 : span * 0.64;
    const relW = relH * (112 / 122);
    L.rel = {
      x: L.xc - relW / 2,
      y: (mobile ? L.top - span * 0.08 : L.top + span * 0.17),
      w: relW, h: relH,
      amp: gap * (mobile ? 4.0 : 5.2),
    };
    measureReveal();
  }

  // ---------- 状态 ----------
  const P = { sig: 1, noise: 1, coh: 0, nSpeed: 1 };      // 平滑后的全局参数
  const T = { sig: 1, noise: 1, coh: 0, nSpeed: 1 };      // 目标
  let state = 'idle';
  let focusField = null;
  let focusAmt = 0, pwAmt = 0;
  let vt = 37.3, tn = 11.7, tFast = 0;                  // 环境时钟(可缓停)、噪声时钟、静电时钟
  let speedK = 1;
  const tun = { x: -9999, y: -9999, tx: -9999, ty: -9999, str: 0, strT: 0, vx: 0, vy: 0, auto: false, user: false, rx: 180, ry: 80, still: 0, dial: -1, armed: true };
  const pulses = [];
  const errFx = { on: false, t0: 0, nextGlitch: 0 };
  const succ = { on: false, t0: 0, landed: false, arrived: 0 };
  const intro = { on: false, t0: 0, needle: -1e4 };
  const relief = { on: false, t0: 0, R: 0, cool: 0 };
  const muteFx = { t0: -10, on: false };
  let lastInteract = nowS();

  // 开场时间表(秒)
  const IT = {
    beamStart: 0.12, beamSpread: 0.42, beamDur: 0.95,
    needleStart: 0.92, needleDur: 1.6,
    relief: 2.4, end: 3.1,
  };

  // ---------- 显影(开场扫描针) ----------
  let rvLeft = [];
  function measureReveal() {
    rvLeft = rvEls.map((el) => {
      const r = el.getBoundingClientRect();
      return { el, left: r.left, right: r.right };
    });
  }
  function applyReveal(nx) {
    for (const o of rvLeft) o.el.style.setProperty('--rv', (nx - o.left).toFixed(1) + 'px');
  }

  // ---------- 每行计算 ----------
  function computeRow(r, t, ctxv) {
    const { xs, env, envC, M, gap } = L;
    const ys = r.ys, cl = r.cl;
    const k0 = L.mobile ? 0 : (r.wire ? 0 : L.kA);
    const m = r.m;
    for (let j = 0; j < r.np; j++) {
      let c = r.pc[j] + r.dA[j] * Math.sin(vt * r.dF[j] * 6.283 + r.dP[j]);
      let a = r.pa[j] * (0.76 + 0.24 * Math.sin(vt * r.bF[j] * 6.283 + r.bP[j]));
      // 调谐吸附:清晰处的尖峰向所属分量的中心靠拢(行行对齐 = 有序),游离尖峰淡出
      const snap = cl[clamp(Math.round((c - L.x0) / L.step), 0, M - 1)] * 0.92;
      if (snap > 0.001) {
        const g = r.pg[j];
        if (g === g) c += (g - c) * snap; else a *= 1 - snap;
      }
      r.cNow[j] = c;
      r.aNow[j] = a;
    }
    const live = (1 - 0.965 * m) * (1 - ctxv.flat);
    const R = ctxv.R;
    const sigK = P.sig * ctxv.over * live * (1 - 0.95 * R) * r.amp;
    const noiK = P.noise * live * (1 - 0.6 * R) * gap * 1.0;
    const tailK = P.noise * live * gap * 0.11;
    const jam = errorVisible && r.wire && !L.mobile ? gap * 0.36 * (1 - ctxv.flat) : 0;
    const errS = ctxv.errS * (1 - 0.8 * m) * (1 - ctxv.flat);
    const cohA = P.coh * (1 - ctxv.flat) * gap * (2.2 + 3.6 * r.V);
    const relA = R * L.rel.amp * live;
    const rel = L.rel;
    const relV = (r.y - rel.y) / rel.h;
    const relOn = relA > 0.01 && relV > 0 && relV < 1;
    const introOn = ctxv.introOn;
    const nx = ctxv.nx;
    const pl = r.plucks;
    const dx = r.dx;
    const t1 = tn * 0.22 + r.p1, t2 = tn * 0.4 + r.p2, t3 = tn * 0.62 + r.p3, t4 = tFast + r.p1;
    const kph = ctxv.cohPhase;
    const minH = -0.42 * gap;
    const yBase = r.yNow;

    for (let k = k0; k < M; k++) {
      const x = xs[k] + dx;
      const c = cl[k];
      const clean = 1 - c;
      const e = env[k];
      const rv = introOn ? sstep(nx + 14, nx - 170, x) : 1;
      // 1) 噪声:三层值噪声,向上偏置(像脉冲星记录的毛刺);调谐处被抚平
      let n = 0.56 * vnoise(x * 0.038 + r.o1, t1) + 0.3 * vnoise(x * 0.11 + r.o2, t2) + 0.2 * vnoise(x * 0.26 + r.o3, t3);
      n = n > 0 ? n : n * 0.4;
      let h = n * clean * (e * noiK * (0.45 + 0.55 * rv) + tailK);
      // 2) 信号:宽包络 + 分量小丘 + 尖峰;噪声按乘性叠在峰上,让峰顶毛糙(调谐越清晰越平滑)
      if (sigK > 0.01 && e > 0.002) {
        let s = r.ba * Math.exp(-(x - r.bc) * (x - r.bc) / (r.bw * r.bw));
        const wk = 1 / ((1 + 1.75 * c) * (1 + 1.75 * c));
        const ak = 1 - 0.12 * c;
        for (let j = 0; j < r.np; j++) {
          const d = x - r.cNow[j];
          const q = d * d * r.pw[j] * wk;
          if (q < 10) s += r.aNow[j] * ak * Math.exp(-q);
        }
        h += e * s * sigK * rv * (1 + 0.55 * n * clean);
      }
      // 3) 静电(开场未调谐处 / 出错);出错后账号、密码两条线留一点「卡顿」直到重新输入
      const st = errS + (introOn ? ctxv.introS * (1 - rv) : 0);
      if (st > 0.01) {
        const n2 = vnoise(x * 0.47 + r.o4, t4) + 0.5 * vnoise(x * 1.3 + r.o2, t4 * 1.7);
        h += (n2 > 0 ? n2 : n2 * 0.55) * st * (0.25 + 0.75 * Math.max(e, 0.15));
      }
      if (jam > 0) {
        const fall = Math.exp(-Math.max(0, x - L.wireX) / 240);
        if (fall > 0.02) h += vnoise(x * 0.5 + r.o4, t4) * jam * fall;
      }
      // 4) 相干波(登录中):所有行同相,向前传播
      if (cohA > 0.05) {
        let w = 0.5 + 0.5 * Math.cos((x - L.fadeA) * kph - ctxv.cohT);
        w = w * w; w = w * w * w;
        h += cohA * envC[k] * (0.15 + 0.85 * w);
      }
      // 5) 吉祥物浮雕
      if (relOn) {
        const u = (x - rel.x) / rel.w;
        if (u > 0 && u < 1) h += relA * hmSample(u, relV) * (1 + 0.14 * n);
      }
      // 6) 拨弦余波
      for (let q = 0; q < pl.length; q++) {
        const p = pl[q];
        const age = t - p.t0;
        const d = (x - p.x) / 38;
        if (d > -3 && d < 3) h += p.a * Math.exp(-d * d) * Math.sin(age * 26) * Math.exp(-age / 0.5);
      }
      if (h < minH) h = minH;
      ys[k] = yBase - h;
    }
    // 7) 按键脉冲(只加在本行及相邻行)
    for (let q = 0; q < pulses.length; q++) {
      const p = pulses[q];
      const dr = r.i - p.row;
      if (dr < -1 || dr > 1) continue;
      const wt = dr === 0 ? 1 : 0.42;
      const age = t - p.t0 - (dr === 0 ? 0 : 0.035);
      if (age < 0) continue;
      const xp = p.x0 + p.v * age;
      const life = 1 - sstep(L.fadeD - 160, L.fadeD, xp);
      const grow = sstep(p.x0, L.fadeA + 60, xp);
      const a = p.a * wt * life * (0.4 + 0.6 * grow) * (1 - 0.9 * m);
      const kL = clamp(Math.floor((xp - 4 * p.w - L.x0) / L.step), k0, M - 1);
      const kR = clamp(Math.ceil((xp + 4 * p.w - L.x0) / L.step), k0, M - 1);
      for (let k = kL; k <= kR; k++) {
        const d = (xs[k] - xp) / p.w;
        ys[k] -= a * Math.exp(-d * d);
      }
    }
  }

  // ---------- 更新 ----------
  let lastT = 0;
  const ctxv = { flat: 0, R: 0, errS: 0, introOn: false, introS: 0, nx: -1e4, over: 1, cohPhase: 0, cohT: 0 };

  function approach(cur, target, dt, tau) { return cur + (target - cur) * (1 - Math.exp(-dt / tau)); }

  function update(t, dt) {
    vt += dt * speedK;
    P.nSpeed = approach(P.nSpeed, T.nSpeed, dt, 0.25);
    tn += dt * speedK * P.nSpeed;
    tFast += dt * 13;
    P.sig = approach(P.sig, T.sig, dt, 0.32);
    P.noise = approach(P.noise, T.noise, dt, 0.32);
    P.coh = approach(P.coh, T.coh, dt, 0.42);
    focusAmt = approach(focusAmt, focusField === 'account' && state !== 'success' ? 1 : 0, dt, 0.18);
    pwAmt = approach(pwAmt, focusField === 'password' && state !== 'success' ? 1 : 0, dt, 0.18);

    // 调谐器
    if (tun.auto || (L.mobile && !tun.user)) {
      const span = L.bottom - L.top;
      tun.tx = L.xc + L.hw * 1.15 * Math.sin(vt * 0.23 + 0.6);
      tun.ty = (L.top + L.bottom) / 2 + span * 0.36 * Math.sin(vt * 0.17 + 1.1);
      tun.strT = 0.9;
      if (tun.x < -1000) { tun.x = tun.tx; tun.y = tun.ty; }
    }
    const px = tun.x, py = tun.y;
    if (tun.x < -1000 && tun.tx > -1000) { tun.x = tun.tx; tun.y = tun.ty; }
    tun.x = approach(tun.x, tun.tx, dt, 0.11);
    tun.y = approach(tun.y, tun.ty, dt, 0.11);
    tun.vx = (tun.x - px) / Math.max(dt, 1e-3);
    tun.vy = (tun.y - py) / Math.max(dt, 1e-3);
    tun.str = approach(tun.str, tun.strT * (1 - pwAmt) * (state === 'loading' ? 0.25 : 1), dt, 0.3);
    tun.rx = clamp(L.W * 0.125, 118, 250);
    const dialT = tun.str > 0.05 ? tun.x : L.xc;
    tun.dial = tun.dial < 0 ? dialT : approach(tun.dial, dialT, dt, 0.16);
    tun.ry = L.gap * 6.5;

    // 久停在峰群正中 → 浮现彩蛋
    const speed = Math.hypot(tun.vx, tun.vy);
    const nearC = Math.abs(tun.x - L.xc) < L.hw * 0.25 && Math.abs(tun.y - (L.rel.y + L.rel.h * 0.5)) < L.rel.h * 0.3;
    if (!nearC) tun.armed = true;                      // 离开中心后才重新上膛,避免停着的鼠标反复触发
    if (!intro.on && tun.armed && tun.str > 0.5 && nearC && speed < 18 && state === 'idle' && !tun.auto) tun.still += dt; else tun.still = 0;
    if (tun.still > 1.8 && t > relief.cool) { triggerRelief(t); tun.still = 0; tun.armed = false; }

    // 开场
    ctxv.introOn = intro.on;
    ctxv.over = 1;
    if (intro.on) {
      const it = t - intro.t0;
      const nt = clamp((it - IT.needleStart) / IT.needleDur, 0, 1);
      intro.needle = lerp(-40, L.W + 40, easeSine(nt));
      ctxv.nx = intro.needle;
      ctxv.introS = L.gap * 0.95;
      ctxv.over = 1 + 0.16 * sstep(1.9, 2.4, it) * (1 - sstep(2.5, 3.4, it));
      applyReveal(intro.needle);
      if (!relief.on && it > IT.relief && it < IT.relief + 0.1 && hm) triggerRelief(t);
      if (nt >= 1 && body.classList.contains('intro')) body.classList.remove('intro');
      if (it > IT.end || (nt >= 1 && !hm && it > 3.2)) finishIntro(true);
    } else {
      ctxv.nx = 1e5;
    }

    // 浮现权重 R:升 0.6s,停 1.15s,落 0.85s
    if (relief.on) {
      const a = t - relief.t0;
      relief.R = a < 0.6 ? easeInOut(a / 0.6) : a < 1.75 ? 1 : a < 2.6 ? 1 - easeInOut((a - 1.75) / 0.85) : 0;
      if (a >= 2.6) { relief.on = false; relief.R = 0; }
    }
    ctxv.R = relief.R;

    // 出错静电:突发 → 回落
    let errS = 0;
    if (errFx.on) {
      const a = t - errFx.t0;
      const env = a < 0.07 ? a / 0.07 : Math.exp(-(a - 0.07) / 0.55);
      errS = env * L.gap * 1.25;
      if (t > errFx.nextGlitch) {
        errFx.nextGlitch = t + 0.06;
        const rnd = Math.random;
        for (const r of rows) {
          const hit = rnd() < 0.34 * env;
          r.dx = hit ? (rnd() - 0.5) * 26 * env : 0;
          r.flick = hit && rnd() < 0.5 ? 0.35 + 0.4 * rnd() : 1;
        }
      }
      if (a > 2.6) { errFx.on = false; for (const r of rows) { r.dx = 0; r.flick = 1; } }
    }
    ctxv.errS = errS;

    // 成功:收束系数
    let flat = 0;
    if (succ.on) {
      const a = t - succ.t0;
      flat = sstep(0, 0.32, a);
      const half = Math.max(1, (L.bottom - L.top) / 2);
      let arrived = 0;
      for (const r of rows) {
        const dist = Math.min(1, Math.abs(r.y - L.yMid) / half);
        const p = clamp((a - (0.3 + dist * 0.32)) / 0.3, 0, 1);
        r.conv = p * p * p;
        r.yNow = lerp(r.y, L.yMid, r.conv);
        if (p >= 1) arrived++;
      }
      succ.arrived = arrived / Math.max(1, rows.length);
    }
    ctxv.flat = flat;

    // 相干波相位
    const span = Math.max(200, L.fadeD - L.fadeA);
    ctxv.cohPhase = 6.283 / (span * 0.22);
    ctxv.cohT = t * 6.283 * 2.1;

    // 静默:从密码那一行向外逐行扩散
    const ms = t - muteFx.t0;
    for (const r of rows) {
      const d = Math.abs(r.i - Math.max(0, L.pwRow)) * (muteFx.on ? 0.011 : 0.007);
      if (ms >= d) r.mTarget = muteFx.on ? 1 : 0;
      r.m = approach(r.m, r.mTarget, dt, muteFx.on ? 0.2 : 0.28);
    }

    // 调谐清晰度场:快升慢落(留下余迹)
    const up = 1 - Math.exp(-dt / 0.07), dn = 1 - Math.exp(-dt / 0.95);
    const irx2 = 1 / (tun.rx * tun.rx), iry2 = 1 / (tun.ry * tun.ry);
    for (const r of rows) {
      const dy = r.y - tun.y;
      const fy = dy * dy * iry2;
      const active = tun.str > 0.003 && fy < 7;
      if (!active && r.clMax < 0.002) continue;
      const cl = r.cl, xs = L.xs;
      let mx = 0;
      for (let k = 0; k < L.M; k++) {
        let target = 0;
        if (active) {
          const dxx = xs[k] - tun.x;
          const f = dxx * dxx * irx2 + fy;
          if (f < 7) target = tun.str * Math.exp(-f);
        }
        const v = cl[k];
        const nv = v + (target - v) * (target > v ? up : dn);
        cl[k] = nv;
        if (nv > mx) mx = nv;
      }
      r.clMax = mx;
    }

    // 拨弦:调谐器纵向穿过某一行时轻拨一下
    if (!L.mobile && tun.str > 0.4 && Math.abs(tun.vy) > 60 && !intro.on && state !== 'success') {
      const y0 = Math.min(py, tun.y), y1 = Math.max(py, tun.y);
      for (const r of rows) {
        if (r.y > y0 && r.y <= y1 && r.plucks.length < 4) {
          r.plucks.push({ x: tun.x, t0: t, a: clamp(Math.abs(tun.vy) * 0.0022, 0.6, 3.2) * (L.gap / 12) });
        }
      }
    }
    for (const r of rows) {
      if (r.plucks.length) r.plucks = r.plucks.filter((p) => t - p.t0 < 2.4);
    }
    for (let q = pulses.length - 1; q >= 0; q--) {
      const p = pulses[q];
      if (p.x0 + p.v * (t - p.t0) > L.fadeD + 60) pulses.splice(q, 1);
    }

    for (const r of rows) computeRow(r, t, ctxv);
  }

  // ---------- 绘制 ----------
  function hGrad(x0, x1, stops) {
    const g = ctx.createLinearGradient(x0, 0, x1, 0);
    for (const s of stops) g.addColorStop(clamp(s[0], 0, 1), s[1]);
    return g;
  }

  function strokeRange(r, k0, k1) {
    const xs = L.xs, ys = r.ys;
    ctx.beginPath();
    ctx.moveTo(xs[k0], ys[k0]);
    for (let k = k0 + 1; k <= k1; k++) ctx.lineTo(xs[k], ys[k]);
  }

  function draw(t) {
    const { W, H, dpr, xs, M, gap } = L;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    ctx.clearRect(0, 0, W, H);
    if (!rows.length) return;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    const lw = dpr >= 2 ? 1.15 : 1.3;

    // 成功阶段两端渐隐收拢 → 亮线贯穿全宽
    let fA = L.fadeA, fB = L.fadeB, fC = L.fadeC, fD = L.fadeD;
    let flare = 0, curtain = 0, sA = 0;
    let mA = fA, mB = fB, mC = fC, mD = fD;          // 合并亮线的渐隐端点(向全宽展开)
    if (succ.on) {
      sA = t - succ.t0;
      const open = easeInOut(clamp((sA - 0.5) / 0.5, 0, 1));
      mA = lerp(fA, -40, open); mB = lerp(fB, -20, open); mC = lerp(fC, W + 20, open); mD = lerp(fD, W + 40, open);
      flare = sstep(0.86, 1.02, sA) * (1 - sstep(1.3, 1.62, sA));
      curtain = easeInOutQuart(clamp((sA - 1.0) / 0.6, 0, 1));
    }
    const span = fD - fA;
    const line = (a) => 'rgba(' + LINE + ',' + a + ')';
    const gNorm = hGrad(fA, fD, [[0, line(0)], [(fB - fA) / span, line(1)], [(fC - fA) / span, line(1)], [1, line(0)]]);
    const wspan = fD - L.wireX;
    const gWire = L.mobile ? gNorm : hGrad(L.wireX, fD, [[0, line(0.3)], [(fB - L.wireX) / wspan, line(1)], [(fC - L.wireX) / wspan, line(1)], [1, line(0)]]);
    const tintG = tun.str > 0.01 ? hGrad(tun.x - tun.rx * 1.25, tun.x + tun.rx * 1.25, [[0, 'rgba(' + TINT + ',0)'], [0.5, 'rgba(' + TINT + ',1)'], [1, 'rgba(' + TINT + ',0)']]) : null;
    const fillOn = !succ.on || sA < 0.3;

    const it = intro.on ? t - intro.t0 : 99;
    const N = rows.length;

    for (let n = 0; n < N; n++) {
      const r = rows[n];
      const isWire = r.wire && !L.mobile && !succ.on && (!intro.on || intro.needle > L.fadeA);
      const k0 = L.mobile ? 0 : (isWire ? L.kW : L.kA);
      let k1 = M - 1;
      let beam = 1;
      if (intro.on) {
        const bs = IT.beamStart + IT.beamSpread * (n / Math.max(1, N - 1));
        beam = easeSine(clamp((it - bs) / IT.beamDur, 0, 1));
        if (beam <= 0) continue;
        const bx = lerp(L.fadeA, L.fadeD, beam);
        k1 = clamp(Math.ceil((bx - L.x0) / L.step), L.kA + 1, M - 1);
      }
      if (k1 <= k0) continue;
      if (succ.on && r.conv >= 1) continue;
      const ys = r.ys;
      const base = r.yNow;

      // 遮挡:用底色填充曲线下方,盖住后排
      if (fillOn) {
        strokeRange(r, L.mobile ? 0 : L.kA, k1);
        ctx.lineTo(xs[k1], base + gap * 0.62);
        ctx.lineTo(xs[L.mobile ? 0 : L.kA], base + gap * 0.62);
        ctx.closePath();
        ctx.globalAlpha = 1;
        ctx.fillStyle = BG;
        ctx.fill();
      }

      // 主线
      const aRow = r.alpha * (1 - 0.74 * r.m) * r.flick * (succ.on ? lerp(1, 1 / r.alpha, r.conv) : 1);
      strokeRange(r, k0, k1);
      ctx.lineWidth = lw;
      ctx.strokeStyle = isWire ? gWire : gNorm;
      ctx.globalAlpha = clamp(aRow, 0, 1);
      ctx.stroke();

      // 调谐器染色(靛)
      if (tintG && !succ.on) {
        const dy = r.y - tun.y;
        const f = tun.str * Math.exp(-dy * dy / (tun.ry * tun.ry)) * (1 - r.m);
        if (f > 0.02) {
          const kL = clamp(Math.floor((tun.x - tun.rx * 1.3 - L.x0) / L.step), k0, k1);
          const kR = clamp(Math.ceil((tun.x + tun.rx * 1.3 - L.x0) / L.step), k0, k1);
          if (kR > kL) {
            strokeRange(r, kL, kR);
            ctx.strokeStyle = tintG;
            ctx.globalAlpha = f * 0.74 * r.alpha;
            ctx.stroke();
          }
        }
      }

      // 选中的「频道」:账号行通体染靛;密码行在静默中留一条暗靛
      if (r.wire === 1 && focusAmt > 0.01 && !L.mobile) {
        strokeRange(r, L.kW, k1);
        ctx.strokeStyle = hGrad(L.wireX, L.fadeD, [[0, 'rgba(' + TINT + ',.95)'], [0.4, 'rgba(' + TINT + ',.7)'], [1, 'rgba(' + TINT + ',0)']]);
        ctx.globalAlpha = focusAmt;
        ctx.stroke();
      }
      if (r.wire && errorVisible && !L.mobile && !succ.on) {
        const kE = clamp(L.kW + Math.ceil(460 / L.step), L.kW + 1, k1);
        strokeRange(r, L.kW, kE);
        ctx.strokeStyle = hGrad(L.wireX, L.wireX + 460, [[0, 'rgba(255,149,140,.85)'], [0.55, 'rgba(255,149,140,.28)'], [1, 'rgba(255,149,140,0)']]);
        ctx.globalAlpha = 1;
        ctx.stroke();
      }
      if (r.wire === 2 && pwAmt > 0.01 && !L.mobile) {
        strokeRange(r, L.kW, k1);
        ctx.strokeStyle = hGrad(L.wireX, L.fadeD, [[0, 'rgba(' + TINT + ',.75)'], [0.5, 'rgba(' + TINT + ',.32)'], [1, 'rgba(' + TINT + ',0)']]);
        ctx.globalAlpha = pwAmt;
        ctx.stroke();
      }

      // 按键脉冲:亮头 + 靛色拖尾
      for (let q = 0; q < pulses.length; q++) {
        const p = pulses[q];
        const dr = r.i - p.row;
        if (dr < -1 || dr > 1) continue;
        const age = t - p.t0 - (dr === 0 ? 0 : 0.035);
        if (age < 0) continue;
        const xp = p.x0 + p.v * age;
        const tail = 190;
        const kMin = (r.wire || L.mobile) ? 0 : L.kA;
        const kL = clamp(Math.floor((xp - tail - L.x0) / L.step), kMin, k1);
        const kR = clamp(Math.ceil((xp + p.w * 3 - L.x0) / L.step), kMin, k1);
        if (kR <= kL) continue;
        const life = 1 - sstep(L.fadeD - 160, L.fadeD, xp);
        strokeRange(r, kL, kR);
        ctx.strokeStyle = hGrad(xp - tail, xp + p.w * 3, [[0, 'rgba(' + TINT + ',0)'], [0.82, 'rgba(' + TINT + ',.85)'], [0.94, 'rgba(255,255,255,1)'], [1, 'rgba(255,255,255,0)']]);
        ctx.globalAlpha = (dr === 0 ? 1 : 0.4 * sstep(L.fadeA, L.fadeB, xp - 30)) * life * (1 - 0.9 * r.m);
        ctx.lineWidth = lw * 1.25;
        ctx.stroke();
        ctx.lineWidth = lw;
      }

      // 开场束流的光点
      if (intro.on && beam < 1) {
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = 'rgb(222,220,255)';
        ctx.beginPath();
        ctx.arc(xs[k1], ys[k1], 1.7, 0, 6.283);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;

    // 调谐刻度(桌面):场底一条细刻度,靛色指针随调谐器走(惯性略大于镜头)
    if (!L.mobile && ctxv.flat < 1) {
      const yd = Math.round(L.bottom + 36) + 0.5;
      const xa = L.fadeB, xb = L.fadeC;
      const lim = intro.on ? intro.needle : 1e9;
      const quiet = (1 - 0.7 * pwAmt) * (1 - ctxv.flat);
      ctx.lineWidth = 1;
      ctx.strokeStyle = gNorm;
      ctx.beginPath();
      let n = 0;
      const x0t = L.xc - Math.ceil((L.xc - L.fadeA) / 16) * 16;
      for (let x = x0t; x <= L.fadeD && x < lim; x += 16, n++) {
        const px = Math.round(x) + 0.5;
        const major = Math.round((x - L.xc) / 16) % 5 === 0;
        ctx.moveTo(px, yd);
        ctx.lineTo(px, yd - (major ? 7 : 4));
      }
      ctx.globalAlpha = 0.3 * quiet;
      ctx.stroke();
      const dx = clamp(tun.dial < 0 ? L.xc : tun.dial, xa, xb);
      if (dx < lim) {
        ctx.globalAlpha = (0.5 + 0.5 * tun.str) * quiet;
        ctx.strokeStyle = 'rgb(' + TINT + ')';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(dx, yd + 4);
        ctx.lineTo(dx, yd - 14);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // 开场扫描针
    if (intro.on && intro.needle > -30 && intro.needle < W + 30) {
      const nx = intro.needle;
      const g = hGrad(nx - 90, nx + 24, [[0, 'rgba(' + TINT + ',0)'], [0.78, 'rgba(' + TINT + ',.13)'], [1, 'rgba(' + TINT + ',0)']]);
      ctx.fillStyle = g;
      ctx.fillRect(nx - 90, 0, 114, H);
      const gv = ctx.createLinearGradient(0, 0, 0, H);
      gv.addColorStop(0, 'rgba(236,236,255,0)');
      gv.addColorStop(0.18, 'rgba(236,236,255,.75)');
      gv.addColorStop(0.82, 'rgba(236,236,255,.75)');
      gv.addColorStop(1, 'rgba(236,236,255,0)');
      ctx.fillStyle = gv;
      ctx.fillRect(Math.round(nx) - 0.5, 0, 1, H);
    }

    // 成功:亮线 → 光幕
    if (succ.on) {
      const y = L.yMid;
      const got = succ.arrived || 0;
      if (got > 0) {
        const span2 = mD - mA;
        const hot = (a) => 'rgba(255,255,255,' + a + ')';
        ctx.save();
        ctx.shadowColor = 'rgba(156,153,245,.95)';
        ctx.shadowBlur = 4 + 14 * got + 22 * flare;
        ctx.strokeStyle = hGrad(mA, mD, [[0, hot(0)], [(mB - mA) / span2, hot(1)], [(mC - mA) / span2, hot(1)], [1, hot(0)]]);
        ctx.lineWidth = 1 + 0.9 * got + 1.2 * flare;
        ctx.globalAlpha = 0.3 + 0.7 * got;
        ctx.beginPath();
        ctx.moveTo(Math.min(0, mA), y);
        ctx.lineTo(Math.max(W, mD), y);
        ctx.stroke();
        ctx.restore();
      }
      if (curtain > 0) {
        const hh = curtain * (H * 0.64);
        const feather = 18 + 40 * (1 - curtain);
        const glow = 80;
        const ga = 0.42 * (1 - curtain);
        const gt = ctx.createLinearGradient(0, y - hh - glow, 0, y - hh + feather);
        gt.addColorStop(0, 'rgba(' + TINT + ',0)');
        gt.addColorStop(glow / (glow + feather), 'rgba(' + TINT + ',' + ga + ')');
        gt.addColorStop(1, 'rgba(' + TINT + ',0)');
        ctx.fillStyle = gt;
        ctx.fillRect(0, y - hh - glow, W, glow + feather);
        const gb = ctx.createLinearGradient(0, y + hh - feather, 0, y + hh + glow);
        gb.addColorStop(0, 'rgba(' + TINT + ',0)');
        gb.addColorStop(feather / (glow + feather), 'rgba(' + TINT + ',' + ga + ')');
        gb.addColorStop(1, 'rgba(' + TINT + ',0)');
        ctx.fillStyle = gb;
        ctx.fillRect(0, y + hh - feather, W, glow + feather);
        const top = y - hh - feather * 0.5, bot = y + hh + feather * 0.5, h2 = bot - top;
        const gc = ctx.createLinearGradient(0, top, 0, bot);
        const f = clamp(feather / h2, 0.02, 0.45);
        const core = curtain < 0.8 ? '#ffffff' : READER;
        gc.addColorStop(0, 'rgba(244,246,249,0)');
        gc.addColorStop(f, READER);
        gc.addColorStop(0.5, core);
        gc.addColorStop(1 - f, READER);
        gc.addColorStop(1, 'rgba(244,246,249,0)');
        ctx.fillStyle = gc;
        ctx.fillRect(0, top, W, h2);
      }
    }
  }

  // ---------- 循环(交互时 60fps,环境态约 20fps,久无交互缓停) ----------
  let raf = 0, timer = 0, sleeping = false;

  function busy(t) {
    if (intro.on || succ.on || errFx.on || relief.on || pulses.length) return true;
    if (t - lastInteract < 2.2) return true;
    if (Math.abs(tun.x - tun.tx) > 0.6 || Math.abs(tun.y - tun.ty) > 0.6) return true;
    if (Math.abs(P.sig - T.sig) > 0.004 || Math.abs(P.noise - T.noise) > 0.004 || Math.abs(P.coh - T.coh) > 0.004) return true;
    if (state === 'loading') return true;
    for (const r of rows) if (Math.abs(r.m - r.mTarget) > 0.003 || r.plucks.length) return true;
    return false;
  }

  function frame(ms) {
    raf = 0;
    if (isReduced() || document.hidden) { lastT = 0; return; }
    const t = ms / 1000;
    const dt = lastT ? Math.min(0.05, Math.max(0.001, t - lastT)) : 1 / 60;
    lastT = t;
    if (sleeping) speedK = 0;
    else speedK = Math.min(1, speedK + dt / 0.8);
    update(t, dt);
    draw(t);
    if (succ.on && t - succ.t0 > 1.62 && !succ.landed) land();
    if (succ.landed && t - succ.t0 > 2.1) { lastT = 0; return; }      // 落幅后停表
    if (busy(t)) {
      raf = requestAnimationFrame(frame);
      return;
    }
    // 环境态:降到约 20fps;40 秒无交互则用 2.5 秒缓停,画面停在最后一帧
    if (t - lastInteract > 40) {
      speedK = Math.max(0, speedK - dt / 2.5);
      if (speedK <= 0.001) { sleeping = true; lastT = 0; return; }
    }
    timer = setTimeout(() => { timer = 0; raf = requestAnimationFrame(frame); }, 1000 / 20 - 6);
  }

  function ensureLoop() {
    if (raf || timer || isReduced() || document.hidden) return;
    if (succ.landed) return;
    raf = requestAnimationFrame(frame);
  }
  function stopLoop() {
    if (raf) cancelAnimationFrame(raf);
    if (timer) clearTimeout(timer);
    raf = 0; timer = 0; lastT = 0;
  }
  function wake() {
    lastInteract = nowS();
    if (sleeping) { sleeping = false; speedK = 0.001; }
    if (timer) { clearTimeout(timer); timer = 0; }
    ensureLoop();
  }

  // ---------- 静态帧(减弱档) ----------
  function renderStatic() {
    stopLoop();
    if (!rows.length) return;
    P.sig = T.sig; P.noise = T.noise; P.coh = T.coh; P.nSpeed = 1;
    focusAmt = focusField === 'account' ? 1 : 0;
    pwAmt = focusField === 'password' ? 1 : 0;
    for (const r of rows) {
      r.m = r.mTarget = muteFx.on ? 1 : 0;
      r.cl.fill(0); r.clMax = 0; r.plucks.length = 0; r.dx = 0; r.flick = 1; r.yNow = r.y;
    }
    pulses.length = 0;
    tun.str = 0; tun.strT = 0;
    Object.assign(ctxv, {
      flat: 0, R: 0, errS: errorVisible ? L.gap * 0.34 : 0, introOn: false, introS: 0, nx: 1e5, over: 1,
      cohPhase: 6.283 / (Math.max(200, L.fadeD - L.fadeA) * 0.22), cohT: 1.3,
    });
    const keepV = vt, keepN = tn;
    vt = 37.3; tn = 11.7; tFast = 4.2;
    for (const r of rows) computeRow(r, 0, ctxv);
    draw(0);
    vt = keepV; tn = keepN;
  }

  // ---------- 开场 ----------
  function startIntro() {
    if (isReduced()) { finishIntro(false); return; }
    resetUI();
    intro.on = true;
    intro.t0 = nowS();
    intro.needle = -1e4;
    relief.on = false; relief.R = 0;
    body.classList.add('intro');
    measureReveal();
    applyReveal(-60);
    for (const r of rows) { r.cl.fill(0); r.clMax = 0; }
    lastInteract = nowS();
    sleeping = false; speedK = 1;
    stopLoop();
    ensureLoop();
  }

  function finishIntro(natural) {
    const was = intro.on;
    intro.on = false;
    body.classList.remove('intro');
    for (const o of rvLeft) o.el.style.removeProperty('--rv');
    if (!natural && was) { relief.on = false; relief.R = 0; }
    if (isReduced()) renderStatic(); else wake();
  }

  function triggerRelief(t) {
    if (!hm || isReduced() || succ.on) return;
    relief.on = true;
    relief.t0 = t;
    relief.cool = t + 9;
    wake();
  }

  // ---------- 状态切换 ----------
  const STATUS = { tune: '调谐中', mute: '静默', sync: '同步中', jam: '干扰', lock: '已锁定' };
  let jamTimer = 0;
  function setStatus(s) { statusEl.dataset.s = s; statusText.textContent = STATUS[s]; }
  function refreshStatus() {
    if (state === 'success') return setStatus('lock');
    if (state === 'loading') return setStatus('sync');
    if (jamTimer) return setStatus('jam');
    setStatus(focusField === 'password' ? 'mute' : 'tune');
  }

  function resetUI() {
    clearTimeout(submitTimer);
    state = 'idle';
    succ.on = false; succ.landed = false;
    errFx.on = false;
    for (const r of rows) { r.dx = 0; r.flick = 1; r.yNow = r.y; r.conv = 0; }
    succ.arrived = 0;
    T.sig = 1; T.noise = 1; T.coh = 0; T.nSpeed = 1;
    body.classList.remove('leaving');
    handoff.classList.remove('on');
    handoff.setAttribute('aria-hidden', 'true');
    setBusy(false);
    hideError();
    syncMute();
    refreshStatus();
  }

  function setBusy(on) {
    btn.setAttribute('aria-busy', on ? 'true' : 'false');
    btnLabel.textContent = on ? '正在登录' : '登录';
    inAcc.readOnly = on;
    inPw.readOnly = on;
  }

  let errClearT = 0;
  let errorVisible = false;
  function showError(msg) {
    errorVisible = true;
    clearTimeout(errClearT);
    errText.textContent = '';
    requestAnimationFrame(() => { errText.textContent = msg; });
    errBox.classList.add('on');
    fAcc.classList.add('bad');
    fPw.classList.add('bad');
    clearTimeout(jamTimer);
    jamTimer = setTimeout(() => { jamTimer = 0; refreshStatus(); }, 1300);
    refreshStatus();
    if (isReduced()) { renderStatic(); return; }
    errFx.on = true;
    errFx.t0 = nowS();
    errFx.nextGlitch = 0;
    wake();
  }
  function hideError() {
    if (!errBox.classList.contains('on')) return;
    errorVisible = false;
    errBox.classList.remove('on');
    fAcc.classList.remove('bad');
    fPw.classList.remove('bad');
    if (isReduced() && rows.length) setTimeout(renderStatic, 0);
    clearTimeout(errClearT);
    errClearT = setTimeout(() => { if (!errBox.classList.contains('on')) errText.textContent = ''; }, 220);
  }

  function setState(name) {
    if (intro.on) finishIntro(false);
    if (name === 'idle') { resetUI(); if (isReduced()) renderStatic(); else wake(); return; }
    if (name === 'loading') {
      if (succ.on) resetUI();
      state = 'loading';
      hideError();
      setBusy(true);
      T.sig = 0.06; T.noise = 0.07; T.coh = 1;
      syncMute();
      refreshStatus();
      if (isReduced()) renderStatic(); else wake();
      return;
    }
    if (name === 'error') {
      if (succ.on) resetUI();
      clearTimeout(submitTimer);
      state = 'idle';
      setBusy(false);
      T.sig = 1; T.noise = 1; T.coh = 0;
      syncMute();
      showError('账号或密码错误');
      return;
    }
    if (name === 'success') {
      state = 'success';
      setBusy(false);
      hideError();
      refreshStatus();
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      focusField = null;
      syncMute();
      body.classList.remove('privacy');
      srLive.textContent = '登录成功';
      if (isReduced()) { land(); return; }
      body.classList.add('leaving');
      T.coh = 0;
      succ.on = true;
      succ.t0 = nowS();
      succ.landed = false;
      tun.strT = 0;
      wake();
    }
  }

  function land() {
    succ.landed = true;
    handoff.classList.add('on');
    handoff.setAttribute('aria-hidden', 'false');
    body.classList.add('leaving');
  }

  // ---------- 表单 ----------
  let submitTimer = 0;
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (state === 'loading' || state === 'success') return;
    if (intro.on) finishIntro(false);
    const u = inAcc.value.trim();
    const p = inPw.value;
    if (!u || !p) {
      showError('请输入账号和密码');
      (u ? inPw : inAcc).focus();
      return;
    }
    setState('loading');
    clearTimeout(submitTimer);
    submitTimer = setTimeout(() => {
      if (state !== 'loading') return;
      if (u === 'admin' && p === 'admin') setState('success');
      else {
        setBusy(false);
        T.coh = 0; T.sig = 1; T.noise = 1;
        state = 'idle';
        syncMute();
        showError('账号或密码错误');
      }
    }, 900);
  });

  // 文字宽度估算(脉冲从光标处出发)
  const measurer = document.createElement('canvas').getContext('2d');
  function caretX(input) {
    const cs = getComputedStyle(input);
    measurer.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    const pos = input.selectionStart == null ? input.value.length : input.selectionStart;
    const txt = input.type === 'password' ? '•'.repeat(pos) : input.value.slice(0, pos);
    const ls = parseFloat(cs.letterSpacing) || 0;
    const w = measurer.measureText(txt).width + ls * txt.length;
    const rect = input.getBoundingClientRect();
    return Math.min(rect.right - 4, rect.left + w - input.scrollLeft);
  }

  function emitPulse() {
    if (L.accRow < 0 || !rows.length) return;
    const x0 = L.mobile ? 0 : caretX(inAcc) + 3;
    if (pulses.length > 18) pulses.shift();
    pulses.push({ row: L.accRow, x0, t0: nowS(), v: L.mobile ? 520 : 1050, a: L.gap * 1.35, w: L.mobile ? 6 : 8 });
    wake();
  }

  function pwTick() {
    const fr = fPw.getBoundingClientRect();
    const from = caretX(inPw) - fr.left;
    const s = document.createElement('span');
    s.className = 'tick';
    s.style.setProperty('--from', Math.max(0, from - 20) + 'px');
    s.style.setProperty('--to', (fr.width - 22) + 'px');
    fPw.appendChild(s);
    s.addEventListener('animationend', () => s.remove());
  }

  inAcc.addEventListener('input', () => { hideError(); if (!isReduced() && state !== 'success') emitPulse(); });
  inPw.addEventListener('input', () => { hideError(); if (!isReduced() && state !== 'success') pwTick(); wake(); });

  function onFocusChange() {
    const a = document.activeElement;
    const next = a === inAcc ? 'account' : a === inPw ? 'password' : null;
    if (next === focusField) return;
    focusField = next;
    body.classList.toggle('privacy', next === 'password');
    syncMute();
    refreshStatus();
    if (isReduced()) renderStatic(); else wake();
  }
  // 静默只在空闲时成立:提交后(登录中 / 成功)信号恢复,即使焦点仍在密码框
  function syncMute() {
    const want = focusField === 'password' && state !== 'loading' && state !== 'success';
    if (want !== muteFx.on) { muteFx.on = want; muteFx.t0 = nowS(); }
  }
  document.addEventListener('focusin', onFocusChange);
  document.addEventListener('focusout', () => setTimeout(onFocusChange, 0));

  // ---------- 指针 = 调谐器 ----------
  window.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch' && !L.mobile) return;
    if (e.target && e.target.closest && e.target.closest('[data-sample-kit]')) return;
    if (e.pointerType === 'touch') tun.user = true;
    const sy = L.mobile ? (window.scrollY || 0) : 0;
    tun.tx = e.clientX;
    tun.ty = e.clientY + sy;
    if (tun.x < -1000) { tun.x = tun.tx; tun.y = tun.ty; }
    tun.strT = 1;
    wake();
  }, { passive: true });
  window.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'touch') return;
    tun.user = true;
    tun.tx = e.clientX;
    tun.ty = e.clientY + (window.scrollY || 0);
    tun.strT = 1;
    wake();
  }, { passive: true });
  window.addEventListener('pointerup', (e) => {
    if (e.pointerType !== 'touch') return;
    setTimeout(() => { tun.user = false; }, 1600);
  }, { passive: true });
  document.documentElement.addEventListener('pointerleave', () => { if (!tun.auto && !L.mobile) tun.strT = 0; wake(); });

  // 开场可被任意点击 / 按键跳过(样页控制条除外)
  function skipHandler(e) {
    if (!intro.on) return;
    if (e.target && e.target.closest && e.target.closest('[data-sample-kit]')) return;
    finishIntro(false);
  }
  window.addEventListener('pointerdown', skipHandler, true);
  window.addEventListener('keydown', skipHandler, true);
  window.addEventListener('keydown', () => wake());

  // ---------- 动效档位 / 可见性 / 尺寸 ----------
  window.addEventListener('sample:motion', () => {
    if (isReduced()) {
      if (intro.on) finishIntro(false);
      relief.on = false; relief.R = 0;
      if (succ.on && !succ.landed) land();
      renderStatic();
    } else {
      if (!succ.landed) wake();
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopLoop();
    else if (!isReduced()) { lastT = 0; wake(); }
  });
  let rsT = 0;
  function onResize() {
    clearTimeout(rsT);
    rsT = setTimeout(() => {
      layout();
      if (isReduced() || succ.landed) renderStatic(); else wake();
    }, 90);
  }
  window.addEventListener('resize', onResize);
  if (window.ResizeObserver) new ResizeObserver(onResize).observe(form);

  // ---------- 本地时钟(客户端可得,真用) ----------
  const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  function tick() {
    const d = new Date();
    const p2 = (n) => String(n).padStart(2, '0');
    clockEl.textContent = p2(d.getMonth() + 1) + '.' + p2(d.getDate()) + ' ' + WEEK[d.getDay()] + '  ' + p2(d.getHours()) + ':' + p2(d.getMinutes());
    setTimeout(tick, (60 - d.getSeconds()) * 1000 + 50);
  }
  tick();

  // ---------- 启动 ----------
  layout();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { layout(); if (isReduced()) renderStatic(); });
  if (isReduced()) { finishIntro(false); renderStatic(); } else startIntro();

  window.LoginSample = {
    replay() { startIntro(); },
    skipIntro() { finishIntro(false); },
    setState(name) { setState(name); },
    extraControls: [
      { label: '浮现吉祥物', run() { if (intro.on) finishIntro(false); triggerRelief(nowS()); } },
      { label: '自走调谐器', run() { tun.auto = !tun.auto; if (!tun.auto) tun.strT = 0; wake(); } },
    ],
  };
})();
