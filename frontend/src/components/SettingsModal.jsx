import { useEffect, useMemo, useState } from 'react';
import {
  BarChart2,
  Check,
  Copy,
  Download,
  FileText,
  Info,
  Loader2,
  LogOut,
  Plug2,
  RefreshCw,
  Settings as SettingsIcon,
  Upload,
  User,
  X,
  Zap,
} from 'lucide-react';
import { MCP_URL } from '../config';
import {
  fetchMcpStatus,
  fetchVectorStats,
  exportArchiveArticles,
  getAutoVectorize,
  importArchiveArticlesJsonl,
  reindexAll,
  setAutoVectorize,
  toggleMcp,
} from '../api';

async function copyText(text) {
  if (!text) throw new Error('没有可复制的内容');
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      /* fall through to textarea fallback */
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand('copy')) throw new Error('浏览器拒绝复制');
  } finally {
    document.body.removeChild(textarea);
  }
}

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

function SectionHeading({ title, hint }) {
  return (
    <div className="mb-4">
      <h4 className="text-sm font-black text-slate-800">{title}</h4>
      {hint && <p className="tiny-meta mt-1">{hint}</p>}
    </div>
  );
}

function FieldRow({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3 last:border-b-0">
      <span className="text-sm font-bold text-slate-600">{label}</span>
      <div className="text-right text-sm font-semibold text-slate-800">{children}</div>
    </div>
  );
}

/* ── Account ─────────────────────────────────────────────── */
function AccountSection({ username, accountRoleLabel, layerLabel, onLogout }) {
  return (
    <div>
      <SectionHeading title="账户" />
      <div className="surface-card rounded-[12px] px-4">
        <FieldRow label="登录账户">{username || '—'}</FieldRow>
        <FieldRow label="账户角色">{accountRoleLabel}</FieldRow>
        <FieldRow label="当前运行层">{layerLabel}</FieldRow>
      </div>

      <div className="mt-4 rounded-[12px] border border-dashed border-slate-200 bg-slate-50/60 p-4">
        <p className="text-sm font-bold text-slate-600">修改密码</p>
        <p className="tiny-meta mt-1">
          账户凭据当前由后端配置（<code className="font-mono">backend.ini</code> 的 admin_users / user_users）统一管理，
          暂不支持在线修改。如需变更请编辑配置后重启服务。
        </p>
      </div>

      <button onClick={onLogout} className="action-button action-button-danger mt-4">
        <LogOut className="h-4 w-4" /> 退出登录
      </button>
    </div>
  );
}

/* ── 向量雷达（向量库管理，仅管理员）─────────────────────── */
function VectorSection({ showToast }) {
  const [autoVec, setAutoVec] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let alive = true;
    getAutoVectorize().then(d => { if (alive) setAutoVec(Boolean(d.enabled)); }).catch(() => {});
    fetchVectorStats().then(d => { if (alive) setStats(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const handleToggleAutoVec = async () => {
    const next = !autoVec;
    setAutoVec(next);
    try {
      await setAutoVectorize(next);
      showToast(next ? '已开启：抓取后自动向量化' : '已关闭自动向量化', 'success');
    } catch (error) {
      setAutoVec(!next);
      showToast(error.message || '设置失败', 'error');
    }
  };

  const handleReindex = async () => {
    if (!window.confirm('全量重索引将清空并重建整个向量库（适用于更换 Embedding 模型）。确认继续？')) return;
    setReindexing(true);
    try {
      const data = await reindexAll();
      showToast(`全量重索引完成：${data.total_reindexed}/${data.total_articles} 篇`, 'success');
      fetchVectorStats().then(setStats).catch(() => {});
    } catch (error) {
      showToast(error.message || '重索引失败', 'error');
    } finally {
      setReindexing(false);
    }
  };

  return (
    <div>
      <SectionHeading title="向量雷达" hint="向量库是全局共享的，构建与重索引会影响所有订阅者，仅管理员可操作。" />

      <div className="surface-card rounded-[12px] p-4">
        <label className="flex cursor-pointer items-center justify-between gap-4">
          <span>
            <span className="block text-sm font-bold text-slate-700">抓取后自动向量化</span>
            <span className="tiny-meta">开启后，每次抓取入库的新文章会自动写入向量库。</span>
          </span>
          <input type="checkbox" checked={autoVec} onChange={handleToggleAutoVec} className="h-5 w-5 shrink-0 rounded border-slate-300 text-indigo-600" />
        </label>
      </div>

      <div className="surface-card mt-4 rounded-[12px] p-4">
        <div className="flex items-center justify-between gap-4">
          <span>
            <span className="block text-sm font-bold text-slate-700">全量重索引</span>
            <span className="tiny-meta">清空并重建整个向量库，更换 Embedding 模型后使用。</span>
          </span>
          <button onClick={handleReindex} disabled={reindexing} className="action-button action-button-secondary text-xs">
            {reindexing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5 text-amber-500" />} 重索引
          </button>
        </div>
        <div className="mt-3 border-t border-slate-100 pt-3">
          <FieldRow label="向量块总数">{stats === null ? '…' : (stats.total_vectors ?? '—')}</FieldRow>
          <FieldRow label="Embedding 模型">
            <span className="font-mono text-xs">BAAI/bge-m3</span>
            <span className="tiny-meta ml-1">（默认，可经 LOCAL_MODEL_PATH 覆盖）</span>
          </FieldRow>
        </div>
      </div>
    </div>
  );
}

/* ── 接入集成（MCP + Skill）──────────────────────────────── */
function IntegrationSection({ showToast, mcpStatus, canToggle, onMcpToggled }) {
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);

  const mcpUrl = mcpStatus?.url ?? MCP_URL;
  const enabled = mcpStatus?.enabled ?? false;

  const handleCopy = async () => {
    try {
      await copyText(mcpUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch (error) {
      showToast(error.message || '复制失败', 'error');
    }
  };

  const handleToggle = async () => {
    setToggling(true);
    try {
      const data = await toggleMcp();
      onMcpToggled?.(data.enabled);
      window.dispatchEvent(new CustomEvent('dorami-mcp-changed', { detail: { enabled: data.enabled } }));
      showToast(data.enabled ? 'MCP Server 已启动' : 'MCP Server 已停止', data.enabled ? 'success' : 'info');
    } catch {
      showToast('切换失败，请重试', 'error');
    } finally {
      setToggling(false);
    }
  };

  return (
    <div>
      <SectionHeading title="接入集成" hint="管理 MCP Server 与 AI 日报 Skill。完整客户端配置、工具说明与安装指南见「接入集成」页。" />

      <div className="surface-card rounded-[12px] p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Plug2 className="h-4 w-4 text-sky-500" />
            <span className="text-sm font-bold text-slate-700">MCP Server</span>
            <span className={`text-xs font-bold ${enabled ? 'text-emerald-500' : 'text-slate-400'}`}>
              {mcpStatus === null ? '…' : enabled ? '● 运行中' : '○ 已停止'}
            </span>
          </div>
          {canToggle ? (
            <button onClick={handleToggle} disabled={toggling || mcpStatus === null} className={`action-button text-xs ${enabled ? 'action-button-danger' : 'action-button-primary'}`}>
              {toggling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plug2 className="h-3.5 w-3.5" />}
              {enabled ? '停止 MCP' : '启动 MCP'}
            </button>
          ) : (
            <span className="tiny-meta">由管理员启停</span>
          )}
        </div>

        <p className="tiny-meta mb-1 mt-3">接入地址</p>
        <div className="flex items-center gap-2 rounded-[10px] border border-slate-100 bg-slate-50 px-3 py-2">
          <code className="min-w-0 flex-1 truncate text-xs font-bold text-slate-600" title={mcpUrl}>{mcpUrl}</code>
          <button onClick={handleCopy} className="shrink-0 text-slate-400 hover:text-indigo-600" title="复制 MCP 地址" aria-label="复制 MCP 地址">
            {copied ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
          </button>
        </div>
      </div>

      <div className="surface-card mt-4 rounded-[12px] p-4">
        <div className="flex items-center justify-between gap-4">
          <span>
            <span className="block text-sm font-bold text-slate-700">AI 日报 Skill</span>
            <span className="tiny-meta">一句话生成结构化日报，支持 Claude Code、Cursor 等平台。</span>
          </span>
          <button onClick={() => downloadFile('/api/skill/daily-brief', 'dorami-daily-brief.zip')} className="action-button action-button-secondary text-xs">
            <Download className="h-3.5 w-3.5" /> 下载 Skill 包
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── 数据同步（离线归档包）──────────────────────────────── */
function DataSyncSection({ showToast, canExport, canImport, onArticlesChanged }) {
  const [exportFilters, setExportFilters] = useState({
    fetched_date_start: '',
    fetched_date_end: '',
    source_ids: '',
    limit: 1000,
    skip: 0,
    has_content: 'true',
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
    source_ids: exportFilters.source_ids,
    limit: Math.max(1, Math.min(5000, Number(exportFilters.limit) || 1000)),
    skip: Math.max(0, Number(exportFilters.skip) || 0),
    has_content: exportFilters.has_content === 'any' ? undefined : exportFilters.has_content,
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
          showToast(`已生成压缩同步包：${count} 篇文章`, 'success');
          return;
        }
        showToast('当前浏览器不支持 gzip 压缩，已改为下载 JSONL 原文', 'info');
      }

      downloadBlob(new Blob([text], { type: 'application/x-ndjson; charset=utf-8' }), `${baseName}.jsonl`);
      showToast(`已生成同步包：${count} 篇文章`, 'success');
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
        hint="用于外网采集层和内网分发层之间离线传递文章归档。建议按 fetched_date 分批导出，导入端可重复导入同一包。"
      />

      {canExport && (
        <div className="surface-card rounded-[12px] p-4">
          <div className="mb-4 flex items-center justify-between gap-4">
            <span>
              <span className="block text-sm font-bold text-slate-700">生成文章同步包</span>
              <span className="tiny-meta">从 collector 导出 JSONL 包，可通过邮件或聊天工具发到内网。</span>
            </span>
            <FileText className="h-5 w-5 text-indigo-500" />
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
              <span className="tiny-meta">来源 ID（可选，逗号分隔）</span>
              <input
                type="text"
                value={exportFilters.source_ids}
                onChange={e => updateExportFilter('source_ids', e.target.value)}
                placeholder="rss_openai_news,rss_anthropic_news"
                className="form-input w-full"
              />
            </label>
            <label className="space-y-1">
              <span className="tiny-meta">跳过条数</span>
              <input
                type="number"
                min={0}
                value={exportFilters.skip}
                onChange={e => updateExportFilter('skip', e.target.value)}
                className="form-input w-full"
              />
            </label>
            <label className="space-y-1">
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
            <label className="space-y-1">
              <span className="tiny-meta">正文过滤</span>
              <select
                value={exportFilters.has_content}
                onChange={e => updateExportFilter('has_content', e.target.value)}
                className="form-input w-full"
              >
                <option value="true">仅含正文</option>
                <option value="false">仅无正文</option>
                <option value="any">全部</option>
              </select>
            </label>
            <label className="flex items-center gap-2 pt-6 text-sm font-bold text-slate-600">
              <input
                type="checkbox"
                checked={compressExport}
                onChange={e => setCompressExport(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-indigo-600"
              />
              gzip 压缩下载
            </label>
          </div>

          <button onClick={handleExport} disabled={exporting} className="action-button action-button-primary mt-4">
            {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            生成并下载同步包
          </button>
        </div>
      )}

      {canImport && (
        <div className={`surface-card rounded-[12px] p-4 ${canExport ? 'mt-4' : ''}`}>
          <div className="mb-4 flex items-center justify-between gap-4">
            <span>
              <span className="block text-sm font-bold text-slate-700">导入文章同步包</span>
              <span className="tiny-meta">在 reader 端导入外网传入的 JSONL 包；重复导入会自动跳过已存在文章。</span>
            </span>
            <Upload className="h-5 w-5 text-emerald-500" />
          </div>

          <label className="block rounded-[12px] border border-dashed border-slate-200 bg-slate-50/70 p-4">
            <span className="block text-sm font-bold text-slate-700">选择归档包</span>
            <span className="tiny-meta mt-1 block">支持 .jsonl；浏览器支持时也可直接导入 .jsonl.gz。</span>
            <input
              type="file"
              accept=".jsonl,.gz,.jsonl.gz,application/x-ndjson,application/gzip"
              onChange={e => setImportFile(e.target.files?.[0] || null)}
              className="mt-3 block w-full text-sm font-semibold text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-white file:px-3 file:py-2 file:text-sm file:font-bold file:text-indigo-600 file:shadow-sm"
            />
          </label>

          <button onClick={handleImport} disabled={importing || !importFile} className="action-button action-button-primary mt-4">
            {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            导入同步包
          </button>

          {importResult && (
            <div className="mt-4 rounded-[12px] border border-slate-100 bg-slate-50/80 px-4 py-3">
              <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div><span className="tiny-meta block">新增</span><b>{importResult.imported_count}</b></div>
                <div><span className="tiny-meta block">更新</span><b>{importResult.updated_count}</b></div>
                <div><span className="tiny-meta block">跳过</span><b>{importResult.skipped_count}</b></div>
                <div><span className="tiny-meta block">错误</span><b className={importResult.error_count ? 'text-rose-500' : ''}>{importResult.error_count}</b></div>
              </div>
              {importResult.error_count > 0 && (
                <pre className="mt-3 max-h-32 overflow-auto rounded-[10px] bg-white p-3 text-xs font-semibold text-rose-600">
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

/* ── About ───────────────────────────────────────────────── */
function AboutSection({ accountRoleLabel, layerLabel, runtimeRole }) {
  return (
    <div>
      <SectionHeading title="关于" />
      <div className="surface-card rounded-[12px] px-4">
        <FieldRow label="产品">哆啦美·归档中枢</FieldRow>
        <FieldRow label="运行角色">
          <span className="font-mono text-xs">{runtimeRole}</span>
          <span className="tiny-meta ml-1">（{layerLabel}）</span>
        </FieldRow>
        <FieldRow label="账户角色">{accountRoleLabel}</FieldRow>
      </div>
      <p className="tiny-meta mt-3">
        运行角色由 <code className="font-mono">backend.ini</code> 的 <code className="font-mono">[runtime] role</code> 决定，修改后需重启生效。
      </p>
    </div>
  );
}

export default function SettingsModal({ open, onClose, runtimeInfo, username, onLogout, showToast, onArticlesChanged }) {
  const collectorEnabled = Boolean(runtimeInfo?.collector_enabled);
  const readerEnabled = Boolean(runtimeInfo?.reader_enabled);
  const ragEnabled = Boolean(runtimeInfo?.rag_enabled);
  const accountRole = runtimeInfo?.account_role;
  const isAdmin = accountRole === 'admin';

  const accountRoleLabel = useMemo(() => {
    if (accountRole === 'admin') return '管理员（采集 + 读者）';
    if (accountRole === 'user') return '订阅读者';
    return '—';
  }, [accountRole]);

  const layerLabel = useMemo(() => {
    if (collectorEnabled && readerEnabled) return '双层一体';
    if (collectorEnabled) return '采集归档层';
    if (readerEnabled) return '分发订阅层';
    return '无可用层';
  }, [collectorEnabled, readerEnabled]);

  const sections = useMemo(() => [
    { id: 'account', label: '账户', icon: User, show: true },
    { id: 'vector', label: '向量雷达', icon: BarChart2, show: collectorEnabled && ragEnabled },
    { id: 'sync', label: '数据同步', icon: FileText, show: isAdmin && (collectorEnabled || readerEnabled) },
    { id: 'integration', label: '接入集成', icon: Plug2, show: readerEnabled },
    { id: 'about', label: '关于', icon: Info, show: true },
  ].filter(s => s.show), [collectorEnabled, isAdmin, ragEnabled, readerEnabled]);

  const [active, setActive] = useState('account');
  const [mcpStatus, setMcpStatus] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    setActive('account');
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open]);

  useEffect(() => {
    if (!open || !readerEnabled) return;
    fetchMcpStatus().then(setMcpStatus).catch(() => setMcpStatus({ enabled: false, url: null }));
  }, [open, readerEnabled]);

  if (!open) return null;

  return (
    <div className="modal-overlay animate-in fade-in" onMouseDown={onClose}>
      <div className="modal-panel max-w-3xl" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-6 py-4">
          <div className="flex items-center gap-3">
            <SettingsIcon className="h-5 w-5 text-indigo-500" />
            <h3 className="text-lg font-black text-slate-800">设置</h3>
          </div>
          <button onClick={onClose} className="rounded-lg bg-white p-1.5 text-slate-400 shadow-sm hover:text-slate-700" aria-label="关闭">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav className="w-40 shrink-0 space-y-1 border-r border-slate-100 bg-slate-50/60 p-3">
            {sections.map(section => (
              <button
                key={section.id}
                onClick={() => setActive(section.id)}
                className={`flex w-full items-center gap-2 rounded-[10px] px-3 py-2 text-sm font-bold transition-colors ${
                  active === section.id ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                <section.icon className="h-4 w-4" /> {section.label}
              </button>
            ))}
          </nav>

          <div className="flex-1 overflow-y-auto p-6">
            {active === 'account' && (
              <AccountSection username={username} accountRoleLabel={accountRoleLabel} layerLabel={layerLabel} onLogout={onLogout} />
            )}
            {active === 'vector' && collectorEnabled && ragEnabled && (
              <VectorSection showToast={showToast} />
            )}
            {active === 'sync' && isAdmin && (collectorEnabled || readerEnabled) && (
              <DataSyncSection
                showToast={showToast}
                canExport={collectorEnabled}
                canImport={readerEnabled}
                onArticlesChanged={onArticlesChanged}
              />
            )}
            {active === 'integration' && readerEnabled && (
              <IntegrationSection
                showToast={showToast}
                mcpStatus={mcpStatus}
                canToggle={collectorEnabled}
                onMcpToggled={enabled => setMcpStatus(prev => ({ ...(prev || {}), enabled }))}
              />
            )}
            {active === 'about' && (
              <AboutSection accountRoleLabel={accountRoleLabel} layerLabel={layerLabel} runtimeRole={runtimeInfo?.role || 'all'} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
