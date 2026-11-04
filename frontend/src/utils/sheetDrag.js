// 底页抓手的下拖关闭(issue #230 层原语;样页拍板 33「抓手能拖、能点、读屏念得出」):
// 按下 → 位移超过 4px 才算拖(之内松手 = 点按,交给抓手按钮的 click 关层)→ 跟手(只往下,往上不动)→
// 松手时越过 96px 或末段速度 > 0.6px/ms 即关,否则回弹。拖过一次之后,那次松手合成的 click 要吞掉
// (否则「拖了一半回弹」会被随后的点击当成「点抓手关闭」)。
//
// 纯状态机,不碰 DOM:components/layer/useSheetDrag.js 把 pointer 事件喂进来、按返回值写 transform;
// test/layerPrimitives.test.mjs 用假时间走 点按 / 防抖 / 跟手 / 回弹 / 甩关 / 吞点击。
export const SHEET_DRAG = Object.freeze({
  slop: 4,          // 防抖:之内算点按
  distance: 96,     // 拖过这么远松手即关
  velocity: 0.6,    // 或末段速度超过(px/ms)
  window: 100,      // 末段速度取最近 100ms
});

export function createSheetDrag(options = {}) {
  const cfg = { ...SHEET_DRAG, ...options };
  let active = false;
  let dragging = false;
  let y0 = 0;
  let samples = [];
  let swallowClick = false;

  const offsetOf = (y) => Math.max(0, y - y0);

  return {
    start(y, t) {
      active = true;
      dragging = false;
      swallowClick = false; // 上一次拖动若没跟出 click(在别处松手),别把这次真点按吞掉
      y0 = y;
      samples = [{ y, t }];
    },
    // 返回 { dragging, offset }:dragging 为假时调用方不动面板
    move(y, t) {
      if (!active) return { dragging: false, offset: 0 };
      if (!dragging && Math.abs(y - y0) > cfg.slop) dragging = true;
      samples.push({ y, t });
      while (samples.length > 2 && t - samples[0].t > cfg.window) samples.shift();
      return { dragging, offset: dragging ? offsetOf(y) : 0 };
    },
    // 返回 { dragged, close }:dragged 为真时随后的 click 由 consumeClick() 吞掉
    end(y, t) {
      if (!active) return { dragged: false, close: false };
      active = false;
      if (!dragging) return { dragged: false, close: false };
      dragging = false;
      swallowClick = true;
      samples.push({ y, t });
      while (samples.length > 2 && t - samples[0].t > cfg.window) samples.shift();
      const first = samples[0];
      const dt = Math.max(1, t - first.t);
      const speed = (y - first.y) / dt;
      const offset = offsetOf(y);
      return { dragged: true, close: offset > cfg.distance || (offset > cfg.slop && speed > cfg.velocity) };
    },
    cancel() {
      const was = dragging;
      active = false;
      dragging = false;
      if (was) swallowClick = true;
      return { dragged: was };
    },
    // 抓手 click 里先问一句:刚拖过就吞掉这次点击(只吞一次)
    consumeClick() {
      const hit = swallowClick;
      swallowClick = false;
      return hit;
    },
    isActive: () => active,
  };
}
