/* 哆啦美登录页 · 方向 A「晨刊」页面脚本(样页,原生 JS,无依赖)
 *
 * 1. 报头日期行:客户端本地公历 + 星期 + 农历(Intl 中国历)+ 本地时间 / 时区 / 版次,不编造任何数字
 * 2. 点刻肖像:墨版 = codex 生成的 hedcut 位图;靛版 = 由同一张图算出的网点(15°网角)。
 *    开场「由粗到细显影」、密码聚焦「化成网点再重组成捂眼图」、悬停印刷放大镜、登录中墨辊过版
 * 3. 开场时间轴 / 跳过 / 重播,表单状态(校对圈出错栏),成功离场(日戳 + 翻过头版)
 */
(() => {
  'use strict';

  const root = document.documentElement;
  const $ = (id) => document.getElementById(id);
  const sheet = $('sheet');
  const reduced = () => root.dataset.motion === 'reduce';
  const dprCap = () => Math.min(2, window.devicePixelRatio || 1);
  const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = (e0, e1, x) => { const t = clamp01((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t); };
  const easeOut3 = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOut3 = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  /* ───────────────────────── 1. 日期与时间 ───────────────────────── */

  const CN = '〇一二三四五六七八九';
  const cnDigits = (n) => String(n).split('').map((d) => CN[+d]).join('');
  const cnNumber = (n) => {
    if (n < 10) return CN[n];
    if (n === 10) return '十';
    if (n < 20) return '十' + CN[n % 10];
    return CN[Math.floor(n / 10)] + '十' + (n % 10 ? CN[n % 10] : '');
  };
  const WEEK = '日一二三四五六';
  const lunarDay = (d) => {
    if (d === 10) return '初十';
    if (d === 20) return '二十';
    if (d === 30) return '三十';
    return ['初', '十', '廿', '三'][Math.floor(d / 10)] + CN[d % 10];
  };

  function lunarText(date) {
    try {
      const parts = new Intl.DateTimeFormat('zh-CN-u-ca-chinese', { year: 'numeric', month: 'long', day: 'numeric' }).formatToParts(date);
      const get = (t) => (parts.find((p) => p.type === t) || {}).value;
      const yearName = get('yearName');
      const month = get('month');
      const dayRaw = get('day');
      if (!yearName || !month || !dayRaw) return '';
      const n = parseInt(dayRaw, 10);
      return `农历${yearName}年${month}${Number.isFinite(n) ? lunarDay(n) : dayRaw}`;
    } catch (e) {
      return '';
    }
  }

  function zoneText(date) {
    const off = -date.getTimezoneOffset();
    if (off % 60 === 0) {
      const h = off / 60;
      if (h === 0) return '零时区';
      return (h > 0 ? '东' : '西') + cnNumber(Math.abs(h)) + '区';
    }
    const a = Math.abs(off);
    return `UTC${off >= 0 ? '+' : '−'}${Math.floor(a / 60)}:${String(a % 60).padStart(2, '0')}`;
  }

  function editionText(h) {
    if (h >= 5 && h < 9) return '清晨版';
    if (h >= 9 && h < 12) return '上午版';
    if (h >= 12 && h < 14) return '午间版';
    if (h >= 14 && h < 18) return '下午版';
    if (h >= 18 && h < 23) return '晚间版';
    return '深夜版';
  }

  const pad2 = (n) => String(n).padStart(2, '0');
  function renderDate() {
    const now = new Date();
    const y = now.getFullYear();
    const mo = now.getMonth() + 1;
    const d = now.getDate();
    const hm = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
    $('date-cn').textContent = `${cnDigits(y)}年${cnNumber(mo)}月${cnNumber(d)}日　星期${WEEK[now.getDay()]}`;
    const lunar = lunarText(now);
    $('date-lunar').textContent = lunar;
    $('date-lunar').hidden = !lunar;
    const clock = $('clock');
    clock.textContent = hm;
    clock.dateTime = hm;
    $('zone').textContent = `${zoneText(now)} · ${editionText(now.getHours())}`;
    $('stamp-date').textContent = `${y}·${pad2(mo)}·${pad2(d)}`;
    $('stamp-time').textContent = `${hm} · 已核验`;
  }

  let clockTimer = 0;
  function scheduleClock() {
    clearTimeout(clockTimer);
    clockTimer = setTimeout(() => { renderDate(); scheduleClock(); }, 60000 - (Date.now() % 60000) + 40);
  }

  /* ───────────────────────── 2. 点刻肖像 ───────────────────────── */

  const INK_RGB = [27, 26, 23];
  const INK = 'rgb(27,26,23)';
  const SPOT = 'rgb(82,82,197)';
  const ANG_K = Math.PI / 4;    // 墨版网角 45°
  const ANG_S = Math.PI / 12;   // 靛版网角 15°
  const TONE_W = 150;

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.crossOrigin = 'anonymous'; // 在线预览时图片被重定向到另一域名,读像素 / 上传纹理需 CORS
      im.decoding = 'async';
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error(`无法载入 ${src}`));
      im.src = src;
    });
  }

  function canvas2d(w, h) {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    return [c, c.getContext('2d', { willReadFrequently: true })];
  }

  // hedcut 灰度位图 → 墨版(墨色 + 暗度作 alpha)+ 色调图(缩小 = 模糊,作网点源)
  function hedcutPose(img) {
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    const [ink, ix] = canvas2d(w, h);
    ix.drawImage(img, 0, 0);
    const id = ix.getImageData(0, 0, w, h);
    const p = id.data;
    for (let i = 0; i < p.length; i += 4) {
      const a = 255 - p[i];
      p[i] = INK_RGB[0]; p[i + 1] = INK_RGB[1]; p[i + 2] = INK_RGB[2]; p[i + 3] = a;
    }
    ix.putImageData(id, 0, 0);
    const tw = TONE_W;
    const th = Math.round((TONE_W * h) / w);
    const [, tx] = canvas2d(tw, th);
    tx.imageSmoothingEnabled = true;
    tx.imageSmoothingQuality = 'high';
    tx.drawImage(img, 0, 0, tw, th);
    const td = tx.getImageData(0, 0, tw, th).data;
    const k = new Float32Array(tw * th);
    const s = new Float32Array(tw * th);
    for (let i = 0; i < k.length; i++) {
      const d = 1 - td[i * 4] / 255;
      k[i] = Math.min(1, d * 1.15);
      s[i] = smooth(0.1, 0.56, d) * 0.5;   // 靛版只吃中间调:蝴蝶结、头罩、项圈最浓,脸几乎留白
    }
    return { ink, k, s, tw, th, aspect: w / h };
  }

  // 像素画抠图(左右并排两张)→ 两个色版的色调图,全部用网点表现
  function pixelPoses(img) {
    const half = img.naturalWidth / 2;
    const size = img.naturalHeight;
    const th = 160;
    const tw = Math.round((th * half) / size);
    const out = [];
    for (let n = 0; n < 2; n++) {
      const [, x] = canvas2d(tw, th);
      x.imageSmoothingEnabled = true;
      x.imageSmoothingQuality = 'high';
      x.drawImage(img, n * half, 0, half, size, 0, 0, tw, th);
      const d = x.getImageData(0, 0, tw, th).data;
      const k = new Float32Array(tw * th);
      const s = new Float32Array(tw * th);
      for (let i = 0; i < k.length; i++) {
        const a = d[i * 4 + 3] / 255;
        const lum = (0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2]) / 255;
        const dark = (1 - lum) * a;
        k[i] = smooth(0.45, 0.9, dark);
        s[i] = smooth(0.14, 0.62, dark) * 0.72;   // 白脸留纸色;黄头罩淡靛,红蝴蝶结浓靛
      }
      out.push({ ink: null, k, s, tw, th, aspect: half / size });
    }
    return out;
  }

  function sample(tone, tw, th, u, v) {
    const x = u * (tw - 1);
    const y = v * (th - 1);
    const x0 = x | 0;
    const y0 = y | 0;
    const x1 = x0 + 1 < tw ? x0 + 1 : x0;
    const y1 = y0 + 1 < th ? y0 + 1 : y0;
    const fx = x - x0;
    const fy = y - y0;
    const a = tone[y0 * tw + x0];
    const b = tone[y0 * tw + x1];
    const c = tone[y1 * tw + x0];
    const d = tone[y1 * tw + x1];
    const top = a + (b - a) * fx;
    return top + (c + (d - c) * fx - top) * fy;
  }

  // 一个色版的网点:旋转网格,每格按色调画一个面积成比例的圆点(一次 fill 批量提交)
  function screen(ctx, rect, A, B, key, cell, angle, opt) {
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const cx = rect.x + rect.w / 2;
    const cy = rect.y + rect.h / 2;
    const n = Math.ceil(Math.hypot(rect.w, rect.h) / 2 / cell) + 1;
    const tA = A[key];
    const tB = B[key];
    const { p, band, gain, pulse } = opt;
    const x0 = rect.x - cell;
    const x1 = rect.x + rect.w + cell;
    const y0 = rect.y - cell;
    const y1 = rect.y + rect.h + cell;
    ctx.beginPath();
    for (let j = -n; j <= n; j++) {
      for (let i = -n; i <= n; i++) {
        const gx = i * cell;
        const gy = j * cell;
        const x = cx + gx * cos - gy * sin;
        const y = cy + gx * sin + gy * cos;
        if (x < x0 || x > x1 || y < y0 || y > y1) continue;
        const u = (x - rect.x) / rect.w;
        const v = (y - rect.y) / rect.h;
        if (u < 0 || u > 1 || v < 0 || v > 1) continue;
        let t = sample(tA, A.tw, A.th, u, v);
        if (p > 0) {
          const m = p >= 1 ? 1 : smooth(0, 1, (p * (1 + band) - v) / band);
          if (m > 0) {
            t += (sample(tB, B.tw, B.th, u, v) - t) * m;
            if (m < 1) t *= 1 - 0.5 * Math.sin(Math.PI * m);   // 过版带:旧点先收、新点再长
          }
        }
        if (pulse >= 0) {
          const dv = (v - pulse) / 0.07;
          t *= 1 + 0.75 * Math.exp(-dv * dv);
        }
        t *= gain;
        if (t < 0.012) continue;
        const r = cell * Math.sqrt(Math.min(t, 1.15) / Math.PI);
        ctx.moveTo(x + r, y);
        ctx.arc(x, y, r, 0, Math.PI * 2);
      }
    }
    ctx.fill();
  }

  // 与 CSS @keyframes misregister 同步:靛版先错位,收尾套准
  const INTRO_MS = 2950;
  function misregAt(te) {
    const k = [[0, 7, -4], [0.84, 7, -4], [0.9, -1.2, 0.7], [0.95, 0.45, -0.25], [1, 0, 0]];
    const f = clamp01(te / INTRO_MS);
    for (let i = 1; i < k.length; i++) {
      if (f <= k[i][0]) {
        const a = k[i - 1];
        const b = k[i];
        let t = (f - a[0]) / (b[0] - a[0] || 1);
        if (i === 3) t = easeOut3(t);
        return [lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
      }
    }
    return [0, 0];
  }

  class Portrait {
    constructor() {
      this.views = [];
      this.sets = { hedcut: null, pixel: null };
      this.mode = 'hedcut';
      this.p = 0;            // 0 = 正常像,1 = 捂眼像(带扫描过版)
      this.pTarget = 0;
      this.dots = 0;         // 0 = 位图墨版,1 = 化成网点(重组过程中)
      this.introTime = Infinity;
      this.pulse = -1;
      this.pulseOn = false;
      this.lens = { x: 0, y: 0, tx: 0, ty: 0, a: 0, ta: 0, view: null };
      this.raf = 0;
      this.last = 0;
    }

    addView(canvas, opt) {
      const v = { canvas, ctx: canvas.getContext('2d'), w: 0, h: 0, dpr: 1, scale: opt.scale || 1, anchor: opt.anchor || 'bottom', lens: !!opt.lens };
      this.views.push(v);
      return v;
    }

    resize() {
      for (const v of this.views) {
        const w = v.canvas.clientWidth;
        const h = v.canvas.clientHeight;
        const dpr = dprCap();
        v.w = w;
        v.h = h;
        v.dpr = dpr;
        const cw = Math.max(1, Math.round(w * dpr));
        const ch = Math.max(1, Math.round(h * dpr));
        if (v.canvas.width !== cw || v.canvas.height !== ch) {
          v.canvas.width = cw;
          v.canvas.height = ch;
        }
      }
      this.render();
    }

    get set() { return this.sets[this.mode]; }

    fit(v, aspect) {
      const pad = v.anchor === 'center' ? 0 : 2;
      const w = v.w - pad * 2;
      const h = v.h - pad;
      let rw = w;
      let rh = w / aspect;
      if (rh > h) { rh = h; rw = h * aspect; }
      const x = (v.w - rw) / 2;
      const y = v.anchor === 'center' ? (v.h - rh) / 2 : v.h - rh;
      return { x, y, w: rw, h: rh };
    }

    state(v) {
      const sc = v.scale;
      const pixel = this.mode === 'pixel';
      const kFine = (pixel ? 3.7 : 4.2) * sc;
      const sFine = (pixel ? 3.9 : 5) * sc;
      let kCell = lerp(kFine, 6.4 * sc, this.dots);
      let sCell = sFine;
      let bitmap = pixel ? 0 : 1 - this.dots;
      let kDots = pixel ? 1 : this.dots;
      let gain = 1;
      let reg = [0, 0];
      const te = this.introTime;
      if (te < Infinity) {
        const D0 = 1450;
        const D1 = 2650;
        if (te < D0) return null;
        const k = easeOut3(clamp01((te - D0) / (D1 - D0)));
        const cell = lerp(26 * sc, kFine, k);
        kCell = cell;
        sCell = Math.max(cell, sFine);
        bitmap = pixel ? 0 : smooth(7 * sc, 4.6 * sc, cell);
        kDots = pixel ? 1 : 1 - bitmap;
        gain = lerp(0.55, 1, k);
        reg = misregAt(te);
      }
      return { kCell, sCell, bitmap, kDots, gain, reg };
    }

    paint(ctx, rect, st) {
      const set = this.set;
      if (!set) return;
      const [A, B] = set;
      const opt = { p: this.p, band: 0.28, gain: st.gain, pulse: this.pulseOn ? this.pulse : -1 };
      // 靛版(先印)
      ctx.save();
      ctx.translate(st.reg[0], st.reg[1]);
      ctx.fillStyle = SPOT;
      screen(ctx, rect, A, B, 's', st.sCell, ANG_S, opt);
      ctx.restore();
      // 墨版(叠印 multiply)
      ctx.globalCompositeOperation = 'multiply';
      if (st.bitmap > 0.002 && A.ink) {
        ctx.globalAlpha = st.bitmap;
        ctx.drawImage(this.p >= 0.5 ? B.ink : A.ink, rect.x, rect.y, rect.w, rect.h);
        ctx.globalAlpha = 1;
      }
      if (st.kDots > 0.002) {
        ctx.globalAlpha = Math.min(1, st.kDots);
        ctx.fillStyle = INK;
        screen(ctx, rect, A, B, 'k', st.kCell, ANG_K, { ...opt, pulse: -1 });
        ctx.globalAlpha = 1;
      }
      ctx.globalCompositeOperation = 'source-over';
    }

    drawView(v) {
      if (!v.w || !v.h) return;
      const ctx = v.ctx;
      ctx.setTransform(v.dpr, 0, 0, v.dpr, 0, 0);
      ctx.clearRect(0, 0, v.w, v.h);
      const set = this.set;
      if (!set) return;
      const st = this.state(v);
      if (!st) return;
      const rect = this.fit(v, set[0].aspect);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      this.paint(ctx, rect, st);
      const L = this.lens;
      if (v.lens && L.view === v && L.a > 0.01) {
        const R = 56 * easeOut3(L.a);
        const M = 1.9;
        ctx.save();
        ctx.beginPath();
        ctx.arc(L.x, L.y, R, 0, Math.PI * 2);
        ctx.clip();
        ctx.fillStyle = '#f7f3ea';
        ctx.fillRect(L.x - R, L.y - R, R * 2, R * 2);
        ctx.translate(L.x, L.y);
        ctx.scale(M, M);
        ctx.translate(-L.x, -L.y);
        // 放大镜底下,点刻像露出它其实是网点:墨版改用细网点绘制(矢量圆点,放大也清晰)
        this.paint(ctx, rect, { ...st, bitmap: 0, kDots: 1, kCell: 3.1 * v.scale, sCell: 3.6 * v.scale });
        ctx.restore();
        ctx.save();
        ctx.globalAlpha = Math.min(1, L.a * 1.2);
        ctx.strokeStyle = INK;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(L.x, L.y, R, 0, Math.PI * 2);
        ctx.stroke();
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        for (let i = 0; i < 4; i++) {
          const a = (i * Math.PI) / 2;
          ctx.moveTo(L.x + Math.cos(a) * (R - 7), L.y + Math.sin(a) * (R - 7));
          ctx.lineTo(L.x + Math.cos(a) * (R + 5), L.y + Math.sin(a) * (R + 5));
        }
        ctx.stroke();
        ctx.restore();
      }
    }

    render() {
      for (const v of this.views) this.drawView(v);
    }

    // 有动画在跑时才开 rAF;全部落定就停,零后台开销
    kick() {
      if (!this.raf) {
        this.last = performance.now();
        this.raf = requestAnimationFrame(() => this.tick());
      }
    }

    tick() {
      const now = performance.now();
      this.raf = 0;
      const dt = Math.min(64, now - this.last);
      this.last = now;
      let busy = false;
      // 重组:先化成网点(dots→1),再扫描过版(p→目标),最后收回位图(dots→0)
      if (this.p !== this.pTarget) {
        if (reduced()) {
          this.p = this.pTarget;
          this.dots = 0;
        } else if (this.mode === 'pixel' || this.dots >= 0.98) {
          const step = dt / 430;
          this.p = this.pTarget > this.p ? Math.min(this.pTarget, this.p + step) : Math.max(this.pTarget, this.p - step);
          busy = true;
        } else {
          this.dots = Math.min(1, this.dots + dt / 170);
          busy = true;
        }
      } else if (this.dots > 0) {
        this.dots = reduced() ? 0 : Math.max(0, this.dots - dt / 230);
        busy = this.dots > 0;
      }
      // 印刷放大镜
      const L = this.lens;
      if (L.view) {
        const k = 1 - Math.pow(0.0008, dt / 1000);
        L.x += (L.tx - L.x) * k;
        L.y += (L.ty - L.y) * k;
        L.a += (L.ta - L.a) * (1 - Math.pow(0.002, dt / 1000));
        if (Math.abs(L.ta - L.a) < 0.004) L.a = L.ta;
        if (Math.abs(L.tx - L.x) > 0.2 || Math.abs(L.ty - L.y) > 0.2 || L.a !== L.ta) busy = true;
        if (L.a === 0 && L.ta === 0) L.view = null;
      }
      // 登录中:一道墨辊自上而下滚过靛版
      if (this.pulseOn) {
        this.pulse = ((now % 1100) / 1100) * 1.3 - 0.15;
        busy = true;
      }
      this.render();
      if (busy && !document.hidden) this.kick();
    }

    setCovered(on) {
      this.pTarget = on ? 1 : 0;
      this.kick();
    }

    setPulse(on) {
      this.pulseOn = on && !reduced();
      if (!this.pulseOn) this.pulse = -1;
      this.kick();
    }
  }

  const portrait = new Portrait();
  const mainView = portrait.addView($('portrait-canvas'), { scale: 1, lens: true });
  portrait.addView($('mini-canvas'), { scale: 0.42, anchor: 'center' });

  const assets = {
    a: loadImage('assets/hedcut-a.png'),
    b: null,
    pixel: null,
  };

  async function loadHedcut() {
    const imgA = await assets.a;
    const A = hedcutPose(imgA);
    // 先只用 A 顶上,首屏只等一张图;捂眼图紧接着在后台取回(不阻塞开场)
    portrait.sets.hedcut = [A, A];
    portrait.resize();
    await ensureCoverLoaded();
  }

  let coverPose = null;
  async function ensureCoverLoaded() {
    if (!coverPose) {
      assets.b = assets.b || loadImage('assets/hedcut-b.png');
      coverPose = assets.b.then(hedcutPose);
    }
    const B = await coverPose;
    if (portrait.sets.hedcut && portrait.sets.hedcut[1] !== B) portrait.sets.hedcut = [portrait.sets.hedcut[0], B];
  }

  // 样页按钮:点刻版 ⇄ 像素网点版(对比择优用)。切换时把显影再走一遍,像换了一块版重新开印
  let portraitChoice = 'hedcut';
  let reprintRaf = 0;
  async function setPortraitMode(mode) {
    if (mode === 'pixel' && !portrait.sets.pixel) {
      assets.pixel = assets.pixel || loadImage('assets/portrait-pair.png');
      portrait.sets.pixel = pixelPoses(await assets.pixel);
    }
    if (portraitChoice !== mode) return;
    portrait.mode = mode;
    portrait.dots = 0;
    cancelAnimationFrame(reprintRaf);
    if (reduced() || introPlaying) { portrait.render(); return; }
    const t0 = performance.now();
    const step = () => {
      const f = (performance.now() - t0) / 900;
      portrait.introTime = f >= 1 ? Infinity : 1450 + f * (INTRO_MS - 1450);
      portrait.render();
      if (f < 1) reprintRaf = requestAnimationFrame(step);
    };
    reprintRaf = requestAnimationFrame(step);
  }

  // 悬停放大镜(仅完整档 + 精确指针)
  const portraitBox = $('portrait');
  function lensMove(e) {
    if (e.pointerType !== 'mouse' || reduced() || introPlaying) return;
    const r = mainView.canvas.getBoundingClientRect();
    const L = portrait.lens;
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    if (!L.view || L.a === 0) { L.x = x; L.y = y; }
    L.view = mainView;
    L.tx = x;
    L.ty = y;
    L.ta = 1;
    portrait.kick();
  }
  portraitBox.addEventListener('pointermove', lensMove);
  portraitBox.addEventListener('pointerenter', lensMove);
  portraitBox.addEventListener('pointerleave', () => { portrait.lens.ta = 0; portrait.kick(); });

  /* ───────────────────────── 3. 开场 ───────────────────────── */

  let introPlaying = false;
  let introT0 = 0;
  let introRaf = 0;

  function startIntro() {
    cancelAnimationFrame(introRaf);
    if (reduced()) { finishIntro(false); return; }
    sheet.classList.remove('is-ready', 'is-playing');
    void sheet.offsetWidth;   // 让 CSS 动画从头再来
    sheet.classList.remove('is-pre');
    sheet.classList.add('is-playing');
    introPlaying = true;
    introT0 = performance.now();
    portrait.lens.ta = 0;
    portrait.lens.a = 0;
    portrait.lens.view = null;
    introRaf = requestAnimationFrame(introFrame);
  }

  function introFrame() {
    const te = performance.now() - introT0;
    portrait.introTime = te;
    portrait.render();
    if (te >= INTRO_MS + 60) { finishIntro(true); return; }
    introRaf = requestAnimationFrame(introFrame);
  }

  function finishIntro(natural) {
    cancelAnimationFrame(introRaf);
    introPlaying = false;
    sheet.classList.remove('is-pre', 'is-playing');
    sheet.classList.add('is-ready');
    portrait.introTime = Infinity;
    portrait.render();
    // 桌面精确指针:开场落定后把光标交给账号框(不抢已有焦点、不滚动)
    if (natural !== null && innerWidth > 720 && matchMedia('(pointer: fine)').matches && document.activeElement === document.body && sheet.dataset.state === 'idle') {
      $('username').focus({ preventScroll: true });
    }
  }

  function onSkip(e) {
    if (!introPlaying) return;
    if (e.target && e.target.closest && e.target.closest('[data-sample-kit]')) return;
    finishIntro(false);
  }
  window.addEventListener('pointerdown', onSkip, true);
  window.addEventListener('keydown', onSkip, true);

  /* ───────────────────────── 4. 表单与状态 ───────────────────────── */

  const form = $('login-form');
  const user = $('username');
  const pass = $('password');
  const errEl = $('form-error');
  const btnLabel = $('submit-label');
  const capLine = $('cap-line');
  const CAP_OPEN = '她替你读遍订阅的来源，只把值得的排进版面。';
  const CAP_COVER = '输入密码时，本报编辑自觉回避。';
  let pendingTimer = 0;
  let exitToken = 0;

  // 图注换句:先淡出再换字;连续聚焦 / 失焦时以最后一次为准
  let capTarget = CAP_OPEN;
  let capTimer = 0;
  function setCaption(text) {
    capTarget = text;
    clearTimeout(capTimer);
    if (reduced() || capLine.textContent === text) {
      capLine.textContent = text;
      capLine.classList.remove('is-swapping');
      return;
    }
    capLine.classList.add('is-swapping');
    capTimer = setTimeout(() => { capLine.textContent = capTarget; capLine.classList.remove('is-swapping'); }, 220);
  }

  pass.addEventListener('focus', async () => {
    await ensureCoverLoaded();
    if (document.activeElement !== pass) return;
    portrait.setCovered(true);
    setCaption(CAP_COVER);
  });
  pass.addEventListener('blur', () => {
    portrait.setCovered(false);
    setCaption(CAP_OPEN);
  });

  // 校对圈:围住出错的栏(手绘感的圆角方圈)+ 一道引线连到页边的说明
  function drawProof(target) {
    const svg = $('proof');
    const loop = $('proof-loop');
    const tail = $('proof-tail');
    const sr = svg.getBoundingClientRect();
    const els = target === 'both' ? [$('field-user'), $('field-pass')] : [$(target === 'user' ? 'field-user' : 'field-pass')];
    const r0 = els[0].getBoundingClientRect();
    const r1 = els[els.length - 1].getBoundingClientRect();
    const box = { x: r0.left - sr.left, y: r0.top - sr.top, w: r0.width, h: r1.bottom - r0.top };
    const cx = box.x + box.w / 2;
    const cy = box.y + box.h / 2;
    const rx = box.w / 2 + 5;
    const ry = box.h / 2 + 6;
    const pts = [];
    const N = 44;
    const a0 = Math.PI * 0.94;
    const sweep = Math.PI * 2.12;
    for (let i = 0; i <= N; i++) {
      const a = a0 + (sweep * i) / N;
      const c = Math.cos(a);
      const s = Math.sin(a);
      const wob = 1 + 0.012 * Math.sin(i * 1.9 + 0.7) + 0.008 * Math.sin(i * 4.3);
      const grow = 1 + 0.022 * (i / N);
      const ex = 0.36;   // 超椭圆指数:圈得住矩形的四角
      pts.push([cx + Math.sign(c) * Math.pow(Math.abs(c), ex) * rx * wob * grow, cy + Math.sign(s) * Math.pow(Math.abs(s), ex) * ry * wob * grow]);
    }
    let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(0, i - 1)];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const p3 = pts[Math.min(pts.length - 1, i + 2)];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += `C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
    }
    loop.setAttribute('d', d);
    // 引线:从圈底左侧落到错误说明的行首
    const er = errEl.getBoundingClientRect();
    const sx = box.x + 22;
    const sy = cy + ry * 1.02;
    const ex2 = er.left - sr.left + 28;
    const ey = er.top - sr.top + 19;
    tail.setAttribute('d', `M${sx.toFixed(1)} ${sy.toFixed(1)}C${(sx - 2).toFixed(1)} ${(sy + 9).toFixed(1)} ${(ex2 - 12).toFixed(1)} ${ey.toFixed(1)} ${ex2.toFixed(1)} ${ey.toFixed(1)}`);
    for (const [path, delay, dur] of [[loop, 0, 520], [tail, 470, 200]]) {
      const len = path.getTotalLength();
      path.style.transition = 'none';
      path.style.strokeDasharray = `${len} ${len}`;
      path.style.strokeDashoffset = reduced() ? '0' : String(len);
      if (!reduced()) {
        void path.getBoundingClientRect();
        path.style.transition = `stroke-dashoffset ${dur}ms cubic-bezier(.45,.05,.25,1) ${delay}ms, opacity .3s ease`;
        path.style.strokeDashoffset = '0';
      }
    }
  }

  function showError(message, target) {
    clearTimeout(pendingTimer);
    sheet.dataset.state = 'error';
    errEl.textContent = message;
    btnLabel.textContent = '登录';
    portrait.setPulse(false);
    user.removeAttribute('aria-invalid');
    pass.removeAttribute('aria-invalid');
    if (target === 'both' || target === 'user') user.setAttribute('aria-invalid', 'true');
    if (target === 'both' || target === 'pass') pass.setAttribute('aria-invalid', 'true');
    user.readOnly = false;
    pass.readOnly = false;
    drawProof(target);
  }

  function clearError() {
    if (sheet.dataset.state !== 'error') return;
    sheet.dataset.state = 'idle';
    errEl.textContent = '';
    user.removeAttribute('aria-invalid');
    pass.removeAttribute('aria-invalid');
  }
  user.addEventListener('input', clearError);
  pass.addEventListener('input', clearError);

  function setLoading() {
    clearTimeout(pendingTimer);
    sheet.dataset.state = 'loading';
    errEl.textContent = '';
    btnLabel.textContent = '登录中';
    user.readOnly = true;
    pass.readOnly = true;
    portrait.setPulse(true);
  }

  function resetAll() {
    clearTimeout(pendingTimer);
    exitToken++;
    sheet.dataset.state = 'idle';
    errEl.textContent = '';
    btnLabel.textContent = '登录';
    user.readOnly = false;
    pass.readOnly = false;
    user.removeAttribute('aria-invalid');
    pass.removeAttribute('aria-invalid');
    portrait.setPulse(false);
    sheet.style.clipPath = '';
    sheet.style.visibility = '';
    sheet.style.opacity = '';
    sheet.style.transition = '';
    $('turn').classList.remove('is-on');
    $('landing').classList.remove('is-on', 'is-done');
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const st = sheet.dataset.state;
    if (st === 'loading' || st === 'success') return;
    const u = user.value.trim();
    const p = pass.value;
    if (!u || !p) {
      showError('请输入账号和密码', !u && !p ? 'both' : !u ? 'user' : 'pass');
      (!u ? user : pass).focus();
      return;
    }
    setLoading();
    pendingTimer = setTimeout(() => {
      if (u === 'admin' && p === 'admin') {
        runSuccess();
      } else {
        showError('账号或密码错误', 'both');
        pass.focus();
        pass.select();
      }
    }, 900);
  });

  /* ───────────────────────── 5. 成功离场:日戳 → 翻过头版 ───────────────────────── */

  async function runSuccess() {
    const token = ++exitToken;
    clearTimeout(pendingTimer);
    sheet.dataset.state = 'success';
    errEl.textContent = '';
    btnLabel.textContent = '登录';
    portrait.setPulse(false);
    user.readOnly = true;
    pass.readOnly = true;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    const landing = $('landing');
    if (reduced()) {
      await wait(700);
      if (token !== exitToken) return;
      landing.classList.add('is-on');
      sheet.style.transition = 'opacity 420ms ease';
      sheet.style.opacity = '0';
      await wait(440);
      if (token !== exitToken) return;
      sheet.style.visibility = 'hidden';
      landing.classList.add('is-done');
      return;
    }
    await wait(760);   // 日戳落下(420ms)+ 停一拍
    if (token !== exitToken) return;
    landing.classList.add('is-on');
    await pageTurn(token);
    if (token !== exitToken) return;
    sheet.style.visibility = 'hidden';
    landing.classList.add('is-done');
  }

  function clipHalf(poly, n, F, keepRight) {
    const out = [];
    const sg = keepRight ? 1 : -1;
    const side = (q) => sg * (n[0] * (q[0] - F[0]) + n[1] * (q[1] - F[1]));
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i];
      const b = poly[(i + 1) % poly.length];
      const sa = side(a);
      const sb = side(b);
      if (sa >= 0) out.push(a);
      if ((sa >= 0) !== (sb >= 0)) {
        const t = sa / (sa - sb);
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
      }
    }
    return out;
  }

  // 纸背透印:新闻纸薄,翻起来能隐约看见正面的反字。翻页开始时记下正面要素的位置,
  // 每帧在纸背上按折线镜像画一遍(低透明度),报头、标题、栏线、文字行、肖像都在。
  function collectGhost() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const out = [];
    const seen = (r) => r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
    const css = (el) => getComputedStyle(el);
    for (const el of document.querySelectorAll('.mast-ch')) {
      const r = el.getBoundingClientRect();
      if (!seen(r)) continue;
      out.push({ k: 'text', s: el.textContent, x: r.left + r.width / 2, y: r.top + r.height / 2, align: 'center', font: `900 ${parseFloat(css(el).fontSize)}px "Dorami Serif"`, c: 'rgba(71,69,179,.13)' });
    }
    const hl = document.querySelector('.headline');
    if (hl) {
      const r = hl.getBoundingClientRect();
      const fs = parseFloat(css(hl).fontSize);
      const lines = getComputedStyle(document.querySelector('.hl-br')).display === 'none' ? ['世界很吵，早报很静。'] : ['世界很吵，', '早报很静。'];
      lines.forEach((s, i) => out.push({ k: 'text', s, x: r.left, y: r.top + fs * 1.07 * (i + 0.5), align: 'left', font: `900 ${fs}px "Dorami Serif"`, c: 'rgba(27,26,23,.085)' }));
    }
    for (const el of document.querySelectorAll('.rule-x, .ear, .col-head, .caption, .body-text')) {
      const r = el.getBoundingClientRect();
      if (!seen(r)) continue;
      const st = css(el);
      const bt = parseFloat(st.borderTopWidth) || 0;
      const bb = parseFloat(st.borderBottomWidth) || 0;
      if (bt) out.push({ k: 'rect', x: r.left, y: r.top, w: r.width, h: bt, c: 'rgba(27,26,23,.12)' });
      if (bb) out.push({ k: 'rect', x: r.left, y: r.bottom - bb, w: r.width, h: bb, c: 'rgba(27,26,23,.12)' });
    }
    const grid = $('grid').getBoundingClientRect();
    for (const [sel, side] of [['.col-index', 'right'], ['.col-pic', 'left'], ['.col-coupon', 'left']]) {
      const el = document.querySelector(sel);
      const r = el && el.getBoundingClientRect();
      if (r && seen(r) && getComputedStyle(el, side === 'right' ? '::after' : '::before').display !== 'none') {
        out.push({ k: 'rect', x: side === 'right' ? r.right - 1 : r.left, y: grid.top, w: 1, h: grid.height, c: 'rgba(27,26,23,.12)' });
      }
    }
    // 文字行:按行高画成淡灰条(透过纸背看,字本来也只剩一行行影子)
    for (const el of document.querySelectorAll('.index li, .body-text p, .deck, .cap-line, .notice, .dateline, .folio-row, .ear dd, .coupon-lede, .stub')) {
      const r = el.getBoundingClientRect();
      if (!seen(r)) continue;
      const st = css(el);
      const fs = parseFloat(st.fontSize) || 14;
      const lh = parseFloat(st.lineHeight) || fs * 1.6;
      const host = el.closest('.body-text');
      const cols = host ? parseInt(getComputedStyle(host).columnCount, 10) || 1 : 1;
      const rows = Math.max(1, Math.round(r.height / lh));
      for (let i = 0; i < rows; i++) {
        const short = i === rows - 1 && rows > 1 ? 0.55 : 1;
        out.push({ k: 'rect', x: r.left, y: r.top + i * lh + (lh - fs * 0.62) / 2, w: r.width * short, h: fs * 0.62, c: cols > 1 ? 'rgba(27,26,23,.05)' : 'rgba(27,26,23,.055)' });
      }
    }
    const btn = $('submit').getBoundingClientRect();
    if (seen(btn)) out.push({ k: 'rect', x: btn.left, y: btn.top, w: btn.width, h: btn.height, c: 'rgba(82,82,197,.075)' });
    const pc = $('portrait-canvas');
    const pr = pc.getBoundingClientRect();
    if (seen(pr) && pc.width > 1) out.push({ k: 'img', el: pc, x: pr.left, y: pr.top, w: pr.width, h: pr.height, a: 0.14 });
    return out;
  }

  function drawGhost(ctx, items) {
    for (const g of items) {
      if (g.k === 'rect') {
        ctx.fillStyle = g.c;
        ctx.fillRect(g.x, g.y, g.w, g.h);
      } else if (g.k === 'text') {
        ctx.font = g.font;
        ctx.textAlign = g.align;
        ctx.textBaseline = 'middle';
        ctx.fillStyle = g.c;
        ctx.fillText(g.s, g.x, g.y);
      } else if (g.k === 'img') {
        ctx.globalAlpha = g.a;
        ctx.drawImage(g.el, g.x, g.y, g.w, g.h);
        ctx.globalAlpha = 1;
      }
    }
  }

  function pageTurn(token) {
    return new Promise((resolve) => {
      const cvs = $('turn');
      const W = window.innerWidth;
      const H = window.innerHeight;
      const dpr = dprCap();
      cvs.width = Math.round(W * dpr);
      cvs.height = Math.round(H * dpr);
      cvs.classList.add('is-on');
      const ctx = cvs.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const rect = sheet.getBoundingClientRect();
      const ghost = collectGhost();
      const view = [[0, 0], [W, 0], [W, H], [0, H]];
      const dur = 1180;
      const t0 = performance.now();
      const poly = (pts) => {
        ctx.beginPath();
        pts.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
        ctx.closePath();
      };
      function frame() {
        const now = performance.now();
        if (token !== exitToken) { ctx.clearRect(0, 0, W, H); cvs.classList.remove('is-on'); resolve(); return; }
        const t = clamp01((now - t0) / dur);
        const e = easeInOut3(t);
        const s = lerp(0.26, 0.05, e);   // 折线斜度:先由右下角掀起,翻到后来渐渐扶正
        const xf = lerp(W + (s * H) / 2 + 6, -(s * H) / 2 - 6, e);
        const len = Math.hypot(1, s);
        const n = [1 / len, s / len];
        const F = [xf, H / 2];
        const left = clipHalf(view, n, F, false);
        const right = clipHalf(view, n, F, true);
        sheet.style.clipPath = left.length > 2
          ? `polygon(${left.map((q) => `${(q[0] - rect.left).toFixed(1)}px ${(q[1] - rect.top).toFixed(1)}px`).join(',')})`
          : 'polygon(0 0, 0 0, 0 0)';
        ctx.clearRect(0, 0, W, H);
        if (right.length > 2) {
          // 掀起的纸在阅读器上投下的影子
          ctx.save();
          poly(right);
          ctx.clip();
          const g = ctx.createLinearGradient(F[0], F[1], F[0] + n[0] * 90, F[1] + n[1] * 90);
          g.addColorStop(0, 'rgba(28,26,40,.22)');
          g.addColorStop(1, 'rgba(28,26,40,0)');
          ctx.fillStyle = g;
          ctx.fillRect(0, 0, W, H);
          ctx.restore();
          // 纸背:沿折线镜像,近折处有一道弯曲的暗-亮
          const refl = (q) => {
            const dd = n[0] * (q[0] - F[0]) + n[1] * (q[1] - F[1]);
            return [q[0] - 2 * dd * n[0], q[1] - 2 * dd * n[1]];
          };
          let D = 1;
          for (const q of right) D = Math.max(D, n[0] * (q[0] - F[0]) + n[1] * (q[1] - F[1]));
          const flap = right.map(refl);
          ctx.save();
          ctx.shadowColor = 'rgba(52,40,18,.26)';
          ctx.shadowBlur = 28;
          ctx.shadowOffsetX = -5;
          const g2 = ctx.createLinearGradient(F[0], F[1], F[0] - n[0] * D, F[1] - n[1] * D);
          g2.addColorStop(0, '#d8d0c0');
          g2.addColorStop(Math.min(0.5, 14 / D), '#f4efe5');
          g2.addColorStop(Math.min(0.8, 90 / D), '#ede7db');
          g2.addColorStop(1, '#e1d9c9');
          ctx.fillStyle = g2;
          poly(flap);
          ctx.fill();
          ctx.restore();
          // 透印:在纸背上按折线镜像重画正面(反字)
          ctx.save();
          poly(flap);
          ctx.clip();
          const nf = n[0] * F[0] + n[1] * F[1];
          ctx.transform(1 - 2 * n[0] * n[0], -2 * n[0] * n[1], -2 * n[0] * n[1], 1 - 2 * n[1] * n[1], 2 * nf * n[0], 2 * nf * n[1]);
          drawGhost(ctx, ghost);
          ctx.restore();
          // 折处的弯:一道窄高光压在透印之上
          ctx.save();
          poly(flap);
          ctx.clip();
          const g3 = ctx.createLinearGradient(F[0], F[1], F[0] - n[0] * 26, F[1] - n[1] * 26);
          g3.addColorStop(0, 'rgba(120,100,70,.22)');
          g3.addColorStop(0.35, 'rgba(255,253,247,.55)');
          g3.addColorStop(1, 'rgba(255,253,247,0)');
          ctx.fillStyle = g3;
          ctx.fillRect(0, 0, W, H);
          ctx.restore();
        }
        if (t < 1) {
          requestAnimationFrame(frame);
        } else {
          ctx.clearRect(0, 0, W, H);
          cvs.classList.remove('is-on');
          resolve();
        }
      }
      requestAnimationFrame(frame);
    });
  }

  function setState(name) {
    if (introPlaying) finishIntro(null);
    resetAll();
    if (name === 'loading') setLoading();
    else if (name === 'error') showError('账号或密码错误', 'both');
    else if (name === 'success') runSuccess();
  }

  /* ───────────────────────── 6. 启动 / 契约 ───────────────────────── */

  window.addEventListener('sample:motion', (e) => {
    if (e.detail && e.detail.reduced) {
      if (introPlaying) finishIntro(false);
      portrait.lens.ta = 0;
      portrait.lens.a = 0;
      portrait.lens.view = null;
      portrait.dots = 0;
      portrait.p = portrait.pTarget;
      portrait.setPulse(false);
      if (sheet.dataset.state === 'loading') portrait.setPulse(false);
    }
    portrait.render();
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (introPlaying) finishIntro(false);
    } else {
      renderDate();
      scheduleClock();
      portrait.kick();
    }
  });

  let resizeRaf = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(() => portrait.resize());
  });

  window.LoginSample = {
    replay() {
      resetAll();
      user.blur();
      pass.blur();
      portrait.p = portrait.pTarget = 0;
      portrait.dots = 0;
      setCaption(CAP_OPEN);
      startIntro();
    },
    skipIntro() {
      if (introPlaying || sheet.classList.contains('is-pre')) finishIntro(false);
    },
    setState,
    extraControls: [
      {
        get label() { return portraitChoice === 'hedcut' ? '肖像:点刻版' : '肖像:像素网点版'; },
        run() {
          portraitChoice = portraitChoice === 'hedcut' ? 'pixel' : 'hedcut';
          setPortraitMode(portraitChoice);
        },
      },
    ],
  };

  renderDate();
  scheduleClock();
  portrait.resize();
  loadHedcut().catch(() => {});

  // 等报头字体就位再开演(最多等 900ms),免得压印压的是回退字体
  const fontsReady = document.fonts && document.fonts.load
    ? Promise.all([
      document.fonts.load('900 100px "Dorami Serif"', '哆啦美世界很吵早报静'),
      document.fonts.load('600 16px "Dorami Serif"', '读者登录账号密码'),
    ]).catch(() => {})
    : Promise.resolve();
  Promise.race([fontsReady, wait(900)]).then(() => {
    if (sheet.classList.contains('is-pre')) startIntro();
  });
})();
