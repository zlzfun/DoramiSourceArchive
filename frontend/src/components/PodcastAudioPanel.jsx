import { useCallback, useEffect, useRef, useState } from 'react';
import { Podcast } from 'lucide-react';
import { mediaProxyUrl } from '../api';
import { formatPodcastDuration, podcastOf } from '../utils/podcast';
import { podcastFullProcessingMeta } from '../utils/analysis';
import {
  readPodcastPosition,
  resumablePodcastPosition,
  writePodcastPosition,
} from '../utils/podcastPlayback';

const PROGRESS_SAVE_INTERVAL_MS = 3000;

export function PodcastCover({ src, className = '' }) {
  const [failedSrc, setFailedSrc] = useState('');
  if (src && failedSrc !== src) {
    return (
      <img
        className={className}
        src={mediaProxyUrl(src)}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => setFailedSrc(src)}
      />
    );
  }
  return (
    <span className={`${className} podcast-cover-fallback`} aria-hidden="true">
      <Podcast />
    </span>
  );
}

export default function PodcastAudioPanel({ article, variant, onVariantChange }) {
  const podcast = podcastOf(article);
  if (!podcast) return null;

  // A keyed player guarantees that switching episodes flushes the old progress
  // under the old episode identity before React mounts the new audio element.
  return (
    <PodcastAudioPlayer
      key={article?.id}
      article={article}
      podcast={podcast}
      variant={variant}
      onVariantChange={onVariantChange}
    />
  );
}

function PodcastAudioPlayer({ article, podcast, variant: controlledVariant, onVariantChange }) {
  const fullProcessing = podcastFullProcessingMeta(article);
  const hasDigest = Boolean(podcast.condensed_audio_url);
  const isFailure = fullProcessing?.tone === 'bad'
    || fullProcessing?.label === '全文处理失败'
    || fullProcessing?.label === '全文处理等待重试'
    || ['failed', 'retry_wait', 'reconciliation_required'].includes(
      String(podcast.processing_status || '').toLowerCase()
    );
  const visibleProcessing = isFailure ? null : fullProcessing;
  const status = hasDigest
    ? { label: '单人速览已就绪', tone: 'ok' }
    : (visibleProcessing || { label: '仅提供原节目', tone: 'idle' });
  const originalDuration = formatPodcastDuration(podcast.duration_seconds);
  const condensedDuration = formatPodcastDuration(podcast.condensed_duration_seconds);
  const [localVariant, setLocalVariant] = useState(() => (
    podcast.audio_url ? 'original' : 'digest'
  ));
  const [audioError, setAudioError] = useState('');
  const audioRef = useRef(null);
  const lastSavedAtRef = useRef(0);
  const preferredVariant = controlledVariant ?? localVariant;
  const activeVariant = preferredVariant === 'digest' && hasDigest
    ? 'digest'
    : podcast.audio_url ? 'original' : hasDigest ? 'digest' : 'original';
  const playbackIdentityRef = useRef({ articleId: article?.id, variant: activeVariant });

  const activeTrack = activeVariant === 'digest'
    ? { label: '精品导读', duration: condensedDuration, src: podcast.condensed_audio_url, generated: true }
    : { label: '原节目', duration: originalDuration, src: podcast.audio_url, generated: false };

  useEffect(() => {
    playbackIdentityRef.current = { articleId: article?.id, variant: activeVariant };
  }, [article?.id, activeVariant]);

  const persistProgress = useCallback((force = false) => {
    const audio = audioRef.current;
    if (!audio) return;
    const now = Date.now();
    if (!force && now - lastSavedAtRef.current < PROGRESS_SAVE_INTERVAL_MS) return;
    const identity = playbackIdentityRef.current;
    if (writePodcastPosition(identity.articleId, identity.variant, audio.currentTime)) {
      lastSavedAtRef.current = now;
    }
  }, []);

  useEffect(() => () => persistProgress(true), [persistProgress]);

  const switchVariant = (nextVariant) => {
    if (nextVariant === activeVariant) return;
    persistProgress(true);
    audioRef.current?.pause();
    lastSavedAtRef.current = 0;
    setAudioError('');
    if (controlledVariant === undefined) setLocalVariant(nextVariant);
    onVariantChange?.(nextVariant);
  };

  const restoreProgress = (event) => {
    const audio = event.currentTarget;
    const saved = readPodcastPosition(article?.id, activeVariant);
    const position = resumablePodcastPosition(saved, audio.duration);
    if (position <= 0) return;
    try {
      audio.currentTime = position;
    } catch {
      // Some browsers reject a seek until metadata/ranges settle. Playback still works from zero.
    }
  };

  const failureMessage = activeVariant === 'digest'
    ? '中文精华音频加载失败，请切换到原节目或稍后重试'
    : '原节目音频加载失败，请打开节目页面收听或稍后重试';

  return (
    <section className="podcast-player-panel" aria-label="播客音频">
      <div className="podcast-player-summary">
        <PodcastCover src={podcast.image_url} className="podcast-player-cover" />
        <div className="podcast-player-copy">
          <span className="podcast-player-kicker">播客节目</span>
          <strong>{podcast.show_title || article?.title || '播客'}</strong>
          <span className={`podcast-status is-${status.tone}`}>{status.label}</span>
        </div>
      </div>
      {visibleProcessing?.detail && (
        <p className={`podcast-full-state is-${visibleProcessing.tone}`} role="status">
          {visibleProcessing.detail}
        </p>
      )}
      {hasDigest && (
        <div className="mini-seg podcast-mode-switch" role="group" aria-label="播客播放模式">
          <button
            type="button"
            className={`mini-seg-btn ${activeVariant === 'original' ? 'is-on' : ''}`}
            aria-pressed={activeVariant === 'original'}
            onClick={() => switchVariant('original')}
          >
            原节目{originalDuration ? ` · ${originalDuration}` : ''}
          </button>
          <button
            type="button"
            className={`mini-seg-btn ${activeVariant === 'digest' ? 'is-on' : ''}`}
            aria-pressed={activeVariant === 'digest'}
            onClick={() => switchVariant('digest')}
          >
            精品导读{condensedDuration ? ` · ${condensedDuration}` : ''}
          </button>
        </div>
      )}
      {activeTrack.src ? (
        <div className="podcast-audio-track">
          <div className="podcast-audio-track-head">
            <span className="podcast-audio-label">{activeTrack.label}</span>
            {activeTrack.duration && <span>{activeTrack.duration}</span>}
            {activeTrack.generated && <span className="podcast-audio-ai">AI 生成</span>}
          </div>
          <audio
            ref={audioRef}
            controls
            preload="metadata"
            src={activeTrack.src}
            aria-label={`${activeTrack.label}音频`}
            onLoadedMetadata={restoreProgress}
            onTimeUpdate={() => persistProgress(false)}
            onPause={() => persistProgress(true)}
            onEnded={() => persistProgress(true)}
            onCanPlay={() => setAudioError('')}
            onError={() => setAudioError(failureMessage)}
          >
            你的浏览器暂不支持音频播放。
          </audio>
        </div>
      ) : (
        <p className="podcast-audio-unavailable">{activeTrack.label}音频暂不可播放</p>
      )}
      {audioError && (
        <div className="podcast-audio-error" role="alert">
          <span>{audioError}</span>
          <div className="podcast-audio-error-actions">
            {activeVariant === 'digest' && podcast.audio_url && (
              <button type="button" onClick={() => switchVariant('original')}>切换到原节目</button>
            )}
            {article?.source_url && (
              <a href={article.source_url} target="_blank" rel="noreferrer">打开节目页面 ↗</a>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
