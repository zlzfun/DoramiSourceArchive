import { useEffect, useRef, useState } from 'react';
import { usePlayer } from './playerContext';
import { Eq, PlayGlyph, Spark } from './PlayerGlyphs';
import {
  articleListenActive,
  articleListenEntries,
  listenEntrySpeech,
  podcastListenEntries,
} from '../../utils/listenEntries';
import {
  articleNarrationItem,
  isPlayingIntent,
  playerItemKey,
  podcastTrackItem,
} from '../../utils/playerCore';
import { podcastOf } from '../../utils/podcast';
import { requestArticleOndemand, requestPodcastOndemand } from '../../api';

const ARTICLE_POLL_MS = 4000;

/**
 * 收听入口:未播放时只有入口,没有任何播放条。文章入口住 AI 速读卡(issue #238)。
 * 播客:「▶ 收听原节目 N 分钟」是实心主钮,旁边同一个位置承接精品导读的就绪 / 点播 / 生成中;
 * 文章:「✦ 听导读」。正在播的那一期,入口是选中态 + 律动条;别的节目的入口仍是「收听」,点一下原地换台。
 * 点播的请求 / 轮询 / 能力位门控沿用旧 PodcastAudioPanel 与 AiReadingCard 的逻辑,只换了外形。
 */
export default function ListenEntries({
  article,
  podcast = false,
  aiEnabled = false,
  ondemand = {},
  sourceName = '',
  showToast,
  onRefresh,
}) {
  const { state, api } = usePlayer();
  const [submitting, setSubmitting] = useState(false);
  const articleId = article?.id;
  const [accepted, setAccepted] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const requestInFlight = useRef(false);
  const previousActive = useRef(false);
  // 父级按文章 id 挂载,异步结果仍只刷新发起请求的那篇。
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  // 文章导读在生成时短轮询刷新状态(播客的生成态由阅读器的分析轮询覆盖)
  const active = !podcast && articleListenActive(article);
  const narrating = !podcast && (active || accepted);
  useEffect(() => {
    if (previousActive.current && article?.listen_guide?.audio_ready) {
      showToast?.('导读已就绪，点「听导读」即可收听', 'success');
    }
    previousActive.current = narrating && !article?.listen_guide?.audio_ready;
  }, [narrating, article?.listen_guide?.audio_ready, showToast]);
  useEffect(() => {
    if (!narrating || !onRefresh) return undefined;
    let stopped = false;
    let running = false;
    const timer = window.setInterval(async () => {
      if (running) return;
      running = true;
      try {
        const fresh = await onRefresh(articleId);
        if (!stopped) {
          setRefreshFailed(!fresh);
          if (fresh) setAccepted(false);
        }
      } catch {
        if (!stopped) setRefreshFailed(true);
      } finally { running = false; }
    }, ARTICLE_POLL_MS);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [narrating, articleId, onRefresh]);

  const opts = {
    aiEnabled,
    ondemandEnabled: (podcast ? ondemand.podcast : ondemand.article) === true,
    submitting,
  };
  const displayedArticle = accepted && !active && !article?.listen_guide?.audio_ready
    ? { ...article, listen_guide: { status: 'queued' } } : article;
  const entries = podcast ? podcastListenEntries(article, opts) : articleListenEntries(displayedArticle, opts);
  const note = podcast ? '' : refreshFailed ? '状态更新暂时中断，正在重新连接…'
    : narrating ? '完成后可在这里收听，也可以先继续阅读'
      : article?.listen_guide?.audio_ready ? '导读已就绪'
        : article?.listen_guide?.status === 'failed'
          ? (aiEnabled && ondemand.article === true ? '导读生成未完成，请稍后重试' : '暂时无法重试，可以继续阅读')
          : '把本文要点讲给你听';
  if (!entries.length) return null;

  const request = async () => {
    if (requestInFlight.current || submitting || !articleId) return;
    requestInFlight.current = true;
    const blogReady = Boolean(podcastOf(article)?.premium_guide?.blog_ready);
    setSubmitting(true);
    try {
      const result = podcast ? await requestPodcastOndemand(articleId) : await requestArticleOndemand(articleId);
      if (!alive.current) { await onRefresh?.(articleId); return; }
      const outcome = String(result?.outcome || '');
      if (!podcast) setAccepted(true);
      if (podcast) {
        if (outcome === 'ready') showToast?.('精品导读音频已就绪', 'success');
        else if (outcome === 'in_progress') showToast?.('精品导读正在生成中', 'info');
        else showToast?.(blogReady ? '已开始生成导读音频' : '已开始生成精品导读', 'success');
      } else if (outcome === 'ready') showToast?.('导读音频已就绪', 'success');
      else if (outcome === 'in_progress') showToast?.('导读音频正在生成中', 'info');
      else showToast?.('已提交导读生成请求', 'success');
      const fresh = await onRefresh?.(articleId);
      if (alive.current) {
        setRefreshFailed(!fresh);
        if (fresh) setAccepted(false);
      }
    } catch (error) {
      // 透传后端说明(每日额度用完 / 评分未达点播要求等),不展示内部原因
      if (!alive.current) return;
      showToast?.(error?.message || '点播失败，请稍后重试', 'error');
    } finally {
      requestInFlight.current = false;
      if (alive.current) setSubmitting(false);
    }
  };

  const listen = (entry, el) => {
    const item = entry.track === 'article_narration'
      ? articleNarrationItem(article, sourceName)
      : podcastTrackItem(article, entry.track, sourceName);
    if (item) api.play(item, { originEl: el });
  };

  return (
    <div className={`listen-entries ${podcast ? 'is-podcast' : 'is-article'}`} role="group" aria-label="收听">
      {entries.map((entry) => {
        // 主钮之外的那个位置(就绪 / 点播 / 提交中 / 生成中)用同一个元素承接:状态变化时焦点不丢
        const slot = entry.track === 'original' ? 'original' : 'secondary';
        if (entry.kind === 'notice') {
          return <span key={slot} className="reader-ai-layer-note" role="status">{entry.label}</span>;
        }
        if (entry.kind === 'busy') {
          return (
            <button key={slot} type="button" className="listen-entry is-busy" aria-disabled="true" aria-label={entry.label}>
              <span className="listen-ico"><Spark breathing /></span>
              <span className="listen-label" role="status">{entry.label}</span>
            </button>
          );
        }
        if (entry.kind === 'request') {
          return (
            <button key={slot} type="button" className="listen-entry" onClick={request}>
              <span className="listen-ico"><Spark /></span>
              <span className="listen-label">{entry.label}</span>
            </button>
          );
        }
        const key = playerItemKey(articleId, entry.track);
        const current = state.item?.key === key && state.form !== 'none' && state.status !== 'error';
        const live = current && isPlayingIntent(state);
        return (
          <button
            key={slot}
            type="button"
            className={`listen-entry ${current ? 'is-current' : entry.primary ? 'is-primary' : ''}`}
            data-listen-key={key}
            aria-label={listenEntrySpeech(entry)}
            aria-pressed={live}
            onClick={(event) => listen(entry, event.currentTarget)}
          >
            <span className="listen-ico">
              {current ? <Eq live={live} ai={entry.ai} /> : entry.primary ? <PlayGlyph /> : <Spark />}
            </span>
            <span className="listen-label">{entry.label}</span>
            {entry.duration && <span className="listen-len">{entry.duration}</span>}
          </button>
        );
      })}
      {note && <span className="listen-note" role="status">{note}</span>}
    </div>
  );
}
