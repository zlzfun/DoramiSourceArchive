// 早报分数助手的回归用例(issue #74 起;issue #184 网格规划器退役后只剩分数口径):
// 缺分是缺席不是 0 分;深度阅读 / 播客精选与任何分区的播客显示内容价值。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scoreDimensionOf, scoreOf } from '../src/utils/briefScore.js';

test('scoreOf:缺分是缺席不是 0 分', () => {
  assert.equal(scoreOf({ quality_score: 7.5 }), 7.5);
  assert.equal(scoreOf({ snapshot: { quality_score: '6' } }), 6);
  assert.equal(scoreOf({ quality_score: null, snapshot: { quality_score: 8 } }), 8);
  assert.equal(scoreOf({ quality_score: '' }), null);
  assert.equal(scoreOf({ quality_score: 'abc' }), null);
  assert.equal(scoreOf({}), null);
  assert.equal(scoreOf(null), null);
});

test('scoreOf / scoreDimensionOf:深度阅读 / 播客精选两区与任何分区的播客显示内容价值(issue #178 第三波)', () => {
  const read = { selection_lane: 'read', quality_score: 4.5, snapshot: { score_dimension: 'content', content_value: 8.2, quality_score: 4.5 } };
  assert.equal(scoreDimensionOf(read), 'content');
  assert.equal(scoreOf(read), 8.2);
  // 内容价值显式缺席(快照写了 null)就是缺分,不拿新闻分顶替
  assert.equal(scoreOf({ selection_lane: 'read', quality_score: 4.5, snapshot: { score_dimension: 'content', content_value: null } }), null);
  // 新闻通道照旧新闻价值
  const news = { selection_lane: 'quality', quality_score: 7.1, snapshot: { score_dimension: 'news', content_value: 8.8 } };
  assert.equal(scoreDimensionOf(news), 'news');
  assert.equal(scoreOf(news), 7.1);
  // 历史快照无 score_dimension:播客回落内容价值维度,分数取播客分
  const legacyPodcast = { selection_lane: 'quality', quality_score: 7.9, snapshot: { content_type: 'podcast_episode' } };
  assert.equal(scoreDimensionOf(legacyPodcast), 'content');
  assert.equal(scoreOf(legacyPodcast), 7.9);
  assert.equal(scoreDimensionOf({ quality_score: 6 }), 'news');
});
