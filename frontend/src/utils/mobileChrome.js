// 手机壳镀层随滚动的状态(issue #230 阶段 3 第 2 批;样页 10 / 11)与「设置在这里」一次性提示(拍板 30)的纯逻辑。
// 组件侧在 components/mobile/MobileReader.jsx:列表 / 早报 / 榜单 / 订阅页滚动时写壳上的 data-scrolled / data-receded。

export const CHROME = Object.freeze({
  scrolledAt: 4,   // 滚过 4px:页头换镀层底
  recedeAt: 140,   // 越过 140px 且向下 > 6px/次:底栏退场;向上 > 6px 或回到 140 以内即回来
  delta: 6,
});

/**
 * 下一帧的镀层状态。prev = { receded },input = { y: 当前 scrollTop, dy: 与上一次的差, reduced: 减少动效,
 * intent: 这次滚动是读者自己滚的(近一秒内有滚轮 / 触摸 / 按键)}。
 * 减少动效时底栏常显、不退场(样页动效清单);页头的镀层底照常随滚动出现(不是动效)。
 * 程序性滚动(回到列表时恢复位置、换期回顶)不让底栏退场——只有读者往下读才收;叫回来不看来源。
 */
export function nextChrome(prev, { y, dy = 0, reduced = false, intent = true }) {
  const scrolled = y > CHROME.scrolledAt;
  let receded = Boolean(prev?.receded);
  if (reduced) receded = false;
  else if (intent && y > CHROME.recedeAt && dy > CHROME.delta) receded = true;
  else if (dy < -CHROME.delta || y < CHROME.recedeAt) receded = false;
  return { scrolled, receded };
}

// ── 老读者迁移提示(拍板 30):底栏「我的」撤掉、设置进头像后,第一次进来在头像下提示一次「设置在这里」 ──
// 只记本机一次性标记(不加服务端字段);存储不可用(隐私模式拒写 / 抛错)时不提示——宁可不提示,也不每次都提示。
export const SETTINGS_HINT_KEY = 'dorami-m-settings-hint';

/** window.localStorage 本身在部分浏览器(禁用站点数据)里一读就抛,取不到时返回 null。 */
export function localStore() {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function settingsHintPending(storage) {
  try {
    if (!storage) return false;
    return storage.getItem(SETTINGS_HINT_KEY) !== 'seen';
  } catch {
    return false;
  }
}

export function markSettingsHintSeen(storage) {
  try {
    storage?.setItem(SETTINGS_HINT_KEY, 'seen');
  } catch {
    /* 存储不可用:本次会话里已收起,下次不再追 */
  }
}
