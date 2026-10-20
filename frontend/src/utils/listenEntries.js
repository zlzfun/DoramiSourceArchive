// 标题区收听入口的状态模型(issue #199 方案 C):未播放时只有入口;点了才出播放条。
// 播客:「▶ 收听原节目 N 分钟」是实心主钮,旁边同一个位置依次承接
//   ✦ 精品导读 N 分钟(音频就绪)/ ✦ 点播精品导读(可点播)/ ✦ 生成导读音频(导读稿已有、音频没有)/
//   ✦ 导读生成中…、✦ 导读音频生成中…(已提交,不让读者的请求凭空消失)。
// 文章:「✦ 听导读 N 分钟」(就绪)/「✦ 听导读」(可点播)/「✦ 导读生成中…」。
// 失败对读者不展示:回到可点播。点播关闭或部署不支持时只剩原节目。
import { formatPodcastDuration, podcastOf } from './podcast.js';

const GUIDE_ACTIVE = new Set(['queued', 'summarizing', 'synthesizing']);
const LISTEN_ACTIVE = new Set(['queued', 'narrating', 'synthesizing']);

const lower = (value) => String(value || '').trim().toLowerCase();
const isHttp = (value) => typeof value === 'string' && /^https?:\/\//i.test(value.trim());

/**
 * @returns {Array<{ id, kind: 'play'|'request'|'busy', track?, label, duration?, primary?, ai }>}
 */
export function podcastListenEntries(article, { aiEnabled = false, ondemandEnabled = false, submitting = false } = {}) {
  const podcast = podcastOf(article);
  if (!article?.id || !podcast) return [];
  const out = [];
  if (isHttp(podcast.audio_url)) {
    out.push({
      id: 'original', kind: 'play', track: 'original', label: '收听原节目',
      duration: formatPodcastDuration(podcast.duration_seconds), primary: true, ai: false,
    });
  }
  const guide = podcast.premium_guide || {};
  const textOnly = lower(guide.mode) === 'brief_zh';   // 只出中文导读文字的模式:没有音频可点播
  const blogReady = Boolean(guide.blog_ready);
  if (isHttp(podcast.condensed_audio_url)) {
    out.push({
      id: 'digest', kind: 'play', track: 'digest', label: '精品导读',
      duration: formatPodcastDuration(podcast.condensed_duration_seconds), ai: true,
    });
    return out;
  }
  if (!aiEnabled || textOnly) return out;
  if (GUIDE_ACTIVE.has(lower(guide.status))) {
    out.push({ id: 'busy', kind: 'busy', label: blogReady ? '导读音频生成中…' : '导读生成中…', ai: true });
  } else if (submitting) {
    out.push({ id: 'busy', kind: 'busy', label: '提交中…', ai: true });
  } else if (ondemandEnabled) {
    out.push({ id: 'request', kind: 'request', label: blogReady ? '生成导读音频' : '点播精品导读', ai: true });
  }
  return out;
}

export function articleListenEntries(article, { aiEnabled = false, ondemandEnabled = false, submitting = false } = {}) {
  if (!article?.id || podcastOf(article)) return [];
  const guide = article.listen_guide && typeof article.listen_guide === 'object' ? article.listen_guide : null;
  if (guide?.audio_ready && isHttp(guide.audio_url)) {
    return [{
      id: 'narration', kind: 'play', track: 'article_narration', label: '听导读',
      duration: formatPodcastDuration(guide.duration_seconds), ai: true,
    }];
  }
  // 已在生成的照样看得见(点播总闸中途关掉,读者也不该以为请求消失了)
  if (LISTEN_ACTIVE.has(lower(guide?.status))) {
    return [{ id: 'busy', kind: 'busy', label: '导读生成中…', ai: true }];
  }
  if (submitting) return [{ id: 'busy', kind: 'busy', label: '提交中…', ai: true }];
  if (aiEnabled && ondemandEnabled) {
    return [{ id: 'request', kind: 'request', label: '听导读', ai: true }];
  }
  return [];
}

/** 文章导读是否在生成中(需要短轮询刷新状态;播客的生成态已由分析轮询覆盖)。 */
export function articleListenActive(article) {
  const guide = article?.listen_guide && typeof article.listen_guide === 'object' ? article.listen_guide : null;
  if (!guide || (guide.audio_ready && isHttp(guide.audio_url))) return false;
  return LISTEN_ACTIVE.has(lower(guide.status));
}

/** 入口的可访问名:始终含可见字(语音控制按看到的字点),AI 音轨补「AI 生成」。 */
export function listenEntrySpeech(entry) {
  if (!entry) return '';
  const parts = [entry.kind === 'play' && entry.track === 'digest' ? `收听${entry.label}` : entry.label];
  if (entry.duration) parts.push(entry.duration);
  if (entry.ai && entry.kind === 'play') parts.push('AI 生成');
  return parts.join('，');
}
