// 内容类型(issue #178)的读者面呈现规则。词表与判定都在后端(services/content_classification.py),
// 条目带 kind / kind_label / event_type / event_label,分面带 {kind, label, count};
// 这里只决定「怎么摆」,不再维护一份中文名表。

// 「资讯」是默认值:列表不挂类型签、筛选条不单列。
const DEFAULT_KIND = 'news';

// 版本更新用紧凑行(单行标题、无摘要),取代原「动态」容器的短条目扫读形。
const COMPACT_KINDS = new Set(['release']);

/** 条目列表上的类型签文字;资讯或尚未判定时返回空串(不挂签)。 */
export function kindChipLabel(article) {
  const kind = article?.kind;
  if (!kind || kind === DEFAULT_KIND) return '';
  return article?.kind_label || '';
}

/** 是否用紧凑行渲染。 */
export function isCompactKind(article) {
  return COMPACT_KINDS.has(article?.kind);
}

/**
 * 阅读窗眉头「源 · ___」的第二段:非资讯写内容类型(论文 / 版本更新…),
 * 资讯写事件类型(模型发布 / 行业动态…),两者都没有时留空。
 */
export function kindKickerLabel(article) {
  return kindChipLabel(article) || article?.event_label || '';
}

/**
 * 筛选条选项:分面里计数大于 0 的类型,保持后端词表顺序。当前选中的类型即使计数归零
 * (例如叠加了「未读」后)也保留,读者才看得见、点得掉它。
 */
export function kindFilterOptions(facets, selected = '') {
  if (!Array.isArray(facets)) return [];
  return facets
    .filter((facet) => facet && facet.kind && facet.kind !== DEFAULT_KIND
      && (Number(facet.count) > 0 || facet.kind === selected))
    .map((facet) => ({ kind: facet.kind, label: facet.label || facet.kind, count: Number(facet.count) }));
}
