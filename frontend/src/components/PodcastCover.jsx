import { useState } from 'react';
import { Podcast } from 'lucide-react';
import { mediaProxyUrl } from '../api';

/** 播客封面(经媒体库代理);加载失败或没有封面时退成图标方块。条目行与播放条共用。 */
export default function PodcastCover({ src, className = '', loading = 'lazy' }) {
  const [failedSrc, setFailedSrc] = useState('');
  if (src && failedSrc !== src) {
    return (
      <img
        className={className}
        src={mediaProxyUrl(src)}
        alt=""
        loading={loading}
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
