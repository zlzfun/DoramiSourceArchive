import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';

// react-markdown 默认不渲染原始 HTML（无 rehype-raw），无 XSS 风险
const MARKDOWN_PLUGINS = [remarkGfm, remarkBreaks];

// 正文图：外链直连加载，图床/代理已评估后明确不做（生产由各用户 IP 分散直连）。
// 这里只兜底裂图——源站删图/防盗链时给出体面占位，而非浏览器默认破图标。不重试、不代理。
function MarkdownImage({ node, alt, ...props }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="markdown-img-fallback" role="img" aria-label={alt || '图片加载失败'}>
        <span className="micro-label">图片加载失败</span>
        {alt ? <span className="markdown-img-fallback-alt">{alt}</span> : null}
      </span>
    );
  }
  // 阅读窗格只展示一篇文章，正文图即时加载（不用 lazy，避免滚动时「现拉现出」）
  return (
    <img
      {...props}
      alt={alt || ''}
      loading="eager"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

const MARKDOWN_COMPONENTS = {
  img: MarkdownImage,
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
};

// 阅读器统一 Markdown 渲染：正文、译文、AI 问答回答共用同一套插件/组件（图片兜底、外链新窗）。
export default function ReaderMarkdown({ children }) {
  return (
    <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS}>
      {children || ''}
    </ReactMarkdown>
  );
}
