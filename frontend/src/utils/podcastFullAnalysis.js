function podcastFullAnalysisIdempotencyKey() {
  const randomId = globalThis.crypto?.randomUUID?.();
  return `podcast-full-${randomId || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

export function buildPodcastFullAnalysisRequest(idempotencyKey = '') {
  return {
    target: 'full_analysis',
    selection_override: true,
    reason: '管理员强制全文处理',
    idempotency_key: idempotencyKey || podcastFullAnalysisIdempotencyKey(),
  };
}

export function podcastFullAnalysisCommand(episodeId, podcast = {}, idempotencyKey = '') {
  const status = podcast.processing_status || podcast.status;
  const processingId = podcast.id || podcast.processing_id;
  const stage = String(podcast.stage || podcast.processing_stage || '').toLowerCase();
  const isAsr = stage === 'asr' || stage === 'fetch';
  const isAnalyze = stage === 'analyze';
  if (processingId && status === 'reconciliation_required') {
    return {
      path: `/admin/podcast-processings/${encodeURIComponent(processingId)}/reconcile`,
      body: {
        expected_attempt_count: podcast.attempt_count,
        reason: isAsr ? '管理员核对并重试 ASR 转录' : (isAnalyze ? '管理员核对并重试全文分析' : '管理员核对并重试全文处理'),
        idempotency_key: idempotencyKey || podcastFullAnalysisIdempotencyKey(),
        outcome: 'submitted',
      },
    };
  }
  if (processingId && ['failed', 'not_required', 'retry_wait'].includes(status)) {
    return {
      path: `/admin/podcast-processings/${encodeURIComponent(processingId)}/retry`,
      body: {
        expected_attempt_count: podcast.attempt_count,
        reason: isAsr ? '管理员重试 ASR 转录' : (isAnalyze ? '管理员重试全文分析' : '管理员重试全文处理'),
        idempotency_key: idempotencyKey || podcastFullAnalysisIdempotencyKey(),
      },
    };
  }
  return {
    path: `/admin/podcast-episodes/${encodeURIComponent(episodeId)}/process`,
    body: buildPodcastFullAnalysisRequest(idempotencyKey),
  };
}
