import { useState } from 'react';
import { LOGO_PATHS, LOGO_SRC_SET } from '../config';
import { LOGO_SIZES, companyLogoUrl } from '../sourceTaxonomy';

const LOCAL_GLYPH_MARKS = new Set(['openclaw']);

function BrandGlyph({ mark }) {
  if (mark === 'openclaw') {
    return (
      <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <path d="M16 9.4c4.3 0 7.1 3.1 7.1 7.5 0 4.7-3 8.1-7.1 8.1s-7.1-3.4-7.1-8.1c0-4.4 2.8-7.5 7.1-7.5Z" fill="#F97316" />
        <path d="M10.3 12.1 5.7 8.8c-1.5 1.9-1.3 4 .5 5.2 1.2.8 2.5.8 3.8.2l.3-2.1Zm11.4 0 4.6-3.3c1.5 1.9 1.3 4-.5 5.2-1.2.8-2.5.8-3.8.2l-.3-2.1Z" fill="#FB923C" />
        <path d="M12 8.9 9.3 5.5M20 8.9l2.7-3.4" stroke="#C2410C" strokeWidth="2" strokeLinecap="round" />
        <path d="M13.2 16.1h.1m5.4 0h.1" stroke="#7C2D12" strokeWidth="2.4" strokeLinecap="round" />
        <path d="M12.4 20.5c1.9 1.1 5.3 1.1 7.2 0" stroke="#FFF7ED" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }

  return null;
}

/**
 * LogoMark — 公司品牌标识。
 * 优先渲染本地缓存的官网 favicon；其他主体使用远程 favicon，加载失败时回退到主题色字母徽标。
 * size: 'xs' | 'sm' | 'md' | 'lg'
 */
export default function LogoMark({ company, size = 'md', emoji, className = '' }) {
  const [imgFailed, setImgFailed] = useState(false);
  const dims = LOGO_SIZES[size] || LOGO_SIZES.md;
  const localLogoPath = imgFailed ? '' : company?.logoPath;
  const url = imgFailed ? '' : companyLogoUrl(company);
  const accent = company?.accent || '#64748b';
  const style = {
    width: dims.box,
    height: dims.box,
    borderRadius: dims.radius,
    '--logo-accent': accent,
  };
  const fallback = company?.monogram || emoji || (company?.name || '?').slice(0, 2);
  const hasLocalGlyph = LOCAL_GLYPH_MARKS.has(company?.mark);

  if (localLogoPath) {
    return (
      <span className={`logo-mark logo-mark-img ${className}`} style={style} title={company?.name}>
        <span className="logo-mark-img-fallback" style={{ fontSize: dims.font }}>{fallback}</span>
        <img
          src={localLogoPath}
          alt={company?.name || ''}
          width={dims.img}
          height={dims.img}
          decoding="async"
          loading="eager"
          onError={() => setImgFailed(true)}
        />
      </span>
    );
  }

  // 哆啦美自有源（如 AI 资讯日报）：直接用品牌 logo 图，与 App 左上角主标识一致。
  if (company?.mark === 'dorami') {
    return (
      <span className={`logo-mark logo-mark-dorami-img ${className}`} style={style} title={company?.name}>
        <img
          src={LOGO_PATHS[128]}
          srcSet={LOGO_SRC_SET}
          sizes={`${dims.box}px`}
          alt={company?.name || '哆啦美'}
          width={dims.img}
          height={dims.img}
          loading="lazy"
        />
      </span>
    );
  }

  if (hasLocalGlyph) {
    return (
      <span className={`logo-mark logo-mark-custom logo-mark-${company.mark} ${className}`} style={{ ...style, fontSize: dims.font }} title={company?.name}>
        <BrandGlyph mark={company.mark} />
      </span>
    );
  }

  if (url) {
    return (
      <span className={`logo-mark logo-mark-img ${className}`} style={style}>
        <span className="logo-mark-img-fallback" style={{ fontSize: dims.font }}>{fallback}</span>
        <img
          src={url}
          alt={company?.name || ''}
          width={dims.img}
          height={dims.img}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      </span>
    );
  }

  return (
    <span className={`logo-mark logo-mark-mono ${className}`} style={{ ...style, fontSize: dims.font }} title={company?.name}>
      {fallback}
    </span>
  );
}
