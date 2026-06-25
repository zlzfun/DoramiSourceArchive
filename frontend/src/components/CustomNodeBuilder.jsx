import { useCallback, useEffect, useState } from 'react';
import { Wand2, Play, Save, RefreshCw, Trash2, ExternalLink, Power } from 'lucide-react';
import {
  analyzeSourceUrl,
  previewSourceConfig,
  createSourceConfig,
  fetchSourceConfigs,
  toggleSourceConfig,
  deleteSourceConfig,
  fetchSourceConfigNow,
} from '../api';

// 把后端建议配置（含 params）拍平成可编辑表单状态
function flatten(cfg) {
  const p = cfg.params || {};
  return {
    source_id: cfg.source_id || '',
    name: cfg.name || '',
    source_type: cfg.source_type || 'web',
    url: cfg.url || '',
    category: cfg.category || '',
    description: cfg.description || '',
    source_owner: cfg.source_owner || '',
    source_brand: cfg.source_brand || '',
    source_scope: cfg.source_scope || '',
    source_channel: cfg.source_channel || '',
    provenance_tier: cfg.provenance_tier || '',
    content_tags: Array.isArray(cfg.content_tags) ? cfg.content_tags.join(', ') : (cfg.content_tags || ''),
    signal_strength: cfg.signal_strength || '',
    noise_risk: cfg.noise_risk || '',
    // params
    article_url_patterns: p.article_url_patterns || '',
    exclude_url_patterns: p.exclude_url_patterns || '',
    limit: p.limit ?? 12,
    detail_use_browser: !!p.detail_use_browser,
    target_elements: p.target_elements || '',
    excluded_selector: p.excluded_selector || '',
    wait_for: p.wait_for || '',
    listing_css: p.listing_css || '',
  };
}

// 表单状态 → SourceConfigCreate / preview 载荷
function toPayload(f) {
  const isWeb = f.source_type === 'web' || f.source_type === 'webpage';
  const params = isWeb
    ? {
        site_name: f.name,
        article_url_patterns: f.article_url_patterns,
        exclude_url_patterns: f.exclude_url_patterns,
        limit: Number(f.limit) || 12,
        fetch_detail: true,
        detail_use_browser: f.detail_use_browser,
        target_elements: f.target_elements,
        excluded_selector: f.excluded_selector,
        wait_for: f.wait_for,
        ...(f.listing_css ? { listing_css: f.listing_css } : {}),
      }
    : { limit: Number(f.limit) || 12, fetch_detail_if_missing: true };
  return {
    source_id: f.source_id,
    name: f.name,
    source_type: f.source_type,
    url: f.url,
    category: f.category,
    description: f.description,
    source_owner: f.source_owner,
    source_brand: f.source_brand,
    source_scope: f.source_scope,
    source_channel: f.source_channel,
    provenance_tier: f.provenance_tier,
    content_tags: f.content_tags ? f.content_tags.split(',').map(s => s.trim()).filter(Boolean) : [],
    signal_strength: f.signal_strength,
    noise_risk: f.noise_risk,
    params,
  };
}

function Field({ label, children }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-slate-500">{label}</span>
      {children}
    </label>
  );
}

const inputCls = 'rounded-[var(--r-control)] border border-[var(--dorami-border)] px-3 py-2 text-sm focus:border-blue-400 focus:outline-none';

export default function CustomNodeBuilder({ showToast }) {
  const [url, setUrl] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [form, setForm] = useState(null);
  const [advanced, setAdvanced] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState([]);

  const set = (k, v) => setForm(prev => ({ ...prev, [k]: v }));

  const loadSaved = useCallback(async () => {
    try {
      const rows = await fetchSourceConfigs({}, 200);
      setSaved(rows.filter(r => ['web', 'webpage', 'rss', 'atom'].includes((r.source_type || '').toLowerCase())));
    } catch (e) {
      showToast?.(e.message || '加载已存源失败', 'error');
    }
  }, [showToast]);

  useEffect(() => { loadSaved(); }, [loadSaved]);

  const onAnalyze = async () => {
    if (!url.trim()) return;
    setAnalyzing(true);
    setAnalysis(null);
    setPreview(null);
    try {
      const res = await analyzeSourceUrl(url.trim());
      setAnalysis(res);
      setForm(flatten(res.proposed_config));
    } catch (e) {
      showToast?.(e.message || '分析失败', 'error');
    } finally {
      setAnalyzing(false);
    }
  };

  const onPreview = async () => {
    if (!form) return;
    setPreviewing(true);
    setPreview(null);
    try {
      setPreview(await previewSourceConfig(toPayload(form)));
    } catch (e) {
      showToast?.(e.message || '试抓失败', 'error');
    } finally {
      setPreviewing(false);
    }
  };

  const onSave = async () => {
    if (!form) return;
    setSaving(true);
    try {
      await createSourceConfig(toPayload(form));
      showToast?.(`已保存节点：${form.name}`, 'success');
      setAnalysis(null);
      setForm(null);
      setPreview(null);
      setUrl('');
      loadSaved();
    } catch (e) {
      showToast?.(e.message || '保存失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  const onToggle = async (row) => {
    try {
      await toggleSourceConfig(row.source_id, !row.is_active);
      loadSaved();
    } catch (e) { showToast?.(e.message || '切换失败', 'error'); }
  };

  const onFetchNow = async (row) => {
    try {
      showToast?.(`正在抓取：${row.name}…`, 'info');
      const res = await fetchSourceConfigNow(row.source_id);
      const r = (res.results && res.results[0]) || {};
      showToast?.(`抓取完成：${row.name}（新增 ${r.saved_count ?? r.count ?? 0}）`, 'success');
    } catch (e) { showToast?.(e.message || '抓取失败', 'error'); }
  };

  const onDelete = async (row) => {
    if (!window.confirm(`删除节点「${row.name}」？该操作不可撤销。`)) return;
    try {
      await deleteSourceConfig(row.source_id);
      loadSaved();
    } catch (e) { showToast?.(e.message || '删除失败', 'error'); }
  };

  return (
    <div className="space-y-6">
      {/* 第一步：输入 URL 分析 */}
      <div className="surface-card rounded-[var(--r-overlay)] p-5">
        <div className="flex items-center gap-2 mb-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-[var(--r-control)] bg-violet-50 text-violet-600"><Wand2 className="h-5 w-5" /></div>
          <div>
            <div className="font-semibold text-slate-800">AI 自定义节点</div>
            <div className="text-xs text-slate-500">输入一个文章列表页 URL，自动判断类型、分析结构并生成可抓取的节点配置。</div>
          </div>
        </div>
        <div className="flex gap-2">
          <input
            value={url}
            onChange={e => setUrl(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && onAnalyze()}
            placeholder="https://example.com/news"
            className={`${inputCls} flex-1`}
          />
          <button onClick={onAnalyze} disabled={analyzing || !url.trim()} className="btn-primary inline-flex items-center gap-2">
            {analyzing ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />} 分析
          </button>
        </div>
        {analysis && (
          <div className="mt-3 text-xs text-slate-500 space-y-1">
            <div>
              类型：<span className="font-mono text-slate-700">{analysis.page_type}</span>
              {' · '}LLM：{analysis.llm_used ? '已用' : '未用（启发式）'}
              {analysis.detail_profiled ? ' · 已分析详情容器' : ''}
            </div>
            {(analysis.warnings || []).map((w, i) => <div key={i} className="text-amber-600">⚠️ {w}</div>)}
          </div>
        )}
      </div>

      {/* 第二步：编辑配置 + 预览 */}
      {form && (
        <div className="surface-card rounded-[var(--r-overlay)] p-5 space-y-4">
          <div className="font-semibold text-slate-800">配置（可编辑）</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="节点名称"><input className={inputCls} value={form.name} onChange={e => set('name', e.target.value)} /></Field>
            <Field label="数据源 ID"><input className={inputCls} value={form.source_id} onChange={e => set('source_id', e.target.value)} /></Field>
            <Field label="类型">
              <select className={inputCls} value={form.source_type} onChange={e => set('source_type', e.target.value)}>
                <option value="web">网页列表 (web)</option>
                <option value="rss">RSS/Atom</option>
              </select>
            </Field>
            <Field label="入口 URL"><input className={inputCls} value={form.url} onChange={e => set('url', e.target.value)} /></Field>
            <Field label="业务分类"><input className={inputCls} value={form.category} onChange={e => set('category', e.target.value)} /></Field>
            <Field label="单次上限"><input type="number" className={inputCls} value={form.limit} onChange={e => set('limit', e.target.value)} /></Field>
          </div>

          {(form.source_type === 'web' || form.source_type === 'webpage') && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="文章 URL 模式（逗号分隔）"><input className={inputCls} value={form.article_url_patterns} onChange={e => set('article_url_patterns', e.target.value)} /></Field>
              <Field label="排除 URL 模式（逗号分隔）"><input className={inputCls} value={form.exclude_url_patterns} onChange={e => set('exclude_url_patterns', e.target.value)} /></Field>
            </div>
          )}

          {(form.source_type === 'web' || form.source_type === 'webpage') && (
            <div>
              <button onClick={() => setAdvanced(a => !a)} className="text-sm text-blue-600">{advanced ? '收起' : '展开'}详情 / 治理高级设置</button>
              {advanced && (
                <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                  <label className="flex items-center gap-2 text-sm text-slate-500">
                    <input type="checkbox" checked={form.detail_use_browser} onChange={e => set('detail_use_browser', e.target.checked)} /> 用浏览器渲染详情 (crawl4ai)
                  </label>
                  <div />
                  <Field label="正文容器选择器（逗号分隔）"><input className={inputCls} value={form.target_elements} onChange={e => set('target_elements', e.target.value)} /></Field>
                  <Field label="正文内排除选择器"><input className={inputCls} value={form.excluded_selector} onChange={e => set('excluded_selector', e.target.value)} /></Field>
                  <Field label="渲染等待条件 (css:/js:)"><input className={inputCls} value={form.wait_for} onChange={e => set('wait_for', e.target.value)} /></Field>
                  <Field label="列表 CSS schema (JSON)"><input className={inputCls} value={form.listing_css} onChange={e => set('listing_css', e.target.value)} /></Field>
                  <Field label="来源主体"><input className={inputCls} value={form.source_owner} onChange={e => set('source_owner', e.target.value)} /></Field>
                  <Field label="内容标签（逗号分隔）"><input className={inputCls} value={form.content_tags} onChange={e => set('content_tags', e.target.value)} /></Field>
                </div>
              )}
            </div>
          )}

          <div className="flex gap-2">
            <button onClick={onPreview} disabled={previewing} className="btn-secondary inline-flex items-center gap-2">
              {previewing ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} 试抓预览
            </button>
            <button onClick={onSave} disabled={saving} className="btn-primary inline-flex items-center gap-2">
              <Save className="h-4 w-4" /> 保存为节点
            </button>
          </div>

          {preview && (
            <div className="mt-2 rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] p-3">
              <div className="text-xs text-slate-500 mb-2">试抓 {preview.count} 条 · 有正文 {preview.has_content_count} 条</div>
              <ul className="space-y-2">
                {(preview.entries || []).map((e, i) => (
                  <li key={i} className="text-sm">
                    <a href={e.url} target="_blank" rel="noreferrer" className="font-medium text-slate-800 hover:text-blue-600 inline-flex items-center gap-1">
                      {e.title || '(无标题)'} <ExternalLink className="h-3 w-3" />
                    </a>
                    {e.method && <span className="ml-2 micro-label font-mono text-slate-500">{e.method}</span>}
                    {e.content_preview && <div className="text-xs text-slate-500 line-clamp-2">{e.content_preview}</div>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 第三步：已存自定义源 */}
      <div className="surface-card rounded-[var(--r-overlay)] p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold text-slate-800">已存自定义源（{saved.length}）</div>
          <button onClick={loadSaved} className="text-sm text-slate-500 inline-flex items-center gap-1"><RefreshCw className="h-3.5 w-3.5" /> 刷新</button>
        </div>
        {saved.length === 0 ? (
          <div className="text-sm text-slate-500">暂无自定义源。用上方功能分析一个 URL 并保存即可。</div>
        ) : (
          <ul className="divide-y divide-[var(--dorami-border)]">
            {saved.map(row => (
              <li key={row.source_id} className="flex items-center gap-3 py-2.5">
                <span className={`h-2 w-2 rounded-full ${row.is_active ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-slate-800">{row.name}</div>
                  <div className="truncate text-xs text-slate-500 font-mono">{row.source_id} · {row.source_type} · {row.url}</div>
                </div>
                <button onClick={() => onFetchNow(row)} title="立即抓取" className="icon-btn text-blue-600"><Play className="h-4 w-4" /></button>
                <button onClick={() => onToggle(row)} title={row.is_active ? '禁用' : '启用'} className="icon-btn text-slate-500"><Power className="h-4 w-4" /></button>
                <button onClick={() => onDelete(row)} title="删除" className="icon-btn text-rose-500"><Trash2 className="h-4 w-4" /></button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
