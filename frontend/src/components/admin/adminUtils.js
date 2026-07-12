// 运维看板的纯工具与常量（与组件分离，满足 react-refresh 只导出组件的约束）。

// KPI 彩色数字体系已退役（运维波·仪表墙）：KPI 数字全 ink，语义色只留异常。

// 向量化率健康度 → KPI 数字语义色变体（这是真正需要语义色的指标，是「数字全 ink」的例外）：
// ≥80% 中性达标（无 tone）、40~80% 琥珀待补（is-warn）、<40% 偏红告警（is-bad）。
export function vectorizedRateClass(rate) {
  const r = Number(rate || 0);
  if (r >= 0.8) return '';
  if (r >= 0.4) return 'is-warn';
  return 'is-bad';
}

// 用途标签：与后端 AiUsageRecord.purpose 对齐。
export const PURPOSE_LABELS = {
  translate: '阅读器翻译',
  ask: '阅读器问答',
  daily_brief_map: '日报·概括',
  daily_brief_dedup: '日报·去重',
  daily_brief_reduce: '日报·汇编',
  source_config: '节点·配置',
  detail_profile: '节点·详情',
};

// 时间戳格式化：把 ISO 时间显示为「MM-DD HH:mm」，无值回落到占位符。
export function formatStamp(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 大数字缩写：1234 → 1.2k，1234567 → 1.2M。
export function fmtNum(n) {
  const v = Number(n || 0);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return String(v);
}

// 比率（0~1）→ 百分比整数。
export function pct(x) {
  return `${Math.round(Number(x || 0) * 100)}%`;
}

// Y 轴类目截断：避免长名撑爆图表（仅 RankBars 的活跃用户 Top 仍用）。
export const truncLabel = (s) => (typeof s === 'string' && s.length > 7 ? `${s.slice(0, 6)}…` : s);
