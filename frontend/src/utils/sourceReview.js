// 源审查三项(信号 / 噪声 / 稳定性)的显示口径:检视器只显示中文档位,目录里的原始取值不直接露出。

export const SIGNAL_LABELS = { high_signal: '高信号', medium_signal: '中信号', low_signal: '低信号' };
export const NOISE_LABELS = { low_noise: '低噪声', medium_noise: '中噪声', high_noise: '高噪声' };
export const RELIABILITY_LABELS = {
  stable_public: '稳定公开',
  official_paid_api: '官方付费 API',
  needs_auth: '需凭据',
  fragile_js_api: 'JS/API 易变',
  blocked_or_fragile: '易阻断',
  external_import_only: '仅外部导入',
};

// v3.48 之前的 Podcast 目录使用短枚举。新写入已与文章节点统一,展示层继续接住旧库,
// 避免要求管理员重新导入目录才能看到中文审查结果。
const LEGACY_VALUES = {
  signal_strength: { high: 'high_signal', medium: 'medium_signal', low: 'low_signal' },
  noise_risk: { low: 'low_noise', medium: 'medium_noise', high: 'high_noise' },
  fetch_reliability: { high: 'stable_public', blocked: 'blocked_or_fragile' },
};

const FIELDS = [
  { key: 'signal_strength', name: '信号', labels: SIGNAL_LABELS },
  { key: 'noise_risk', name: '噪声', labels: NOISE_LABELS },
  { key: 'fetch_reliability', name: '稳定性', labels: RELIABILITY_LABELS },
];

// 目录里的取值是「档位 + 抓法后缀」(stable_public_rss / stable_public_website_category …):按档位显示;
// 认不出的原样返回(检视器那一格会截断,不撑宽右栏)。
function labelOf(labels, value) {
  if (labels[value]) return labels[value];
  const family = Object.keys(labels)
    .filter((key) => value.startsWith(`${key}_`))
    .sort((a, b) => b.length - a.length)[0];
  return family ? labels[family] : value;
}

/**
 * 一个源的审查结果:只列有值的项。`raw` 是目录里的原始取值,只在它比 `label` 多出信息时给
 * (带抓法后缀或认不出),供悬停提示用。
 */
export function reviewOf(source) {
  return FIELDS.flatMap(({ key, name, labels }) => {
    const stored = String(source?.[key] || '').trim();
    if (!stored) return [];
    const value = LEGACY_VALUES[key][stored] || stored;
    const label = labelOf(labels, value);
    return [{ key, name, label, raw: labels[value] ? '' : value }];
  });
}
