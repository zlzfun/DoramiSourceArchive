import { isPlayableAudioUrl } from './playbackUrl.js';

export function isBriefPodcast(item, source) {
  return Boolean(item?.article_id && (
    item.snapshot?.content_type === 'podcast_episode'
    || source?.shape === 'podcast'
    || ['podcast', 'podcast_rss'].includes(source?.source_type)
    || item.selection_lane === 'listen'
  ));
}

export function briefGuideActive(article) {
  const podcast = article?.podcast;
  return !isPlayableAudioUrl(podcast?.condensed_audio_url)
    && ['queued', 'summarizing', 'synthesizing', 'retry_wait'].includes(podcast?.premium_guide?.status);
}

// Only a current playable URL makes the action a player; a frozen score/text/status does not.
export function briefGuideAction(article, { submitting = false, accepted = false, error = '' } = {}) {
  if (submitting) return { kind: 'busy', label: '提交中…' };
  if (error) return { kind: 'refresh', label: '重试加载导读' };
  if (isPlayableAudioUrl(article?.podcast?.condensed_audio_url)) {
    return { kind: 'play', label: '播放导读' };
  }
  if (accepted || briefGuideActive(article)) return { kind: 'busy', label: '导读音频生成中…' };
  if (!article) return { kind: 'busy', label: '正在加载导读…' };
  if (!article.podcast) return { kind: 'unavailable', label: '导读暂不可用' };
  return { kind: 'request', label: '生成导读音频' };
}
