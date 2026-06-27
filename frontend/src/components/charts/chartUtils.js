/** 看板图表的共享常量与数据辅助（与组件分离，满足 react-refresh 只导出组件的约束）。 */

// 主色（沿用品牌令牌，暗色翻转）—— 单系列图表用。
export const C_PRIMARY = 'var(--dorami-blue)';
export const C_LIGHT = 'var(--dorami-blue-2)';
export const AXIS = 'var(--dorami-faint)';
export const GRID = 'var(--dorami-border)';

/**
 * 多系列分类调色板：图表里不同用户/用途靠颜色区分，故跳出单一主题色、补一组
 * 中等饱和的离散色（在亮/暗底上均可辨识）。按系列索引循环取色。
 */
export const CATEGORICAL = [
  '#5b54e8', // indigo（品牌）
  '#14b8a6', // teal
  '#f59e0b', // amber
  '#ef4444', // red
  '#0ea5e9', // sky
  '#a855f7', // violet
  '#10b981', // emerald
  '#f97316', // orange
  '#ec4899', // pink
  '#64748b', // slate（兜底/其它）
];

export const seriesColor = (i) => CATEGORICAL[i % CATEGORICAL.length];

// 各源互动分组柱的语义色：阅读=品牌靛蓝，收藏=青绿（跳出靛蓝同族，明显可分）。
export const C_READ = CATEGORICAL[0];
export const C_FAVORITE = CATEGORICAL[1];

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
