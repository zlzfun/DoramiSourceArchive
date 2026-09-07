export const PODCAST_ARTIFACT_KIND_LABELS = Object.freeze({
  source_audio: '原始音频',
  digest_audio_zh: '中文精简版',
});

export const PODCAST_ARTIFACT_STATUS_META = Object.freeze({
  ready: { label: '待发布', tone: 'warn' },
  published: { label: '已发布', tone: 'ok' },
  withdrawn: { label: '已下架', tone: 'idle' },
  expired: { label: '已过期', tone: 'idle' },
});

export const PODCAST_ARTIFACT_RETENTION_LABELS = Object.freeze({
  durable: '永久保留',
  temporary: '临时缓存',
  protected: '到期·处理中保护',
  due: '待安全回收',
  expired: '已解除引用',
});

export function podcastArtifactKindLabel(kind) {
  return PODCAST_ARTIFACT_KIND_LABELS[kind] || kind || '未知类型';
}

export function podcastArtifactStatusMeta(status, kind = '') {
  if (status === 'ready' && kind === 'source_audio') {
    return { label: '可处理', tone: 'ok' };
  }
  return PODCAST_ARTIFACT_STATUS_META[status] || { label: status || '未知状态', tone: 'idle' };
}

export function podcastArtifactRetentionLabel(value) {
  return PODCAST_ARTIFACT_RETENTION_LABELS[value] || value || '—';
}

export function formatPodcastArtifactBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const unitIndex = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / (1024 ** unitIndex);
  const digits = scaled >= 100 || unitIndex === 0 ? 0 : 1;
  return `${scaled.toFixed(digits)} ${units[unitIndex]}`;
}

export function formatPodcastArtifactTime(iso, locale = 'zh-CN') {
  if (!iso) return '—';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '—';
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}
