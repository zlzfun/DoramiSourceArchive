import { isPlayableAudioUrl } from './playbackUrl.js';

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

/**
 * 播客正文文字面板(导读稿 / 逐字稿)的挂载键:只在导读稿就绪(premium_guide.blog_ready)翻转的那一次变化。
 * 同一篇停留在本页时导读稿生成完成,面板据此重挂载、重取文字;分析结果、阅读量、premium_guide 其它字段的
 * 刷新都不改变它,逐字稿的翻页位置不会被无关刷新清掉。
 */
export function podcastBodyKey(article) {
  return `${article?.id || ''}:${podcastOf(article)?.premium_guide?.blog_ready ? 'guide' : 'notes'}`;
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

/**
 * 列表只提示可用导读或读者正在等待的生成任务，不重复「只有原节目」的默认态。
 * 音频须有实际地址，文字须 blog_ready；任务完成不等于产物可用。
 * 全文/简介评分依据与生成过程仍由详情页承接。
 */
export function podcastListAvailabilityMeta(podcast = {}) {
  if (typeof podcast === 'boolean') return podcast ? { label: '有声导读', kind: 'audio' } : null;
  const guide = podcast?.premium_guide || {};
  if (isPlayableAudioUrl(podcast?.condensed_audio_url)) return { label: '有声导读', kind: 'audio' };
  const status = String(guide.status || '').trim().toLowerCase();
  if (guide.blog_ready) {
    // 文字已可读时，音频失败/重试不掩盖阅读能力；brief_zh 也不暗示会有音频。
    return { label: '中文导读', kind: 'text' };
  }
  if (GENERATING_WITHOUT_TEXT.has(status)) {
    return { label: '导读生成中…', kind: 'generating' };
  }
  return null;
}
