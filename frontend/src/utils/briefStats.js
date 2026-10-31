// 早报「本期编排 · 入选」的取数(issue #230 批 3b 从 PersonalBriefPage 抽出,纯函数单测):
// 一律取正在看的那一期 edition 自己的编排快照——expected_source_ids(冻结的订阅范围)、selection_stats(编排时一次写入)、
// items(入选条目)——看往期时不能混进今天的数字(样页第 9 轮回填第 12 条)。历史版本没有 selection_stats 时对应半句省略。

// 额外于新闻精选之上的通道:重大事件 + 深度阅读 / 播客精选(「N 篇精选」不数它们)
export const EXTRA_LANES = new Set(['breaking', 'read', 'listen']);
export const VALUE_LANES = new Set(['read', 'listen']);

const hitsInterest = (item) => (item.matched_interest_codes || []).length > 0;

export function briefSelectionCounts(edition) {
  const items = edition?.items || [];
  const ownItems = items.filter((item) => !EXTRA_LANES.has(item.selection_lane));
  const readCount = items.filter((item) => item.selection_lane === 'read').length;
  const listenCount = items.filter((item) => item.selection_lane === 'listen').length;
  const stats = edition?.selection_stats || null;
  const scopeCount = (edition?.expected_source_ids || []).length;
  const merged = Number(stats?.same_event_merged);
  return {
    items,
    ownItems,
    readCount,
    listenCount,
    valueCount: readCount + listenCount,
    interestCount: ownItems.filter(hitsInterest).length,
    // 订阅外命中兴趣的条数(快照事实 subscribed=false)
    externalHits: ownItems.filter((item) => hitsInterest(item) && item.snapshot?.subscribed === false).length,
    stats,
    scopeCount,
    // 订阅为空只按兴趣编排(v3.54)
    interestOnly: !!edition && scopeCount === 0 && (stats ? !!stats.interest_only : items.length > 0),
    followedCount: stats?.followed_count ?? null,
    candidateCount: stats?.candidate_count ?? null,
    // issue #103:同事件合并到代表条目的候选数
    mergedCount: merged > 0 ? merged : 0,
    ratioUnfillable: edition?.degraded_reason === 'insufficient_non_interest_content',
  };
}
