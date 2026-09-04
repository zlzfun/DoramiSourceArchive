import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  BookOpenText,
  CalendarDays,
  Clock3,
  ExternalLink,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from 'lucide-react';
import {
  ensurePersonalBrief,
  fetchInterests,
  fetchPersonalBrief,
  fetchPersonalBriefs,
  fetchTodayPersonalBrief,
  rebuildPersonalBrief,
} from '../api';
import ReaderMarkdown from './ReaderMarkdown';
import { formatDateTime } from '../utils/datetime';
import { useModalA11y } from '../hooks/useModalA11y';

const TERMINAL = new Set(['ready', 'degraded', 'failed', 'superseded']);
const STATUS_META = {
  pending: { label: '准备中', cls: 'stamp-run' },
  generating: { label: '编排中', cls: 'stamp-run' },
  ready: { label: '已生成', cls: 'stamp-ok' },
  degraded: { label: '降级生成', cls: 'stamp-warn' },
  failed: { label: '生成失败', cls: 'stamp-bad' },
  superseded: { label: '已被新版本替代', cls: 'stamp-idle' },
};

const tagName = (tag) => tag?.name_zh || tag?.name_en || tag?.label || tag?.code || '';

function scoreText(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(number % 1 ? 1 : 0) : '—';
}

function SnapshotDialog({ item, onClose, onOpenArticle }) {
  const panelRef = useRef(null);
  useModalA11y(Boolean(item), onClose, panelRef);
  if (!item) return null;
  const snapshot = item.snapshot || {};
  const tags = (snapshot.display_tags || snapshot.tags || []).slice(0, 6);
  return (
    <div className="modal-overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <article ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="brief-snapshot-title" className="modal-panel max-w-4xl form-sheet">
        <div className="form-sheet-head">
          <div className="min-w-0">
            <div className="micro-label">早报快照 · {item.section || '精选'}</div>
            <h2 id="brief-snapshot-title" className="card-title mt-1 line-clamp-2">{snapshot.title || '（无标题）'}</h2>
          </div>
          <button type="button" className="icon-button shrink-0" onClick={onClose} aria-label="关闭早报条目"><X className="h-4 w-4" /></button>
        </div>
        <div className="form-sheet-body min-h-0 overflow-y-auto">
          <div className="flex flex-wrap items-center gap-2 tiny-meta">
            <span>{snapshot.source_name || snapshot.source_id || '未知来源'}</span>
            {snapshot.publish_date && <span>· {formatDateTime(snapshot.publish_date)}</span>}
            {snapshot.quality_score != null && <span className="brief-score">内容价值分 {scoreText(snapshot.quality_score)}</span>}
          </div>
          {tags.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {tags.map((tag, index) => <span key={`${tag.type || 'canonical'}-${tag.code || tag.id || tag.candidate_id || index}`} className={`brief-tag ${tag.type === 'extracted' ? 'is-extracted' : ''}`} title={tag.type === 'extracted' ? 'AI 提取标签；尚未纳入规范标签' : '规范标签'}>{tagName(tag)}</span>)}
            </div>
          )}
          {snapshot.score_reason && (
            <section className="mt-4 rounded-[var(--r-card)] bg-[var(--dorami-soft)] p-4">
              <h3 className="micro-label">评分理由</h3>
              <p className="body-text mt-1">{snapshot.score_reason}</p>
            </section>
          )}
          {snapshot.summary && <p className="body-text mt-4">{snapshot.summary}</p>}
          {snapshot.content ? (
            <div className="markdown-body mt-5"><ReaderMarkdown>{snapshot.content}</ReaderMarkdown></div>
          ) : (
            <p className="tiny-meta mt-5">历史快照仅保留摘要和标签；完整正文请在阅读器或原文中查看。</p>
          )}
        </div>
        <div className="form-sheet-foot">
          {snapshot.source_url && (
            <a href={snapshot.source_url} target="_blank" rel="noreferrer" className="action-button action-button-quiet min-h-[32px] px-3 text-xs">
              <ExternalLink className="h-3.5 w-3.5" /> 查看原文
            </a>
          )}
          <span className="flex-1" />
          {item.article_id && onOpenArticle && (
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => onOpenArticle(item.article_id)}>在阅读器中打开</button>
          )}
          <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={onClose}>继续阅读早报</button>
        </div>
      </article>
    </div>
  );
}

function BriefItem({ item, degraded, onOpen }) {
  const snapshot = item.snapshot || {};
  const primary = (snapshot.tags || []).find((tag) => tag.is_primary) || (snapshot.tags || [])[0];
  return (
    <button type="button" className="brief-item" onClick={() => onOpen(item)}>
      <span className="brief-item-score" aria-label={`内容价值分 ${scoreText(item.quality_score)}`}>{scoreText(item.quality_score)}</span>
      <span className="brief-item-main">
        <span className="brief-item-meta">
          {primary && <span className="brief-tag">{tagName(primary)}</span>}
          <span>{snapshot.source_name || snapshot.source_id || '未知来源'}</span>
          {snapshot.publish_date && <span>· {formatDateTime(snapshot.publish_date)}</span>}
        </span>
        <span className="brief-item-title">{snapshot.title || '（无标题）'}</span>
        {/* one_sentence_summary 自 v3.45.1 取缔;历史 edition 快照仍带该键,保留回退读取 */}
        {(snapshot.one_sentence_summary || snapshot.summary) && (
          <span className="brief-item-summary">{snapshot.one_sentence_summary || snapshot.summary}</span>
        )}
        <span className={`brief-item-reason ${degraded ? 'is-latest' : ''}`}>
          {degraded
            ? (item.selection_reason || snapshot.selection_reason || '订阅源最新更新，不计入正式精选。')
            : (item.selection_reason || snapshot.selection_reason || '来自你的订阅范围')}
        </span>
      </span>
      <BookOpenText className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
    </button>
  );
}

export default function PersonalBriefTab({
  showToast,
  onManageSubscriptions,
  onOpenArticle,
  interestVersion = 0,
  mobile = false,
}) {
  const [payload, setPayload] = useState(null);
  const [history, setHistory] = useState([]);
  const [interestCount, setInterestCount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [selectedItem, setSelectedItem] = useState(null);
  const [viewingDate, setViewingDate] = useState('');
  const [viewingRevision, setViewingRevision] = useState(null);

  const loadSidebar = useCallback(() => {
    Promise.all([fetchPersonalBriefs(30), fetchInterests()]).then(([briefs, interests]) => {
      setHistory(briefs.items || []);
      setInterestCount((interests.items || []).length);
    }).catch(() => {
      // 历史与兴趣计数是辅助信息，失败不覆盖今日早报主状态。
    });
  }, []);

  const loadToday = useCallback(async ({ ensure = false } = {}) => {
    setError('');
    try {
      const data = ensure ? await ensurePersonalBrief() : await fetchTodayPersonalBrief();
      setPayload(data);
      setViewingDate('');
      setViewingRevision(null);
      const nextStatus = data?.status || data?.edition?.status;
      if (TERMINAL.has(nextStatus)) loadSidebar();
      return data;
    } catch (err) {
      setError(err.message || '加载今日早报失败，请重试');
      return null;
    } finally {
      setLoading(false);
    }
  }, [loadSidebar]);

  useEffect(() => {
    setLoading(true);
    loadToday({ ensure: true });
    loadSidebar();
  }, [loadSidebar, loadToday, interestVersion]);

  const status = payload?.status || payload?.edition?.status;
  useEffect(() => {
    if (!status || TERMINAL.has(status) || status === 'empty_subscriptions' || viewingDate) return undefined;
    const timer = window.setInterval(() => loadToday(), 8000);
    return () => window.clearInterval(timer);
  }, [loadToday, status, viewingDate]);

  const edition = payload?.edition || (payload?.id ? payload : null);
  const grouped = useMemo(() => {
    const result = [];
    (edition?.items || []).forEach((item) => {
      const key = item.section || (edition.status === 'degraded' ? '订阅源最新更新' : '今日精选');
      const current = result.find((group) => group.key === key);
      if (current) current.items.push(item);
      else result.push({ key, items: [item] });
    });
    return result;
  }, [edition]);

  const handleRebuild = async () => {
    setWorking(true);
    try {
      const data = await rebuildPersonalBrief();
      setPayload(data);
      setViewingDate('');
      setViewingRevision(null);
      showToast?.('已开始重新编排今日早报', 'success');
      loadSidebar();
    } catch (err) {
      showToast?.(err.message || '重新编排失败，请重试', 'error');
    } finally {
      setWorking(false);
    }
  };

  const openHistory = async (date, revision) => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchPersonalBrief(date, revision);
      setPayload({ status: data.status, edition: data });
      setViewingDate(date);
      setViewingRevision(revision);
    } catch (err) {
      setError(err.message || '加载历史早报失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const noInterest = interestCount === 0;
  const meta = STATUS_META[edition?.status || status] || STATUS_META.pending;
  const ratioUnfillable = edition?.degraded_reason === 'insufficient_non_interest_content';

  return (
    <section className={`personal-brief ${mobile ? 'is-mobile' : ''}`} aria-label="我的早报">
      <div className="brief-main">
        <header className="brief-head">
          <div>
            <div className="micro-label">{viewingDate ? '历史早报' : '每日 · 我的订阅'}</div>
            <h1 className="page-title mt-1">{viewingDate || edition?.report_date || '今日早报'}{viewingRevision ? ` · 第 ${viewingRevision} 版` : ''}</h1>
            <p className="tiny-meta mt-1">
              {noInterest ? '根据你的订阅源精选' : '在你的订阅范围内，兴趣匹配最多占 50%，其余由高质量内容补齐'}
            </p>
          </div>
          <div className="brief-head-actions">
            {viewingDate && (
              <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={() => { setLoading(true); loadToday(); }}>
                <ArrowLeft className="h-3.5 w-3.5" /> 回到今日
              </button>
            )}
            {!viewingDate && (
              <button type="button" className="icon-button" onClick={handleRebuild} disabled={working || loading} title="重新编排今日早报" aria-label="重新编排今日早报">
                <RefreshCw className={`h-4 w-4 ${working ? 'animate-spin' : ''}`} />
              </button>
            )}
          </div>
        </header>

        {loading ? (
          <div className="brief-state" role="status"><Loader2 className="h-5 w-5 animate-spin" /><span>正在准备你的早报…</span></div>
        ) : error ? (
          <div className="brief-state is-error">
            <span>{error}</span>
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => { setLoading(true); loadToday({ ensure: true }); }}>重试加载</button>
          </div>
        ) : status === 'empty_subscriptions' ? (
          <div className="brief-state">
            <CalendarDays className="h-7 w-7 text-slate-500" />
            <h2 className="card-title">订阅后，哆啦美才能为你准备早报</h2>
            <p className="tiny-meta">我的早报不会从未订阅内容补齐，只使用你当前的有效订阅。</p>
            <button type="button" className="action-button action-button-primary" onClick={onManageSubscriptions}>去发现来源</button>
          </div>
        ) : ['pending', 'generating', 'not_started'].includes(status) ? (
          <div className="brief-state">
            <Clock3 className="h-7 w-7 text-[var(--dorami-accent)]" />
            <span className={`stamp ${meta.cls}`}>{meta.label}</span>
            <h2 className="card-title">正在等待订阅源和文章分析就绪</h2>
            <p className="tiny-meta">08:30 前只等待；08:30 后开始检查，最晚检查时间到达仍未就绪时，将按已完成内容降级生成。</p>
            {edition?.deadline_at && <span className="tiny-meta">最晚检查 {formatDateTime(edition.deadline_at)}</span>}
          </div>
        ) : status === 'failed' ? (
          <div className="brief-state is-error">
            <span className={`stamp ${meta.cls}`}>{meta.label}</span>
            <h2 className="card-title">今日早报没有完成</h2>
            <p className="tiny-meta">{edition?.error || '生成过程遇到问题，可以重试。'}</p>
            <button type="button" className="action-button action-button-primary" onClick={handleRebuild} disabled={working}>{working ? '重试中…' : '重试生成'}</button>
          </div>
        ) : (
          <>
            <div className="brief-edition-meta">
              <span className={`stamp ${meta.cls}`}>{meta.label}</span>
              <span>第 {edition?.revision || 1} 版</span>
              {edition?.generated_at && <span>· {formatDateTime(edition.generated_at)}</span>}
            </div>
            {edition?.status === 'degraded' && (
              <div className="brief-notice">
                <Sparkles className="h-4 w-4" />
                {ratioUnfillable ? (
                  <div><strong>今天缺少用于补齐的非兴趣内容</strong><p>为避免假装满足 50% 上限，下方只如实展示订阅源最新更新；兴趣占比可能超过 50%，这些条目不计入正式精选。</p></div>
                ) : (
                  <div><strong>今天暂时没有达到早报入选标准的内容</strong><p>下方是你当前订阅源的最新更新，不作为精选推荐。</p></div>
                )}
              </div>
            )}
            {grouped.length === 0 ? (
              <div className="brief-state"><span>你的订阅源今天还没有可展示的更新</span><button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={onManageSubscriptions}>管理订阅</button></div>
            ) : grouped.map((group) => (
              <section key={group.key} className="brief-section">
                <div className="brief-section-head"><h2 className="card-title">{group.key}</h2><span className="tiny-meta">{group.items.length} 篇</span></div>
                <div className="brief-items">
                  {group.items.map((item) => <BriefItem key={item.id || item.position} item={item} degraded={edition?.status === 'degraded'} onOpen={setSelectedItem} />)}
                </div>
              </section>
            ))}
          </>
        )}
      </div>

      <aside className="brief-history" aria-label="历史早报">
        <div className="brief-history-head"><CalendarDays className="h-4 w-4" /><span className="section-title">近期早报</span></div>
        {history.length === 0 ? (
          <p className="tiny-meta">生成第一份早报后，历史版本会保存在这里。</p>
        ) : history.map((entry) => (
          <button
            type="button"
            key={entry.id}
            className={`brief-history-row ${viewingDate === entry.report_date && viewingRevision === entry.revision ? 'is-on' : ''}`}
            onClick={() => openHistory(entry.report_date, entry.revision)}
          >
            <span><strong>{entry.report_date}</strong><small>第 {entry.revision} 版</small></span>
            <span className={`stamp ${STATUS_META[entry.status]?.cls || 'stamp-idle'}`}>{STATUS_META[entry.status]?.label || entry.status}</span>
          </button>
        ))}
      </aside>

      <SnapshotDialog item={selectedItem} onClose={() => setSelectedItem(null)} onOpenArticle={onOpenArticle} />
    </section>
  );
}
