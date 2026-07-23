import { useEffect, useRef, useState } from 'react';
import { Check, Download, Globe, Loader2, Upload } from 'lucide-react';
import {
  exportArchiveArticles,
  importArchiveArticlesJsonl,
  testRemoteSync,
  startRemoteSync,
  fetchRemoteSyncStatus,
  fetchBackgroundJob,
} from '../../api';

function downloadFile(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    downloadFile(url, filename);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

function safeNamePart(value, fallback) {
  return (value || fallback).replace(/[^0-9A-Za-z_-]+/g, '-').replace(/^-+|-+$/g, '') || fallback;
}

// 当天本地 00:00–23:59，格式为 datetime-local 所需的 YYYY-MM-DDTHH:mm。
function todayRange() {
  const now = new Date();
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  return { start: `${day}T00:00`, end: `${day}T23:59` };
}

async function gzipJsonl(text) {
  if (typeof CompressionStream === 'undefined') return null;
  const stream = new Blob([text], { type: 'application/x-ndjson; charset=utf-8' })
    .stream()
    .pipeThrough(new CompressionStream('gzip'));
  return new Response(stream).blob();
}

async function readArchiveFile(file) {
  if (!file) throw new Error('请选择要导入的归档包');
  const isGzip = file.name.toLowerCase().endsWith('.gz') || file.type === 'application/gzip';
  if (!isGzip) return file.text();
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('当前浏览器不支持直接解压 .gz，请先解压为 .jsonl 后再导入');
  }
  const stream = file.stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
}

// 数据同步（离线归档包）：一端导出、另一端导入，在不同部署端之间搬运文章归档。
export default function DataSyncSection({ showToast, canExport, canImport, onArticlesChanged }) {
  const [exportFilters, setExportFilters] = useState(() => {
    const { start, end } = todayRange();
    return { fetched_date_start: start, fetched_date_end: end, limit: 1000 };
  });
  const [compressExport, setCompressExport] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState(null);

  const updateExportFilter = (key, value) => {
    setExportFilters(prev => ({ ...prev, [key]: value }));
  };

  const exportPayload = () => ({
    fetched_date_start: exportFilters.fetched_date_start,
    fetched_date_end: exportFilters.fetched_date_end,
    source_ids: '',
    limit: Math.max(1, Math.min(5000, Number(exportFilters.limit) || 1000)),
    skip: 0,
    has_content: undefined,
  });

  const handleExport = async () => {
    setExporting(true);
    try {
      const payload = exportPayload();
      const text = await exportArchiveArticles(payload);
      const firstLine = text.split('\n').find(Boolean);
      let count = 0;
      try {
        count = JSON.parse(firstLine)?.count ?? 0;
      } catch {
        count = 0;
      }

      const start = safeNamePart(payload.fetched_date_start, 'begin');
      const end = safeNamePart(payload.fetched_date_end, 'now');
      const suffix = `skip${payload.skip}-limit${payload.limit}`;
      const baseName = `dorami-archive-${start}_${end}-${suffix}`;

      if (compressExport) {
        const gzipBlob = await gzipJsonl(text);
        if (gzipBlob) {
          downloadBlob(gzipBlob, `${baseName}.jsonl.gz`);
          showToast(`已生成压缩归档包：${count} 篇文章`, 'success');
          return;
        }
        showToast('当前浏览器不支持 gzip 压缩，已改为下载 JSONL 原文', 'info');
      }

      downloadBlob(new Blob([text], { type: 'application/x-ndjson; charset=utf-8' }), `${baseName}.jsonl`);
      showToast(`已生成归档包：${count} 篇文章`, 'success');
    } catch (error) {
      showToast(error.message || '导出失败', 'error');
    } finally {
      setExporting(false);
    }
  };

  const handleImport = async () => {
    if (!importFile) {
      showToast('请选择 .jsonl 或 .jsonl.gz 归档包', 'error');
      return;
    }
    setImporting(true);
    setImportResult(null);
    try {
      const rawText = await readArchiveFile(importFile);
      const result = await importArchiveArticlesJsonl(rawText);
      setImportResult(result);
      onArticlesChanged?.();
      showToast(
        `导入完成：新增 ${result.imported_count}，更新 ${result.updated_count}，跳过 ${result.skipped_count}`,
        result.error_count ? 'info' : 'success',
      );
    } catch (error) {
      showToast(error.message || '导入失败', 'error');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="sett-sync-grid">
      {canExport && (
        <div className="sett-sync-card">
          <div className="sett-sync-title">导出归档包</div>
          <p className="sett-sync-sub">把本端文章导出为 .jsonl 归档包,用于搬运到另一部署端;默认导出今天收录的内容。</p>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="tiny-meta">收录时间起点</span>
              <input
                type="datetime-local"
                value={exportFilters.fetched_date_start}
                onChange={e => updateExportFilter('fetched_date_start', e.target.value)}
                className="form-input w-full"
              />
            </label>
            <label className="space-y-1">
              <span className="tiny-meta">收录时间终点</span>
              <input
                type="datetime-local"
                value={exportFilters.fetched_date_end}
                onChange={e => updateExportFilter('fetched_date_end', e.target.value)}
                className="form-input w-full"
              />
            </label>
            <label className="space-y-1 sm:col-span-2">
              <span className="tiny-meta">每包上限</span>
              <input
                type="number"
                min={1}
                max={5000}
                value={exportFilters.limit}
                onChange={e => updateExportFilter('limit', e.target.value)}
                className="form-input w-full"
              />
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--dorami-border)] pt-4">
            <label className="flex items-center gap-2 text-sm font-bold text-slate-500">
              <input
                type="checkbox"
                checked={compressExport}
                onChange={e => setCompressExport(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-indigo-600"
              />
              gzip 压缩下载
            </label>
            <button onClick={handleExport} disabled={exporting} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              导出 .jsonl
            </button>
          </div>
        </div>
      )}

      {canImport && (
        <div className="sett-sync-card">
          <div className="sett-sync-title">导入归档包</div>
          <p className="sett-sync-sub">选择另一端导出的 .jsonl(.gz)导入本端;按内容指纹去重,重复导入自动跳过。</p>

          <label className="block cursor-pointer rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] bg-[var(--dorami-soft)] p-4 transition-colors hover:border-[var(--dorami-border-strong)]">
            <span className="block text-sm font-bold text-slate-700">选择归档包</span>
            <span className="tiny-meta mt-1 block">支持 .jsonl；浏览器支持时也可直接导入 .jsonl.gz。</span>
            <input
              type="file"
              accept=".jsonl,.gz,.jsonl.gz,application/x-ndjson,application/gzip"
              onChange={e => setImportFile(e.target.files?.[0] || null)}
              className="mt-3 block w-full text-sm font-semibold text-slate-500 file:mr-3 file:rounded-[var(--r-control)] file:border-0 file:bg-[var(--dorami-surface)] dark:file:bg-[var(--dorami-raised)] file:px-3 file:py-2 file:text-sm file:font-bold file:text-[var(--dorami-ink)] file:shadow-sm"
            />
            {importFile && (
              <span className="mt-2 flex items-center gap-1.5 text-xs font-bold text-emerald-600">
                <Check className="h-3.5 w-3.5" /> 已选择：{importFile.name}（{(importFile.size / 1024).toFixed(0)} KB）
              </span>
            )}
          </label>

          <button onClick={handleImport} disabled={importing || !importFile} className="action-button action-button-secondary min-h-[32px] px-3 text-xs mt-4">
            {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            导入归档包
          </button>

          {importResult && (
            <div className="mt-4 rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-3">
              <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div><span className="tiny-meta block">新增</span><b>{importResult.imported_count}</b></div>
                <div><span className="tiny-meta block">更新</span><b>{importResult.updated_count}</b></div>
                <div><span className="tiny-meta block">跳过</span><b>{importResult.skipped_count}</b></div>
                <div><span className="tiny-meta block">错误</span><b className={importResult.error_count ? 'text-rose-500' : ''}>{importResult.error_count}</b></div>
              </div>
              {importResult.error_count > 0 && (
                <pre className="mt-3 max-h-32 overflow-auto rounded-[var(--r-control)] bg-[var(--dorami-surface)] dark:bg-[var(--dorami-well)] p-3 text-xs font-semibold text-rose-600">
                  {JSON.stringify(importResult.errors?.slice(0, 5) || [], null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      )}

      {canImport && <RemoteSyncCard showToast={showToast} onArticlesChanged={onArticlesChanged} />}
    </div>
  );
}

// ==================== 远程同步（v3.18 互通波） ====================
// 接收方主动拉取另一个存量后端的归档:填地址+远端管理员凭据 → 测试连接 →
// 选范围(全量/增量自上次/自定起点)→ 后台任务拉取,轮询进度。密码只在本次
// 请求中使用,不落任何存储。

const SYNC_POLL_MS = 1500;

function fmtDate(iso) {
  return iso ? iso.slice(0, 16).replace('T', ' ') : '—';
}

function RemoteSyncCard({ showToast, onArticlesChanged }) {
  const [form, setForm] = useState({ baseUrl: '', username: '', password: '' });
  const [targets, setTargets] = useState({});
  const [probe, setProbe] = useState(null);
  const [probing, setProbing] = useState(false);
  const [mode, setMode] = useState('full'); // full | incremental | custom
  const [customStart, setCustomStart] = useState('');
  const [job, setJob] = useState(null); // 运行中/终态的后台任务
  const [starting, setStarting] = useState(false);
  const pollingRef = useRef(false);

  // 上次同步过的目标:预填地址/账号,并支撑「增量自上次」。
  useEffect(() => {
    let cancelled = false;
    fetchRemoteSyncStatus().then((data) => {
      if (cancelled) return;
      const targetMap = data?.state?.targets || {};
      setTargets(targetMap);
      const [firstUrl] = Object.keys(targetMap);
      if (firstUrl) {
        setForm(prev => (prev.baseUrl ? prev : { ...prev, baseUrl: firstUrl, username: targetMap[firstUrl].username || '' }));
      }
      const running = (data?.jobs || []).find(j => j.status === 'running' || j.status === 'queued');
      if (running) { setJob(running); pollJobLoop(running.job_id); }
    }).catch(() => {});
    return () => { cancelled = true; pollingRef.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const normalizedUrl = form.baseUrl.trim().replace(/\/+$/, '');
  const lastTarget = targets[normalizedUrl] || null;
  const running = job && (job.status === 'running' || job.status === 'queued');

  const updateForm = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  const handleProbe = async () => {
    setProbing(true);
    setProbe(null);
    try {
      const result = await testRemoteSync(form.baseUrl, form.username, form.password);
      setProbe(result);
      showToast('远端连接正常', 'success');
    } catch (error) {
      showToast(error.message || '远端连接测试失败', 'error');
    } finally {
      setProbing(false);
    }
  };

  async function pollJobLoop(jobId) {
    if (pollingRef.current) return;
    pollingRef.current = true;
    try {
      while (pollingRef.current) {
        let current;
        try {
          current = await fetchBackgroundJob(jobId);
        } catch {
          break; // 轮询失败不刷屏,下次进入分区再看
        }
        setJob(current);
        if (current.status === 'succeeded' || current.status === 'failed' || current.status === 'cancelled') {
          if (current.status === 'succeeded') {
            const r = current.result || {};
            showToast(`同步完成:新增 ${r.imported ?? 0},回填 ${r.updated ?? 0},跳过 ${r.skipped ?? 0}`, r.errors ? 'info' : 'success');
            onArticlesChanged?.();
            fetchRemoteSyncStatus().then(data => setTargets(data?.state?.targets || {})).catch(() => {});
          } else if (current.status === 'failed') {
            showToast(current.error || '远程同步失败', 'error');
          }
          break;
        }
        await new Promise(resolve => setTimeout(resolve, SYNC_POLL_MS));
      }
    } finally {
      pollingRef.current = false;
    }
  }

  const handleStart = async () => {
    setStarting(true);
    try {
      const options = {};
      if (mode === 'incremental' && lastTarget?.last_fetched_date) options.fetchedDateStart = lastTarget.last_fetched_date;
      if (mode === 'custom' && customStart) options.fetchedDateStart = customStart;
      const { job_id: jobId } = await startRemoteSync(form.baseUrl, form.username, form.password, options);
      setJob({ job_id: jobId, status: 'queued', processed: 0, total: null });
      pollJobLoop(jobId);
    } catch (error) {
      showToast(error.message || '启动远程同步失败', 'error');
    } finally {
      setStarting(false);
    }
  };

  const progressPct = job?.total ? Math.min(100, Math.round((job.processed / job.total) * 100)) : null;
  const canStart = normalizedUrl && form.username.trim() && form.password && !running && !starting;

  return (
    <div className="sett-sync-card col-span-full">
      <div className="sett-sync-title flex items-center gap-1.5"><Globe className="h-3.5 w-3.5" /> 远程同步</div>
      <p className="sett-sync-sub">
        直接连到另一个部署端,把它的文章归档拉到本端——适合新部署快速灌入内容,或只打通了单点网络的环境。
        需要远端的管理员账号;密码仅用于本次连接,不会保存。
      </p>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1">
          <span className="tiny-meta">远端地址</span>
          <input
            type="text"
            placeholder="http://主机:8088"
            value={form.baseUrl}
            onChange={e => updateForm('baseUrl', e.target.value)}
            className="form-input w-full"
            disabled={running}
          />
        </label>
        <label className="space-y-1">
          <span className="tiny-meta">远端管理员账号</span>
          <input
            type="text"
            autoComplete="off"
            value={form.username}
            onChange={e => updateForm('username', e.target.value)}
            className="form-input w-full"
            disabled={running}
          />
        </label>
        <label className="space-y-1">
          <span className="tiny-meta">远端密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={e => updateForm('password', e.target.value)}
            className="form-input w-full"
            disabled={running}
          />
        </label>
      </div>

      {probe && (
        <div className="mt-3 rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-3 text-sm">
          <span className="flex items-center gap-1.5 font-bold text-emerald-600"><Check className="h-3.5 w-3.5" /> 远端可用</span>
          <div className="mt-1.5 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div><span className="tiny-meta block">远端版本</span><b>{probe.version || '未知'}</b></div>
            <div><span className="tiny-meta block">文章总量</span><b>{probe.article_total ?? '未知'}</b></div>
            <div><span className="tiny-meta block">数据格式</span><b>{probe.schema_version}</b></div>
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="tiny-meta">同步范围</span>
        <div className="mini-seg" role="group" aria-label="同步范围">
          <button type="button" onClick={() => setMode('full')} className={`mini-seg-btn ${mode === 'full' ? 'is-on' : ''}`}>全量</button>
          <button
            type="button"
            onClick={() => setMode('incremental')}
            disabled={!lastTarget?.last_fetched_date}
            title={lastTarget?.last_fetched_date ? `自 ${fmtDate(lastTarget.last_fetched_date)} 起` : '该远端尚无同步记录'}
            className={`mini-seg-btn ${mode === 'incremental' ? 'is-on' : ''}`}
          >
            增量自上次
          </button>
          <button type="button" onClick={() => setMode('custom')} className={`mini-seg-btn ${mode === 'custom' ? 'is-on' : ''}`}>自定起点</button>
        </div>
        {mode === 'custom' && (
          <input
            type="datetime-local"
            value={customStart}
            onChange={e => setCustomStart(e.target.value)}
            className="form-input"
            aria-label="同步起始时间"
          />
        )}
        {mode === 'incremental' && lastTarget?.last_fetched_date && (
          <span className="tiny-meta">自 {fmtDate(lastTarget.last_fetched_date)} 起</span>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--dorami-border)] pt-4">
        <button onClick={handleProbe} disabled={!canStart || probing} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
          {probing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          {probing ? '连接中…' : '测试连接'}
        </button>
        <button onClick={handleStart} disabled={!canStart} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
          {(starting || running) ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {running ? '同步中…' : '开始同步'}
        </button>
        {lastTarget && !running && (
          <span className="tiny-meta">
            上次同步 {fmtDate(lastTarget.last_synced_at)} · 新增 {lastTarget.last_result?.imported ?? 0} · 跳过 {lastTarget.last_result?.skipped ?? 0}
          </span>
        )}
      </div>

      {job && (
        <div className="mt-4 rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-3">
          {running ? (
            <>
              <div className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-1.5 font-bold"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在同步…</span>
                <span className="run-progress-n text-xs font-bold">
                  {progressPct != null ? `${job.processed} / ${job.total}(${progressPct}%)` : `已拉取 ${job.processed || 0} 条`}
                </span>
              </div>
              {progressPct != null && (
                <div className="run-progress-track mt-2">
                  <div className="run-progress-fill" style={{ width: `${progressPct}%` }} />
                </div>
              )}
            </>
          ) : job.status === 'succeeded' ? (
            <>
              <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
                <div><span className="tiny-meta block">拉取</span><b>{job.result?.pulled ?? 0}</b></div>
                <div><span className="tiny-meta block">新增</span><b>{job.result?.imported ?? 0}</b></div>
                <div><span className="tiny-meta block">回填</span><b>{job.result?.updated ?? 0}</b></div>
                <div><span className="tiny-meta block">跳过</span><b>{job.result?.skipped ?? 0}</b></div>
                <div><span className="tiny-meta block">错误</span><b className={job.result?.errors ? 'text-rose-500' : ''}>{job.result?.errors ?? 0}</b></div>
              </div>
              {(job.result?.error_samples?.length ?? 0) > 0 && (
                <pre className="mt-3 max-h-32 overflow-auto rounded-[var(--r-control)] bg-[var(--dorami-well)] p-3 text-xs font-semibold text-rose-600">
                  {JSON.stringify(job.result.error_samples.slice(0, 5), null, 2)}
                </pre>
              )}
            </>
          ) : job.status === 'failed' ? (
            <p className="text-sm font-bold text-rose-600">同步失败:{job.error || '未知原因'}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}
