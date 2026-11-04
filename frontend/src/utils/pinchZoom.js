// 灯箱双指缩放的几何(issue #230 M-40):图片按 translate(tx, ty) scale(s) 变换,变换原点在图片自身中心 c。
// 屏幕点 = c + t + s · p(p 是图片局部坐标,以中心为原点)。
//
// 双指:起手时记下两指中点 m0 落在图片上的局部点 p = (m0 − c − t0) / s0;移动中保持这一点跟着当前中点 m 走,
// t = m − c − (s / s0) · (m0 − c − t0),所以缩放围着两指之间发生、两指一起挪就是平移。
// 单指且已放大:平移。松手后 s 回落到 [1, MAX];s 接近 1 时归位;平移量夹在「图片边缘不离开视口太多」的范围里。
// 纯函数,不碰 DOM;ReaderMarkdown 的灯箱调用,test/layerPrimitives.test.mjs 核对。
export const PINCH = Object.freeze({ min: 1, max: 4, snap: 1.04 });

export const clampScale = (s) => Math.min(PINCH.max, Math.max(PINCH.min, s));

export const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
export const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

// start = { s0, t0: {x,y}, m0: {x,y}, d0 },center = 图片中心(屏幕坐标,未变换时)
export function pinchTransform(start, center, mid, dist) {
  const s = clampScale(start.s0 * (dist / Math.max(1, start.d0)));
  const k = s / start.s0;
  return {
    s,
    tx: mid.x - center.x - k * (start.m0.x - center.x - start.t0.x),
    ty: mid.y - center.y - k * (start.m0.y - center.y - start.t0.y),
  };
}

// 松手归位:s 接近 1 时整体归零;否则把平移量夹在 ±(s − 1)·尺寸 / 2 之内(放大后的图边不越过原图边)
export function settleTransform({ s, tx, ty }, size) {
  if (s <= PINCH.snap) return { s: 1, tx: 0, ty: 0 };
  const limX = ((s - 1) * size.width) / 2;
  const limY = ((s - 1) * size.height) / 2;
  return {
    s,
    tx: Math.min(limX, Math.max(-limX, tx)),
    ty: Math.min(limY, Math.max(-limY, ty)),
  };
}
