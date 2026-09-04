import test from 'node:test';
import assert from 'node:assert/strict';

import {
  analysisItemsFromResponse,
  analysisNeedsPolling,
  analysisStatusMeta,
  hasReadableAnalysis,
  preferredAnalysisSummary,
  qualityScoreText,
} from './analysis.js';

test('qualityScoreText never turns missing values into a zero score', () => {
  assert.equal(qualityScoreText(null), '');
  assert.equal(qualityScoreText(undefined), '');
  assert.equal(qualityScoreText(''), '');
  assert.equal(qualityScoreText('   '), '');
  assert.equal(qualityScoreText(8), '8');
  assert.equal(qualityScoreText(8.5), '8.5');
});

test('a reader summary alone is not mistaken for a scored analysis', () => {
  assert.equal(hasReadableAnalysis({ summary_zh: '读者按需生成的摘要' }), false);
  assert.equal(hasReadableAnalysis({ one_sentence_summary: '历史残留摘要' }), false);
});

test('manual tags do not turn a first analysis into an update', () => {
  const article = {
    analysis_status: 'running',
    display_tags: [{ code: 'manual', assignment_source: 'manual' }],
  };
  assert.equal(hasReadableAnalysis(article), false);
  assert.equal(analysisStatusMeta(article).label, '正在分析…');
});

test('pending and running analysis have reader-safe labels', () => {
  assert.deepEqual(analysisStatusMeta({ analysis_status: 'pending' }), {
    label: '正在分析…',
    cls: 'stamp-run',
  });
  assert.equal(
    analysisStatusMeta({ analysis_status: 'running' }).label,
    '正在分析…',
  );
  assert.equal(
    analysisStatusMeta({ analysis_status: 'pending' }, { podcast: true }).label,
    '简介分析中…',
  );
  assert.equal(
    analysisStatusMeta({ analysis_status: 'running' }, { podcast: true }).label,
    '简介分析中…',
  );
});

test('a preserved result is shown while reanalysis says updating', () => {
  const article = {
    analysis_status: 'pending',
    analysis_has_result: true,
    quality_score: 8.4,
    display_tags: [{ code: 'agents', name_zh: '智能体' }],
  };
  assert.equal(hasReadableAnalysis(article), true);
  assert.equal(analysisStatusMeta(article).label, '更新中…');
  assert.equal(analysisStatusMeta(article, { podcast: true }).label, '简介更新中…');
});

test('retryable failures continue polling without exposing a reader error', () => {
  const retrying = {
    analysis_status: 'failed',
    analysis_next_attempt_at: '2026-09-04T10:00:00+00:00',
  };
  assert.equal(analysisNeedsPolling(retrying), true);
  assert.equal(analysisStatusMeta(retrying), null);
  assert.equal(analysisNeedsPolling({ analysis_status: 'failed' }), false);
});

test('terminal analysis failures stay hidden from ordinary readers', () => {
  for (const status of ['failed', 'timeout', 'skipped']) {
    assert.equal(analysisStatusMeta({ analysis_status: status }), null);
  }
  assert.deepEqual(
    analysisStatusMeta({ analysis_status: 'failed' }, { includeTerminal: true }),
    { label: '分析失败', cls: 'stamp-bad' },
  );
});

test('a completed analysis refreshes the reading card without replacing a session summary', () => {
  assert.equal(
    preferredAnalysisSummary(undefined, 'new analysis summary'),
    'new analysis summary',
  );
  assert.equal(
    preferredAnalysisSummary('session-generated summary', 'new analysis summary'),
    'session-generated summary',
  );
});

test('analysis polling accepts both list and envelope article responses', () => {
  const rows = [{ id: 'article-1' }];
  assert.deepEqual(analysisItemsFromResponse(rows), rows);
  assert.deepEqual(analysisItemsFromResponse({ items: rows }), rows);
  assert.deepEqual(analysisItemsFromResponse({}), []);
});
