/** 看板图表的共享常量与数据辅助（与组件分离，满足 react-refresh 只导出组件的约束）。 */

// 主色（沿用品牌令牌，暗色翻转）—— 单系列图表用。
export const C_PRIMARY = 'var(--dorami-blue)';
export const AXIS = 'var(--dorami-faint)';
export const GRID = 'var(--dorami-border)';

/**
 * 分类调色板(运维波,dataviz 纪律):固定序 6 槽 + 中性「其它」槽,
 * 值走 --chart-* token(亮暗两组均已过 validate_palette.js 六项校验,暗色自动翻转)。
 * 纪律:固定序取色、不循环生成第 7 色——超出 6 系一律由 pivotDaily 并入「其它」;
 * 「其它」恒为中性灰,不占彩色配额。
 */
export const CHART_SLOTS = [
  'var(--chart-1)', // indigo(品牌)
  'var(--chart-2)', // teal
  'var(--chart-3)', // amber
  'var(--chart-4)', // red
  'var(--chart-5)', // sky
  'var(--chart-6)', // violet
];
export const C_OTHER = 'var(--chart-other)';

/**
 * 色随实体不随排位:同一命名空间里,一个实体(用户名/用途名)首次出现时按固定序
 * 认领一个槽位并终身持有——时间窗切换导致的重排/增减不会重刷幸存系列的颜色。
 * 「其它」恒走中性槽。(会话级记忆,刷新页面重新分配;跨会话稳定需后端排序保证。)
 */
const slotMemory = new Map(); // namespace → Map(entity → slotIndex)
export function colorForEntity(namespace, name) {
  if (name === '其它') return C_OTHER;
  let mem = slotMemory.get(namespace);
  if (!mem) { mem = new Map(); slotMemory.set(namespace, mem); }
  if (!mem.has(name)) mem.set(name, mem.size % CHART_SLOTS.length);
  return CHART_SLOTS[mem.get(name)];
}

// 各源互动的语义色三件套(固定语义,全站一致):阅读=槽1 靛蓝,收藏=槽3 琥珀
// (与阅读器的琥珀收藏星同色呼应,2026-07-24 拍板),订阅=槽2 青绿(原走中性灰
// 「其它」槽,信息被降权——它是真实指标,该有自己的颜色;靛/青/琥珀为验证过的相邻三槽)。
export const C_READ = CHART_SLOTS[0];
export const C_FAVORITE = CHART_SLOTS[2];
export const C_SUBSCRIBE = CHART_SLOTS[1];

export const fmtNumLocale = (n) => Number(n || 0).toLocaleString();

// 短轴标签：'2026-06-22' → '6-22'
function shortDay(iso) {
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso || '');
  if (!m) return iso || '';
  return `${Number(m[1])}-${Number(m[2])}`;
}

// 近 days 天的连续日期序列 [{day:'YYYY-MM-DD', label:'M-D'}]（含今天，升序）。
export function dateRange(days) {
  const out = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    out.push({ day: iso, label: shortDay(iso) });
  }
  return out;
}

/**
 * 把「日×维度」明细透视成 recharts 多系列格式。
 * rows: [{day, [dimKey], [valueKey]}]；按 valueKey 取 Top topN 维度为系列，其余并入「其它」。
 * 返回 { data: [{day, label, <series>: number, ...}], keys: [系列名...] }，缺失日补 0。
 */
export function pivotDaily(rows, days, dimKey, valueKey, topN = 6) {
  const list = rows || [];
  // 1) 各维度总量 → 选 Top N 作为系列，其余归「其它」。
  const totals = new Map();
  for (const r of list) {
    const k = r[dimKey];
    totals.set(k, (totals.get(k) || 0) + (r[valueKey] || 0));
  }
  const ranked = [...totals.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const top = ranked.slice(0, topN).map(([k]) => k);
  const topSet = new Set(top);
  const hasOther = ranked.length > topN;
  const keys = hasOther ? [...top, '其它'] : top;

  // 2) 按日累加到所属系列。
  const byDay = new Map();
  for (const r of list) {
    if (!totals.get(r[dimKey])) continue;
    const k = topSet.has(r[dimKey]) ? r[dimKey] : (hasOther ? '其它' : null);
    if (k === null) continue;
    const slot = byDay.get(r.day) || {};
    slot[k] = (slot[k] || 0) + (r[valueKey] || 0);
    byDay.set(r.day, slot);
  }

  // 3) 按完整日期范围零填充。
  const data = dateRange(days).map(({ day, label }) => {
    const slot = byDay.get(day) || {};
    const obj = { day, label };
    for (const k of keys) obj[k] = slot[k] || 0;
    return obj;
  });
  return { data, keys };
}
