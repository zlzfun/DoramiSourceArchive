// TTS 状态的展示投影已并入 utils/podcastProcessing.js(issue #76 归一层);本文件只剩强制 TTS 命令组装。
export const PODCAST_FORCE_TTS_REASON = '管理员手动强制生成 TTS';

export function podcastPremiumTtsIdempotencyKey() {
  const randomId = globalThis.crypto?.randomUUID?.();
  if (randomId) return `podcast-tts-${randomId}`;
  return `podcast-tts-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export function podcastPremiumTtsCommand(episodeId, idempotencyKey = '') {
  return {
    path: `/admin/podcast-premium-guides/${encodeURIComponent(episodeId)}/force`,
    body: {
      reason: PODCAST_FORCE_TTS_REASON,
      idempotency_key: idempotencyKey || podcastPremiumTtsIdempotencyKey(),
    },
  };
}
