import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  FolderPlus,
  LayoutGrid,
  Layers,
  Play,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Star,
  Target,
  Trash2,
  X,
} from 'lucide-react';
import {
  createNodeGroup,
  deleteNodeGroup,
  fetchNodeGroups,
  fetchRunningProgress,
  fetchSourceHealth,
  runNodeGroup,
  triggerFetch,
  updateNodeGroup,
} from '../api';
import LogoMark from './LogoMark';
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

const FAVORITE_FETCHERS_STORAGE_KEY = 'dorami.favorite_fetchers';
const TEST_RUN_LIMIT = 1;

const CATALOG_SCOPE_OPTIONS = [
  { value: 'focused', label: '聚焦', icon: Target },
  { value: 'favorites', label: '收藏', icon: Star },
  { value: 'hidden', label: '通用能力', icon: Settings2 },
  { value: 'all', label: '全部', icon: LayoutGrid },
];

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
  slate: 'bg-slate-50 text-slate-500 border-slate-200',
};

function tierPillClass(tier) {
  return TIER_TONE_CLASS[tierMeta(tier).tone] || TIER_TONE_CLASS.slate;
}

function formatDateTime(value) {
  if (!value) return '从未运行';
  return value.replace('T', ' ').substring(0, 19);
}

function formatRelativeTime(value) {
  if (!value) return '从未运行';
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return formatDateTime(value);
  const diffSec = Math.floor((Date.now() - ts) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} 天前`;
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  if (d.getFullYear() === new Date().getFullYear()) return `${mm}-${dd}`;
  return `${d.getFullYear()}-${mm}-${dd}`;
}

const HEALTH_DOT = {
  healthy: 'bg-emerald-500',
  failing: 'bg-red-500',
  running: 'bg-amber-400',
  never_run: 'bg-slate-300',
};

function healthMeta(status) {
  if (status === 'healthy') return { label: '健康', className: 'bg-emerald-50 text-emerald-700 border-emerald-100' };
  if (status === 'failing') return { label: '失败', className: 'bg-red-50 text-red-700 border-red-100' };
  if (status === 'running') return { label: '运行中', className: 'bg-amber-50 text-amber-700 border-amber-100' };
  if (status === 'never_run') return { label: '未运行', className: 'bg-slate-50 text-slate-500 border-slate-200' };
  return { label: '未知', className: 'bg-slate-50 text-slate-500 border-slate-200' };
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

export default function FetchTab({ availableFetchers, showToast, onArticlesChanged, onRunsChanged, onViewArticles, onViewRuns, onViewRunning }) {
  const [view, setView] = useState('catalog');
  const [fetchLoading, setFetchLoading] = useState(false);
  const [nodeGroups, setNodeGroups] = useState([]);
  const [healthByFetcher, setHealthByFetcher] = useState({});
  const [selectedFetchers, setSelectedFetchers] = useState([]);
  const [fetchConfigs, setFetchConfigs] = useState({});
  const [runningFetcherIds, setRunningFetcherIds] = useState(() => new Set());
  const [fetchProgress, setFetchProgress] = useState({});
  const [sectionFilter, setSectionFilter] = useState('all');
  const [tierFilter, setTierFilter] = useState('all');
  const [catalogScope, setCatalogScope] = useState('focused');
  const [favoriteFetcherIds, setFavoriteFetcherIds] = useState(() => {
    try {
      if (typeof localStorage === 'undefined') return [];
      return JSON.parse(localStorage.getItem(FAVORITE_FETCHERS_STORAGE_KEY) || '[]');
    } catch {
      return [];
    }
  });
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedCompanies, setExpandedCompanies] = useState(() => new Set());
  const [collapsedCatalogSections, setCollapsedCatalogSections] = useState(() => new Set());
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
    if (runningFetcherIds.size === 0) {
      setFetchProgress({});
      return undefined;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchRunningProgress();
        if (!cancelled) setFetchProgress(data || {});
      } catch { /* ignore transient polling errors */ }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [runningFetcherIds]);

  useEffect(() => {
    if (!groupModalOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [groupModalOpen]);

  useEffect(() => {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(FAVORITE_FETCHERS_STORAGE_KEY, JSON.stringify(favoriteFetcherIds));
  }, [favoriteFetcherIds]);

  const scopeCounts = useMemo(() => {
    const favorites = new Set(favoriteFetcherIds);
    return {
      focused: availableFetchers.filter(fetcher => fetcher.default_visible !== false).length,
      favorites: availableFetchers.filter(fetcher => favorites.has(fetcher.id)).length,
      hidden: availableFetchers.filter(fetcher => fetcher.default_visible === false).length,
      all: availableFetchers.length,
    };
  }, [availableFetchers, favoriteFetcherIds]);

  const scopedFetchers = useMemo(() => {
    const favorites = new Set(favoriteFetcherIds);
    if (catalogScope === 'favorites') return availableFetchers.filter(fetcher => favorites.has(fetcher.id));
    if (catalogScope === 'hidden') return availableFetchers.filter(fetcher => fetcher.default_visible === false);
    if (catalogScope === 'all') return availableFetchers;
    return availableFetchers.filter(fetcher => fetcher.default_visible !== false);
  }, [availableFetchers, catalogScope, favoriteFetcherIds]);

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

  const searchedFetchers = useMemo(() => scopedFetchers.filter(matchesSearch), [scopedFetchers, matchesSearch]);

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

  const setAllCatalogSections = useCallback((open) => {
    if (open) {
      setCollapsedCatalogSections(new Set());
      return;
    }
    setCollapsedCatalogSections(new Set(groupedSections.map(section => section.id)));
  }, [groupedSections]);

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

  const setAllCompanies = (open) => {
    if (!open) {
      setExpandedCompanies(new Set());
      return;
    }
    setExpandedCompanies(() => {
      const next = new Set();
      groupedSections.forEach(section => section.companies.forEach(({ company }) => next.add(company.key)));
      return next;
    });
  };

  const modalFetchers = useMemo(() => {
    const query = modalSearch.trim().toLowerCase();
    const favorites = new Set(favoriteFetcherIds);
    const baseFetchers = query
      ? availableFetchers
      : availableFetchers.filter(fetcher => fetcher.default_visible !== false || favorites.has(fetcher.id));
    return baseFetchers.filter(fetcher => [
      fetcher.name, fetcher.id, fetcher.desc, fetcher.base_url, fetcher.source_owner, fetcher.source_brand,
      ...(fetcher.content_tags || []),
    ].filter(Boolean).join(' ').toLowerCase().includes(query));
  }, [availableFetchers, favoriteFetcherIds, modalSearch]);

  const getFetcherName = (id) => fetchersById[id]?.name || id;

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

  const toggleFavorite = (id) => {
    setFavoriteFetcherIds(prev => prev.includes(id) ? prev.filter(fid => fid !== id) : [...prev, id]);
  };

  const updateModalNodeParam = (fetcherId, field, value) => {
    setGroupDraft(prev => ({
      ...prev,
      per_fetcher_params: {
        ...(prev.per_fetcher_params || {}),
        [fetcherId]: { ...((prev.per_fetcher_params || {})[fetcherId] || {}), [field]: value },
      },
    }));
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
      showToast('采集范围名称不能为空', 'error');
      return;
    }
    if ((groupDraft.fetcher_ids || []).length === 0) {
      showToast('采集范围至少需要一个节点', 'error');
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
      showToast('采集范围已保存', 'success');
    } catch (e) { showToast(e.message || '保存采集范围失败', 'error'); }
  };

  const handleDeleteGroup = async (id) => {
    if (!window.confirm('确定删除该采集范围？')) return;
    try {
      await deleteNodeGroup(id);
      if (expandedGroupId === id) setExpandedGroupId(null);
      await loadNodeGroups();
      showToast('采集范围已删除', 'success');
    } catch (e) { showToast(e.message || '删除采集范围失败', 'error'); }
  };

  const handleRunGroup = async (id, options = {}) => {
    onRunsChanged?.();
    try {
      const result = await runNodeGroup(id, options);
      const prefix = options.testLimit ? `测试运行完成（每源 ${options.testLimit} 条）` : '采集范围运行完成';
      showToast(`${prefix}：新增 ${result.saved_count || 0} 条`, result.failed_count ? 'info' : 'success');
      loadSourceHealth();
      onArticlesChanged?.();
      onRunsChanged?.();
    } catch (e) { showToast(e.message || '采集范围运行失败', 'error'); }
  };

  const handleBatchFetch = async (options = {}) => {
    setFetchLoading(true);
    onRunsChanged?.();
    setRunningFetcherIds(prev => {
      const next = new Set(prev);
      selectedFetchers.forEach(id => next.add(id));
      return next;
    });
    let successCount = 0;
    for (const fetcherId of selectedFetchers) {
      try {
        await triggerFetch(fetcherId, fetchConfigs[fetcherId] || {}, options);
        successCount++;
      } catch (e) { showToast(e.message || `[${getFetcherName(fetcherId)}] 抓取失败`, 'error'); }
      setRunningFetcherIds(prev => {
        const next = new Set(prev);
        next.delete(fetcherId);
        return next;
      });
    }
    setFetchLoading(false);
    if (successCount > 0) {
      const suffix = options.testLimit ? `（每源 ${options.testLimit} 条）` : '';
      showToast(`已触发 ${successCount} 个节点${suffix}`, 'success');
      setSelectedFetchers([]);
      loadSourceHealth();
      onArticlesChanged?.();
      onRunsChanged?.();
    }
  };

  const runSingleFetcher = (fetcher) => {
    if (runningFetcherIds.has(fetcher.id)) return;
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

  const renderSourceRow = (fetcher) => {
    const isSelected = selectedFetchers.includes(fetcher.id);
    const health = healthByFetcher[fetcher.id];
    const paramCount = (fetcher.parameters || []).length;
    const paramsExpanded = expandedParamFetcherId === fetcher.id;
    const isFavorite = favoriteFetcherIds.includes(fetcher.id);
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

    return (
      <div key={fetcher.id} className={`source-row ${isSelected ? 'source-row-selected' : ''}`}>
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
              title={`查看运行记录 · ${formatDateTime(health?.latest_run_at)}`}
            >
              <Activity className="h-3.5 w-3.5 text-slate-400" />
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
            <button
              type="button"
              onClick={() => toggleFavorite(fetcher.id)}
              className={`source-icon-btn ${isFavorite ? 'is-fav' : ''}`}
              title={isFavorite ? '取消收藏' : '收藏节点'}
            >
              <Star className={`h-4 w-4 ${isFavorite ? 'fill-current' : ''}`} />
            </button>
            <button
              type="button"
              onClick={() => setExpandedParamFetcherId(paramsExpanded ? null : fetcher.id)}
              className={`source-config-btn ${paramsExpanded ? 'is-active' : ''}`}
              title={`参数配置（${paramCount} 项）`}
            >
              <Settings2 className="h-4 w-4" />
              <span>配置</span>
              <span className="source-config-count">{paramCount}</span>
            </button>
            <button
              type="button"
              disabled={isRunning}
              onClick={() => runSingleFetcher(fetcher)}
              className={`source-run ${isRunning ? 'is-running' : ''}`}
            >
              {isRunning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              <span>{isRunning ? (progress ? (progress.total ? `${progress.current}/${progress.total}` : `${progress.current}`) : '抓取中') : '抓取'}</span>
            </button>
          </div>
        </div>

        {fetcher.desc && (
          <p className="source-desc">{fetcher.desc}</p>
        )}

        {paramsExpanded && (
          <div className="node-param-panel source-review-panel animate-in fade-in slide-in-from-top-1">
            <div className="node-param-title">
              <span>源审查字段</span>
              <span className="font-mono text-slate-400">配置 {paramCount} 项</span>
            </div>
            <div className="source-review-grid">
              <div><span className="tiny-meta">主体 / 承载</span><div className="source-meta-val">{ownerLabel}</div></div>
              {fetcher.source_scope && <div><span className="tiny-meta">范围</span><div className="source-meta-val">{scopeLabel}</div></div>}
              {fetcher.source_channel && <div><span className="tiny-meta">渠道</span><div className="source-meta-val">{channelLabel}</div></div>}
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
            {fetcher.base_url && (
              <div className="source-url-row">
                <span className="tiny-meta">来源入口</span>
                <a className="source-url truncate" href={fetcher.base_url} target="_blank" rel="noreferrer" title={fetcher.base_url}>{fetcher.base_url}</a>
                <a className="source-url-open" href={fetcher.base_url} target="_blank" rel="noreferrer" title="打开来源入口">
                  <ExternalLink className="h-3.5 w-3.5" />
                  打开
                </a>
              </div>
            )}
            {paramCount === 0 ? (
              <div className="source-param-empty">该节点无需额外抓取参数</div>
            ) : (
              <div className="source-param-block">
                <div className="source-param-block-head">
                  <span>抓取参数</span>
                  <span>{paramCount} 项</span>
                </div>
                <div className="node-param-grid">
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
        )}
      </div>
    );
  };

  return (
    <div className={`space-y-6 animate-in fade-in ${selectedFetchers.length > 0 && view === 'catalog' ? 'pb-24' : ''}`}>
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">节点管理</h2>
          <p className="page-subtitle mt-3 max-w-4xl">按主体聚合内置抓取节点，一站式查看各来源的官方源、运行健康与最新产出，并维护可复用的采集范围。</p>
        </div>
        <div className="page-actions">
          <div className="segmented-control">
            <button onClick={() => setView('catalog')} className={`segmented-option ${view === 'catalog' ? 'segmented-option-active' : ''}`}><Layers /> 节点目录</button>
            <button onClick={() => setView('groups')} className={`segmented-option ${view === 'groups' ? 'segmented-option-active' : ''}`}><FolderPlus /> 采集范围</button>
          </div>
        </div>
      </div>

      {view === 'catalog' && (
        <>
          <div className="surface-card rounded-[16px] overflow-hidden">
            <div className="catalog-topbar">
              <div className="section-title">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                  <Layers className="h-5 w-5" />
                </div>
                <span>内置节点目录</span>
                <span className="text-xs font-mono text-slate-400">{visibleFetchers.length}/{scopeCounts[catalogScope]}</span>
              </div>
              <div className="flex items-center gap-2">
                {!autoExpand && (
                  <div className="catalog-expand-tools">
                    <div className="dept-expand-toggle">
                      <span>板块</span>
                      <button type="button" onClick={() => setAllCatalogSections(true)}>展开</button>
                      <span>·</span>
                      <button type="button" onClick={() => setAllCatalogSections(false)}>收起</button>
                    </div>
                    <div className="dept-expand-toggle">
                      <span>主体</span>
                      <button type="button" onClick={() => setAllCompanies(true)}>展开</button>
                      <span>·</span>
                      <button type="button" onClick={() => setAllCompanies(false)}>收起</button>
                    </div>
                  </div>
                )}
                <div className="catalog-scope-tabs" role="tablist" aria-label="节点视图范围">
                  {CATALOG_SCOPE_OPTIONS.map(option => {
                    const Icon = option.icon;
                    const active = catalogScope === option.value;
                    return (
                      <button
                        key={option.value}
                        role="tab"
                        aria-selected={active}
                        onClick={() => { setCatalogScope(option.value); setSectionFilter('all'); setTierFilter('all'); }}
                        className={`catalog-scope-tab ${active ? 'catalog-scope-tab-active' : ''}`}
                      >
                        {Icon && <Icon />}
                        <span>{option.label}</span>
                        <span className="catalog-scope-tab-count">{scopeCounts[option.value] || 0}</span>
                      </button>
                    );
                  })}
                </div>
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
                <Search className="mr-2 h-4 w-4 text-slate-400" />
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
            <div className="surface-card rounded-[16px] p-16 text-center text-slate-400 font-medium">当前筛选条件下没有匹配的节点</div>
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
                                <span className={`dept-dot ${HEALTH_DOT[health.worst] || HEALTH_DOT.never_run}`} title={healthMeta(health.worst).label} />
                              </div>
                              <div className="dept-meta">
                                <span>{health.total} 源</span>
                                <span className="dept-meta-dot">{health.articles} 篇</span>
                                {health.failing > 0 && <span className="dept-meta-fail">{health.failing} 失败</span>}
                                <span className="dept-meta-dot">{health.latest ? formatRelativeTime(health.latest) : '未运行'}</span>
                              </div>
                            </button>
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

      {view === 'groups' && (
        <div className="surface-card rounded-[16px] overflow-hidden">
          <div className="panel-header">
            <div className="section-title">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                <FolderPlus className="h-5 w-5" />
              </div>
              <span>采集范围</span>
              <span className="text-xs font-mono text-slate-400">{nodeGroups.length}</span>
            </div>
            <button onClick={() => openCreateGroup([])} className="action-button action-button-primary min-h-[36px] px-3 text-xs">
              <FolderPlus /> 新建采集范围
            </button>
          </div>
          <div className="divide-y divide-slate-100">
            {nodeGroups.length === 0 ? (
              <div className="p-12 text-center text-slate-400 font-medium">暂无采集范围</div>
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
                    <div className="hidden sm:flex items-center -space-x-1.5">
                      {(group.fetcher_ids || []).slice(0, 5).map(id => {
                        const f = fetchersById[id];
                        return <LogoMark key={id} company={resolveCompany(f || {})} size="xs" className="ring-2 ring-white" />;
                      })}
                    </div>
                  </button>
                  {isExpanded && (
                    <div className="px-5 pb-5 ml-6 space-y-4">
                      <div className="flex flex-wrap gap-2">
                        <button onClick={() => openEditGroup(group)} className="action-button action-button-quiet min-h-[34px] px-3 text-xs">编辑</button>
                        <button onClick={() => handleRunGroup(group.id, { testLimit: TEST_RUN_LIMIT })} className="action-button action-button-quiet min-h-[34px] px-3 text-xs"><Play /> 测试运行 1 条/源</button>
                        <button onClick={() => handleRunGroup(group.id)} className="action-button action-button-primary min-h-[34px] px-3 text-xs"><Play /> 临时运行</button>
                        <button onClick={() => handleDeleteGroup(group.id)} className="action-button action-button-danger min-h-[34px] px-3 text-xs"><Trash2 /> 删除</button>
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {(group.fetcher_ids || []).map(fetcherId => {
                          const f = fetchersById[fetcherId];
                          return (
                            <div key={fetcherId} className="flex items-center gap-3 border border-slate-200 rounded-lg p-3 bg-slate-50">
                              <LogoMark company={resolveCompany(f || {})} size="sm" />
                              <div className="min-w-0 flex-1">
                                <div className="font-bold text-slate-700 text-sm truncate">{getFetcherName(fetcherId)}</div>
                                <div className="font-mono text-[11px] text-slate-400 truncate">{fetcherId}</div>
                              </div>
                              <code className="hidden md:block text-[11px] text-slate-500 bg-white border border-slate-100 rounded px-2 py-1 max-w-[180px] truncate" title={JSON.stringify((group.per_fetcher_params || {})[fetcherId] || {})}>
                                {JSON.stringify((group.per_fetcher_params || {})[fetcherId] || {})}
                              </code>
                            </div>
                          );
                        })}
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
          {runningFetcherIds.size > 0 ? (
            <button type="button" onClick={() => onViewRunning?.()} className="running-widget running-widget-embedded" title="查看运行历史">
              <RefreshCw className="running-widget-icon animate-spin" />
              <div className="running-widget-body">
                <div className="running-widget-headline">
                  <span>{runningFetcherIds.size} 个节点正在抓取</span>
                  <ChevronRight className="running-widget-chevron" />
                </div>
                <div className="running-widget-list">
                  {Array.from(runningFetcherIds).slice(0, 4).map(id => {
                    const p = fetchProgress[id];
                    const name = fetchersById[id]?.name || id;
                    const isQueued = !p;
                    const progressText = isQueued ? '排队中' : (p.total ? `${p.current}/${p.total}` : `${p.current}`);
                    return (
                      <div key={id} className="running-widget-row">
                        <span className="running-widget-name">{name}</span>
                        <span className={`running-widget-progress ${isQueued ? 'running-widget-progress-queued' : ''}`}>{progressText}</span>
                      </div>
                    );
                  })}
                  {runningFetcherIds.size > 4 && <div className="running-widget-more">+{runningFetcherIds.size - 4} 个</div>}
                </div>
              </div>
            </button>
          ) : (
            <div className="selection-bar-info">
              <CheckSquare /> 已选择 {selectedFetchers.length} 个节点
            </div>
          )}
          <div className="selection-bar-actions">
            <button onClick={() => openCreateGroup(selectedFetchers)} className="action-button action-button-secondary text-indigo-700">
              <FolderPlus /> 保存为采集范围
            </button>
            <button onClick={() => handleBatchFetch()} disabled={fetchLoading} className="action-button action-button-primary">
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
                <h3 className="card-title">{editingGroupId ? '编辑采集范围' : '新建采集范围'}</h3>
                <p className="text-xs text-slate-400 mt-1">采集范围只维护节点集合和参数模板，可被采集任务复用。</p>
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
                          <LogoMark company={resolveCompany(fetcher)} size="sm" />
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
                          <div className="flex items-center gap-3 min-w-0">
                            <LogoMark company={resolveCompany(fetcher || {})} size="sm" />
                            <div className="min-w-0">
                              <div className="card-title truncate">{fetcher?.name || fetcherId}</div>
                              <div className="font-mono text-[11px] text-slate-400 mt-0.5">{fetcherId}</div>
                            </div>
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

      {runningFetcherIds.size > 0 && (
        <button
          type="button"
          onClick={() => onViewRunning?.()}
          className={`running-widget ${selectedFetchers.length > 0 && view === 'catalog' ? 'running-widget-hidden' : ''}`}
          title="查看运行历史"
          aria-hidden={selectedFetchers.length > 0 && view === 'catalog'}
        >
          <RefreshCw className="running-widget-icon animate-spin" />
          <div className="running-widget-body">
            <div className="running-widget-headline">
              <span>{runningFetcherIds.size} 个节点正在抓取</span>
              <ChevronRight className="running-widget-chevron" />
            </div>
            <div className="running-widget-list">
              {Array.from(runningFetcherIds).slice(0, 4).map(id => {
                const p = fetchProgress[id];
                const name = fetchersById[id]?.name || id;
                const isQueued = !p;
                const progressText = isQueued ? '排队中' : (p.total ? `${p.current}/${p.total}` : `${p.current}`);
                return (
                  <div key={id} className="running-widget-row">
                    <span className="running-widget-name">{name}</span>
                    <span className={`running-widget-progress ${isQueued ? 'running-widget-progress-queued' : ''}`}>{progressText}</span>
                  </div>
                );
              })}
              {runningFetcherIds.size > 4 && <div className="running-widget-more">+{runningFetcherIds.size - 4} 个</div>}
            </div>
          </div>
        </button>
      )}
    </div>
  );
}
