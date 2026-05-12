import { useState, useEffect, useMemo, useCallback } from 'react';
import { Server, Clock, CheckSquare, Play, RefreshCw, Calendar, Search, Layers } from 'lucide-react';
import { triggerFetch, fetchTasks as apiFetchTasks, createTask, deleteTask, fetchSourceHealth } from '../api';

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

const CATEGORY_ORDER = [
  'official',
  'official_web',
  'framework',
  'paper',
  'product_update',
  'developer_platform',
  'community',
  'wechat',
  'workflow',
  'advanced',
  'general',
];

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

export default function FetchTab({ availableFetchers, showToast }) {
  const [fetchLoading, setFetchLoading] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [healthByFetcher, setHealthByFetcher] = useState({});
  const [selectedFetchers, setSelectedFetchers] = useState([]);
  const [cronExpr, setCronExpr] = useState('0 8 * * *');
  const [fetchConfigs, setFetchConfigs] = useState({});
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    const initialConfigs = {};
    availableFetchers.forEach(f => {
      initialConfigs[f.id] = {};
      (f.parameters || []).forEach(p => {
        initialConfigs[f.id][p.field] = p.default;
      });
    });
    setFetchConfigs(initialConfigs);
  }, [availableFetchers]);

  const loadTasks = async () => {
    try {
      setTasks(await apiFetchTasks());
    } catch (e) { console.error(e); }
  };

  const loadSourceHealth = useCallback(async () => {
    try {
      const healthItems = await fetchSourceHealth();
      setHealthByFetcher(Object.fromEntries(healthItems.map(item => [item.fetcher_id, item])));
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    loadTasks();
    loadSourceHealth();
  }, [loadSourceHealth]);

  const getFetcherName = (id) => {
    const fetcher = availableFetchers.find(f => f.id === id);
    return fetcher ? fetcher.name : id;
  };

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

  const visibleFetchers = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return [...availableFetchers]
      .filter(fetcher => categoryFilter === 'all' || (fetcher.category || 'general') === categoryFilter)
      .filter(fetcher => {
        if (!query) return true;
        const haystack = [
          fetcher.name,
          fetcher.id,
          fetcher.desc,
          fetcher.content_type,
          getCategoryLabel(fetcher.category),
        ].filter(Boolean).join(' ').toLowerCase();
        return haystack.includes(query);
      })
      .sort((a, b) => {
        const categoryDelta = getCategoryRank(a.category) - getCategoryRank(b.category);
        if (categoryDelta !== 0) return categoryDelta;
        return a.name.localeCompare(b.name, 'zh-Hans-CN');
      });
  }, [availableFetchers, categoryFilter, searchQuery]);

  const toggleFetcherSelection = (id) => {
    setSelectedFetchers(prev => prev.includes(id) ? prev.filter(fid => fid !== id) : [...prev, id]);
  };

  const handleConfigChange = (fetcherId, field, value) => {
    setFetchConfigs(prev => ({ ...prev, [fetcherId]: { ...prev[fetcherId], [field]: value } }));
  };

  const handleBatchFetch = async () => {
    setFetchLoading(true);
    let successCount = 0;
    for (const fId of selectedFetchers) {
      try {
        await triggerFetch(fId, fetchConfigs[fId] || {});
        successCount++;
      } catch (e) { showToast(e.message || `[${getFetcherName(fId)}] 网络异常`, 'error'); }
    }
    setFetchLoading(false);
    if (successCount > 0) {
      showToast(`已向 ${successCount} 个节点下发立即抓取指令！`, 'success');
      setSelectedFetchers([]);
      loadSourceHealth();
    }
  };

  const handleBatchSchedule = async () => {
    let successCount = 0;
    for (const fId of selectedFetchers) {
      try {
        await createTask({ fetcher_id: fId, cron_expr: cronExpr, params: fetchConfigs[fId] || {} });
        successCount++;
      } catch (e) { showToast(e.message || '创建任务网络异常', 'error'); }
    }
    if (successCount > 0) {
      showToast(`成功为您挂载了 ${successCount} 个定时轮询计划！`, 'success');
      setSelectedFetchers([]);
      loadTasks();
    }
  };

  const handleDeleteTask = async (id) => {
    if (!window.confirm('确定移除该定时计划？')) return;
    try {
      await deleteTask(id);
      showToast('已取消该调度任务', 'success');
      loadTasks();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
  };

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold flex items-center"><Server className="w-6 h-6 mr-2 text-indigo-500" /> 抓取节点与自动化调度</h2>
          <p className="text-sm text-slate-500 mt-2">勾选节点唤起批量指挥台，可直接在卡片上修改参数实现一键下发。</p>
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-4 space-y-4">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-bold text-slate-700">
            <Layers className="w-4 h-4 text-indigo-500" />
            <span>内置节点目录</span>
            <span className="text-xs text-slate-400 font-mono">{visibleFetchers.length}/{availableFetchers.length}</span>
          </div>
          <div className="relative w-full lg:w-80">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="搜索名称、ID、类型"
              className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-bold text-slate-700 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setCategoryFilter('all')}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors ${categoryFilter === 'all' ? 'bg-blue-600 border-blue-600 text-white' : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'}`}
          >
            全部 {availableFetchers.length}
          </button>
          {categoryOptions.map(({ category, count }) => (
            <button
              key={category}
              onClick={() => setCategoryFilter(category)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors ${categoryFilter === category ? 'bg-blue-600 border-blue-600 text-white' : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'}`}
            >
              {getCategoryLabel(category)} {count}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {availableFetchers.length === 0 ? <div className="col-span-full py-10 text-center text-slate-400 font-bold border-2 border-dashed border-slate-200 rounded-xl">未探测到可用节点</div> : null}
        {availableFetchers.length > 0 && visibleFetchers.length === 0 ? <div className="col-span-full py-10 text-center text-slate-400 font-bold border-2 border-dashed border-slate-200 rounded-xl">没有匹配的抓取节点</div> : null}
        {visibleFetchers.map(fetcher => {
          const isSelected = selectedFetchers.includes(fetcher.id);
          const hasTask = tasks.some(t => t.fetcher_id === fetcher.id);
          const health = healthByFetcher[fetcher.id];
          const healthInfo = healthMeta(health?.health_status);
          return (
            <div key={fetcher.id} className={`bg-white border-2 rounded-xl flex flex-col transition-all group shadow-sm ${isSelected ? 'border-blue-500 ring-4 ring-blue-500/10' : 'border-slate-200 hover:border-blue-300'}`}>
              <div onClick={() => toggleFetcherSelection(fetcher.id)} className="p-4 flex items-start gap-3 cursor-pointer border-b border-slate-100 bg-slate-50/50 rounded-t-lg hover:bg-slate-100/70 transition-colors">
                <div className={`mt-1.5 w-5 h-5 shrink-0 rounded border flex items-center justify-center transition-colors ${isSelected ? 'bg-blue-600 border-blue-600' : 'border-slate-300 group-hover:border-blue-400'}`}>
                  {isSelected && <CheckSquare className="w-4 h-4 text-white" />}
                </div>
                <div className="w-11 h-11 shrink-0 bg-white border border-slate-200 rounded-lg flex items-center justify-center text-2xl shadow-sm">{fetcher.icon}</div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-extrabold text-slate-800 text-sm leading-snug">{fetcher.name}</h3>
                  <div className="flex items-center gap-2 mt-1.5 min-w-0">
                    {hasTask && <Clock className="w-3.5 h-3.5 text-emerald-500 shrink-0" title="已有定时任务" />}
                    <span className="text-[10px] text-blue-700 font-bold bg-blue-50 border border-blue-100 px-1.5 py-0.5 rounded truncate">{getCategoryLabel(fetcher.category)}</span>
                    <span className={`text-[10px] font-bold border px-1.5 py-0.5 rounded truncate ${healthInfo.className}`}>{healthInfo.label}</span>
                    <span className="text-[10px] text-slate-500 font-mono bg-slate-200/50 px-1.5 py-0.5 rounded truncate">{fetcher.id}</span>
                  </div>
                </div>
              </div>

              <div className="p-4 bg-white rounded-b-lg flex-1 flex flex-col justify-between gap-3">
                <p className="text-xs text-slate-500 leading-relaxed min-h-[34px]">{fetcher.desc}</p>
                <div className="grid grid-cols-3 gap-2 text-[11px]">
                  <div className="bg-slate-50 border border-slate-100 rounded-lg px-2 py-1.5">
                    <div className="text-slate-400 font-bold">最近运行</div>
                    <div className="text-slate-700 font-mono truncate" title={formatDateTime(health?.latest_run_at)}>{formatDateTime(health?.latest_run_at)}</div>
                  </div>
                  <div className="bg-slate-50 border border-slate-100 rounded-lg px-2 py-1.5">
                    <div className="text-slate-400 font-bold">新增</div>
                    <div className="text-emerald-700 font-black">{health?.latest_saved_count ?? 0}</div>
                  </div>
                  <div className="bg-slate-50 border border-slate-100 rounded-lg px-2 py-1.5">
                    <div className="text-slate-400 font-bold">失败</div>
                    <div className="text-red-700 font-black">{health?.consecutive_failures ?? 0}</div>
                  </div>
                </div>
                {fetcher.parameters?.length > 0 ? (
                  fetcher.parameters.map(param => (
                    <div key={param.field} className="flex items-center justify-between gap-3">
                      <label className="text-xs font-bold text-slate-500 truncate" title={param.label}>{param.label}</label>
                      <input
                        type={param.type || 'text'}
                        value={(fetchConfigs[fetcher.id] && fetchConfigs[fetcher.id][param.field]) ?? param.default ?? ''}
                        onChange={(e) => handleConfigChange(fetcher.id, param.field, e.target.value)}
                        className="w-1/2 max-w-[140px] px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg focus:ring-1 focus:ring-blue-500 outline-none text-xs font-bold text-slate-700 transition-all text-right"
                      />
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-slate-400 font-medium italic text-center py-2">无需扩展参数</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-8 bg-white rounded-3xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="bg-slate-50 px-5 py-4 border-b border-slate-200 flex items-center">
          <Calendar className="w-5 h-5 text-emerald-600 mr-2" />
          <h3 className="font-bold text-slate-700 text-sm">正在后台巡检的自动化计划</h3>
        </div>
        <table className="w-full text-left">
          <thead className="bg-white text-[11px] font-bold text-slate-400 uppercase border-b border-slate-100">
            <tr><th className="p-4">数据节点</th><th className="p-4">Cron 频率</th><th className="p-4">执行参数</th><th className="p-4 text-right">管理</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-50 text-sm">
            {tasks.length === 0 ? <tr><td colSpan="4" className="p-8 text-center text-slate-400 text-sm font-medium">当前无自动调度任务</td></tr> : tasks.map(t => (
              <tr key={t.id} className="hover:bg-slate-50 transition-colors">
                <td className="p-4 font-bold text-slate-700 flex items-center"><span className="w-2 h-2 rounded-full bg-emerald-400 mr-2 animate-pulse shadow-[0_0_8px_rgba(52,211,153,0.8)]"></span> {getFetcherName(t.fetcher_id)}</td>
                <td className="p-4"><span className="font-mono bg-slate-100 border border-slate-200 px-2.5 py-1 rounded text-slate-600 text-xs">{t.cron_expr}</span></td>
                <td className="p-4 text-xs text-slate-500 max-w-xs truncate" title={t.params_json}>{t.params_json}</td>
                <td className="p-4 text-right"><button onClick={() => handleDeleteTask(t.id)} className="text-red-500 hover:text-red-700 font-bold text-xs bg-red-50 hover:bg-red-100 px-3 py-1.5 rounded-lg transition-colors">移除任务</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedFetchers.length > 0 && (
        <div className="fixed bottom-0 left-0 w-full bg-white/90 backdrop-blur-xl border-t border-slate-200 p-4 z-40 flex justify-center items-center shadow-[0_-10px_40px_-15px_rgba(0,0,0,0.1)] animate-in slide-in-from-bottom-full">
          <div className="max-w-7xl w-full flex flex-col md:flex-row justify-between items-center px-6 gap-4">
            <span className="font-extrabold text-blue-700 flex items-center bg-blue-50 px-4 py-2 rounded-xl"><CheckSquare className="w-5 h-5 mr-2" /> 已蓄势 {selectedFetchers.length} 个抓取节点</span>
            <div className="flex items-center space-x-4">
              <div className="flex items-center space-x-2 border-r border-slate-200 pr-4">
                <Clock className="w-4 h-4 text-emerald-600" />
                <input type="text" value={cronExpr} onChange={e => setCronExpr(e.target.value)} className="w-28 text-sm px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg font-mono outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all text-emerald-700 font-bold" placeholder="0 8 * * *" />
                <button onClick={handleBatchSchedule} className="px-5 py-2.5 bg-emerald-600 text-white font-bold rounded-xl hover:bg-emerald-700 text-sm flex items-center shadow-md transition-all">
                  <Calendar className="w-4 h-4 mr-1.5" /> 批量挂载定时
                </button>
              </div>
              <button onClick={handleBatchFetch} disabled={fetchLoading} className="px-6 py-2.5 bg-blue-600 text-white font-extrabold rounded-xl hover:bg-blue-700 text-sm flex items-center shadow-md transition-all">
                {fetchLoading ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Play className="w-4 h-4 mr-1.5 fill-current" />} {fetchLoading ? '指挥执行中...' : '立即批量抓取'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
