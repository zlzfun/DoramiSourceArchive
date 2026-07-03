// 运维看板的纯工具与常量（与组件分离，满足 react-refresh 只导出组件的约束）。

// KPI 数字的语义配色：登录/AI 拉开色相（蓝 vs 紫），详情页与外层同语义同色。
export const KPI_COLOR = {
  active: 'text-emerald-600',
  login: 'text-sky-600',
  read: 'text-amber-600',
  ai: 'text-violet-600',
  subscription: 'text-teal-600',
};

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
