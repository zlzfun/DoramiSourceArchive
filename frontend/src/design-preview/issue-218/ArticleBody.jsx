import { Component, useEffect, useRef, useState } from 'react';
import ReaderMarkdown from '../../components/ReaderMarkdown';
import SlotSkeleton from './SlotSkeleton';
import { announcementOf, stoppedLine } from './jobCopy';
import { canResumeJob, isActiveJob, prefixLength } from './translationSim';

// 「连续展示」推进边界的骨架:单个固定 4 行,行形同阅读窗正文加载骨架 PaneBodySkeleton(`.skeleton` + h-4 + mt-3)
const FRONTIER_LINES = ['w-full', 'w-11/12', 'w-full', 'w-2/3'];

// 读屏一次性播报:只在这篇打开期间亲眼看着任务结束时,往不可见的 status 区写一句,几秒后清空——
// 不逐片、不逐块;打开时已是结束态不补播;清空后不与可见的原因行在阅读顺序里重复。
function useOneShotAnnouncement(job) {
  const message = announcementOf(job);
  const [text, setText] = useState('');
  const last = useRef(message);
  useEffect(() => {
    if (message === last.current) return undefined;
    last.current = message;
    setText(message);
    if (!message) return undefined;
    const timer = setTimeout(() => setText(''), 6000);
    return () => clearTimeout(timer);
  }, [message]);
  return text;
}

// 停止态的那一行,整篇只出现一次(连续展示:推进边界下;逐段占位:第一个未完成片下):一行原因与动作。
// 进行中只有一句不播报的隐藏文字,不占版面——样页选定后去掉了进行中的「取消翻译」,也不另设取消入口。
// 用键盘点了动作后,焦点交给变化后的这一行(click 的 detail 为 0 即键盘触发),不丢回页首。
function PendingFoot({ job, pendingText, onResume }) {
  const active = isActiveJob(job);
  const stopped = active ? null : stoppedLine(job);
  const rootRef = useRef(null);
  const actionRef = useRef(null);
  const focusNext = useRef(false);
  const stateKey = active ? 'active' : `${job.status}:${job.reason || ''}`;
  useEffect(() => {
    if (!focusNext.current) return;
    focusNext.current = false;
    (actionRef.current || rootRef.current)?.focus({ preventScroll: true });
  }, [stateKey]);
  const handle = (fn) => (event) => {
    focusNext.current = event.detail === 0;
    fn();
  };
  if (active) return <div ref={rootRef} className="sr-only" tabIndex={-1}>{pendingText}</div>;
  return (
    <div className="r2-frontier-foot tiny-meta">
      <span>{stopped.text}</span>
      {stopped.action && canResumeJob(job) && (
        <button ref={actionRef} type="button" className="reader-pane-act" onClick={handle(onResume)}>{stopped.action}</button>
      )}
    </div>
  );
}

// 连续展示的推进边界:进行中 = 走动的骨架;停止 = 同一块骨架停走,下面多一行原因。
function Frontier({ job, onResume }) {
  const active = isActiveJob(job);
  return (
    <div className={`r2-frontier skeleton-delay${active ? '' : ' is-stopped'}`} data-r2-frontier="">
      <div aria-hidden="true">
        {FRONTIER_LINES.map((width, index) => (
          <div key={index} className={`skeleton h-4 ${width} ${index > 0 ? 'mt-3' : ''}`} />
        ))}
      </div>
      <PendingFoot job={job} pendingText="后续内容翻译中" onResume={onResume} />
    </div>
  );
}

// 正文栏的实测宽度与字号:逐段占位据此估算每段骨架的行数(手机 / 桌面、窄宽窗口各自成立)
function useCopyMetrics() {
  const ref = useRef(null);
  const [metrics, setMetrics] = useState({ width: 640, font: 17 });
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      const width = el.clientWidth;
      const font = parseFloat(window.getComputedStyle(el).fontSize) || 17;
      if (!width) return;
      setMetrics((current) => (Math.abs(current.width - width) < 4 && current.font === font ? current : { width, font }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, metrics];
}

function ZhSegment({ index, segment, fresh }) {
  return (
    <div data-r2-seg={index} className="r2-seg">
      <div key="zh" className={fresh ? 'r2-seg-body is-fresh' : 'r2-seg-body'}>
        <ReaderMarkdown>{segment.zh}</ReaderMarkdown>
      </div>
    </div>
  );
}

// 正文:原文视图 = 全文原文。中文视图两种显示方式(只在演示控制切换,共用同一个后台任务):
//   逐段占位(默认):每个未完成片在原位置显示按原文块结构估算的骨架,哪片完成就把它自己那组骨架换成中文,
//                   后片可以先出现,位置与顺序不重排;
//   连续展示:只接上从第 1 片起连续完成的部分,其后一块推进边界骨架。
// 两种方式都没有进度文字或段数,进行中只看「中文」上的小圈圈;全部完成后不另提示。
// 片段 DOM 按序号挂载(data-r2-seg),ScrollAnchor 据此对位;zhEntry = 进入中文视图时的进度快照,
// 之后才出现的片换入时淡入一次,切视图 / 切显示方式本身不闪。
export default function ArticleBody({ article, job, zhView, zhEntry, displayMode = 'slots', onResume }) {
  const [copyRef, metrics] = useCopyMetrics();
  const announcement = useOneShotAnnouncement(job);
  const { body } = article;
  if (body.lang === 'zh') {
    return (
      <div ref={copyRef} className="reader-article-copy markdown-body" data-ai-translation-scope="article-body">
        <ReaderMarkdown>{body.text}</ReaderMarkdown>
      </div>
    );
  }
  const status = <span className="sr-only" role="status">{announcement}</span>;
  const zh = zhView && Boolean(job);

  if (!zh) {
    return (
      <div ref={copyRef} className="reader-article-copy markdown-body" data-ai-translation-scope="article-body">
        {body.segments.map((segment, index) => (
          <div key={index} data-r2-seg={index} className="r2-seg" lang="en">
            <div key="en" className="r2-seg-body">
              <ReaderMarkdown>{segment.en}</ReaderMarkdown>
            </div>
          </div>
        ))}
        {status}
      </div>
    );
  }

  const entry = zhEntry || { from: 0, done: [] };

  if (displayMode === 'prefix') {
    const shown = prefixLength(job);
    return (
      <div ref={copyRef} className="reader-article-copy markdown-body" data-ai-translation-scope="article-body">
        {body.segments.slice(0, shown).map((segment, index) => (
          <ZhSegment key={index} index={index} segment={segment} fresh={index >= entry.from} />
        ))}
        {shown < job.total && <Frontier key="frontier" job={job} onResume={onResume} />}
        {status}
      </div>
    );
  }

  const stopped = !isActiveJob(job);
  const firstPending = body.segments.findIndex((_, index) => !job.done.includes(index));
  return (
    <div ref={copyRef} className="reader-article-copy markdown-body" data-ai-translation-scope="article-body">
      {body.segments.map((segment, index) => {
        if (job.done.includes(index)) {
          return <ZhSegment key={index} index={index} segment={segment} fresh={!entry.done.includes(index)} />;
        }
        return (
          <div key={index} data-r2-seg={index} data-r2-pending="" className="r2-seg">
            <SlotSkeleton key="slot" blocks={segment.blocks} metrics={metrics} stopped={stopped} />
            {index === firstPending && (
              <PendingFoot job={job} pendingText="其余段落翻译中" onResume={onResume} />
            )}
          </div>
        );
      })}
      {status}
    </div>
  );
}

// ── 阅读锚点(最小实现,不改全局阅读器)──
// 内容变化前记下视口顶部所在的片(序号 + 片内比例),变化后把同一片放回原处:前方骨架换成中文、
// 后片先出现、新片接上,读者所在位置都不动,也不自动滚到新内容。
// 视口顶部落在骨架里、而视口内已能看到真实内容(已译或原文)时,锚定那段可见的真实内容——骨架里没有可读的东西,
// 它的高度还会随那唯一一行动作 / 原因挪进来、估行变化而变,按骨架比例恢复会把下方正在读的译文推走。切视图时:
//   原文 → 中文:所在片在中文视图里存在(已译或占位)就对齐同一片,否则落到推进边界(视口约 40% 处);
//   中文 → 原文:读者在中文里没动过(或停在推进边界)就回到离开原文时的位置;已在中文里读到某片则原文对齐同一片。
// 切显示方式时所在片若不再显示(逐段 → 连续),同样落到推进边界。
// 每次挂载对应一篇的一次阅读(滚动容器按文章 key 重建),记忆随之重置。
function anchorOf(el, rect, top) {
  const inside = rect.top < top && rect.height > 0;
  return {
    index: Number(el.getAttribute('data-r2-seg')),
    offset: rect.top - top,
    fraction: inside ? (top - rect.top) / rect.height : null,
  };
}

function captureAnchor(scroller) {
  if (!scroller || scroller.scrollTop <= 0) return null;
  const box = scroller.getBoundingClientRect();
  const segs = [...scroller.querySelectorAll('[data-r2-seg]')];
  const first = segs.findIndex((el) => el.getBoundingClientRect().bottom > box.top + 1);
  if (first < 0) return { index: null }; // 视口顶部已在全部已显示片之下(推进边界或文末)
  const head = segs[first];
  if (head.hasAttribute('data-r2-pending')) {
    for (const el of segs.slice(first + 1)) {
      const rect = el.getBoundingClientRect();
      if (rect.top >= box.bottom) break;
      if (!el.hasAttribute('data-r2-pending')) return anchorOf(el, rect, box.top);
    }
  }
  return anchorOf(head, head.getBoundingClientRect(), box.top);
}

function restoreToSegment(scroller, anchor) {
  const el = scroller.querySelector(`[data-r2-seg="${anchor.index}"]`);
  if (!el) return false;
  const top = scroller.getBoundingClientRect().top;
  const rect = el.getBoundingClientRect();
  const wantedTop = anchor.fraction == null ? top + anchor.offset : top - anchor.fraction * rect.height;
  const delta = rect.top - wantedTop;
  if (Math.abs(delta) >= 1) scroller.scrollTop += delta;
  return true;
}

function landAtFrontier(scroller) {
  const frontier = scroller.querySelector('[data-r2-frontier]');
  if (!frontier) return;
  const box = scroller.getBoundingClientRect();
  const delta = frontier.getBoundingClientRect().top - (box.top + box.height * 0.4);
  if (Math.abs(delta) >= 1) scroller.scrollTop += delta;
}

export class ScrollAnchor extends Component {
  origSaved = null; // 离开原文视图时的位置
  zhLanding = null; // 进入中文视图后的落点;读者没动过时切回原文按 origSaved 复位

  getSnapshotBeforeUpdate(prevProps) {
    if (prevProps.version === this.props.version) return null;
    const scroller = this.props.scrollRef.current;
    if (!scroller) return null;
    const anchor = captureAnchor(scroller);
    const viewChanged = prevProps.view !== this.props.view;
    if (viewChanged && prevProps.view === 'orig') this.origSaved = { anchor, scrollTop: scroller.scrollTop };
    return { anchor, viewChanged, scrollTop: scroller.scrollTop };
  }

  componentDidUpdate(prevProps, prevState, snapshot) {
    const scroller = this.props.scrollRef.current;
    if (!snapshot || !scroller) return;
    const { anchor, viewChanged, scrollTop } = snapshot;
    const stayed = this.zhLanding != null && Math.abs(scrollTop - this.zhLanding) <= 2;

    if (!viewChanged) {
      if (anchor?.index != null && !restoreToSegment(scroller, anchor)) landAtFrontier(scroller);
      if (stayed) this.zhLanding = scroller.scrollTop; // 补偿不算读者移动
      return;
    }

    if (this.props.view === 'orig') {
      this.zhLanding = null;
      if (!stayed && anchor?.index != null && restoreToSegment(scroller, anchor)) return;
      const saved = this.origSaved;
      if (saved?.anchor?.index != null) restoreToSegment(scroller, saved.anchor);
      else if (saved) scroller.scrollTop = saved.scrollTop;
      return;
    }

    if (anchor && (anchor.index == null || !restoreToSegment(scroller, anchor))) landAtFrontier(scroller);
    this.zhLanding = scroller.scrollTop;
  }

  render() {
    return this.props.children;
  }
}
