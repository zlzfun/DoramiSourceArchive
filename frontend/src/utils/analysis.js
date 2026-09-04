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
  return {
    className: `reader-tag-chip${extracted ? ' is-extracted' : ''}`,
    title: extracted ? 'AI 灵活标签；点击可临时检索，不参与长期兴趣和个性化日报选文' : '规范标签',
  };
}

export function primaryAnalysisLabel(article) {
  return cmsTagLabel(article?.primary_tag) || contentGenreLabel(article?.content_genre);
}

export function qualityScoreText(value) {
  // issue #12:Number(null) === 0 会把「未分析」画成 0 分;分数只可能是 null 或 [1,10]。
  if (value == null || value === '') return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return '';
  return number.toFixed(number % 1 ? 1 : 0);
}

export const SCORE_DISCLAIMER = 'AI 内容价值评估，用于辅助筛选，不代表事实保证或你的个人评分';
