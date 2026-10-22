/* 哆啦美登录页样页 · E 天色 —— 天空模型 + WebGL 渲染器
 *
 * 全部输入只来自客户端本地时间与日期:
 *   太阳高度 / 方位 = 本地钟点 + 日期(太阳赤纬),假定纬度 32°N(只影响弧线高度与季节昼长);
 *   月相 = 日期(朔望月 29.53 天),月亮位置按月龄滞后太阳的时角近似。
 * 画面:多层渐变天空 + 透视云层(域扭曲 fBm)+ 海面倒影与日 / 月光路 + 星野(可拖成星轨)。
 * 没有 WebGL 时退回 Canvas 2D 的静态渐变版。
 */
(function () {
  'use strict';

  const DEG = Math.PI / 180;
  const LAT = 32 * DEG;
  const SYNODIC = 29.530588853;
  const NEW_MOON_REF = Date.UTC(2000, 0, 6, 18, 14);

  /* ---------------- 小工具 ---------------- */
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
  const hex = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; };
  const mix3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  const lin = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const luminance = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
  const toCss = (rgb, a) => {
    const c = rgb.map((v) => Math.round(clamp(v, 0, 1) * 255));
    return a == null ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  };

  /* ---------------- 调色板:按太阳高度(度)插值;上午 = 拂晓玫瑰金,傍晚 = 黄昏紫橙 ---------------- */
  const COLS = ['zen', 'mid', 'hor', 'glow', 'sea', 'lit', 'shade'];
  function keys(rows) {
    return rows.map((r) => {
      const o = { a: r[0], glowAmt: r[5], stars: r[9] };
      o.zen = hex(r[1]); o.mid = hex(r[2]); o.hor = hex(r[3]); o.glow = hex(r[4]);
      o.sea = hex(r[6]); o.lit = hex(r[7]); o.shade = hex(r[8]);
      return o;
    });
  }
  //            alt   zenith     mid        horizon    glow     glowAmt  sea        cloudLit   cloudShade stars
  const NIGHT = [-90, '#04061c', '#080c2c', '#141a48', '#181e50', 0.0, '#03050f', '#262c58', '#080b20', 1.0];
  const DAY25 = [25, '#5b97d6', '#9cc8ec', '#e0eef8', '#fffbf2', 0.25, '#164670', '#ffffff', '#b2c4da', 0];
  const DAY90 = [90, '#5893d4', '#94c2ea', '#dbebf7', '#ffffff', 0.2, '#134272', '#ffffff', '#b6c8de', 0];
  const MORNING = keys([
    NIGHT,
    [-18, '#060a2c', '#0d1442', '#1f275e', '#2a2c6c', 0.12, '#04071a', '#30366a', '#0a0e28', 1.0],
    [-12, '#0b1338', '#1b2664', '#3a4088', '#6a4e92', 0.4, '#080c26', '#4e4c86', '#131838', 0.75],
    [-7, '#18265e', '#354a92', '#8c80bc', '#e09cac', 0.7, '#121a3e', '#c48eaa', '#2c3268', 0.35],
    [-3, '#2a427f', '#6c7ab4', '#eab4b4', '#ffc6ae', 0.9, '#1e2650', '#ffc8bc', '#585e96', 0.08],
    [0.5, '#3e5e9c', '#a0a2c8', '#ffd2bc', '#ffd0a4', 0.95, '#283258', '#ffdccc', '#7c80aa', 0],
    [5, '#4c76b2', '#a6bcdc', '#f8dfd0', '#ffe6c4', 0.65, '#213a60', '#fff2e8', '#96a2c2', 0],
    [12, '#4e86c6', '#9cc2e4', '#e6eff4', '#fff6e6', 0.4, '#1b4468', '#ffffff', '#aab9d0', 0],
    DAY25, DAY90,
  ]);
  const EVENING = keys([
    NIGHT,
    [-18, '#060a2c', '#0e1344', '#222660', '#2e2a6c', 0.14, '#04071a', '#30366a', '#0a0e28', 1.0],
    [-12, '#0c1236', '#1e2260', '#453b80', '#82407a', 0.45, '#090a24', '#5c4884', '#16163a', 0.75],
    [-7, '#18215c', '#3d3d8a', '#9e6ca2', '#e8727c', 0.75, '#161636', '#d07c9c', '#322a60', 0.35],
    [-3, '#27367c', '#6c5ca2', '#f2907a', '#ff8050', 0.95, '#22193e', '#ffa082', '#5c4884', 0.08],
    [0.5, '#355290', '#9c76a8', '#ffaa6c', '#ff7c34', 1.0, '#2a2242', '#ffb684', '#705c8a', 0],
    [5, '#4470a8', '#b49cb8', '#ffca94', '#ffa656', 0.8, '#2a3656', '#ffd4a8', '#8c86a6', 0],
    [12, '#4a80c0', '#a4bcd8', '#f6e2c8', '#ffd8a4', 0.5, '#1f3e64', '#fff4e2', '#a2aec6', 0],
    DAY25, DAY90,
  ]);
  const SUN_COLS = [[-1, '#ff5a24'], [1, '#ff7030'], [4, '#ff9c50'], [9, '#ffcc88'], [18, '#ffefd2'], [40, '#fffaf0']].map((r) => [r[0], hex(r[1])]);
  const MOON_LIGHT = hex('#c9d3f2');
  const BELT = hex('#e6a6b8');

  function lerpKey(a, b, t) {
    const o = { glowAmt: a.glowAmt + (b.glowAmt - a.glowAmt) * t, stars: a.stars + (b.stars - a.stars) * t };
    for (const c of COLS) o[c] = mix3(a[c], b[c], t);
    return o;
  }
  function samplePalette(list, alt) {
    if (alt <= list[0].a) return lerpKey(list[0], list[0], 0);
    for (let i = 1; i < list.length; i++) {
      if (alt <= list[i].a) return lerpKey(list[i - 1], list[i], (alt - list[i - 1].a) / (list[i].a - list[i - 1].a));
    }
    const last = list[list.length - 1];
    return lerpKey(last, last, 0);
  }
  function sunColor(alt) {
    if (alt <= SUN_COLS[0][0]) return SUN_COLS[0][1];
    for (let i = 1; i < SUN_COLS.length; i++) {
      if (alt <= SUN_COLS[i][0]) return mix3(SUN_COLS[i - 1][1], SUN_COLS[i][1], (alt - SUN_COLS[i - 1][0]) / (SUN_COLS[i][0] - SUN_COLS[i - 1][0]));
    }
    return SUN_COLS[SUN_COLS.length - 1][1];
  }

  /* ---------------- 天文近似 ---------------- */
  function dayOfYear(d) { return Math.floor((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(d.getFullYear(), 0, 0)) / 86400000); }
  function declination(doy) { return 23.44 * DEG * Math.sin(2 * Math.PI * (284 + doy) / 365); }
  function altAz(hourAngle, decl) {
    const sinAlt = Math.sin(LAT) * Math.sin(decl) + Math.cos(LAT) * Math.cos(decl) * Math.cos(hourAngle);
    const alt = Math.asin(clamp(sinAlt, -1, 1));
    const az = Math.atan2(Math.sin(hourAngle), Math.cos(hourAngle) * Math.sin(LAT) - Math.tan(decl) * Math.cos(LAT));
    return { alt, az }; // az:自正南起算,向西为正
  }
  function sunTimes(date) {
    const decl = declination(dayOfYear(date));
    const c = -Math.tan(LAT) * Math.tan(decl);
    const h0 = Math.acos(clamp(c, -1, 1)) / DEG / 15;
    return { rise: 12 - h0, set: 12 + h0 };
  }
  function moonAge(date) {
    let d = ((date.getTime() - NEW_MOON_REF) / 86400000) % SYNODIC;
    if (d < 0) d += SYNODIC;
    return d;
  }
  function moonPhaseName(age) {
    if (age < 1.0 || age > 28.6) return '新月';
    if (age < 6.4) return '蛾眉月';
    if (age < 8.4) return '上弦月';
    if (age < 13.8) return '盈凸月';
    if (age < 15.8) return '满月';
    if (age < 21.1) return '亏凸月';
    if (age < 23.1) return '下弦月';
    return '残月';
  }

  /* 场景:frame = { W, H, horizon, cx, spanX, spanY } 皆为 CSS px */
  function computeScene(hour, date, frame) {
    const decl = declination(dayOfYear(date));
    const ha = (hour - 12) * 15 * DEG;
    const sun = altAz(ha, decl);
    const age = moonAge(date);
    const phase = age / SYNODIC * 2 * Math.PI; // 0 朔 → π 望
    const elong = phase <= Math.PI ? phase : 2 * Math.PI - phase;
    const moon = altAz(ha - phase, decl * Math.cos(phase));
    const morning = 0.5 - 0.5 * Math.sin(ha);
    const sunAlt = sun.alt / DEG;
    const moonAlt = moon.alt / DEG;
    const pal = lerpKey(samplePalette(EVENING, sunAlt), samplePalette(MORNING, sunAlt), morning);

    const proj = (o) => ({
      x: frame.cx + Math.cos(o.alt) * Math.sin(o.az) * frame.spanX,
      y: frame.horizon - Math.sin(o.alt) * frame.spanY,
    });
    const sp = proj(sun);
    const mp = proj(moon);
    const unit = Math.min(frame.W, frame.H);
    const sunR = clamp(unit * 0.021, 11, 24);
    const moonR = clamp(unit * 0.026, 13, 28);
    const night = smooth(4, -8, sunAlt); // 0 = 白天,1 = 夜(月亮亮度、星光)
    const illum = (1 - Math.cos(phase)) / 2;

    // 月面受光方向:画面上指向太阳,强度按日月角距
    let dx = sp.x - mp.x, dy = sp.y - mp.y;
    const dl = Math.hypot(dx, dy) || 1;
    dx /= dl; dy /= dl;
    const moonL = [dx * Math.sin(elong), dy * Math.sin(elong), -Math.cos(elong)];

    // 主光源(云的受光 / 海面光路 / 隐私云的目标):太阳在地平线附近时用太阳,入夜后交给月亮
    const sunI = smooth(-4, 1, sunAlt);
    const moonI = smooth(-4, -9, sunAlt) * smooth(-1, 3, moonAlt) * (0.25 + 0.75 * illum);
    let lum;
    if (sunAlt > -4) {
      lum = { x: sp.x, y: sp.y, I: sunI, c: sunColor(sunAlt) };
    } else if (moonI > 0) {
      lum = { x: mp.x, y: mp.y, I: moonI * 0.6, c: MOON_LIGHT };
    } else {
      lum = { x: frame.cx, y: frame.horizon - frame.spanY * 0.62, I: 0, c: [0, 0, 0] };
    }
    lum.h = (frame.horizon - lum.y) / frame.H;

    // 南天极(星野旋转中心)在地平线下方
    const pole = { x: frame.cx, y: frame.horizon + Math.sin(LAT) * frame.spanY * 1.25 };
    const belt = smooth(3, -1, sunAlt) * smooth(-10, -3, sunAlt);

    return {
      hour, sunAlt, moonAlt, morning, pal, illum, age, phaseName: moonPhaseName(age), night,
      sun: { x: sp.x, y: sp.y, r: sunR, vis: smooth(-1.2, 0.3, sunAlt), c: sunColor(sunAlt) },
      moon: { x: mp.x, y: mp.y, r: moonR, vis: smooth(-1.5, 1, moonAlt), L: moonL, bright: night },
      lum, pole, rot: hour * 15 * DEG, belt, frame,
    };
  }

  /* JS 侧复刻 shader 的天空渐变,供文字墨色取样 */
  function skyColorAt(scene, u, px) {
    const p = scene.pal;
    let c = mix3(p.hor, p.mid, smooth(0, 0.5, u));
    c = mix3(c, p.zen, smooth(0.3, 1, u));
    const dx = (px - scene.sun.x) / scene.frame.W;
    const g = Math.exp(-Math.abs(dx) * 2.6) * Math.exp(-u * 4.5);
    c = mix3(c, p.glow, clamp(g * p.glowAmt, 0, 1));
    return c;
  }

  /* ---------------- GLSL ---------------- */
  const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

  const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;
uniform float uDpr;
uniform float uHz;
uniform float uT;
uniform float uCT;
uniform float uCover;
uniform vec2 uSeed;
uniform vec3 uZen;
uniform vec3 uMid;
uniform vec3 uHor;
uniform vec3 uGlow;
uniform vec3 uSea;
uniform vec3 uLit;
uniform vec3 uShade;
uniform float uGlowAmt;
uniform float uStars;
uniform vec4 uSun;
uniform vec3 uSunC;
uniform vec4 uMoon;
uniform vec3 uMoonL;
uniform float uMoonB;
uniform float uIllum;
uniform float uHasTex;
uniform sampler2D uTex;
uniform vec4 uLum;
uniform vec3 uLumC;
uniform vec2 uPole;
uniform float uRot;
uniform float uTrail;
uniform float uVeilF;
uniform float uVeilA;
uniform vec2 uVeilP;
uniform float uExpo;
uniform float uShadow;
uniform float uBloom;
uniform vec3 uBloomC;
uniform vec2 uBloomP;
uniform vec2 uPar;
uniform float uBelt;
uniform vec3 uBeltC;
uniform float uClear;
uniform vec4 uScrim;
uniform float uScrimK;
uniform float uTopK;

float W;
float H;
float HZ;
float SKYF;

float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
vec2 hash22(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.xx + p3.yz) * p3.zy) * 2.0 - 1.0;
}
float gnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  float a = dot(hash22(i), f);
  float b = dot(hash22(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0));
  float c = dot(hash22(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0));
  float d = dot(hash22(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
const mat2 ROT = mat2(1.6, 1.2, -1.2, 1.6);
float fbm3(vec2 p) {
  float s = 0.0; float a = 0.5;
  for (int i = 0; i < 3; i++) { s += a * gnoise(p); p = ROT * p; a *= 0.5; }
  return s;
}
float fbm5(vec2 p) {
  float s = 0.0; float a = 0.5;
  for (int i = 0; i < 5; i++) { s += a * gnoise(p); p = ROT * p; a *= 0.5; }
  return s;
}

vec3 skyGrad(float u, float px) {
  vec3 c = mix(uHor, uMid, smoothstep(0.0, 0.5, u));
  c = mix(c, uZen, smoothstep(0.3, 1.0, u));
  float dx = (px - uSun.x) / W;
  float g = exp(-abs(dx) * 2.6) * exp(-u * 4.5);
  c = mix(c, uGlow, clamp(g * uGlowAmt, 0.0, 1.0));
  if (uBelt > 0.001) {
    float side = smoothstep(0.15, 0.75, abs(dx));
    float band = exp(-pow((u - 0.15) / 0.08, 2.0));
    c = mix(c, uBeltC, band * side * uBelt * 0.42);
    float sh = 1.0 - smoothstep(0.0, 0.09, u);
    c = mix(c, uZen * 1.15, sh * side * uBelt * 0.32);
  }
  return c;
}

vec3 starField(vec2 p, float u) {
  vec2 d = p - uPole;
  float r = length(d);
  float th = atan(d.y, d.x) - uRot;
  float RW = 7.0;
  float ring = floor(r / RW);
  float rs0 = ring * RW;
  float cw = 15.0 / max(rs0 + RW * 0.5, 1.0);
  float L = uTrail * 0.62;
  float c0 = floor(th / cw);
  float n = floor(L / cw) + 1.0;
  if (uTrail > 0.0) n += 1.0;
  float px = 1.0 / uDpr;
  vec3 acc = vec3(0.0);
  for (int i = 0; i < 72; i++) {
    if (float(i) >= n) break;
    float ci = c0 + float(i);
    vec2 id = vec2(ring, ci);
    float h1 = hash12(id);
    if (h1 > 0.085) continue;
    float h2 = hash12(id + 17.31);
    float h3 = hash12(id + 41.70);
    float h4 = hash12(id + 7.77);
    float ths = (ci + 0.15 + 0.7 * h2) * cw;
    float rs = rs0 + RW * (0.3 + 0.4 * h3);
    float dth = th - ths;
    float along = 0.0;
    if (dth > 0.0) along = dth * r;
    else if (dth < -L) along = (-L - dth) * r;
    float dr = r - rs;
    float onTrail = step(-L, dth) * step(dth, 0.0) * step(0.001, L);
    float sig = max((0.5 + 0.7 * pow(h4, 4.0)) * (1.0 - 0.25 * onTrail), 0.7 * px);
    float k = exp(-(along * along + dr * dr) / (2.0 * sig * sig));
    float tw = 0.78 + 0.22 * sin(uT * (1.3 + 3.0 * h2) + h3 * 6.283);
    float bPoint = (0.10 + 1.5 * pow(h4, 7.0)) * tw;
    float bTrail = (0.012 + 1.05 * pow(h4, 4.0)) * (0.5 + 0.45 * (1.0 + dth / max(L, 0.001)));
    float b = mix(bPoint, bTrail, onTrail);
    vec3 sc = mix(vec3(0.74, 0.82, 1.0), vec3(1.0, 0.9, 0.78), h3);
    acc += sc * b * k;
  }
  return acc * smoothstep(0.0, 0.14, u);
}

vec3 addSun(vec3 c, vec2 p) {
  if (uSun.w <= 0.0) return c;
  vec2 d = p - uSun.xy;
  float low = 1.0 - smoothstep(0.0, 0.05, (HZ - uSun.y) / H);
  d.y *= 1.0 + 0.16 * low;
  float l = length(d);
  float r = uSun.z;
  float disc = 1.0 - smoothstep(r - 1.0, r + 0.7, l);
  float limb = 0.84 + 0.16 * sqrt(max(0.0, 1.0 - (l * l) / (r * r)));
  float lowK = 1.0 - smoothstep(0.0, 0.2, (HZ - uSun.y) / H);
  float lw = length((p - uSun.xy) * vec2(1.0 / (1.0 + lowK * 2.2), 1.0));
  float aure = exp(-l / (r * 1.5)) * 0.5 + exp(-l / (H * 0.09)) * 0.22 + exp(-lw / (H * 0.16)) * (0.06 + lowK * 0.3) + exp(-l / (H * 0.38)) * 0.07;
  c += uSunC * aure * uSun.w;
  float hk = smoothstep(0.0, 0.16, (HZ - uSun.y) / H); // 贴近地平线时大气消光:日轮更橙、更暗,不过曝
  c = mix(c, max(c, uSunC * mix(0.92, 1.05, hk) * limb + 0.04 * hk), disc * uSun.w);
  c += uSunC * disc * limb * mix(0.12, 0.9, hk) * uSun.w;
  return c;
}

vec3 addMoon(vec3 c, vec2 p) {
  if (uMoon.w <= 0.0) return c;
  vec2 dv = p - uMoon.xy;
  vec2 dm = dv / uMoon.z;
  float l = length(dm);
  float dist = length(dv);
  float halo = exp(-dist / (uMoon.z * 2.2)) * 0.20 + exp(-dist / (H * 0.14)) * 0.07;
  c += vec3(0.70, 0.76, 0.96) * halo * uMoon.w * uMoonB * (0.25 + 0.75 * uIllum);
  vec2 tuv = clamp(dm * 0.5 + 0.5, 0.0, 1.0);
  float tex = texture2D(uTex, tuv).r;
  if (l < 1.0 + 2.0 / uMoon.z) {
    float z = sqrt(max(0.0, 1.0 - l * l));
    vec3 n = vec3(dm, z);
    float lit = smoothstep(-0.05, 0.09, dot(n, uMoonL));
    float alb = mix(0.86 + 0.1 * gnoise(dm * 3.0 + 4.0), mix(0.66, 1.18, tex), uHasTex);
    float edge = 1.0 - smoothstep(1.0 - 1.1 / uMoon.z, 1.0 + 0.6 / uMoon.z, l);
    vec3 mc = vec3(0.985, 0.965, 0.92) * alb * (0.86 + 0.14 * z);
    float a = edge * uMoon.w * lit * mix(0.62, 1.0, uMoonB);
    c = mix(c, mc * mix(vec3(1.10, 1.06, 1.0), vec3(1.07), uMoonB), a);
    c += vec3(0.20, 0.23, 0.34) * alb * edge * (1.0 - lit) * 0.12 * uMoonB * uMoon.w;
  }
  return c;
}

vec4 clouds(vec2 p, float u) {
  float hh = u * SKYF;
  float depth = 1.0 / (hh * 2.3 + 0.065);
  vec2 cp = vec2((p.x - W * 0.5) / H * depth * 0.95, depth * 1.15);
  cp += uPar * vec2(0.06, 0.035) * (0.6 + depth * 0.12);
  cp = cp * 0.92 + uSeed;
  cp.x -= uCT * 0.021;
  cp.y += uCT * 0.0025;
  vec2 w = vec2(fbm3(cp * 0.6 + vec2(1.7, 9.2 + uCT * 0.004)), fbm3(cp * 0.6 + vec2(8.3 - uCT * 0.003, 2.8)));
  vec2 q = cp + w * 1.15;
  float n = fbm5(q) * 0.9 + 0.5;
  float cover = uCover + uClear;
  float den = smoothstep(cover, cover + 0.2, n);
  vec2 toL = uLum.xy - p;
  float dl = length(toL);
  vec2 ld = toL / max(dl, 1.0);
  float n2 = fbm5(q + ld * 0.16) * 0.9 + 0.5;
  float lightK = smoothstep(0.0, 0.25, uLum.z);
  float light = clamp(0.55 + (n - n2) * 2.6 * lightK, 0.0, 1.0);
  vec3 cc = mix(uShade, uLit, light);
  float near = exp(-dl / (H * 0.09));
  float wide = exp(-dl / (H * 0.24));
  float rim = (1.0 - den) * den * 4.0;
  cc += uLumC * uLum.z * (wide * 0.22 + near * (0.35 + rim * 1.5) + near * near * den * 0.6);
  cc = mix(cc, uHor, (1.0 - smoothstep(0.0, 0.22, u)) * 0.55);
  den *= smoothstep(0.0, 0.07, u) * (1.0 - smoothstep(0.76, 0.92, u));
  // 高空卷云:各向异性拉伸的细丝,黎明 / 黄昏时最先染色
  vec2 cq = vec2((p.x - W * 0.5) / H * depth * 0.42, depth * 3.4) + uSeed.yx * 1.3;
  cq.x -= uCT * 0.012;
  float ci = fbm3(cq) + 0.12;
  float cden = smoothstep(0.06, 0.42, ci) * 0.38 * smoothstep(0.18, 0.62, u) * (1.0 - smoothstep(0.74, 0.9, u)) * (1.0 - uClear);
  vec3 ccol = mix(uShade, uLit, 0.82) + uLumC * wide * 0.4 * uLum.z;
  float a = den * mix(0.96, 0.8, uStars);
  vec3 outc = mix(ccol, cc, a / max(a + cden * (1.0 - a), 0.0001));
  return vec4(outc, a + cden * (1.0 - a));
}

// 密码隐私云:一团低云从上风处(左)飘来停在日 / 月前;失焦后继续顺风飘走
float veilCover() {
  return smoothstep(0.5, 1.2, uVeilF) * (1.0 - smoothstep(1.7, 2.4, uVeilF)) * uVeilA;
}
vec4 veilLayer(vec2 p) {
  vec2 dv = (p - uVeilP) / H;
  vec2 vq = vec2(dv.x * 2.0, dv.y * 3.6) + vec2(4.1 - uCT * 0.03, 7.3) + uSeed * 0.21;
  vec2 vw = vec2(fbm3(vq * 0.7 + 2.0), fbm3(vq * 0.7 + 6.0));
  float n = fbm5(vq + vw * 1.1) * 0.9 + 0.5;
  // 云团整体轮廓:中心浓、外缘稀,边缘完全由噪声决定(不画几何椭圆)
  float rad = length(dv * vec2(0.46, 1.3));
  float m = dv.x / 0.55 * 0.5 + 0.5;
  float front = uVeilF - m + (vw.y - 0.0) * 0.35;
  float shape = smoothstep(-0.1, 0.6, front) * (1.0 - smoothstep(1.2, 1.9, front)) * (1.0 - smoothstep(0.0, 0.44, rad));
  float th = mix(0.96, 0.30, shape) - 0.3 * shape * exp(-dot(dv, dv) / 0.004);
  float d = smoothstep(th, th + 0.12, n) * uVeilA;
  float dl = length(p - uVeilP);
  float core = exp(-dl / (H * 0.05));
  float near = exp(-dl / (H * 0.1));
  float rim = clamp((1.0 - d) * d * 4.0, 0.0, 1.0);
  vec3 vc = mix(uShade, uLit, 0.32);
  vc += uLumC * uLum.z * (core * 0.5 + near * rim * 0.6 * (0.6 + 0.8 * vw.x) + near * 0.08);
  vc = mix(vc, uHor, (1.0 - smoothstep(0.0, 0.05, (HZ - p.y) / H)) * 0.3);
  return vec4(vc, d * 0.97);
}

vec3 sea(vec2 p, float d) {
  float px = d * H;
  float depth = 1.0 / (d * 3.0 + 0.01);
  vec2 sp = vec2((p.x - W * 0.5) / H * depth, depth);
  float w1 = gnoise(sp * vec2(1.4, 3.4) + vec2(uT * 0.05, uT * 0.42));
  float w2 = gnoise(sp * vec2(3.1, 7.0) + vec2(-uT * 0.08, uT * 0.75));
  float wave = w1 * 0.62 + w2 * 0.38;
  float hr = d * 1.8 + 0.010 + wave * (0.012 + d * 0.55);
  float ur = clamp(hr / SKYF, 0.0, 1.0);
  vec3 refl = skyGrad(ur, p.x + wave * 30.0);
  float F = 0.03 + 0.97 * exp(-px / 34.0);
  if (F > 0.12) {
    vec4 cl = clouds(vec2(p.x + wave * 12.0, HZ - hr * H), ur);
    refl = mix(refl, cl.rgb, cl.a * 0.8);
  }
  vec3 c = mix(uSea, refl * 0.9, F);
  c *= 1.0 + wave * 0.12 * (1.0 - F);
  if (uShadow > -0.9) {
    float blob = fbm3(sp * vec2(0.22, 0.55) + vec2(7.0, 3.0));
    float sx = p.x / W - uShadow + blob * 0.45;
    float s = 1.0 - smoothstep(0.06, 0.24, abs(sx));
    c *= 1.0 - 0.5 * s * smoothstep(0.0, 0.03, d);
  }
  if (uLum.z > 0.0) {
    float hl = max(uLum.w, 0.0);
    float dx = (p.x - uLum.x) / H;
    float wdt = 0.006 + d * (0.16 + hl * 0.8);
    float colm = exp(-dx * dx / (wdt * wdt));
    float vf = exp(-d / (0.03 + hl * 0.55));
    float s = gnoise(sp * vec2(5.0, 22.0) + vec2(0.0, uT * 1.3) + wave * 1.6);
    float spark = smoothstep(0.1, 0.48, s);
    c += uLumC * uLum.z * colm * vf * (0.16 + spark * 1.5) * (1.0 - 0.85 * veilCover());
  }
  return c;
}

vec3 shoulder(vec3 x) {
  vec3 k = 0.82 + 0.18 * (1.0 - exp(-(x - 0.82) / 0.18));
  return mix(x, k, step(0.82, x));
}

void main() {
  W = uRes.x / uDpr;
  H = uRes.y / uDpr;
  HZ = uHz;
  SKYF = max(HZ / H, 0.05);
  vec2 p = vec2(gl_FragCoord.x / uDpr, H - gl_FragCoord.y / uDpr);
  vec3 c;
  if (p.y <= HZ) {
    float u = clamp((HZ - p.y) / HZ, 0.0, 1.0);
    c = skyGrad(u, p.x);
    if (uStars > 0.003) c += starField(p + uPar * 3.0, u) * uStars;
    c = addMoon(c, p);
    c = addSun(c, p);
    vec4 cl = clouds(p, u);
    c = mix(c, cl.rgb, cl.a);
    if (uVeilA > 0.0) {
      vec4 vl = veilLayer(p);
      c = mix(c, vl.rgb, vl.a);
    }
    if (abs(uTopK) > 0.001) {
      float band = 1.0 - smoothstep(70.0, 175.0, p.y);
      vec3 tgt = uTopK > 0.0 ? mix(c, vec3(1.0), 0.5) : c * 0.55;
      c = mix(c, tgt, abs(uTopK) * band);
    }
    if (abs(uScrimK) > 0.001) {
      vec2 sd = (p - uScrim.xy) / uScrim.zw;
      float fall = exp(-dot(sd, sd) * 1.25);
      vec3 tgt = uScrimK > 0.0 ? mix(c, vec3(1.0), 0.55) : c * 0.5;
      c = mix(c, tgt, abs(uScrimK) * fall);
    }
  } else {
    c = sea(p, (p.y - HZ) / H);
  }
  c += uHor * 0.08 * exp(-abs(p.y - HZ) / 1.2);
  c *= uExpo;
  if (uBloom > 0.0) {
    float db = length(p - uBloomP) / H;
    float r = 0.03 + uBloom * uBloom * 1.9;
    float b = exp(-db / r);
    c = mix(c, uBloomC, clamp(b * (0.35 + uBloom * 1.4), 0.0, 1.0));
    c += uBloomC * uBloom * uBloom * 0.45;
  }
  vec2 q = p / vec2(W, H) - 0.5;
  c *= 1.0 - 0.16 * smoothstep(0.35, 0.95, length(q * vec2(1.0, 1.2))) * mix(0.25, 1.0, smoothstep(-0.45, 0.15, q.y));
  c = shoulder(c);
  c += (hash12(gl_FragCoord.xy + fract(uT * 7.0) * 113.0) - 0.5) / 255.0;
  gl_FragColor = vec4(c, 1.0);
}
`;

  const UNIFORMS = ['uTopK', 'uScrim', 'uScrimK', 'uRes', 'uDpr', 'uHz', 'uT', 'uCT', 'uCover', 'uSeed', 'uZen', 'uMid', 'uHor', 'uGlow', 'uSea', 'uLit', 'uShade',
    'uGlowAmt', 'uStars', 'uSun', 'uSunC', 'uMoon', 'uMoonL', 'uMoonB', 'uIllum', 'uHasTex', 'uTex', 'uLum', 'uLumC', 'uPole',
    'uRot', 'uTrail', 'uVeilF', 'uVeilA', 'uVeilP', 'uExpo', 'uShadow', 'uBloom', 'uBloomC', 'uBloomP', 'uPar', 'uBelt', 'uBeltC', 'uClear'];

  function createRenderer(canvas) {
    let gl = null;
    try {
      const opts = { antialias: false, alpha: false, depth: false, stencil: false, premultipliedAlpha: false, preserveDrawingBuffer: false, powerPreference: 'default' };
      gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts);
    } catch (e) { gl = null; }
    if (gl) {
      const r = buildGL(gl);
      if (r) return r;
    }
    return build2D(canvas);
  }

  function buildGL(gl) {
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.warn('[天色] shader 编译失败,退回 2D:', gl.getShaderInfoLog(s));
        return null;
      }
      return s;
    };
    const vs = sh(gl.VERTEX_SHADER, VERT);
    const fs = sh(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.warn('[天色] program 链接失败,退回 2D');
      return null;
    }
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const U = {};
    for (const n of UNIFORMS) U[n] = gl.getUniformLocation(prog, n);

    // 月面贴图:先放 1×1 灰占位,加载完再换
    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, 1, 1, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, new Uint8Array([180]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.uniform1i(U.uTex, 0);
    let hasTex = 0;

    let lost = false;
    gl.canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); lost = true; });

    return {
      kind: 'webgl',
      setMoonTexture(img) {
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, img);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        hasTex = 1;
      },
      render(scene, fx, size) {
        if (lost) return;
        const { w, h, dpr } = size;
        if (gl.canvas.width !== w || gl.canvas.height !== h) { gl.canvas.width = w; gl.canvas.height = h; }
        gl.viewport(0, 0, w, h);
        const p = scene.pal;
        gl.uniform2f(U.uRes, w, h);
        gl.uniform1f(U.uDpr, dpr);
        gl.uniform1f(U.uHz, scene.frame.horizon);
        gl.uniform1f(U.uT, fx.time);
        gl.uniform1f(U.uCT, fx.cloudT);
        gl.uniform1f(U.uCover, fx.cover);
        gl.uniform2f(U.uSeed, fx.seed[0], fx.seed[1]);
        gl.uniform3fv(U.uZen, p.zen); gl.uniform3fv(U.uMid, p.mid); gl.uniform3fv(U.uHor, p.hor);
        gl.uniform3fv(U.uGlow, p.glow); gl.uniform3fv(U.uSea, p.sea); gl.uniform3fv(U.uLit, p.lit); gl.uniform3fv(U.uShade, p.shade);
        gl.uniform1f(U.uGlowAmt, p.glowAmt);
        gl.uniform1f(U.uStars, p.stars * (1 - 0.35 * scene.illum * scene.moon.vis));
        const s = scene.sun, m = scene.moon, l = scene.lum;
        const par = fx.par || [0, 0];
        gl.uniform4f(U.uSun, s.x - par[0] * 5, s.y - par[1] * 3, s.r, s.vis);
        gl.uniform3fv(U.uSunC, s.c);
        gl.uniform4f(U.uMoon, m.x - par[0] * 5, m.y - par[1] * 3, m.r, m.vis);
        gl.uniform3fv(U.uMoonL, m.L);
        gl.uniform1f(U.uMoonB, m.bright);
        gl.uniform1f(U.uIllum, scene.illum);
        gl.uniform1f(U.uHasTex, hasTex);
        gl.uniform4f(U.uLum, l.x - par[0] * 5, l.y - par[1] * 3, l.I, l.h);
        gl.uniform3fv(U.uLumC, l.c);
        gl.uniform2f(U.uPole, scene.pole.x, scene.pole.y);
        gl.uniform1f(U.uRot, scene.rot);
        gl.uniform1f(U.uTrail, fx.trail);
        gl.uniform1f(U.uVeilF, fx.veilF);
        gl.uniform1f(U.uVeilA, fx.veilA);
        gl.uniform2f(U.uVeilP, l.x - par[0] * 5, l.y - par[1] * 3);
        gl.uniform1f(U.uExpo, fx.expo);
        gl.uniform1f(U.uShadow, fx.shadow);
        gl.uniform1f(U.uBloom, fx.bloom);
        gl.uniform3fv(U.uBloomC, fx.bloomC);
        gl.uniform2f(U.uBloomP, fx.bloomP[0], fx.bloomP[1]);
        gl.uniform2f(U.uPar, par[0], par[1]);
        gl.uniform1f(U.uBelt, scene.belt);
        gl.uniform3fv(U.uBeltC, BELT);
        gl.uniform1f(U.uClear, fx.clear || 0);
        const sc = fx.scrim || [0, 0, 1, 1, 0];
        gl.uniform4f(U.uScrim, sc[0], sc[1], sc[2], sc[3]);
        gl.uniform1f(U.uScrimK, sc[4]);
        gl.uniform1f(U.uTopK, fx.topK || 0);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      },
    };
  }

  /* 无 WebGL:2D 静态渐变版(天空三段渐变 + 日月圆盘 + 海面) */
  function build2D(canvas) {
    const ctx = canvas.getContext('2d');
    return {
      kind: '2d',
      setMoonTexture() {},
      render(scene, fx, size) {
        if (!ctx) return;
        const { w, h, dpr } = size;
        if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const f = scene.frame, p = scene.pal, hz = f.horizon;
        const g = ctx.createLinearGradient(0, 0, 0, hz);
        g.addColorStop(0, toCss(p.zen)); g.addColorStop(0.55, toCss(p.mid)); g.addColorStop(1, toCss(mix3(p.hor, p.glow, p.glowAmt * 0.4)));
        ctx.fillStyle = g; ctx.fillRect(0, 0, f.W, hz);
        const s = scene.sun, m = scene.moon;
        if (m.vis > 0) { ctx.fillStyle = `rgba(246,242,232,${0.5 + 0.5 * m.bright})`; ctx.beginPath(); ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2); ctx.fill(); }
        if (s.vis > 0) {
          const rg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r * 6);
          rg.addColorStop(0, toCss(s.c, 0.9)); rg.addColorStop(1, toCss(s.c, 0));
          ctx.fillStyle = rg; ctx.fillRect(s.x - s.r * 6, s.y - s.r * 6, s.r * 12, s.r * 12);
          ctx.fillStyle = toCss(s.c); ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill();
        }
        const sg = ctx.createLinearGradient(0, hz, 0, f.H);
        sg.addColorStop(0, toCss(mix3(p.hor, p.sea, 0.35))); sg.addColorStop(0.25, toCss(p.sea)); sg.addColorStop(1, toCss(mix3(p.sea, [0, 0, 0], 0.3)));
        ctx.fillStyle = sg; ctx.fillRect(0, hz, f.W, f.H - hz);
        if (fx.expo < 1) { ctx.fillStyle = `rgba(0,0,0,${1 - fx.expo})`; ctx.fillRect(0, 0, f.W, f.H); }
      },
    };
  }

  window.SkySim = { computeScene, skyColorAt, luminance, toCss, mix3, hex, sunTimes, moonAge, moonPhaseName, createRenderer, smooth, clamp };
})();
