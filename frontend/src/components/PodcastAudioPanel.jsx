import { useState } from 'react';
import { Podcast } from 'lucide-react';
import { mediaProxyUrl } from '../api';
import { formatPodcastDuration, podcastOf, podcastProcessingMeta } from '../utils/podcast';

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

function AudioTrack({ label, duration, src, generated = false }) {
  return (
    <div className="podcast-audio-track">
      <div className="podcast-audio-track-head">
        <span className="podcast-audio-label">{label}</span>
        {duration && <span>{duration}</span>}
        {generated && <span>AI 生成</span>}
      </div>
      <audio controls preload="metadata" src={src} aria-label={`${label}音频`}>
        你的浏览器暂不支持音频播放。
      </audio>
    </div>
  );
}

export default function PodcastAudioPanel({ article }) {
  const podcast = podcastOf(article);
  if (!podcast) return null;

  const status = podcastProcessingMeta(
    podcast.processing_status,
    Boolean(podcast.condensed_audio_url),
  );
  const originalDuration = formatPodcastDuration(podcast.duration_seconds);
  const condensedDuration = formatPodcastDuration(podcast.condensed_duration_seconds);

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
      {podcast.audio_url ? (
        <AudioTrack label="原版" duration={originalDuration} src={podcast.audio_url} />
      ) : (
        <p className="podcast-audio-unavailable">原版音频暂不可播放</p>
      )}
      {podcast.condensed_audio_url && (
        <AudioTrack
          label="精简版"
          duration={condensedDuration}
          src={podcast.condensed_audio_url}
          generated
        />
      )}
    </section>
  );
}
