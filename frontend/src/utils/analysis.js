const GENRE_LABELS = {
  model_release: '模型发布',
  product_update: '产品更新',
  open_source_update: '开源动态',
  research_paper: '学术论文',
  tutorial: '教程',
  opinion: '观点',
  industry_news: '行业资讯',
  conference: '技术大会',
  social_discussion: '社交讨论',
  aggregation: '资讯聚合',
  security_incident: '安全事件',
  regulation: '监管政策',
  other: '其他',
};

export function contentGenreLabel(value) {
  return GENRE_LABELS[value] || value || '';
}

export function cmsTagLabel(tag) {
  return tag?.name_zh || tag?.name_en || tag?.label || tag?.code || '';
}

export function displayAnalysisTags(article) {
  const tags = Array.isArray(article?.display_tags)
    ? article.display_tags
    : (Array.isArray(article?.tags) ? article.tags : []);
  return tags.slice(0, 6);
}

export function displayTagProps(tag) {
  const extracted = tag?.type === 'extracted';
  // 不挂 title:原生 tooltip 是一块黑框,「规范标签」说不出任何读者关心的事,
  // 长句更把内部术语漏到读者面(issue #23 验收:读者面不得出现「个性化日报」)。
  // 虚线小签可点检索的语义由 AnalysisTagChip 的 aria-label + 悬停提墨承担。
  return {
    className: `reader-tag-chip${extracted ? ' is-extracted' : ''}`,
  };
}

export function primaryAnalysisLabel(article) {
  return cmsTagLabel(article?.primary_tag) || contentGenreLabel(article?.content_genre);
}

export function qualityScoreText(value) {
  // issue #12:Number(null) === 0 会把「未分析」画成 0 分;分数只可能是 null 或 [1,10]。
  if (value == null || (typeof value === 'string' && value.trim() === '')) return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return '';
  return number.toFixed(number % 1 ? 1 : 0);
}

const TRANSCRIPT_ANALYSIS_BASES = new Set(['publisher_transcript', 'asr_transcript']);

/** 播客唯一分数的输入依据标签；不返回任何第二套分值。 */
export function podcastAssessmentMeta(article) {
  if (article?.content_type !== 'podcast_episode') return null;
  if (!qualityScoreText(article?.quality_score) && !String(article?.score_reason || '').trim()) {
    return null;
  }
  const basis = String(article?.analysis_basis || article?.podcast?.analysis_basis || '').trim();
  if (TRANSCRIPT_ANALYSIS_BASES.has(basis)) {
    return {
      label: '全文深度分析',
      note: 'AI 基于完整逐字稿分析，关键结论可回到原节目时间码核验',
    };
  }
  return {
    label: '简介初评',
    note: 'AI 基于节目简介的初步评估，尚未分析完整音频，仅用于辅助筛选',
  };
}

/** 已落库分析不依赖本部署是否配置 LLM；aiEnabled 只决定能否现场生成。 */
export function shouldShowAiReadingCard(article, { summary, aiEnabled, body } = {}) {
  return Boolean(
    summary
    || qualityScoreText(article?.quality_score)
    || String(article?.score_reason || '').trim()
    || (aiEnabled && body)
  );
}

export function hasReadableAnalysis(article) {
  if (typeof article?.analysis_has_result === 'boolean') return article.analysis_has_result;
  const machineTag = displayAnalysisTags(article).some((tag) => (
    tag?.type === 'extracted' || tag?.assignment_source === 'llm'
  ));
  return Boolean(
    qualityScoreText(article?.quality_score)
    || article?.content_genre
    || machineTag
  );
}

export function analysisNeedsPolling(article) {
  const status = article?.analysis_status;
  return status === 'pending' || status === 'running' || (
    (status === 'failed' || status === 'timeout')
    && Boolean(article?.analysis_next_attempt_at)
  );
}

export function preferredAnalysisSummary(cachedSummary, incomingSummary) {
  return cachedSummary ?? incomingSummary ?? null;
}

export function analysisItemsFromResponse(response) {
  if (Array.isArray(response)) return response;
  return Array.isArray(response?.items) ? response.items : [];
}

/**
 * Human-facing analysis state. Terminal errors stay hidden from readers by
 * default; admin surfaces opt in with includeTerminal so internal enum values
 * never leak into UI copy. A pending/running row that still carries a readable
 * result is a forced refresh and therefore says “更新中” rather than pretending
 * the old score disappeared.
 */
export function analysisStatusMeta(article, { podcast = false, includeTerminal = false } = {}) {
  const status = article?.analysis_status;
  const refreshing = hasReadableAnalysis(article);
  if (status === 'pending') {
    return {
      label: refreshing ? (podcast ? '简介更新中…' : '更新中…') : (podcast ? '简介分析中…' : '正在分析…'),
      cls: 'stamp-run',
    };
  }
  if (status === 'running') {
    return {
      label: refreshing ? (podcast ? '简介更新中…' : '更新中…') : (podcast ? '简介分析中…' : '正在分析…'),
      cls: 'stamp-run',
    };
  }
  if (!includeTerminal) return null;
  if (status === 'failed') return { label: '分析失败', cls: 'stamp-bad' };
  if (status === 'timeout') return { label: '分析超时', cls: 'stamp-warn' };
  if (status === 'skipped') return { label: '未执行分析', cls: 'stamp-idle' };
  if (!status) return { label: '尚未分析', cls: 'stamp-idle' };
  return null;
}

// 分数语义 v3.48 起是「新闻价值」(这件事有多重要),不是阅读质量;全站唯一一把尺子。
export const SCORE_DISCLAIMER = 'AI 新闻价值评估，用于辅助筛选，不代表事实保证或你的个人评分';
