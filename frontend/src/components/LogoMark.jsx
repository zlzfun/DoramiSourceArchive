import { useState } from 'react';
import { LOGO_SIZES, companyLogoUrl } from '../sourceTaxonomy';

function BrandGlyph({ mark }) {
  if (mark === 'anthropic') {
    return (
      <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <path d="M9.2 25.5 15.3 6h1.5l6.1 19.5h-3.8l-1.2-4.2h-5.8l-1.2 4.2H9.2Z" fill="currentColor" />
        <path d="M13.1 18.1h3.8L15 11.4l-1.9 6.7Z" fill="rgba(255,255,255,.72)" />
      </svg>
    );
  }

  if (mark === 'google') {
    return (
      <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <path d="M26.6 16.3c0-.9-.1-1.7-.2-2.5H16v4.7h6a5.2 5.2 0 0 1-2.2 3.3v3.1h3.6c2.1-1.9 3.2-4.8 3.2-8.6Z" fill="#4285F4" />
        <path d="M16 27c3 0 5.6-1 7.4-2.6l-3.6-3.1c-1 .7-2.3 1.1-3.8 1.1-2.9 0-5.3-1.9-6.2-4.5H6.1v3.2A11 11 0 0 0 16 27Z" fill="#34A853" />
        <path d="M9.8 17.9a6.7 6.7 0 0 1 0-3.8v-3.2H6.1a11 11 0 0 0 0 10.2l3.7-3.2Z" fill="#FBBC05" />
        <path d="M16 9.6c1.7 0 3.2.6 4.4 1.7l3.2-3.2A10.8 10.8 0 0 0 16 5a11 11 0 0 0-9.9 5.9l3.7 3.2c.9-2.6 3.3-4.5 6.2-4.5Z" fill="#EA4335" />
      </svg>
    );
  }

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
 * 优先渲染本地维护的关键品牌标识；其他主体使用 favicon，加载失败时回退到主题色字母徽标。
 * size: 'xs' | 'sm' | 'md' | 'lg'
 */
export default function LogoMark({ company, size = 'md', emoji, className = '' }) {
  const [imgFailed, setImgFailed] = useState(false);
  const dims = LOGO_SIZES[size] || LOGO_SIZES.md;
  const url = imgFailed ? '' : companyLogoUrl(company);
  const accent = company?.accent || '#64748b';
  const style = {
    width: dims.box,
    height: dims.box,
    borderRadius: dims.radius,
    '--logo-accent': accent,
  };
  const fallback = company?.monogram || emoji || (company?.name || '?').slice(0, 2);

  if (company?.mark) {
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
