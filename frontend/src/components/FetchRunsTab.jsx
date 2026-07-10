import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AnimatedNumber from './AnimatedNumber';
import {
  AlertTriangle,
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
} from 'lucide-react';
import {
  createCollectionJob,
  deleteCollectionJob,
  fetchCollectionJobRuns,
  fetchCollectionJobs,
  fetchFetchRuns,
  runCollectionJob,
  updateCollectionJob,
} from '../api';
import ActiveFilterBar from './ActiveFilterBar';
import LogoMark from './LogoMark';
import Modal from './Modal';
import StatusBadge from './StatusBadge';
import { runStatusMeta } from '../statusMeta';
import { resolveCompany } from '../sourceTaxonomy';
import { formatDateTime } from '../utils/datetime';
import { runAction } from '../utils/runAction';
import { useConfirm } from '../hooks/useConfirm';
import { TEST_RUN_LIMIT, normalizeIds, collectionRunMessage, paramChips } from '../utils/collection';

function formatDuration(durationMs) {
  if (durationMs === null || durationMs === undefined) return '-';
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(1)} s`;
}

function scopeLabel(scope) {
  if (scope === 'saved_job') return '采集任务';
  if (scope === 'legacy_task') return '旧版计划';
  return '临时抓取';
}

function triggerLabel(type) {
  return type === 'scheduled' ? '定时' : '手动';
}

function normalizeCollectorDisplayName(value) {
  return String(value || '').replaceAll('节点组', '采集范围');
}

function blankJob() {
  return {
    name: '',
    description: '',
    fetcher_ids: [],
    params: {},
    per_fetcher_params: {},
    cron_expr: '',
    per_fetcher_cron: {},
    is_active: true,
    downstream_policy: {},
  };
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

export default function FetchRunsTab({
  availableFetchers,
  showToast,
  view,
  setView,
  onArticlesChanged,
  onRunsChanged,
  isActive = true,
  runsDirty = false,
  onRunsRefreshed,
  pendingFilter,
  onPendingFilterApplied,
  pendingJobDraft,
  onPendingJobDraftApplied,
}) {
  const confirm = useConfirm();
  const loadRequestRef = useRef(0);
  const [collectionJobs, setCollectionJobs] = useState([]);
  const [collectionRuns, setCollectionRuns] = useState([]);
  const [fetchRuns, setFetchRuns] = useState([]);
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

  const jobsById = useMemo(
    () => Object.fromEntries(collectionJobs.map(job => [job.id, job])),
    [collectionJobs]
  );

  const getFetcherName = useCallback((id) => fetchersById[id]?.name || id, [fetchersById]);
  const companyForId = useCallback((id) => resolveCompany(fetchersById[id] || { source_owner: '', base_url: '' }), [fetchersById]);

  const draftFetcherIds = useMemo(() => jobDraft.fetcher_ids || [], [jobDraft.fetcher_ids]);

  const filteredModalFetchers = useMemo(() => {
    const query = jobSearch.trim().toLowerCase();
    if (!query) return availableFetchers;
    return availableFetchers.filter(fetcher => [fetcher.name, fetcher.id, fetcher.desc].filter(Boolean).join(' ').toLowerCase().includes(query));
  }, [availableFetchers, jobSearch]);

  // 整刷自增，作为运行表 tbody 的 key：筛选一变即整体重挂载，让行入场动画对每次切换触发。
  const [listVersion, setListVersion] = useState(0);

  const loadAll = useCallback(async () => {
    const reqId = ++loadRequestRef.current;
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
      const [jobs, jobRuns, nodeRuns] = await Promise.all([
        fetchCollectionJobs(),
        fetchCollectionJobRuns(runFilters, 100),
        fetchFetchRuns(fetchRunFilters, 200),
      ]);
      if (reqId !== loadRequestRef.current) return; // 被更新的加载抢占，丢弃过期结果
      setCollectionJobs(jobs);
      setCollectionRuns(jobRuns);
      setFetchRuns(nodeRuns);
      setLoadError('');
      setListVersion(v => v + 1);
    } catch (e) {
      if (reqId !== loadRequestRef.current) return;
      setLoadError(e.message || '任务与运行数据加载失败');
      showToast(e.message || '任务与运行数据加载失败', 'error');
    } finally {
      if (reqId === loadRequestRef.current) setLoading(false);
    }
  }, [filters.fetcher_id, filters.status, filters.trigger_type, showToast]);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => {
    if (isActive && runsDirty) {
      loadAll();
      onRunsRefreshed?.();
    }
  }, [isActive, runsDirty, loadAll, onRunsRefreshed]);

  useEffect(() => {
    if (!pendingFilter) return;
    setView('history');
    setFilters(prev => ({
      ...prev,
      fetcher_id: pendingFilter.fetcher_id ?? prev.fetcher_id,
      status: pendingFilter.status ?? '',
      trigger_type: '',
    }));
    onPendingFilterApplied?.();
  }, [pendingFilter, onPendingFilterApplied, setView]);

  // 来自节点管理「保存为采集任务」的草稿：切到采集任务视图，以「新建」态打开编辑器并预填
  //（节点选择 + 每节点参数覆盖）。用户自行补名称/cron 再保存。消费后回执清空。
  useEffect(() => {
    if (!pendingJobDraft) return;
    setView('jobs');
    setEditingJobId(null);
    setJobDraft({ ...blankJob(), ...pendingJobDraft });
    setJobSearch('');
    setJobModalOpen(true);
    onPendingJobDraftApplied?.();
  }, [pendingJobDraft, onPendingJobDraftApplied, setView]);


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
    const fetcherIds = normalizeIds(jobDraft.fetcher_ids);
    if (!name) {
      showToast('采集任务名称不能为空', 'error');
      return;
    }
    if (fetcherIds.length === 0) {
      showToast('采集任务至少需要选择一个节点', 'error');
      return;
    }
    const payload = {
      ...jobDraft,
      name,
      fetcher_ids: fetcherIds,
      cron_expr: jobDraft.cron_expr.trim(),
      per_fetcher_cron: cleanStringMap(jobDraft.per_fetcher_cron),
    };
    await runAction(() => (editingJobId ? updateCollectionJob(editingJobId, payload) : createCollectionJob(payload)), {
      showToast,
      success: '采集任务已保存',
      error: '保存采集任务失败',
      onSuccess: (saved) => {
        setExpandedJobId(saved.id);
        setJobModalOpen(false);
        loadAll();
      },
    });
  };

  const handleRunJob = async (id, options = {}) => {
    onRunsChanged?.();
    try {
      const result = await runCollectionJob(id, options);
      const prefix = options.testLimit ? `测试运行完成（每源 ${options.testLimit} 条）` : '采集任务运行完成';
      showToast(collectionRunMessage(prefix, result), result.failed_count ? 'error' : 'success');
      await loadAll();
      onArticlesChanged?.();
      onRunsChanged?.();
    } catch (e) {
      showToast(e.message || '采集任务运行失败', 'error');
    }
  };

  const handleDeleteJob = async (id) => {
    if (!(await confirm('确定删除该采集任务？'))) return;
    await runAction(() => deleteCollectionJob(id), {
      showToast,
      success: '采集任务已删除',
      error: '删除采集任务失败',
      onSuccess: () => {
        if (expandedJobId === id) setExpandedJobId(null);
        loadAll();
      },
    });
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
          className="w-4 h-4 text-[var(--dorami-blue)] rounded border-slate-300"
        />
      );
    }
    return (
      <input
        type={param.type || 'text'}
        value={value}
        onChange={event => updateDraftParam(fetcherId, param.field, param.type === 'number' ? Number(event.target.value) : event.target.value)}
        className="form-input py-1.5 text-xs"
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
        title: normalizeCollectorDisplayName(run.name || job?.name || `采集运行 #${run.id}`),
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

  // 当前生效筛选（运行历史）；清除后由 loadAll（依赖 filters.*）自动重载。
  const activeFilterItems = [];
  if (filters.fetcher_id) {
    activeFilterItems.push({
      key: 'fetcher', label: '数据来源', value: getFetcherName(filters.fetcher_id),
      onRemove: () => setFilters(prev => ({ ...prev, fetcher_id: '' })),
    });
  }
  if (filters.status) {
    activeFilterItems.push({
      key: 'status', label: '运行状态', value: runStatusMeta(filters.status).label,
      onRemove: () => setFilters(prev => ({ ...prev, status: '' })),
    });
  }
  if (filters.trigger_type) {
    activeFilterItems.push({
      key: 'trigger', label: '触发方式', value: triggerLabel(filters.trigger_type),
      onRemove: () => setFilters(prev => ({ ...prev, trigger_type: '' })),
    });
  }
  const clearAllFilters = () => setFilters({ fetcher_id: '', status: '', trigger_type: '' });

  return (
    <div className="space-y-6">
      <div className="page-head">
        <h1 className="page-title">任务与运行</h1>
        <div className="page-head-actions">
          <div className="segmented-control">
            <button onClick={() => setView('jobs')} className={`segmented-option ${view === 'jobs' ? 'segmented-option-active' : ''}`}>
              <Settings2 /> 采集任务
            </button>
            <button onClick={() => setView('history')} className={`segmented-option ${view === 'history' ? 'segmented-option-active' : ''}`}>
              <Clock3 /> 运行历史
            </button>
          </div>
        </div>
      </div>

      {view === 'jobs' && (
        <>
          <div className="surface-card rounded-[var(--r-overlay)] overflow-hidden">
            <div className="panel-header">
              <div>
                <div className="section-title">
                  <div className="flex h-10 w-10 items-center justify-center rounded-[var(--r-control)] bg-[var(--dorami-wash)] text-[var(--dorami-blue)]">
                    <Settings2 className="h-5 w-5" />
                  </div>
                  <span>采集任务</span>
                  <span className="text-xs font-mono text-slate-500">{collectionJobs.length}</span>
                </div>
                <p className="panel-header-subtitle">任务负责节点选择、参数覆盖、整体 cron 和单节点 cron。</p>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={loadAll} disabled={loading} className="action-button action-button-secondary min-h-[36px] px-3 text-xs">
                  <RefreshCw className={loading ? 'animate-spin' : ''} /> 刷新
                </button>
                <button onClick={openCreateJob} className="action-button action-button-primary min-h-[36px] px-3 text-xs">
                  <Plus /> 新建采集任务
                </button>
              </div>
            </div>
          </div>

          <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
            <div className="divide-y divide-[var(--dorami-border)]">
              {collectionJobs.length === 0 ? (
                <div className="p-12 text-center text-slate-500 font-medium">还没有采集任务，点「新建采集任务」创建第一个。</div>
              ) : collectionJobs.map(job => {
                const isExpanded = expandedJobId === job.id;
                const ids = job.fetcher_ids || [];
                return (
                  <div key={job.id}>
                    <button onClick={() => setExpandedJobId(isExpanded ? null : job.id)} className="w-full px-5 py-4 flex items-center justify-between gap-4 hover:bg-[var(--dorami-soft)] text-left">
                      <div className="min-w-0">
                        <div className="flex items-center">
                          {isExpanded ? <ChevronDown className="w-4 h-4 text-slate-500 mr-2" /> : <ChevronRight className="w-4 h-4 text-slate-500 mr-2" />}
                          <div className="card-title truncate">{job.name}</div>
                          {!job.is_active && <span className="ml-2 micro-label bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">停用</span>}
                        </div>
                        <div className="text-xs text-slate-500 mt-1 ml-6">
                          {ids.length} 个节点 · {job.cron_expr || '无整体定时'}
                        </div>
                      </div>
                      <div className="hidden md:block text-xs font-bold text-slate-500 truncate max-w-sm">{ids.slice(0, 3).map(getFetcherName).join('、')}</div>
                    </button>
                    {isExpanded && (
                      <div className="px-5 pb-5 ml-6 space-y-4">
                        <div className="flex flex-wrap gap-2">
                          <button onClick={() => openEditJob(job)} className="action-button action-button-quiet min-h-[34px] px-3 text-xs">编辑配置</button>
                          <button onClick={() => handleRunJob(job.id, { testLimit: TEST_RUN_LIMIT })} className="action-button action-button-quiet min-h-[34px] px-3 text-xs"><Play /> 测试运行 1 条/源</button>
                          <button onClick={() => handleRunJob(job.id)} className="action-button action-button-primary min-h-[34px] px-3 text-xs"><Play /> 立即运行</button>
                          <button onClick={() => handleDeleteJob(job.id)} className="action-button action-button-danger min-h-[34px] px-3 text-xs ml-auto"><Trash2 /> 删除</button>
                        </div>
                        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                          <div className="border border-[var(--dorami-border)] rounded-[var(--r-control)] p-3 bg-[var(--dorami-soft)]">
                            <div className="text-xs font-bold text-slate-500">节点来源</div>
                            <div className="card-title mt-1">任务内直接选择</div>
                          </div>
                          <div className="border border-[var(--dorami-border)] rounded-[var(--r-control)] p-3 bg-[var(--dorami-soft)]">
                            <div className="text-xs font-bold text-slate-500">整体 cron</div>
                            <div className="font-mono text-xs text-slate-700 mt-1">{job.cron_expr || '-'}</div>
                          </div>
                          <div className="border border-[var(--dorami-border)] rounded-[var(--r-control)] p-3 bg-[var(--dorami-soft)]">
                            <div className="text-xs font-bold text-slate-500">单节点 cron 覆盖</div>
                            <div className="font-mono text-xs text-slate-700 mt-1">{Object.keys(job.per_fetcher_cron || {}).length} 个</div>
                          </div>
                        </div>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                          {ids.map(fetcherId => {
                            // 只消除「真正为空」的噪声：生效 cron（节点覆盖 || 任务整体）有值才渲染；
                            // 参数覆盖翻成可读 chips（键名走 schema 中文 label），空对象不渲染。
                            const nodeCron = (job.per_fetcher_cron || {})[fetcherId];
                            const effectiveCron = nodeCron || job.cron_expr;
                            const chips = paramChips((job.per_fetcher_params || {})[fetcherId], fetchersById[fetcherId]);
                            return (
                            <div key={fetcherId} className="border border-[var(--dorami-border)] rounded-[var(--r-control)] p-3 bg-[var(--dorami-surface)]">
                              <div className="flex items-center gap-2.5">
                                <LogoMark company={companyForId(fetcherId)} size="sm" />
                                <div className="min-w-0">
                                  <div className="font-bold text-slate-700 text-sm truncate">{getFetcherName(fetcherId)}</div>
                                  <div className="font-mono text-xs text-slate-500 truncate">{fetcherId}</div>
                                </div>
                              </div>
                              {effectiveCron && (
                                <div className="mt-2 micro-label text-blue-700 bg-[var(--dorami-wash)] rounded px-2 py-1 font-mono">
                                  cron：{effectiveCron}{nodeCron ? ' · 覆盖' : ''}
                                </div>
                              )}
                              {chips.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1.5" title={JSON.stringify((job.per_fetcher_params || {})[fetcherId] || {})}>
                                  {chips.map(chip => (
                                    <span key={chip.key} className="inline-flex items-center gap-1 text-xs bg-[var(--dorami-well)] border border-[var(--dorami-border)] rounded px-2 py-0.5">
                                      <span className="text-slate-500">{chip.label}</span>
                                      <span className="font-medium text-slate-700">{chip.value}</span>
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          );})}
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
          <div className="surface-card rounded-[var(--r-overlay)] overflow-hidden">
            <div className="panel-header">
              <div>
                <div className="section-title">
                  <div className="flex h-10 w-10 items-center justify-center rounded-[var(--r-control)] bg-violet-50 text-violet-600">
                    <Clock3 className="h-5 w-5" />
                  </div>
                  <span>运行历史</span>
                  <span className="text-xs font-mono text-slate-500">{unifiedRuns.length}</span>
                </div>
                <p className="panel-header-subtitle">汇总手动、定时与旧版运行记录，可按节点、状态、时间筛选回溯。</p>
              </div>
              <button onClick={loadAll} disabled={loading} className="action-button action-button-secondary min-h-[36px] px-3 text-xs">
                <RefreshCw className={loading ? 'animate-spin' : ''} /> 刷新
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="metric-card rounded-[var(--r-card)] p-4">
              <div className="text-xs font-bold text-slate-500 mb-1">本页运行</div>
              <div className="stat-number text-slate-800"><AnimatedNumber value={unifiedRuns.length} /></div>
            </div>
            <div className="metric-card rounded-[var(--r-card)] p-4">
              <div className="text-xs font-bold text-slate-500 mb-1">新增入库</div>
              <div className="stat-number text-emerald-600"><AnimatedNumber value={totals.saved} /></div>
            </div>
            <div className="metric-card rounded-[var(--r-card)] p-4">
              <div className="text-xs font-bold text-slate-500 mb-1">抓取产出</div>
              <div className="stat-number text-[var(--dorami-blue)]"><AnimatedNumber value={totals.fetched} /></div>
            </div>
            <div className="metric-card rounded-[var(--r-card)] p-4">
              <div className="text-xs font-bold text-slate-500 mb-1">重复跳过</div>
              <div className="stat-number text-amber-600"><AnimatedNumber value={totals.skipped} /></div>
            </div>
            <div className="metric-card rounded-[var(--r-card)] p-4">
              <div className="text-xs font-bold text-slate-500 mb-1">失败次数</div>
              <div className="stat-number text-red-600"><AnimatedNumber value={totals.failed} /></div>
            </div>
          </div>

          <div className="surface-card rounded-[var(--r-overlay)] p-5">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3 xl:grid-cols-[1.2fr_1fr_1fr_auto]">
              <div className="field-box">
                <span>数据来源</span>
                <select value={filters.fetcher_id} onChange={event => setFilters({ ...filters, fetcher_id: event.target.value })}>
                  <option value="">全部节点</option>
                  {fetcherOptions.map(id => <option key={id} value={id}>{getFetcherName(id)}</option>)}
                </select>
              </div>
              <div className="field-box">
                <span>运行状态</span>
                <select value={filters.status} onChange={event => setFilters({ ...filters, status: event.target.value })}>
                  <option value="">全部状态</option>
                  <option value="success">成功</option>
                  <option value="partial_failed">部分失败</option>
                  <option value="failed">失败</option>
                  <option value="running">运行中</option>
                </select>
              </div>
              <div className="field-box">
                <span>触发方式</span>
                <select value={filters.trigger_type} onChange={event => setFilters({ ...filters, trigger_type: event.target.value })}>
                  <option value="">全部触发</option>
                  <option value="manual">手动</option>
                  <option value="scheduled">定时</option>
                </select>
              </div>
              <button onClick={loadAll} disabled={loading} className="action-button action-button-secondary self-stretch">
                <RefreshCw className={loading ? 'animate-spin' : ''} /> 刷新
              </button>
            </div>
            <ActiveFilterBar items={activeFilterItems} onClearAll={clearAllFilters} className="mt-3" />
          </div>

          <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
            {loadError && (
              <div className="px-4 py-3 bg-red-50 border-b border-red-100 text-red-700 text-sm font-bold flex items-center">
                <AlertTriangle className="w-4 h-4 mr-2" /> {loadError}
              </div>
            )}
            <table className="data-table w-full text-left">
              <thead className="bg-[var(--dorami-well)] border-b border-[var(--dorami-border)] text-slate-500 text-xs tracking-wider">
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
              <tbody key={listVersion} className="divide-y divide-[var(--dorami-border)] text-sm">
                {loading && unifiedRuns.length === 0 ? (
                  Array.from({ length: 6 }).map((_, i) => (
                    <tr key={`run-skeleton-${i}`}>
                      <td className="px-4 py-4"><div className="skeleton h-6 w-16 rounded-full" /></td>
                      <td className="px-4 py-4"><div className="flex items-center gap-2.5"><div className="skeleton h-8 w-8 rounded-[var(--r-control)]" /><div className="skeleton h-4 w-32" /></div></td>
                      <td className="px-4 py-4"><div className="skeleton h-6 w-14 rounded-full" /></td>
                      <td className="px-4 py-4"><div className="skeleton h-4 w-28" /></td>
                      <td className="px-4 py-4"><div className="skeleton h-4 w-12" /></td>
                      <td className="px-4 py-4"><div className="skeleton h-4 w-10" /></td>
                      <td className="px-4 py-4"><div className="skeleton h-4 w-20" /></td>
                    </tr>
                  ))
                ) : unifiedRuns.length === 0 ? (
                  <tr>
                    <td colSpan="7" className="px-6 py-16 text-center text-slate-500 font-medium">当前过滤条件下暂无运行记录</td>
                  </tr>
                ) : unifiedRuns.map(run => {
                  const meta = runStatusMeta(run.status);
                  const isRunning = run.status === 'running';
                  return (
                    <tr key={run.key} className={`transition-colors ${isRunning ? 'bg-[var(--dorami-wash)]' : 'hover:bg-[var(--dorami-soft)]'}`}>
                      <td className="px-4 py-4">
                        <StatusBadge meta={meta} />
                      </td>
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-2.5 min-w-0">
                          {run.type === 'fetch' ? (
                            <LogoMark company={companyForId(run.fetcher_id)} size="sm" />
                          ) : (
                            <span className="run-object-mark"><Layers className="h-4 w-4" /></span>
                          )}
                          <div className="min-w-0">
                            <div className="card-title truncate">{run.title}</div>
                            <div className="font-mono text-xs text-slate-500 mt-0.5 truncate">{run.subtitle} · {run.nodeLabel}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4">
                        <StatusBadge tone="slate">{triggerLabel(run.trigger_type)}</StatusBadge>
                      </td>
                      <td className="px-4 py-4 font-mono text-xs text-slate-500">{formatDateTime(run.started_at)}</td>
                      <td className="px-4 py-4 text-xs font-bold text-slate-500 tabular-nums">
                        <span className="inline-flex items-center"><Timer className="w-3.5 h-3.5 mr-1 text-slate-500" /> {formatDuration(run.duration_ms)}</span>
                      </td>
                      <td className="px-4 py-4">
                        <div className="text-xs font-bold text-slate-700 tabular-nums">抓取 {run.fetched_count || 0} / 新增 {run.saved_count || 0} / 跳过 {run.skipped_count || 0}</div>
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

      <Modal open={jobModalOpen} onClose={() => setJobModalOpen(false)} size="6xl">
            <div className="px-5 py-4 border-b border-[var(--dorami-border)] bg-[var(--dorami-well)] flex items-center justify-between">
              <div>
                <h3 className="card-title">{editingJobId ? '编辑采集任务' : '新建采集任务'}</h3>
                <p className="text-xs text-slate-500 mt-1">采集任务负责调度、参数覆盖和运行追踪。</p>
              </div>
              <button onClick={() => setJobModalOpen(false)} className="icon-button"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-5 overflow-auto space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="text-xs font-bold text-slate-500">
                  名称
                  <input value={jobDraft.name} onChange={event => setJobDraft(prev => ({ ...prev, name: event.target.value }))} className="form-input mt-1" />
                </label>
                <label className="text-xs font-bold text-slate-500">
                  说明
                  <input value={jobDraft.description} onChange={event => setJobDraft(prev => ({ ...prev, description: event.target.value }))} className="form-input mt-1" />
                </label>
                <label className="text-xs font-bold text-slate-500">
                  整体 cron
                  <input value={jobDraft.cron_expr} onChange={event => setJobDraft(prev => ({ ...prev, cron_expr: event.target.value }))} placeholder="例如：0 9 * * *" className="form-input mt-1 font-mono" />
                </label>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5">
                <div className="border border-[var(--dorami-border)] rounded-[var(--r-card)] overflow-hidden">
                  <div className="p-3 bg-[var(--dorami-soft)] border-b border-[var(--dorami-border)]">
                    <div className="font-bold text-slate-700 text-sm flex items-center">
                      <Layers className="w-4 h-4 mr-2 text-[var(--dorami-blue)]" /> 选择节点
                    </div>
                    <div className="form-search-box relative mt-3">
                      <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input value={jobSearch} onChange={event => setJobSearch(event.target.value)} placeholder="搜索节点" className="form-input pl-9" />
                    </div>
                  </div>
                  <div className="max-h-[460px] overflow-auto divide-y divide-[var(--dorami-border)]">
                    {filteredModalFetchers.map(fetcher => {
                      const checked = draftFetcherIds.includes(fetcher.id);
                      return (
                        <button
                          key={fetcher.id}
                          onClick={() => toggleDraftFetcher(fetcher)}
                          className={`w-full px-3 py-3 flex items-center gap-3 text-left ${checked ? 'bg-[var(--dorami-wash)]' : ''} hover:bg-[var(--dorami-soft)]`}
                        >
                          <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${checked ? 'bg-blue-600 border-blue-600' : 'border-slate-300'}`}>{checked && <CheckSquare className="w-3.5 h-3.5 text-white" />}</div>
                          <LogoMark company={companyForId(fetcher.id)} size="sm" />
                          <div className="min-w-0">
                            <div className="font-bold text-slate-700 text-sm truncate">{fetcher.name}</div>
                            <div className="font-mono text-xs text-slate-500 truncate">{fetcher.id}</div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-3">
                  {draftFetcherIds.length === 0 ? (
                    <div className="border border-dashed border-[var(--dorami-border)] rounded-[var(--r-card)] p-10 text-center text-slate-500 font-medium">未选择节点</div>
                  ) : draftFetcherIds.map(fetcherId => {
                    const fetcher = fetchersById[fetcherId];
                    return (
                      <div key={fetcherId} className="border border-[var(--dorami-border)] rounded-[var(--r-card)] p-3 bg-[var(--dorami-surface)]">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-center gap-2.5 min-w-0">
                            <LogoMark company={companyForId(fetcherId)} size="sm" />
                            <div className="min-w-0">
                              <div className="card-title truncate">{fetcher?.name || fetcherId}</div>
                              <div className="font-mono text-xs text-slate-500 mt-0.5">{fetcherId}</div>
                            </div>
                          </div>
                          <button onClick={() => setJobDraft(prev => ({ ...prev, fetcher_ids: prev.fetcher_ids.filter(id => id !== fetcherId) }))} className="p-1.5 text-slate-500 hover:text-red-600 hover:bg-red-50 rounded-[var(--r-control)]"><X className="w-4 h-4" /></button>
                        </div>
                        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                          <label className="text-xs font-bold text-slate-500 md:col-span-2">
                            该节点 cron
                            <input value={(jobDraft.per_fetcher_cron || {})[fetcherId] || ''} onChange={event => updateDraftCron(fetcherId, event.target.value)} placeholder={jobDraft.cron_expr || '留空则不单独调度'} className="form-input mt-1 py-1.5 text-xs font-mono" />
                          </label>
                          {(fetcher?.parameters || []).length === 0 ? (
                            <div className="text-xs text-slate-500 font-medium bg-[var(--dorami-soft)] border border-[var(--dorami-border)] rounded-[var(--r-control)] px-3 py-2">该节点无需扩展参数</div>
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
            <div className="px-5 py-4 border-t border-[var(--dorami-border)] bg-[var(--dorami-surface)] flex justify-between gap-2">
              <label className="inline-flex items-center text-xs font-bold text-slate-500">
                <input type="checkbox" checked={jobDraft.is_active} onChange={event => setJobDraft(prev => ({ ...prev, is_active: event.target.checked }))} className="w-4 h-4 mr-2 text-[var(--dorami-blue)] rounded border-slate-300" />
                启用任务
              </label>
              <div className="flex justify-end gap-2">
                <button onClick={() => setJobModalOpen(false)} className="action-button action-button-quiet">取消</button>
                <button onClick={handleSaveJob} className="action-button action-button-primary"><Save /> 保存</button>
              </div>
            </div>
      </Modal>
    </div>
  );
}
