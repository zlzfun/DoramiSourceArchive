// 早报卡长按的动作单(issue #230 批 3b,样页拍板 13 / 5):复用阅读器的文章动作单(useReaderState.buildArticleMenuItems),
// 早报条目只有编排快照,这里把它折成动作单认得的「文章」形状。
// 早报快照不带逐篇读态:「标为已读 / 未读」一项拿不准当前态,不给(呈现的诚实,conventions §4.2);其余照文章动作单。

// 早报条目 → 动作单的文章形状;没有 article_id(源已不在库里)的条目不出动作单
export function briefMenuArticle(item, source = null) {
  if (!item?.article_id) return null;
  const snapshot = item.snapshot || {};
  const podcast = source?.shape === 'podcast' || snapshot.content_type === 'podcast_episode';
  return {
    id: item.article_id,
    title: snapshot.title || '',
    title_zh: snapshot.title_zh || null,
    source_id: snapshot.source_id || '',
    source_url: snapshot.source_url || '',
    ...(podcast ? { podcast: {} } : {}),
  };
}

// 去掉读态一项;去掉后若开头 / 结尾 / 相邻出现分隔线,一并收掉
export function briefMenuItems(items) {
  const kept = (items || []).filter((item) => item.key !== 'read');
  return kept.filter((item, index) => {
    if (item.type !== 'sep') return true;
    const prev = kept[index - 1];
    const next = kept[index + 1];
    return Boolean(prev && next && prev.type !== 'sep' && next.type !== 'sep');
  });
}
