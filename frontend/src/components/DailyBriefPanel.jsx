import { useEffect, useState } from 'react';
import { Brain, ChevronDown, Loader2, Newspaper, Trash2 } from 'lucide-react';
import DailyBriefFlow from './DailyBriefFlow';
import {
  getLLMConfig,
  getDailyBriefConfig,
  saveDailyBriefConfig,
  generateDailyBrief,
  getDailyBriefProgress,
  fetchArticles,
  deleteArticle,
} from '../api';
import { useConfirm } from '../hooks/useConfirm';

const INPUT_CLS = 'w-full rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-2.5 text-sm';
const DAILY_BRIEF_SOURCE_ID = 'dorami_daily_brief';

// 生成阶段 → 中文标签（与后端 set_progress 的 phase 对齐）
const PHASE_LABELS = {
  collecting: '筛选候选内容',
  mapping: '概括打分',
  selecting: '择优排序',
  reducing: '汇编日报正文',
  persisting: '写入与分发',
  done: '完成',
  empty: '无新增内容',
  error: '生成失败',
};

/* 后端 LLM 配置 + 每日日报管理。日报是「对归档内容的下游加工/分发」，
   故归入接入集成；管理控件仅对管理员（collector + admin）开放。 */
export default function DailyBriefPanel({ showToast, collectorEnabled = false, isAdmin = false }) {
  const canManage = collectorEnabled && isAdmin;
  const confirm = useConfirm();

  // ── LLM 配置（只读概览；编辑入口已迁至「运维管理」面板） ──
  const [llmStatus, setLlmStatus] = useState(null);

  // ── 日报配置 ──
  const [briefConfig, setBriefConfig] = useState(null);
  const [cron, setCron] = useState('30 8 * * *');
  const [topN, setTopN] = useState(12);
  const [enabled, setEnabled] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState(null);  // 生成过程中的实时阶段

  // ── 历史日报 ──
  const [history, setHistory] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const loadLlm = () => getLLMConfig().then(setLlmStatus).catch(() => {});

  const loadBrief = () => getDailyBriefConfig()
    .then(d => { setBriefConfig(d); setCron(d.cron || '30 8 * * *'); setTopN(d.top_n ?? 12); setEnabled(Boolean(d.enabled)); })
    .catch(() => {});

  // 历史日报直接按来源拉取（不带 exclude_source_ids，故能取到日报本体含正文）。
  const loadHistory = () => fetchArticles({ source_id: DAILY_BRIEF_SOURCE_ID }, 60, 0, true)
    .then(d => setHistory(d.items || []))
    .catch(() => {});

  useEffect(() => {
    if (!canManage) return;
    loadLlm();
    loadBrief();
    loadHistory();
  }, [canManage]);

  // 生成进行中才轮询后端实时阶段；组件卸载 / 生成结束时由 cleanup 清除，
  // 避免遗留定时器在退出登录后继续打 collector 端点（/api/daily-brief/progress）。
  useEffect(() => {
    if (!generating) return undefined;
    const poll = setInterval(() => {
      getDailyBriefProgress().then(setProgress).catch(() => {});
    }, 1200);
    return () => clearInterval(poll);
  }, [generating]);

  const briefMeta = (record) => {
    try { return JSON.parse(record.extensions_json || '{}'); } catch { return {}; }
  };

  const handleDelete = async (id) => {
    if (!(await confirm('确认删除这篇日报？下游订阅将不再能拉取到它。若删除的是最新一期，增量游标会自动回退到生成它之前，便于重新生成。'))) return;
    setDeletingId(id);
    try {
      await deleteArticle(id);
      if (expandedId === id) setExpandedId(null);
      showToast('日报已删除', 'success');
      loadHistory();
      loadBrief();  // 游标可能已随删除回退，刷新显示
    } catch (error) {
      showToast(error.message || '删除失败', 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const handleResetCursor = async () => {
    if (!(await confirm('重置增量游标后，下次生成会从近期归档（最多 120 篇最新内容）重做，用于重做 / 补生成。确认重置？'))) return;
    try {
      await saveDailyBriefConfig({ cursor: '' });
      showToast('增量游标已重置', 'success');
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
      showToast(next ? '已开启每日日报定时生成' : '已关闭每日日报', 'success');
      loadBrief();
    } catch (error) {
      setEnabled(!next);
      showToast(error.message || '设置失败', 'error');
    }
  };

  // Cron + 精选条数合并为一次保存（定时开关仍即时生效，见 handleToggle）。
  const handleSaveSettings = async () => {
    const n = Number(topN);
    if (!Number.isInteger(n) || n < 1 || n > 50) {
      showToast('精选条数需为 1–50 的整数', 'error');
      return;
    }
    const c = cron.trim();
    if (!c) {
      showToast('Cron 表达式不能为空', 'error');
      return;
    }
    try {
      await saveDailyBriefConfig({ cron: c, top_n: n });
      showToast('日报设置已保存', 'success');
      loadBrief();
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    }
  };

  const handleGenerate = async () => {
    // 生成是同步长请求；实时阶段轮询由上方 generating 驱动的 effect 负责启停。
    setGenerating(true);
    setProgress({ phase: 'collecting', message: '正在启动…', done: 0, total: 0 });
    try {
      const r = await generateDailyBrief({});
      if (r.status === 'empty') showToast('暂无新增内容可生成日报', 'info');
      else showToast(`日报已生成：${r.report_date} · 收录 ${r.articles_count} 条`, 'success');
      loadBrief();
      loadHistory();
    } catch (error) {
      showToast(error.message || '生成失败', 'error');
    } finally {
      setProgress(null);
      setGenerating(false);
    }
  };

  // 非管理员（受限读者）：只给一个订阅指引，不暴露任何管理控件。
  if (!canManage) {
    return (
      <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
          <div className="w-1 h-5 rounded-full bg-amber-500" />
          <h3 className="section-title">AI 资讯日报</h3>
        </div>
        <div className="p-6">
          <p className="text-sm text-slate-500 leading-relaxed">
            本站点支持由后端大模型每日自动生成 AI 资讯日报。前往「阅读器」的「发现更多来源」一键订阅
            <span className="font-bold text-slate-700"> 🤖 哆啦美·AI资讯日报</span>，即可通过订阅 / 个人聚合接口获取每日日报。
          </p>
        </div>
      </div>
    );
  }

  const lastRun = briefConfig?.last_run;
  const statusLabel = { success: '成功', empty: '无内容', failed: '失败' }[lastRun?.status] || lastRun?.status || '—';
  const statusColor = lastRun?.status === 'success' ? 'text-emerald-500' : lastRun?.status === 'failed' ? 'text-rose-500' : 'text-slate-500';

  return (
    <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
      <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
        <div className="w-1 h-5 rounded-full bg-amber-500" />
        <h3 className="section-title">AI 资讯日报</h3>
        <span
          className="ml-auto inline-flex items-center gap-1.5 rounded-[var(--r-control)] border border-[var(--dorami-border)] px-2.5 py-1.5 text-xs font-bold text-slate-500"
          title="日报与阅读器 AI 共用的大模型，在「运维管理」面板统一配置"
        >
          <Brain className="h-3.5 w-3.5" />
          {llmStatus?.api_key_set ? (llmStatus.model || '模型已配置') : '模型未配置'}
          <span className={`ml-0.5 h-1.5 w-1.5 rounded-full ${llmStatus?.api_key_set ? 'bg-emerald-500' : 'bg-amber-500'}`} />
        </span>
      </div>

      <div className="p-6 space-y-6">
        {/* ── 日报生成 ── */}
        <div>
          <div className="flex items-center gap-2 mb-4">
            <Newspaper className="w-4 h-4 text-amber-500" />
            <p className="text-sm font-bold text-slate-700">日报生成</p>
            <span className="tiny-meta">汇总择优近期归档内容，生成「🤖 哆啦美·AI资讯日报」</span>
          </div>

          {/* 焦点：立即生成 + 最近一次状态 */}
          <div className="rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50 to-orange-50/40 p-4">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-bold text-slate-800">立即生成今天的日报</p>
                <p className="tiny-meta mt-0.5">手动汇总择优近期内容，耗时数十秒到数分钟。</p>
              </div>
              <button onClick={handleGenerate} disabled={generating} className="action-button action-button-primary shrink-0">
                {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Newspaper className="h-4 w-4" />} 立即生成
              </button>
            </div>

            {/* 生成过程中的实时阶段 */}
            {generating && (
              <div className="mt-3 rounded-[var(--r-control)] border border-amber-200/70 bg-white/70 dark:bg-[var(--dorami-surface)] px-3 py-2.5">
                <div className="flex items-center gap-2 text-xs">
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-amber-600" />
                  <span className="font-bold text-slate-700">{PHASE_LABELS[progress?.phase] || '处理中…'}</span>
                  {progress?.total > 0 && <span className="font-mono text-slate-500">{progress.done}/{progress.total}</span>}
                  {progress?.message && progress.total === 0 && <span className="text-slate-500">{progress.message}</span>}
                </div>
                {progress?.total > 0 && (
                  <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-amber-100">
                    <div
                      className="h-full rounded-full bg-amber-500 transition-all duration-300"
                      style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }}
                    />
                  </div>
                )}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-amber-200/60 pt-3 text-xs">
              <span className="font-medium text-slate-500">最近一次</span>
              {lastRun ? (
                <>
                  <span className={`font-bold ${statusColor}`}>{statusLabel}</span>
                  {lastRun.report_date && <span className="text-slate-500">· {lastRun.report_date}</span>}
                  {typeof lastRun.articles_count === 'number' && lastRun.status === 'success' && <span className="text-slate-500">· 收录 {lastRun.articles_count} 条</span>}
                </>
              ) : <span className="text-slate-500">尚未运行</span>}
              {lastRun?.error_message && <span className="w-full text-rose-500">错误：{lastRun.error_message}</span>}
            </div>
          </div>

          {/* 设置：自动调度 | 生成参数（Cron 与精选条数共用底部一枚「保存设置」） */}
          <div className="mt-3">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              {/* 自动调度 */}
              <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] p-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-bold text-slate-700">定时生成</span>
                  <button
                    onClick={handleToggle}
                    role="switch"
                    aria-checked={enabled}
                    aria-label="定时生成开关"
                    className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${enabled ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-[var(--dorami-raised)]'}`}
                  >
                    <span className={`inline-block h-4 w-4 rounded-full bg-[var(--dorami-surface)] shadow transition-transform ${enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                  </button>
                </div>
                <p className="tiny-meta mt-1">开启后按 Cron 自动生成（默认每天 8:30，排在采集之后）。</p>
                <div className="mt-3">
                  <p className="form-label">Cron 表达式（5 段）</p>
                  <input value={cron} onChange={e => setCron(e.target.value)} placeholder="30 8 * * *" className={`${INPUT_CLS} font-mono`} />
                </div>
              </div>

              {/* 生成参数 */}
              <div className="flex flex-col rounded-[var(--r-card)] border border-[var(--dorami-border)] p-4">
                <p className="text-sm font-bold text-slate-700">生成参数</p>
                <div className="mt-3">
                  <p className="form-label">精选条数（1–50）</p>
                  <input type="number" min="1" max="50" step="1" value={topN} onChange={e => setTopN(e.target.value)} className={INPUT_CLS} />
                  <span className="tiny-meta">按重要性取分数最高的前 N 条（正文与导出 JSON 同步）。</span>
                </div>
                <div className="mt-3 flex items-center justify-between gap-2 border-t border-[var(--dorami-border)] pt-3 text-xs">
                  <span className="font-medium text-slate-500">增量游标</span>
                  <span className="flex items-center gap-2">
                    <code className="font-mono text-slate-500">{briefConfig?.cursor ? briefConfig.cursor.slice(0, 19) : '（空）'}</code>
                    <button onClick={handleResetCursor} className="rounded-md px-2 py-0.5 micro-label text-slate-500 hover:bg-slate-100 hover:text-slate-700" title="重置增量游标（用于重做/补生成）">
                      重置
                    </button>
                  </span>
                </div>
              </div>
            </div>

            {/* 统一保存：一次落库 Cron + 精选条数 */}
            <div className="mt-3 flex justify-end">
              <button onClick={handleSaveSettings} className="action-button action-button-secondary text-xs">保存日报设置</button>
            </div>
          </div>
        </div>

        <div className="border-t border-[var(--dorami-border)]" />

        {/* ── 生成原理（流程图 + 提示词） ── */}
        <DailyBriefFlow showToast={showToast} canManage={canManage} />

        <div className="border-t border-[var(--dorami-border)]" />

        {/* ── 历史日报 ── */}
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Newspaper className="w-4 h-4 text-slate-500" />
            <p className="text-sm font-bold text-slate-700">历史日报</p>
            <span className="tiny-meta">已生成的日报（不在「知识台账」中展示，在此查看与管理）</span>
          </div>

          {history.length === 0 ? (
            <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-6 text-center text-xs text-slate-500">
              还没有生成过日报。配置好模型后点「立即生成」试试。
            </p>
          ) : (
            <div className="divide-y divide-[var(--dorami-border)] rounded-[var(--r-card)] border border-[var(--dorami-border)] overflow-hidden">
              {history.map(record => {
                const meta = briefMeta(record);
                const open = expandedId === record.id;
                return (
                  <div key={record.id} className="bg-[var(--dorami-soft)]">
                    <div className="flex items-center gap-3 px-4 py-3">
                      <button
                        onClick={() => setExpandedId(open ? null : record.id)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <ChevronDown className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
                        <span className="truncate text-sm font-bold text-slate-700">{record.publish_date || record.id}</span>
                        {typeof meta.articles_count === 'number' && (
                          <span className="tiny-meta shrink-0">· 收录 {meta.articles_count} 条</span>
                        )}
                        {meta.llm_model && <span className="tiny-meta shrink-0 hidden sm:inline">· {meta.llm_model}</span>}
                      </button>
                      <button
                        onClick={() => handleDelete(record.id)}
                        disabled={deletingId === record.id}
                        className="shrink-0 rounded-[var(--r-control)] p-1.5 text-slate-500 hover:bg-rose-50 hover:text-rose-500"
                        title="删除该日报"
                      >
                        {deletingId === record.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                      </button>
                    </div>
                    {open && (
                      <div className="border-t border-[var(--dorami-border)] bg-[var(--dorami-surface)] px-4 py-3">
                        <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700 font-mono">
                          {record.content || '（无正文）'}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
