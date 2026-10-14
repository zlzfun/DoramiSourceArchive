// 分析轮询 / 详情回读时,把新到的分析投影合并进已加载的条目(自 hooks/useReaderState 抽出,便于测试)。
export const ANALYSIS_PROJECTION_KEYS = [
  'analysis_status',
  'tagging_status',
  'analysis_has_result',
  'analysis_next_attempt_at',
  'quality_score',
  'score_reason',
  // 双评分(issue #178 第二波):内容价值与主分随分析落地变化
  'news_value',
  'news_value_reason',
  'content_value',
  'content_value_reason',
  'primary_dimension',
  'primary_score',
  'analysis_basis',
  'analysis_input_hash',
  'transcript_artifact_id',
  'prompt_version',
  'scoring_version',
  'summary_zh',
  'content_genre',
  'content_genre_label',
  // 内容类型 / 事件类型随分析落地变化(体裁到了才知道是教程还是观点),须随轮询刷新
  'kind',
  'kind_label',
  'kind_basis',
  'event_type',
  'event_label',
  'primary_tag',
  'tags',
  'display_tags',
  'podcast',
  // 兴趣标注随分析轮询刷新:pending 篇拿到标签后,命中胶囊不必等整列重载(codex 检视 P2)
  'interest_hits',
];

export function withFreshAnalysis(article, incoming) {
  if (!article || !incoming) return article;
  const next = { ...article };
  for (const key of ANALYSIS_PROJECTION_KEYS) next[key] = incoming[key];
  return next;
}
