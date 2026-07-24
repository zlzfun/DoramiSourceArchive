import { useCallback, useEffect, useState } from 'react';
import { ChevronRight, Loader2, Play, Trash2 } from 'lucide-react';
import {
  getDailyBriefConfig,
  saveDailyBriefConfig,
  generateDailyBrief,
  getDailyBriefProgress,
  fetchArticles,
  fetchReaderSources,
  deleteArticle,
} from '../api';
import { useConfirm } from '../hooks/useConfirm';
import { usePolling } from '../hooks/usePolling';

const DAILY_BRIEF_SOURCE_ID = 'dorami_daily_brief';

// 生成流水线五段:收集 / 摘要 / 去重 / 精选 / 成稿。
// 后端 progress phase(collecting/mapping/selecting/reducing/persisting/done)映射到段索引;
// selecting 阶段内含「同事件去重 + 择优」,取「精选」为进行段、「去重」标记已过。
const PIPE_SEGMENTS = ['收集', '摘要', '去重', '精选', '成稿'];
const PHASE_NOW = { collecting: 0, mapping: 1, selecting: 3, reducing: 4, persisting: 4, done: 5 };
const PHASE_NOTE = {
  collecting: '筛选候选内容',
  mapping: '概括打分',
  selecting: '同事件去重与择优',
  reducing: '汇编日报正文',
  persisting: '写入与分发',
  done: '已完成',
  empty: '暂无新增内容',
  error: '生成失败',
};

/* 每日 AI 资讯日报:定时配置 / 手动生成 / 近期日报,三段 hairline 卡(surface-card.brief-card)。
   管理控件仅对管理员(collector + admin)开放;模型 chip 已移至页头(DailyBriefTab)。 */
export default function DailyBriefPanel({ showToast, collectorEnabled = false, isAdmin = false }) {
  const canManage = collectorEnabled && isAdmin;
  const confirm = useConfirm();

  const [briefConfig, setBriefConfig] = useState(null);
  const [cron, setCron] = useState('30 8 * * *');
  const [topN, setTopN] = useState(12);
  const [enabled, setEnabled] = useState(false);
  // 源范围手工名单(用户拍板):all=全部源(后端 source_ids 空);custom=只取勾选名单。
  // 新增源默认不进名单——高噪即时源的取舍交给名单 + LLM 打分,不做类型规则过滤。
  const [scopeMode, setScopeMode] = useState('all');
  const [scopeIds, setScopeIds] = useState(() => new Set());
  const [sourceCatalog, setSourceCatalog] = useState(null); // null=未加载
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState(null);
  const [stepIndex, setStepIndex] = useState(0);   // 当前流水线段索引(0..5),error 时停在最后已知段

  const [history, setHistory] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const loadBrief = () => getDailyBriefConfig()
    .then(d => {
      setBriefConfig(d); setCron(d.cron || '30 8 * * *'); setTopN(d.top_n ?? 12); setEnabled(Boolean(d.enabled));
      const ids = Array.isArray(d.source_ids) ? d.source_ids : null;
      setScopeMode(ids && ids.length > 0 ? 'custom' : 'all');
      setScopeIds(new Set(ids || []));
    })
    .catch(() => {});

  // 源目录懒加载:切到自定名单时才拉(注册源 ∪ 归档源并集,与阅读器目录同一接口)
  const ensureSourceCatalog = () => {
    if (sourceCatalog !== null) return;
    fetchReaderSources()
      .then(d => setSourceCatalog((d.sources || []).filter(s => s.source_id !== DAILY_BRIEF_SOURCE_ID)))
      .catch(() => setSourceCatalog([]));
  };

  const toggleScopeId = (sourceId) => {
    setScopeIds(prev => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId); else next.add(sourceId);
      return next;
    });
  };

  const loadHistory = () => fetchArticles({ source_id: DAILY_BRIEF_SOURCE_ID }, 60, 0, true)
    .then(d => setHistory(d.items || []))
    .catch(() => {});

  useEffect(() => {
    if (!canManage) return;
    loadBrief();
    loadHistory();
  }, [canManage]);

  const pollProgress = useCallback(async () => {
    const p = await getDailyBriefProgress();
    setProgress(p);
    const idx = PHASE_NOW[p?.phase];
    if (idx !== undefined) setStepIndex(idx);   // error/empty(idx undefined)不动,停在最后已知段
  }, []);

  // 生成进行中才轮询后端实时阶段;卸载/结束时清除,避免退出登录后仍打 collector 端点。
  // 生成动画需要持续走字,故不随页面隐藏暂停。
  usePolling(pollProgress, 1200, { immediate: false, pauseWhenHidden: false, enabled: generating });

  const briefMeta = (record) => {
    try { return JSON.parse(record.extensions_json || '{}'); } catch { return {}; }
  };

  const handleDelete = async (id) => {
    if (!(await confirm('确认删除这篇日报?下游订阅将不再能拉取到它。若删除的是最新一期,增量游标会自动回退到生成它之前,便于重新生成。'))) return;
    setDeletingId(id);
    try {
      await deleteArticle(id);
      if (expandedId === id) setExpandedId(null);
      showToast('已删除 日报', 'success');
      loadHistory();
      loadBrief();
    } catch (error) {
      showToast(error.message || '删除失败', 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const handleResetCursor = async () => {
    if (!(await confirm('重置增量游标后,下次生成会从近期归档(最多 120 篇最新内容)重做,用于重做 / 补生成。确认重置?'))) return;
    try {
      await saveDailyBriefConfig({ cursor: '' });
      showToast('已重置 增量游标', 'success');
      loadBrief();
    } catch (error) {
      showToast(error.message || '重置失败', 'error');
    }
  };

  const handleToggle = async () => {
    const next = !enabled;
    setEnabled(next);
    try {
      await saveDailyBriefConfig({ enabled: next });
      showToast(next ? '已开启 每日定时生成' : '已关闭 每日定时生成', 'success');
      loadBrief();
    } catch (error) {
      setEnabled(!next);
      showToast(error.message || '设置失败', 'error');
    }
  };

  const handleSaveSettings = async () => {
    const n = Number(topN);
    if (!Number.isInteger(n) || n < 1 || n > 50) {
      showToast('精选条数需为 1–50 的整数', 'error');
      return;
    }
    const c = cron.trim();
    if (!c) {
      showToast('Cron 表达式不能为空,请填写 5 段 cron', 'error');
      return;
    }
    if (scopeMode === 'custom' && scopeIds.size === 0) {
      showToast('自定名单至少勾选一个来源(或切回「全部来源」)', 'error');
      return;
    }
    try {
      // 全部来源 → 传 [] 清空名单(后端语义:空=全部);自定 → 传勾选集合
      await saveDailyBriefConfig({ cron: c, top_n: n, source_ids: scopeMode === 'custom' ? [...scopeIds] : [] });
      showToast('已保存 日报配置', 'success');
      loadBrief();
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    setStepIndex(0);
    setProgress({ phase: 'collecting', message: '正在启动…', done: 0, total: 0 });
    try {
      const r = await generateDailyBrief({});
      if (r.status === 'empty') showToast('暂无新增内容可生成日报', 'info');
      else showToast(`已生成 日报 ${r.report_date} · 收录 ${r.articles_count} 条`, 'success');
      loadBrief();
      loadHistory();
    } catch (error) {
      showToast(error.message || '生成失败', 'error');
    } finally {
      setProgress(null);
      setGenerating(false);
    }
  };

  if (!canManage) return null;

  const phase = progress?.phase;
  const isErr = phase === 'error';
  const activeIndex = stepIndex;
  const terminal = phase === 'done' || phase === 'empty' || phase === 'error';
  let note = PHASE_NOTE[phase] || '处理中';
  if (progress?.total > 0) note += ` · ${progress.done}/${progress.total} 篇`;
  if (!terminal) note += '…';

  const lastRun = briefConfig?.last_run;
  const lastFailed = !generating && lastRun?.status === 'failed';
  const cursorVal = briefConfig?.cursor ? briefConfig.cursor.slice(0, 19) : '（空）';

  return (
    <section className="surface-card brief-card">
      {/* ── 定时配置 ── */}
      <div className="brief-col">
        <div className="brief-col-title">定时配置</div>
        {/* 开关与保存同一行:开关即时生效,保存针对下方 cron/条数 的编辑 */}
        <div className="brief-switch-row">
          <span className="brief-switch-main">
            <button
              type="button"
              role="switch"
              aria-checked={enabled}
              aria-label="启用每日定时生成"
              onClick={handleToggle}
              className={`ledger-switch ${enabled ? 'is-on' : ''}`}
            />
            每日自动生成
          </span>
          <button onClick={handleSaveSettings} className="action-button action-button-quiet min-h-[28px] px-3 text-xs">保存配置</button>
        </div>
        <div className="brief-field">
          <label className="form-label" htmlFor="brief-cron">cron 表达式（5 段）</label>
          <input id="brief-cron" value={cron} onChange={e => setCron(e.target.value)} placeholder="30 8 * * *" className="form-input font-mono" />
        </div>
        <div className="brief-field">
          <label className="form-label" htmlFor="brief-topn">每期条数（Top N，1–50）</label>
          <input id="brief-topn" type="number" min="1" max="50" step="1" value={topN} onChange={e => setTopN(e.target.value)} className="form-input" />
        </div>

        {/* ── 源范围:手工名单(全部来源 ⇄ 自定名单) ── */}
        <div className="brief-field">
          <span className="form-label">日报源范围</span>
          <div className="mini-seg" role="tablist" aria-label="日报源范围">
            {[['all', '全部来源'], ['custom', '自定名单']].map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={scopeMode === value}
                onClick={() => { setScopeMode(value); if (value === 'custom') ensureSourceCatalog(); }}
                className={`mini-seg-btn ${scopeMode === value ? 'is-on' : ''}`}
              >
                {label}
              </button>
            ))}
          </div>
          {scopeMode === 'custom' && (
            <div className="brief-scope-list" role="group" aria-label="日报来源名单">
              {sourceCatalog === null ? (
                <p className="tiny-meta px-2 py-1.5">加载来源目录…</p>
              ) : sourceCatalog.length === 0 ? (
                <p className="tiny-meta px-2 py-1.5">来源目录为空</p>
              ) : (
                sourceCatalog.map(s => (
                  <label key={s.source_id} className="brief-scope-row">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5"
                      checked={scopeIds.has(s.source_id)}
                      onChange={() => toggleScopeId(s.source_id)}
                    />
                    <span className="brief-scope-name">{s.name || s.source_id}</span>
                    <span className="tiny-meta shrink-0">{s.count || 0} 篇{s.shape === 'bulletin' ? ' · 动态' : ''}</span>
                  </label>
                ))
              )}
            </div>
          )}
          <p className="tiny-meta mt-1">
            {scopeMode === 'custom'
              ? `候选只取名单内来源(已选 ${scopeIds.size} 个);新增来源默认不进名单`
              : '候选取全部来源(新增来源自动纳入)'}
          </p>
        </div>

        <div className="brief-cursor-row">
          <span className="tiny-meta shrink-0">增量游标</span>
          <code className="brief-cursor-val" title={cursorVal}>{cursorVal}</code>
          <button onClick={handleResetCursor} className="brief-cursor-reset" title="重置增量游标（用于重做 / 补生成）">重置</button>
        </div>
      </div>

      {/* ── 近期日报（含手动生成入口） ── */}
      <div className="brief-col">
        <div className="brief-col-head">
          <div className="brief-col-title">近期日报</div>
          <button onClick={handleGenerate} disabled={generating} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {generating ? '生成中…' : '立即生成日报'}
          </button>
        </div>

        {generating && (
          <>
            <div className="ledger-pipeline" aria-label="生成阶段">
              {PIPE_SEGMENTS.map((label, i) => {
                let cls = 'ledger-pipeline-step';
                if (i < activeIndex) cls += ' is-done';
                else if (i === activeIndex) cls += isErr ? ' is-now is-err' : ' is-now';
                return <span key={label} className={cls}>{label}</span>;
              })}
            </div>
            <div className={`pipeline-note ${isErr ? 'is-err' : ''}`}>{note}</div>
          </>
        )}

        {lastFailed && (
          <p className="pipeline-note is-err mt-3">最近一次生成失败{lastRun?.error_message ? `：${lastRun.error_message}` : ''}</p>
        )}

        <details className="scope-note">
          <summary>取材与去重口径</summary>
          <p>候选取自上次成功生成之后新入库的文章（游标不回退）；同日同事件聚类合并，近几日正文注入 reduce 阶段做跨日语义去重。生成在后台任务中执行，可离开本页。</p>
        </details>

        {history.length === 0 ? (
          <p className="brief-empty">还没有生成过日报。配置好模型后点「立即生成日报」试试。</p>
        ) : (
          history.map(record => {
            const meta = briefMeta(record);
            const open = expandedId === record.id;
            const metaBits = [
              typeof meta.articles_count === 'number' ? `收录 ${meta.articles_count} 条` : null,
              meta.llm_model || null,
            ].filter(Boolean);
            return (
              <div key={record.id} className="brief-run">
                <div className="brief-run-line">
                  <button
                    type="button"
                    className="brief-run-head"
                    aria-expanded={open}
                    onClick={() => setExpandedId(open ? null : record.id)}
                  >
                    <ChevronRight className="brief-run-caret" />
                    <span className="brief-run-date">{record.publish_date || record.id}</span>
                    <span className="brief-run-meta">{metaBits.join(' · ')}</span>
                    <span className="stamp stamp-ok">已生成</span>
                  </button>
                  <button
                    type="button"
                    className="brief-run-del"
                    onClick={() => handleDelete(record.id)}
                    disabled={deletingId === record.id}
                    title="删除该日报"
                  >
                    {deletingId === record.id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                  </button>
                </div>
                {open && <pre className="brief-run-body">{record.content || '（无正文）'}</pre>}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}
