// 播放条形变的弹簧缓动(issue #199):阻尼振子的阶跃响应采样成 CSS linear(),WAAPI 的 easing 与 CSS transition 都认。
// zeta = 阻尼比;response = 无阻尼周期(秒);dur = 动画时长(毫秒);kick = 初速度(以 ω0 为单位,0 = 静止起步)。
// 末点钉到 1:动画结束时恰好落在终态,与真实元素交接不跳。
// 不支持 linear()(Safari < 17.2 / Chrome < 113)时退回近似的三次贝塞尔——WAAPI 遇到不认识的 easing 会直接抛错。

export const SPRING_FALLBACK = 'cubic-bezier(.2,.85,.25,1)';

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

// 样页拍板的参数(global.js MOTION):形变 / 换台 / 小位移(浮钮避让、推页)。
export const PLAYER_MOTION = {
  openDur: 620,             // 宽条「先落再铺」总时长;窄条一步形变用 560
  morphDur: 560,
  miniDur: 460,
  expandDur: 500,
  swapDur: 300,
  flyDur: 420,
  closeDur: 200,
  settleDur: 420,
  tickerOutDur: 180,
  tickerDelay: 70,
  toneFadeDur: 420,
};

export function playerEasings(supported = linearSupported()) {
  const s = (o) => springEasing(o, supported);
  return {
    morph: s({ zeta: 0.84, response: 0.46, dur: PLAYER_MOTION.morphDur }),
    mini: s({ zeta: 0.86, response: 0.4, dur: PLAYER_MOTION.miniDur }),
    expand: s({ zeta: 0.84, response: 0.42, dur: PLAYER_MOTION.expandDur }),
    swap: s({ zeta: 0.9, response: 0.36, dur: PLAYER_MOTION.swapDur }),
    fly: s({ zeta: 0.9, response: 0.36, dur: PLAYER_MOTION.flyDur }),
    settle: s({ zeta: 0.9, response: 0.36, dur: PLAYER_MOTION.settleDur }),
    // 宽条两段:落(收成条高、落到中区)与铺(带初速度向两侧铺开,不在拐角停顿)
    drop: s({ zeta: 0.9, response: 0.26, dur: PLAYER_MOTION.openDur * 0.42 }),
    spread: s({ zeta: 0.92, response: 0.3, dur: PLAYER_MOTION.openDur * 0.58, kick: 0.6 }),
    out: 'cubic-bezier(.4, 0, 1, 1)',
    fade: 'cubic-bezier(.2, 0, 0, 1)',
  };
}
