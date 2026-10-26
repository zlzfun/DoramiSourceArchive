import { Component, memo, useEffect, useMemo, useRef, useState } from 'react';
import ReaderMarkdown from './ReaderMarkdown';
import TranslationSkeleton from './TranslationSkeleton';
import { useCopyMetrics } from '../hooks/useCopyMetrics';
import { latinLang } from '../utils/readerText';
import { stripDuplicateLeadingHeading } from '../utils/markdownTitle';
import {
  announcementOf, blockShapes, isActiveStatus, parseBlocks, stoppedLine,
} from '../utils/translationBlocks';
import {
  SCROLLER_SELECTOR, captureAnchor, restoreAcross, restoreAnchor,
} from '../utils/translationAnchor';

// 阅读窗 / 手机正文页的正文容器(翻译作用域),issue #218 第三稿「逐段占位、完成后原位替换」:
//   原文视图 = 全文原文;中文视图 = 服务端预览的完整 Markdown 块,已译块用 ReaderMarkdown 渲染(净化 / 媒体代理 /
//   公式同正文),未完成块在原位置画按原文结构估算的骨架,哪块就绪就把它自己那组骨架换成中文,后块可先出现,
//   位置与顺序不重排。没有进度文字或段数,进行中只看「中文」上的小圈圈;全部完成后不另提示。
//   停止态(失败 / 额度用尽 / 取消)只在第一个未完成块下一行原因 + 可用动作。

// 读屏一次性播报:只在这篇打开期间亲眼看着任务从进行中走到终态时,往不可见的 status 区写一句,几秒后清空——
// 不逐块;打开时已是终态不补播;清空后不与可见的原因行在阅读顺序里重复。
function useTerminalAnnouncement(status, job) {
  const [text, setText] = useState('');
  const jobRef = useRef(job);
  const prevRef = useRef(status);
  useEffect(() => { jobRef.current = job; });
  useEffect(() => {
    const was = prevRef.current;
    prevRef.current = status;
    if (isActiveStatus(status)) {
      setText('');
      return undefined;
    }
    if (!isActiveStatus(was)) return undefined;
    const message = announcementOf(status, jobRef.current);
    if (!message) return undefined;
    setText(message);
    const timer = setTimeout(() => setText(''), 6000);
    return () => clearTimeout(timer);
  }, [status]);
  return text;
}

// 第一个未完成块下的那一行:停止 = 原因 + 可用动作;进行中 = 只有一句不播报的隐藏文字(没有取消入口)。
// 用键盘点了动作后,焦点交给变化后的这一行(click 的 detail 为 0 即键盘触发),不丢回页首。
function TranslationFoot({ status, job, active, onResume, onRestart }) {
  const line = active ? null : stoppedLine(status, job);
  const rootRef = useRef(null);
  const actionRef = useRef(null);
  const handoff = useRef(false);
  const stateKey = line ? `${line.text}|${line.action || ''}` : 'active';
  useEffect(() => {
    if (!handoff.current) return;
    handoff.current = false;
    (actionRef.current || rootRef.current)?.focus({ preventScroll: true });
  }, [stateKey]);
  if (!line) {
    return active ? <div ref={rootRef} className="sr-only" tabIndex={-1}>其余段落翻译中</div> : null;
  }
  const run = line.action === 'resume' ? onResume : line.action === 'restart' ? onRestart : null;
  return (
    <div ref={rootRef} className="reader-tr-foot tiny-meta">
      <span>{line.text}</span>
      {run && (
        <button
          ref={actionRef}
          type="button"
          className="reader-pane-act"
          onClick={(event) => {
            handoff.current = event.detail === 0;
            run();
          }}
        >
          {line.label}
        </button>
      )}
    </div>
  );
}

// 同一段文字不重复解析:进行中 SSE 无变化也每 2 秒推一次快照,长文 / 公式整篇重渲很贵
const MarkdownBlock = memo(function MarkdownBlock({ text, sourceUrl }) {
  return <ReaderMarkdown sourceUrl={sourceUrl}>{text}</ReaderMarkdown>;
});

// 首块是与标题同名的标题块时整块不画——原文视图同样剥掉了它(stripDuplicateLeadingHeading)
function duplicatesTitle(block, title) {
  if (!block || typeof block.source !== 'string' || !title) return false;
  return !stripDuplicateLeadingHeading(block.source, title).trim();
}

// 内容变化前记下阅读位置、变化后放回原处(见 utils/translationAnchor);每次挂载对应一篇的一次阅读。
class TranslationScrollAnchor extends Component {
  origSaved = null; // 离开原文视图时的位置
  zhLanding = null; // 进入中文视图后的落点;读者没动过时切回原文按 origSaved 复位

  getSnapshotBeforeUpdate(prevProps) {
    if (prevProps.version === this.props.version) return null;
    const copy = this.props.copyRef.current;
    const scroller = copy?.closest(SCROLLER_SELECTOR);
    if (!scroller) return null;
    const anchor = captureAnchor(scroller, copy, prevProps.view);
    const viewChanged = prevProps.view !== this.props.view;
    if (viewChanged && prevProps.view === 'orig') this.origSaved = { anchor, scrollTop: scroller.scrollTop };
    return { anchor, viewChanged, scrollTop: scroller.scrollTop };
  }

  componentDidUpdate(prevProps, prevState, snapshot) {
    const copy = this.props.copyRef.current;
    const scroller = copy?.closest(SCROLLER_SELECTOR);
    if (!snapshot || !scroller) return;
    const { anchor, viewChanged, scrollTop } = snapshot;
    const stayed = this.zhLanding != null && Math.abs(scrollTop - this.zhLanding) <= 2;
    const { view } = this.props;

    if (!viewChanged) {
      if (anchor) restoreAnchor(scroller, copy, view, anchor);
      if (stayed) this.zhLanding = scroller.scrollTop; // 补偿不算读者移动
      return;
    }
    if (view === 'orig') {
      this.zhLanding = null;
      if (!stayed && anchor && restoreAcross(scroller, copy, 'orig', anchor)) return;
      const saved = this.origSaved;
      if (saved?.anchor) restoreAcross(scroller, copy, 'orig', saved.anchor);
      else if (saved) scroller.scrollTop = saved.scrollTop;
      return;
    }
    if (anchor) restoreAcross(scroller, copy, 'zh', anchor);
    this.zhLanding = scroller.scrollTop;
  }

  render() {
    return this.props.children;
  }
}

/**
 * body = 已剥同名首标题的原文;rawBody 只用来判 lang;loading = 正文在途(调用方给骨架 fallback)。
 * zhView / translation 来自 useArticleTranslation;translatedBody = 已剥标题的整篇译文缓存(没有块预览时用)。
 */
export default function ArticleCopy({
  title, body, rawBody, loading, loadingFallback = null, emptyText, sourceUrl,
  zhView = false, translation = null, translatedBody = '', onResume, onRestart,
}) {
  const copyRef = useRef(null);
  const metrics = useCopyMetrics(copyRef);
  const status = translation?.status || 'idle';
  const job = translation?.job || null;
  const announcement = useTerminalAnnouncement(status, job);
  const preview = zhView ? translation?.preview : null;
  // 进入中文视图(或换了任务)那一刻已就绪的块不做换入淡入;之后才就绪的块换入时淡入一次(切视图本身不闪)
  const [entry, setEntry] = useState(null);
  if (!zhView && entry !== null) setEntry(null);
  if (preview && (entry === null || entry.jobId !== preview.jobId)) {
    setEntry({
      jobId: preview.jobId,
      ids: new Set(preview.blocks.filter((block) => block.translation != null).map((block) => block.id)),
    });
  }

  // 骨架形状只在块集合变化时重算(无新块的增量保留同一个 blocks 数组)
  const previewBlocks = preview?.blocks;
  const shapesById = useMemo(
    () => new Map((previewBlocks || []).filter((block) => block.translation == null).map((block) => [block.id, blockShapes(block)])),
    [previewBlocks],
  );
  const estimateShapes = useMemo(() => parseBlocks(body || ''), [body]);

  const statusRegion = <span className="sr-only" role="status" data-tr-status="">{announcement}</span>;
  const view = zhView ? 'zh' : 'orig';
  const active = isActiveStatus(status) || Boolean(translation?.requesting);
  let content;
  let version = view;

  if (loading) {
    content = loadingFallback;
    version = 'loading';
  } else if (!zhView) {
    content = body ? <MarkdownBlock text={body} sourceUrl={sourceUrl} /> : emptyText;
  } else if (preview) {
    const blocks = duplicatesTitle(preview.blocks[0], title) ? preview.blocks.slice(1) : preview.blocks;
    const firstPending = blocks.findIndex((block) => block.translation == null);
    const seen = entry?.ids;
    content = blocks.map((block, index) => {
      if (block.translation != null) {
        return (
          <div key={block.id} className="reader-tr-block" data-tr-block={block.id}>
            <div key="zh" className={!seen || seen.has(block.id) ? 'reader-tr-block-body' : 'reader-tr-block-body is-fresh'}>
              <MarkdownBlock text={block.translation} sourceUrl={sourceUrl} />
            </div>
          </div>
        );
      }
      return (
        <div key={block.id} className="reader-tr-block" data-tr-block={block.id} data-tr-pending="">
          <TranslationSkeleton key="slot" shapes={shapesById.get(block.id) || blockShapes(block)} metrics={metrics} stopped={!active} />
          {index === firstPending && (
            <TranslationFoot status={status} job={job} active={active} onResume={onResume} onRestart={onRestart} />
          )}
        </div>
      );
    });
    version = `zh|blocks|${blocks.map((block) => (block.translation == null ? 0 : 1)).join('')}|${active ? 1 : 0}`;
  } else if (translatedBody) {
    content = <MarkdownBlock text={translatedBody} sourceUrl={sourceUrl} />;
    version = 'zh|result';
  } else {
    // 首个预览到达前(刚点「译为中文」/ 回到这篇):按原文结构先画整篇骨架,150ms 内到达则不可见
    content = (
      <div className="reader-tr-block" data-tr-block="estimate" data-tr-pending="">
        <TranslationSkeleton shapes={estimateShapes} metrics={metrics} stopped={Boolean(!active && translation?.loaded)} />
        {translation?.loaded && !active && (
          <TranslationFoot status={status} job={job} active={false} onResume={onResume} onRestart={onRestart} />
        )}
      </div>
    );
    version = `zh|estimate|${active ? 1 : 0}`;
  }

  return (
    <TranslationScrollAnchor version={version} view={view} copyRef={copyRef}>
      <div
        ref={copyRef}
        className="reader-article-copy markdown-body"
        data-ai-translation-scope="article-body"
        lang={zhView ? undefined : latinLang(rawBody)}
      >
        {content}
        {statusRegion}
      </div>
    </TranslationScrollAnchor>
  );
}
