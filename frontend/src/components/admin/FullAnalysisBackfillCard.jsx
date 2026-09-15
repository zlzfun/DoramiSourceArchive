import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Calculator, Loader2, Pause, Play, RotateCcw, XCircle } from 'lucide-react';
import {
  cancelFullAnalysisBackfill,
  createFullAnalysisBackfill,
  estimateFullAnalysisBackfill,
  fetchFullAnalysisBackfills,
  pauseFullAnalysisBackfill,
  resumeFullAnalysisBackfill,
  retryFullAnalysisBackfill,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import { usePolling } from '../../hooks/usePolling';
import { TableFoot } from './Pager';
import StaleNotice from './StaleNotice';
import { ThFilter } from './TableTh';
import { formatStamp } from './adminUtils';

const RANGE_OPTIONS = [
  ['30', '最近 30 天'],
  ['7', '最近 7 天'],
  ['90', '最近 90 天'],
  ['365', '最近 365 天'],
  ['all', '全部历史'],
];

const STATUS_META = {
  queued: ['排队中', 'idle'],
  running: ['分析中', 'run'],
  paused: ['已暂停', 'warn'],
  succeeded: ['已完成', 'ok'],
  partial_failed: ['部分失败', 'warn'],
  failed: ['已失败', 'bad'],
  cancelled: ['已取消', 'idle'],
};
const STATUS_FILTERS = [['', '状态'], ['running', '运行中'], ['paused', '已暂停'], ['succeeded', '已完成'], ['partial_failed', '部分失败'], ['failed', '已失败']];
const PAGE_SIZE = 10;
const int = (value) => Number(value || 0).toLocaleString('zh-CN');
const pct = (value) => Math.round(Number(value || 0) * 100);

/**
 * 历史文章完整分析(回填):一行参数(范围 / 策略 / 来源限定 / 估算)+ 任务表。
 * 拍板④:默认档「仅缺失或版本过期」,「全部强制重分析」留作显式强制档(估算 + 确认)。
 * 「创建回填任务」是分析与标签子页唯一的实心 primary(一屏 accent 预算 ≤2)。
 * 任务表:列头状态筛选(本地,表只取近 100 条)、进度条 role=progressbar、行内 rowact
 * (暂停 / 继续 / 重试失败项 / 取消,取消过确认),表脚「共 N 条」诚实计数;活动任务存在时 3s 轮询。
 */
export default function FullAnalysisBackfillCard({ showToast, refreshTick = 0 }) {
  const confirm = useConfirm();
  const [range, setRange] = useState('30');
  const [selection, setSelection] = useState('missing_or_outdated');
  const [sourceFilter, setSourceFilter] = useState('');
  const [estimate, setEstimate] = useState(null);
  const [jobs, setJobs] = useState({ status: 'loading', items: [], total: 0, error: '' });
  const [statusScope, setStatusScope] = useState('');
  const [page, setPage] = useState(1);
  const [estimating, setEstimating] = useState(false);
  const [creating, setCreating] = useState(false);
  const [jobBusy, setJobBusy] = useState('');
  const genRef = useRef(0);

  const payload = useMemo(() => ({
    days: range === 'all' ? null : Number(range),
    selection,
    source_ids: sourceFilter.split(',').map((sourceId) => sourceId.trim()).filter(Boolean),
  }), [range, selection, sourceFilter]);

  const loadJobs = useCallback(async ({ quiet = false } = {}) => {
    const gen = ++genRef.current;
    if (!quiet) setJobs((prev) => ({ ...prev, status: 'loading', error: '' }));
    try {
      const data = await fetchFullAnalysisBackfills({ limit: 100 });
      if (gen === genRef.current) setJobs({ status: 'ok', items: data.items || [], total: Number(data.total ?? (data.items || []).length), error: '' });
    } catch (error) {
      if (gen === genRef.current) setJobs((prev) => ({ ...prev, status: 'error', error: error.message }));
    }
  }, []);

  useEffect(() => { loadJobs(); }, [loadJobs]);
  useEffect(() => {
    if (refreshTick > 0) loadJobs({ quiet: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应刷新脉冲
  }, [refreshTick]);
  useEffect(() => { setEstimate(null); }, [payload]);
  useEffect(() => { setPage(1); }, [statusScope]);

  const anyActive = jobs.items.some((job) => ['queued', 'running'].includes(job.status));
  const pollJobs = useCallback(() => loadJobs({ quiet: true }), [loadJobs]);
  usePolling(pollJobs, 3000, { immediate: false, enabled: anyActive });

  const calculate = async () => {
    setEstimating(true);
    try { setEstimate(await estimateFullAnalysisBackfill(payload)); }
    catch (error) { showToast(error.message, 'error'); }
    finally { setEstimating(false); }
  };

  const create = async () => {
    if (!estimate?.ready) return;
    const scope = payload.days == null ? '全部历史文章' : `最近 ${payload.days} 天文章`;
    const message = selection === 'all'
      ? `将强制重新分析${scope}中的 ${int(estimate.article_count)} 篇文章，重算评分、摘要和标签，包括当前版本已成功的文章。新文章始终优先。`
      : `将补充分析${scope}中缺失或版本过期的 ${int(estimate.article_count)} 篇文章。新文章始终优先。`;
    if (!(await confirm({ title: '创建回填任务', message, confirmText: '开始', tone: 'primary' }))) return;
    setCreating(true);
    try {
      const job = await createFullAnalysisBackfill(payload);
      showToast(`已创建回填任务 #${job.job_id}`, 'success');
      setEstimate(null);
      await loadJobs({ quiet: true });
    } catch (error) { showToast(error.message, 'error'); }
    finally { setCreating(false); }
  };

  const act = async (action, job) => {
    if (action === 'cancel' && !(await confirm({
      title: `取消任务 #${job.job_id}`,
      message: '取消后不再派发剩余文章，已经开始的单篇分析会正常收尾。',
      confirmText: '取消任务',
    }))) return;
    setJobBusy(`${action}-${job.job_id}`);
    try {
      if (action === 'pause') await pauseFullAnalysisBackfill(job.job_id);
      if (action === 'resume') await resumeFullAnalysisBackfill(job.job_id);
      if (action === 'cancel') await cancelFullAnalysisBackfill(job.job_id);
      if (action === 'retry') await retryFullAnalysisBackfill(job.job_id);
      const verbs = { pause: '暂停', resume: '继续', cancel: '取消', retry: '重新排队失败项' };
      showToast(`已${verbs[action]}任务 #${job.job_id}`, 'success');
      await loadJobs({ quiet: true });
    } catch (error) { showToast(error.message, 'error'); }
    finally { setJobBusy(''); }
  };

  const visible = statusScope ? jobs.items.filter((job) => job.status === statusScope) : jobs.items;
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageItems = visible.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const truncated = jobs.total > jobs.items.length;

  return (
    <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
      <div className="card-pad backfill-head">
        <div className="card-head">
          <span className="card-title">历史文章完整分析</span>
          {jobs.items.length > 0 && <StaleNotice status={jobs.status} error={jobs.error} onRetry={() => loadJobs()} label="任务" />}
        </div>
        <div className="backfill-row">
          <select className="form-input form-input-inline" value={range} onChange={(event) => setRange(event.target.value)} aria-label="回填时间范围">
            {RANGE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select className="form-input form-input-inline" value={selection} onChange={(event) => setSelection(event.target.value)} aria-label="回填选择策略">
            <option value="missing_or_outdated">仅缺失或版本过期</option>
            <option value="all">全部强制重分析</option>
          </select>
          <input
            type="text"
            className="form-input form-input-inline backfill-source"
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            aria-label="限定来源 source_id"
            placeholder="限定来源 source_id，逗号分隔（可选）"
          />
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" disabled={estimating || creating} onClick={calculate}>
            {estimating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Calculator className="h-3.5 w-3.5" />} 估算
          </button>
          {estimate && (
            <span className="tiny-meta tabular-nums" title={estimate.blockers?.length ? `开始前处理：${estimate.blockers.join('；')}` : undefined}>
              {int(estimate.article_count)} 篇 · {int(estimate.source_count)} 个来源 · 约 {int(estimate.estimated_initial_llm_calls)} 次调用 · 目录 v{estimate.taxonomy_version}
              {estimate.blockers?.length > 0 && <> · <span className="stamp stamp-warn">{estimate.blockers.length} 项阻塞</span></>}
            </span>
          )}
          <button
            type="button"
            className="action-button action-button-primary min-h-[32px] px-3 text-xs ml-auto"
            disabled={!estimate?.ready || creating}
            title={!estimate ? '先估算范围' : !estimate.ready ? (estimate.blockers || []).join('；') : undefined}
            onClick={create}
          >
            {creating && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 创建回填任务
          </button>
        </div>
      </div>
      {jobs.status === 'error' && jobs.items.length === 0 ? (
        <p className="acct-empty tiny-meta" role="alert">{jobs.error} · <button type="button" className="kpi-sub-link" onClick={() => loadJobs()}>重试</button></p>
      ) : (
        <>
          <div className="acct-scroll">
            <table className="acct-table is-fixed">
              <thead>
                <tr>
                  <th className="acct-th" style={{ width: 90 }}>任务</th>
                  <th className="acct-th" style={{ width: 160 }}>范围</th>
                  <th className="acct-th" style={{ width: 150 }}>策略</th>
                  <ThFilter label="状态" value={statusScope} onChange={setStatusScope} options={STATUS_FILTERS} width={110} />
                  <th className="acct-th">进度</th>
                  <th className="acct-th" style={{ width: 130 }}>创建</th>
                  <th className="acct-th" aria-label="操作" style={{ width: 112 }} />
                </tr>
              </thead>
              <tbody>
                {pageItems.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="acct-empty tiny-meta">
                      {jobs.status === 'loading' ? '正在加载任务…' : statusScope ? (
                        <>没有该状态的任务。<button type="button" className="kpi-sub-link" onClick={() => setStatusScope('')}>清除筛选</button></>
                      ) : '还没有回填任务'}
                    </td>
                  </tr>
                ) : pageItems.map((job) => {
                  const [statusLabel, tone] = STATUS_META[job.status] || [`未知状态(${job.status})`, 'idle'];
                  const counts = job.counts || {};
                  const busy = jobBusy.endsWith(`-${job.job_id}`);
                  const progress = pct(job.progress);
                  return (
                    <tr key={job.job_id} className="acct-row is-static">
                      <td><span className="acct-mono">#{job.job_id}</span></td>
                      <td><span className="tiny-meta">{job.days == null ? '全部历史' : `最近 ${job.days} 天`}{job.source_count ? ` · ${job.source_count} 源` : ''}</span></td>
                      <td><span className="tiny-meta">{job.selection === 'all' ? '全部强制重分析' : '仅缺失或过期'}</span></td>
                      <td><span className={`stamp stamp-${tone}`} title={job.last_error || undefined}>{statusLabel}</span></td>
                      <td>
                        <span className="prog-cell">
                          <span className="prog" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-label={`任务进度 ${progress}%`}>
                            <i style={{ width: `${progress}%` }} />
                          </span>
                          <span className="acct-mono">
                            {int(counts.finished)} / {int(counts.total)}
                            {Number(counts.failed) > 0 && <> · 失败 <span className="is-bad-ink">{int(counts.failed)}</span></>}
                          </span>
                        </span>
                      </td>
                      <td><span className="acct-mono">{formatStamp(job.created_at)}</span></td>
                      <td>
                        <span className="rowacts">
                          {busy ? <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span> : (
                            <>
                              {['queued', 'running'].includes(job.status) && (
                                <button type="button" className="rowact-btn" title="暂停" aria-label={`暂停任务 #${job.job_id}`} onClick={() => act('pause', job)}><Pause /></button>
                              )}
                              {job.status === 'paused' && (
                                <button type="button" className="rowact-btn" title="继续" aria-label={`继续任务 #${job.job_id}`} onClick={() => act('resume', job)}><Play /></button>
                              )}
                              {job.status === 'partial_failed' && (
                                <button type="button" className="rowact-btn" title="重试失败项" aria-label={`重试任务 #${job.job_id} 的失败项`} onClick={() => act('retry', job)}><RotateCcw /></button>
                              )}
                              {['queued', 'running', 'paused'].includes(job.status) && (
                                <button type="button" className="rowact-btn is-danger" title="取消（需确认；已开始的单篇正常收尾）" aria-label={`取消任务 #${job.job_id}`} onClick={() => act('cancel', job)}><XCircle /></button>
                              )}
                            </>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <TableFoot
            total={visible.length}
            page={safePage}
            pageSize={PAGE_SIZE}
            onPage={setPage}
            extra={truncated ? `仅载入最近 ${jobs.items.length} 条（共 ${int(jobs.total)} 条）` : null}
          />
        </>
      )}
    </section>
  );
}
