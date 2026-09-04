// 自 frontend/src/sourceTaxonomy.js 的信息角色单轴复制(判定序 自定源→个人→榜单→媒体→官方);
// 后端 services/source_naming.source_role 亦是镜像——三处 scope 集合须逐字同步。
export const SOURCE_ROLES = [
  { key: 'official', label: '官方' },
  { key: 'media', label: '媒体' },
  { key: 'personal', label: '个人' },
  { key: 'leaderboard', label: '榜单' },
  { key: 'custom', label: '自定源' },
];
const PERSONAL_SCOPES = new Set([
  'personal_commentary', 'expert_commentary', 'executive_commentary', 'expert_newsletter',
]);
const LEADERBOARD_SCOPES = new Set([
  'community', 'developer_community', 'research_community', 'forum',
  'ai_benchmark_platform', 'ai_benchmark_analysis',
]);
const MEDIA_SCOPES = new Set(['ai_media', 'tech_media']);

export const sourceRoleOf = (source) => {
  if (source?.user_source || String(source?.source_id || '').startsWith('user_rss_')) return 'custom';
  const scope = source?.source_scope || '';
  if (source?.provenance_tier === 'tier2_personal_social' || PERSONAL_SCOPES.has(scope)) return 'personal';
  if (LEADERBOARD_SCOPES.has(scope)) return 'leaderboard';
  if (MEDIA_SCOPES.has(scope) || source?.provenance_tier === 'tier1_curated') return 'media';
  return 'official';
};

// 按角色分组,组内按名称排序;空组不出现
export function groupByRole(sources) {
  const buckets = Object.fromEntries(SOURCE_ROLES.map((r) => [r.key, []]));
  sources.forEach((s) => buckets[sourceRoleOf(s)].push(s));
  return SOURCE_ROLES
    .map((r) => ({ key: r.key, label: r.label, list: buckets[r.key].sort((a, b) => String(a.name || a.source_id).localeCompare(String(b.name || b.source_id), 'zh')) }))
    .filter((g) => g.list.length);
}

export const SHAPE_LABELS = { article: '文章', podcast: '播客', bulletin: '动态', social: '社交' };
export const SHAPE_ALL_LABEL = { article: '全部文章', podcast: '全部播客', bulletin: '全部动态', social: '全部社媒' };
