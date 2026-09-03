const READY_STATUSES = new Set(['ready', 'audio_ready', 'condensed_ready', 'completed', 'complete', 'succeeded', 'success']);
const FAILED_STATUSES = new Set(['failed', 'error']);

const STATUS_META = {
  pending: { label: '待生成精简版', tone: 'idle' },
  not_started: { label: '待生成精简版', tone: 'idle' },
  not_requested: { label: '仅提供原版', tone: 'idle' },
  original_only: { label: '仅提供原版', tone: 'idle' },
  idle: { label: '仅提供原版', tone: 'idle' },
  queued: { label: '已加入精简队列', tone: 'idle' },
  downloading: { label: '正在准备音频…', tone: 'run' },
  transcribing: { label: '正在转写…', tone: 'run' },
  summarizing: { label: '正在提炼…', tone: 'run' },
  synthesizing: { label: '正在生成精简音频…', tone: 'run' },
  processing: { label: '正在生成精简版…', tone: 'run' },
  transcript_ready: { label: '转写已完成', tone: 'run' },
  summary_ready: { label: '文字精简已完成', tone: 'run' },
  cancelled: { label: '已取消精简生成', tone: 'idle' },
  skipped: { label: '暂不生成精简版', tone: 'idle' },
  not_required: { label: '无需生成精简版', tone: 'idle' },
};

export function podcastOf(article) {
  return article?.podcast && typeof article.podcast === 'object' ? article.podcast : null;
}

export function formatPodcastDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const roundedMinutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(roundedMinutes / 60);
  const minutes = roundedMinutes % 60;
  if (hours === 0) return `${roundedMinutes} 分钟`;
  return minutes > 0 ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
}

export function podcastProcessingMeta(status, hasCondensedAudio = false) {
  const normalized = String(status || '').trim().toLowerCase();
  if (hasCondensedAudio || READY_STATUSES.has(normalized)) {
    return { label: '精简版已就绪', tone: 'ok' };
  }
  if (FAILED_STATUSES.has(normalized)) {
    return { label: '精简版生成失败', tone: 'bad' };
  }
  if (STATUS_META[normalized]) return STATUS_META[normalized];
  return normalized
    ? { label: '精简版状态待确认', tone: 'idle' }
    : { label: '仅提供原版', tone: 'idle' };
}
