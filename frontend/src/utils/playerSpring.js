// 播放条形变的弹簧缓动(issue #199)。取样算法在 utils/spring.js(issue #301 抽成共用),这里只留播放条的参数表;
// 取样函数照旧从本文件再导出,既有引用不用改。
import { SPRING_FALLBACK, linearSupported, springEasing, springSamples } from './spring.js';

export { SPRING_FALLBACK, linearSupported, springEasing, springSamples };

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
