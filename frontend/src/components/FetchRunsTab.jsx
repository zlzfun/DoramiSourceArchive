import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, History, RefreshCw, Search, Timer, XCircle } from 'lucide-react';
import { fetchFetchRuns } from '../api';

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
  return { label: '运行中', className: 'text-amber-700 bg-amber-50 border-amber-100', icon: Clock3 };
}

export default function FetchRunsTab({ availableFetchers, showToast }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [filters, setFilters] = useState({ fetcher_id: '', status: '', trigger_type: '' });
  const { fetcher_id: filterFetcherId, status: filterStatus, trigger_type: filterTriggerType } = filters;

  const fetcherOptions = useMemo(() => {
    const ids = new Set(runs.map(run => run.fetcher_id).filter(Boolean));
    availableFetchers.forEach(fetcher => ids.add(fetcher.id));
    return Array.from(ids).sort();
  }, [runs, availableFetchers]);

  const getFetcherName = (id) => {
    const fetcher = availableFetchers.find(f => f.id === id);
    return fetcher ? fetcher.name : id;
  };

  const loadRuns = async () => {
    setLoading(true);
    try {
      const data = await fetchFetchRuns(filters);
      setRuns(data);
      setLoadError('');
    } catch (e) {
      showToast(e.message || '运行历史加载失败', 'error');
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;

    async function loadInitialRuns() {
      try {
        const data = await fetchFetchRuns({
          fetcher_id: filterFetcherId,
          status: filterStatus,
          trigger_type: filterTriggerType,
        });
        if (!cancelled) {
          setRuns(data);
          setLoadError('');
        }
      } catch (e) {
        if (!cancelled) {
          setLoadError(e.message || '运行历史加载失败');
        }
      }
    }

    loadInitialRuns();
    return () => { cancelled = true; };
  }, [filterFetcherId, filterStatus, filterTriggerType]);

  const totals = runs.reduce((acc, run) => {
    acc.fetched += run.fetched_count || 0;
    acc.saved += run.saved_count || 0;
    acc.skipped += run.skipped_count || 0;
    if (run.status === 'success') acc.success += 1;
    if (run.status === 'failed') acc.failed += 1;
    if (run.status === 'running') acc.running += 1;
    return acc;
  }, { fetched: 0, saved: 0, skipped: 0, success: 0, failed: 0, running: 0 });

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold flex items-center"><History className="w-6 h-6 mr-2 text-blue-500" /> 抓取运行历史</h2>
          <p className="text-sm text-slate-500 mt-2">追踪手动与定时抓取的状态、耗时、增量数量和失败原因。</p>
        </div>
        <button onClick={loadRuns} disabled={loading} className="text-sm text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 shadow-sm px-4 py-2 rounded-lg transition-all flex items-center font-bold w-fit">
          <RefreshCw className={`w-4 h-4 mr-2 text-blue-600 ${loading ? 'animate-spin' : ''}`} /> 刷新历史
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="text-xs font-bold text-slate-400 mb-1">本页运行</div>
          <div className="text-2xl font-black text-slate-800">{runs.length}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="text-xs font-bold text-slate-400 mb-1">新增入库</div>
          <div className="text-2xl font-black text-emerald-600">{totals.saved}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="text-xs font-bold text-slate-400 mb-1">抓取产出</div>
          <div className="text-2xl font-black text-blue-600">{totals.fetched}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="text-xs font-bold text-slate-400 mb-1">重复跳过</div>
          <div className="text-2xl font-black text-amber-600">{totals.skipped}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="text-xs font-bold text-slate-400 mb-1">失败次数</div>
          <div className="text-2xl font-black text-red-600">{totals.failed}</div>
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded-2xl shadow-sm p-3 flex flex-col md:flex-row gap-3">
        <div className="flex items-center flex-1 bg-slate-50 border border-slate-100 rounded-xl px-3">
          <Search className="w-4 h-4 text-slate-400 mr-2" />
          <select value={filters.fetcher_id} onChange={e => setFilters({ ...filters, fetcher_id: e.target.value })} className="w-full bg-transparent py-2 text-sm font-bold text-slate-700 outline-none">
            <option value="">全部数据源</option>
            {fetcherOptions.map(id => <option key={id} value={id}>{getFetcherName(id)}</option>)}
          </select>
        </div>
        <select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })} className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 outline-none">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
          <option value="running">运行中</option>
        </select>
        <select value={filters.trigger_type} onChange={e => setFilters({ ...filters, trigger_type: e.target.value })} className="bg-slate-50 border border-slate-100 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 outline-none">
          <option value="">全部触发</option>
          <option value="manual">手动</option>
          <option value="scheduled">定时</option>
        </select>
      </div>

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
              <th className="px-4 py-3">数据源</th>
              <th className="px-4 py-3">触发</th>
              <th className="px-4 py-3">开始时间</th>
              <th className="px-4 py-3">耗时</th>
              <th className="px-4 py-3">数量</th>
              <th className="px-4 py-3">参数</th>
              <th className="px-4 py-3">失败信息</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-sm">
            {runs.length === 0 ? (
              <tr>
                <td colSpan="8" className="px-6 py-16 text-center text-slate-400 font-medium">
                  当前过滤条件下暂无运行记录
                </td>
              </tr>
            ) : runs.map(run => {
              const meta = statusMeta(run.status);
              const StatusIcon = meta.icon;
              return (
                <tr key={run.id} className="hover:bg-blue-50/40 transition-colors">
                  <td className="px-4 py-4">
                    <span className={`inline-flex items-center px-2.5 py-1 rounded-lg border text-xs font-black ${meta.className}`}>
                      <StatusIcon className="w-3.5 h-3.5 mr-1" /> {meta.label}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    <div className="font-extrabold text-slate-800 text-sm">{getFetcherName(run.fetcher_id)}</div>
                    <div className="font-mono text-[11px] text-slate-400 mt-0.5">{run.fetcher_id}</div>
                  </td>
                  <td className="px-4 py-4">
                    <span className="text-xs font-bold text-slate-600 bg-slate-100 px-2 py-1 rounded-lg">
                      {run.trigger_type === 'scheduled' ? '定时' : '手动'}
                    </span>
                  </td>
                  <td className="px-4 py-4 font-mono text-xs text-slate-500">{formatDateTime(run.started_at)}</td>
                  <td className="px-4 py-4 text-xs font-bold text-slate-600">
                    <span className="inline-flex items-center"><Timer className="w-3.5 h-3.5 mr-1 text-blue-500" /> {formatDuration(run.duration_ms)}</span>
                  </td>
                  <td className="px-4 py-4">
                    <div className="text-xs font-bold text-slate-700">
                      抓取 {run.fetched_count || 0} / 新增 {run.saved_count || 0} / 跳过 {run.skipped_count || 0}
                    </div>
                  </td>
                  <td className="px-4 py-4 max-w-xs">
                    <code className="block text-[11px] text-slate-500 bg-slate-50 border border-slate-100 rounded-lg px-2 py-1 truncate" title={run.params_json}>{run.params_json}</code>
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
    </div>
  );
}
