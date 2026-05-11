import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Edit2, Plus, RefreshCw, Save, Settings2, Trash2, XCircle } from 'lucide-react';
import {
  createSourceConfig,
  deleteSourceConfig,
  fetchActiveRssSources,
  fetchSourceConfigNow,
  fetchSourceConfigs,
  toggleSourceConfig,
  updateSourceConfig,
} from '../api';

const EMPTY_FORM = {
  source_id: '',
  name: '',
  source_type: 'rss',
  url: '',
  category: '',
  fetcher_id: '',
  description: '',
  is_active: true,
  fetch_interval_minutes: '',
  cron_expr: '',
  paramsText: '{}',
};

function toForm(source) {
  return {
    source_id: source.source_id || '',
    name: source.name || '',
    source_type: source.source_type || 'rss',
    url: source.url || '',
    category: source.category || '',
    fetcher_id: source.fetcher_id || '',
    description: source.description || '',
    is_active: source.is_active !== false,
    fetch_interval_minutes: source.fetch_interval_minutes ?? '',
    cron_expr: source.cron_expr || '',
    paramsText: JSON.stringify(source.params || {}, null, 2),
  };
}

function parseParams(paramsText) {
  const trimmed = paramsText.trim();
  if (!trimmed) return {};
  const parsed = JSON.parse(trimmed);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('参数 JSON 必须是对象');
  }
  return parsed;
}

export default function SourcesTab({ showToast }) {
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [fetchingSourceId, setFetchingSourceId] = useState('');
  const [batchFetching, setBatchFetching] = useState(false);
  const [filters, setFilters] = useState({ source_type: '', category: '', is_active: '' });
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);

  const {
    source_type: filterSourceType,
    category: filterCategory,
    is_active: filterIsActive,
  } = filters;

  const sourceTypes = useMemo(() => {
    return Array.from(new Set(sources.map(source => source.source_type).filter(Boolean))).sort();
  }, [sources]);

  const categories = useMemo(() => {
    return Array.from(new Set(sources.map(source => source.category).filter(Boolean))).sort();
  }, [sources]);

  const resetForm = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const updateForm = (field, value) => {
    setForm(prev => ({ ...prev, [field]: value }));
  };

  const loadSources = async () => {
    setLoading(true);
    try {
      const data = await fetchSourceConfigs(filters);
      setSources(data);
      setLoadError('');
    } catch (e) {
      showToast(e.message || '数据源加载失败', 'error');
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;

    async function loadInitialSources() {
      try {
        const data = await fetchSourceConfigs({
          source_type: filterSourceType,
          category: filterCategory,
          is_active: filterIsActive,
        });
        if (!cancelled) {
          setSources(data);
          setLoadError('');
        }
      } catch (e) {
        if (!cancelled) {
          setLoadError(e.message || '数据源加载失败');
        }
      }
    }

    loadInitialSources();
    return () => { cancelled = true; };
  }, [filterSourceType, filterCategory, filterIsActive]);

  const handleEdit = (source) => {
    setEditingId(source.source_id);
    setForm(toForm(source));
  };

  const buildPayload = () => {
    const params = parseParams(form.paramsText);
    const intervalValue = form.fetch_interval_minutes === '' ? null : Number(form.fetch_interval_minutes);
    if (intervalValue !== null && (!Number.isFinite(intervalValue) || intervalValue <= 0)) {
      throw new Error('抓取间隔必须是正数');
    }

    return {
      source_id: form.source_id.trim(),
      name: form.name.trim(),
      source_type: form.source_type.trim() || 'rss',
      url: form.url.trim(),
      category: form.category.trim(),
      fetcher_id: form.fetcher_id.trim(),
      description: form.description.trim(),
      is_active: form.is_active,
      fetch_interval_minutes: intervalValue,
      cron_expr: form.cron_expr.trim(),
      params,
    };
  };

  const handleSubmit = async () => {
    try {
      const payload = buildPayload();
      if (!payload.source_id || !payload.name) {
        showToast('source_id 和名称不能为空', 'error');
        return;
      }

      if (editingId) {
        const updatePayload = { ...payload };
        delete updatePayload.source_id;
        await updateSourceConfig(editingId, updatePayload);
        showToast('数据源已更新', 'success');
      } else {
        await createSourceConfig(payload);
        showToast('数据源已创建', 'success');
      }

      resetForm();
      loadSources();
    } catch (e) {
      showToast(e.message || '保存失败', 'error');
    }
  };

  const handleToggle = async (source) => {
    try {
      await toggleSourceConfig(source.source_id, !source.is_active);
      showToast(source.is_active ? '数据源已停用' : '数据源已启用', 'success');
      loadSources();
    } catch (e) {
      showToast(e.message || '状态切换失败', 'error');
    }
  };

  const handleDelete = async (source) => {
    if (!window.confirm(`确定删除数据源「${source.name}」吗？`)) return;
    try {
      await deleteSourceConfig(source.source_id);
      showToast('数据源已删除', 'success');
      if (editingId === source.source_id) resetForm();
      loadSources();
    } catch (e) {
      showToast(e.message || '删除失败', 'error');
    }
  };

  const handleFetchSource = async (source) => {
    setFetchingSourceId(source.source_id);
    try {
      const result = await fetchSourceConfigNow(source.source_id);
      showToast(`抓取完成：新增 ${result.saved_count || 0} 条，跳过 ${result.skipped_count || 0} 条`, 'success');
    } catch (e) {
      showToast(e.message || '触发抓取失败', 'error');
    }
    setFetchingSourceId('');
  };

  const handleBatchFetchRss = async () => {
    setBatchFetching(true);
    try {
      const result = await fetchActiveRssSources();
      const successCount = (result.results || []).filter(item => item.status === 'success').length;
      const failedCount = (result.results || []).filter(item => item.status === 'failed').length;
      showToast(`RSS 批量抓取完成：成功 ${successCount} 个，失败 ${failedCount} 个`, failedCount ? 'info' : 'success');
    } catch (e) {
      showToast(e.message || '批量触发失败', 'error');
    }
    setBatchFetching(false);
  };

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold flex items-center"><Settings2 className="w-6 h-6 mr-2 text-indigo-500" /> 数据源配置</h2>
          <p className="text-sm text-slate-500 mt-2">维护可配置数据源，为后续通用 RSS、GitHub、论文和社区抓取打底。</p>
        </div>
        <button onClick={loadSources} disabled={loading} className="text-sm text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 shadow-sm px-4 py-2 rounded-lg transition-all flex items-center font-bold w-fit">
          <RefreshCw className={`w-4 h-4 mr-2 text-blue-600 ${loading ? 'animate-spin' : ''}`} /> 刷新配置
        </button>
        <button onClick={handleBatchFetchRss} disabled={batchFetching} className="text-sm text-white bg-blue-600 hover:bg-blue-700 shadow-sm px-4 py-2 rounded-lg transition-all flex items-center font-bold w-fit">
          <RefreshCw className={`w-4 h-4 mr-2 ${batchFetching ? 'animate-spin' : ''}`} /> 批量抓取 RSS
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_420px] gap-6 items-start">
        <div className="space-y-4">
          <div className="bg-white border border-slate-200 rounded-2xl shadow-sm p-3 flex flex-col md:flex-row gap-3">
            <select value={filters.source_type} onChange={e => setFilters({ ...filters, source_type: e.target.value })} className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 outline-none">
              <option value="">全部类型</option>
              {sourceTypes.map(type => <option key={type} value={type}>{type}</option>)}
            </select>
            <select value={filters.category} onChange={e => setFilters({ ...filters, category: e.target.value })} className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 outline-none">
              <option value="">全部分类</option>
              {categories.map(category => <option key={category} value={category}>{category}</option>)}
            </select>
            <select value={filters.is_active} onChange={e => setFilters({ ...filters, is_active: e.target.value })} className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 outline-none">
              <option value="">全部状态</option>
              <option value="true">启用</option>
              <option value="false">停用</option>
            </select>
          </div>

          <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
            {loadError && <div className="px-4 py-3 bg-red-50 border-b border-red-100 text-red-700 text-sm font-bold">{loadError}</div>}
            <table className="w-full text-left border-collapse">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs tracking-wider">
                <tr>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">数据源</th>
                  <th className="px-4 py-3">类型/分类</th>
                  <th className="px-4 py-3">调度建议</th>
                  <th className="px-4 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-sm">
                {sources.length === 0 ? (
                  <tr><td colSpan="5" className="px-6 py-16 text-center text-slate-400 font-medium">暂无数据源配置</td></tr>
                ) : sources.map(source => (
                  <tr key={source.source_id} className="hover:bg-blue-50/40 transition-colors">
                    <td className="px-4 py-4">
                      {source.is_active ? (
                        <span className="inline-flex items-center text-xs font-black text-emerald-700 bg-emerald-50 border border-emerald-100 px-2 py-1 rounded-lg"><CheckCircle2 className="w-3.5 h-3.5 mr-1" /> 启用</span>
                      ) : (
                        <span className="inline-flex items-center text-xs font-black text-slate-500 bg-slate-100 border border-slate-200 px-2 py-1 rounded-lg"><XCircle className="w-3.5 h-3.5 mr-1" /> 停用</span>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      <div className="font-extrabold text-slate-800">{source.name}</div>
                      <div className="font-mono text-[11px] text-slate-400 mt-0.5">{source.source_id}</div>
                      {source.url && <div className="text-xs text-blue-600 truncate max-w-sm mt-1" title={source.url}>{source.url}</div>}
                    </td>
                    <td className="px-4 py-4">
                      <div className="text-xs font-black text-indigo-700 bg-indigo-50 border border-indigo-100 inline-block px-2 py-1 rounded-lg">{source.source_type}</div>
                      {source.category && <div className="text-xs text-slate-500 mt-1">{source.category}</div>}
                    </td>
                    <td className="px-4 py-4 text-xs text-slate-600">
                      {source.cron_expr ? <div className="font-mono bg-slate-100 px-2 py-1 rounded inline-block">{source.cron_expr}</div> : <div>-</div>}
                      {source.fetch_interval_minutes ? <div className="mt-1">每 {source.fetch_interval_minutes} 分钟</div> : null}
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex justify-end gap-2">
                        <button onClick={() => handleEdit(source)} className="p-2 text-slate-500 hover:text-blue-600 bg-slate-50 hover:bg-blue-50 rounded-lg transition-colors" title="编辑">
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button onClick={() => handleToggle(source)} className="px-3 py-2 text-xs font-bold text-slate-600 bg-slate-50 hover:bg-slate-100 rounded-lg transition-colors">
                          {source.is_active ? '停用' : '启用'}
                        </button>
                        <button onClick={() => handleFetchSource(source)} disabled={!source.is_active || fetchingSourceId === source.source_id} className="px-3 py-2 text-xs font-bold text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors disabled:text-slate-300 disabled:bg-slate-50">
                          {fetchingSourceId === source.source_id ? '抓取中' : '抓取'}
                        </button>
                        <button onClick={() => handleDelete(source)} className="p-2 text-red-500 hover:text-red-700 bg-red-50 hover:bg-red-100 rounded-lg transition-colors" title="删除">
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-2xl shadow-sm p-5 sticky top-24">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-extrabold text-slate-800 flex items-center">
              {editingId ? <Edit2 className="w-4 h-4 mr-2 text-blue-500" /> : <Plus className="w-4 h-4 mr-2 text-emerald-500" />}
              {editingId ? '编辑数据源' : '新增数据源'}
            </h3>
            {editingId && <button onClick={resetForm} className="text-xs font-bold text-slate-500 hover:text-slate-800">取消编辑</button>}
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-bold text-slate-500">
                Source ID
                <input value={form.source_id} disabled={Boolean(editingId)} onChange={e => updateForm('source_id', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500 disabled:text-slate-400 font-mono" placeholder="openai_blog" />
              </label>
              <label className="text-xs font-bold text-slate-500">
                名称
                <input value={form.name} onChange={e => updateForm('name', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500" placeholder="OpenAI Blog" />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-bold text-slate-500">
                类型
                <input value={form.source_type} onChange={e => updateForm('source_type', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500 font-mono" placeholder="rss" />
              </label>
              <label className="text-xs font-bold text-slate-500">
                分类
                <input value={form.category} onChange={e => updateForm('category', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500" placeholder="official" />
              </label>
            </div>

            <label className="text-xs font-bold text-slate-500 block">
              URL
              <input value={form.url} onChange={e => updateForm('url', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500" placeholder="https://example.com/rss.xml" />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-bold text-slate-500">
                绑定 Fetcher
                <input value={form.fetcher_id} onChange={e => updateForm('fetcher_id', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500 font-mono" placeholder="generic_rss" />
              </label>
              <label className="text-xs font-bold text-slate-500">
                间隔分钟
                <input type="number" min="1" value={form.fetch_interval_minutes} onChange={e => updateForm('fetch_interval_minutes', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500" placeholder="60" />
              </label>
            </div>

            <label className="text-xs font-bold text-slate-500 block">
              Cron 建议
              <input value={form.cron_expr} onChange={e => updateForm('cron_expr', e.target.value)} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500 font-mono" placeholder="0 * * * *" />
            </label>

            <label className="text-xs font-bold text-slate-500 block">
              说明
              <textarea value={form.description} onChange={e => updateForm('description', e.target.value)} rows="2" className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg outline-none focus:border-blue-500 resize-none" />
            </label>

            <label className="text-xs font-bold text-slate-500 block">
              参数 JSON
              <textarea value={form.paramsText} onChange={e => updateForm('paramsText', e.target.value)} rows="6" className="mt-1 w-full px-3 py-2 bg-slate-900 text-emerald-300 border border-slate-700 rounded-lg outline-none focus:border-blue-500 font-mono text-xs" />
            </label>

            <label className="flex items-center gap-2 text-sm font-bold text-slate-600">
              <input type="checkbox" checked={form.is_active} onChange={e => updateForm('is_active', e.target.checked)} className="w-4 h-4 text-blue-600 rounded" />
              启用该数据源
            </label>

            <button onClick={handleSubmit} className="w-full px-4 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-xl font-extrabold flex items-center justify-center shadow-md transition-colors">
              <Save className="w-4 h-4 mr-2" /> {editingId ? '保存修改' : '创建数据源'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
