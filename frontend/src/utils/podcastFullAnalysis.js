import { podcastProcessingReason, podcastRetryKind } from './podcastProcessing.js';

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

// 命令组装单点:重试种类与审计 reason 都来自 utils/podcastProcessing 的同一谓词
// (issue #76 归并稿 P2 #11),台账 / 面板 / 抽屉三处只传 podcast 投影,不各自判 stage。
export function podcastFullAnalysisCommand(episodeId, podcast = {}, idempotencyKey = '') {
  const processingId = podcast.id || podcast.processing_id;
  const kind = podcastRetryKind(podcast);
  if (processingId && kind === 'reconcile') {
    return {
      path: `/admin/podcast-processings/${encodeURIComponent(processingId)}/reconcile`,
      body: {
        expected_attempt_count: podcast.attempt_count,
        reason: podcastProcessingReason(podcast),
        idempotency_key: idempotencyKey || podcastFullAnalysisIdempotencyKey(),
        outcome: 'submitted',
      },
    };
  }
  const status = String(podcast.processing_status || podcast.status || '').toLowerCase();
  if (processingId && ['failed', 'not_required', 'retry_wait'].includes(status)) {
    return {
      path: `/admin/podcast-processings/${encodeURIComponent(processingId)}/retry`,
      body: {
        expected_attempt_count: podcast.attempt_count,
        reason: podcastProcessingReason(podcast, 'retry'),
        idempotency_key: idempotencyKey || podcastFullAnalysisIdempotencyKey(),
      },
    };
  }
  return {
    path: `/admin/podcast-episodes/${encodeURIComponent(episodeId)}/process`,
    body: buildPodcastFullAnalysisRequest(idempotencyKey),
  };
}
