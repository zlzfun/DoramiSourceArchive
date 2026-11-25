// 今天的早报由系统自动换版(issue #343):新内容落地、读者改了兴趣或订阅、生成失败后重试,都不需要读者操作。
// 页面只做三件事——按后端给的 refresh_pending 决定多久再来取一次;新的一版快到时在页头说一声「正在编排」;
// 取到新的一版时,决定现在换不换(读者正往下读就先不换,回到页首或切回页面时再换),换的时候不硬切
// (动效在 utils/spatialMotion 的 leaveBrief / swapInBrief)。

export const SOON_POLL_MS = 8000;      // 在途 / 几十秒内会有新版
export const CONTENT_POLL_MS = 60000;  // 等新内容落地(上午)

const IN_FLIGHT = new Set(['pending', 'generating']);
const PUBLISHED = new Set(['ready', 'degraded']);

// 后端给的「接下来还会不会换版」。有版面时在 edition 上;空订阅的引导页没有 edition,在响应顶层
// (读者把订阅和兴趣都清空后再订回来,页面靠它知道稍后会有新版)
const pendingOf = (today) => today?.edition?.refresh_pending || today?.refresh_pending || null;

// 多久再取一次今日早报;0 = 不必再取
export function briefPollInterval(today) {
  const pending = pendingOf(today);
  if (IN_FLIGHT.has(today?.status) || pending === 'soon') return SOON_POLL_MS;
  if (pending === 'content') return CONTENT_POLL_MS;
  return 0;
}

// 失败了但系统还会自己重试:页面照「编排中」画,不画失败
export function isBriefPreparing(today) {
  if (IN_FLIGHT.has(today?.status)) return true;
  return today?.status === 'failed' && today?.edition?.refresh_pending === 'soon';
}

// 眼前是编好的一版,而新的一版几十秒内就到(读者刚改了兴趣或订阅):页头的编排时刻换成「正在编排」。
// 等新内容落地(content)可能是几个小时,不算
export function isBriefRefreshing(today) {
  return PUBLISHED.has(today?.status) && today?.edition?.refresh_pending === 'soon';
}

// 版面上画的是哪一样。后台再取回来时它变了,才是「版面换了」(换上去带动效);
// 同一版的字段更新、pending → generating 这类画法不变的变化不算
export function briefFace(today) {
  if (!today) return '';
  if (isBriefPreparing(today)) return 'preparing';
  if (PUBLISHED.has(today.status)) return `edition:${today.edition?.id ?? ''}`;
  if (today.status === 'failed') return `failed:${today.edition?.id ?? ''}`;
  return String(today.status || '');
}

// 取到的是不是「换了一版」:两边都是编好的版面,且不是同一版
export function isBriefSwap(shown, incoming) {
  const a = shown?.edition;
  const b = incoming?.edition;
  return Boolean(a && b && a.id !== b.id && PUBLISHED.has(shown?.status) && PUBLISHED.has(incoming?.status));
}
