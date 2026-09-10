import test from 'node:test';
import assert from 'node:assert/strict';

import {
  analysisItemsFromResponse,
  analysisNeedsPolling,
  analysisStatusMeta,
  hasReadableAnalysis,
  podcastAnalysisBasis,
  podcastAssessmentMeta,
  podcastFullProcessingMeta,
  preferredAnalysisSummary,
  qualityScoreText,
  shouldShowAiReadingCard,
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

test('podcast assessment exposes one score with an honest input-basis label', () => {
  const initial = {
    content_type: 'podcast_episode',
    quality_score: 8.2,
    analysis_basis: 'podcast_show_notes',
  };
  assert.equal(podcastAssessmentMeta(initial).label, '简介初评');
  assert.match(podcastAssessmentMeta(initial).note, /尚未分析完整音频/);

  const transcript = {
    ...initial,
    analysis_basis: 'publisher_transcript',
  };
  assert.equal(podcastAssessmentMeta(transcript).label, '全文深度分析');
  assert.equal(podcastAssessmentMeta({ ...initial, quality_score: null }), null);
  assert.equal(podcastAssessmentMeta({ ...initial, content_type: 'rss_article' }), null);
});

const podcastFixture = (podcast = {}, article = {}) => ({
  content_type: 'podcast_episode',
  quality_score: 7.2,
  score_reason: '简介初评依据',
  analysis_basis: 'podcast_show_notes',
  podcast: {
    analysis_basis: 'podcast_show_notes',
    processing_status: 'not_started',
    stage: '',
    error: '',
    retryable: false,
    transcript_source: '',
    full_analysis_candidate: true,
    final_premium: null,
    ...podcast,
  },
  ...article,
});

test('podcast full-processing projection covers waiting and ASR stages', () => {
  assert.equal(
    podcastFullProcessingMeta(podcastFixture()).label,
    '等待完整逐字稿',
  );
  assert.equal(
    podcastFullProcessingMeta(podcastFixture({ processing_status: 'queued', stage: 'asr' })).label,
    'ASR 排队中…',
  );
  assert.equal(
    podcastFullProcessingMeta(podcastFixture({ processing_status: 'running', stage: 'asr' })).label,
    'ASR 转录中…',
  );
  assert.equal(
    podcastFullProcessingMeta(podcastFixture({ processing_status: 'running', stage: 'fetch' })).label,
    '正在准备音频…',
  );
});

test('podcast full-processing projection distinguishes transcript-ready and analysis-running', () => {
  const transcriptReady = podcastFullProcessingMeta(podcastFixture({
    processing_status: 'queued',
    stage: 'analyze',
    transcript_source: 'asr_transcript',
  }));
  assert.equal(transcriptReady.label, '转录完成，等待全文分析');
  assert.match(transcriptReady.detail, /ASR 逐字稿/);

  const analyzing = podcastFullProcessingMeta(podcastFixture({
    processing_status: 'running',
    stage: 'analyze',
    transcript_source: 'publisher_transcript',
  }));
  assert.equal(analyzing.label, '全文分析中…');
  assert.match(analyzing.detail, /发布方完整逐字稿/);
});

test('completed full analysis replaces the intro basis and reports the premium result', () => {
  const complete = podcastFixture({
    analysis_basis: 'publisher_transcript',
    processing_status: 'ready',
    stage: 'analyze',
    transcript_source: 'publisher_transcript',
    final_premium: true,
  });
  // 模拟列表与详情交错刷新：任一权威投影已是全文 basis，就不能退回简介标签。
  assert.equal(podcastAnalysisBasis(complete), 'publisher_transcript');
  assert.equal(podcastAssessmentMeta(complete).label, '全文深度分析');
  assert.equal(podcastFullProcessingMeta(complete).label, '全文评分完成');
  assert.match(podcastFullProcessingMeta(complete).detail, /当前分已替换简介初评/);

  const belowThreshold = podcastFixture({
    analysis_basis: 'asr_transcript',
    processing_status: 'ready',
    stage: 'analyze',
    transcript_source: 'asr_transcript',
    final_premium: false,
  }, {
    analysis_basis: 'asr_transcript',
    quality_score: 7.9,
    score_reason: '全文证据不足以达到优质门槛',
  });
  assert.equal(
    podcastFullProcessingMeta(belowThreshold).label,
    '全文评分未达优质门槛',
  );
  assert.equal(podcastAssessmentMeta(belowThreshold).label, '全文深度分析');
});

test('podcast full-processing failures visibly retain the retry reason', () => {
  const failed = podcastFullProcessingMeta(podcastFixture({
    processing_status: 'failed',
    stage: 'asr',
    error: 'ASR 配额已用完',
    retryable: true,
  }));
  assert.equal(failed.label, '全文处理失败');
  assert.equal(failed.tone, 'bad');
  assert.match(failed.detail, /ASR 配额已用完/);
  assert.match(failed.detail, /可重试/);

  const retryWait = podcastFullProcessingMeta(podcastFixture({
    processing_status: 'retry_wait',
    stage: 'analyze',
    error: '模型暂时不可用',
    retryable: true,
  }));
  assert.equal(retryWait.label, '全文处理等待重试');
  assert.match(retryWait.detail, /系统将重试/);

  const notRequired = podcastFullProcessingMeta(podcastFixture({
    id: 'processing-low-score',
    processing_status: 'not_required',
    full_analysis_candidate: false,
  }));
  assert.equal(notRequired.label, '暂未进入全文处理');
  assert.match(notRequired.detail, /管理员仍可强制全文处理/);
});

test('legacy podcast states are not relabelled as full processing without the additive projection', () => {
  const legacy = {
    content_type: 'podcast_episode',
    quality_score: 8,
    analysis_basis: 'podcast_show_notes',
    podcast: { processing_status: 'transcribing' },
  };
  assert.equal(podcastFullProcessingMeta(legacy), null);
});

test('prefixed draft fields remain compatible during rolling deployment', () => {
  const compatible = podcastFullProcessingMeta({
    content_type: 'podcast_episode',
    podcast: {
      processing_id: 'processing-old-projection',
      processing_status: 'running',
      processing_stage: 'analyze',
      processing_error: '',
      processing_retryable: false,
    },
  });
  assert.equal(compatible.label, '全文分析中…');
});

test('reader polling follows active full processing but stops at terminal results', () => {
  assert.equal(analysisNeedsPolling(podcastFixture()), true);
  assert.equal(analysisNeedsPolling(podcastFixture({
    processing_status: 'running',
    stage: 'analyze',
  })), true);
  assert.equal(analysisNeedsPolling(podcastFixture({
    processing_status: 'ready',
    stage: 'analyze',
    analysis_basis: 'publisher_transcript',
    final_premium: true,
  }, { analysis_basis: 'publisher_transcript' })), false);
});

test('persisted analysis card remains visible without a local LLM', () => {
  assert.equal(shouldShowAiReadingCard(
    { quality_score: 8.4, score_reason: '包含一手信息。' },
    { aiEnabled: false, body: 'show notes' },
  ), true);
  assert.equal(shouldShowAiReadingCard(
    {},
    { aiEnabled: false, body: 'show notes' },
  ), false);
  assert.equal(shouldShowAiReadingCard(
    {},
    { aiEnabled: true, body: 'show notes' },
  ), true);
});
