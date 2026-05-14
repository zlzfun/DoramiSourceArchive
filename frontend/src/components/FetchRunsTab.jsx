import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Clock3,
  Layers,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Timer,
  Trash2,
  X,
  XCircle,
} from 'lucide-react';
import {
  createCollectionJob,
  deleteCollectionJob,
  deleteTask,
  fetchCollectionJobRuns,
  fetchCollectionJobs,
  fetchFetchRuns,
  fetchNodeGroups,
  fetchTasks,
  runCollectionJob,
  updateCollectionJob,
} from '../api';

function formatDateTime(value) {
  if (!value) return '-';
  return value.replace('T', ' ').substring(0, 19);
}

function formatDuration(durationMs) {
  if (durationMs === null || durationMs === undefined) return '-';
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(1)} s`;
}

function statusMeta(status) {
  if (status === 'success') return { label: '成功', className: 'text-emerald-700 bg-emerald-50 border-emerald-100', icon: CheckCircle2 };
  if (status === 'failed') return { label: '失败', className: 'text-red-700 bg-red-50 border-red-100', icon: XCircle };
  if (status === 'partial_failed') return { label: '部分失败', className: 'text-amber-700 bg-amber-50 border-amber-100', icon: AlertTriangle };
  return { label: '运行中', className: 'text-blue-700 bg-blue-50 border-blue-100', icon: Clock3 };
}

function scopeLabel(scope) {
  if (scope === 'saved_job') return '采集任务';
  if (scope === 'legacy_task') return '旧版计划';
  return '临时抓取';
}

function triggerLabel(type) {
  return type === 'scheduled' ? '定时' : '手动';
}

function blankJob() {
  return {
    name: '',
    description: '',
    group_id: '',
    fetcher_ids: [],
    params: {},
    per_fetcher_params: {},
    cron_expr: '',
    per_fetcher_cron: {},
    is_active: true,
    downstream_policy: {},
  };
}

function normalizeIds(ids) {
  return Array.from(new Set((ids || []).filter(Boolean)));
}

function defaultParamsFor(fetcher) {
  const params = {};
  (fetcher?.parameters || []).forEach(param => {
    params[param.field] = param.default ?? '';
  });
  return params;
}

function cleanStringMap(map) {
  return Object.fromEntries(
    Object.entries(map || {})
      .map(([key, value]) => [key, String(value || '').trim()])
      .filter(([, value]) => value)
  );
}

export default function FetchRunsTab({ availableFetchers, showToast }) {
  const [view, setView] = useState('jobs');
  const [collectionJobs, setCollectionJobs] = useState([]);
  const [nodeGroups, setNodeGroups] = useState([]);
  const [collectionRuns, setCollectionRuns] = useState([]);
  const [fetchRuns, setFetchRuns] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [filters, setFilters] = useState({ fetcher_id: '', status: '', trigger_type: '' });
  const [expandedJobId, setExpandedJobId] = useState(null);
  const [jobModalOpen, setJobModalOpen] = useState(false);
  const [editingJobId, setEditingJobId] = useState(null);
  const [jobDraft, setJobDraft] = useState(blankJob());
  const [jobSearch, setJobSearch] = useState('');

  const fetchersById = useMemo(
    () => Object.fromEntries(availableFetchers.map(fetcher => [fetcher.id, fetcher])),
    [availableFetchers]
  );

  const groupsById = useMemo(
    () => Object.fromEntries(nodeGroups.map(group => [String(group.id), group])),
    [nodeGroups]
  );

  const jobsById = useMemo(
    () => Object.fromEntries(collectionJobs.map(job => [job.id, job])),
    [collectionJobs]
  );

  const getFetcherName = useCallback((id) => fetchersById[id]?.name || id, [fetchersById]);

  const getJobFetchers = useCallback((job) => {
    if (job?.group_id) return groupsById[String(job.group_id)]?.fetcher_ids || [];
    return job?.fetcher_ids || [];
  }, [groupsById]);

  const draftFetcherIds = useMemo(() => {
    if (jobDraft.group_id) return groupsById[String(jobDraft.group_id)]?.fetcher_ids || [];
    return jobDraft.fetcher_ids || [];
  }, [groupsById, jobDraft.fetcher_ids, jobDraft.group_id]);

  const filteredModalFetchers = useMemo(() => {
    const query = jobSearch.trim().toLowerCase();
    if (!query) return availableFetchers;
    return availableFetchers.filter(fetcher => [fetcher.name, fetcher.id, fetcher.desc].filter(Boolean).join(' ').toLowerCase().includes(query));
  }, [availableFetchers, jobSearch]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const runFilters = {
        status: filters.status,
        trigger_type: filters.trigger_type,
      };
      const fetchRunFilters = {
        trigger_type: filters.trigger_type,
        fetcher_id: filters.fetcher_id,
      };
      const [groups, jobs, jobRuns, nodeRuns, legacyTasks] = await Promise.all([
        fetchNodeGroups(),
        fetchCollectionJobs(),
        fetchCollectionJobRuns(runFilters, 100),
        fetchFetchRuns(fetchRunFilters, 200),
        fetchTasks(),
      ]);
      setNodeGroups(groups);
      setCollectionJobs(jobs);
      setCollectionRuns(jobRuns);
      setFetchRuns(nodeRuns);
      setTasks(legacyTasks);
      setLoadError('');
    } catch (e) {
      setLoadError(e.message || '任务与运行数据加载失败');
      showToast(e.message || '任务与运行数据加载失败', 'error');
    } finally {
      setLoading(false);
    }
  }, [filters.fetcher_id, filters.status, filters.trigger_type, showToast]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const openCreateJob = () => {
    setEditingJobId(null);
    setJobDraft(blankJob());
    setJobSearch('');
    setJobModalOpen(true);
  };

  const openEditJob = (job) => {
    setEditingJobId(job.id);
    setJobDraft({
      name: job.name || '',
      description: job.description || '',
      group_id: job.group_id ? String(job.group_id) : '',
      fetcher_ids: job.fetcher_ids || [],
      params: job.params || {},
      per_fetcher_params: job.per_fetcher_params || {},
      cron_expr: job.cron_expr || '',
      per_fetcher_cron: job.per_fetcher_cron || {},
      is_active: job.is_active !== false,
      downstream_policy: job.downstream_policy || {},
    });
    setJobSearch('');
    setJobModalOpen(true);
  };

  const updateDraftParam = (fetcherId, field, value) => {
    setJobDraft(prev => ({
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

  const updateDraftCron = (fetcherId, value) => {
    setJobDraft(prev => ({
      ...prev,
      per_fetcher_cron: {
        ...(prev.per_fetcher_cron || {}),
        [fetcherId]: value,
      },
    }));
  };

  const toggleDraftFetcher = (fetcher) => {
    setJobDraft(prev => {
      const checked = (prev.fetcher_ids || []).includes(fetcher.id);
      const nextIds = checked
        ? prev.fetcher_ids.filter(id => id !== fetcher.id)
        : [...(prev.fetcher_ids || []), fetcher.id];
      return {
        ...prev,
        fetcher_ids: normalizeIds(nextIds),
        per_fetcher_params: checked
          ? prev.per_fetcher_params
          : { ...(prev.per_fetcher_params || {}), [fetcher.id]: defaultParamsFor(fetcher) },
      };
    });
  };

  const handleSaveJob = async () => {
    const name = jobDraft.name.trim();
    const groupId = jobDraft.group_id ? Number(jobDraft.group_id) : null;
    const fetcherIds = groupId ? [] : normalizeIds(jobDraft.fetcher_ids);
    if (!name) {
      showToast('采集任务名称不能为空', 'error');
      return;
    }
    if (!groupId && fetcherIds.length === 0) {
      showToast('采集任务需要选择节点组或至少一个节点', 'error');
      return;
    }
    const payload = {
      ...jobDraft,
      name,
      group_id: groupId,
      fetcher_ids: fetcherIds,
      cron_expr: jobDraft.cron_expr.trim(),
      per_fetcher_cron: cleanStringMap(jobDraft.per_fetcher_cron),
    };
    try {
      const saved = editingJobId ? await updateCollectionJob(editingJobId, payload) : await createCollectionJob(payload);
      setExpandedJobId(saved.id);
      setJobModalOpen(false);
      await loadAll();
      showToast('采集任务已保存', 'success');
    } catch (e) {
      showToast(e.message || '保存采集任务失败', 'error');
    }
  };

  const handleRunJob = async (id) => {
    try {
      const result = await runCollectionJob(id);
      showToast(`采集任务运行完成：新增 ${result.saved_count || 0} 条`, result.failed_count ? 'info' : 'success');
      await loadAll();
    } catch (e) {
      showToast(e.message || '采集任务运行失败', 'error');
    }
  };

  const handleDeleteJob = async (id) => {
    if (!window.confirm('确定删除该采集任务？')) return;
    try {
      await deleteCollectionJob(id);
      if (expandedJobId === id) setExpandedJobId(null);
      await loadAll();
      showToast('采集任务已删除', 'success');
    } catch (e) {
      showToast(e.message || '删除采集任务失败', 'error');
    }
  };

  const handleDeleteLegacyTask = async (id) => {
    if (!window.confirm('确定删除旧版单节点定时计划？')) return;
    try {
      await deleteTask(id);
      await loadAll();
      showToast('旧版计划已删除', 'success');
    } catch (e) {
      showToast(e.message || '删除旧版计划失败', 'error');
    }
  };

  const renderParamInput = (fetcherId, param) => {
    const params = (jobDraft.per_fetcher_params || {})[fetcherId] || {};
    const value = params[param.field] ?? param.default ?? '';
    if (param.type === 'boolean') {
      const checked = typeof value === 'boolean' ? value : ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
      return (
        <input
          type="checkbox"
          checked={checked}
          onChange={event => updateDraftParam(fetcherId, param.field, event.target.checked)}
          className="w-4 h-4 text-blue-600 rounded border-slate-300"
        />
      );
    }
    return (
      <input
        type={param.type || 'text'}
        value={value}
        onChange={event => updateDraftParam(fetcherId, param.field, param.type === 'number' ? Number(event.target.value) : event.target.value)}
        className="w-full px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg text-xs font-bold text-slate-700 outline-none focus:border-blue-500"
      />
    );
  };

  const unifiedRuns = useMemo(() => {
    const matchedJobRunIds = new Set(fetchRuns.map(run => run.job_run_id).filter(Boolean));
    const rows = [];
    collectionRuns.forEach(run => {
      if (filters.fetcher_id && !matchedJobRunIds.has(run.id)) return;
      const job = run.job_id ? jobsById[run.job_id] : null;
      rows.push({
        key: `collection-${run.id}`,
        type: 'collection',
        title: run.name || job?.name || `采集运行 #${run.id}`,
        subtitle: run.job_id ? `任务 #${run.job_id}` : scopeLabel(run.run_scope),
        nodeLabel: `${run.node_count || 0} 个节点`,
        ...run,
      });
    });
    fetchRuns
      .filter(run => !run.job_run_id)
      .filter(run => !filters.status || run.status === filters.status)
      .forEach(run => {
        rows.push({
          key: `fetch-${run.id}`,
          type: 'fetch',
          title: getFetcherName(run.fetcher_id),
          subtitle: run.fetcher_id,
          nodeLabel: '单节点',
          ...run,
        });
      });
    return rows.sort((a, b) => String(b.started_at || '').localeCompare(String(a.started_at || '')));
  }, [collectionRuns, fetchRuns, filters.fetcher_id, filters.status, getFetcherName, jobsById]);

  const totals = unifiedRuns.reduce((acc, run) => {
    acc.fetched += run.fetched_count || 0;
    acc.saved += run.saved_count || 0;
    acc.skipped += run.skipped_count || 0;
    if (run.status === 'success') acc.success += 1;
    if (run.status === 'failed' || run.status === 'partial_failed') acc.failed += 1;
    if (run.status === 'running') acc.running += 1;
    return acc;
  }, { fetched: 0, saved: 0, skipped: 0, success: 0, failed: 0, running: 0 });

  const fetcherOptions = useMemo(() => {
    const ids = new Set(availableFetchers.map(fetcher => fetcher.id));
    fetchRuns.forEach(run => ids.add(run.fetcher_id));
    return Array.from(ids).filter(Boolean).sort();
  }, [availableFetchers, fetchRuns]);

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold flex items-center"><CalendarClock className="w-6 h-6 mr-2 text-blue-500" /> 任务与运行</h2>
          <p className="text-sm text-slate-500 mt-2">配置采集任务，统一查看手动、定时和旧版运行记录。</p>
        </div>
        <div className="flex bg-slate-100 border border-slate-200 rounded-xl p-1 w-fit">
          <button onClick={() => setView('jobs')} className={`px-4 py-2 rounded-lg text-sm font-bold ${view === 'jobs' ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>采集任务</button>
          <button onClick={() => setView('history')} className={`px-4 py-2 rounded-lg text-sm font-bold ${view === 'history' ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>运行历史</button>
        </div>
      </div>

      {view === 'jobs' && (
        <>
          <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-4 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
            <div>
              <div className="flex items-center font-bold text-slate-700 text-sm">
                <Settings2 className="w-4 h-4 mr-2 text-blue-600" /> 采集任务
                <span className="ml-2 text-xs font-mono text-slate-400">{collectionJobs.length}</span>
              </div>
              <p className="text-xs text-slate-400 mt-1">任务负责节点组合、参数覆盖、整体 cron 和单节点 cron。</p>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={loadAll} disabled={loading} className="px-3 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold hover:bg-slate-200 flex items-center">
                <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} /> 刷新
              </button>
              <button onClick={openCreateJob} className="px-3 py-2 rounded-lg bg-blue-600 text-white text-xs font-bold hover:bg-blue-700 flex items-center">
                <Plus className="w-3.5 h-3.5 mr-1.5" /> 新建采集任务
              </button>
            </div>
          </div>

          <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
            <div className="divide-y divide-slate-100">
              {collectionJobs.length === 0 ? (
                <div className="p-12 text-center text-slate-400 font-medium">暂无采集任务</div>
              ) : collectionJobs.map(job => {
                const isExpanded = expandedJobId === job.id;
                const group = job.group_id ? groupsById[String(job.group_id)] : null;
                const ids = getJobFetchers(job);
                return (
                  <div key={job.id}>
                    <button onClick={() => setExpandedJobId(isExpanded ? null : job.id)} className="w-full px-5 py-4 flex items-center justify-between gap-4 hover:bg-slate-50 text-left">
                      <div className="min-w-0">
                        <div className="flex items-center">
                          {isExpanded ? <ChevronDown className="w-4 h-4 text-slate-400 mr-2" /> : <ChevronRight className="w-4 h-4 text-slate-400 mr-2" />}
                          <div className="font-extrabold text-slate-800 text-sm truncate">{job.name}</div>
                          {!job.is_active && <span className="ml-2 text-[10px] font-bold bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">停用</span>}
                        </div>
                        <div className="text-xs text-slate-400 mt-1 ml-6">
                          {group ? `节点组：${group.name}` : '直接选择节点'} · {ids.length} 个节点 · {job.cron_expr || '无整体定时'}
                        </div>
                      </div>
                      <div className="hidden md:block text-xs font-bold text-slate-500 truncate max-w-sm">{ids.slice(0, 3).map(getFetcherName).join('、')}</div>
                    </button>
                    {isExpanded && (
                      <div className="px-5 pb-5 ml-6 space-y-4">
                        <div className="flex flex-wrap gap-2">
                          <button onClick={() => openEditJob(job)} className="px-3 py-2 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold hover:bg-slate-200">编辑配置</button>
                          <button onClick={() => handleRunJob(job.id)} className="px-3 py-2 rounded-lg bg-emerald-600 text-white text-xs font-bold hover:bg-emerald-700 flex items-center"><Play className="w-3.5 h-3.5 mr-1.5" /> 立即运行</button>
                          <button onClick={() => handleDeleteJob(job.id)} className="px-3 py-2 rounded-lg bg-red-50 text-red-600 border border-red-100 text-xs font-bold hover:bg-red-100 flex items-center"><Trash2 className="w-3.5 h-3.5 mr-1.5" /> 删除</button>
                        </div>
                        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                          <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
                            <div className="text-xs font-bold text-slate-400">节点来源</div>
                            <div className="font-extrabold text-slate-700 text-sm mt-1">{group ? group.name : '任务内直接选择'}</div>
                          </div>
                          <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
                            <div className="text-xs font-bold text-slate-400">整体 cron</div>
                            <div className="font-mono text-xs text-slate-700 mt-1">{job.cron_expr || '-'}</div>
                          </div>
                          <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
                            <div className="text-xs font-bold text-slate-400">单节点 cron 覆盖</div>
                            <div className="font-mono text-xs text-slate-700 mt-1">{Object.keys(job.per_fetcher_cron || {}).length} 个</div>
                          </div>
                        </div>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                          {ids.map(fetcherId => (
                            <div key={fetcherId} className="border border-slate-200 rounded-lg p-3 bg-white">
                              <div className="font-bold text-slate-700 text-sm">{getFetcherName(fetcherId)}</div>
                              <div className="font-mono text-[11px] text-slate-400 mt-1">{fetcherId}</div>
                              <div className="mt-2 text-[11px] font-bold text-blue-700 bg-blue-50 border border-blue-100 rounded px-2 py-1">
                                cron：{(job.per_fetcher_cron || {})[fetcherId] || job.cron_expr || '-'}
                              </div>
                              <code className="block mt-2 text-[11px] text-slate-500 bg-slate-50 border border-slate-100 rounded px-2 py-1 truncate" title={JSON.stringify((job.per_fetcher_params || {})[fetcherId] || {})}>
                                {JSON.stringify((job.per_fetcher_params || {})[fetcherId] || {})}
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
        </>
      )}

      {view === 'history' && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="text-xs font-bold text-slate-400 mb-1">本页运行</div>
              <div className="text-2xl font-black text-slate-800">{unifiedRuns.length}</div>
            </div>
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="text-xs font-bold text-slate-400 mb-1">新增入库</div>
              <div className="text-2xl font-black text-emerald-600">{totals.saved}</div>
            </div>
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="text-xs font-bold text-slate-400 mb-1">抓取产出</div>
              <div className="text-2xl font-black text-blue-600">{totals.fetched}</div>
            </div>
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="text-xs font-bold text-slate-400 mb-1">重复跳过</div>
              <div className="text-2xl font-black text-amber-600">{totals.skipped}</div>
            </div>
            <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="text-xs font-bold text-slate-400 mb-1">失败次数</div>
              <div className="text-2xl font-black text-red-600">{totals.failed}</div>
            </div>
          </div>

          <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-3 flex flex-col md:flex-row gap-3">
            <div className="flex items-center flex-1 bg-slate-50 border border-slate-100 rounded-lg px-3">
              <Search className="w-4 h-4 text-slate-400 mr-2" />
              <select value={filters.fetcher_id} onChange={event => setFilters({ ...filters, fetcher_id: event.target.value })} className="w-full bg-transparent py-2 text-sm font-bold text-slate-700 outline-none">
                <option value="">全部节点</option>
                {fetcherOptions.map(id => <option key={id} value={id}>{getFetcherName(id)}</option>)}
              </select>
            </div>
            <select value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value })} className="bg-slate-50 border border-slate-100 rounded-lg px-3 py-2 text-sm font-bold text-slate-700 outline-none">
              <option value="">全部状态</option>
              <option value="success">成功</option>
              <option value="partial_failed">部分失败</option>
              <option value="failed">失败</option>
              <option value="running">运行中</option>
            </select>
            <select value={filters.trigger_type} onChange={event => setFilters({ ...filters, trigger_type: event.target.value })} className="bg-slate-50 border border-slate-100 rounded-lg px-3 py-2 text-sm font-bold text-slate-700 outline-none">
              <option value="">全部触发</option>
              <option value="manual">手动</option>
              <option value="scheduled">定时</option>
            </select>
            <button onClick={loadAll} disabled={loading} className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 text-sm font-bold hover:bg-slate-200 flex items-center justify-center">
              <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} /> 刷新
            </button>
          </div>

          {tasks.length > 0 && (
            <div className="bg-amber-50 border border-amber-100 rounded-xl p-4">
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="font-bold text-amber-900 text-sm">旧版单节点定时计划</div>
                <div className="text-xs font-bold text-amber-700">{tasks.length} 个</div>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
                {tasks.map(task => (
                  <div key={task.id} className="bg-white border border-amber-100 rounded-lg px-3 py-2 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-bold text-slate-700 text-sm truncate">{getFetcherName(task.fetcher_id)}</div>
                      <div className="font-mono text-[11px] text-slate-400 truncate">{task.cron_expr}</div>
                    </div>
                    <button onClick={() => handleDeleteLegacyTask(task.id)} className="p-1.5 text-amber-700 hover:text-red-600 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4" /></button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
            {loadError && (
              <div className="px-4 py-3 bg-red-50 border-b border-red-100 text-red-700 text-sm font-bold flex items-center">
                <AlertTriangle className="w-4 h-4 mr-2" /> {loadError}
              </div>
            )}
            <table className="w-full text-left border-collapse">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs tracking-wider">
                <tr>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">对象</th>
                  <th className="px-4 py-3">触发</th>
                  <th className="px-4 py-3">开始时间</th>
                  <th className="px-4 py-3">耗时</th>
                  <th className="px-4 py-3">数量</th>
                  <th className="px-4 py-3">失败信息</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-sm">
                {unifiedRuns.length === 0 ? (
                  <tr>
                    <td colSpan="7" className="px-6 py-16 text-center text-slate-400 font-medium">当前过滤条件下暂无运行记录</td>
                  </tr>
                ) : unifiedRuns.map(run => {
                  const meta = statusMeta(run.status);
                  const StatusIcon = meta.icon;
                  return (
                    <tr key={run.key} className="hover:bg-blue-50/40 transition-colors">
                      <td className="px-4 py-4">
                        <span className={`inline-flex items-center px-2.5 py-1 rounded-lg border text-xs font-black ${meta.className}`}>
                          <StatusIcon className="w-3.5 h-3.5 mr-1" /> {meta.label}
                        </span>
                      </td>
                      <td className="px-4 py-4">
                        <div className="font-extrabold text-slate-800 text-sm">{run.title}</div>
                        <div className="font-mono text-[11px] text-slate-400 mt-0.5">{run.subtitle} · {run.nodeLabel}</div>
                      </td>
                      <td className="px-4 py-4">
                        <span className="text-xs font-bold text-slate-600 bg-slate-100 px-2 py-1 rounded-lg">{triggerLabel(run.trigger_type)}</span>
                      </td>
                      <td className="px-4 py-4 font-mono text-xs text-slate-500">{formatDateTime(run.started_at)}</td>
                      <td className="px-4 py-4 text-xs font-bold text-slate-600">
                        <span className="inline-flex items-center"><Timer className="w-3.5 h-3.5 mr-1 text-blue-500" /> {formatDuration(run.duration_ms)}</span>
                      </td>
                      <td className="px-4 py-4">
                        <div className="text-xs font-bold text-slate-700">抓取 {run.fetched_count || 0} / 新增 {run.saved_count || 0} / 跳过 {run.skipped_count || 0}</div>
                      </td>
                      <td className="px-4 py-4 max-w-sm">
                        {run.error_message ? (
                          <div className="flex items-start text-xs text-red-600 font-medium">
                            <AlertTriangle className="w-3.5 h-3.5 mr-1.5 mt-0.5 shrink-0" />
                            <span className="line-clamp-2" title={run.error_message}>{run.error_message}</span>
                          </div>
                        ) : <span className="text-xs text-slate-300">-</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {jobModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-6xl max-h-[90vh] overflow-hidden flex flex-col">
            <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
              <div>
                <h3 className="font-extrabold text-slate-800">{editingJobId ? '编辑采集任务' : '新建采集任务'}</h3>
                <p className="text-xs text-slate-400 mt-1">采集任务负责调度、参数覆盖和运行追踪。</p>
              </div>
              <button onClick={() => setJobModalOpen(false)} className="p-2 rounded-lg hover:bg-slate-200 text-slate-500"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-5 overflow-auto space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="text-xs font-bold text-slate-500">
                  名称
                  <input value={jobDraft.name} onChange={event => setJobDraft(prev => ({ ...prev, name: event.target.value }))} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-bold text-slate-700 outline-none focus:border-blue-500" />
                </label>
                <label className="text-xs font-bold text-slate-500">
                  说明
                  <input value={jobDraft.description} onChange={event => setJobDraft(prev => ({ ...prev, description: event.target.value }))} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-700 outline-none focus:border-blue-500" />
                </label>
                <label className="text-xs font-bold text-slate-500">
                  节点组
                  <select value={jobDraft.group_id} onChange={event => setJobDraft(prev => ({ ...prev, group_id: event.target.value, fetcher_ids: [] }))} className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-bold text-slate-700 outline-none focus:border-blue-500">
                    <option value="">不使用节点组，直接选择节点</option>
                    {nodeGroups.map(group => <option key={group.id} value={group.id}>{group.name}（{(group.fetcher_ids || []).length}）</option>)}
                  </select>
                </label>
                <label className="text-xs font-bold text-slate-500">
                  整体 cron
                  <input value={jobDraft.cron_expr} onChange={event => setJobDraft(prev => ({ ...prev, cron_expr: event.target.value }))} placeholder="例如：0 9 * * *" className="mt-1 w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono text-slate-700 outline-none focus:border-blue-500" />
                </label>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5">
                <div className="border border-slate-200 rounded-xl overflow-hidden">
                  <div className="p-3 bg-slate-50 border-b border-slate-200">
                    <div className="font-bold text-slate-700 text-sm flex items-center">
                      <Layers className="w-4 h-4 mr-2 text-blue-600" /> {jobDraft.group_id ? '节点组包含节点' : '选择节点'}
                    </div>
                    {!jobDraft.group_id && (
                      <div className="relative mt-3">
                        <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                        <input value={jobSearch} onChange={event => setJobSearch(event.target.value)} placeholder="搜索节点" className="w-full pl-9 pr-3 py-2 bg-white border border-slate-200 rounded-lg text-sm font-bold outline-none focus:border-blue-500" />
                      </div>
                    )}
                  </div>
                  <div className="max-h-[460px] overflow-auto divide-y divide-slate-100">
                    {(jobDraft.group_id ? draftFetcherIds.map(id => fetchersById[id] || { id, name: id }) : filteredModalFetchers).map(fetcher => {
                      const checked = draftFetcherIds.includes(fetcher.id);
                      return (
                        <button
                          key={fetcher.id}
                          disabled={Boolean(jobDraft.group_id)}
                          onClick={() => toggleDraftFetcher(fetcher)}
                          className={`w-full px-3 py-3 flex items-center gap-3 text-left ${checked ? 'bg-blue-50/60' : ''} ${jobDraft.group_id ? 'cursor-default' : 'hover:bg-slate-50'}`}
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
                  {draftFetcherIds.length === 0 ? (
                    <div className="border border-dashed border-slate-200 rounded-xl p-10 text-center text-slate-400 font-medium">未选择节点</div>
                  ) : draftFetcherIds.map(fetcherId => {
                    const fetcher = fetchersById[fetcherId];
                    return (
                      <div key={fetcherId} className="border border-slate-200 rounded-xl p-3 bg-white">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="font-extrabold text-slate-800 text-sm truncate">{fetcher?.name || fetcherId}</div>
                            <div className="font-mono text-[11px] text-slate-400 mt-0.5">{fetcherId}</div>
                          </div>
                          {!jobDraft.group_id && (
                            <button onClick={() => setJobDraft(prev => ({ ...prev, fetcher_ids: prev.fetcher_ids.filter(id => id !== fetcherId) }))} className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg"><X className="w-4 h-4" /></button>
                          )}
                        </div>
                        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                          <label className="text-xs font-bold text-slate-500 md:col-span-2">
                            该节点 cron
                            <input value={(jobDraft.per_fetcher_cron || {})[fetcherId] || ''} onChange={event => updateDraftCron(fetcherId, event.target.value)} placeholder={jobDraft.cron_expr || '留空则不单独调度'} className="mt-1 w-full px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono text-slate-700 outline-none focus:border-blue-500" />
                          </label>
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
            <div className="px-5 py-4 border-t border-slate-200 bg-white flex justify-between gap-2">
              <label className="inline-flex items-center text-xs font-bold text-slate-600">
                <input type="checkbox" checked={jobDraft.is_active} onChange={event => setJobDraft(prev => ({ ...prev, is_active: event.target.checked }))} className="w-4 h-4 mr-2 text-blue-600 rounded border-slate-300" />
                启用任务
              </label>
              <div className="flex justify-end gap-2">
                <button onClick={() => setJobModalOpen(false)} className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 text-sm font-bold hover:bg-slate-200">取消</button>
                <button onClick={handleSaveJob} className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold hover:bg-blue-700 flex items-center"><Save className="w-4 h-4 mr-1.5" /> 保存</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
