import { useEffect, useRef, useState } from 'react';
import { requestArticleOndemand } from '../api';
import { formatPodcastDuration } from '../utils/podcast';

const ACTIVE = new Set(['queued', 'narrating', 'synthesizing']);
const POLL_MS = 4000;

/**
 * 文章点播精简旁白：挂在速读卡附近的轻量听入口。
 * 未就绪 →「点播听导读」；生成中 → 状态文案；就绪 → 迷你播放器。
 */
export default function ArticleListenBar({
  article,
  aiEnabled = false,
  showToast,
  onArticleRefresh,
}) {
  const guide = article?.listen_guide && typeof article.listen_guide === 'object'
    ? article.listen_guide
    : null;
  const status = String(guide?.status || '').trim().toLowerCase();
  const ready = Boolean(guide?.audio_ready && guide?.audio_url);
  const active = !ready && ACTIVE.has(status);
  const failed = status === 'failed';
  const [busy, setBusy] = useState(false);
  const [audioError, setAudioError] = useState('');
  const audioRef = useRef(null);
  const canRequest = Boolean(aiEnabled && article?.id && !ready && !active && !busy);

  useEffect(() => {
    setBusy(false);
    setAudioError('');
  }, [article?.id]);

  useEffect(() => {
    if (!active || !onArticleRefresh) return undefined;
    const timer = window.setInterval(() => {
      onArticleRefresh().catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, article?.id, onArticleRefresh]);

  const handleOndemand = async () => {
    if (!canRequest) return;
    setBusy(true);
    try {
      const result = await requestArticleOndemand(article.id);
      const outcome = String(result?.outcome || '');
      if (outcome === 'ready') {
        showToast?.('导读音频已就绪', 'success');
      } else if (outcome === 'in_progress') {
        showToast?.('导读音频正在生成中', 'info');
      } else {
        showToast?.('已开始生成导读音频', 'success');
      }
      await onArticleRefresh?.();
    } catch (error) {
      showToast?.(error?.message || '点播失败，请稍后重试', 'error');
    } finally {
      setBusy(false);
    }
  };

  if (!aiEnabled && !ready && !active && !failed) return null;

  const duration = formatPodcastDuration(guide?.duration_seconds);

  return (
    <div className="article-listen-bar" data-status={ready ? 'ready' : active ? 'active' : failed ? 'failed' : 'idle'}>
      {ready ? (
        <div className="article-listen-player">
          <div className="article-listen-player-head">
            <span className="article-listen-label">听导读</span>
            {duration && <span className="article-listen-duration">{duration}</span>}
            <span className="article-listen-ai">AI 生成</span>
          </div>
          <audio
            ref={audioRef}
            controls
            preload="metadata"
            src={guide.audio_url}
            aria-label="文章精简旁白音频"
            onCanPlay={() => setAudioError('')}
            onError={() => setAudioError('音频暂不可播放')}
          >
            你的浏览器暂不支持音频播放。
          </audio>
          {audioError && <p className="article-listen-error">{audioError}</p>}
        </div>
      ) : (
        <div className="article-listen-actions">
          {active || busy ? (
            <p className="article-listen-hint" role="status">
              {busy ? '提交中…' : '导读音频生成中…'}
            </p>
          ) : canRequest ? (
            <button
              type="button"
              className="article-listen-request"
              onClick={handleOndemand}
            >
              点播听导读
            </button>
          ) : failed ? (
            <div className="article-listen-failed">
              <p className="article-listen-hint" role="status">
                {guide?.error || '上次点播失败'}
              </p>
              {aiEnabled && (
                <button
                  type="button"
                  className="article-listen-request"
                  onClick={handleOndemand}
                  disabled={busy}
                >
                  重新点播
                </button>
              )}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
