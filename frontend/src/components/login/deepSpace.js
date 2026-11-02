// 深空剧场 · 场景引擎(登录页背景,issue #214 重制)
//
// 一个以摄像机为中心、环面回绕的三维星场:摄像机可以无限偏航、沿视线无限推进,星场密度处处一致
// (旧版是固定盒子绕中心转,偏航久了推轨会变成横移、画面变稀)。
//
// 渲染 = WebGL1 两个程序:
//   ① 背景:近黑底 + 朝登录卡的靛辉 + 开场在 GPU 上烘焙一次的圆柱全景星云(随偏航视差平移);
//   ② 胶囊精灵:星点 / 景深光斑 / 运动光轨 / 星座连线 / 信号脉冲都是同一个「胶囊」图元——
//      CPU 每帧投影、写一个动态顶点缓冲,一次 drawElements,加色混合。
// 星座是成组的「星图」(每组 4–8 颗、树状连线,偶尔闭环),在三维里有真实结构,随镜头产生视差;
// 不再按屏幕距离临时连线(那样镜头一动连线就重组闪烁)。
//
// 演出接口:开场「跃迁抵达」(光轨减速成星点、星座逐笔连线)· setVeil 密码聚焦「失焦」
// (景深拉开,整片星空化成柔焦光斑——与 logo 蒙眼同一出哑剧)· ping 键入「信号」(一道脉冲沿某个
// 星座的连线跑过)· kick 失败「撞偏」· startWarp 成功「跃迁离场」。
// 交互沿用旧版的灵魂:鼠标横移 = 偏航转头,纵移 = 推轨;另加指针位置的微视差。
// 触屏:只认同一次按压之内的滑动(横滑转头),两次轻触之间不算位移;不做指针视差(手指位置不是视线)。
// 跃迁离场时偏航与视差归零,光轨严格从屏幕中心放射——竖屏上焦点不跑偏。
// 无 WebGL / 初始化失败时退成 Canvas 2D 静止一帧(live = false,调用方据此跳过跃迁),画在调用方提供的
// 回退画布上(React 管理的节点,引擎不替换 DOM)。片元着色器没有 highp 时不烘焙星云、不做抖动(中间量会超 fp16)。

const TAU = Math.PI * 2;

// —— 世界与镜头 ——
const B = 1800;                       // 回绕盒半边长(世界单位);远景按三维距离在 B 之内淡尽,回绕永不可见
const B2 = B * 2;
const ZCLIP = 36;
const FADE_NEAR0 = 40, FADE_NEAR1 = 170;
const FADE_FAR0 = 1050, FADE_FAR1 = 1760;
// 鼠标注入:FRIC 是「每 1/60 秒」的速度保留率,按 dt 归一化(120Hz 与 60Hz 手感一致)
const KYAW = 0.00085, KDOLLY = 2.4, FRIC = 0.9, YAWMAX = 0.55, VZMAX = 780;
const AMB_YAW = 0.02;                 // 环境自转 rad/s
const AMB_DRIFT = 16;                 // 环境巡航 单位/s:无输入时也在星海里缓缓前行
// 景深:coc = A · |z − zf| / z(像素)。常态只有近处星成光斑;失焦态整片化开
const DOF_IDLE = { a: 2.6, zf: 640 };
const DOF_VEIL = { a: 15, zf: 150 };

// 色温:靛(主体)/ 琥珀(少数暖星)/ 青白 / 冷白(星座主星)/ 尘
const TONES = [
  [150, 146, 248],
  [244, 199, 138],
  [186, 226, 248],
  [228, 228, 255],
  [168, 172, 226],
];
const EDGE_RGB = [132, 126, 240];
const PULSE_RGB = [214, 210, 255];
const BOKEH_RGB = [138, 120, 255];

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const smooth = (a, b, x) => {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
const rand = (a, b) => a + Math.random() * (b - a);
const wrapB = (v) => v - B2 * Math.round(v / B2);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

// ————————————————————————————— 着色器 —————————————————————————————

// 抖动的哈希中间量远超 fp16 上限,只在片元有 highp 时做
const PREC = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
float dither() { return (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0; }
#else
precision mediump float;
float dither() { return 0.0; }
#endif
`;

const FULL_VS = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5);
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

// 全景星云烘焙:圆柱面上采样三维噪声,u 方向首尾无缝;输出 ×4 存进 8 位纹理换精度(取样时 ×0.25)
const PANO_FS = `${PREC}
varying vec2 vUv;
uniform vec3 uSeed;
float h3(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float n3(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(h3(i), h3(i + vec3(1, 0, 0)), f.x),
                 mix(h3(i + vec3(0, 1, 0)), h3(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(h3(i + vec3(0, 0, 1)), h3(i + vec3(1, 0, 1)), f.x),
                 mix(h3(i + vec3(0, 1, 1)), h3(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
float fbm(vec3 p) {
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) {
    s += a * n3(p);
    p = p * 2.03 + vec3(1.7, 9.2, 3.1);
    a *= 0.5;
  }
  return s;
}
void main() {
  float th = vUv.x * 6.2831853;
  // 竖向与横向角尺度一致(横向:半径 1.7 的圆;竖向:取样时 v 每单位高度 0.30),噪声不被拉成竖条
  float y = (vUv.y - 0.5) * 5.6;
  vec3 p = vec3(cos(th) * 1.7, y, sin(th) * 1.7) + uSeed;
  vec3 q = vec3(fbm(p * 1.1), fbm(p * 1.1 + 5.2), fbm(p * 1.1 + 9.7));
  float n = fbm(p * 1.35 + q * 1.7);
  float band = exp(-pow((y - 0.9 * sin(th + 0.9)) / 1.05, 2.0));
  float lanes = smoothstep(0.42, 0.72, fbm(p * 3.1 + q * 1.3));
  float neb = smoothstep(0.40, 0.92, n);
  float k = neb * (0.30 + 0.70 * band) * (1.0 - 0.6 * lanes * band);
  vec3 indigo = vec3(0.36, 0.33, 0.91);
  vec3 violet = vec3(0.58, 0.36, 0.92);
  vec3 cyan = vec3(0.20, 0.60, 0.90);
  vec3 col = mix(indigo, violet, smoothstep(0.38, 0.68, fbm(p * 0.7 + 13.0)));
  col = mix(col, cyan, 0.55 * smoothstep(0.60, 0.84, fbm(p * 0.9 + 21.0)));
  vec3 c = col * k * 0.15 + vec3(0.62, 0.64, 0.98) * band * (0.010 + 0.016 * n);
  gl_FragColor = vec4(clamp(c * 4.0 + dither(), 0.0, 1.0), 1.0);
}`;

const BG_FS = `${PREC}
varying vec2 vUv;
uniform sampler2D uPano;
uniform vec2 uView;
uniform float uYaw;
uniform float uPitch;
uniform float uFocal;
uniform float uSpeed;
uniform float uNeb;
uniform float uExpo;
uniform float uCore;
uniform vec4 uGlow;
void main() {
  vec2 p = vUv * uView;
  vec2 c = uView * 0.5;
  // 顶部极淡的深靛天光 → 近黑
  vec2 q = (vUv - vec2(0.5, -0.12)) / vec2(0.92, 0.82);
  vec3 col = mix(vec3(0.063, 0.055, 0.157), vec3(0.016, 0.016, 0.055), smoothstep(0.0, 0.62, length(q)));
  // 朝登录卡的靛辉(锚点随卡片位置)
  vec2 g = (p - uGlow.xy) / uGlow.zw;
  col += vec3(0.357, 0.329, 0.91) * 0.09 * exp(-dot(g, g) * 2.0);
  // 圆柱全景星云:方向角 = 偏航 + atan(屏幕横向),高度 = 圆柱面上的投影高度
  float zoom = 1.0 + uSpeed * 0.45;
  vec2 rel = (p - c) / (uFocal * zoom);
  float ang = uYaw + atan(rel.x);
  float v = 0.5 + (rel.y / sqrt(1.0 + rel.x * rel.x) + uPitch) * 0.30;
  col += texture2D(uPano, vec2(ang / 6.2831853, v)).rgb * 0.25 * uNeb;
  // 速度光:跃迁时视线正前方的一团冷光(按短边归一,竖屏上也收得住);竖屏另加一枚紧致的光核做焦点
  float r = length((p - c) / min(uView.x, uView.y));
  col += vec3(0.56, 0.56, 1.0) * uSpeed * 0.16 * exp(-r * r * 7.0);
  col += vec3(0.80, 0.78, 1.0) * uCore * uSpeed * uSpeed * 0.34 * exp(-r * r * 70.0);
  col *= uExpo;
  col += dither();
  gl_FragColor = vec4(col, 1.0);
}`;

const SPRITE_VS = `
attribute vec2 aPos;
attribute vec2 aLocal;
attribute vec4 aShape;
attribute vec4 aColor;
uniform vec2 uView;
varying vec2 vLocal;
varying vec4 vShape;
varying vec4 vColor;
void main() {
  vLocal = aLocal;
  vShape = aShape;
  vColor = aColor;
  vec2 clip = aPos / uView * 2.0 - 1.0;
  gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
}`;

// 胶囊:x 半长 / y 核半径 / z 辉光半径 / w 光斑混合(0 = 星点高斯核 + 辉光,1 = 景深光斑圆盘 + 亮边)
const SPRITE_FS = `${PREC}
varying vec2 vLocal;
varying vec4 vShape;
varying vec4 vColor;
void main() {
  float d = length(vec2(max(abs(vLocal.x) - vShape.x, 0.0), vLocal.y));
  float r = vShape.y;
  float k = d / r;
  float star = exp(-k * k) + 0.32 * exp(-d / vShape.z);
  float disc = 1.0 - smoothstep(r * 0.6 - 0.6, r + 0.6, d);
  float bokeh = disc * (0.8 + 0.4 * smoothstep(r * 0.25, r * 0.8, d));
  gl_FragColor = vColor * mix(star, bokeh, vShape.w);
}`;

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    gl.deleteShader(sh);
    throw new Error(`deepSpace shader: ${log}`);
  }
  return sh;
}

// 链接后立即拆掉着色器对象(程序留着二进制即可);任何一步失败都把这一步建的对象删干净再抛
function program(gl, vs, fs, attribs) {
  const shaders = [];
  const p = gl.createProgram();
  try {
    shaders.push(compile(gl, gl.VERTEX_SHADER, vs));
    shaders.push(compile(gl, gl.FRAGMENT_SHADER, fs));
    shaders.forEach((sh) => gl.attachShader(p, sh));
    attribs.forEach((name, i) => gl.bindAttribLocation(p, i, name));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(`deepSpace link: ${gl.getProgramInfoLog(p)}`);
  } catch (err) {
    shaders.forEach((sh) => gl.deleteShader(sh));
    gl.deleteProgram(p);
    throw err;
  }
  shaders.forEach((sh) => { gl.detachShader(p, sh); gl.deleteShader(sh); });
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const info = gl.getActiveUniform(p, i);
    u[info.name] = gl.getUniformLocation(p, info.name);
  }
  return { p, u };
}

// ————————————————————————————— 星场 —————————————————————————————

// 一组星图:根星在原点,其余星挂在前一颗(七成)或任意已有星上,步长 70–150;偶尔加一条闭环边。
// 偏移压扁 z,让图形大体正对镜头、又保有真实的纵深视差。
function makeCluster(cx, cy, cz) {
  const n = 4 + Math.floor(Math.random() * 5);
  const ox = new Float32Array(n), oy = new Float32Array(n), oz = new Float32Array(n);
  const parent = new Int16Array(n);
  parent[0] = -1;
  for (let i = 1; i < n; i++) {
    const p = Math.random() < 0.7 ? i - 1 : Math.floor(Math.random() * i);
    parent[i] = p;
    const th = Math.random() * TAU;
    const step = rand(70, 150);
    ox[i] = ox[p] + Math.cos(th) * step;
    oy[i] = oy[p] + Math.sin(th) * step;
    oz[i] = oz[p] + rand(-0.5, 0.5) * step;
  }
  const edges = [];
  for (let i = 1; i < n; i++) edges.push(parent[i], i);
  if (n >= 5 && Math.random() < 0.4) {
    const a = n - 1;
    for (let j = 0; j < n - 2; j++) {
      if (j !== parent[a] && Math.hypot(ox[a] - ox[j], oy[a] - oy[j], oz[a] - oz[j]) < 190) {
        edges.push(j, a);
        break;
      }
    }
  }
  // 质心归零,图形围绕组中心
  let mx = 0, my = 0, mz = 0;
  for (let i = 0; i < n; i++) { mx += ox[i]; my += oy[i]; mz += oz[i]; }
  for (let i = 0; i < n; i++) { ox[i] -= mx / n; oy[i] -= my / n; oz[i] -= mz / n; }
  const r0 = new Float32Array(n), tone = new Uint8Array(n);
  const brightest = Math.floor(Math.random() * n);
  for (let i = 0; i < n; i++) {
    r0[i] = i === brightest ? rand(1.9, 2.4) : rand(1.15, 1.75);
    const roll = Math.random();
    tone[i] = i === brightest ? 3 : roll < 0.12 ? 1 : roll < 0.3 ? 2 : roll < 0.55 ? 3 : 0;
  }
  return {
    cx, cy, cz, n, ox, oy, oz, r0, tone, edges,
    drawStart: 0,
    tw: rand(0.4, 1.1), ph: Math.random() * TAU,
    // 每帧投影结果
    sx: new Float32Array(n), sy: new Float32Array(n), sz: new Float32Array(n),
    sa: new Float32Array(n), coc: new Float32Array(n), vis: new Uint8Array(n),
    pulseT0: -10,
  };
}

function putVertex(v, o, x, y, lx, ly, L, R, gR, bok, r, g, b) {
  v[o] = x; v[o + 1] = y; v[o + 2] = lx; v[o + 3] = ly;
  v[o + 4] = L; v[o + 5] = R; v[o + 6] = gR; v[o + 7] = bok;
  v[o + 8] = r; v[o + 9] = g; v[o + 10] = b; v[o + 11] = 0;
  return o + 12;
}

// ————————————————————————————— 静止回退 —————————————————————————————

function mountStatic(canvas) {
  const noop = () => {};
  const ctx = canvas.getContext('2d');
  const paint = () => {
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = window.innerWidth, H = window.innerHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const count = Math.round(clamp((W * H) / 2600, 160, 520));
    for (let i = 0; i < count; i++) {
      const tone = TONES[Math.random() < 0.08 ? 1 : Math.random() < 0.2 ? 2 : 0];
      const r = Math.random() < 0.12 ? rand(1, 1.8) : rand(0.4, 0.9);
      ctx.fillStyle = `rgba(${tone[0]},${tone[1]},${tone[2]},${rand(0.25, 0.85).toFixed(2)})`;
      ctx.beginPath(); ctx.arc(Math.random() * W, Math.random() * H, r, 0, TAU); ctx.fill();
    }
  };
  paint();
  window.addEventListener('resize', paint);
  return {
    live: false,
    cleanup() { window.removeEventListener('resize', paint); },
    setVeil: noop, ping: noop, kick: noop, startWarp: noop, setAnchor: noop,
  };
}

// 回退:静止一帧画在调用方的回退画布上并显示它,WebGL 画布藏起来;cleanup 还原显隐(StrictMode 重挂载会再来一遍)。
// 没给回退画布时:WebGL 画布没拿到过上下文就直接画在它上面,拿到过(2D 取不到)就只藏起来、露出 CSS 底。
function mountFallback(glCanvas, fallbackCanvas, hadContext) {
  if (fallbackCanvas) {
    glCanvas.style.visibility = 'hidden';
    fallbackCanvas.classList.add('is-on');
    const api = mountStatic(fallbackCanvas);
    const base = api.cleanup;
    api.cleanup = () => {
      base();
      fallbackCanvas.classList.remove('is-on');
      glCanvas.style.visibility = '';
    };
    return api;
  }
  if (!hadContext) return mountStatic(glCanvas);
  glCanvas.style.visibility = 'hidden';
  const api = mountStatic(document.createElement('canvas'));
  const base = api.cleanup;
  api.cleanup = () => { base(); glCanvas.style.visibility = ''; };
  return api;
}

// ————————————————————————————— 主引擎 —————————————————————————————

export function mountDeepSpace(canvas, { arrival = true, fallbackCanvas = null } = {}) {
  let gl = null;
  try {
    gl = canvas.getContext('webgl', {
      alpha: false, antialias: false, depth: false, stencil: false,
      premultipliedAlpha: true, preserveDrawingBuffer: false, powerPreference: 'high-performance',
    });
  } catch { gl = null; }
  if (!gl) return mountFallback(canvas, fallbackCanvas, false);

  // —— 视口 ——
  let W = 0, H = 0, F = 600, dprCap = 2, dpr = 1;
  // —— 群体 ——
  let dust = null, field = null, clusters = [];
  let nDust = 0, nField = 0;
  let dustStride = 1;           // 低画质档:尘埃隔一画一
  // —— 镜头 ——
  const cam = { x: 0, y: 0, z: 0, yaw: rand(0, TAU), yawV: 0, vz: 0, lx: 0, ly: 0, has: false, pid: -1 };
  const look = { x: 0, y: 0, tx: 0, ty: 0 };
  const dof = { a: DOF_IDLE.a, zf: DOF_IDLE.zf };
  const glow = { x: 0, y: 0, w: 0, h: 0, tx: -1, ty: 0, tw: 0, th: 0 };
  let veil = false;
  let T = 0;
  let arrive = arrival ? { t: 0, v0: 9000 } : null;
  let warp = null;              // { t, v }
  let shake = null;             // { t, dur, amp, freq, dir }
  let lastInputAt = 0;
  let frameNo = 0;
  let perfSum = 0, perfN = 0, perfDone = false;

  // —— GL 资源 ——
  let bgProg = null, spriteProg = null, panoTex = null, fullBuf = null, vbuf = null, ibuf = null;
  const CAP = 5200;
  const FLOATS = 12;            // aPos2 aLocal2 aShape4 aColor4
  const verts = new Float32Array(CAP * 4 * FLOATS);
  let nSprites = 0;
  let lost = false;
  let rafId = 0, running = false, last;

  function initGL() {
    bgProg = program(gl, FULL_VS, BG_FS, ['aPos']);
    spriteProg = program(gl, SPRITE_VS, SPRITE_FS, ['aPos', 'aLocal', 'aShape', 'aColor']);
    fullBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, fullBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    vbuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbuf);
    gl.bufferData(gl.ARRAY_BUFFER, verts.byteLength, gl.DYNAMIC_DRAW);
    const idx = new Uint16Array(CAP * 6);
    for (let i = 0; i < CAP; i++) {
      const v = i * 4, o = i * 6;
      idx[o] = v; idx[o + 1] = v + 1; idx[o + 2] = v + 2;
      idx[o + 3] = v; idx[o + 4] = v + 2; idx[o + 5] = v + 3;
    }
    ibuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    panoTex = bakePano();
  }

  // 主动释放本实例建的 GL 对象(正常清理与初始化失败都走这里;上下文已丢时调用是空操作)
  function releaseGL() {
    if (bgProg) gl.deleteProgram(bgProg.p);
    if (spriteProg) gl.deleteProgram(spriteProg.p);
    [fullBuf, vbuf, ibuf].forEach((b) => { if (b) gl.deleteBuffer(b); });
    if (panoTex) gl.deleteTexture(panoTex);
    bgProg = spriteProg = panoTex = fullBuf = vbuf = ibuf = null;
  }

  function bakePano() {
    const PW = 1024, PH = 512;
    // 片元没有 highp(老移动 GPU):噪声哈希的中间量会超 fp16,星云不烘焙(留 1×1 黑),其余照常
    const hp = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
    const fragHighp = !!hp && hp.precision > 0;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, PW, PH, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    if (fragHighp && gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE) {
      const pano = program(gl, FULL_VS, PANO_FS, ['aPos']);
      gl.viewport(0, 0, PW, PH);
      gl.useProgram(pano.p);
      gl.uniform3f(pano.u.uSeed, rand(0, 40), rand(0, 40), rand(0, 40));
      gl.bindBuffer(gl.ARRAY_BUFFER, fullBuf);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.disable(gl.BLEND);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.deleteProgram(pano.p);
    } else {
      // 帧缓冲不可用 / 片元无 highp:星云留空(1×1 黑)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255]));
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteFramebuffer(fb);
    return tex;
  }

  // —— 群体生成:按「视锥可见份额」反推总量,保证不同视口下看到的星数相近 ——
  function populate() {
    const tanX = W / 2 / F, tanY = H / 2 / F;
    const frac = clamp((tanX * tanY) / 6, 0.03, 0.4);
    const area = W * H;
    nDust = Math.round(clamp(area / 1500, 300, 900) / frac);
    nField = Math.round(clamp(area / 6800, 110, 220) / frac);
    nDust = Math.min(nDust, 12000);
    nField = Math.min(nField, 3200);
    // dust: x y z r0 tw ph a
    dust = new Float32Array(nDust * 7);
    for (let i = 0; i < nDust; i++) {
      const o = i * 7;
      dust[o] = rand(-B, B); dust[o + 1] = rand(-B, B); dust[o + 2] = rand(-B, B);
      dust[o + 3] = rand(0.45, 0.95); dust[o + 4] = rand(0.8, 2.6); dust[o + 5] = Math.random() * TAU;
      dust[o + 6] = rand(0.2, 0.5);
    }
    // field: x y z r0 tw ph a tone
    field = new Float32Array(nField * 8);
    for (let i = 0; i < nField; i++) {
      const o = i * 8;
      field[o] = rand(-B, B); field[o + 1] = rand(-B, B); field[o + 2] = rand(-B, B);
      field[o + 3] = Math.random() < 0.1 ? rand(1.4, 2.1) : rand(0.8, 1.4);
      field[o + 4] = rand(0.5, 1.9); field[o + 5] = Math.random() * TAU;
      field[o + 6] = rand(0.55, 1);
      const roll = Math.random();
      field[o + 7] = roll < 0.08 ? 1 : roll < 0.15 ? 2 : 0;
    }
    // 星图:先在「开场落定后的取景」里摆几组保证构图(避开登录卡所在的右中区域),其余随机散布
    clusters = [];
    // 视野里同时约 5–7 组:星图是点缀,不是网
    const total = Math.round(clamp(area / 420000, 2, 4) / frac);
    const settle = arrival ? 9000 * 0.34 : 0;
    const fx = Math.sin(cam.yaw), fz = Math.cos(cam.yaw);
    const ex = cam.x + fx * settle, ez = cam.z + fz * settle;
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
    const slots = W > H
      ? [[-0.74, -0.58], [-0.3, 0.66], [0.2, -0.72], [0.8, 0.7]]
      : [[-0.5, -0.74], [0.55, -0.4], [-0.45, 0.78]];
    slots.forEach(([nx, ny]) => {
      const z = rand(520, 1050);
      const xr = (nx + rand(-0.08, 0.08)) * tanX * z, yr = (ny + rand(-0.08, 0.08)) * tanY * z;
      // 镜头系 → 世界系(偏航旋转的逆)
      const wx = xr * cy + z * sy, wz = -xr * sy + z * cy;
      clusters.push(makeCluster(wrapB(ex + wx), yr, wrapB(ez + wz)));
    });
    while (clusters.length < total) clusters.push(makeCluster(rand(-B, B), rand(-B, B), rand(-B, B)));
    clusters.forEach((c) => { c.drawStart = arrival ? rand(1.0, 2.3) : -10; });
  }

  // —— 精灵写入:一个胶囊 = 4 个顶点 ——
  function emit(x0, y0, x1, y1, R, glowR, bok, r, g, b, a) {
    if (nSprites >= CAP || a < 0.004) return;
    let dx = x1 - x0, dy = y1 - y0;
    let len = Math.sqrt(dx * dx + dy * dy);
    let ux = 1, uy = 0;
    if (len > 0.01) { ux = dx / len; uy = dy / len; } else len = 0;
    const L = len * 0.5;
    const E = Math.max(R * 2.6 + 1, glowR * 5.5 * (1 - bok), R + 1.5);
    const mx = (x0 + x1) * 0.5, my = (y0 + y1) * 0.5;
    if (mx + L + E < 0 || mx - L - E > W || my + L + E < 0 || my - L - E > H) return;
    const ax = ux * (L + E), ay = uy * (L + E);
    const px = -uy * E, py = ux * E;
    const cr = (r / 255) * a, cg = (g / 255) * a, cb = (b / 255) * a;
    let o = nSprites * 4 * FLOATS;
    o = putVertex(verts, o, mx - ax - px, my - ay - py, -(L + E), -E, L, R, glowR, bok, cr, cg, cb);
    o = putVertex(verts, o, mx + ax - px, my + ay - py, L + E, -E, L, R, glowR, bok, cr, cg, cb);
    o = putVertex(verts, o, mx + ax + px, my + ay + py, L + E, E, L, R, glowR, bok, cr, cg, cb);
    putVertex(verts, o, mx - ax + px, my - ay + py, -(L + E), E, L, R, glowR, bok, cr, cg, cb);
    nSprites++;
  }

  // —— 每帧的视图参数(project 读这些) ——
  let cyaw = 1, syaw = 0, cp = 1, sp = 0, cx = 0, cyScr = 0;
  let blurRot = 0, blurFwd = 0, blurOn = false;
  // project 的输出
  let oX = 0, oY = 0, oZ = 0, oD = 0, oPX = 0, oPY = 0;

  // 相对坐标(已回绕)→ 屏幕;返回 false = 不可见。顺带算出快门起点(运动光轨)
  function project(dx, dy, dz) {
    // 先转到镜头系剔掉身后的一半,再算距离
    const xr = dx * cyaw - dz * syaw;
    const zr0 = dx * syaw + dz * cyaw;
    const zr = dy * sp + zr0 * cp;
    if (zr < ZCLIP) return false;
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 > FADE_FAR1 * FADE_FAR1) return false;
    const dist = Math.sqrt(d2);
    const yr = dy * cp - zr0 * sp;
    const s = F / zr;
    oX = cx + xr * s; oY = cyScr + yr * s; oZ = zr; oD = dist;
    if (blurOn) {
      const xp = xr + zr * blurRot;
      let zp = zr - xr * blurRot + blurFwd;
      if (zp < ZCLIP) zp = ZCLIP;
      const s2 = F / zp;
      oPX = cx + xp * s2; oPY = cyScr + yr * s2;
      // 光轨封顶,超长的从当前点往回截
      const ddx = oPX - oX, ddy = oPY - oY, l = Math.sqrt(ddx * ddx + ddy * ddy);
      if (l > 280) { oPX = oX + (ddx / l) * 280; oPY = oY + (ddy / l) * 280; }
    } else { oPX = oX; oPY = oY; }
    return true;
  }

  function drawStar(r0, tone, baseA, glowMul, cocCap, alertK) {
    const z = oZ;
    const fade = smooth(FADE_NEAR0, FADE_NEAR1, z) * (1 - smooth(FADE_FAR0, FADE_FAR1, oD));
    if (fade <= 0) return;
    const s = F / z;
    const r = r0 * clamp(Math.sqrt(s) * 0.92, 0.5, 2.5);
    let coc = Math.min(cocCap, dof.a * Math.abs(z - dof.zf) / z);
    if (warp) coc *= Math.max(0, 1 - warp.t * 2);
    const w = smooth(1.0, 3.6, coc);
    const Rb = Math.sqrt(r * r + coc * coc);
    const R = r + (Rb - r) * w;
    let a = baseA * fade;
    const aB = a * Math.min(1, (r * r * 4.2 + 1.2) / (Rb * Rb));
    a = a + (aB - a) * w;
    let col = TONES[tone];
    if (w > 0.01) {
      // 光斑偏向品牌靛紫,大面积失焦时不发灰
      const k = 0.55 * w;
      col = [col[0] + (BOKEH_RGB[0] - col[0]) * k, col[1] + (BOKEH_RGB[1] - col[1]) * k, col[2] + (BOKEH_RGB[2] - col[2]) * k];
    }
    let RR = R, gm = glowMul;
    if (alertK > 0 && tone === 1) {
      col = [Math.round(244 + 11 * alertK), Math.round(199 - 141 * alertK), Math.round(138 - 88 * alertK)];
      a = Math.min(1, a * (1 + 1.4 * alertK));
      RR = R * (1 + 0.75 * alertK);
      gm = glowMul * (1 + 1.5 * alertK);
    }
    // 光轨按长度摊薄亮度(部分守恒,保留速度感)
    const sl = Math.hypot(oX - oPX, oY - oPY);
    if (sl > 0.5) a *= Math.pow((2.5 * RR + 1) / (sl + 2.5 * RR + 1), 0.5);
    emit(oPX, oPY, oX, oY, RR, Math.max(0.3, RR * gm), w, col[0], col[1], col[2], a);
  }

  function frame(dt) {
    T += dt;
    frameNo++;
    // —— 镜头动力学 ——
    const decay = Math.pow(FRIC, dt * 60);
    cam.yawV *= decay;
    if (Math.abs(cam.yawV) < 1e-4) cam.yawV = 0;
    if (warp) {
      warp.t += dt;
      // 由慢到快:前 0.9s 温和推进,之后加速度平方放大 + 指数增益爆发
      const ramp = Math.min(1, warp.t / 0.9);
      warp.v = Math.min(32000, (warp.v + (1400 + 15000 * ramp * ramp) * dt) * (1 + 2.2 * ramp * dt));
      cam.vz = warp.v;
    } else if (arrive) {
      arrive.t += dt;
      const v = arrive.v0 * Math.exp(-arrive.t / 0.34);
      cam.vz = Math.max(cam.vz * decay, v);
      if (v < 30) arrive = null;
    } else {
      cam.vz *= decay;
      if (Math.abs(cam.vz) < 0.4) cam.vz = 0;
    }
    let yawOff = 0, yOff = 0, alertK = 0;
    if (shake) {
      shake.t += dt;
      if (shake.t >= shake.dur) shake = null;
      else {
        const k = 1 - shake.t / shake.dur;
        const env = k * k;
        yawOff = shake.dir * shake.amp * Math.sin(shake.t * shake.freq * TAU) * env;
        yOff = H * 0.008 * Math.sin(shake.t * shake.freq * TAU * 1.35 + 1.2) * env;
        alertK = k;
      }
    }
    // 跃迁离场:偏航(含环境自转)在 0.35s 内收住,光轨的消失点钉在屏幕中心
    const steer = warp ? Math.max(0, 1 - warp.t / 0.35) : 1;
    const yawRate = (cam.yawV + AMB_YAW) * steer;
    cam.yaw = (cam.yaw + yawRate * dt) % TAU;
    const fwd = cam.vz + AMB_DRIFT;
    cam.x = wrapB(cam.x + Math.sin(cam.yaw) * fwd * dt);
    cam.z = wrapB(cam.z + Math.cos(cam.yaw) * fwd * dt);

    if (warp) { look.tx = 0; look.ty = 0; }
    const kLook = 1 - Math.exp(-dt * (warp ? 7 : 2.4));
    look.x += (look.tx - look.x) * kLook;
    look.y += (look.ty - look.y) * kLook;
    const tgt = veil ? DOF_VEIL : DOF_IDLE;
    const kDof = 1 - Math.exp(-dt * (veil ? 5.5 : 4));
    dof.a += (tgt.a - dof.a) * kDof;
    dof.zf += (tgt.zf - dof.zf) * kDof;
    if (glow.tx >= 0) {
      const kG = 1 - Math.exp(-dt * 3);
      glow.x += (glow.tx - glow.x) * kG; glow.y += (glow.ty - glow.y) * kG;
      glow.w += (glow.tw - glow.w) * kG; glow.h += (glow.th - glow.h) * kG;
    }

    // —— 视图参数 ——
    const viewYaw = cam.yaw + look.x + yawOff;
    cyaw = Math.cos(viewYaw); syaw = Math.sin(viewYaw);
    cp = Math.cos(look.y); sp = Math.sin(look.y);
    cx = W / 2; cyScr = H / 2 + yOff;
    const speed = Math.abs(cam.vz);
    const shutter = 1 / 45 + Math.min(1, speed / 12000) * (1 / 9 - 1 / 45);
    blurRot = yawRate * shutter;
    blurFwd = fwd * shutter;
    blurOn = Math.abs(blurFwd) > 0.6 || Math.abs(blurRot) > 0.0006;
    const expo = arrive ? smooth(0, 0.75, arrive.t) : 1;
    const veilK = smooth(DOF_IDLE.a + 0.5, DOF_VEIL.a - 2, dof.a); // 0 常态 → 1 失焦
    const speedK = Math.min(1, speed / 12000);

    // —— 背景 ——
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.disable(gl.BLEND);
    gl.useProgram(bgProg.p);
    gl.uniform2f(bgProg.u.uView, W, H);
    gl.uniform1f(bgProg.u.uYaw, ((viewYaw % TAU) + TAU) % TAU);
    gl.uniform1f(bgProg.u.uPitch, look.y);
    gl.uniform1f(bgProg.u.uFocal, F);
    gl.uniform1f(bgProg.u.uSpeed, speedK);
    gl.uniform1f(bgProg.u.uNeb, 1 - 0.3 * veilK);
    gl.uniform1f(bgProg.u.uExpo, expo);
    gl.uniform1f(bgProg.u.uCore, H > W ? 1 : 0);
    gl.uniform4f(bgProg.u.uGlow, glow.x, glow.y, Math.max(1, glow.w), Math.max(1, glow.h));
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, panoTex);
    gl.uniform1i(bgProg.u.uPano, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, fullBuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    for (let i = 1; i < 4; i++) gl.disableVertexAttribArray(i);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    // —— 精灵 ——
    nSprites = 0;
    const starExpo = expo * (1 + speedK * 0.35);
    // 尘埃:细、暗、闪烁更明显;景深封顶(失焦时只是更淡,不铺满大光斑)
    for (let i = 0; i < nDust; i += dustStride) {
      const o = i * 7;
      if (!project(wrapB(dust[o] - cam.x), dust[o + 1] - cam.y, wrapB(dust[o + 2] - cam.z))) continue;
      const tw = 1 + 0.38 * Math.sin(T * dust[o + 4] + dust[o + 5]);
      drawStar(dust[o + 3], 4, dust[o + 6] * tw * starExpo * (1 - 0.45 * veilK), 0.6, 5, 0);
    }
    for (let i = 0; i < nField; i++) {
      const o = i * 8;
      if (!project(wrapB(field[o] - cam.x), field[o + 1] - cam.y, wrapB(field[o + 2] - cam.z))) continue;
      const tw = 1 + 0.2 * Math.sin(T * field[o + 4] + field[o + 5]);
      drawStar(field[o + 3], field[o + 7], field[o + 6] * tw * starExpo, 1.9, 26, alertK);
    }
    // 星图:成员星 + 连线(开场逐笔画出)+ 信号脉冲
    const edgeVis = Math.max(1 - veilK * 0.88, alertK) * (warp ? Math.max(0, 1 - warp.t * 1.8) : 1) * (1 + 0.8 * alertK);
    for (let ci = 0; ci < clusters.length; ci++) {
      const c = clusters[ci];
      const dx = wrapB(c.cx - cam.x), dy = c.cy - cam.y, dz = wrapB(c.cz - cam.z);
      if (dx * dx + dy * dy + dz * dz > (FADE_FAR1 + 420) ** 2) continue;
      const tw = 1 + 0.08 * Math.sin(T * c.tw + c.ph);
      const tDraw = T - c.drawStart;
      for (let i = 0; i < c.n; i++) {
        c.vis[i] = project(dx + c.ox[i], dy + c.oy[i], dz + c.oz[i]) ? 1 : 0;
        if (!c.vis[i]) continue;
        c.sx[i] = oX; c.sy[i] = oY; c.sz[i] = oZ;
        c.sa[i] = smooth(FADE_NEAR0, FADE_NEAR1, oZ) * (1 - smooth(FADE_FAR0, FADE_FAR1, oD));
        c.coc[i] = Math.min(26, dof.a * Math.abs(oZ - dof.zf) / oZ);
        // 连上这颗星的那一笔落成时,星点亮一下
        let flash = 0;
        if (i > 0 && tDraw > 0) {
          const k = (i - 1);
          const done = tDraw - (k * 0.2 + 0.4);
          if (done > 0 && done < 0.6) flash = Math.sin((done / 0.6) * Math.PI) * 0.9;
        }
        drawStar(c.r0[i] * (1 + flash * 0.4), c.tone[i], Math.min(1, 0.9 * tw * starExpo * (1 + flash)), 2.6, 26, alertK);
      }
      if (edgeVis <= 0.01) continue;
      const pulseAge = T - c.pulseT0;
      const nEdges = c.edges.length / 2;
      for (let k = 0; k < nEdges; k++) {
        const a = c.edges[k * 2], b = c.edges[k * 2 + 1];
        if (!c.vis[a] || !c.vis[b]) continue;
        let p = 1;
        if (tDraw < k * 0.2 + 0.45) p = easeOut(clamp((tDraw - k * 0.2) / 0.45, 0, 1));
        if (p <= 0) continue;
        const ax = c.sx[a], ay = c.sy[a];
        const bx = ax + (c.sx[b] - ax) * p, by = ay + (c.sy[b] - ay) * p;
        const coc = (c.coc[a] + c.coc[b]) * 0.5;
        let boost = 1;
        if (pulseAge >= 0 && pulseAge < 2) {
          const q = (pulseAge - k * 0.11) / 0.34;
          if (q > -0.2 && q < 1.6) boost += 2.4 * Math.exp(-((q - 0.5) ** 2) * 5);
        }
        const al = 0.3 * Math.min(c.sa[a], c.sa[b]) * edgeVis * expo * boost / (1 + coc * 0.45);
        emit(ax, ay, bx, by, 0.55 + coc * 0.42, 0.9, 0, EDGE_RGB[0], EDGE_RGB[1], EDGE_RGB[2], al);
        // 脉冲彗星头:沿这条边跑过
        if (pulseAge >= 0 && pulseAge < 2) {
          const q = (pulseAge - k * 0.11) / 0.34;
          if (q > 0 && q < 1) {
            const hx = ax + (c.sx[b] - ax) * q, hy = ay + (c.sy[b] - ay) * q;
            const t0 = Math.max(0, q - 0.32);
            const tx = ax + (c.sx[b] - ax) * t0, ty = ay + (c.sy[b] - ay) * t0;
            const fadeQ = Math.sin(q * Math.PI);
            emit(tx, ty, hx, hy, 1.15 + coc * 0.3, 3.2, 0, PULSE_RGB[0], PULSE_RGB[1], PULSE_RGB[2],
              0.8 * fadeQ * Math.min(c.sa[a], c.sa[b]) * expo / (1 + coc * 0.3));
          }
        }
      }
    }

    if (nSprites > 0) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.useProgram(spriteProg.p);
      gl.uniform2f(spriteProg.u.uView, W, H);
      gl.bindBuffer(gl.ARRAY_BUFFER, vbuf);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, verts.subarray(0, nSprites * 4 * FLOATS));
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibuf);
      const stride = FLOATS * 4;
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);
      gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, stride, 8);
      gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 4, gl.FLOAT, false, stride, 16);
      gl.enableVertexAttribArray(3); gl.vertexAttribPointer(3, 4, gl.FLOAT, false, stride, 32);
      gl.drawElements(gl.TRIANGLES, nSprites * 6, gl.UNSIGNED_SHORT, 0);
    }
  }

  // 是否处在需要满帧的过场里(空闲时降到半帧率,星海缓移看不出差别)
  function busy(now) {
    if (arrive || warp || shake) return true;
    if (now - lastInputAt < 12000) return true;
    if (Math.abs(dof.a - (veil ? DOF_VEIL.a : DOF_IDLE.a)) > 0.05) return true;
    if (T < 4.5) return true;
    return clusters.some((c) => T - c.pulseT0 < 2);
  }

  function tick(now) {
    if (lost) return;
    rafId = requestAnimationFrame(tick);
    const raw = last == null ? 16.7 : now - last;
    if (!busy(now) && (frameNo & 1) && raw < 30) { frameNo++; return; }
    last = now;
    const dt = Math.min(0.05, raw / 1000);
    // 画质自适应:前 140 帧的平均帧间隔明显超标(软件渲染 / 弱显卡)→ 降到 1× 像素、尘埃减半,只做一次
    if (!perfDone && frameNo > 20) {
      perfSum += raw; perfN++;
      if (perfN >= 120) {
        perfDone = true;
        if (perfSum / perfN > 24) { dprCap = 1; dustStride = 2; resize(); }
      }
    }
    frame(dt);
  }

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    dpr = Math.min(window.devicePixelRatio || 1, dprCap);
    // 超大屏封顶像素量:渲染倍率可低于 1(CSS 显示尺寸不变),下限 0.5
    if (W * H * dpr * dpr > 7.2e6) dpr = Math.max(0.5, Math.sqrt(7.2e6 / (W * H)));
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
    const prevF = F;
    F = clamp(Math.max(W, H) * 0.5, 380, 1100);
    if (glow.tx < 0) { glow.x = W * 0.74; glow.y = H * 0.5; glow.w = W * 0.36; glow.h = H * 0.44; }
    if (!dust || Math.abs(prevF - F) > 120) populate();
  }

  function start() {
    if (running) return;
    running = true; last = undefined;
    rafId = requestAnimationFrame(tick);
  }

  const onMove = (e) => {
    const now = performance.now();
    lastInputAt = now;
    const touch = e.pointerType !== 'mouse';
    // 触屏:手指没按着时不会有 pointermove;按下才起算(onDown),两次轻触之间的跳变不注入;只认第一根手指
    if (touch && (!cam.has || e.pointerId !== cam.pid)) return;
    if (cam.has && !warp) {
      const dx = e.clientX - cam.lx, dy = e.clientY - cam.ly;
      cam.yawV = clamp(cam.yawV + dx * KYAW * (touch ? 1.6 : 1), -YAWMAX, YAWMAX);
      cam.vz = clamp(cam.vz - dy * KDOLLY, -VZMAX, Math.max(VZMAX, cam.vz)); // 上移(dy<0)→推进
    }
    cam.lx = e.clientX; cam.ly = e.clientY; cam.has = true;
    if (touch) return;
    look.tx = (e.clientX / Math.max(1, W) - 0.5) * 0.05;
    look.ty = (e.clientY / Math.max(1, H) - 0.5) * 0.035;
  };
  const onDown = (e) => {
    if (e.pointerType === 'mouse' || (cam.has && cam.pid !== e.pointerId)) return;
    cam.lx = e.clientX; cam.ly = e.clientY; cam.has = true; cam.pid = e.pointerId;
    lastInputAt = performance.now();
  };
  const onUp = (e) => {
    if (e.pointerType !== 'mouse' && e.pointerId === cam.pid) { cam.has = false; cam.pid = -1; }
  };
  const onLeave = () => { cam.has = false; };
  const onLost = (e) => { e.preventDefault(); lost = true; running = false; cancelAnimationFrame(rafId); };
  const onRestored = () => {
    lost = false;
    try { initGL(); resize(); start(); } catch { /* 恢复失败:停在最后一帧 */ }
  };

  try {
    initGL();
  } catch {
    // 着色器 / 资源初始化失败:放弃 WebGL。同一画布已持有 webgl 上下文(取不到 2D),回退画在调用方的回退画布上
    releaseGL();
    return mountFallback(canvas, fallbackCanvas, true);
  }
  resize();
  window.addEventListener('resize', resize);
  window.addEventListener('pointermove', onMove, { passive: true });
  window.addEventListener('pointerdown', onDown, { passive: true });
  window.addEventListener('pointerup', onUp, { passive: true });
  window.addEventListener('pointercancel', onUp, { passive: true });
  window.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('webglcontextlost', onLost);
  canvas.addEventListener('webglcontextrestored', onRestored);
  start();

  return {
    live: true,
    cleanup() {
      running = false;
      cancelAnimationFrame(rafId);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      window.removeEventListener('pointerleave', onLeave);
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      releaseGL();
      // 画布真正离开文档才释放上下文(StrictMode 开发期会在同一画布上卸载再挂载,不能提前弄丢)
      setTimeout(() => {
        if (canvas.isConnected) return;
        const ext = gl.getExtension('WEBGL_lose_context');
        if (ext) ext.loseContext();
      }, 0);
    },
    // 密码聚焦:景深拉开,整片星空化成柔焦光斑,星座连线隐去
    setVeil(on) {
      veil = !!on;
      lastInputAt = performance.now();
    },
    // 键入:一道信号脉冲沿某个视野内星座的连线跑过
    ping() {
      lastInputAt = performance.now();
      const candidates = [];
      for (let ci = 0; ci < clusters.length; ci++) {
        const c = clusters[ci];
        if (T - c.pulseT0 < 1.1 || T < c.drawStart + 0.6) continue;
        let seen = 0;
        for (let i = 0; i < c.n; i++) {
          if (c.vis[i] && c.sx[i] > 0 && c.sx[i] < W && c.sy[i] > 0 && c.sy[i] < H && c.sz[i] > 220) seen++;
        }
        if (seen >= Math.min(3, c.n)) candidates.push(c);
      }
      if (!candidates.length) return;
      candidates[Math.floor(Math.random() * candidates.length)].pulseT0 = T;
    },
    // 登录失败:镜头被撞偏——低频回摆 + 同向残余冲量(震定后望向新方向),琥珀星短暂闪红
    kick() {
      const dir = Math.random() < 0.5 ? -1 : 1;
      shake = { t: 0, dur: 0.85, amp: 0.07, freq: 1.6, dir };
      cam.yawV += dir * 1.5;
      lastInputAt = performance.now();
    },
    // 登录成功:跃迁离场(组件侧按时序叠幕布、完成切换)
    startWarp() {
      arrive = null;
      veil = false;
      shake = null;
      cam.has = false;
      warp = { t: 0, v: Math.max(cam.vz, 320) };
    },
    // 登录卡位置:背景靛辉跟着卡走
    setAnchor(x, y, w, h) {
      glow.tx = x; glow.ty = y;
      glow.tw = Math.max(W * 0.3, w * 0.95);
      glow.th = Math.max(H * 0.36, h * 0.85);
      if (frameNo === 0) { glow.x = glow.tx; glow.y = glow.ty; glow.w = glow.tw; glow.h = glow.th; }
    },
  };
}
