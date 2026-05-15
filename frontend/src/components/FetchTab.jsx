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
  Settings2,
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
  const [expandedParamFetcherId, setExpandedParamFetcherId] = useState(null);
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

  useEffect(() => {
    if (!groupModalOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [groupModalOpen]);

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

  const updateFetcherConfig = (fetcherId, field, value) => {
    setFetchConfigs(prev => ({
      ...prev,
      [fetcherId]: {
        ...(prev[fetcherId] || {}),
        [field]: value,
      },
    }));
  };

  const renderCatalogParamInput = (fetcherId, param) => {
    const value = (fetchConfigs[fetcherId] || {})[param.field] ?? param.default ?? '';
    if (param.type === 'boolean') {
      const checked = typeof value === 'boolean' ? value : ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
      return (
        <label className="node-param-checkbox">
          <input
            type="checkbox"
            checked={checked}
            onChange={event => updateFetcherConfig(fetcherId, param.field, event.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-blue-600"
          />
          <span>{checked ? '已启用' : '未启用'}</span>
        </label>
      );
    }

    if (Array.isArray(param.options) && param.options.length > 0) {
      return (
        <select
          value={value}
          onChange={event => updateFetcherConfig(fetcherId, param.field, event.target.value)}
          className="node-param-input"
        >
          {param.options.map(option => {
            const optionValue = typeof option === 'object' ? option.value : option;
            const optionLabel = typeof option === 'object' ? option.label : option;
            return <option key={optionValue} value={optionValue}>{optionLabel}</option>;
          })}
        </select>
      );
    }

    return (
      <input
        type={param.type || 'text'}
        value={value}
        onChange={event => updateFetcherConfig(fetcherId, param.field, param.type === 'number' ? Number(event.target.value) : event.target.value)}
        placeholder={param.placeholder || String(param.default ?? '')}
        className="node-param-input"
      />
    );
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
        className="form-input py-1.5 text-xs"
      />
    );
  };

  return (
    <div className={`space-y-6 animate-in fade-in ${selectedFetchers.length > 0 && view === 'catalog' ? 'pb-24' : ''}`}>
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">节点管理</h2>
          <p className="page-subtitle mt-3 max-w-4xl">管理抓取节点目录与可复用节点组，统一配置与监控，保障数据抓取稳定高效。</p>
        </div>
        <div className="page-actions">
          <div className="segmented-control">
            <button onClick={() => setView('catalog')} className={`segmented-option ${view === 'catalog' ? 'segmented-option-active' : ''}`}><Layers /> 节点目录</button>
            <button onClick={() => setView('groups')} className={`segmented-option ${view === 'groups' ? 'segmented-option-active' : ''}`}><FolderPlus /> 节点组</button>
          </div>
        </div>
      </div>

      {view === 'catalog' && (
        <>
          <div className="surface-card rounded-[16px] p-6">
            <div className="catalog-header">
              <div className="section-title">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                  <Layers className="h-5 w-5" />
                </div>
                <span>内置节点目录</span>
                <span className="text-xs font-mono text-slate-400">{filteredFetchers.length}/{availableFetchers.length}</span>
              </div>
              <div className="search-box catalog-search">
                <Search className="mr-2 h-4 w-4 text-slate-400" />
                <input value={searchQuery} onChange={event => setSearchQuery(event.target.value)} placeholder="搜索名称、ID、类型" className="py-2.5" />
              </div>
            </div>
            <div className="catalog-chips">
              <button onClick={() => setCategoryFilter('all')} className={`catalog-chip border ${categoryFilter === 'all' ? 'filter-chip-active catalog-chip-active' : 'filter-chip'}`}>全部 {availableFetchers.length}</button>
              {categoryOptions.map(({ category, count }) => (
                <button key={category} onClick={() => setCategoryFilter(category)} className={`catalog-chip border ${categoryFilter === category ? 'filter-chip-active catalog-chip-active' : 'filter-chip'}`}>
                  {getCategoryLabel(category)} {count}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 items-start gap-5 md:grid-cols-2 lg:grid-cols-3">
            {filteredFetchers.map(fetcher => {
              const isSelected = selectedFetchers.includes(fetcher.id);
              const health = healthByFetcher[fetcher.id];
              const healthInfo = healthMeta(health?.health_status);
              const paramCount = (fetcher.parameters || []).length;
              const paramsExpanded = expandedParamFetcherId === fetcher.id;
              return (
                <div key={fetcher.id} className={`node-card rounded-[16px] flex flex-col transition-all ${isSelected ? 'border-blue-500 ring-4 ring-blue-500/10' : ''}`}>
                  <div onClick={() => toggleFetcherSelection(fetcher.id)} className="p-4 flex items-start gap-3 cursor-pointer rounded-t-[16px] hover:bg-slate-50">
                    <div className={`mt-1.5 w-5 h-5 shrink-0 rounded border flex items-center justify-center ${isSelected ? 'bg-blue-600 border-blue-600' : 'border-slate-300'}`}>
                      {isSelected && <CheckSquare className="w-4 h-4 text-white" />}
                    </div>
                    <div className="w-12 h-12 shrink-0 bg-white border border-slate-200 rounded-xl flex items-center justify-center text-2xl shadow-sm">{fetcher.icon}</div>
                    <div className="flex-1 min-w-0">
                      <h3 className="card-title">{fetcher.name}</h3>
                      <div className="flex items-center gap-2 mt-1.5 min-w-0">
                        <span className="status-badge bg-blue-50 text-blue-700 border-blue-100 truncate">{getCategoryLabel(fetcher.category)}</span>
                        <span className={`status-badge truncate ${healthInfo.className}`}>{healthInfo.label}</span>
                        <span className="status-badge bg-slate-100 text-slate-500 border-slate-200 truncate">{fetcher.id}</span>
                      </div>
                    </div>
                  </div>
                  <div className="px-4 pb-4 bg-white/70 rounded-b-[16px] flex-1 flex flex-col gap-3">
                    <p className="text-sm text-slate-600 leading-relaxed min-h-[38px]">{fetcher.desc}</p>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="bg-slate-50 border border-slate-100 rounded-lg px-2.5 py-2">
                        <div className="tiny-meta">最近运行</div>
                        <div className="text-xs text-slate-700 font-mono truncate" title={formatDateTime(health?.latest_run_at)}>{formatDateTime(health?.latest_run_at)}</div>
                      </div>
                      <div className="bg-slate-50 border border-slate-100 rounded-lg px-2.5 py-2">
                        <div className="tiny-meta">最近新增</div>
                        <div className="text-sm font-bold text-emerald-700">{health?.latest_saved_count ?? 0}</div>
                      </div>
                      <div className="bg-slate-50 border border-slate-100 rounded-lg px-2.5 py-2">
                        <div className="tiny-meta">连续失败</div>
                        <div className="text-sm font-bold text-red-700">{health?.consecutive_failures ?? 0}</div>
                      </div>
                    </div>

                    {paramsExpanded && (
                      <div className="node-param-panel animate-in fade-in slide-in-from-top-1">
                        <div className="node-param-title">
                          <span>{paramCount > 0 ? '参数配置' : '参数配置'}</span>
                          <span className="font-mono text-slate-400">{paramCount} 项</span>
                        </div>
                        {paramCount === 0 ? (
                          <div className="tiny-meta rounded-lg border border-slate-100 bg-white px-3 py-2">该节点无需额外参数</div>
                        ) : (
                          <div className="node-param-grid">
                            {fetcher.parameters.map(param => (
                              <div key={param.field} className="node-param-field">
                                <label className="node-param-label" title={param.field}>{param.label || param.field}</label>
                                {renderCatalogParamInput(fetcher.id, param)}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    <div className="grid grid-cols-[auto_1fr] gap-2">
                      <button
                        onClick={(event) => {
                          event.stopPropagation();
                          setExpandedParamFetcherId(paramsExpanded ? null : fetcher.id);
                        }}
                        className={`config-toggle ${paramsExpanded ? 'config-toggle-active' : ''}`}
                        title={`${paramCount} 项参数配置`}
                        aria-label={`${fetcher.name} 参数配置，${paramCount} 项`}
                      >
                        <Settings2 />
                        <span>配置</span>
                        <span className="config-badge">{paramCount}</span>
                      </button>
                      <button onClick={(event) => { event.stopPropagation(); triggerFetch(fetcher.id, fetchConfigs[fetcher.id] || {}).then(() => { showToast('已触发临时抓取', 'success'); loadSourceHealth(); }).catch(e => showToast(e.message || '抓取失败', 'error')); }} className="w-full px-3 py-2.5 rounded-lg bg-blue-50 text-blue-700 border border-blue-100 text-xs font-bold hover:bg-blue-100 flex items-center justify-center">
                        <Play className="w-3.5 h-3.5 mr-1.5" /> 临时抓取
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {view === 'groups' && (
        <div className="surface-card rounded-[14px] overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-200/70 bg-white/55 flex items-center justify-between">
            <div className="flex items-center">
              <FolderPlus className="w-5 h-5 text-indigo-600 mr-2" />
              <h3 className="font-bold text-slate-700 text-sm">节点组</h3>
              <span className="ml-2 text-xs font-mono text-slate-400">{nodeGroups.length}</span>
            </div>
            <button onClick={() => openCreateGroup([])} className="action-button action-button-primary min-h-[36px] px-3 text-xs">
              <FolderPlus /> 新建节点组
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
                        <div className="card-title truncate">{group.name}</div>
                      </div>
                      <div className="text-xs text-slate-400 mt-1 ml-6">{(group.fetcher_ids || []).length} 个节点 · {group.description || '无说明'}</div>
                    </div>
                    <div className="text-xs font-bold text-slate-500">{(group.fetcher_ids || []).slice(0, 3).map(getFetcherName).join('、')}</div>
                  </button>
                  {isExpanded && (
                    <div className="px-5 pb-5 ml-6 space-y-4">
                      <div className="flex flex-wrap gap-2">
                        <button onClick={() => openEditGroup(group)} className="action-button action-button-quiet min-h-[34px] px-3 text-xs">编辑</button>
                        <button onClick={() => handleRunGroup(group.id)} className="action-button action-button-primary min-h-[34px] px-3 text-xs"><Play /> 临时运行</button>
                        <button onClick={() => handleDeleteGroup(group.id)} className="action-button action-button-danger min-h-[34px] px-3 text-xs"><Trash2 /> 删除</button>
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
        <div className="selection-bar animate-in slide-in-from-bottom-4">
          <div className="selection-bar-info">
            <CheckSquare /> 已选择 {selectedFetchers.length} 个节点
          </div>
          <div className="selection-bar-actions">
            <button onClick={() => openCreateGroup(selectedFetchers)} className="action-button action-button-secondary text-indigo-700">
              <FolderPlus /> 新建节点组
            </button>
            <button onClick={handleBatchFetch} disabled={fetchLoading} className="action-button action-button-primary">
              {fetchLoading ? <RefreshCw className="animate-spin" /> : <Play className="fill-current" />} {fetchLoading ? '执行中...' : '立即临时抓取'}
            </button>
          </div>
        </div>
      )}

      {groupModalOpen && (
        <div className="modal-overlay">
          <div className="modal-panel max-w-5xl">
            <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
              <div>
                <h3 className="card-title">{editingGroupId ? '编辑节点组' : '新建节点组'}</h3>
                <p className="text-xs text-slate-400 mt-1">节点组只维护节点集合和参数模板。</p>
              </div>
              <button onClick={() => setGroupModalOpen(false)} className="p-2 rounded-lg hover:bg-slate-200 text-slate-500"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-5 overflow-auto space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="text-xs font-bold text-slate-500">
                  名称
                  <input value={groupDraft.name} onChange={event => setGroupDraft(prev => ({ ...prev, name: event.target.value }))} className="form-input mt-1" />
                </label>
                <label className="text-xs font-bold text-slate-500">
                  说明
                  <input value={groupDraft.description} onChange={event => setGroupDraft(prev => ({ ...prev, description: event.target.value }))} className="form-input mt-1" />
                </label>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5">
                <div className="border border-slate-200 rounded-xl overflow-hidden">
                  <div className="p-3 bg-slate-50 border-b border-slate-200">
                    <div className="relative">
                      <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input value={modalSearch} onChange={event => setModalSearch(event.target.value)} placeholder="搜索节点" className="form-input pl-9" />
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
                            <div className="card-title truncate">{fetcher?.name || fetcherId}</div>
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
              <button onClick={() => setGroupModalOpen(false)} className="action-button action-button-quiet">取消</button>
              <button onClick={handleSaveGroup} className="action-button action-button-primary"><Save /> 保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
