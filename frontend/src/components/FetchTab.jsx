import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  Info,
  Layers,
  Play,
  RefreshCw,
  Search,
  Settings2,
  Wand2,
} from 'lucide-react';
import {
  fetchRunningProgress,
  fetchSourceHealth,
  triggerBatchFetch,
  triggerFetch,
} from '../api';
import LogoMark from './LogoMark';
import RunningWidget from './RunningWidget';
import CustomNodeBuilder from './CustomNodeBuilder';

// 高级目标「AI 自定义节点」暂不开放前端入口：后端流程保留，UI 入口与面板用此开关隐藏。
const ENABLE_CUSTOM_NODE_BUILDER = false;
import {
  SECTIONS,
  groupBySection,
  labelFrom,
  resolveCompany,
  tierMeta,
  SOURCE_CHANNEL_LABELS,
  SOURCE_SCOPE_LABELS,
  SIGNAL_LABELS,
  NOISE_LABELS,
  RELIABILITY_LABELS,
} from '../sourceTaxonomy';
import { healthMeta } from '../statusMeta';
import { formatDateTime, formatRelativeTime } from '../utils/datetime';
import { collectionRunMessage } from '../utils/collection';

const TIER_FILTER_OPTIONS = [
  { value: 'all', label: '全部层级' },
  { value: 'tier0_primary', label: '官方一手' },
  { value: 'tier1_curated', label: '聚合筛选' },
  { value: 'tier2_commentary', label: '评论观点' },
];

const TIER_TONE_CLASS = {
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  sky: 'bg-sky-50 text-sky-700 border-sky-100',
  violet: 'bg-violet-50 text-violet-700 border-violet-100',
  slate: 'bg-[var(--dorami-soft)] text-slate-500 border-[var(--dorami-border)]',
};

function tierPillClass(tier) {
  return TIER_TONE_CLASS[tierMeta(tier).tone] || TIER_TONE_CLASS.slate;
}

const HEALTH_RANK = { failing: 0, running: 1, never_run: 2, healthy: 3 };

function aggregateHealth(fetchers, healthByFetcher) {
  const summary = { total: fetchers.length, healthy: 0, failing: 0, running: 0, never_run: 0, articles: 0, latest: null, worst: 'healthy' };
  fetchers.forEach(fetcher => {
    const h = healthByFetcher[fetcher.id] || {};
    const status = h.health_status || 'never_run';
    if (summary[status] !== undefined) summary[status] += 1;
    summary.articles += h.total_articles || 0;
    if (h.latest_run_at && (!summary.latest || h.latest_run_at > summary.latest)) summary.latest = h.latest_run_at;
    if ((HEALTH_RANK[status] ?? 3) < (HEALTH_RANK[summary.worst] ?? 3)) summary.worst = status;
  });
  return summary;
}

export default function FetchTab({ availableFetchers, showToast, view, setView, onArticlesChanged, onRunsChanged, onViewArticles, onViewRuns, onViewRunning, pendingFocus, onPendingFocusApplied }) {
  const [fetchLoading, setFetchLoading] = useState(false);
  const [healthByFetcher, setHealthByFetcher] = useState({});
  const [selectedFetchers, setSelectedFetchers] = useState([]);
  const [fetchConfigs, setFetchConfigs] = useState({});
  const [runningFetcherIds, setRunningFetcherIds] = useState(() => new Set());
  const [fetchProgress, setFetchProgress] = useState({});
  const progressSeenFetcherIdsRef = useRef(new Set());
  const [sectionFilter, setSectionFilter] = useState('all');
  const [tierFilter, setTierFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedCompanies, setExpandedCompanies] = useState(() => new Set());
  const [collapsedCatalogSections, setCollapsedCatalogSections] = useState(() => new Set());
  const [expandedParamFetcherId, setExpandedParamFetcherId] = useState(null);
  const [expandedReviewFetcherId, setExpandedReviewFetcherId] = useState(null);
  const [highlightedFetcherId, setHighlightedFetcherId] = useState(null);
  const sourceRowRefs = useRef({});

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

  const loadSourceHealth = useCallback(async () => {
    try {
      const healthItems = await fetchSourceHealth();
      setHealthByFetcher(Object.fromEntries(healthItems.map(item => [item.fetcher_id, item])));
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => {
    loadSourceHealth();
  }, [loadSourceHealth]);

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


  const matchesSearch = useCallback((fetcher) => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return true;
    const company = resolveCompany(fetcher);
    return [
      fetcher.name, fetcher.id, fetcher.desc, fetcher.content_type,
      fetcher.base_url, fetcher.source_owner, fetcher.source_brand, fetcher.source_scope,
      fetcher.source_channel, fetcher.provenance_tier, company.name, company.en,
      ...(fetcher.content_tags || []),
    ].filter(Boolean).join(' ').toLowerCase().includes(query);
  }, [searchQuery]);

  const searchedFetchers = useMemo(() => availableFetchers.filter(matchesSearch), [availableFetchers, matchesSearch]);

  const sectionOf = useCallback((fetcher) => {
    const key = resolveCompany(fetcher).key;
    const section = SECTIONS.find(s => s.companies.includes(key));
    return section ? section.id : 'other';
  }, []);

  const sectionOptions = useMemo(() => {
    const pool = searchedFetchers.filter(f => tierFilter === 'all' || f.provenance_tier === tierFilter);
    const counts = {};
    pool.forEach(f => { const id = sectionOf(f); counts[id] = (counts[id] || 0) + 1; });
    const ordered = SECTIONS.filter(s => counts[s.id]).map(s => ({ id: s.id, label: s.label, count: counts[s.id] }));
    if (counts.other) ordered.push({ id: 'other', label: '其他来源', count: counts.other });
    return ordered;
  }, [searchedFetchers, tierFilter, sectionOf]);

  const tierCounts = useMemo(() => {
    const pool = searchedFetchers.filter(f => sectionFilter === 'all' || sectionOf(f) === sectionFilter);
    const counts = { all: pool.length };
    pool.forEach(f => { const t = f.provenance_tier || 'none'; counts[t] = (counts[t] || 0) + 1; });
    return counts;
  }, [searchedFetchers, sectionFilter, sectionOf]);

  const visibleTierFilterOptions = useMemo(() => TIER_FILTER_OPTIONS.filter(option => (
    option.value === 'all' || (tierCounts[option.value] || 0) > 0
  )), [tierCounts]);

  useEffect(() => {
    if (tierFilter !== 'all' && (tierCounts[tierFilter] || 0) === 0) {
      setTierFilter('all');
    }
  }, [tierCounts, tierFilter]);

  const visibleFetchers = useMemo(() => searchedFetchers
    .filter(f => sectionFilter === 'all' || sectionOf(f) === sectionFilter)
    .filter(f => tierFilter === 'all' || f.provenance_tier === tierFilter),
  [searchedFetchers, sectionFilter, tierFilter, sectionOf]);

  const groupedSections = useMemo(() => groupBySection(visibleFetchers), [visibleFetchers]);

  // 搜索 / 筛选时强制展开，便于聚焦命中结果；否则默认展开，用户可逐个收起。
  const autoExpand = searchQuery.trim().length > 0 || sectionFilter !== 'all' || tierFilter !== 'all';

  const isCatalogSectionOpen = useCallback((sectionId) => {
    if (autoExpand) return true;
    return !collapsedCatalogSections.has(sectionId);
  }, [autoExpand, collapsedCatalogSections]);

  const toggleCatalogSection = useCallback((sectionId, currentlyOpen) => {
    setCollapsedCatalogSections(prev => {
      const next = new Set(prev);
      if (currentlyOpen) next.add(sectionId);
      else next.delete(sectionId);
      return next;
    });
  }, []);

  const handleCatalogSectionKeyDown = useCallback((event, sectionId, currentlyOpen) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleCatalogSection(sectionId, currentlyOpen);
  }, [toggleCatalogSection]);

  const isCompanyOpen = useCallback((companyKey, fetchers) => {
    if (autoExpand) return true;
    if (fetchers.some(f => selectedFetchers.includes(f.id) || runningFetcherIds.has(f.id))) return true;
    return expandedCompanies.has(companyKey);
  }, [expandedCompanies, autoExpand, selectedFetchers, runningFetcherIds]);

  const toggleCompany = (companyKey, currentlyOpen) => {
    setExpandedCompanies(prev => {
      const next = new Set(prev);
      if (currentlyOpen) next.delete(companyKey);
      else next.add(companyKey);
      return next;
    });
  };

  // 接收来自知识台账「数据来源」列的定位请求：切到目录、清空筛选、展开对应板块/主体并高亮该节点行。
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
    const company = resolveCompany(fetcher);
    const sectionId = sectionOf(fetcher);
    setView('catalog');
    setSectionFilter('all');
    setTierFilter('all');
    setSearchQuery('');
    setCollapsedCatalogSections(prev => {
      if (!prev.has(sectionId)) return prev;
      const next = new Set(prev);
      next.delete(sectionId);
      return next;
    });
    setExpandedCompanies(prev => (prev.has(company.key) ? prev : new Set(prev).add(company.key)));
    setHighlightedFetcherId(sid);
  }, [pendingFocus, availableFetchers, fetchersById, sectionOf, onPendingFocusApplied, showToast, setView]);

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

  const toggleFetcherSelection = (id) => {
    setSelectedFetchers(prev => prev.includes(id) ? prev.filter(fid => fid !== id) : [...prev, id]);
  };

  const toggleCompanySelection = (fetchers) => {
    const ids = fetchers.map(f => f.id);
    const allSelected = ids.every(id => selectedFetchers.includes(id));
    setSelectedFetchers(prev => allSelected
      ? prev.filter(id => !ids.includes(id))
      : Array.from(new Set([...prev, ...ids])));
  };

  const updateFetcherConfig = (fetcherId, field, value) => {
    setFetchConfigs(prev => ({
      ...prev,
      [fetcherId]: { ...(prev[fetcherId] || {}), [field]: value },
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
        <select value={value} onChange={event => updateFetcherConfig(fetcherId, param.field, event.target.value)} className="node-param-input">
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

  const handleBatchFetch = async (options = {}) => {
    setFetchLoading(true);
    onRunsChanged?.();
    selectedFetchers.forEach(id => progressSeenFetcherIdsRef.current.delete(id));
    setRunningFetcherIds(prev => {
      const next = new Set(prev);
      selectedFetchers.forEach(id => next.add(id));
      return next;
    });
    const items = selectedFetchers.map(fetcherId => ({
      fetcher_id: fetcherId,
      params: fetchConfigs[fetcherId] || {},
    }));
    let result = null;
    try {
      result = await triggerBatchFetch(items, options);
    } catch (e) {
      showToast(e.message || '批量抓取失败', 'error');
    }
    setFetchLoading(false);
    setRunningFetcherIds(prev => {
      const next = new Set(prev);
      selectedFetchers.forEach(id => next.delete(id));
      return next;
    });
    if (result) {
      const successCount = selectedFetchers.length - (result.failed_count || 0);
      const suffix = options.testLimit ? `（每源 ${options.testLimit} 条）` : '';
      showToast(
        collectionRunMessage(`批量抓取完成${suffix}`, result, successCount),
        result.failed_count ? 'error' : 'success',
      );
      setSelectedFetchers([]);
      loadSourceHealth();
      onArticlesChanged?.();
      onRunsChanged?.();
    }
  };

  const runSingleFetcher = (fetcher) => {
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
  };

  const renderSourceRow = (fetcher) => {
    const isSelected = selectedFetchers.includes(fetcher.id);
    const health = healthByFetcher[fetcher.id];
    const paramCount = (fetcher.parameters || []).length;
    const paramsExpanded = expandedParamFetcherId === fetcher.id;
    const reviewExpanded = expandedReviewFetcherId === fetcher.id;
    const isRunning = runningFetcherIds.has(fetcher.id);
    const tier = tierMeta(fetcher.provenance_tier);
    const progress = fetchProgress[fetcher.id];
    const status = health?.health_status || 'never_run';
    const statusLabel = healthMeta(status).label;
    const company = resolveCompany(fetcher);
    const ownerLabel = fetcher.source_brand || fetcher.source_owner || company.name;
    const scopeLabel = labelFrom(SOURCE_SCOPE_LABELS, fetcher.source_scope);
    const channelLabel = labelFrom(SOURCE_CHANNEL_LABELS, fetcher.source_channel);
    const signalLabel = labelFrom(SIGNAL_LABELS, fetcher.signal_strength);
    const noiseLabel = labelFrom(NOISE_LABELS, fetcher.noise_risk);
    const reliabilityLabel = labelFrom(RELIABILITY_LABELS, fetcher.fetch_reliability);
    const contentTags = (fetcher.content_tags || []).slice(0, 5);
    const hasReview = Boolean(
      fetcher.signal_strength || fetcher.noise_risk || fetcher.fetch_reliability || contentTags.length
    );

    return (
      <div
        key={fetcher.id}
        ref={el => { sourceRowRefs.current[fetcher.id] = el; }}
        className={`source-row ${isSelected ? 'source-row-selected' : ''} ${highlightedFetcherId === fetcher.id ? 'source-row-focus' : ''}`}
      >
        <div className="source-row-head">
          <button
            type="button"
            onClick={() => toggleFetcherSelection(fetcher.id)}
            className={`source-check ${isSelected ? 'source-check-on' : ''}`}
            aria-label={isSelected ? `取消选择 ${fetcher.name}` : `选择 ${fetcher.name}`}
          >
            {isSelected && <CheckSquare className="h-3.5 w-3.5 text-white" />}
          </button>

          <div className="source-row-id" onClick={() => toggleFetcherSelection(fetcher.id)}>
            <div className="flex items-center gap-2 min-w-0">
              <span className="source-name truncate" title={fetcher.name}>{fetcher.name}</span>
              {fetcher.provenance_tier && <span className={`tier-pill tier-pill-wide ${tierPillClass(fetcher.provenance_tier)}`}>{tier.short} {tier.label}</span>}
            </div>
            <div className="source-sub">
              <span className="font-mono truncate" title={fetcher.id}>{fetcher.id}</span>
              {ownerLabel && <span className="source-sub-dot">{ownerLabel}</span>}
              {fetcher.source_scope && <span className="source-sub-dot">{scopeLabel}</span>}
              {fetcher.source_channel && <span className="source-sub-dot">{channelLabel}</span>}
            </div>
          </div>

          <div className="source-stats">
            <span className={`source-health-pill source-health-${status}`} title={`运行状态：${statusLabel}`}>{statusLabel}</span>
            <button
              type="button"
              onClick={() => onViewRuns?.(fetcher.id)}
              className="source-stat"
              title={`查看运行记录 · ${formatDateTime(health?.latest_run_at, '从未运行')}`}
            >
              <Activity className="h-3.5 w-3.5 text-slate-500" />
              <span>{formatRelativeTime(health?.latest_run_at)}</span>
            </button>
            <button
              type="button"
              onClick={() => onViewArticles?.(fetcher.id)}
              className="source-stat"
              title={`查看 ${fetcher.name} 抓取的文章`}
            >
              <FileText className="h-3.5 w-3.5 text-emerald-500" />
              <span className="font-bold text-emerald-700">{health?.total_articles ?? 0}</span>
            </button>
            {(health?.consecutive_failures ?? 0) > 0 && (
              <button
                type="button"
                onClick={() => onViewRuns?.(fetcher.id, { status: 'failed' })}
                className="source-stat source-stat-danger"
                title="查看失败运行记录"
              >
                ✕{health.consecutive_failures}
              </button>
            )}
          </div>

          <div className="source-actions">
            {hasReview && (
              <button
                type="button"
                onClick={() => setExpandedReviewFetcherId(reviewExpanded ? null : fetcher.id)}
                className={`source-config-btn ${reviewExpanded ? 'is-active' : ''}`}
                title="源审查字段与标签"
              >
                <Info className="h-4 w-4" />
                <span>详情</span>
              </button>
            )}
            <button
              type="button"
              disabled={paramCount === 0}
              onClick={() => setExpandedParamFetcherId(paramsExpanded ? null : fetcher.id)}
              className={`source-config-btn ${paramsExpanded ? 'is-active' : ''}`}
              title={paramCount === 0 ? '该节点无需抓取参数' : `抓取参数（${paramCount} 项）`}
            >
              <Settings2 className="h-4 w-4" />
              <span>配置</span>
              {paramCount > 0 && <span className="source-config-count">{paramCount}</span>}
            </button>
            <button
              type="button"
              disabled={isRunning}
              onClick={() => runSingleFetcher(fetcher)}
              className={`source-run ${isRunning ? 'is-running' : ''}`}
            >
              {isRunning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              <span>{isRunning ? (progress ? (progress.total ? `${progress.current}/${progress.total}` : `${progress.current}`) : '抓取中…') : '抓取'}</span>
            </button>
          </div>
        </div>

        {fetcher.desc && (
          <p className="source-desc">{fetcher.desc}</p>
        )}

        {fetcher.base_url && (
          <div className="source-url-inline">
            <ExternalLink className="h-3.5 w-3.5 shrink-0 text-slate-500" />
            <a className="source-url truncate" href={fetcher.base_url} target="_blank" rel="noreferrer" title="打开来源入口">{fetcher.base_url}</a>
          </div>
        )}

        {reviewExpanded && hasReview && (
          <div className="node-param-panel source-review-panel animate-in fade-in slide-in-from-top-1">
            <div className="node-param-title">
              <span>源审查字段</span>
            </div>
            <div className="source-review-grid">
              {fetcher.signal_strength && <div><span className="tiny-meta">信号</span><div className="source-meta-val">{signalLabel}</div></div>}
              {fetcher.noise_risk && <div><span className="tiny-meta">噪声</span><div className="source-meta-val">{noiseLabel}</div></div>}
              {fetcher.fetch_reliability && <div><span className="tiny-meta">稳定性</span><div className="source-meta-val">{reliabilityLabel}</div></div>}
            </div>
            {contentTags.length > 0 && (
              <div className="source-tag-row">
                <span className="tiny-meta">标签</span>
                <div className="source-tags">
                  {contentTags.map(tag => <span key={tag}>{tag}</span>)}
                </div>
              </div>
            )}
          </div>
        )}

        {paramsExpanded && paramCount > 0 && (
          <div className="node-param-panel source-review-panel animate-in fade-in slide-in-from-top-1">
            <div className="source-param-block-head">
              <span>抓取参数</span>
              <span>{paramCount} 项</span>
            </div>
            <div className="node-param-grid source-param-grid">
              {fetcher.parameters.map(param => (
                <div key={param.field} className="node-param-field">
                  <label className="node-param-label" title={param.field}>{param.label || param.field}</label>
                  {renderCatalogParamInput(fetcher.id, param)}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className={`space-y-6 animate-in fade-in ${selectedFetchers.length > 0 && view === 'catalog' ? 'pb-24' : ''}`}>
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">节点管理</h2>
          <p className="page-subtitle mt-3 max-w-4xl">
            {view === 'custom'
              ? '输入一个文章列表页 URL，自动分析并生成可抓取的自定义节点，无需写代码。'
              : '按主体聚合内置抓取节点，查看各来源的官方源、运行健康与最新产出。'}
          </p>
        </div>
        {ENABLE_CUSTOM_NODE_BUILDER && (
          <div className="page-actions">
            <div className="segmented-control">
              <button onClick={() => setView('catalog')} className={`segmented-option ${view === 'catalog' ? 'segmented-option-active' : ''}`}><Layers /> 节点目录</button>
              <button onClick={() => setView('custom')} className={`segmented-option ${view === 'custom' ? 'segmented-option-active' : ''}`}><Wand2 /> AI 自定义节点</button>
            </div>
          </div>
        )}
      </div>

      {ENABLE_CUSTOM_NODE_BUILDER && view === 'custom' && <CustomNodeBuilder showToast={showToast} />}

      {view === 'catalog' && (
        <>
          <div className="surface-card rounded-[var(--r-overlay)] overflow-hidden">
            <div className="catalog-topbar">
              <div className="section-title">
                <div className="flex h-10 w-10 items-center justify-center rounded-[var(--r-control)] bg-blue-50 text-blue-600">
                  <Layers className="h-5 w-5" />
                </div>
                <span>内置节点目录</span>
                <span className="text-xs font-mono text-slate-500">{visibleFetchers.length}/{availableFetchers.length}</span>
              </div>
            </div>
            <div className="catalog-filter-row catalog-filter-row-with-search">
              <span className="catalog-dimension-label">板块</span>
              <div className="catalog-chips">
                <button onClick={() => setSectionFilter('all')} className={`category-chip ${sectionFilter === 'all' ? 'category-chip-active' : ''}`}>
                  <span>全部</span>
                  <span className="category-chip-count">{searchedFetchers.filter(f => tierFilter === 'all' || f.provenance_tier === tierFilter).length}</span>
                </button>
                {sectionOptions.map(({ id, label, count }) => (
                  <button key={id} onClick={() => setSectionFilter(id)} className={`category-chip ${sectionFilter === id ? 'category-chip-active' : ''}`}>
                    <span>{label}</span>
                    <span className="category-chip-count">{count}</span>
                  </button>
                ))}
              </div>
              <div className="search-box catalog-search">
                <Search className="mr-2 h-4 w-4 text-slate-500" />
                <input value={searchQuery} onChange={event => setSearchQuery(event.target.value)} placeholder="搜索名称、主体、ID、标签、Base URL" className="py-2" />
              </div>
            </div>
            <div className="catalog-filter-row catalog-tier-row">
              <span className="catalog-dimension-label">层级</span>
              <div className="tier-segment">
                {visibleTierFilterOptions.map(option => {
                  const active = tierFilter === option.value;
                  const count = option.value === 'all' ? (tierCounts.all || 0) : (tierCounts[option.value] || 0);
                  return (
                    <button
                      key={option.value}
                      onClick={() => setTierFilter(option.value)}
                      className={`tier-segment-btn ${active ? 'tier-segment-btn-active' : ''}`}
                      disabled={option.value !== 'all' && count === 0}
                    >
                      {option.label}<span className="tier-segment-count">{count}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {groupedSections.length === 0 ? (
            <div className="surface-card rounded-[var(--r-overlay)] p-16 text-center text-slate-500 font-medium">当前筛选条件下没有匹配的节点</div>
          ) : (
            <div className="space-y-8">
              {groupedSections.map(section => {
                const sectionOpen = isCatalogSectionOpen(section.id);
                return (
                  <section key={section.id} className="space-y-4">
                    <div
                      role="button"
                      tabIndex={0}
                      className="section-band section-band-toggle"
                      style={{ '--section-accent': section.accent }}
                      onClick={() => toggleCatalogSection(section.id, sectionOpen)}
                      onKeyDown={event => handleCatalogSectionKeyDown(event, section.id, sectionOpen)}
                      aria-expanded={sectionOpen}
                    >
                      <div className="section-band-line" />
                      <div className="section-band-text">
                        <h3 className="section-band-title">{section.label}</h3>
                        <span className="section-band-en">{section.en}</span>
                      </div>
                      <span className="section-band-blurb">{section.blurb}</span>
                      <span className="section-band-count">{section.companies.reduce((sum, c) => sum + c.fetchers.length, 0)} 源 · {section.companies.length} 主体</span>
                      <span className="section-band-chevron">
                        {sectionOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </span>
                    </div>

                    {sectionOpen && (
                      <div className="dept-grid animate-in fade-in slide-in-from-top-1">
                        {section.companies.map(({ company, fetchers }) => {
                      const open = isCompanyOpen(company.key, fetchers);
                      const health = aggregateHealth(fetchers, healthByFetcher);
                      const allSelected = fetchers.every(f => selectedFetchers.includes(f.id));
                      const someSelected = fetchers.some(f => selectedFetchers.includes(f.id));
                      return (
                        <div key={company.key} className={`dept-card ${open ? 'dept-card-open' : ''}`} style={{ '--dept-accent': company.accent }}>
                          <div className="dept-spine" />
                          <div className="dept-head">
                            <button
                              type="button"
                              onClick={() => toggleCompanySelection(fetchers)}
                              className={`dept-check ${allSelected ? 'dept-check-on' : someSelected ? 'dept-check-some' : ''}`}
                              title={allSelected ? '取消选择本主体全部节点' : '选择本主体全部节点'}
                            >
                              {allSelected ? <CheckSquare className="h-3.5 w-3.5 text-white" /> : someSelected ? <span className="dept-check-dash" /> : null}
                            </button>
                            <LogoMark company={company} size="md" />
                            <button type="button" className="dept-headline" onClick={() => toggleCompany(company.key, open)}>
                              <div className="flex items-center gap-2 min-w-0">
                                <span className="dept-name truncate">{company.name}</span>
                                {company.en && company.en !== company.name && <span className="dept-alias truncate">{company.en}</span>}
                              </div>
                              <div className="dept-meta">
                                <span>{health.total} 源</span>
                                <span className="dept-meta-dot">{health.articles} 篇</span>
                                {health.failing > 0 && <span className="dept-meta-fail">{health.failing} 失败</span>}
                                <span className="dept-meta-dot">{health.latest ? formatRelativeTime(health.latest) : '未运行'}</span>
                              </div>
                            </button>
                            {!open && (
                              <div className="dept-dotgrid" title="各源运行健康">
                                {fetchers.slice(0, 16).map(f => {
                                  const st = healthByFetcher[f.id]?.health_status || 'never_run';
                                  const meta = healthMeta(st);
                                  return <span key={f.id} className={`dept-dotgrid-dot ${meta.dot}`} title={`${f.name}：${meta.label}`} />;
                                })}
                              </div>
                            )}
                            <button type="button" className="dept-chevron" onClick={() => toggleCompany(company.key, open)} aria-label={open ? '收起' : '展开'}>
                              {open ? <ChevronDown className="h-4.5 w-4.5" /> : <ChevronRight className="h-4.5 w-4.5" />}
                            </button>
                          </div>
                          {open && (
                            <div className="dept-body animate-in fade-in slide-in-from-top-1">
                              {fetchers.map(renderSourceRow)}
                            </div>
                          )}
                        </div>
                      );
                        })}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          )}
        </>
      )}

      {selectedFetchers.length > 0 && view === 'catalog' && (
        <div className="selection-bar animate-in slide-in-from-bottom-4">
          {runningFetcherIds.size > 0 ? (
            <RunningWidget
              variant="embedded"
              runningIds={runningFetcherIds}
              fetchProgress={fetchProgress}
              fetchersById={fetchersById}
              onViewRunning={onViewRunning}
            />
          ) : (
            <div className="selection-bar-info">
              <CheckSquare /> 已选择 {selectedFetchers.length} 个节点
            </div>
          )}
          <div className="selection-bar-actions">
            <button onClick={() => handleBatchFetch()} disabled={fetchLoading} className="action-button action-button-primary">
              {fetchLoading ? <RefreshCw className="animate-spin" /> : <Play className="fill-current" />} {fetchLoading ? '执行中...' : '立即临时抓取'}
            </button>
          </div>
        </div>
      )}

      {runningFetcherIds.size > 0 && (
        <RunningWidget
          variant="floating"
          runningIds={runningFetcherIds}
          fetchProgress={fetchProgress}
          fetchersById={fetchersById}
          onViewRunning={onViewRunning}
          hidden={selectedFetchers.length > 0 && view === 'catalog'}
        />
      )}
    </div>
  );
}
