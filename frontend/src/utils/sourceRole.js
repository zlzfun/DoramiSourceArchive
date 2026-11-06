/* 源 → 信息角色判定(纯函数,无依赖;sourceTaxonomy.js 转出,单测直接导入)。 */

// ⚠️ 三个 scope 集合与 sourceRoleOf 的判定序在后端有镜像
// (src/services/source_naming.py source_role,v3.35 日报权威机械层),改一处必须同步另一处。
// expert_newsletter:专家自营 newsletter(Import AI = Jack Clark 个人执笔),
// 此前因 tier1_curated 被判成「媒体」,但它没有编辑部,归「个人」才对。
const PERSONAL_SCOPES = new Set([
  'personal_commentary', 'expert_commentary', 'executive_commentary', 'expert_newsletter',
]);
// 基准评测榜(Arena 排行榜更新 / Artificial Analysis):此前 Arena 因 tier0 被判「官方」、
// AA 因 tier1 被判「媒体」——两个同类源分居两组。它们产出的是排序信号,归「榜单」。
const LEADERBOARD_SCOPES = new Set([
  'community', 'developer_community', 'research_community', 'forum',
  'ai_benchmark_platform', 'ai_benchmark_analysis',
]);
const MEDIA_SCOPES = new Set(['ai_media', 'tech_media']);

/* 源 → 信息角色 key。仅看策展元数据(provenance_tier / source_scope),不看形态。
   用户自定源(user_rss_ 前缀/user_source 标记)最先短路进「自定」组。
   没有任何策展元数据的源(只在归档里出现、未登记)与后端 DEFAULT_SOURCE_ROLE 一致
   归「媒体」——官方待遇只给身份可考的策展源(issue #262 B7:此前前端回落「官方」)。 */
export const sourceRoleOf = (source) => {
  if (source?.user_source || String(source?.source_id || '').startsWith('user_rss_')) return 'custom';
  if (source?.listing === 'unlisted') return 'collected';
  const scope = source?.source_scope || '';
  const tier = source?.provenance_tier || '';
  if (tier === 'tier2_personal_social' || PERSONAL_SCOPES.has(scope)) return 'personal';
  if (LEADERBOARD_SCOPES.has(scope)) return 'leaderboard';
  if (MEDIA_SCOPES.has(scope) || tier === 'tier1_curated') return 'media';
  return scope || tier ? 'official' : 'media';
};

