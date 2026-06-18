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
}) {
  return (
    <img
      src={sourceForSize(displaySize)}
      srcSet={LOGO_SRC_SET}
      sizes={`${displaySize}px`}
      width={displaySize}
      height={displaySize}
      alt={alt}
      className={className}
      decoding="async"
      loading={loading}
      onError={onError}
    />
  );
}
