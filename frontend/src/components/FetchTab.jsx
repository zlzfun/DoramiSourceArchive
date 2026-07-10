import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  FlaskConical,
  Inbox,
  ListChecks,
  Layers,
  Play,
  RefreshCw,
  Save,
  Wand2,
  X,
} from 'lucide-react';
import {
  fetchFetchRuns,
  fetchRunningProgress,
  fetchSourceHealth,
  triggerBatchFetch,
  triggerFetch,
} from '../api';
import CustomNodeBuilder from './CustomNodeBuilder';

// 高级目标「AI 自定义节点」暂不开放前端入口：后端流程保留，UI 入口与面板用此开关隐藏。
const ENABLE_CUSTOM_NODE_BUILDER = false;
import {
  groupBySection,
  labelFrom,
  tierMeta,
  SOURCE_CHANNEL_LABELS,
  SIGNAL_LABELS,
  NOISE_LABELS,
  RELIABILITY_LABELS,
} from '../sourceTaxonomy';
import { healthMeta, errorTypeLabel, runStatusMeta } from '../statusMeta';
import { formatDateTime, formatRelativeTime } from '../utils/datetime';
import { collectionRunMessage, TEST_RUN_LIMIT } from '../utils/collection';

// 健康态 → 信号灯样式（后端 SourceStateRecord 只有四态，无独立「告警」态，故灯位取四态 + 全部）。
const HEALTH_SIGNAL = { healthy: 'ok', failing: 'fail', running: 'running', never_run: 'idle' };

const SIGNAL_STATS = [
  { key: 'all', label: '全部节点' },
  { key: 'healthy', label: '正常', sig: 'ok' },
  { key: 'failing', label: '失败', sig: 'fail' },
  { key: 'running', label: '运行中', sig: 'running' },
  { key: 'never_run', label: '未运行', sig: 'idle' },
];

const REFRESH_SECONDS = 45;

// 抓取参数覆盖项持久化：只存「与 schema 默认值不同」的字段，key 见下。
const FETCH_CONFIGS_STORAGE_KEY = 'dorami.fetchConfigs';

function loadStoredConfigOverrides() {
  try {
    const raw = localStorage.getItem(FETCH_CONFIGS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

const TIER_TONE_CLASS = {
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  sky: 'bg-sky-50 text-sky-700 border-sky-100',
  violet: 'bg-violet-50 text-violet-700 border-violet-100',
  slate: 'bg-[var(--dorami-soft)] text-slate-500 border-[var(--dorami-border)]',
};

function tierPillClass(tier) {
  return TIER_TONE_CLASS[tierMeta(tier).tone] || TIER_TONE_CLASS.slate;
}

function typeLabelOf(fetcher) {
  return labelFrom(SOURCE_CHANNEL_LABELS, fetcher.source_channel) || fetcher.content_type || '节点';
}

export default function FetchTab({ availableFetchers, showToast, view, setView, onArticlesChanged, onRunsChanged, onViewArticles, onViewRuns, onSaveAsJob, pendingFocus, onPendingFocusApplied }) {
  const [fetchLoading, setFetchLoading] = useState(false);
  const [healthByFetcher, setHealthByFetcher] = useState({});
  const [fetchConfigs, setFetchConfigs] = useState({});
  const [runningFetcherIds, setRunningFetcherIds] = useState(() => new Set());
  const [fetchProgress, setFetchProgress] = useState({});
  const progressSeenFetcherIdsRef = useRef(new Set());
  const [healthFilter, setHealthFilter] = useState('all');

  // 批量选择模式(方案 A):常态零勾选框;进入模式后行首浮现勾选框、分组头可整组勾选,
  // 底部批量条承载动作(批量运行/存为采集任务),ESC 或「取消」退出。
  // 「检视选中」(accent 竖条)与「批量勾选」(wash 底)两种选中语义并存、可叠加。
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState(() => new Set());
  const exitSelectMode = useCallback(() => { setSelectMode(false); setCheckedIds(new Set()); }, []);
  const toggleChecked = useCallback((id) => setCheckedIds(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);
  useEffect(() => {
    if (!selectMode) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') exitSelectMode(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectMode, exitSelectMode]);
  const [healthRefreshing, setHealthRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [countdown, setCountdown] = useState(REFRESH_SECONDS);
  const [expandedErrorFetcherIds, setExpandedErrorFetcherIds] = useState(() => new Set());
  const fetchConfigDefaultsRef = useRef({});
  const [highlightedFetcherId, setHighlightedFetcherId] = useState(null);
  const sourceRowRefs = useRef({});

  // 常驻检视器：当前点选的节点 + 其近期运行 + 试抓状态。
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [inspectorRuns, setInspectorRuns] = useState([]);
  const [inspectorRunsLoading, setInspectorRunsLoading] = useState(false);
  const [previewByNode, setPreviewByNode] = useState({});

  const fetchersById = useMemo(
    () => Object.fromEntries(availableFetchers.map(fetcher => [fetcher.id, fetcher])),
    [availableFetchers]
  );

  useEffect(() => {
    const overrides = loadStoredConfigOverrides();
    const initialConfigs = {};
    const defaults = {};
    availableFetchers.forEach(fetcher => {
      const cfg = {};
      const defaultCfg = {};
      const paramByField = {};
      (fetcher.parameters || []).forEach(param => {
        paramByField[param.field] = param;
        cfg[param.field] = param.default;
        defaultCfg[param.field] = param.default;
      });
      const ov = overrides[fetcher.id];
      if (ov && typeof ov === 'object' && !Array.isArray(ov)) {
        Object.entries(ov).forEach(([field, value]) => {
          const param = paramByField[field];
          if (!param) return; // 陈旧字段：schema 已无此参数，忽略
          // 类型不符时安全回退默认值
          if (param.type === 'number' && typeof value !== 'number') return;
          if (param.type === 'boolean' && typeof value !== 'boolean') return;
          cfg[field] = value;
        });
      }
      initialConfigs[fetcher.id] = cfg;
      defaults[fetcher.id] = defaultCfg;
    });
    fetchConfigDefaultsRef.current = defaults;
    setFetchConfigs(initialConfigs);
  }, [availableFetchers]);

  // 把「与默认值不同」的覆盖项写回 localStorage（默认值不入库，让 schema 变更自然生效）。
  const persistConfigOverrides = useCallback((configs) => {
    const defaults = fetchConfigDefaultsRef.current;
    const overrides = {};
    Object.entries(configs).forEach(([fid, cfg]) => {
      const def = defaults[fid] || {};
      const diff = {};
      Object.entries(cfg).forEach(([field, value]) => {
        if (!Object.is(value, def[field])) diff[field] = value;
      });
      if (Object.keys(diff).length) overrides[fid] = diff;
    });
    try {
      localStorage.setItem(FETCH_CONFIGS_STORAGE_KEY, JSON.stringify(overrides));
    } catch { /* localStorage 不可用时静默降级，不影响本次会话内的参数 */ }
  }, []);

  const loadSourceHealth = useCallback(async () => {
    try {
      const healthItems = await fetchSourceHealth();
      setHealthByFetcher(Object.fromEntries(healthItems.map(item => [item.fetcher_id, item])));
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => {
    loadSourceHealth();
  }, [loadSourceHealth]);

  const refreshSourceHealth = useCallback(async () => {
    setHealthRefreshing(true);
    try {
      await loadSourceHealth();
      setCountdown(REFRESH_SECONDS);
    } finally {
      setHealthRefreshing(false);
    }
  }, [loadSourceHealth]);

  // 自动刷新：开关驱动。开启时每秒倒计时，归零静默刷新健康数据（页面隐藏时暂停倒计时）；关闭时停摆。
  useEffect(() => {
    if (!autoRefresh) {
      setCountdown(REFRESH_SECONDS);
      return undefined;
    }
    let secs = REFRESH_SECONDS;
    setCountdown(secs);
    const id = setInterval(() => {
      if (document.hidden) return;
      secs -= 1;
      if (secs <= 0) {
        loadSourceHealth();
        secs = REFRESH_SECONDS;
      }
      setCountdown(secs);
    }, 1000);
    return () => clearInterval(id);
  }, [autoRefresh, loadSourceHealth]);

  useEffect(() => {
    if (runningFetcherIds.size === 0) {
      setFetchProgress({});
      progressSeenFetcherIdsRef.current.clear();
      return undefined;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchRunningProgress();
        if (cancelled) return;
        const progress = data || {};
        Object.keys(progress).forEach(id => progressSeenFetcherIdsRef.current.add(id));
        setFetchProgress(progress);
        setRunningFetcherIds(prev => {
          let changed = false;
          const next = new Set(prev);
          prev.forEach(id => {
            if (
              progressSeenFetcherIdsRef.current.has(id)
              && (!progress[id] || progress[id].status === 'completed')
            ) {
              next.delete(id);
              changed = true;
            }
          });
          return changed ? next : prev;
        });
      } catch { /* ignore transient polling errors */ }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [runningFetcherIds]);

  // 健康状态维度：缺省健康记录视为「从未运行」。
  const statusOf = useCallback((fetcher) => (
    healthByFetcher[fetcher.id]?.health_status || 'never_run'
  ), [healthByFetcher]);

  const matchesHealth = useCallback((f) => healthFilter === 'all' || statusOf(f) === healthFilter, [healthFilter, statusOf]);

  // 信号灯计数:唯一保留的筛选维度(搜索/板块/层级工具栏已退役,分组本身即板块导航)。
  const healthCounts = useMemo(() => {
    const counts = { all: availableFetchers.length };
    availableFetchers.forEach(f => { const st = statusOf(f); counts[st] = (counts[st] || 0) + 1; });
    return counts;
  }, [availableFetchers, statusOf]);

  useEffect(() => {
    if (healthFilter !== 'all' && (healthCounts[healthFilter] || 0) === 0) setHealthFilter('all');
  }, [healthCounts, healthFilter]);

  const visibleFetchers = useMemo(
    () => availableFetchers.filter(matchesHealth),
    [availableFetchers, matchesHealth],
  );

  // 调度板：按现有类别（板块）分组，组内节点铺成行（去掉主体中间层）。
  const groupedBoard = useMemo(() => (
    groupBySection(visibleFetchers).map(section => ({
      id: section.id,
      label: section.label,
      accent: section.accent,
      fetchers: section.companies
        .flatMap(bucket => bucket.fetchers)
        .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN')),
    }))
  ), [visibleFetchers]);

  // 默认选中第一个失败节点（最需关注），否则第一个可见节点；已选节点仍可见时保持不变。
  useEffect(() => {
    if (selectedNodeId && visibleFetchers.some(f => f.id === selectedNodeId)) return;
    const failing = visibleFetchers.find(f => statusOf(f) === 'failing');
    const next = failing || visibleFetchers[0];
    setSelectedNodeId(next ? next.id : null);
  }, [visibleFetchers, statusOf, selectedNodeId]);

  // 检视器选中节点时拉取该节点近期运行记录（最近 5 条）。
  useEffect(() => {
    if (!selectedNodeId) { setInspectorRuns([]); return undefined; }
    let cancelled = false;
    setInspectorRunsLoading(true);
    fetchFetchRuns({ fetcher_id: selectedNodeId }, 5)
      .then(rows => { if (!cancelled) setInspectorRuns(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setInspectorRuns([]); })
      .finally(() => { if (!cancelled) setInspectorRunsLoading(false); });
    return () => { cancelled = true; };
  }, [selectedNodeId]);

  // 接收来自知识台账「数据来源」列的定位请求：清空筛选、选中并高亮该节点。
  useEffect(() => {
    if (!pendingFocus?.source_id) return;
    if (availableFetchers.length === 0) return; // 节点目录尚未就绪，等下一轮
    const sid = pendingFocus.source_id;
    const fetcher = fetchersById[sid];
    onPendingFocusApplied?.();
    if (!fetcher) {
      showToast?.('该来源在节点目录中没有对应节点', 'info');
      return;
    }
    setView('catalog');
    setHealthFilter('all');
    setSelectedNodeId(sid);
    setHighlightedFetcherId(sid);
  }, [pendingFocus, availableFetchers, fetchersById, onPendingFocusApplied, showToast, setView]);

  // 高亮目标行：下一帧滚动到视野中央，短暂高亮后自动消退。
  useEffect(() => {
    if (!highlightedFetcherId) return undefined;
    const raf = requestAnimationFrame(() => {
      sourceRowRefs.current[highlightedFetcherId]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    const timer = setTimeout(() => setHighlightedFetcherId(null), 2400);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
    };
  }, [highlightedFetcherId]);

  const toggleErrorExpanded = (fetcherId) => {
    setExpandedErrorFetcherIds(prev => {
      const next = new Set(prev);
      if (next.has(fetcherId)) next.delete(fetcherId);
      else next.add(fetcherId);
      return next;
    });
  };

  const updateFetcherConfig = (fetcherId, field, value) => {
    setFetchConfigs(prev => {
      const next = {
        ...prev,
        [fetcherId]: { ...(prev[fetcherId] || {}), [field]: value },
      };
      persistConfigOverrides(next);
      return next;
    });
  };

  const paramOverrideDiff = useCallback((fetcherId) => {
    const cfg = fetchConfigs[fetcherId] || {};
    const def = fetchConfigDefaultsRef.current[fetcherId] || {};
    const diff = {};
    Object.entries(cfg).forEach(([field, value]) => {
      if (!Object.is(value, def[field])) diff[field] = value;
    });
    return diff;
  }, [fetchConfigs]);

  const selectNode = (fetcherId) => {
    setSelectedNodeId(fetcherId);
    setMobileInspectorOpen(true);
  };

  const runSingleFetcher = useCallback((fetcher) => {
    if (runningFetcherIds.has(fetcher.id)) return;
    progressSeenFetcherIdsRef.current.delete(fetcher.id);
    setRunningFetcherIds(prev => new Set(prev).add(fetcher.id));
    showToast(`开始抓取「${fetcher.name}」…`, 'info');
    onRunsChanged?.();
    triggerFetch(fetcher.id, fetchConfigs[fetcher.id] || {})
      .then((result) => {
        const saved = result?.saved_count ?? 0;
        const failed = result?.failed_count ?? 0;
        showToast(`「${fetcher.name}」抓取完成：新增 ${saved} 条${failed ? `，失败 ${failed}` : ''}`, failed > 0 ? 'info' : 'success');
        loadSourceHealth();
        onArticlesChanged?.();
        onRunsChanged?.();
      })
      .catch(e => showToast(`「${fetcher.name}」抓取失败：${e.message || '未知错误'}`, 'error'))
      .finally(() => {
        setRunningFetcherIds(prev => {
          const next = new Set(prev);
          next.delete(fetcher.id);
          return next;
        });
      });
  }, [runningFetcherIds, fetchConfigs, showToast, onRunsChanged, onArticlesChanged, loadSourceHealth]);

  // 批量运行：对给定节点集触发临时抓取（后台任务，聚合结果回来后统一提示）。
  const runFetchers = useCallback(async (ids, options = {}) => {
    if (ids.length === 0) return;
    setFetchLoading(true);
    onRunsChanged?.();
    ids.forEach(id => progressSeenFetcherIdsRef.current.delete(id));
    setRunningFetcherIds(prev => {
      const next = new Set(prev);
      ids.forEach(id => next.add(id));
      return next;
    });
    const items = ids.map(fetcherId => ({ fetcher_id: fetcherId, params: fetchConfigs[fetcherId] || {} }));
    let result = null;
    try {
      result = await triggerBatchFetch(items, options);
    } catch (e) {
      showToast(e.message || '批量抓取失败', 'error');
    }
    setFetchLoading(false);
    setRunningFetcherIds(prev => {
      const next = new Set(prev);
      ids.forEach(id => next.delete(id));
      return next;
    });
    if (result) {
      const successCount = ids.length - (result.failed_count || 0);
      const suffix = options.testLimit ? `（每源 ${options.testLimit} 条）` : '';
      showToast(
        collectionRunMessage(`批量抓取完成${suffix}`, result, successCount),
        result.failed_count ? 'error' : 'success',
      );
      loadSourceHealth();
      onArticlesChanged?.();
      onRunsChanged?.();
    }
    return result;
  }, [fetchConfigs, onRunsChanged, showToast, loadSourceHealth, onArticlesChanged]);

  const handleBatchRun = () => {
    const ids = visibleFetchers.map(f => f.id);
    if (ids.length === 0) return;
    runFetchers(ids);
  };

  // 勾选集批量运行 / 存为采集任务(按可见顺序,参数差量随行携带)。
  const runChecked = () => {
    const ids = visibleFetchers.filter(f => checkedIds.has(f.id)).map(f => f.id);
    if (ids.length) runFetchers(ids);
  };
  const saveCheckedAsJob = () => {
    const ids = visibleFetchers.filter(f => checkedIds.has(f.id)).map(f => f.id);
    if (!ids.length) return;
    const per = {};
    ids.forEach(id => {
      const diff = paramOverrideDiff(id);
      if (Object.keys(diff).length) per[id] = diff;
    });
    onSaveAsJob?.({ fetcher_ids: ids, per_fetcher_params: per });
  };
  const checkAllVisible = () => setCheckedIds(new Set(visibleFetchers.map(f => f.id)));

  // 单节点试抓预览：以当前参数抓取 1 条（会入库，与既有「试抓」同口径），结果内嵌到检视器。
  const runNodePreview = useCallback(async (fetcher) => {
    setPreviewByNode(prev => ({ ...prev, [fetcher.id]: { status: 'running' } }));
    try {
      const result = await triggerBatchFetch(
        [{ fetcher_id: fetcher.id, params: fetchConfigs[fetcher.id] || {} }],
        { testLimit: TEST_RUN_LIMIT },
      );
      const saved = result?.saved_count ?? 0;
      const fetched = result?.fetched_count ?? result?.total_fetched ?? null;
      const failed = result?.failed_count ?? 0;
      setPreviewByNode(prev => ({
        ...prev,
        [fetcher.id]: { status: failed ? 'error' : 'done', saved, fetched, failed },
      }));
      loadSourceHealth();
      onArticlesChanged?.();
      onRunsChanged?.();
    } catch (e) {
      setPreviewByNode(prev => ({ ...prev, [fetcher.id]: { status: 'error', error: e.message || '试抓失败' } }));
    }
  }, [fetchConfigs, loadSourceHealth, onArticlesChanged, onRunsChanged]);

  const saveNodeAsJob = (fetcher) => {
    const diff = paramOverrideDiff(fetcher.id);
    onSaveAsJob?.({
      fetcher_ids: [fetcher.id],
      per_fetcher_params: Object.keys(diff).length ? { [fetcher.id]: diff } : {},
    });
  };

  const renderParamInput = (fetcherId, param) => {
    const value = (fetchConfigs[fetcherId] || {})[param.field] ?? param.default ?? '';
    if (param.type === 'boolean') {
      const checked = typeof value === 'boolean' ? value : ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
      return (
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          onClick={() => updateFetcherConfig(fetcherId, param.field, !checked)}
          className={`signal-switch ${checked ? 'signal-switch-on' : ''}`}
          aria-label={param.label || param.field}
        />
      );
    }
    if (Array.isArray(param.options) && param.options.length > 0) {
      return (
        <select value={value} onChange={event => updateFetcherConfig(fetcherId, param.field, event.target.value)} className="node-param-input inspector-param-select">
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
        className="inspector-param-input"
        aria-label={param.label || param.field}
      />
    );
  };

  const renderNode = (fetcher) => {
    const health = healthByFetcher[fetcher.id];
    const status = health?.health_status || 'never_run';
    const statusLabel = healthMeta(status).label;
    const isRunning = runningFetcherIds.has(fetcher.id);
    const progress = fetchProgress[fetcher.id];
    const errorMessage = health?.latest_error_message;
    const errorType = health?.latest_error_type;
    const isFailing = status === 'failing' && Boolean(errorMessage);
    const errorExpanded = expandedErrorFetcherIds.has(fetcher.id);
    const isSelected = selectedNodeId === fetcher.id;
    const totalArticles = health?.total_articles ?? 0;
    const consecutiveFailures = health?.consecutive_failures ?? 0;

    return (
      <div key={fetcher.id} ref={el => { sourceRowRefs.current[fetcher.id] = el; }}>
        <div
          role="button"
          tabIndex={0}
          onClick={() => selectNode(fetcher.id)}
          onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(fetcher.id); } }}
          className={`board-node ${isSelected ? 'board-node-sel' : ''} ${selectMode && checkedIds.has(fetcher.id) ? 'board-node-checked' : ''} ${highlightedFetcherId === fetcher.id ? 'board-node-focus' : ''}`}
          title={health?.latest_run_at ? `上次运行 ${formatDateTime(health.latest_run_at)}` : '从未运行'}
        >
          {selectMode && (
            <input
              type="checkbox"
              className="h-4 w-4 cursor-pointer rounded"
              checked={checkedIds.has(fetcher.id)}
              onClick={e => e.stopPropagation()}
              onChange={() => toggleChecked(fetcher.id)}
              aria-label={`选择：${fetcher.name}`}
            />
          )}
          <span className={`signal-dot signal-dot-${HEALTH_SIGNAL[status] || 'idle'}`} title={statusLabel} />
          <span className="board-node-id">
            <span className="board-node-name" title={fetcher.name}>
              <span className="board-node-name-text">{fetcher.name}</span>
              {fetcher.provenance_tier && (
                <span className={`tier-pill ${tierPillClass(fetcher.provenance_tier)}`}>{tierMeta(fetcher.provenance_tier).short}</span>
              )}
            </span>
            <span className="board-node-sid" title={fetcher.id}>{fetcher.id}</span>
          </span>
          <span className="chip board-node-type">{typeLabelOf(fetcher)}</span>
          <span className="board-node-last">
            <span className="board-node-last-time">{formatRelativeTime(health?.latest_run_at)}</span>
            <span className={`board-node-last-res ${status === 'failing' ? 'is-bad' : ''}`}>
              {status === 'failing'
                ? (consecutiveFailures > 0 ? `连续 ${consecutiveFailures} 次失败` : '最近失败')
                : status === 'running'
                  ? '运行中'
                  : `${statusLabel} · 累计 ${totalArticles} 篇`}
            </span>
          </span>
          <span className="board-node-acts">
            <button
              type="button"
              className="board-node-act"
              disabled={isRunning}
              onClick={event => { event.stopPropagation(); runSingleFetcher(fetcher); }}
              title="立即运行"
              aria-label={`立即运行 ${fetcher.name}`}
            >
              {isRunning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            </button>
            <button
              type="button"
              className="board-node-act"
              onClick={event => { event.stopPropagation(); selectNode(fetcher.id); runNodePreview(fetcher); }}
              title="试抓预览"
              aria-label={`试抓预览 ${fetcher.name}`}
            >
              <FlaskConical className="h-3.5 w-3.5" />
            </button>
          </span>
        </div>

        {isFailing && (
          <div className="board-node-error">
            <button
              type="button"
              className="board-node-error-summary"
              onClick={() => toggleErrorExpanded(fetcher.id)}
              aria-expanded={errorExpanded}
              title={errorExpanded ? '收起失败详情' : '展开失败详情'}
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span className="board-node-error-type">{errorTypeLabel(errorType)}</span>
              <span className={`board-node-error-msg ${errorExpanded ? '' : 'truncate'}`}>{errorMessage}</span>
              {health?.latest_failure_at && (
                <span className="board-node-error-time">{formatRelativeTime(health.latest_failure_at)}</span>
              )}
              {errorExpanded ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
            </button>
            {errorExpanded && (
              <div className="board-node-error-detail">
                <p className="board-node-error-detail-msg">{errorMessage}</p>
                <div className="board-node-error-detail-meta">
                  <span>类型：{errorTypeLabel(errorType)}</span>
                  {health?.latest_failure_at && <span>失败时间：{formatDateTime(health.latest_failure_at, '未知')}</span>}
                  <button type="button" className="board-node-error-link" onClick={() => onViewRuns?.(fetcher.id, { status: 'failed' })}>查看失败运行 →</button>
                </div>
              </div>
            )}
          </div>
        )}

        {isRunning && (
          <div className="board-node-progress">
            <RefreshCw className="h-3 w-3 animate-spin shrink-0" />
            <span>采集中</span>
            <div className="board-progress-bar">
              <i style={progress?.total ? { width: `${Math.min(100, Math.round((progress.current / progress.total) * 100))}%` } : { width: '35%' }} />
            </div>
            <span className="board-node-progress-count">
              {progress ? (progress.total ? `${progress.current} / ${progress.total}` : `${progress.current}`) : '排队中'}
            </span>
          </div>
        )}
      </div>
    );
  };

  const selectedFetcher = selectedNodeId ? fetchersById[selectedNodeId] : null;

  const renderInspector = () => {
    if (!selectedFetcher) {
      return (
        <div className="inspector-empty">
          <Inbox className="h-7 w-7" />
          <p>从左侧调度板选择一个节点</p>
          <span>查看描述、运行参数、试抓预览与近期运行</span>
        </div>
      );
    }
    const fetcher = selectedFetcher;
    const health = healthByFetcher[fetcher.id];
    const status = health?.health_status || 'never_run';
    const statusLabel = healthMeta(status).label;
    const isRunning = runningFetcherIds.has(fetcher.id);
    const params = fetcher.parameters || [];
    const preview = previewByNode[fetcher.id];
    const signalLabel = labelFrom(SIGNAL_LABELS, fetcher.signal_strength);
    const noiseLabel = labelFrom(NOISE_LABELS, fetcher.noise_risk);
    const reliabilityLabel = labelFrom(RELIABILITY_LABELS, fetcher.fetch_reliability);
    const contentTags = (fetcher.content_tags || []).slice(0, 6);
    const hasReview = Boolean(fetcher.signal_strength || fetcher.noise_risk || fetcher.fetch_reliability || contentTags.length);
    const totalArticles = health?.total_articles ?? 0;

    return (
      <>
        <div className="inspector-head">
          <div className="inspector-head-top">
            <span className={`signal-dot signal-dot-${HEALTH_SIGNAL[status] || 'idle'}`} title={statusLabel} />
            <h2 className="inspector-title" title={fetcher.name}>{fetcher.name}</h2>
            <span className={`inspector-stamp inspector-stamp-${status}`}>{statusLabel}</span>
            <button type="button" className="inspector-close" onClick={() => setMobileInspectorOpen(false)} aria-label="关闭检视器">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="inspector-sid">
            <span className="font-mono truncate" title={fetcher.id}>{fetcher.id}</span>
            <span className="inspector-sid-dot">{typeLabelOf(fetcher)}</span>
          </div>
        </div>

        <div className="inspector-body">
          {fetcher.desc && <p className="inspector-desc">{fetcher.desc}</p>}

          {fetcher.base_url && (
            <a className="inspector-url" href={fetcher.base_url} target="_blank" rel="noreferrer" title="打开来源入口">
              <ExternalLink className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{fetcher.base_url}</span>
            </a>
          )}

          <div className="inspector-metarow">
            <button type="button" className="inspector-metabtn" onClick={() => onViewArticles?.(fetcher.id)} title="查看该节点抓取的文章">
              <FileText className="h-3.5 w-3.5" />
              <span>抓取文章</span>
              <b>{totalArticles}</b>
            </button>
            <button type="button" className="inspector-metabtn" onClick={() => onViewRuns?.(fetcher.id)} title="查看全部运行历史">
              <Activity className="h-3.5 w-3.5" />
              <span>运行历史</span>
            </button>
          </div>

          <div className="inspector-section">
            <div className="inspector-section-head">
              <h3 className="micro-label">运行参数</h3>
              {params.length > 0 && <span className="inspector-persist-note">改动即存草稿，下次自动带出</span>}
            </div>
            {params.length === 0 ? (
              <div className="inspector-empty-mini">该节点无需抓取参数</div>
            ) : (
              <div className="inspector-params">
                {params.map(param => (
                  <div key={param.field} className="inspector-param">
                    <label className="inspector-param-label" title={param.field}>
                      {param.label || param.field}
                      {param.hint && <span className="inspector-param-hint">{param.hint}</span>}
                    </label>
                    {renderParamInput(fetcher.id, param)}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="inspector-actions">
            <button
              type="button"
              className="action-button action-button-primary inspector-act"
              disabled={isRunning}
              onClick={() => runSingleFetcher(fetcher)}
            >
              {isRunning ? <RefreshCw className="animate-spin" /> : <Play className="fill-current" />} 立即运行
            </button>
            <button
              type="button"
              className="action-button action-button-quiet inspector-act"
              disabled={preview?.status === 'running'}
              onClick={() => runNodePreview(fetcher)}
            >
              <FlaskConical /> 试抓预览
            </button>
            <button
              type="button"
              className="action-button action-button-quiet inspector-act"
              onClick={() => saveNodeAsJob(fetcher)}
            >
              <Save /> 存为采集任务
            </button>
          </div>

          {preview && (
            <div className="inspector-preview">
              {preview.status === 'running' ? (
                <div className="inspector-preview-head">
                  <span className="inspector-spin" /> 试抓中（每源 {TEST_RUN_LIMIT} 条）…
                </div>
              ) : preview.status === 'error' ? (
                <div className="inspector-preview-head is-bad">
                  <AlertTriangle className="h-3.5 w-3.5" /> 试抓失败{preview.error ? `：${preview.error}` : ''}
                </div>
              ) : (
                <>
                  <div className="inspector-preview-head">
                    试抓完成 · 新增 {preview.saved ?? 0} 条{preview.failed ? `，失败 ${preview.failed}` : ''}
                  </div>
                  <div className="inspector-preview-item">
                    以当前参数试抓 1 条并写入台账；如需批量入库请用「立即运行」。
                  </div>
                </>
              )}
            </div>
          )}

          <div className="inspector-section">
            <h3 className="micro-label">近期运行</h3>
            {inspectorRunsLoading ? (
              <div className="inspector-empty-mini">加载中…</div>
            ) : inspectorRuns.length === 0 ? (
              <div className="inspector-empty-mini">尚无运行记录，首次运行后这里会出现批次时间线</div>
            ) : (
              <div className="inspector-runlist">
                {inspectorRuns.map(run => {
                  const meta = runStatusMeta(run.status);
                  return (
                    <button
                      key={run.id}
                      type="button"
                      className="inspector-runrow"
                      onClick={() => onViewRuns?.(fetcher.id)}
                      title="查看运行历史"
                    >
                      <span className={`inspector-run-dot inspector-run-${meta.tone}`} />
                      <b>{meta.label}</b>
                      <span className="inspector-run-saved">+{run.saved_count ?? 0}</span>
                      <time>{formatRelativeTime(run.started_at)}</time>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {hasReview && (
            <div className="inspector-section">
              <h3 className="micro-label">源审查</h3>
              <div className="inspector-review-grid">
                {fetcher.signal_strength && <div><span className="tiny-meta">信号</span><div className="inspector-review-val">{signalLabel}</div></div>}
                {fetcher.noise_risk && <div><span className="tiny-meta">噪声</span><div className="inspector-review-val">{noiseLabel}</div></div>}
                {fetcher.fetch_reliability && <div><span className="tiny-meta">稳定性</span><div className="inspector-review-val">{reliabilityLabel}</div></div>}
              </div>
              {contentTags.length > 0 && (
                <div className="inspector-tags">
                  {contentTags.map(tag => <span key={tag}>{tag}</span>)}
                </div>
              )}
            </div>
          )}
        </div>
      </>
    );
  };

  // 自定义节点构建器（暂关闭）：保留原路由分支，避免破坏 view 契约。
  if (ENABLE_CUSTOM_NODE_BUILDER && view === 'custom') {
    return (
      <div className="space-y-6">
        <div className="page-head">
          <h1 className="page-title">节点管理</h1>
          <div className="page-head-actions">
            <div className="segmented-control">
              <button onClick={() => setView('catalog')} className="segmented-option"><Layers /> 节点目录</button>
              <button onClick={() => setView('custom')} className="segmented-option segmented-option-active"><Wand2 /> AI 自定义节点</button>
            </div>
          </div>
        </div>
        <CustomNodeBuilder showToast={showToast} />
      </div>
    );
  }

  const boardIsEmpty = groupedBoard.length === 0;

  return (
    <div className="nodes-shell">
      <div className="page-head">
        <h1 className="page-title">节点管理</h1>
        <div className="page-head-actions">
          {ENABLE_CUSTOM_NODE_BUILDER && (
            <div className="segmented-control">
              <button onClick={() => setView('catalog')} className="segmented-option segmented-option-active"><Layers /> 节点目录</button>
              <button onClick={() => setView('custom')} className="segmented-option"><Wand2 /> AI 自定义节点</button>
            </div>
          )}
          <button
            type="button"
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
            className="action-button action-button-quiet"
            title={selectMode ? '退出批量选择(ESC)' : '勾选若干节点后批量运行或存为采集任务'}
          >
            <ListChecks /> {selectMode ? '退出批量选择' : '批量选择'}
          </button>
          {!selectMode && (
            <button
              type="button"
              onClick={handleBatchRun}
              disabled={fetchLoading || visibleFetchers.length === 0}
              className="action-button action-button-primary"
              title="对当前筛选下的全部节点触发临时抓取"
            >
              {fetchLoading ? <RefreshCw className="animate-spin" /> : <Play className="fill-current" />} 批量运行 {visibleFetchers.length}
            </button>
          )}
        </div>
      </div>

      <div className="nodespaper">
        <div className="signal-strip" role="group" aria-label="节点健康总览与筛选">
          {SIGNAL_STATS.map((stat, idx) => (
            <div key={stat.key} className="signal-stat-wrap">
              {idx === 1 && <span className="signal-strip-divider" />}
              <button
                type="button"
                className={`signal-stat ${healthFilter === stat.key ? 'signal-stat-on' : ''}`}
                onClick={() => setHealthFilter(stat.key)}
              >
                {stat.sig && <span className={`signal-dot signal-dot-${stat.sig}`} />}
                <span className="signal-stat-n">{healthCounts[stat.key] || 0}</span>
                <span className="signal-stat-label">{stat.label}</span>
              </button>
            </div>
          ))}
          <div className="signal-strip-tools">
            <button
              type="button"
              onClick={refreshSourceHealth}
              disabled={healthRefreshing}
              className="icon-button signal-refresh"
              title="立即刷新运行健康数据"
              aria-label="刷新运行健康数据"
            >
              <RefreshCw className={`h-4 w-4 ${healthRefreshing ? 'animate-spin' : ''}`} />
            </button>
            <div className="signal-autorefresh">
              <button
                type="button"
                role="switch"
                aria-checked={autoRefresh}
                onClick={() => setAutoRefresh(v => !v)}
                className={`signal-switch ${autoRefresh ? 'signal-switch-on' : ''}`}
                aria-label="自动刷新"
              />
              <span>自动刷新</span>
              <span className="signal-countdown">{autoRefresh ? `${countdown}s` : '—'}</span>
            </div>
          </div>
        </div>


        <div className={`nodes-body ${mobileInspectorOpen ? 'inspector-open' : ''}`}>
          <div className={`board ${selectMode ? 'is-selecting' : ''}`}>
            {boardIsEmpty ? (
              <div className="board-empty">当前筛选条件下没有匹配的节点</div>
            ) : (
              groupedBoard.map(section => (
                <div key={section.id} className="board-group">
                  <div className="board-group-head">
                    {selectMode && (
                      <input
                        type="checkbox"
                        className="h-4 w-4 cursor-pointer rounded"
                        checked={section.fetchers.every(f => checkedIds.has(f.id))}
                        ref={el => {
                          if (!el) return;
                          const all = section.fetchers.every(f => checkedIds.has(f.id));
                          const some = section.fetchers.some(f => checkedIds.has(f.id));
                          el.indeterminate = some && !all;
                        }}
                        onChange={() => setCheckedIds(prev => {
                          const next = new Set(prev);
                          const all = section.fetchers.every(f => next.has(f.id));
                          section.fetchers.forEach(f => { if (all) next.delete(f.id); else next.add(f.id); });
                          return next;
                        })}
                        aria-label={`选择整组：${section.label}`}
                      />
                    )}
                    <span className="board-group-marker" style={{ '--group-accent': section.accent }} />
                    <span className="board-group-name">{section.label}</span>
                    <span className="board-group-count">{section.fetchers.length} 个节点</span>
                    <span className="board-group-rule" />
                  </div>
                  {section.fetchers.map(renderNode)}
                </div>
              ))
            )}
          </div>

          <aside className="board-inspector" aria-label="节点检视器">
            {renderInspector()}
          </aside>
          {mobileInspectorOpen && <div className="inspector-scrim" onClick={() => setMobileInspectorOpen(false)} />}
        </div>

        {selectMode && (
          <div className="board-batchbar">
            <span className="board-batch-n">已选 {checkedIds.size} 个节点</span>
            <button
              type="button"
              className="action-button action-button-primary"
              disabled={fetchLoading || checkedIds.size === 0}
              onClick={runChecked}
            >
              {fetchLoading ? <RefreshCw className="animate-spin" /> : <Play className="fill-current" />} 批量运行
            </button>
            <button
              type="button"
              className="action-button action-button-quiet"
              disabled={checkedIds.size === 0}
              onClick={saveCheckedAsJob}
            >
              <Save /> 存为采集任务
            </button>
            <span className="flex-1" />
            <button type="button" className="action-button action-button-quiet" onClick={checkAllVisible}>全选可见</button>
            <button type="button" className="action-button action-button-quiet" onClick={exitSelectMode}>取消</button>
          </div>
        )}
      </div>

    </div>
  );
}
