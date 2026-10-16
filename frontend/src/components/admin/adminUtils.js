// 运维看板的纯工具与常量（与组件分离，满足 react-refresh 只导出组件的约束）。

// KPI 彩色数字体系已退役（运维波·仪表墙）：KPI 数字全 ink，语义色只留异常。

// 用途标签：与后端 AiUsageRecord.purpose 对齐(UsageMeta(purpose=…) 的全部取值;issue #184 补齐此前漏掉的用途,
// 图例不再一半中文一半原始键)。未登记的新用途仍按原键显示。
export const PURPOSE_LABELS = {
  translate: '阅读器翻译',
  ask: '阅读器问答',
  summarize: '阅读器速读',
  article_analysis: '文章分析',
  image_insight: '图片理解',
  personal_digest_title: '早报标题',
  daily_brief_editorial: '日报·点评',
  daily_brief_dedup: '日报·去重',
  daily_brief_reduce: '日报·汇编',
  podcast_premium_blog: '播客·导读文稿',
  podcast_premium_narration: '播客·导读旁白',
  podcast_ondemand: '播客·点播',
  article_ondemand: '文章·点播',
  article_ondemand_narration: '文章·点播旁白',
  taxonomy_bootstrap: '标签·目录初始化',
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
