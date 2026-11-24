// 弹簧缓动的共用取样:阻尼振子的阶跃响应采样成 CSS linear(),WAAPI 的 easing 与 CSS transition / animation 都认。
// 起于播放条形变(issue #199,utils/playerSpring.js),issue #301 抽成共用——桌面读者面的五档弹簧、
// 播放条与手机壳的 --motion-sheet-ease 是同一种算法,只是参数的写法不同:
//   springSamples / springEasing  按「阻尼比 zeta + 无阻尼周期 response + 动画时长 dur」取样(播放条的写法,时长自己定);
//   spring                        按「感知时长 duration + 回弹 bounce」取样,落定时长由算法算出(读者面五档的写法)。
// 不支持 linear()(Safari < 17.2 / Chrome < 113)时退回近似的三次贝塞尔——WAAPI 遇到不认识的 easing 会直接抛错。

export const SPRING_FALLBACK = 'cubic-bezier(.2,.85,.25,1)';

// zeta = 阻尼比;response = 无阻尼周期(秒);dur = 动画时长(毫秒);kick = 初速度(以 ω0 为单位,0 = 静止起步)。
// 末点钉到 1:动画结束时恰好落在终态,与真实元素交接不跳。
export function springSamples({ zeta, response, dur, samples = 56, kick = 0 }) {
  const w0 = (2 * Math.PI) / response;
  const v0 = kick * w0;
  const out = [];
  for (let i = 0; i <= samples; i += 1) {
    const t = (i / samples) * (dur / 1000);
    let x;
    if (zeta >= 1) {
      // 临界阻尼:没有过冲
      x = 1 - (1 + (w0 - v0) * t) * Math.exp(-w0 * t);
    } else {
      const wd = w0 * Math.sqrt(1 - zeta * zeta);
      x = 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0 - v0) / wd) * Math.sin(wd * t));
    }
    out.push(i === samples ? 1 : Math.round(x * 10000) / 10000);
  }
  return out;
}

export function linearSupported() {
  try {
    return typeof CSS !== 'undefined' && Boolean(CSS.supports?.('animation-timing-function', 'linear(0, 0.5, 1)'));
  } catch {
    return false;
  }
}

export function springEasing(options, supported = linearSupported()) {
  if (!supported) return SPRING_FALLBACK;
  return `linear(${springSamples(options).join(', ')})`;
}

// 落定判据:偏离终值不超过 0.1% 的最后一刻。取样密度约每帧一点(16–64 点)。
const SETTLE_EPSILON = 0.001;
const SETTLE_STEP = 1 / 240;

function stepResponse(zeta, w0) {
  if (zeta >= 1) return (t) => 1 - Math.exp(-w0 * t) * (1 + w0 * t);
  const wd = w0 * Math.sqrt(1 - zeta * zeta);
  return (t) => 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0) / wd) * Math.sin(wd * t));
}

/** 「感知时长 + 回弹」的弹簧:duration(秒)= 无阻尼周期;bounce 0 = 临界阻尼,0.2 = 略有回弹。
 *  返回 { samples, duration }:duration(毫秒)是**落定时长**(不是入参),samples 是 0→1 的取样点,末点钉到 1。 */
export function springCurve({ duration = 0.4, bounce = 0 } = {}) {
  const zeta = Math.max(0.05, 1 - bounce);
  const x = stepResponse(zeta, (2 * Math.PI) / duration);
  let settle = duration;
  for (let t = duration * 5; t > 0; t -= SETTLE_STEP) {
    if (Math.abs(x(t) - 1) > SETTLE_EPSILON) { settle = t + SETTLE_STEP; break; }
  }
  const n = Math.max(16, Math.min(64, Math.round(settle * 60)));
  const samples = [];
  for (let i = 0; i <= n; i += 1) samples.push(Number(x((settle * i) / n).toFixed(4)));
  samples[n] = 1;
  return { samples, duration: Math.round(settle * 1000) };
}

const springCache = new Map();

/** WAAPI 用:{ easing, duration }。不支持 linear() 时 easing 退回 SPRING_FALLBACK,时长不变。 */
export function spring(options = {}, supported = linearSupported()) {
  const key = `${options.duration ?? 0.4}|${options.bounce ?? 0}|${supported ? 1 : 0}`;
  let hit = springCache.get(key);
  if (!hit) {
    const { samples, duration } = springCurve(options);
    hit = { easing: supported ? `linear(${samples.join(', ')})` : SPRING_FALLBACK, duration };
    springCache.set(key, hit);
  }
  return hit;
}

// 桌面读者面的五档弹簧(issue #301,conventions §7)。index.css 的 --motion-spring-<档> / -dur 是这张表取样后的结果,
// 守卫 test/motionTokens.test.mjs 逐字比对——改参数要两边一起改。
export const MOTION_SPRINGS = Object.freeze({
  snappy: Object.freeze({ duration: 0.28, bounce: 0 }),     // 松手回弹
  glide: Object.freeze({ duration: 0.32, bounce: 0.12 }),   // 列表选中块、提示弹入
  move: Object.freeze({ duration: 0.42, bounce: 0.12 }),    // 位移:筛选后的行、分数逐位滚动
  settle: Object.freeze({ duration: 0.5, bounce: 0.18 }),   // 大变形:卡面成页、到场
  pop: Object.freeze({ duration: 0.35, bounce: 0.45 }),     // 落点:星标、导轨图标、订阅钮
});

/** 取一档弹簧给 WAAPI:motionSpring('glide') → { easing, duration }。 */
export function motionSpring(name, supported = linearSupported()) {
  const def = MOTION_SPRINGS[name];
  if (!def) throw new Error(`unknown motion spring: ${name}`);
  return spring(def, supported);
}
