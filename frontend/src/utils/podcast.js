const READY_STATUSES = new Set(['ready', 'audio_ready', 'condensed_ready', 'completed', 'complete', 'succeeded', 'success']);
const FAILED_STATUSES = new Set(['failed', 'error']);

const STATUS_META = {
  pending: { label: '待生成精品导读', tone: 'idle' },
  not_started: { label: '待生成精品导读', tone: 'idle' },
  not_requested: { label: '仅提供原节目', tone: 'idle' },
  original_only: { label: '仅提供原节目', tone: 'idle' },
  idle: { label: '仅提供原节目', tone: 'idle' },
  queued: { label: '已加入精品导读队列', tone: 'idle' },
  downloading: { label: '正在准备音频…', tone: 'run' },
  transcribing: { label: '正在转写…', tone: 'run' },
  summarizing: { label: '正在提炼…', tone: 'run' },
  synthesizing: { label: '正在生成精品导读音频…', tone: 'run' },
  processing: { label: '正在生成精品导读…', tone: 'run' },
  transcript_ready: { label: '转写已完成', tone: 'run' },
  summary_ready: { label: '精品导读稿已完成', tone: 'run' },
  cancelled: { label: '已取消精品导读生成', tone: 'idle' },
  skipped: { label: '暂不生成精品导读', tone: 'idle' },
  not_required: { label: '无需生成精品导读', tone: 'idle' },
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
    return { label: '精品导读已就绪', tone: 'ok' };
  }
  // 读者面不展示失败。无导读文字时，失败与等待重试都回到原节目。
  if (FAILED_STATUSES.has(normalized) || normalized === 'retry_wait') {
    return { label: '仅提供原节目', tone: 'idle' };
  }
  if (STATUS_META[normalized]) return STATUS_META[normalized];
  return normalized
    ? { label: '精品导读状态待确认', tone: 'idle' }
    : { label: '仅提供原节目', tone: 'idle' };
}

const GENERATING_WITHOUT_TEXT = new Set(['queued', 'summarizing', 'synthesizing']);

/** The same audio/text/task projection is used by list and detail. */
export function podcastListAvailabilityMeta(podcast = {}) {
  if (typeof podcast === 'boolean') return podcastProcessingMeta('', podcast);
  const guide = podcast?.premium_guide || {};
  if (podcast?.condensed_audio_url) return { label: '精品导读音频已就绪', tone: 'ok' };
  const status = String(guide.status || '').toLowerCase();
  const mode = String(guide.mode || '').toLowerCase();
  if (guide.blog_ready) {
    if (mode === 'brief_zh') return { label: '中文导读已生成', tone: 'ok' };
    // 文字已可读、音频尚未就绪：排队、合成、等待重试、失败与待生成同一句，中性。
    return { label: '中文导读可阅读', tone: 'idle' };
  }
  if (GENERATING_WITHOUT_TEXT.has(status)) {
    return { label: '精品导读生成中…', tone: 'run' };
  }
  return { label: '仅提供原节目', tone: 'idle' };
}
