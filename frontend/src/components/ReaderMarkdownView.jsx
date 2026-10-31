import { createContext, useCallback, useContext, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { mediaProxyUrl } from '../api';
import { latinLang } from '../utils/readerText';
import remarkPaperLayout from '../utils/remarkPaperLayout';
import remarkArticleImages from '../utils/remarkArticleImages';
import { useLayer } from './layer/useLayer';
import { useCompactLayout } from '../hooks/useCompactLayout';
import { distance, midpoint, pinchTransform, settleTransform } from '../utils/pinchZoom';

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

const MARKDOWN_PLUGINS = [remarkGfm, remarkBreaks, remarkMath, remarkCurrencyRangeGuard, remarkPaperLayout];
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
  const labelId = useId();
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
    // 语言标记跟着名字走(Codex E1-P2-02):英文正文容器标了 lang="en",组件自己的中文文案要显式标回 zh-CN,
    // 有 alt 时名字就是 alt,按它自己的语种标
    return (
      <span className="markdown-img-fallback" role="img" aria-label={alt || '图片加载失败'} lang={alt ? (latinLang(alt) || 'zh-CN') : 'zh-CN'}>
        <span className="micro-label" lang="zh-CN">图片加载失败</span>
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
      // 名字 =「放大图片」+ alt:两段各标各的语言再用 aria-labelledby 拼起来,不继承正文容器的 lang(E1-P2-02);
      // 没有 alt 时整钮就是中文
      aria-labelledby={alt ? `${labelId}-act ${labelId}-alt` : undefined}
      aria-label={alt ? undefined : '放大图片'}
      lang={alt ? undefined : 'zh-CN'}
    >
      {alt && (
        <>
          <span id={`${labelId}-act`} lang="zh-CN" hidden>放大图片：</span>
          <span id={`${labelId}-alt`} lang={latinLang(alt) || 'zh-CN'} hidden>{alt}</span>
        </>
      )}
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
// issue #230(审计 M-40):层的行为走层原语 useLayer(kind='modal',不锁 body 滚动——与此前一致):
// Esc 经层栈、下层 inert、焦点进出,手机壳里入返回键层栈(系统返回关灯箱、不关正文页);
// 手机壳另有一枚关闭钮(读屏与 iOS 主屏幕独立窗口的出口,拍板 33);触屏双指缩放 / 放大后单指平移,
// 几何在 utils/pinchZoom.js;轻点:未放大即关闭、已放大先复位。鼠标与桌面行为不变(点任意处关)。
function ImageLightbox({ src, alt, onClose }) {
  const rootRef = useRef(null);
  const imgRef = useRef(null);
  const compact = useCompactLayout();
  useLayer({
    open: true,
    onClose,
    kind: 'modal',
    panelRef: rootRef,
    scrollLock: false,
    initialFocus: (panel, { keyboard }) => (keyboard ? panel?.querySelector('.reader-lightbox-close') : null) || panel,
  });

  // 触屏手势(只认 pointerType === 'touch';鼠标走 onClick)
  const pointers = useRef(new Map());
  const gesture = useRef(null);
  const xf = useRef({ s: 1, tx: 0, ty: 0 });
  const lastPointer = useRef('mouse');
  const apply = (animate) => {
    const img = imgRef.current;
    if (!img) return;
    img.classList.toggle('is-gesturing', !animate);
    const { s, tx, ty } = xf.current;
    img.style.transform = s === 1 && tx === 0 && ty === 0 ? '' : `translate(${tx}px, ${ty}px) scale(${s})`;
  };
  const beginPinch = () => {
    const [a, b] = Array.from(pointers.current.values());
    const rect = imgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const { s, tx, ty } = xf.current;
    gesture.current = {
      kind: 'pinch',
      start: { s0: s, t0: { x: tx, y: ty }, m0: midpoint(a, b), d0: distance(a, b) },
      // 变换原点在图片中心:变换后外框的中心 = 原中心 + 平移
      center: { x: rect.left + rect.width / 2 - tx, y: rect.top + rect.height / 2 - ty },
    };
  };
  const beginPan = (point, tap) => {
    gesture.current = { kind: tap ? 'tap' : 'pan', p0: point, t0: Date.now(), moved: false, from: { ...xf.current } };
  };
  const onCloseButton = (event) => Boolean(event.target.closest?.('.reader-lightbox-close'));
  const handlers = {
    onPointerDown: (event) => {
      lastPointer.current = event.pointerType;
      if (event.pointerType !== 'touch' || onCloseButton(event)) return;
      pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.current.size === 1) beginPan({ x: event.clientX, y: event.clientY }, true);
      else if (pointers.current.size === 2) beginPinch();
    },
    onPointerMove: (event) => {
      if (event.pointerType !== 'touch' || !pointers.current.has(event.pointerId)) return;
      pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      const g = gesture.current;
      if (!g) return;
      if (g.kind === 'pinch' && pointers.current.size >= 2) {
        const [a, b] = Array.from(pointers.current.values());
        xf.current = pinchTransform(g.start, g.center, midpoint(a, b), distance(a, b));
        apply(false);
        return;
      }
      const dx = event.clientX - g.p0.x;
      const dy = event.clientY - g.p0.y;
      if (!g.moved && Math.hypot(dx, dy) > 8) g.moved = true;
      if (g.moved && xf.current.s > 1) {
        xf.current = { s: xf.current.s, tx: g.from.tx + dx, ty: g.from.ty + dy };
        apply(false);
      }
    },
    onPointerUp: (event) => {
      if (event.pointerType !== 'touch' || !pointers.current.has(event.pointerId)) return;
      pointers.current.delete(event.pointerId);
      const g = gesture.current;
      const left = pointers.current.size;
      if (left === 1) {
        // 双指放开一指:剩下那指接着平移
        beginPan(Array.from(pointers.current.values())[0], false);
        return;
      }
      if (left > 0) return;
      gesture.current = null;
      if (g?.kind === 'tap' && !g.moved && Date.now() - g.t0 < 400) {
        if (xf.current.s > 1) { xf.current = { s: 1, tx: 0, ty: 0 }; apply(true); }
        else onClose();
        return;
      }
      const img = imgRef.current;
      xf.current = settleTransform(xf.current, { width: img?.offsetWidth || 0, height: img?.offsetHeight || 0 });
      apply(true);
    },
    onPointerCancel: (event) => {
      pointers.current.delete(event.pointerId);
      if (pointers.current.size === 0) {
        gesture.current = null;
        const img = imgRef.current;
        xf.current = settleTransform(xf.current, { width: img?.offsetWidth || 0, height: img?.offsetHeight || 0 });
        apply(true);
      }
    },
  };

  return createPortal(
    <div
      ref={rootRef}
      className="reader-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={alt || '图片预览'}
      tabIndex={-1}
      data-layer-panel=""
      {...handlers}
      // 鼠标 / 键盘:点任意处关闭(点图片本身也关闭,cursor: zoom-out);触屏由上面的手势判定,不走 click
      onClick={() => { if (lastPointer.current !== 'touch') onClose(); }}
    >
      <img ref={imgRef} className="reader-lightbox-img" src={src} alt={alt || ''} referrerPolicy="no-referrer" draggable={false} />
      {compact && (
        <button
          type="button"
          className="reader-lightbox-close"
          aria-label="关闭图片预览"
          onClick={(event) => { event.stopPropagation(); onClose(); }}
        >
          <X aria-hidden="true" />
        </button>
      )}
    </div>,
    document.body,
  );
}

// 阅读器统一 Markdown 渲染：正文、译文、AI 问答回答共用同一套插件/组件（图片兜底、外链新窗、点击放大）。
// 本文件是重实现(react-markdown / remark / rehype / KaTeX 与其样式全在这里),只经 ./ReaderMarkdown 的
// 动态 import 进来(issue #230 M-14:登录页与读者首屏不再预载 vendor-markdown,正文首开才拉)——
// 业务代码一律 import ReaderMarkdown,不直接 import 本文件(test/chunkSplit.test.mjs 守)。
// resolveImageSrc（可选）：图片取图路径 builder（url → 请求地址），缺省为登录面媒体库代理；
// 传入方需保证引用稳定（useCallback），否则每次渲染都会打散图片组件的 state。
// citations（可选，AI 问答）：{ titleFor(n) → 标题, onCite(n) } —— 激活行内引用 chip 渲染；
// 同样要求引用稳定（useMemo/useCallback）。
export default function ReaderMarkdown({ children, resolveImageSrc, citations, sourceUrl }) {
  const markdownPlugins = useMemo(
    () => [...MARKDOWN_PLUGINS, [remarkArticleImages, { sourceUrl }]],
    [sourceUrl],
  );
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
          <ReactMarkdown remarkPlugins={markdownPlugins} rehypePlugins={REHYPE_PLUGINS} components={MARKDOWN_COMPONENTS}>
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
