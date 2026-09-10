import test from 'node:test';
import assert from 'node:assert/strict';

import { buildPodcastFullAnalysisRequest, podcastFullAnalysisCommand } from './podcastFullAnalysis.js';

test('full-analysis override request stays bounded to the five-point candidate gate', () => {
  assert.deepEqual(buildPodcastFullAnalysisRequest('force-once-123'), {
    target: 'full_analysis',
    selection_override: true,
    reason: '管理员强制全文处理',
    idempotency_key: 'force-once-123',
  });
});

test('full-analysis override creates a fresh namespaced idempotency key', () => {
  const first = buildPodcastFullAnalysisRequest();
  const second = buildPodcastFullAnalysisRequest();
  assert.match(first.idempotency_key, /^podcast-full-.{8,}$/);
  assert.match(second.idempotency_key, /^podcast-full-.{8,}$/);
  assert.notEqual(first.idempotency_key, second.idempotency_key);
});

for (const status of ['failed', 'not_required', 'retry_wait']) {
  test(`${status} full analysis retries the existing processing with its attempt count`, () => {
    assert.deepEqual(podcastFullAnalysisCommand('episode', {
      id: 'processing/old', processing_status: status, attempt_count: 3,
    }, 'retry-key-123'), {
      path: '/admin/podcast-processings/processing%2Fold/retry',
      body: {
        expected_attempt_count: 3,
        reason: '管理员重试全文处理',
        idempotency_key: 'retry-key-123',
      },
    });
  });
}

test('a first request creates full analysis and a zero-attempt failure can retry', () => {
  assert.deepEqual(podcastFullAnalysisCommand('episode/new', {}, 'create-key-123'), {
    path: '/admin/podcast-episodes/episode%2Fnew/process',
    body: buildPodcastFullAnalysisRequest('create-key-123'),
  });
  assert.equal(podcastFullAnalysisCommand('episode', {
    id: 'processing', processing_status: 'failed', attempt_count: 0,
  }).body.expected_attempt_count, 0);
});
