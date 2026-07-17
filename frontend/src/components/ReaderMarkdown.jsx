import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

// react-markdown 默认不渲染原始 HTML（无 rehype-raw），无 XSS 风险
// remark-math + KaTeX:渲染正文里的 $...$/$$...$$ LaTeX(学术型源如 Lil'Log 公式密集;
// 提取侧忠实保留 TeX 源码,渲染在此收口)。单 $ 行内数学有内建启发式
// (开 $ 后与闭 $ 前不允许空格),金额串「$100M and $2B」不会误判;
// 若未来仍现误判,收紧口子是给 remarkMath 传 { singleDollarTextMath: false }。
const MARKDOWN_PLUGINS = [remarkGfm, remarkBreaks, remarkMath];
const REHYPE_PLUGINS = [rehypeKatex];

// 灯箱开关注入：img 组件是模块级常量（避免 react-markdown 每次渲染重解析），
// 故用 Context 把「放大」回调下传给 MarkdownImage，而非重建 components 表。
const LightboxContext = createContext(null);

// 正文图：外链直连加载，图床/代理已评估后明确不做（生产由各用户 IP 分散直连）。
// 这里只兜底裂图——源站删图/防盗链时给出体面占位，而非浏览器默认破图标。不重试、不代理。
function MarkdownImage({ node, alt, ...props }) {
  const [failed, setFailed] = useState(false);
  const openLightbox = useContext(LightboxContext);
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
      onClick={(e) => openLightbox?.(props.src, alt || '', e.currentTarget)}
      aria-label={alt ? `放大图片：${alt}` : '放大图片'}
    >
      <img
        {...props}
        alt={alt || ''}
        loading="eager"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </button>
  );
}

const MARKDOWN_COMPONENTS = {
  img: MarkdownImage,
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
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
export default function ReaderMarkdown({ children }) {
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
    <LightboxContext.Provider value={openLightbox}>
      <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={MARKDOWN_COMPONENTS}>
        {children || ''}
      </ReactMarkdown>
      {lightbox ? (
        <ImageLightbox src={lightbox.src} alt={lightbox.alt} onClose={closeLightbox} />
      ) : null}
    </LightboxContext.Provider>
  );
}
