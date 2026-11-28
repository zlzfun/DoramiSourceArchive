import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, Loader2, Play, Trash2, X } from 'lucide-react';
import {
  deleteArticle,
  fetchArticle,
  fetchGenerationFlowPeriods,
  fetchReaderSources,
  saveGenerationFlow,
} from '../api';
import { useConfirm } from '../hooks/useConfirm';
import { useModalA11y } from '../hooks/useModalA11y';
import {
  clockOf,
  draftOf,
  isDirty,
  pageWindow,
  payloadOf,
  phaseClass,
  progressNote,
} from '../utils/generationFlows';

const PAGE_SIZE = 10;

/**
 * 一个生成流程的抽屉(右缘滑入):上面「配置」(取材、定时、旋钮、游标,由「保存配置」一并提交),
 * 下面「往期」(展开读正文、删除)。开关与立即生成在泳道的行上;抽屉开着时行被遮住,所以题条里另有一个立即生成。
 * 「配置」里有哪些行由后端按流程声明(flow.form),这里只管怎么画每一种。
 */
export default function GenerationFlowDrawer({
  open,
  flow,
  running = false,
  systemSourceIds = [],
  periodsVersion = 0,
  showToast,
  onClose,
  onSaved,
  onGenerate,
  onPeriodsChanged,
  onViewSource,
}) {
  const confirm = useConfirm();
  const panelRef = useRef(null);
  useModalA11y(open, onClose, panelRef);

  const flowId = flow?.id || null;
  const [draft, setDraft] = useState(() => (flow ? draftOf(flow) : null));
  const [draftFor, setDraftFor] = useState(flowId);
  const [saving, setSaving] = useState(false);
  const [sourceCatalog, setSourceCatalog] = useState(null);     // 自定名单的候选:切到自定名单时才取
  const [periods, setPeriods] = useState(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadingPage, setLoadingPage] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [bodyCache, setBodyCache] = useState({});
  const [bodyLoadingId, setBodyLoadingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  // 换了一个流程:草稿重来。同一个流程保存 / 别处改动之后,没在编辑(不脏)时才跟着新值走。
  if (flow && (draftFor !== flowId || (draft && !isDirty(flow, draft) && JSON.stringify(draftOf(flow)) !== JSON.stringify(draft)))) {
    setDraft(draftOf(flow));
    setDraftFor(flowId);
  }

  const ensureSourceCatalog = useCallback(() => {
    if (sourceCatalog !== null) return;
    fetchReaderSources()
      .then((data) => setSourceCatalog(data.sources || []))
      .catch(() => setSourceCatalog([]));
  }, [sourceCatalog]);

  const loadPeriods = useCallback((targetPage) => {
    if (!flowId) return Promise.resolve();
    setLoadingPage(true);
    setExpandedId(null);
    return fetchGenerationFlowPeriods(flowId, targetPage, PAGE_SIZE)
      .then((data) => {
        setPeriods(data.items || []);
        setTotal(data.total || 0);
        setPage(targetPage);
      })
      .catch(() => {})
      .finally(() => setLoadingPage(false));
  }, [flowId]);

  // 打开、换流程、生成或删除之后:回到第一页重取
  useEffect(() => {
    if (!open || !flowId) return;
    setPeriods(null);
    setBodyCache({});
    loadPeriods(1);
  }, [open, flowId, periodsVersion, loadPeriods]);

  useEffect(() => {
    if (open && draft?.scopeMode === 'custom') ensureSourceCatalog();
  }, [open, draft?.scopeMode, ensureSourceCatalog]);

  const patch = (changes) => setDraft((prev) => ({ ...prev, ...changes }));
  const setValue = (field, value) => setDraft((prev) => ({ ...prev, values: { ...prev.values, [field]: value } }));
  const toggleScopeId = (sourceId) => setDraft((prev) => ({
    ...prev,
    scopeIds: prev.scopeIds.includes(sourceId) ? prev.scopeIds.filter((id) => id !== sourceId) : [...prev.scopeIds, sourceId],
  }));

  const handleSave = async () => {
    const { payload, error } = payloadOf(flow, draft);
    if (error) {
      showToast(error, 'error');
      return;
    }
    setSaving(true);
    try {
      const updated = await saveGenerationFlow(flow.id, payload);
      setDraft(draftOf(updated));
      onSaved?.(updated);
      showToast(`已保存 ${flow.name} 的配置`, 'success');
    } catch (err) {
      showToast(err.message || '保存失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleResetCursor = async (row) => {
    if (!(await confirm(row.reset_confirm || '确认重置?'))) return;
    try {
      onSaved?.(await saveGenerationFlow(flow.id, { config: { [row.field]: '' } }));
      showToast(`已重置 ${row.label}`, 'success');
    } catch (err) {
      showToast(err.message || '重置失败', 'error');
    }
  };

  // 展开一期时才取正文(往期列表不带正文);null = 失败,再次展开可重试
  const toggleExpand = async (period) => {
    if (expandedId === period.article_id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(period.article_id);
    if (bodyCache[period.article_id] == null) {
      setBodyLoadingId(period.article_id);
      try {
        const full = await fetchArticle(period.article_id);
        setBodyCache((prev) => ({ ...prev, [period.article_id]: full.content || '' }));
      } catch {
        setBodyCache((prev) => ({ ...prev, [period.article_id]: null }));
      } finally {
        setBodyLoadingId(null);
      }
    }
  };

  const handleDelete = async (period) => {
    const question = ['确认删除这一期?下游订阅将不再能拉取到它。', flow.delete_note].filter(Boolean).join('');
    if (!(await confirm(question))) return;
    setDeletingId(period.article_id);
    try {
      await deleteArticle(period.article_id);
      showToast(`已删除 ${flow.name} ${period.date}`, 'success');
      // 删掉当页最后一条后回退一页,避免落在空页
      const emptied = (periods?.length || 0) <= 1 && page > 1;
      await loadPeriods(emptied ? page - 1 : page);
      onPeriodsChanged?.();
    } catch (err) {
      showToast(err.message || '删除失败', 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const dirty = Boolean(flow && draft) && isDirty(flow, draft);
  const candidates = (sourceCatalog || []).filter((source) => !systemSourceIds.includes(source.source_id));
  const note = flow ? progressNote({ ...flow, running }) : '';

  const renderRow = (row) => {
    const key = row.field || row.type;
    if (row.type === 'source_scope') {
      const seg = (
        <div className="mini-seg" role="tablist" aria-label="取材范围">
          {[['all', '全部来源'], ['custom', '自定名单']].map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={draft.scopeMode === value}
              className={`mini-seg-btn ${draft.scopeMode === value ? 'is-on' : ''}`}
              onClick={() => patch({ scopeMode: value })}
            >
              {label}
            </button>
          ))}
        </div>
      );
      if (draft.scopeMode !== 'custom') {
        return <div key={key} className="inspector-param"><span className="inspector-param-label">{row.label}</span>{seg}</div>;
      }
      return (
        <div key={key} className="inspector-param is-block">
          <div className="gf-param-line"><span className="inspector-param-label">{row.label}</span>{seg}</div>
          <div className="brief-scope-list" role="group" aria-label="来源名单">
            {sourceCatalog === null ? (
              <p className="tiny-meta px-2 py-1.5">加载来源目录…</p>
            ) : candidates.length === 0 ? (
              <p className="tiny-meta px-2 py-1.5">来源目录为空</p>
            ) : candidates.map((source) => (
              <label key={source.source_id} className="brief-scope-row">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5"
                  checked={draft.scopeIds.includes(source.source_id)}
                  onChange={() => toggleScopeId(source.source_id)}
                />
                <span className="brief-scope-name">{source.name || source.source_id}</span>
                <span className="tiny-meta shrink-0">
                  {source.count || 0} 篇{source.default_kind_label && source.default_kind !== 'digest' ? ` · ${source.default_kind_label}` : ''}
                </span>
              </label>
            ))}
          </div>
          <p className="tiny-meta mt-1">已选 {draft.scopeIds.length} 个</p>
        </div>
      );
    }
    if (row.type === 'source_link') {
      return (
        <div key={key} className="inspector-param">
          <span className="inspector-param-label">{row.label}</span>
          <button type="button" className="inspector-metabtn" title="去源管理看这个源" onClick={() => onViewSource?.(row.source_id)}>
            {row.text}
          </button>
        </div>
      );
    }
    if (row.type === 'cron') {
      return (
        <div key={key} className="inspector-param">
          <label className="inspector-param-label" htmlFor={`gf-cron-${flow.id}`}>{row.label}</label>
          <input
            id={`gf-cron-${flow.id}`}
            className="inspector-param-input"
            value={draft.cron}
            onChange={(event) => patch({ cron: event.target.value })}
            title="cron 5 段"
            spellCheck={false}
          />
        </div>
      );
    }
    if (row.type === 'number') {
      return (
        <div key={key} className="inspector-param">
          <label className="inspector-param-label" htmlFor={`gf-${flow.id}-${row.field}`}>{row.label}</label>
          <input
            id={`gf-${flow.id}-${row.field}`}
            type="number"
            className="inspector-param-input"
            min={row.min ?? undefined}
            max={row.max ?? undefined}
            step={row.step ?? 1}
            value={draft.values[row.field] ?? ''}
            onChange={(event) => setValue(row.field, event.target.value)}
            title={row.min != null && row.max != null ? `${row.min}–${row.max}` : undefined}
          />
        </div>
      );
    }
    if (row.type === 'toggle') {
      const on = Boolean(draft.values[row.field]);
      return (
        <div key={key} className="inspector-param">
          <span className="inspector-param-label">{row.label}</span>
          <button
            type="button"
            role="switch"
            aria-checked={on}
            aria-label={row.label}
            className={`ledger-switch ${on ? 'is-on' : ''}`}
            onClick={() => setValue(row.field, !on)}
          />
        </div>
      );
    }
    if (row.type === 'cursor') {
      const value = row.value ? String(row.value).slice(0, 19) : '（空）';
      return (
        <div key={key} className="inspector-param">
          <span className="inspector-param-label">{row.label}</span>
          <code className="brief-cursor-val" title={value}>{value}</code>
          <button type="button" className="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）" onClick={() => handleResetCursor(row)}>重置</button>
        </div>
      );
    }
    return null;
  };

  return (
    <>
      <div className={`ledger-scrim ${open ? 'is-open' : ''}`} onClick={onClose} aria-hidden="true" />
      <aside
        ref={panelRef}
        className={`ledger-drawer gf-drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={flow ? `${flow.name}：配置与往期` : '流程'}
        aria-hidden={!open}
        inert={!open}
        tabIndex={-1}
      >
        {flow && draft && (
          <>
            <div className="ledger-drawer-head">
              <div className="ledger-drawer-title">
                {flow.name}
                <span className="drawer-title-sub font-mono">{flow.source_id}</span>
              </div>
              <button
                type="button"
                className="icon-button gf-icon"
                title={running ? '生成中' : '立即生成'}
                aria-label={`立即生成 ${flow.name}`}
                disabled={running}
                onClick={() => onGenerate?.(flow)}
              >
                {running ? <Loader2 className="animate-spin" /> : <Play />}
              </button>
              <button type="button" className="icon-button gf-icon" onClick={onClose} title="关闭" aria-label="关闭">
                <X />
              </button>
            </div>

            <div className="ledger-drawer-body">
              <section className="acq-panel gf-box gf-config">
                <div className="acq-panel-head"><span className="tools-title">配置</span></div>
                <div className="acq-panel-body">
                  <div className="gf-params">{(flow.form || []).map(renderRow)}</div>
                  {flow.scope_note && (
                    <details className="scope-note">
                      <summary>取材与去重口径</summary>
                      <p>{flow.scope_note}</p>
                    </details>
                  )}
                  <div className="gf-save">
                    <button
                      type="button"
                      className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
                      disabled={!dirty || saving}
                      onClick={handleSave}
                    >
                      {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '保存配置'}
                    </button>
                  </div>
                </div>
              </section>

              <section className="acq-panel gf-box gf-periods">
                <div className="acq-panel-head"><span className="tools-title">往期</span></div>
                <div className="acq-panel-body">
                  {running && (
                    <div className="gf-running">
                      <div className="ledger-pipeline" aria-label="生成阶段">
                        {flow.phases.map((phase, index) => (
                          <span key={phase} className={`ledger-pipeline-step${phaseClass({ ...flow, running }, index)}`}>{phase}</span>
                        ))}
                      </div>
                      {note && <div className="pipeline-note">{note}</div>}
                    </div>
                  )}
                  {!running && flow.last_run?.status === 'failed' && (
                    <p className="pipeline-note is-err gf-notice">最近一次生成失败{flow.last_run.reason ? `：${flow.last_run.reason}` : ''}</p>
                  )}
                  {!running && (flow.notices || []).map((notice) => (
                    <p key={notice} className="pipeline-note is-warn gf-notice">{notice}</p>
                  ))}

                  {periods !== null && periods.length === 0 && (
                    <p className="brief-empty">{page === 1 ? '还没有往期' : '本页已无往期'}</p>
                  )}
                  {(periods || []).map((period) => {
                    const expanded = expandedId === period.article_id;
                    const body = bodyCache[period.article_id];
                    return (
                      <div key={period.article_id} className="brief-run">
                        <div className="brief-run-line">
                          <button type="button" className="brief-run-head" aria-expanded={expanded} onClick={() => toggleExpand(period)}>
                            <ChevronRight className="brief-run-caret" />
                            <span className="brief-run-date">{period.date || period.article_id}</span>
                            <span className="brief-run-meta">{period.meta}</span>
                            <time className="gf-time">{clockOf(period.generated_at)}</time>
                          </button>
                          <button
                            type="button"
                            className="brief-run-del"
                            onClick={() => handleDelete(period)}
                            disabled={deletingId === period.article_id}
                            title="删除这一期"
                          >
                            {deletingId === period.article_id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                          </button>
                        </div>
                        {expanded && (
                          bodyLoadingId === period.article_id
                            ? <p className="pipeline-note">加载正文…</p>
                            : <pre className="brief-run-body">{body == null ? '正文加载失败,重新展开可重试' : (body || '（无正文）')}</pre>
                        )}
                      </div>
                    );
                  })}

                  {total > PAGE_SIZE && (
                    <div className="brief-pager-row">
                      <span className="tiny-meta">共 {total} 期</span>
                      <div className="pager">
                        <button type="button" className="pager-btn" onClick={() => loadPeriods(page - 1)} disabled={page <= 1 || loadingPage} aria-label="上一页">«</button>
                        {pageWindow(page, totalPages).map((item) => (item.ellipsis ? (
                          <span key={item.key} className="pager-ellipsis">…</span>
                        ) : (
                          <button
                            type="button"
                            key={item.key}
                            onClick={() => loadPeriods(item.page)}
                            disabled={loadingPage}
                            className={`pager-btn ${item.page === page ? 'is-on' : ''}`}
                            aria-current={item.page === page ? 'page' : undefined}
                          >
                            {item.page}
                          </button>
                        )))}
                        <button type="button" className="pager-btn" onClick={() => loadPeriods(page + 1)} disabled={page >= totalPages || loadingPage} aria-label="下一页">»</button>
                      </div>
                    </div>
                  )}
                </div>
              </section>
            </div>
          </>
        )}
      </aside>
    </>
  );
}
