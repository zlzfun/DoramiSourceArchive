import { useCallback, useState } from 'react';
import { LOGO_PATH, LOGO_PATHS, LOGO_SRC_SET } from '../config';

function sourceForSize(size) {
  if (size <= 32) return LOGO_PATHS[32];
  if (size <= 48) return LOGO_PATHS[48];
  if (size <= 128) return LOGO_PATHS[128];
  return LOGO_PATH;
}

export default function BrandLogoImage({
  alt = '哆啦美 Logo',
  className = '',
  displaySize = 48,
  loading = 'eager',
  onError,
  // 开启后：解码完成前保持透明，onLoad 时加 is-loaded 整张淡入——杜绝大图自上而下「半张」渲染。
  // 默认关，不影响其它调用点（小图标无此需求）。
  fadeInOnLoad = false,
  // 基图加载完成（含缓存命中）后触发一次；用于把非关键资源（如登录页闭眼彩蛋图）
  // 的预取排在基图之后、不与首屏争抢带宽。默认无。
  onLoaded,
}) {
  const [loaded, setLoaded] = useState(false);
  const wants = fadeInOnLoad || !!onLoaded;
  const handleLoad = useCallback(() => {
    if (fadeInOnLoad) setLoaded(true);
    onLoaded?.();
  }, [fadeInOnLoad, onLoaded]);
  // 缓存命中时 onLoad 可能早于 React 绑定，故用回调 ref 检查 complete 兜底
  const imgRef = useCallback((node) => {
    if (node && wants && node.complete && node.naturalWidth > 0) handleLoad();
  }, [wants, handleLoad]);
  const cls = `${className}${fadeInOnLoad && loaded ? ' is-loaded' : ''}`.trim();
  return (
    <img
      ref={imgRef}
      src={sourceForSize(displaySize)}
      srcSet={LOGO_SRC_SET}
      sizes={`${displaySize}px`}
      width={displaySize}
      height={displaySize}
      alt={alt}
      className={cls}
      decoding="async"
      loading={loading}
      onLoad={wants ? handleLoad : undefined}
      onError={onError}
    />
  );
}
