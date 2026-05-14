import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckSquare,
  ChevronDown,
  ChevronRight,
  FolderPlus,
  Layers,
  Play,
  RefreshCw,
  Save,
  Search,
  Server,
  Trash2,
  X,
} from 'lucide-react';
import {
  createNodeGroup,
  deleteNodeGroup,
  fetchNodeGroups,
  fetchSourceHealth,
  runNodeGroup,
  triggerFetch,
  updateNodeGroup,
} from '../api';

const CATEGORY_LABELS = {
  official: '官方动态',
  official_web: '官网网页',
  framework: '框架生态',
  paper: '论文源',
  developer_platform: '开发平台',
  community: '社区资讯',
  product_update: '版本发布',
  wechat: '微信公众号',
  workflow: '后置编排',
  advanced: '高级通用',
  general: '其他',
};

const CATEGORY_ORDER = ['official', 'official_web', 'framework', 'paper', 'product_update', 'developer_platform', 'community', 'wechat', 'workflow', 'advanced', 'general'];

function getCategoryLabel(category) {
  return CATEGORY_LABELS[category] || category || '其他';
}

function getCategoryRank(category) {
  const index = CATEGORY_ORDER.indexOf(category || 'general');
  return index === -1 ? CATEGORY_ORDER.length : index;
}

function formatDateTime(value) {
  if (!value) return '从未运行';
  return value.replace('T', ' ').substring(0, 19);
}

function healthMeta(status) {
  if (status === 'healthy') return { label: '健康', className: 'bg-emerald-50 text-emerald-700 border-emerald-100' };
  if (status === 'failing') return { label: '失败', className: 'bg-red-50 text-red-700 border-red-100' };
  if (status === 'running') return { label: '运行中', className: 'bg-amber-50 text-amber-700 border-amber-100' };
  if (status === 'never_run') return { label: '未运行', className: 'bg-slate-50 text-slate-500 border-slate-200' };
  return { label: '未知', className: 'bg-slate-50 text-slate-500 border-slate-200' };
}

function blankGroup(fetcherIds = [], configs = {}) {
  return {
    name: '',
    description: '',
    fetcher_ids: fetcherIds,
    params: {},
    per_fetcher_params: Object.fromEntries(fetcherIds.map(id => [id, configs[id] || {}])),
    cron_expr: '',
    per_fetcher_cron: {},
    is_active: true,
  };
}

function normalizeIds(ids) {
  return Array.from(new Set((ids || []).filter(Boolean)));
}

export default function FetchTab({ availableFetchers, showToast }) {
  const [view, setView] = useState('catalog');
  const [fetchLoading, setFetchLoading] = useState(false);
  const [nodeGroups, setNodeGroups] = useState([]);
  const [healthByFetcher, setHealthByFetcher] = useState({});
  const [selectedFetchers, setSelectedFetchers] = useState([]);
  const [fetchConfigs, setFetchConfigs] = useState({});
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedGroupId, setExpandedGroupId] = useState(null);
  const [groupModalOpen, setGroupModalOpen] = useState(false);
  const [editingGroupId, setEditingGroupId] = useState(null);
  const [groupDraft, setGroupDraft] = useState(blankGroup());
  const [modalSearch, setModalSearch] = useState('');

  const fetchersById = useMemo(
    () => Object.fromEntries(availableFetchers.map(fetcher => [fetcher.id, fetcher])),
    [availableFetchers]
  );

  useEffect(() => {
    const initialConfigs = {};
    availableFetchers.forEach(fetcher => {
      initialConfigs[fetcher.id] = {};
      (fetcher.parameters || []).forEach(param => {
        initialConfigs[fetcher.id][param.field] = param.default;
      });
    });
    setFetchConfigs(initialConfigs);
  }, [availableFetchers]);

  const loadNodeGroups = useCallback(async () => {
    try {
      setNodeGroups(await fetchNodeGroups());
    } catch (e) { console.error(e); }
  }, []);

  const loadSourceHealth = useCallback(async () => {
    try {
      const healthItems = await fetchSourceHealth();
      setHealthByFetcher(Object.fromEntries(healthItems.map(item => [item.fetcher_id, item])));
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => {
    loadNodeGroups();
    loadSourceHealth();
  }, [loadNodeGroups, loadSourceHealth]);

  const categoryOptions = useMemo(() => {
    const counts = availableFetchers.reduce((acc, fetcher) => {
      const category = fetcher.category || 'general';
      acc[category] = (acc[category] || 0) + 1;
      return acc;
    }, {});

    return Object.entries(counts)
      .sort(([a], [b]) => getCategoryRank(a) - getCategoryRank(b) || getCategoryLabel(a).localeCompare(getCategoryLabel(b), 'zh-Hans-CN'))
      .map(([category, count]) => ({ category, count }));
  }, [availableFetchers]);

  const filteredFetchers = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return [...availableFetchers]
      .filter(fetcher => categoryFilter === 'all' || (fetcher.category || 'general') === categoryFilter)
      .filter(fetcher => {
        if (!query) return true;
        return [fetcher.name, fetcher.id, fetcher.desc, fetcher.content_type, getCategoryLabel(fetcher.category)]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(query);
      })
      .sort((a, b) => {
        const categoryDelta = getCategoryRank(a.category) - getCategoryRank(b.category);
        if (categoryDelta !== 0) return categoryDelta;
        return a.name.localeCompare(b.name, 'zh-Hans-CN');
      });
  }, [availableFetchers, categoryFilter, searchQuery]);

  const modalFetchers = useMemo(() => {
    const query = modalSearch.trim().toLowerCase();
    if (!query) return availableFetchers;
    return availableFetchers.filter(fetcher => [fetcher.name, fetcher.id, fetcher.desc].filter(Boolean).join(' ').toLowerCase().includes(query));
  }, [availableFetchers, modalSearch]);

  const getFetcherName = (id) => fetchersById[id]?.name || id;

  const toggleFetcherSelection = (id) => {
    setSelectedFetchers(prev => prev.includes(id) ? prev.filter(fid => fid !== id) : [...prev, id]);
  };

  const updateModalNodeParam = (fetcherId, field, value) => {
    setGroupDraft(prev => ({
      ...prev,
      per_fetcher_params: {
        ...(prev.per_fetcher_params || {}),
        [fetcherId]: {
          ...((prev.per_fetcher_params || {})[fetcherId] || {}),
          [field]: value,
        },
      },
    }));
  };

  const openCreateGroup = (fetcherIds = selectedFetchers) => {
    setEditingGroupId(null);
    setGroupDraft(blankGroup(fetcherIds, fetchConfigs));
    setModalSearch('');
    setGroupModalOpen(true);
  };

  const openEditGroup = (group) => {
    setEditingGroupId(group.id);
    setGroupDraft({
      name: group.name || '',
      description: group.description || '',
      fetcher_ids: group.fetcher_ids || [],
      params: group.params || {},
      per_fetcher_params: group.per_fetcher_params || {},
      cron_expr: '',
      per_fetcher_cron: {},
      is_active: group.is_active !== false,
    });
    setModalSearch('');
    setGroupModalOpen(true);
  };

  const handleSaveGroup = async () => {
    if (!groupDraft.name.trim()) {
      showToast('节点组名称不能为空', 'error');
      return;
    }
    if ((groupDraft.fetcher_ids || []).length === 0) {
      showToast('节点组至少需要一个节点', 'error');
      return;
    }
    const payload = {
      ...groupDraft,
      name: groupDraft.name.trim(),
      fetcher_ids: normalizeIds(groupDraft.fetcher_ids),
      cron_expr: '',
      per_fetcher_cron: {},
    };
    try {
      const saved = editingGroupId ? await updateNodeGroup(editingGroupId, payload) : await createNodeGroup(payload);
      setExpandedGroupId(saved.id);
      setGroupModalOpen(false);
      setSelectedFetchers([]);
      await loadNodeGroups();
      showToast('节点组已保存', 'success');
    } catch (e) { showToast(e.message || '保存节点组失败', 'error'); }
  };

  const handleDeleteGroup = async (id) => {
    if (!window.confirm('确定删除该节点组？')) return;
    try {
      await deleteNodeGroup(id);
      if (expandedGroupId === id) setExpandedGroupId(null);
      await loadNodeGroups();
      showToast('节点组已删除', 'success');
    } catch (e) { showToast(e.message || '删除节点组失败', 'error'); }
  };

  const handleRunGroup = async (id) => {
    try {
      const result = await runNodeGroup(id);
      showToast(`节点组运行完成：新增 ${result.saved_count || 0} 条`, result.failed_count ? 'info' : 'success');
      loadSourceHealth();
    } catch (e) { showToast(e.message || '节点组运行失败', 'error'); }
  };

  const handleBatchFetch = async () => {
    setFetchLoading(true);
    let successCount = 0;
    for (const fetcherId of selectedFetchers) {
      try {
        await triggerFetch(fetcherId, fetchConfigs[fetcherId] || {});
        successCount++;
      } catch (e) { showToast(e.message || `[${getFetcherName(fetcherId)}] 抓取失败`, 'error'); }
    }
    setFetchLoading(false);
    if (successCount > 0) {
      showToast(`已触发 ${successCount} 个节点`, 'success');
      setSelectedFetchers([]);
      loadSourceHealth();
    }
  };

  const renderParamInput = (fetcherId, param) => {
    const params = (groupDraft.per_fetcher_params || {})[fetcherId] || {};
    const value = params[param.field] ?? param.default ?? '';
    if (param.type === 'boolean') {
      const checked = typeof value === 'boolean' ? value : ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
      return (
        <input
          type="checkbox"
          checked={checked}
          onChange={event => updateModalNodeParam(fetcherId, param.field, event.target.checked)}
          className="w-4 h-4 text-blue-600 rounded border-slate-300"
        />
      );
    }
    return (
      <input
        type={param.type || 'text'}
        value={value}
        onChange={event => updateModalNodeParam(fetcherId, param.field, param.type === 'number' ? Number(event.target.value) : event.target.value)}
        className="w-full px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg text-xs font-bold text-slate-700 outline-none focus:border-blue-500"
      />
    );
  };

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold flex items-center"><Server className="w-6 h-6 mr-2 text-indigo-500" /> 节点管理</h2>
          <p className="text-sm text-slate-500 mt-2">管理抓取节点目录与可复用节点组。</p>
        </div>
        <div className="flex bg-slate-100 border border-slate-200 rounded-xl p-1 w-fit">
          <button onClick={() => setView('catalog')} className={`px-4 py-2 rounded-lg text-sm font-bold ${view === 'catalog' ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>节点目录</button>
          <button onClick={() => setView('groups')} className={`px-4 py-2 rounded-lg text-sm font-bold ${view === 'groups' ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>节点组</button>
        </div>
      </div>

      {view === 'catalog' && (
        <>
          <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-4 space-y-4">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-bold text-slate-700">
                <Layers className="w-4 h-4 text-indigo-500" />
                <span>内置节点目录</span>
                <span className="text-xs text-slate-400 font-mono">{filteredFetchers.length}/{availableFetchers.length}</span>
              </div>
              <div className="relative w-full lg:w-80">
                <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input value={searchQuery} onChange={event => setSearchQuery(event.target.value)} placeholder="搜索名称、ID、类型" className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-bold text-slate-700 outline-none focus:border-blue-500" />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setCategoryFilter('all')} className={`px-3 py-1.5 rounded-lg text-xs font-bold border ${categoryFilter === 'all' ? 'bg-blue-600 border-blue-600 text-white' : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'}`}>全部 {availableFetchers.length}</button>
              {categoryOptions.map(({ category, count }) => (
                <button key={category} onClick={() => setCategoryFilter(category)} className={`px-3 py-1.5 rounded-lg text-xs font-bold border ${categoryFilter === category ? 'bg-blue-600 border-blue-600 text-white' : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'}`}>
                  {getCategoryLabel(category)} {count}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {filteredFetchers.map(fetcher => {
              const isSelected = selectedFetchers.includes(fetcher.id);
              const health = healthByFetcher[fetcher.id];
              const healthInfo = healthMeta(health?.health_status);
              return (
                <div key={fetcher.id} className={`bg-white border-2 rounded-xl flex flex-col transition-all shadow-sm ${isSelected ? 'border-blue-500 ring-4 ring-blue-500/10' : 'border-slate-200 hover:border-blue-300'}`}>
                  <div onClick={() => toggleFetcherSelection(fetcher.id)} className="p-4 flex items-start gap-3 cursor-pointer border-b border-slate-100 bg-slate-50/50 rounded-t-lg hover:bg-slate-100/70">
                    <div className={`mt-1.5 w-5 h-5 shrink-0 rounded border flex items-center justify-center ${isSelected ? 'bg-blue-600 border-blue-600' : 'border-slate-300'}`}>
                      {isSelected && <CheckSquare className="w-4 h-4 text-white" />}
                    </div>
                    <div className="w-11 h-11 shrink-0 bg-white border border-slate-200 rounded-lg flex items-center justify-center text-2xl shadow-sm">{fetcher.icon}</div>
                    <div className="flex-1 min-w-0">
                      <h3 className="font-extrabold text-slate-800 text-sm leading-snug">{fetcher.name}</h3>
                      <div className="flex items-center gap-2 mt-1.5 min-w-0">
                        <span className="text-[10px] text-blue-700 font-bold bg-blue-50 border border-blue-100 px-1.5 py-0.5 rounded truncate">{getCategoryLabel(fetcher.category)}</span>
                        <span className={`text-[10px] font-bold border px-1.5 py-0.5 rounded truncate ${healthInfo.className}`}>{healthInfo.label}</span>
                        <span className="text-[10px] text-slate-500 font-mono bg-slate-200/50 px-1.5 py-0.5 rounded truncate">{fetcher.id}</span>
                      </div>
                    </div>
                  </div>
                  <div className="p-4 bg-white rounded-b-lg flex-1 flex flex-col gap-3">
                    <p className="text-xs text-slate-500 leading-relaxed min-h-[34px]">{fetcher.desc}</p>
                    <div className="grid grid-cols-3 gap-2 text-[11px]">
                      <div className="bg-slate-50 border border-slate-100 rounded-lg px-2 py-1.5">
                        <div className="text-slate-400 font-bold">最近运行</div>
                        <div className="text-slate-700 font-mono truncate" title={formatDateTime(health?.latest_run_at)}>{formatDateTime(health?.latest_run_at)}</div>
                      </div>
                      <div className="bg-slate-50 border border-slate-100 rounded-lg px-2 py-1.5">
                        <div className="text-slate-400 font-bold">最近新增</div>
                        <div className="text-emerald-700 font-black">{health?.latest_saved_count ?? 0}</div>
                      </div>
                      <div className="bg-slate-50 border border-slate-100 rounded-lg px-2 py-1.5">
                        <div className="text-slate-400 font-bold">连续失败</div>
                        <div className="text-red-700 font-black">{health?.consecutive_failures ?? 0}</div>
                      </div>
                    </div>
                    <button onClick={() => triggerFetch(fetcher.id, fetchConfigs[fetcher.id] || {}).then(() => { showToast('已触发临时抓取', 'success'); loadSourceHealth(); }).catch(e => showToast(e.message || '抓取失败', 'error'))} className="w-full px-3 py-2 rounded-lg bg-blue-50 text-blue-700 border border-blue-100 text-xs font-bold hover:bg-blue-100 flex items-center justify-center">
                      <Play className="w-3.5 h-3.5 mr-1.5" /> 临时抓取
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {view === 'groups' && (
        <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
            <div className="flex items-center">
              <FolderPlus className="w-5 h-5 text-indigo-600 mr-2" />
              <h3 className="font-bold text-slate-700 text-sm">节点组</h3>
              <span className="ml-2 text-xs font-mono text-slate-400">{nodeGroups.length}</span>
            </div>
            <button onClick={() => openCreateGroup([])} className="px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-bold hover:bg-blue-700 flex items-center">
              <FolderPlus className="w-3.5 h-3.5 mr-1.5" /> 新建节点组
            </button>
          </div>
          <div className="divide-y divide-slate-100">
            {nodeGroups.length === 0 ? (
              <div className="p-12 text-center text-slate-400 font-medium">暂无节点组</div>
            ) : nodeGroups.map(group => {
              const isExpanded = expandedGroupId === group.id;
              return (
                <div key={group.id}>
                  <button onClick={() => setExpandedGroupId(isExpanded ? null : group.id)} className="w-full px-5 py-4 flex items-center justify-between hover:bg-slate-50 text-left">
                    <div className="min-w-0">
                      <div className="flex items-center">
                        {isExpanded ? <ChevronDown className="w-4 h-4 text-slate-400 mr-2" /> : <ChevronRight className="w-4 h-4 text-slate-400 mr-2" />}
                        <div className="font-extrabold text-slate-800 text-sm truncate">{group.name}</div>
                      </div>
                      <div className="text-xs text-slate-400 mt-1 ml-6">{(group.fetcher_ids || []).length} 个节点 · {group.description || '无说明'}</div>
                    </div>
                    <div className="text-xs font-bold text-slate-500">{(group.fetcher_ids || []).slice(0, 3).map(getFetcherName).join('、')}</div>
                  </button>
                  {isExpanded && (
                    <div className="px-5 pb-5 ml-6 space-y-4">
                      <div className="flex flex-wrap gap-2">
                        <button onClick={() => openEditGroup(group)} className="px-3 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold hover:bg-slate-200">编辑</button>
                        <button onClick={() => handleRunGroup(group.id)} className="px-3 py-2 rounded-lg bg-emerald-600 text-white text-xs font-bold hover:bg-emerald-700 flex items-center"><Play className="w-3.5 h-3.5 mr-1.5" /> 临时运行</button>
                        <button onClick={() => handleDeleteGroup(group.id)} className="px-3 py-2 rounded-lg bg-red-50 text-red-600 border border-red-100 text-xs font-bold hover:bg-red-100 flex items-center"><Trash2 className="w-3.5 h-3.5 mr-1.5" /> 删除</button>
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {(group.fetcher_ids || []).map(fetcherId => (
                          <div key={fetcherId} className="border border-slate-200 rounded-lg p-3 bg-slate-50">
                            <div className="font-bold text-slate-700 text-sm">{getFetcherName(fetcherId)}</div>
                            <div className="font-mono text-[11px] text-slate-400 mt-1">{fetcherId}</div>
                            <code className="block mt-2 text-[11px] text-slate-500 bg-white border border-slate-100 rounded px-2 py-1 truncate" title={JSON.stringify((group.per_fetcher_params || {})[fetcherId] || {})}>
                              {JSON.stringify((group.per_fetcher_params || {})[fetcherId] || {})}
                            </code>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {selectedFetchers.length > 0 && view === 'catalog' && (
        <div className="fixed bottom-0 left-0 w-full bg-white/90 backdrop-blur-xl border-t border-slate-200 p-4 z-40 flex justify-center items-center shadow-[0_-10px_40px_-15px_rgba(0,0,0,0.1)] animate-in slide-in-from-bottom-full">
          <div className="max-w-7xl w-full flex flex-col md:flex-row justify-between items-center px-6 gap-4">
            <span className="font-extrabold text-blue-700 flex items-center bg-blue-50 px-4 py-2 rounded-xl"><CheckSquare className="w-5 h-5 mr-2" /> 已选择 {selectedFetchers.length} 个节点</span>
            <div className="flex flex-wrap items-center gap-3">
              <button onClick={() => openCreateGroup(selectedFetchers)} className="px-4 py-2.5 bg-indigo-50 text-indigo-700 border border-indigo-100 font-bold rounded-xl hover:bg-indigo-100 text-sm flex items-center">
                <FolderPlus className="w-4 h-4 mr-1.5" /> 新建节点组
              </button>
              <button onClick={handleBatchFetch} disabled={fetchLoading} className="px-6 py-2.5 bg-blue-600 text-white font-extrabold rounded-xl hover:bg-blue-700 text-sm flex items-center shadow-md">
                {fetchLoading ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Play className="w-4 h-4 mr-1.5 fill-current" />} {fetchLoading ? '执行中...' : '立即临时抓取'}
              </button>
            </div>
          </div>
        </div>
      )}

      {groupModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col">
            <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
              <div>
                <h3 className="font-extrabold text-slate-800">{editingGroupId ? '编辑节点组' : '新建节点组'}</h3>
                <p className="text-xs text-slate-400 mt-1">节点组只维护节点集合和参数模板。</p>
              </div>
              <button onClick={() => setGroupModalOpen(false)} className="p-2 rounded-lg hover:bg-slate-200 text-slate-500"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-5 overflow-auto space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="text-xs font-bold text-slate-500">
                  名称
                  <input value={groupDraft.name} onChange={event => setGroupDraft(prev => ({ ...prev, name: event.target.value }))} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-bold text-slate-700 outline-none focus:border-blue-500" />
                </label>
                <label className="text-xs font-bold text-slate-500">
                  说明
                  <input value={groupDraft.description} onChange={event => setGroupDraft(prev => ({ ...prev, description: event.target.value }))} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-700 outline-none focus:border-blue-500" />
                </label>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5">
                <div className="border border-slate-200 rounded-xl overflow-hidden">
                  <div className="p-3 bg-slate-50 border-b border-slate-200">
                    <div className="relative">
                      <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input value={modalSearch} onChange={event => setModalSearch(event.target.value)} placeholder="搜索节点" className="w-full pl-9 pr-3 py-2 bg-white border border-slate-200 rounded-lg text-sm font-bold outline-none focus:border-blue-500" />
                    </div>
                  </div>
                  <div className="max-h-[420px] overflow-auto divide-y divide-slate-100">
                    {modalFetchers.map(fetcher => {
                      const checked = (groupDraft.fetcher_ids || []).includes(fetcher.id);
                      return (
                        <button
                          key={fetcher.id}
                          onClick={() => setGroupDraft(prev => {
                            const ids = checked ? prev.fetcher_ids.filter(id => id !== fetcher.id) : [...(prev.fetcher_ids || []), fetcher.id];
                            return {
                              ...prev,
                              fetcher_ids: normalizeIds(ids),
                              per_fetcher_params: checked
                                ? prev.per_fetcher_params
                                : { ...(prev.per_fetcher_params || {}), [fetcher.id]: fetchConfigs[fetcher.id] || {} },
                            };
                          })}
                          className={`w-full px-3 py-3 flex items-center gap-3 text-left hover:bg-slate-50 ${checked ? 'bg-blue-50/60' : ''}`}
                        >
                          <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${checked ? 'bg-blue-600 border-blue-600' : 'border-slate-300'}`}>{checked && <CheckSquare className="w-3.5 h-3.5 text-white" />}</div>
                          <div className="min-w-0">
                            <div className="font-bold text-slate-700 text-sm truncate">{fetcher.name}</div>
                            <div className="font-mono text-[11px] text-slate-400 truncate">{fetcher.id}</div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-3">
                  {(groupDraft.fetcher_ids || []).length === 0 ? (
                    <div className="border border-dashed border-slate-200 rounded-xl p-10 text-center text-slate-400 font-medium">未选择节点</div>
                  ) : (groupDraft.fetcher_ids || []).map(fetcherId => {
                    const fetcher = fetchersById[fetcherId];
                    return (
                      <div key={fetcherId} className="border border-slate-200 rounded-xl p-3 bg-white">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="font-extrabold text-slate-800 text-sm truncate">{fetcher?.name || fetcherId}</div>
                            <div className="font-mono text-[11px] text-slate-400 mt-0.5">{fetcherId}</div>
                          </div>
                          <button onClick={() => setGroupDraft(prev => ({ ...prev, fetcher_ids: prev.fetcher_ids.filter(id => id !== fetcherId) }))} className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg"><X className="w-4 h-4" /></button>
                        </div>
                        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                          {(fetcher?.parameters || []).length === 0 ? (
                            <div className="text-xs text-slate-400 font-medium bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">该节点无需扩展参数</div>
                          ) : (fetcher.parameters || []).map(param => (
                            <label key={param.field} className="text-xs font-bold text-slate-500">
                              {param.label}
                              <div className="mt-1">{renderParamInput(fetcherId, param)}</div>
                            </label>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
            <div className="px-5 py-4 border-t border-slate-200 bg-white flex justify-end gap-2">
              <button onClick={() => setGroupModalOpen(false)} className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 text-sm font-bold hover:bg-slate-200">取消</button>
              <button onClick={handleSaveGroup} className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold hover:bg-blue-700 flex items-center"><Save className="w-4 h-4 mr-1.5" /> 保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
