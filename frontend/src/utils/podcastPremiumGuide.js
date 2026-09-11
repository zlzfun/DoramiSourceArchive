export const PODCAST_FORCE_TTS_REASON = '管理员手动强制生成 TTS';

const TTS_STATUS_META = Object.freeze({
  not_started: { label: '未开始', tone: 'idle', active: false },
  queued: { label: '已排队', tone: 'run', active: true },
  summarizing: { label: '正在生成摘要', tone: 'run', active: true },
  synthesizing: { label: '正在合成音频', tone: 'run', active: true },
  ready: { label: '已生成', tone: 'ok', active: false },
  failed: { label: '失败', tone: 'bad', active: false },
});

export function podcastTtsStatusMeta(item = {}) {
  const status = item.tts_status || (item.audio_ready ? 'ready' : 'not_started');
  const base = TTS_STATUS_META[status] || TTS_STATUS_META.not_started;
  return {
    status,
    label: item.tts_status_label || base.label,
    tone: base.tone,
    active: base.active,
    error: item.tts_error || '',
    forced: Boolean(item.tts_forced),
  };
}

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
