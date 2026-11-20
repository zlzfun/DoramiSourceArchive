// 令牌交付计量(issue #312)的展示口径:接口分哪几组(名称 / 颜色 / 固定次序)、接口与令牌怎么叫、
// 逐日数据怎么透视成图。纯函数、无 React 依赖(frontend/test/deliveryChannels.test.mjs)。
// 组的划分由后端给(services/token_usage.py 的 channel_group),这里只管怎么显示。
import { CHART_SLOTS, dateRange } from '../components/charts/chartUtils.js';

// 固定次序 = 图例次序 = 堆叠次序(自下而上):色随组不随排位,时间窗一换不重刷颜色。
export const DELIVERY_GROUPS = [
  { key: 'articles', name: '文章', color: CHART_SLOTS[0] },
  { key: 'events', name: '热点事件', color: CHART_SLOTS[1] },
  { key: 'subscription', name: '单订阅', color: CHART_SLOTS[2] },
  { key: 'mcp', name: 'MCP', color: CHART_SLOTS[4] },
];

const BY_KEY = new Map(DELIVERY_GROUPS.map((g) => [g.key, g]));
const BY_NAME = new Map(DELIVERY_GROUPS.map((g) => [g.name, g]));

// 认 key 也认显示名(图表的系列名是显示名);不认识的组落到第一组的颜色,不抛错。
export function deliveryGroupColor(keyOrName) {
  return (BY_KEY.get(keyOrName) || BY_NAME.get(keyOrName) || DELIVERY_GROUPS[0]).color;
}

// 接口就叫它的真实路径(拍板 2):`feed/articles`、`subscriptions/{id}/articles`;MCP 工具写成 `mcp · search_articles`。
export function channelLabel(channel) {
  const name = String(channel || '');
  return name.startsWith('mcp:') ? `mcp · ${name.slice(4)}` : name;
}

// 一枚令牌的类型名:聚合令牌 / 订阅令牌 · <订阅名>(订阅已删、名字取不到时只写类型)。
export function tokenKindLabel(token) {
  if (token?.kind !== 'subscription') return '聚合令牌';
  const name = token?.subscription?.name;
  return name ? `订阅令牌 · ${name}` : '订阅令牌';
}

// 令牌卡第二行:最近一次调用 + 条件性的「作废后仍被调」;一次都没调过写「未调用」。
export function tokenMetaLine(token, formatStamp) {
  const parts = [];
  if (token?.calls > 0 && token?.last_at) parts.push(`最近调用 ${formatStamp(token.last_at)}`);
  if (token?.status === 'retired' && token?.rejected > 0) parts.push(`作废后仍被调 ${token.rejected} 次`);
  return parts.length ? parts.join(' · ') : '未调用';
}

// 抽屉里这一区的标题:接口调用 · 近 N 天 M 次 · K 枚令牌(没有调用不写次数,只有一枚不写枚数)。
export function deliverySectionTitle(delivery) {
  const calls = delivery?.totals?.calls || 0;
  const count = delivery?.tokens?.length || 0;
  return [
    '接口调用',
    calls > 0 ? `近 ${delivery.window_days} 天 ${calls} 次` : '',
    count > 1 ? `${count} 枚令牌` : '',
  ].filter(Boolean).join(' · ');
}

/**
 * 把「日 × 接口组」明细透视成 MultiSeriesBars 的数据集:{ data: [{day, label, <组名>: n}], keys: [组名…] }。
 * 系列只出窗口内有数的组,次序恒按 DELIVERY_GROUPS;缺失日补 0。
 */
export function pivotDeliveryDaily(rows, days, valueKey = 'calls') {
  const list = rows || [];
  const totals = new Map();
  for (const r of list) totals.set(r.group, (totals.get(r.group) || 0) + (Number(r[valueKey]) || 0));
  const groups = DELIVERY_GROUPS.filter((g) => (totals.get(g.key) || 0) > 0);
  const byDay = new Map();
  for (const r of list) {
    const group = BY_KEY.get(r.group);
    if (!group || !(totals.get(r.group) > 0)) continue;
    const slot = byDay.get(r.day) || {};
    slot[group.name] = (slot[group.name] || 0) + (Number(r[valueKey]) || 0);
    byDay.set(r.day, slot);
  }
  const data = dateRange(days).map(({ day, label }) => {
    const slot = byDay.get(day) || {};
    const row = { day, label };
    for (const g of groups) row[g.name] = slot[g.name] || 0;
    return row;
  });
  return { data, keys: groups.map((g) => g.name) };
}
