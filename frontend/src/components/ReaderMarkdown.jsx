import { lazy, Suspense, useEffect, useState } from 'react';
import { loadReaderMarkdownView, loadedReaderMarkdownView } from '../utils/readerMarkdownLoader';

// 阅读器 Markdown 的轻入口(issue #230 审计 M-14):真正的渲染器 ReaderMarkdownView 连同 react-markdown /
// remark / rehype / KaTeX 与 KaTeX 样式不进登录页与读者首屏,第一次要画 Markdown(打开一篇 / 分享页 /
// 问答面板 / 播客文稿)时才动态 import,之后同步可用。
//
// - 用法与原来一样:<ReaderMarkdown {...props}>{markdown}</ReaderMarkdown>(props 原样透传给 View)。
// - 渲染器没到之前画 fallback(缺省什么都不画);要「骨架不闪」的调用方(正文容器 ArticleCopy、分享页)
//   用 useReaderMarkdownReady() 把「渲染器未就绪」并进自己的加载态——骨架还是同一个元素,不重挂、不重放显现延迟。
// - 拉取失败(断网 / 发版后旧 chunk 已下线):退回按空行分段的纯文本,内容仍可读;下一次挂载再试。
// - 挂载即开始拉取:正文容器在正文请求在途时就并行拉渲染器,两者通常同时落地。

// 模块级 lazy:只在加载器已就绪后才渲染它,此时工厂交回同步 thenable,React 当场解析、不挂起;
// Suspense 只是兜底(万一挂起,只降级这一块,不波及整页的 TabBoundary)。
const View = lazy(() => {
  const loaded = loadedReaderMarkdownView();
  if (loaded) return { then: (resolve) => resolve({ default: loaded }) };
  return loadReaderMarkdownView().then((component) => ({ default: component }));
});

// 'ready' | 'loading' | 'failed'
function useViewState() {
  const [state, setState] = useState(() => (loadedReaderMarkdownView() ? 'ready' : 'loading'));
  useEffect(() => {
    if (loadedReaderMarkdownView()) {
      if (state !== 'ready') setState('ready');
      return undefined;
    }
    if (state === 'failed') return undefined; // 本次挂载不再重试(下一次挂载重新拉)
    let alive = true;
    loadReaderMarkdownView().then(
      () => { if (alive) setState('ready'); },
      () => { if (alive) setState('failed'); },
    );
    return () => { alive = false; };
  }, [state]);
  return state;
}

// 调用方把它并进自己的加载态:false = 渲染器还在路上(失败也算「不再等」——交给 ReaderMarkdown 退回纯文本)。
// 只调用这个 hook 也会触发拉取(问答面板 / 播客文稿挂载时先拉,用户输入或文稿到达时已就绪)。
// eslint-disable-next-line react-refresh/only-export-components -- 与组件同生同死的就绪信号,拆文件反而割裂
export function useReaderMarkdownReady() {
  return useViewState() !== 'loading';
}

function PlainTextFallback({ text }) {
  const paragraphs = String(text || '').split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  return paragraphs.map((part, index) => (
    <p key={index} style={{ whiteSpace: 'pre-wrap' }}>{part}</p>
  ));
}

export default function ReaderMarkdown({ fallback = null, ...props }) {
  const state = useViewState();
  if (state === 'ready') return <Suspense fallback={fallback}><View {...props} /></Suspense>;
  if (state === 'failed') return <PlainTextFallback text={props.children} />;
  return fallback;
}
