import { useCallback, useEffect, useMemo, useState } from 'react';
import { Calculator, Loader2, Pause, Play, RefreshCw, RotateCcw, XCircle } from 'lucide-react';
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

const RANGE_OPTIONS = [
  ['7', '最近 7 天'],
  ['30', '最近 30 天'],
  ['90', '最近 90 天'],
  ['365', '最近 365 天'],
  ['all', '全部历史'],
];

const STATUS_META = {
  queued: ['排队中', 'stamp-idle'],
  running: ['分析中', 'stamp-run'],
  paused: ['已暂停', 'stamp-warn'],
  succeeded: ['已完成', 'stamp-ok'],
  partial_failed: ['部分失败', 'stamp-warn'],
  failed: ['已失败', 'stamp-bad'],
  cancelled: ['已取消', 'stamp-idle'],
};

const int = (value) => Number(value || 0).toLocaleString('zh-CN');
const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`;

function JobRow({ job, busy, onAction }) {
  const [statusLabel, statusClass] = STATUS_META[job.status] || [job.status, 'stamp-idle'];
  const counts = job.counts || {};
  return (
    <div className="rounded-[var(--r-control)] border border-[var(--dorami-border)] p-3">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="body-text">任务 #{job.job_id}</strong>
            <span className={`stamp ${statusClass}`}>{statusLabel}</span>
            <span className="micro-label">taxonomy v{job.target_taxonomy_version}</span>
          </div>
          <p className="tiny-meta mt-1">
            {job.days == null ? '全部历史' : `最近 ${job.days} 天`} · {job.selection === 'all' ? '强制重分析' : '仅缺失或过期'} · {job.target_prompt_version}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5">
          {['queued', 'running'].includes(job.status) && (
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={busy} onClick={() => onAction('pause', job)}><Pause className="h-3.5 w-3.5" /> 暂停</button>
          )}
          {job.status === 'paused' && (
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={busy} onClick={() => onAction('resume', job)}><Play className="h-3.5 w-3.5" /> 继续</button>
          )}
          {job.status === 'partial_failed' && (
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={busy} onClick={() => onAction('retry', job)}><RotateCcw className="h-3.5 w-3.5" /> 重试失败项</button>
          )}
          {['queued', 'running', 'paused'].includes(job.status) && (
            <button type="button" className="action-button action-button-danger min-h-[32px] px-3 text-xs" disabled={busy} onClick={() => onAction('cancel', job)}><XCircle className="h-3.5 w-3.5" /> 取消</button>
          )}
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--dorami-well)]" aria-label={`任务进度 ${percent(job.progress)}`}>
        <div className="h-full rounded-full bg-[var(--dorami-accent)]" style={{ width: percent(job.progress) }} />
      </div>
      <p className="tiny-meta mt-2 tabular-nums">
        完成 {int(counts.finished)} / {int(counts.total)} · 成功 {int(counts.succeeded)} · 失败 {int(counts.failed)} · 跳过 {int(counts.skipped)} · 等待 {int(counts.pending + counts.queued)}
      </p>
      {job.last_error && <p className="mt-2 tiny-meta text-[var(--state-bad)]">{job.last_error}</p>}
    </div>
  );
}

export default function FullAnalysisBackfillCard({ showToast }) {
  const confirm = useConfirm();
  const [range, setRange] = useState('30');
  const [selection, setSelection] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('');
  const [estimate, setEstimate] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [estimating, setEstimating] = useState(false);
  const [creating, setCreating] = useState(false);
  const [jobBusy, setJobBusy] = useState('');

  const payload = useMemo(() => ({
    days: range === 'all' ? null : Number(range),
    selection,
    source_ids: sourceFilter
      .split(',')
      .map((sourceId) => sourceId.trim())
      .filter(Boolean),
  }), [range, selection, sourceFilter]);

  const loadJobs = useCallback(async ({ quiet = false } = {}) => {
    try {
      const data = await fetchFullAnalysisBackfills();
      setJobs(data.items || []);
    } catch (error) {
      if (!quiet) showToast?.(error.message || '获取历史分析任务失败', 'error');
    }
  }, [showToast]);

  useEffect(() => { loadJobs(); }, [loadJobs]);
  useEffect(() => {
    if (!jobs.some((job) => ['queued', 'running'].includes(job.status))) return undefined;
    const timer = window.setInterval(() => loadJobs({ quiet: true }), 3000);
    return () => window.clearInterval(timer);
  }, [jobs, loadJobs]);
  useEffect(() => { setEstimate(null); }, [payload]);

  const calculate = async () => {
    setEstimating(true);
    try { setEstimate(await estimateFullAnalysisBackfill(payload)); }
    catch (error) { showToast?.(error.message || '估算历史分析范围失败', 'error'); }
    finally { setEstimating(false); }
  };

  const create = async () => {
    if (!estimate?.ready) return;
    const scope = payload.days == null ? '全部历史文章' : `最近 ${payload.days} 天文章`;
    const warning = selection === 'all'
      ? `将强制重新分析${scope}中的 ${int(estimate.article_count)} 篇文章，重算评分、摘要和标签。新文章始终优先，确认开始？`
      : `将补充分析${scope}中缺失或版本过期的 ${int(estimate.article_count)} 篇文章。新文章始终优先，确认开始？`;
    if (!(await confirm(warning))) return;
    setCreating(true);
    try {
      const job = await createFullAnalysisBackfill(payload);
      showToast?.(`已创建历史分析任务 #${job.job_id}`, 'success');
      setEstimate(null);
      await loadJobs();
    } catch (error) { showToast?.(error.message || '创建历史分析回填失败', 'error'); }
    finally { setCreating(false); }
  };

  const act = async (action, job) => {
    if (action === 'cancel' && !(await confirm(`取消任务 #${job.job_id} 后不会再派发剩余文章，已经开始的单篇分析会正常收尾。继续取消？`))) return;
    setJobBusy(`${action}-${job.job_id}`);
    try {
      if (action === 'pause') await pauseFullAnalysisBackfill(job.job_id);
      if (action === 'resume') await resumeFullAnalysisBackfill(job.job_id);
      if (action === 'cancel') await cancelFullAnalysisBackfill(job.job_id);
      if (action === 'retry') await retryFullAnalysisBackfill(job.job_id);
      const verbs = { pause: '暂停', resume: '继续', cancel: '取消', retry: '重新排队失败项' };
      showToast?.(`已${verbs[action]}任务 #${job.job_id}`, 'success');
      await loadJobs();
    } catch (error) { showToast?.(error.message || '更新历史分析任务失败', 'error'); }
    finally { setJobBusy(''); }
  };

  return (
    <section className="surface-card card-pad rounded-[var(--r-card)]">
      <div className="card-head">
        <div>
          <h2 className="card-title">历史文章完整分析</h2>
          <p className="tiny-meta mt-1">调用模型重算评分、摘要和规范标签；按低优先级小批量执行，不挤占新文章</p>
        </div>
        <button type="button" className="icon-button" onClick={() => loadJobs()} aria-label="刷新历史分析任务"><RefreshCw className="h-4 w-4" /></button>
      </div>

      <div className="grid gap-2 sm:grid-cols-[160px_190px_auto_1fr] sm:items-center">
        <select className="form-input form-input-inline" value={range} onChange={(event) => setRange(event.target.value)} aria-label="历史分析时间范围">
          {RANGE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select className="form-input form-input-inline" value={selection} onChange={(event) => setSelection(event.target.value)} aria-label="历史分析选择策略">
          <option value="all">全部强制重分析</option>
          <option value="missing_or_outdated">仅缺失或版本过期</option>
        </select>
        <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={estimating || creating} onClick={calculate}>{estimating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Calculator className="h-3.5 w-3.5" />} 估算范围</button>
        <span className="tiny-meta">强制模式也会重跑当前版本已成功的文章，适合新增标签后的语义补标</span>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-[360px_1fr] sm:items-center">
        <input
          type="text"
          className="form-input form-input-inline"
          value={sourceFilter}
          onChange={(event) => setSourceFilter(event.target.value)}
          aria-label="限定历史分析来源"
          placeholder="限定 source_id（可选，多个用逗号分隔）"
        />
        <span className="tiny-meta">建议首次上线先限定一个小来源做 canary，确认模型、标签和成本后再扩大范围</span>
      </div>

      {estimate && (
        <div className="mt-3 rounded-[var(--r-control)] bg-[var(--dorami-soft)] p-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 tiny-meta tabular-nums">
            <strong className="body-text">{int(estimate.article_count)} 篇</strong>
            <span>{int(estimate.source_count)} 个来源</span>
            <span>首轮约 {int(estimate.estimated_initial_llm_calls)} 次调用</span>
            <span>文章输入约 {int(estimate.estimated_article_input_tokens)} tokens</span>
            <span>taxonomy v{estimate.taxonomy_version}</span>
          </div>
          {estimate.blockers?.length > 0 && <p className="mt-2 tiny-meta"><strong>开始前处理：</strong>{estimate.blockers.join('；')}</p>}
          <div className="mt-3 flex justify-end">
            <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" disabled={!estimate.ready || creating} onClick={create}>{creating && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 创建回填任务</button>
          </div>
        </div>
      )}

      <div className="mt-4 grid gap-2">
        <h3 className="section-title">最近任务</h3>
        {jobs.length === 0
          ? <p className="tiny-meta">还没有历史分析任务，先选择范围并估算需要处理的文章。</p>
          : jobs.slice(0, 5).map((job) => <JobRow key={job.job_id} job={job} busy={jobBusy.endsWith(`-${job.job_id}`)} onAction={act} />)}
      </div>
    </section>
  );
}
