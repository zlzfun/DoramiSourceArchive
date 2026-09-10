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
  if (processingId && ['failed', 'not_required', 'retry_wait'].includes(status)) {
    return {
      path: `/admin/podcast-processings/${encodeURIComponent(processingId)}/retry`,
      body: {
        expected_attempt_count: podcast.attempt_count,
        reason: '管理员重试全文处理',
        idempotency_key: idempotencyKey || podcastFullAnalysisIdempotencyKey(),
      },
    };
  }
  return {
    path: `/admin/podcast-episodes/${encodeURIComponent(episodeId)}/process`,
    body: buildPodcastFullAnalysisRequest(idempotencyKey),
  };
}
