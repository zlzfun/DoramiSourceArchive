const STORAGE_PREFIX = 'dorami.podcastPlayback.v1';

function storageOrNull(storage) {
  if (storage) return storage;
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function podcastPlaybackKey(articleId, variant) {
  const id = encodeURIComponent(String(articleId || '').trim());
  const safeVariant = variant === 'digest' ? 'digest' : 'original';
  return `${STORAGE_PREFIX}:${id}:${safeVariant}`;
}

export function normalizePodcastPosition(value) {
  const position = Number(value);
  return Number.isFinite(position) && position >= 0 ? position : 0;
}

export function readPodcastPosition(articleId, variant, storage) {
  if (!articleId) return 0;
  const target = storageOrNull(storage);
  if (!target) return 0;
  try {
    return normalizePodcastPosition(target.getItem(podcastPlaybackKey(articleId, variant)));
  } catch {
    return 0;
  }
}

export function writePodcastPosition(articleId, variant, position, storage) {
  if (!articleId) return false;
  const target = storageOrNull(storage);
  const normalized = normalizePodcastPosition(position);
  if (!target || normalized !== Number(position)) return false;
  try {
    target.setItem(podcastPlaybackKey(articleId, variant), String(normalized));
    return true;
  } catch {
    return false;
  }
}

export function resumablePodcastPosition(position, duration) {
  const normalized = normalizePodcastPosition(position);
  const audioDuration = Number(duration);
  if (!Number.isFinite(audioDuration) || audioDuration <= 0) return normalized;
  return Math.min(normalized, audioDuration);
}
