import test from 'node:test';
import assert from 'node:assert/strict';
import { primaryScoreOf, scoreDimensions, scoreDisclaimer } from '../src/utils/analysis.js';

const keys = (article) => scoreDimensions(article).map((d) => d.key);

test('主分在前:后端给的 primary_dimension 决定顺序', () => {
  const paper = {
    news_value: 5.2, news_value_reason: '常规论文', content_value: 7.6, content_value_reason: '方法新',
    primary_dimension: 'content', primary_score: 7.6,
  };
  assert.deepEqual(keys(paper), ['content', 'news']);
  assert.equal(primaryScoreOf(paper).text, '7.6');
  assert.equal(primaryScoreOf(paper).label, '内容价值');
  assert.equal(primaryScoreOf(paper).reason, '方法新');
  assert.deepEqual(keys({ ...paper, primary_dimension: 'news' }), ['news', 'content']);
});

test('缺失即缺席:只有一个分时只出现那一个', () => {
  assert.deepEqual(keys({ news_value: 6.1, content_value: null, primary_dimension: 'news' }), ['news']);
  // 播客(第二波甲方案):现有分数投影为内容价值,没有新闻价值
  const podcast = { news_value: null, content_value: 8.4, content_value_reason: '一手长访谈', primary_dimension: 'content' };
  assert.deepEqual(keys(podcast), ['content']);
  assert.deepEqual(keys({ news_value: null, content_value: null, primary_dimension: null }), []);
});

test('旧响应(没有双评分字段)回落 quality_score 作新闻价值', () => {
  const legacy = { quality_score: 7, score_reason: '旗舰发布' };
  const [only] = scoreDimensions(legacy);
  assert.equal(only.key, 'news');
  assert.equal(only.text, '7');
  assert.equal(only.reason, '旗舰发布');
  // 新响应里 quality_score 只是旧字段,不能被当成第三个分
  assert.deepEqual(keys({ quality_score: 8.4, news_value: null, content_value: 8.4, primary_dimension: 'content' }), ['content']);
});

test('免责小字随维度换说法', () => {
  assert.match(scoreDisclaimer('content'), /内容价值评估/);
  assert.match(scoreDisclaimer('news'), /新闻价值评估/);
  assert.match(scoreDisclaimer(undefined), /新闻价值评估/);
});

test('旧响应经轮询 / 详情合并后仍回落 quality_score(codex R1-5)', async () => {
  const { withFreshAnalysis } = await import('../src/utils/analysisProjection.js');
  const listed = { id: 'a', quality_score: 7, score_reason: '旗舰发布' };
  const refreshed = withFreshAnalysis(listed, { quality_score: 7, score_reason: '旗舰发布' });
  assert.equal('news_value' in refreshed, true);   // 合并会带出 undefined 键
  assert.deepEqual(keys(refreshed), ['news']);
  assert.equal(primaryScoreOf(refreshed).text, '7');
  // 新协议里显式 null 表示缺席,不回落旧分
  const fresh = withFreshAnalysis(listed, { quality_score: 7, news_value: null, content_value: null, primary_dimension: null });
  assert.deepEqual(keys(fresh), []);
});
