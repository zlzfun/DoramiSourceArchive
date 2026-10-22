/* 哆啦美登录页样页 · E 天色 —— 开场延时摄影、表单状态机、隐私云、交棒、样页时刻拨盘 */
(function () {
  'use strict';
  const S = window.SkySim;
  const root = document.documentElement;
  const params = new URLSearchParams(location.search);
  const embedded = window.top !== window || params.get('kit') === '0';
  const $ = (id) => document.getElementById(id);
  const el = {
    canvas: $('sky'), curtain: $('curtain'), copy: $('copy'), eyebrow: $('eyebrow'), greet: $('greet'), sub: $('sub'),
    clock: $('clock'), phase: $('phase'), date: $('date'), lunar: $('lunar'), tz: $('tz'), pix: $('pixstar'),
    form: $('login'), user: $('u'), pass: $('p'), go: $('go'), goLabel: $('go-label'), err: $('err'),
    handoff: $('handoff'), note: $('handoff-note'), dial: $('dial'), dialRange: $('dial-range'), dialOut: $('dial-out'),
    skyPane: document.querySelector('.sky-pane'), grain: document.querySelector('.grain'),
  };
  const reduced = () => root.dataset.motion === 'reduce';
  const clamp = S.clamp;

  /* ---------------- 缓动 ---------------- */
  function bezier(x1, y1, x2, y2) {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sx = (t) => ((ax * t + bx) * t + cx) * t;
    const sy = (t) => ((ay * t + by) * t + cy) * t;
    const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
    return (x) => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let t = x;
      for (let i = 0; i < 8; i++) {
        const d = sx(t) - x;
        if (Math.abs(d) < 1e-5) break;
        const dd = dx(t);
        if (Math.abs(dd) < 1e-6) break;
        t -= d / dd;
      }
      return sy(clamp(t, 0, 1));
    };
  }
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeInOutSine = (t) => -(Math.cos(Math.PI * t) - 1) / 2;
  const easeInCubic = (t) => t * t * t;
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
  const timelapse = easeInOutSine;

  /* ---------------- 本地时间 → 文案 ---------------- */
  const today = new Date();
  const times = S.sunTimes(today);
  const COPY = {
    深夜: { g: '夜深了', en: 'Late night,', sub: '星星还醒着，你也是。' },
    拂晓: { g: '早安', en: 'Good morning,', sub: '趁天色微明，先读一页早报。' },
    上午: { g: '上午好', en: 'Good morning,', sub: '新的一天，从一份早报开始。' },
    午间: { g: '午安', en: 'Good afternoon,', sub: '午间小憩，听一期播客。' },
    午后: { g: '下午好', en: 'Good afternoon,', sub: '午后的光很慢，适合读一篇长文。' },
    黄昏: { g: '傍晚好', en: 'Good evening,', sub: '天色将晚，读一份今天的日报。' },
    入夜: { g: '晚上好', en: 'Good evening,', sub: '夜里，向 AI 问一个今天的问题。' },
  };
  const norm = (h) => ((h % 24) + 24) % 24;
  function phaseOf(h) {
    h = norm(h);
    if (h < times.rise - 1.5) return '深夜';
    if (h < times.rise + 0.9) return '拂晓';
    if (h < 11) return '上午';
    if (h < 13.5) return '午间';
    if (h < times.set - 1.2) return '午后';
    if (h < times.set + 0.9) return '黄昏';
    if (h < 22.5) return '入夜';
    return '深夜';
  }
  const pad = (n) => String(n).padStart(2, '0');
  function fmtHour(h) {
    const m = Math.floor(norm(h) * 60 + 1e-6) % 1440;
    return pad(Math.floor(m / 60)) + ':' + pad(m % 60);
  }
  const ZH = ['〇', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
  function zhNum(n) {
    if (n <= 10) return ZH[n];
    if (n < 20) return '十' + (n % 10 ? ZH[n % 10] : '');
    return ZH[Math.floor(n / 10)] + '十' + (n % 10 ? ZH[n % 10] : '');
  }
  function lunarDay(n) {
    if (n === 10) return '初十';
    if (n === 20) return '二十';
    if (n === 30) return '三十';
    if (n < 10) return '初' + ZH[n];
    if (n < 20) return '十' + ZH[n - 10];
    return '廿' + ZH[n - 20];
  }
  function lunarText(d) {
    try {
      const parts = new Intl.DateTimeFormat('zh-CN-u-ca-chinese', { month: 'long', day: 'numeric' }).formatToParts(d);
      const month = (parts.find((p) => p.type === 'month') || {}).value;
      const day = parseInt((parts.find((p) => p.type === 'day') || {}).value, 10);
      if (!month || !day || /\d/.test(month)) return '';
      return '农历' + month + lunarDay(day);
    } catch (e) { return ''; }
  }
  el.date.textContent = zhNum(today.getMonth() + 1) + '月' + zhNum(today.getDate()) + '日 · 星期' + '日一二三四五六'[today.getDay()];
  const lunar = lunarText(today);
  el.lunar.textContent = (lunar ? lunar + ' · ' : '') + S.moonPhaseName(S.moonAge(today));
  try {
    const tzName = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    const off = -today.getTimezoneOffset() / 60;
    el.tz.textContent = (tzName ? tzName.replace(/_/g, ' ') + ' · ' : '') + 'GMT' + (off >= 0 ? '+' : '−') + Math.abs(off);
  } catch (e) { /* 忽略 */ }

  /* ---------------- 时间源:本地时间,或样页 ?hour= / 拨盘覆盖 ---------------- */
  function parseHour(v) {
    if (v == null || v === '') return null;
    const h = parseFloat(v);
    return isFinite(h) ? norm(h) : null;
  }
  let override = parseHour(params.get('hour'));
  function realHour() { const d = new Date(); return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600; }
  const targetHour = () => (override != null ? override : realHour());

  // 每次登录的云都不一样(样页可用 ?seed= 固定)
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const seedParam = parseFloat(params.get('seed'));
  const rnd = mulberry32(Math.floor((isFinite(seedParam) ? seedParam : Math.random() * 100000) * 7919) >>> 0);

  const fx = {
    time: 0, cloudT: rnd() * 40, cover: 0.53 + rnd() * 0.05, seed: [rnd() * 50, rnd() * 50],
    veilF: 0, veilA: 0, expo: 1, shadow: -1, bloom: 0, bloomC: [1, 0.965, 0.9], bloomP: [0, 0], trail: 0, clear: 0, par: [0, 0],
  };
  const parTarget = [0, 0];
  let wind = 1;
  const BASE_WIND = 0.3;

  /* ---------------- 画面尺寸与日月弧线框 ---------------- */
  const renderer = S.createRenderer(el.canvas);
  root.dataset.renderer = renderer.kind;
  let frame = null;
  let size = null;
  let quality = 1; // 弱 GPU 时自动降档
  let paneTop = 0;
  function measure() {
    const W = window.innerWidth, H = window.innerHeight;
    const pr = el.skyPane.getBoundingClientRect();
    paneTop = pr.top;
    const hz = pr.bottom;
    const mobile = W < 760;
    let cx, spanX, spanY;
    if (mobile) {
      const top = el.copy.getBoundingClientRect().bottom + 34;
      cx = W * 0.56; spanX = W * 0.40; spanY = Math.max(70, hz - top);
    } else {
      cx = W * 0.665; spanX = W * 0.30; spanY = Math.max(120, hz - 76);
    }
    frame = { W, H, horizon: hz, cx, spanX, spanY, mobile };
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const scale = mobile ? Math.min(dpr, 1.5) * 0.75 : clamp(dpr * 0.62, 0.85, 1.25);
    size = { w: Math.max(1, Math.round(W * scale * quality)), h: Math.max(1, Math.round(H * scale * quality)), dpr: scale * quality };
  }

  /* ---------------- 文案与墨色(墨色随天色自动取深 / 浅) ---------------- */
  let currentPhase = null;
  function applyCopy(hour, force) {
    const ph = phaseOf(hour);
    if (ph === currentPhase && !force) return false;
    currentPhase = ph;
    const c = COPY[ph];
    el.eyebrow.textContent = c.en;
    el.greet.setAttribute('aria-label', c.g);
    el.greet.textContent = '';
    Array.from(c.g).forEach((ch, i) => {
      const s = document.createElement('span');
      s.className = 'ch';
      s.style.setProperty('--i', i);
      s.setAttribute('aria-hidden', 'true');
      s.textContent = ch;
      el.greet.append(s);
    });
    el.sub.textContent = c.sub;
    return true;
  }
  const INK_LIGHT = S.hex('#f7f3ea');
  // 墨色:在文字块上取一组天色样本,深 / 浅两种墨各算最差对比度,取更好的那一个(不做中间灰);
  // 最差对比度不足时加重文字背后的柔光衬底(--halo)
  function pickInk(points, sc) {
    const Ls = points.map(([x, y]) => S.luminance(S.skyColorAt(sc, clamp((frame.horizon - y) / frame.horizon, 0, 1), x)));
    const dark = S.mix3(S.hex('#0a1122'), sc.pal.zen, 0.16);
    const Ld = S.luminance(dark), Ll = S.luminance(INK_LIGHT);
    const minDark = Math.min(...Ls.map((L) => (L + 0.05) / (Ld + 0.05)));
    const minLight = Math.min(...Ls.map((L) => (Ll + 0.05) / (L + 0.05)));
    const light = minLight >= minDark;
    const worst = Math.max(minDark, minLight);
    const a = clamp((6 - worst) / 5, 0.16, 0.62);
    return { rgb: light ? INK_LIGHT : dark, light, worst, halo: light ? `rgba(6,8,30,${a.toFixed(2)})` : `rgba(255,255,255,${a.toFixed(2)})` };
  }
  function applyInk(hour) {
    const sc = S.computeScene(hour, today, frame);
    const r = el.copy.getBoundingClientRect();
    const pts = [];
    for (const fy of [0.1, 0.38, 0.62, 0.9]) for (const fx_ of [0.08, 0.4, 0.75]) pts.push([r.left + r.width * fx_, r.top + r.height * fy]);
    const ink = pickInk(pts, sc);
    // 对比不足时在文字块背后做一块看不出形状的减淡 / 加深(摄影里的 dodge & burn)
    const k = clamp((6.4 - ink.worst) / 5, 0, 0.5);
    fx.scrim = [r.left + r.width * 0.45, r.top + r.height * 0.5, r.width * 0.85 + 100, r.height * 1.05 + 90, ink.light ? -k : k];
    root.style.setProperty('--ink', S.toCss(ink.rgb));
    root.style.setProperty('--ink-soft', S.toCss(ink.rgb, 0.9));
    root.style.setProperty('--halo', ink.halo);
    const top = pickInk([[frame.W * 0.08, 40], [frame.W * 0.16, 40], [frame.W * 0.84, 40], [frame.W * 0.92, 70]], sc);
    const tk = clamp((6.4 - top.worst) / 5, 0, 0.4);
    fx.topK = top.light ? -tk : tk;
    root.style.setProperty('--top-ink', S.toCss(top.rgb));
    root.style.setProperty('--top-halo', top.halo);
  }

  /* ---------------- 补间 ---------------- */
  const tweens = new Map();
  function tween(key, to, dur, ease, done) {
    if (reduced() || dur <= 0) {
      tweens.delete(key);
      fx[key] = to;
      if (done) done();
      requestFrame();
      return;
    }
    tweens.set(key, { from: fx[key], to, t0: performance.now(), dur, ease: ease || easeInOut, done });
    requestFrame();
  }
  function runTweens(now) {
    let active = false;
    for (const [k, tw] of tweens) {
      const t = Math.min(1, (now - tw.t0) / tw.dur);
      fx[k] = tw.from + (tw.to - tw.from) * tw.ease(t);
      if (t >= 1) { tweens.delete(k); if (tw.done) tw.done(); } else active = true;
    }
    return active;
  }
  function finishTweens() {
    for (const [k, tw] of tweens) { fx[k] = tw.to; if (tw.done) tw.done(); }
    tweens.clear();
  }

  /* ---------------- 主循环:演出时 60fps;环境动效 24fps(胶片帧率),久置 12fps;隐藏即停 ---------------- */
  let sim = targetHour();
  let simRate = 0;
  let sweep = null; // { t0, dur, from, to, ease, final, travel }
  let scene = null;
  let raf = 0, timer = 0, last = 0, fastUntil = 0, idleSince = performance.now();
  let baseRot = 0;
  let pwFocused = false;
  let state = 'idle';

  function requestFrame() {
    if (raf || document.hidden) return;
    if (timer) { clearTimeout(timer); timer = 0; }
    raf = requestAnimationFrame(tick);
  }
  let slowFrames = 0, sampled = 0;
  function tick(now) {
    raf = 0;
    // 120Hz 屏上封顶 60fps
    if (last && now - last < 15) { raf = requestAnimationFrame(tick); return; }
    const dt = Math.min(0.1, last ? (now - last) / 1000 : 1 / 60);
    last = now;
    const busy = step(now, dt);
    // 弱 GPU 兜底:演出期间连续掉帧就把画布分辨率降一档(最多两档)
    if (busy && dt > 0 && dt < 0.1 && quality > 0.56 && !reduced()) {
      sampled++;
      if (dt > 0.024) slowFrames++;
      if (sampled >= 45) {
        if (slowFrames > 30) { quality *= 0.75; measure(); }
        sampled = 0; slowFrames = 0;
      }
    }
    draw();
    if (document.hidden) return;
    if (reduced()) { if (busy) raf = requestAnimationFrame(tick); return; }
    if (busy || now < fastUntil) { raf = requestAnimationFrame(tick); return; }
    const fps = now - idleSince > 60000 ? 12 : 24;
    timer = setTimeout(() => { timer = 0; raf = requestAnimationFrame(tick); }, 1000 / fps - 4);
  }

  function step(now, dt) {
    const red = reduced();
    let busy = false;
    if (sweep) {
      const t = Math.min(1, (now - sweep.t0) / sweep.dur);
      const prev = sim;
      sim = sweep.from + (sweep.to - sweep.from) * sweep.ease(t);
      simRate = (sim - prev) / Math.max(dt, 1e-3);
      busy = true;
      if (t >= 1) {
        const s = sweep;
        sweep = null;
        simRate = 0;
        sim = norm(s.to);
        if (s.travel) onTravelDone(s.final);
      }
    } else {
      sim = targetHour();
      simRate = 0;
    }
    const windTarget = state === 'loading' ? 8 : 1;
    wind += (windTarget - wind) * (1 - Math.exp(-dt / (windTarget > wind ? 0.45 : 0.9)));
    if (Math.abs(windTarget - wind) > 0.02) busy = true;
    if (!red) {
      fx.time += dt;
      fx.cloudT += dt * (BASE_WIND * wind + Math.min(Math.abs(simRate) * 5.5, 22));
    }
    // 密码隐私云:聚焦 → 云层从上风处滑来遮住日 / 月;失焦 → 云散
    const veilOn = pwFocused && state !== 'success';
    if (red) {
      fx.veilA = veilOn ? 1 : 0;
      fx.veilF = veilOn ? 1.5 : 0;
    } else if (veilOn) {
      if (fx.veilF > 2.3) fx.veilF = 0; // 上一团云已飘远:从上风处再来一团
      fx.veilA = 1;
      fx.veilF += (1.5 - fx.veilF) * (1 - Math.exp(-dt / 0.42));
      if (Math.abs(1.5 - fx.veilF) > 0.004) busy = true;
    } else if (fx.veilA > 0) {
      fx.veilF += dt * 1.5; // 顺风飘走
      if (fx.veilF >= 3.3) { fx.veilA = 0; fx.veilF = 0; } else busy = true;
    }
    if (runTweens(now)) busy = true;
    if (red) { fx.par[0] = 0; fx.par[1] = 0; } else {
      const k = 1 - Math.exp(-dt / 0.4);
      fx.par[0] += (parTarget[0] - fx.par[0]) * k;
      fx.par[1] += (parTarget[1] - fx.par[1]) * k;
      if (Math.abs(parTarget[0] - fx.par[0]) + Math.abs(parTarget[1] - fx.par[1]) > 0.004) busy = true;
    }
    return busy;
  }

  let lastClock = '', lastPhase = '', lastPix = '';
  function draw() {
    if (!frame) measure();
    scene = S.computeScene(sim, today, frame);
    if (fx.bloom > 0) {
      const useMoon = el.handoff.classList.contains('night');
      fx.bloomP = useMoon ? [scene.moon.x, scene.moon.y] : [scene.sun.x, Math.min(scene.sun.y, frame.horizon - 6)];
    }
    renderer.render(scene, fx, size);
    const t = fmtHour(sim);
    if (t !== lastClock) { el.clock.textContent = t; el.clock.setAttribute('datetime', t); lastClock = t; }
    const ph = phaseOf(sim);
    if (ph !== lastPhase) { el.phase.textContent = ph; lastPhase = ph; }
    el.copy.style.transform = fx.par[0] || fx.par[1] ? `translate3d(${(-fx.par[0] * 6).toFixed(2)}px, ${(-fx.par[1] * 4).toFixed(2)}px, 0)` : '';
    // 像素星:哆啦美藏在夜空里的一颗星,随星野转动
    const show = scene.pal.stars > 0.55 && state !== 'success';
    let pix = 'off';
    if (show) {
      const ax = frame.mobile ? frame.W * 0.14 : frame.W * 0.315;
      const ay = frame.mobile ? frame.horizon - frame.spanY * 0.42 : frame.horizon * 0.25;
      const a = scene.rot - baseRot;
      const dx = ax - scene.pole.x, dy = ay - scene.pole.y;
      const x = scene.pole.x + dx * Math.cos(a) - dy * Math.sin(a) + fx.par[0] * 3;
      const y = scene.pole.y + dx * Math.sin(a) + dy * Math.cos(a) + fx.par[1] * 3 - paneTop;
      pix = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
    }
    if (pix !== lastPix) {
      if (pix !== 'off') el.pix.style.transform = pix;
      el.pix.classList.toggle('on', pix !== 'off');
      lastPix = pix;
    }
  }

  /* ---------------- 开场:约 3 秒延时摄影,天色从前一个时段流到此刻 ---------------- */
  const timers = [];
  function later(ms, fn) { const id = setTimeout(fn, ms); timers.push(id); return id; }
  function clearTimers() { while (timers.length) clearTimeout(timers.pop()); }
  const stateTimers = [];
  function laterS(ms, fn) { const id = setTimeout(fn, ms); stateTimers.push(id); return id; }
  function clearStateTimers() { while (stateTimers.length) clearTimeout(stateTimers.pop()); }

  const REVEAL = [
    [16, () => el.curtain.classList.add('lift')],
    [650, () => reveal('.wordmark, #phase')],
    [950, () => reveal('#date, #lunar')],
    [1500, () => reveal('#eyebrow')],
    [1720, () => reveal('#greet')],
    [2550, () => reveal('#sub-wrap')],
    [2750, () => reveal('.login .field:nth-child(1)')],
    [2850, () => reveal('.login .field:nth-child(2)')],
    [2950, () => reveal('#go')],
    [3350, () => reveal('.seal')],
    [3450, () => reveal('.billing')],
    [5600, () => root.classList.remove('intro', 'quick')],
  ];
  function reveal(sel) { document.querySelectorAll(sel).forEach((n) => n.classList.add('in')); }
  function revealAll() { reveal('.rv, .greet, .seal'); }
  function hideAll() { document.querySelectorAll('.rv, .greet, .seal').forEach((n) => n.classList.remove('in')); }

  function startIntro() {
    clearTimers();
    measure();
    const to = targetHour();
    applyCopy(to, true);
    applyInk(to);
    baseRot = to * 15 * Math.PI / 180;
    if (reduced()) { finishInstant(to); return; }
    root.classList.remove('quick', 'instant');
    root.classList.add('intro');
    hideAll();
    el.curtain.classList.remove('lift');
    void el.curtain.offsetWidth;
    sweep = { t0: performance.now(), dur: 3400, from: to - 3.5, to, ease: timelapse };
    sim = sweep.from;
    for (const [ms, fn] of REVEAL) later(ms, fn);
    requestFrame();
  }
  function finishInstant(to) {
    clearTimers();
    root.classList.add('instant');
    revealAll();
    el.curtain.classList.add('lift');
    root.classList.remove('intro', 'quick');
    if (sweep && !sweep.travel) { sweep = null; sim = norm(to != null ? to : targetHour()); simRate = 0; }
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('instant')));
    requestFrame();
  }
  function userSkip() {
    clearTimers();
    root.classList.add('quick');
    revealAll();
    el.curtain.classList.add('lift');
    if (sweep && !sweep.travel) sweep = { t0: performance.now(), dur: 520, from: sim, to: sweep.to, ease: easeOutCubic };
    later(700, () => root.classList.remove('intro', 'quick'));
    requestFrame();
  }
  function anyInput(e) {
    if (e.target && e.target.closest && e.target.closest('[data-sample-kit]')) return;
    if (root.classList.contains('intro') && !root.classList.contains('quick')) userSkip();
  }
  window.addEventListener('pointerdown', anyInput, true);
  window.addEventListener('keydown', anyInput, true);

  /* ---------------- 样页:跳到任意时刻(短延时摄影) ---------------- */
  function onTravelDone(final) {
    applyCopy(final);
    applyInk(final);
    el.copy.classList.remove('fading');
  }
  function travelTo(h) {
    override = h;
    const target = h == null ? realHour() : h;
    syncDial(target);
    try {
      const u = new URL(location.href);
      if (h == null) u.searchParams.delete('hour'); else u.searchParams.set('hour', String(Math.round(h * 100) / 100));
      history.replaceState(null, '', u);
    } catch (e) { /* 忽略 */ }
    baseRot = target * 15 * Math.PI / 180;
    if (reduced()) { sweep = null; sim = target; applyCopy(target); applyInk(target); requestFrame(); return; }
    const delta = norm(target - sim + 12) - 12;
    if (Math.abs(delta) < 0.01) { applyCopy(target); applyInk(target); requestFrame(); return; }
    if (phaseOf(target) !== currentPhase) el.copy.classList.add('fading');
    sweep = { t0: performance.now(), dur: 1500 + Math.abs(delta) * 110, from: sim, to: sim + delta, ease: bezier(0.45, 0, 0.2, 1), travel: true, final: target };
    requestFrame();
  }
  function syncDial(h) {
    el.dialRange.value = String(Math.round(norm(h) * 60) % 1440);
    el.dialOut.textContent = fmtHour(h);
  }
  if (!embedded) {
    el.dial.hidden = false;
    syncDial(targetHour());
    let swapTimer = 0;
    el.dialRange.addEventListener('input', () => {
      const h = parseInt(el.dialRange.value, 10) / 60;
      override = h;
      sweep = null;
      sim = h;
      baseRot = h * 15 * Math.PI / 180;
      el.dialOut.textContent = fmtHour(h);
      applyInk(h);
      if (phaseOf(h) !== currentPhase && !swapTimer) {
        el.copy.classList.add('fading');
        swapTimer = setTimeout(() => { swapTimer = 0; applyCopy(override != null ? override : h); el.copy.classList.remove('fading'); }, 240);
      }
      requestFrame();
    });
    el.dialRange.addEventListener('change', () => {
      try { const u = new URL(location.href); u.searchParams.set('hour', String(Math.round(parseInt(el.dialRange.value, 10) / 60 * 100) / 100)); history.replaceState(null, '', u); } catch (e) { /* 忽略 */ }
    });
  }

  /* ---------------- 表单与状态 ---------------- */
  function setInvalid(a, b) { el.user.setAttribute('aria-invalid', String(!!a)); el.pass.setAttribute('aria-invalid', String(!!b)); }
  function setLoadingUI(on) {
    el.go.classList.toggle('is-loading', on);
    el.go.setAttribute('aria-busy', String(on));
    el.goLabel.textContent = on ? '登录中' : '登录';
    el.user.readOnly = on;
    el.pass.readOnly = on;
  }
  function resetScene() {
    tween('expo', 1, 700);
    tween('bloom', 0, 0);
    tween('trail', 0, 0);
    tween('clear', 0, 0);
    tweens.delete('shadow');
    fx.shadow = -1;
  }
  function setState(name, msg) {
    clearStateTimers();
    if (name !== 'success') {
      root.classList.remove('leaving');
      el.handoff.classList.remove('on', 'night');
      el.note.classList.remove('show');
    }
    if (name === 'idle') {
      setLoadingUI(false);
      el.err.textContent = '';
      setInvalid(false, false);
      resetScene();
    } else if (name === 'loading') {
      setLoadingUI(true);
      el.err.textContent = '';
      setInvalid(false, false);
      resetScene();
    } else if (name === 'error') {
      setLoadingUI(false);
      const text = msg || '账号或密码错误';
      el.err.textContent = '';
      laterS(30, () => { el.err.textContent = text; });
      if (text === '请输入账号和密码') setInvalid(!el.user.value.trim(), !el.pass.value);
      else setInvalid(true, true);
      // 天色微暗,一片云影从画面掠过
      tween('bloom', 0, 0); tween('trail', 0, 0); tween('clear', 0, 0);
      tween('expo', 0.84, 420, easeOutCubic, () => tween('expo', 0.92, 1400));
      if (!reduced()) {
        fx.shadow = -0.4;
        tween('shadow', 1.45, 1900, easeInOutSine, () => { fx.shadow = -1; });
      }
    } else if (name === 'success') {
      setLoadingUI(false);
      el.err.textContent = '';
      setInvalid(false, false);
      handoff();
    } else return;
    state = name;
    requestFrame();
  }

  /* 成功交棒:白天 → 日光晕铺满画面;夜里 → 云散、星野拖成星轨,月光晕开;最后落到阅读器底色 */
  function handoff() {
    if (!scene) draw();
    const night = scene.pal.stars > 0.4;
    pwFocused = false;
    root.classList.remove('pw');
    root.classList.add('leaving');
    el.handoff.classList.toggle('night', night);
    el.handoff.classList.remove('on');
    el.note.classList.remove('show');
    if (reduced()) {
      laterS(120, () => el.handoff.classList.add('on'));
      laterS(900, () => el.note.classList.add('show'));
      return;
    }
    tween('expo', 1, 500);
    if (!night) {
      fx.bloomC = [1.0, 0.968, 0.91];
      tween('bloom', 1, 1750, bezier(0.5, 0, 0.75, 0.6));
      laterS(1550, () => el.handoff.classList.add('on'));
      laterS(2650, () => el.note.classList.add('show'));
    } else {
      fx.bloomC = [0.78, 0.84, 1.0];
      tween('clear', 0.55, 1100, easeInOut);
      tween('expo', 0.9, 1400, easeInOut);
      tween('trail', 1, 2300, easeInOutSine);
      if (scene.moon.vis > 0.2 && scene.lum.I > 0) tween('bloom', 0.22, 2300, easeInOut);
      laterS(2050, () => el.handoff.classList.add('on'));
      laterS(2950, () => el.note.classList.add('show'));
    }
  }

  el.form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (state === 'loading' || state === 'success') return;
    const u = el.user.value.trim();
    const p = el.pass.value;
    if (!u || !p) {
      setState('error', '请输入账号和密码');
      (u ? el.pass : el.user).focus();
      return;
    }
    setState('loading');
    laterS(900, () => {
      if (u === 'admin' && p === 'admin') setState('success');
      else setState('error', '账号或密码错误');
    });
  });
  for (const input of [el.user, el.pass]) {
    input.addEventListener('input', () => {
      if (state === 'error') {
        clearStateTimers();
        state = 'idle';
        el.err.textContent = '';
        setInvalid(false, false);
        tween('expo', 1, 900);
      }
    });
  }
  el.pass.addEventListener('focus', () => { pwFocused = true; root.classList.add('pw'); requestFrame(); });
  el.pass.addEventListener('blur', () => { pwFocused = false; root.classList.remove('pw'); requestFrame(); });
  for (const input of [el.user, el.pass]) {
    input.addEventListener('focus', () => {
      if (frame && frame.mobile) setTimeout(() => input.scrollIntoView({ block: 'center', behavior: reduced() ? 'auto' : 'smooth' }), 320);
    });
  }

  /* ---------------- 环境:指针视差、尺寸、键盘、可见性、动效档位 ---------------- */
  const finePointer = window.matchMedia && window.matchMedia('(pointer: fine)').matches;
  window.addEventListener('pointermove', (e) => {
    if (!finePointer || reduced()) return;
    parTarget[0] = (e.clientX / window.innerWidth - 0.5) * 2;
    parTarget[1] = (e.clientY / window.innerHeight - 0.5) * 2;
    const now = performance.now();
    fastUntil = now + 900;
    idleSince = now;
    requestFrame();
  }, { passive: true });
  window.addEventListener('keydown', () => { idleSince = performance.now(); }, true);

  let resizeQueued = false;
  function onResize() {
    if (resizeQueued) return;
    resizeQueued = true;
    requestAnimationFrame(() => {
      resizeQueued = false;
      measure();
      applyInk(sweep ? (sweep.final != null ? sweep.final : sweep.to) : targetHour());
      requestFrame();
    });
  }
  window.addEventListener('resize', onResize);
  window.addEventListener('scroll', () => { measure(); requestFrame(); }, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => {
      const kb = window.innerWidth < 760 && window.visualViewport.height < window.innerHeight * 0.78;
      if (kb !== root.classList.contains('kb')) {
        root.classList.toggle('kb', kb);
        onResize();
        setTimeout(onResize, 450);
      }
    });
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      if (timer) { clearTimeout(timer); timer = 0; }
    } else {
      last = 0;
      requestFrame();
    }
  });
  window.addEventListener('sample:motion', (e) => {
    if (e.detail && e.detail.reduced) {
      if (sweep && !sweep.travel) finishInstant(sweep.to);
      else if (sweep) { const f = sweep.final; sweep = null; sim = f; onTravelDone(f); }
      finishTweens();
      if (fx.shadow > -1) fx.shadow = -1;
      parTarget[0] = parTarget[1] = 0;
    }
    last = 0;
    requestFrame();
  });
  if (document.fonts && document.fonts.ready) {
    const c = COPY[phaseOf(targetHour())];
    document.fonts.load('900 120px "Dorami Serif"', c.g).catch(() => {});
    document.fonts.ready.then(onResize);
  }

  // 胶片颗粒:一次性生成的噪点贴图,CSS 低帧率抖动
  try {
    const c = document.createElement('canvas');
    c.width = c.height = 160;
    const g = c.getContext('2d');
    const img = g.createImageData(160, 160);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = 128 + (Math.random() - 0.5) * 255 * 0.9;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    el.grain.style.backgroundImage = `url(${c.toDataURL('image/png')})`;
  } catch (e) { /* 忽略 */ }

  const moonImg = new Image();
  moonImg.onload = () => { renderer.setMoonTexture(moonImg); requestFrame(); };
  moonImg.src = 'assets/moon.jpg';

  /* ---------------- 样页契约 ---------------- */
  window.LoginSample = {
    replay() { setState('idle'); startIntro(); },
    skipIntro() { finishInstant(); },
    setState(name) { setState(name); },
    extraControls: [
      { label: '拂晓', run: () => travelTo(6.5) },
      { label: '正午', run: () => travelTo(12) },
      { label: '黄昏', run: () => travelTo(17.6) },
      { label: '深夜', run: () => travelTo(23.5) },
      { label: '此刻', run: () => travelTo(null) },
    ],
  };

  startIntro();
})();
