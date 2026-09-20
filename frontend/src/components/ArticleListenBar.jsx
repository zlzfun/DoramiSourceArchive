import { useEffect, useRef, useState } from 'react';
import { formatPodcastDuration } from '../utils/podcast';

/**
 * 文章精简旁白播放条：仅在音频就绪时展示。
 * 结构对齐播客 `.podcast-audio-track`（顶部分隔线 + 标签行 + 原生 audio），不另开边框卡。
 * 点播/生成中入口收在 AiReadingCard 内，不挂在这里。
 */
export default function ArticleListenBar({ article }) {
  const guide = article?.listen_guide && typeof article.listen_guide === 'object'
    ? article.listen_guide
    : null;
  const ready = Boolean(guide?.audio_ready && guide?.audio_url);
  const [audioError, setAudioError] = useState('');
  const audioRef = useRef(null);

  useEffect(() => {
    setAudioError('');
  }, [article?.id, guide?.audio_url]);

  if (!ready) return null;

  const duration = formatPodcastDuration(guide?.duration_seconds);

  return (
    <div className="article-listen-track" data-status="ready">
      <div className="article-listen-track-head">
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
  );
}
