import { useState } from 'react';
import { ArrowRight, Check, Download, FileText, Loader2, Upload } from 'lucide-react';
import { exportArchiveArticles, importArchiveArticlesJsonl } from '../../api';
import { SectionHeading } from './SectionPrimitives';

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
    <div>
      <SectionHeading
        title="数据同步"
        hint="在不同部署端之间离线搬运文章归档：一端导出归档包，另一端导入。建议按收录时间分批导出，导入端可重复导入同一包。"
      />

      <div className="mb-4 flex items-center justify-center gap-2.5 rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-3 text-xs font-bold text-slate-500">
        <span className="flex items-center gap-1.5"><Download className="h-3.5 w-3.5 text-indigo-500" /> 本端导出</span>
        <ArrowRight className="h-3.5 w-3.5 text-slate-300" />
        <span className="flex items-center gap-1.5"><FileText className="h-3.5 w-3.5 text-slate-500" /> 归档包</span>
        <ArrowRight className="h-3.5 w-3.5 text-slate-300" />
        <span className="flex items-center gap-1.5"><Upload className="h-3.5 w-3.5 text-emerald-500" /> 另一端导入</span>
      </div>

      {canExport && (
        <div className="surface-card rounded-[var(--r-card)] p-4">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--r-control)] bg-[var(--dorami-wash)] text-indigo-500">
              <Download className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <span className="block text-sm font-bold text-slate-700">导出归档包</span>
              <span className="tiny-meta">把已归档文章打包成 JSONL 文件，供另一个端导入。</span>
            </div>
          </div>

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
          <p className="tiny-meta mt-2">默认导出今天收录的全部来源内容；可自行调整时间范围，留空则导出至今全部。</p>

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
            <button onClick={handleExport} disabled={exporting} className="action-button action-button-primary">
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              生成并下载归档包
            </button>
          </div>
        </div>
      )}

      {canImport && (
        <div className={`surface-card rounded-[var(--r-card)] p-4 ${canExport ? 'mt-4' : ''}`}>
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--r-control)] bg-emerald-50 text-emerald-500">
              <Upload className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <span className="block text-sm font-bold text-slate-700">导入归档包</span>
              <span className="tiny-meta">导入其他端生成的归档包；重复导入会自动跳过已存在文章。</span>
            </div>
          </div>

          <label className="block cursor-pointer rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] bg-[var(--dorami-soft)] p-4 transition-colors hover:border-emerald-300 hover:bg-emerald-50/40">
            <span className="block text-sm font-bold text-slate-700">选择归档包</span>
            <span className="tiny-meta mt-1 block">支持 .jsonl；浏览器支持时也可直接导入 .jsonl.gz。</span>
            <input
              type="file"
              accept=".jsonl,.gz,.jsonl.gz,application/x-ndjson,application/gzip"
              onChange={e => setImportFile(e.target.files?.[0] || null)}
              className="mt-3 block w-full text-sm font-semibold text-slate-500 file:mr-3 file:rounded-[var(--r-control)] file:border-0 file:bg-[var(--dorami-surface)] dark:file:bg-[var(--dorami-raised)] file:px-3 file:py-2 file:text-sm file:font-bold file:text-indigo-600 file:shadow-sm"
            />
            {importFile && (
              <span className="mt-2 flex items-center gap-1.5 text-xs font-bold text-emerald-600">
                <Check className="h-3.5 w-3.5" /> 已选择：{importFile.name}（{(importFile.size / 1024).toFixed(0)} KB）
              </span>
            )}
          </label>

          <button onClick={handleImport} disabled={importing || !importFile} className="action-button action-button-primary mt-4">
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
    </div>
  );
}
