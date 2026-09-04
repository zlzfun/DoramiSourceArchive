import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { mediaProxyUrl } from '../api';

// react-markdown 默认不渲染原始 HTML（无 rehype-raw），无 XSS 风险
// remark-math + KaTeX:渲染正文里的 $...$ / $$...$$ LaTeX(学术型源如 Lil'Log
// 公式密集，提取侧忠实保留 TeX 源码,渲染在此收口)。商业文章里的金额区间
// 「$350M ... $1B」会被 remark-math 把两个货币符号误配成一段行内公式；在 AST
// 里只把“带 M/B/亿等货币量级开头、闭合 $ 后紧跟第二个数字金额”的误配还原
// 为文本，正常的 $E=mc^2$、$2 + 2 = 4$ 与块级公式继续交给 KaTeX；要求明确
// 量级也避免把 `$2 + 2 = 4$ 2026` 这种数字公式 + 年份误判为金额区间。
function remarkCurrencyRangeGuard() {
  const currencyStart = /^\$\s*\d[\d,.]*\s?(?:[KMBT]\b|million\b|billion\b|trillion\b|万|亿)/i;
  const secondAmount = /^\s*\d[\d,.]*(?:\s?(?:K|M|B|T|million|billion|trillion|万|亿))?\b/i;

  return (tree, file) => {
    const source = String(file.value || '');
    const visit = (node) => {
      if (!Array.isArray(node?.children)) return;
      node.children = node.children.map((child) => {
        if (child.type === 'inlineMath') {
          const start = child.position?.start?.offset;
          const end = child.position?.end?.offset;
          if (Number.isInteger(start) && Number.isInteger(end)) {
            const raw = source.slice(start, end);
            if (currencyStart.test(raw) && secondAmount.test(source.slice(end))) {
              return { type: 'text', value: raw };
            }
          }
        }
        visit(child);
        return child;
      });
    };
    visit(tree);
  };
}

const MARKDOWN_PLUGINS = [remarkGfm, remarkBreaks, remarkMath, remarkCurrencyRangeGuard];
const REHYPE_PLUGINS = [rehypeKatex];

// 灯箱开关注入：img 组件是模块级常量（避免 react-markdown 每次渲染重解析），
// 故用 Context 把「放大」回调下传给 MarkdownImage，而非重建 components 表。
const LightboxContext = createContext(null);

// 图片取图路径注入（同为 Context，理由同上）：默认走登录面的媒体库代理;
// 公开分享页(SharedArticlePage)传入指向 /api/public/share/{token}/media 的 builder——
// 访客没有会话,打默认代理只会吃 401 再回退直连,防盗链源(qbitai/mmbiz)直连又 403,整页裂图。
const ImageSrcContext = createContext(mediaProxyUrl);

// 正文图（图床波 v3.11 推翻早前「外链直连、不代理」决策）：统一经后端媒体库代理取图
// （命中本地缓存回文件；未命中后端即时下载；后端失败 302 回源）。代理自身加载失败时
// 前端再回退原链直连一次，仍失败才落裂图占位——三层降级保证可用性只增不减。
function MarkdownImage({ node, alt, ...props }) {
  const resolveSrc = useContext(ImageSrcContext);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // 初始走代理（取图路径由 ImageSrcContext 决定）；onError 回退原链（fallback=true 后不再重试）
  const [src, setSrc] = useState(() => resolveSrc(props.src));
  const openLightbox = useContext(LightboxContext);
  // 缓存命中的图片可能在 onLoad 绑定前就 complete,挂载时兜底检查
  const imgRef = useCallback((el) => {
    if (el && el.complete && el.naturalWidth > 0) setLoaded(true);
  }, []);
  const handleError = useCallback(() => {
    setSrc((current) => {
      if (current !== props.src && props.src) return props.src; // 代理失败→原链直连
      setFailed(true);
      return current;
    });
  }, [props.src]);
  if (failed) {
    // 裂图态不加点击放大：没有可展示的原图
    return (
      <span className="markdown-img-fallback" role="img" aria-label={alt || '图片加载失败'}>
        <span className="micro-label">图片加载失败</span>
        {alt ? <span className="markdown-img-fallback-alt">{alt}</span> : null}
      </span>
    );
  }
  // 图片包在真正的 <button> 里：原生可聚焦 + 自带全局焦点环，比 role=button 的 div 更规范。
  // 阅读窗格只展示一篇文章，正文图即时加载（不用 lazy，避免滚动时「现拉现出」）。
  return (
    <button
      type="button"
      className="markdown-img-button"
      // e.currentTarget（button 本体）作触发元素传出，关闭灯箱后焦点归还到它
      onClick={(e) => openLightbox?.(src, alt || '', e.currentTarget)}
      aria-label={alt ? `放大图片：${alt}` : '放大图片'}
    >
      <img
        {...props}
        src={src}
        ref={imgRef}
        alt={alt || ''}
        loading="eager"
        decoding="async"
        referrerPolicy="no-referrer"
        className={loaded ? 'is-loaded' : ''}
        onLoad={() => setLoaded(true)}
        onError={handleError}
      />
    </button>
  );
}

// 行内引用注入（AI 问答 v3.32）：回答文本里的 [n] 标记在面板侧被预转换为
// `[n](#dorami-cite-n)` 链接，这里把命中该形状的链接渲染成可点的引用 chip
// （Context 注入回调，理由同图片：components 表是模块级常量）。普通正文里
// 不会出现这种 href，未注入 citations 时命中也只降级为纯文本序号。
const CitationContext = createContext(null);
const CITE_HREF_RE = /^#dorami-cite-(\d{1,2})$/;

function MarkdownAnchor({ node, href, children, ...props }) {
  const citations = useContext(CitationContext);
  const match = CITE_HREF_RE.exec(href || '');
  if (match) {
    const num = Number(match[1]);
    if (citations) {
      return (
        <button
          type="button"
          className="reader-ai-cite"
          title={citations.titleFor?.(num) || undefined}
          onClick={() => citations.onCite?.(num)}
        >
          {num}
        </button>
      );
    }
    return <span>[{num}]</span>;
  }
  return <a href={href} {...props} target="_blank" rel="noreferrer">{children}</a>;
}

const MARKDOWN_COMPONENTS = {
  img: MarkdownImage,
  a: MarkdownAnchor,
};

// 图片灯箱：全屏深色遮罩居中放大原图；点任意处或 Esc 关闭。挂到 document.body
// （portal）以避开阅读器内可能的 transform 祖先成为 fixed 包含块的坑。
function ImageLightbox({ src, alt, onClose }) {
  useEffect(() => {
    // 仅在打开期间监听 Esc，关闭时清理
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // 无内部交互元素，Esc + 焦点归还即可，不做完整焦点陷阱
  return createPortal(
    <div
      className="reader-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={alt || '图片预览'}
      onClick={onClose}
    >
      {/* 点图片本身也关闭（cursor: zoom-out），故无需 stopPropagation */}
      <img className="reader-lightbox-img" src={src} alt={alt || ''} referrerPolicy="no-referrer" />
    </div>,
    document.body,
  );
}

// 阅读器统一 Markdown 渲染：正文、译文、AI 问答回答共用同一套插件/组件（图片兜底、外链新窗、点击放大）。
// resolveImageSrc（可选）：图片取图路径 builder（url → 请求地址），缺省为登录面媒体库代理；
// 传入方需保证引用稳定（useCallback），否则每次渲染都会打散图片组件的 state。
// citations（可选，AI 问答）：{ titleFor(n) → 标题, onCite(n) } —— 激活行内引用 chip 渲染；
// 同样要求引用稳定（useMemo/useCallback）。
export default function ReaderMarkdown({ children, resolveImageSrc, citations }) {
  const [lightbox, setLightbox] = useState(null); // { src, alt } | null
  const triggerRef = useRef(null); // 触发放大的 button，关闭后焦点归还

  const openLightbox = useCallback((src, alt, triggerEl) => {
    if (!src) return;
    triggerRef.current = triggerEl || null;
    setLightbox({ src, alt });
  }, []);

  const closeLightbox = useCallback(() => {
    setLightbox(null);
    const el = triggerRef.current;
    // 等 DOM 卸载灯箱后再归还焦点，避免争抢
    if (el) requestAnimationFrame(() => el.focus());
  }, []);

  return (
    <ImageSrcContext.Provider value={resolveImageSrc || mediaProxyUrl}>
      <CitationContext.Provider value={citations || null}>
        <LightboxContext.Provider value={openLightbox}>
          <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={MARKDOWN_COMPONENTS}>
            {children || ''}
          </ReactMarkdown>
          {lightbox ? (
            <ImageLightbox src={lightbox.src} alt={lightbox.alt} onClose={closeLightbox} />
          ) : null}
        </LightboxContext.Provider>
      </CitationContext.Provider>
    </ImageSrcContext.Provider>
  );
}
