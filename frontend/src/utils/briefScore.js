// 早报条目「显示哪个分、分是多少」(issue #178 第三波;原在 briefGrid.js,issue #184 早报改夜航版式后
// 分值驱动的卡片网格规划器退役,只留这两个分数助手)。纯函数,不读 DOM。

const VALUE_LANES = new Set(['read', 'listen']);

// 条目上显示哪个分(issue #178 第三波):快照写明 score_dimension;「深度阅读」「播客精选」两区与任何分区的播客是内容价值,
// 其余是新闻价值。没有该键的历史快照:播客回落内容价值(第二波起播客分即内容价值),其余新闻价值。
export function scoreDimensionOf(item) {
  const snapshot = item?.snapshot || {};
  if (snapshot.score_dimension === 'content' || snapshot.score_dimension === 'news') return snapshot.score_dimension;
  if (VALUE_LANES.has(item?.selection_lane) || snapshot.content_type === 'podcast_episode') return 'content';
  return 'news';
}

const toScore = (raw) => {
  if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return null;
  const number = Number(raw);
  return Number.isFinite(number) ? number : null;
};

// 缺分是「缺席」而不是 0 分(与 utils/analysis.qualityScoreText 同口径):排序殿后、不当头条、不画数字。
// 取条目上显示的那个分:内容价值维度读快照 content_value(没有该键的历史播客快照回落播客分 quality_score)。
export function scoreOf(item) {
  if (scoreDimensionOf(item) === 'content') {
    const value = toScore(item?.snapshot?.content_value);
    if (value != null || item?.snapshot?.content_value === null) return value;
  }
  return toScore(item?.quality_score ?? item?.snapshot?.quality_score);
}
