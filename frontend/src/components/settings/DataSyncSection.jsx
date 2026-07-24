import { useEffect, useRef, useState } from 'react';
import { CalendarClock, Check, Download, FileText, Globe, Loader2, Upload } from 'lucide-react';
import {
  exportArchiveArticles,
  importArchiveArticlesJsonl,
  testRemoteSync,
  startRemoteSync,
  fetchRemoteSyncStatus,
  fetchRemoteSyncSchedule,
  saveRemoteSyncSchedule,
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

// 数据同步（v3.19.2 重设计为单列纵排动作卡）：离线归档包一端导出、另一端导入;
// 远程同步直连拉取;定时同步无人值守。字段统一走 .sett-field 刻度。
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
  const importInputRef = useRef(null);

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
          <div className="sett-sync-head"><Download /><span className="sett-sync-title">导出归档包</span></div>
          <p className="sett-sync-sub">把本端文章导出为 .jsonl 归档包,用于搬运到另一部署端;默认导出今天收录的内容。</p>

          <div className="sett-sync-fields">
            <label className="sett-field">
              <span className="sett-field-lbl">收录时间起点</span>
              <input
                type="datetime-local"
                value={exportFilters.fetched_date_start}
                onChange={e => updateExportFilter('fetched_date_start', e.target.value)}
                className="form-input"
              />
            </label>
            <label className="sett-field">
              <span className="sett-field-lbl">收录时间终点</span>
              <input
                type="datetime-local"
                value={exportFilters.fetched_date_end}
                onChange={e => updateExportFilter('fetched_date_end', e.target.value)}
                className="form-input"
              />
            </label>
            <label className="sett-field">
              <span className="sett-field-lbl">每包上限</span>
              <input
                type="number"
                min={1}
                max={5000}
                value={exportFilters.limit}
                onChange={e => updateExportFilter('limit', e.target.value)}
                className="form-input"
              />
            </label>
          </div>

          <div className="sett-sync-foot">
            <span className="sett-sw">
              <button
                type="button"
                role="switch"
                aria-checked={compressExport}
                aria-label="gzip 压缩下载"
                onClick={() => setCompressExport(v => !v)}
                className={`ledger-switch ${compressExport ? 'is-on' : ''}`}
              />
              gzip 压缩下载
            </span>
            <button onClick={handleExport} disabled={exporting} className="action-button action-button-secondary min-h-[32px] px-3 text-xs ml-auto">
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              导出 .jsonl
            </button>
          </div>
        </div>
      )}

      {canImport && (
        <div className="sett-sync-card">
          <div className="sett-sync-head"><Upload /><span className="sett-sync-title">导入归档包</span></div>
          <p className="sett-sync-sub">选择另一端导出的 .jsonl(.gz)导入本端;按内容指纹去重,重复导入自动跳过。</p>

          <button
            type="button"
            className={`sett-file ${importFile ? 'has-file' : ''}`}
            onClick={() => importInputRef.current?.click()}
          >
            {importFile ? <Check className="text-emerald-600" /> : <FileText />}
            <span className="min-w-0">
              <span className="sett-file-name">
                {importFile ? importFile.name : '选择归档包'}
              </span>
              <span className="sett-file-hint">
                {importFile
                  ? `${(importFile.size / 1024).toFixed(0)} KB · 点击可重新选择`
                  : '支持 .jsonl;浏览器支持时也可直接导入 .jsonl.gz'}
              </span>
            </span>
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept=".jsonl,.gz,.jsonl.gz,application/x-ndjson,application/gzip"
            onChange={e => setImportFile(e.target.files?.[0] || null)}
            className="hidden"
          />

          <div className="sett-sync-foot">
            <button onClick={handleImport} disabled={importing || !importFile} className="action-button action-button-secondary min-h-[32px] px-3 text-xs ml-auto">
              {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              导入归档包
            </button>
          </div>

          {importResult && (
            <div className="sett-sync-panel">
              <div className="sett-sync-stats">
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
      {canImport && <RemoteSyncScheduleCard showToast={showToast} />}
    </div>
  );
}

// ==================== 远程同步（v3.18 互通波） ====================
// 接收方主动拉取另一个存量后端的归档:填地址+远端管理员凭据 → 测试连接 →
// 选范围(全量/增量自上次/自定起点)→ 后台任务拉取,轮询进度。密码只在本次
// 请求中使用,不落任何存储(定时同步的凭据持久化独立在下方定时卡,契约见其注释)。

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
    <div className="sett-sync-card">
      <div className="sett-sync-head"><Globe /><span className="sett-sync-title">远程同步</span></div>
      <p className="sett-sync-sub">
        直接连到另一个部署端,把它的文章归档拉到本端——适合新部署快速灌入内容,或只打通了单点网络的环境。
        需要远端的管理员账号;密码仅用于本次连接,不会保存。
      </p>

      <div className="sett-sync-fields">
        <label className="sett-field">
          <span className="sett-field-lbl">远端地址</span>
          <input
            type="text"
            placeholder="http://主机:8088"
            value={form.baseUrl}
            onChange={e => updateForm('baseUrl', e.target.value)}
            className="form-input"
            disabled={running}
          />
        </label>
        <label className="sett-field">
          <span className="sett-field-lbl">远端管理员账号</span>
          <input
            type="text"
            autoComplete="off"
            value={form.username}
            onChange={e => updateForm('username', e.target.value)}
            className="form-input"
            disabled={running}
          />
        </label>
        <label className="sett-field">
          <span className="sett-field-lbl">远端密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={e => updateForm('password', e.target.value)}
            className="form-input"
            disabled={running}
          />
        </label>
      </div>

      {probe && (
        <div className="sett-sync-panel">
          <span className="flex items-center gap-1.5 text-sm font-bold text-emerald-600"><Check className="h-3.5 w-3.5" /> 远端可用</span>
          <div className="sett-sync-stats mt-1.5">
            <div><span className="tiny-meta block">远端版本</span><b>{probe.version || '未知'}</b></div>
            <div><span className="tiny-meta block">文章总量</span><b>{probe.article_total ?? '未知'}</b></div>
            <div><span className="tiny-meta block">数据格式</span><b>{probe.schema_version}</b></div>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span className="sett-field-lbl">同步范围</span>
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
            style={{ width: 'auto' }}
            aria-label="同步起始时间"
          />
        )}
        {mode === 'incremental' && lastTarget?.last_fetched_date && (
          <span className="tiny-meta">自 {fmtDate(lastTarget.last_fetched_date)} 起</span>
        )}
      </div>

      <div className="sett-sync-foot">
        {lastTarget && !running && (
          <span className="tiny-meta">
            上次同步 {fmtDate(lastTarget.last_synced_at)} · 新增 {lastTarget.last_result?.imported ?? 0} · 跳过 {lastTarget.last_result?.skipped ?? 0}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2.5">
          <button onClick={handleProbe} disabled={!canStart || probing} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
            {probing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {probing ? '连接中…' : '测试连接'}
          </button>
          <button onClick={handleStart} disabled={!canStart} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {(starting || running) ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            {running ? '同步中…' : '开始同步'}
          </button>
        </span>
      </div>

      {job && (
        <div className="sett-sync-panel">
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
              <div className="sett-sync-stats">
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

// ==================== 定时同步（v3.19.2） ====================
// 远程同步的无人值守版:到点自动从配置的远端做增量拉取(自该远端上次成功位置,
// 无记录则全量)。与手动同步「密码不保存」不同,定时任务必须持久化凭据——沿用
// X API token 的既有契约:凭据保存在本端数据库,只写不回显(读接口只给 password_set),
// 建议在远端建专用同步账号。运行记录与手动同步同列(触发者显示为 system)。

const FREQ_PRESETS = [
  ['daily', '每天'],
  ['six', '每 6 小时'],
  ['hourly', '每小时'],
  ['custom', '自定义'],
];

function cronFromFreq(freq, dailyTime, customCron) {
  if (freq === 'hourly') return '0 * * * *';
  if (freq === 'six') return '0 */6 * * *';
  if (freq === 'daily') {
    const [h, m] = (dailyTime || '03:00').split(':');
    return `${Number(m) || 0} ${Number(h) || 0} * * *`;
  }
  return (customCron || '').trim();
}

function freqFromCron(cron) {
  const c = (cron || '').trim();
  if (!c || c === '0 3 * * *') return { freq: 'daily', dailyTime: '03:00', customCron: '' };
  if (c === '0 * * * *') return { freq: 'hourly', dailyTime: '03:00', customCron: '' };
  if (c === '0 */6 * * *') return { freq: 'six', dailyTime: '03:00', customCron: '' };
  const m = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec(c);
  if (m) return { freq: 'daily', dailyTime: `${String(m[2]).padStart(2, '0')}:${String(m[1]).padStart(2, '0')}`, customCron: '' };
  return { freq: 'custom', dailyTime: '03:00', customCron: c };
}

function RemoteSyncScheduleCard({ showToast }) {
  const [enabled, setEnabled] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordSet, setPasswordSet] = useState(false);
  const [freq, setFreq] = useState('daily');
  const [dailyTime, setDailyTime] = useState('03:00');
  const [customCron, setCustomCron] = useState('');
  const [updatedAt, setUpdatedAt] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchRemoteSyncSchedule().then((data) => {
      if (cancelled || !data) return;
      setEnabled(Boolean(data.enabled));
      setBaseUrl(data.base_url || '');
      setUsername(data.username || '');
      setPasswordSet(Boolean(data.password_set));
      setUpdatedAt(data.updated_at || '');
      const parsed = freqFromCron(data.cron);
      setFreq(parsed.freq);
      setDailyTime(parsed.dailyTime);
      setCustomCron(parsed.customCron);
    }).catch(() => {}).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const handleSave = async () => {
    const cron = cronFromFreq(freq, dailyTime, customCron);
    if (enabled) {
      if (!baseUrl.trim() || !username.trim()) {
        showToast('启用定时同步需要填写远端地址与账号', 'error');
        return;
      }
      if (!password && !passwordSet) {
        showToast('首次启用需要填写远端密码', 'error');
        return;
      }
      if (!cron) {
        showToast('请填写 cron 表达式', 'error');
        return;
      }
    }
    setSaving(true);
    try {
      const data = await saveRemoteSyncSchedule({
        enabled,
        cron: cron || '0 3 * * *',
        base_url: baseUrl.trim(),
        username: username.trim(),
        password,
        source_ids: [],
      });
      setPasswordSet(Boolean(data?.password_set ?? (passwordSet || password)));
      setUpdatedAt(data?.updated_at || updatedAt);
      setPassword('');
      showToast(enabled ? '定时同步已开启' : '定时同步已保存(未启用)', 'success');
    } catch (error) {
      showToast(error.message || '保存定时同步配置失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="sett-sync-card">
      <div className="sett-sync-head">
        <CalendarClock />
        <span className="sett-sync-title">定时同步</span>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="定时同步开关"
          disabled={loading}
          onClick={() => setEnabled(v => !v)}
          className={`ledger-switch ml-auto ${enabled ? 'is-on' : ''}`}
        />
      </div>
      <p className="sett-sync-sub">
        到点自动从下方远端做增量拉取(自上次成功位置,无记录则全量)。
        凭据会保存在本端、仅用于定时任务且不回显——建议在远端使用专用同步账号。
      </p>

      <div className="sett-sync-fields">
        <label className="sett-field">
          <span className="sett-field-lbl">远端地址</span>
          <input
            type="text"
            placeholder="http://主机:8088"
            value={baseUrl}
            onChange={e => setBaseUrl(e.target.value)}
            className="form-input"
            disabled={loading}
          />
        </label>
        <label className="sett-field">
          <span className="sett-field-lbl">远端管理员账号</span>
          <input
            type="text"
            autoComplete="off"
            value={username}
            onChange={e => setUsername(e.target.value)}
            className="form-input"
            disabled={loading}
          />
        </label>
        <label className="sett-field">
          <span className="sett-field-lbl">远端密码</span>
          <input
            type="password"
            autoComplete="new-password"
            placeholder={passwordSet ? '已保存,留空保持不变' : ''}
            value={password}
            onChange={e => setPassword(e.target.value)}
            className="form-input"
            disabled={loading}
          />
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span className="sett-field-lbl">同步频率</span>
        <div className="mini-seg" role="group" aria-label="同步频率">
          {FREQ_PRESETS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setFreq(id)}
              className={`mini-seg-btn ${freq === id ? 'is-on' : ''}`}
            >
              {label}
            </button>
          ))}
        </div>
        {freq === 'daily' && (
          <input
            type="time"
            value={dailyTime}
            onChange={e => setDailyTime(e.target.value)}
            className="form-input"
            style={{ width: 'auto' }}
            aria-label="每天同步时刻"
          />
        )}
        {freq === 'custom' && (
          <input
            type="text"
            value={customCron}
            onChange={e => setCustomCron(e.target.value)}
            placeholder="分 时 日 月 周,如 30 5 * * *"
            className="form-input font-mono"
            style={{ width: 220 }}
            aria-label="cron 表达式"
          />
        )}
      </div>

      <div className="sett-sync-foot">
        <span className="tiny-meta">
          {loading ? '读取配置…' : updatedAt ? `上次保存 ${fmtDate(updatedAt)}` : '尚未配置'}
        </span>
        <button onClick={handleSave} disabled={loading || saving} className="action-button action-button-primary min-h-[32px] px-3 text-xs ml-auto">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          保存
        </button>
      </div>
    </div>
  );
}
