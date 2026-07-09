import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { RefreshCw, CheckCircle, Zap, Search, Plus, Trash2, ChevronDown } from 'lucide-react';
import DateRangePicker from './DateRangePicker';
import ArticleDetailModal from './ArticleDetailModal';
import ArticleDetailDrawer from './ArticleDetailDrawer';
import ManualAddModal from './ManualAddModal';
import ActiveFilterBar from './ActiveFilterBar';
import { resolveCompany } from '../sourceTaxonomy';
import {
  fetchArticles as apiFetchArticles,
  fetchArticle,
  batchDeleteArticles,
  deleteArticle,
  vectorizeArticle,
  batchVectorizeArticles,
  vectorizeAllPending,
  reindexAll,
  getAutoVectorize,
  setAutoVectorize,
  updateArticle,
  createArticle,
} from '../api';
import { runAction } from '../utils/runAction';
import { excerptOf } from '../utils/readerText';
import { contentTypeLabel } from '../utils/contentType';
import { useConfirm } from '../hooks/useConfirm';
import { useAbortableLoad } from '../hooks/useAbortableLoad';

const ARTICLE_PAGE_SIZE = 30;

// 总账条：每格 = 一个 index_status，点击即按该状态筛选表格。
const STAT_DEFS = [
  { key: 'all', label: '总收录', tone: '' },
  { key: 'indexed', label: '已入索引', tone: 'is-ok' },
  { key: 'pending', label: '待处理', tone: '' },
  { key: 'indexing', label: '索引中', tone: 'is-run' },
  { key: 'failed', label: '失败', tone: 'is-bad' },
  { key: 'stale', label: '陈旧', tone: 'is-warn' },
];

// 收录时间快捷段（今天 / 近 7 天 / 近 30 天）→ fetched_date 区间。
// 用本地日期分量（与 DateRangePicker 同口径，避免 UTC 跨日偏移）。
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
function quickFetchedRange(days) {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - (days - 1));
  return { start: isoDay(start), end: isoDay(end) };
}

export default function DataTab({
  availableFetchers,
  showToast,
  isActive = true,
  canManageArticles = true,
  ragEnabled = false,
  articlesDirty = false,
  onArticlesRefreshed,
  pendingFilter,
  onPendingFilterApplied,
  onFocusSource,
}) {
  const confirm = useConfirm();
  // 列表 / 详情各自的竞态安全加载器（发新弃旧 + 卸载自动中止，见 useAbortableLoad）。
  const runList = useAbortableLoad();
  const runDetail = useAbortableLoad();
  // 记录上一次的 loadArticles 身份，用于区分「筛选/搜索变化」与「翻页」：前者要回到第 1 页。
  const loaderRef = useRef(null);
  const [articles, setArticles] = useState([]);
  const [articlePageInfo, setArticlePageInfo] = useState({ total: 0 });
  const [currentPage, setCurrentPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [selectedArticles, setSelectedArticles] = useState(new Set());
  const [modalState, setModalState] = useState({ isOpen: false, data: null, isEditing: false });
  const [detailLoading, setDetailLoading] = useState(false);
  const [manualAddModal, setManualAddModal] = useState(false);
  const [vectorizingId, setVectorizingId] = useState(null);
  const [vectorizingAll, setVectorizingAll] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [autoVec, setAutoVec] = useState(false);
  const [showMore, setShowMore] = useState(false);
  // 总账条计数（全局概览，不随分面筛选变化；见 loadLedgerStats）。
  const [ledgerStats, setLedgerStats] = useState(null);
  // 详情抽屉：查看 + 快捷操作（编辑仍走 ArticleDetailModal）。
  const [drawer, setDrawer] = useState({ open: false, article: null });
  // 整刷一次自增（loadArticles），作为 tbody 的 key：查询/筛选/分页一变即整体重挂载。
  const [listVersion, setListVersion] = useState(0);

  // 搜索是提交式：searchInput 为输入框即时值（不触发加载），appliedSearch 为已提交值。
  const [searchInput, setSearchInput] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [filters, setFilters] = useState({
    content_type: '',
    source_id: '',
    index_status: '',
    has_content: '', // '' 不限 | 'true' 仅有正文 | 'false' 仅无正文
    publish_date_start: '',
    publish_date_end: '',
    fetched_date_start: '',
    fetched_date_end: '',
    subscribed_scope: 'off', // off | prioritize | only（相对当前用户订阅的源）
  });

  const fetchersById = useMemo(
    () => Object.fromEntries(availableFetchers.map(f => [f.id, f])),
    [availableFetchers]
  );

  const getFetcherName = (id) => fetchersById[id]?.name || id;
  const companyFor = (sourceId) => {
    const fetcher = fetchersById[sourceId];
    if (fetcher) return resolveCompany(fetcher);
    // 阅读端运行时无 fetcher 元数据：用 source_id 兜底，保证标识仍可区分
    const sid = String(sourceId || 'unknown');
    const mono = sid.replace(/[^a-zA-Z0-9一-龥]/g, '').slice(0, 2).toUpperCase() || '··';
    return { key: `sid:${sid}`, name: sid, en: sid, accent: '#64748b', domain: '', monogram: mono };
  };

  // 类型分面选项必须稳定:若只取当前页,筛选后选项塌缩成「全部+已选」无法切换。
  // 用「历史所见类型的并集」(只增不减),切换筛选后其余选项仍在。
  const [uniqueContentTypes, setUniqueContentTypes] = useState([]);
  useEffect(() => {
    setUniqueContentTypes(prev => {
      const set = new Set(prev);
      let grew = false;
      articles.forEach(a => {
        if (a.content_type && !set.has(a.content_type)) { set.add(a.content_type); grew = true; }
      });
      return grew ? [...set].sort() : prev;
    });
  }, [articles]);
  const uniqueSourceIds = [...new Set([
    ...availableFetchers.map(f => f.id).filter(Boolean),
    ...articles.map(a => a.source_id).filter(Boolean),
    ...(filters.source_id ? [filters.source_id] : []),
  ])];

  // 数据来源分面：按公司分组，让来源更易定位
  const sourceKey = uniqueSourceIds.join('|');
  const sourceGroups = useMemo(() => {
    const groups = new Map();
    uniqueSourceIds.forEach(src => {
      const company = companyFor(src);
      if (!groups.has(company.key)) groups.set(company.key, { name: company.name, items: [] });
      groups.get(company.key).items.push(src);
    });
    return Array.from(groups.values()).sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, fetchersById]);

  const canSelectArticles = canManageArticles;
  // index_status 是归档事实,与 RAG 开关无关:状态格/状态列/计数对管理员始终可见;
  // 向量化「动作」(自动开关/全量/重建/单条构建)才依赖 RAG(向量子系统关闭时 503)。
  const showIndexBreakdown = canManageArticles;
  const showVectorActions = canManageArticles && ragEnabled;
  const totalPages = Math.max(1, Math.ceil((articlePageInfo.total || 0) / ARTICLE_PAGE_SIZE));
  const pageStart = articlePageInfo.total === 0 ? 0 : (currentPage - 1) * ARTICLE_PAGE_SIZE + 1;
  const pageEnd = Math.min(currentPage * ARTICLE_PAGE_SIZE, articlePageInfo.total || 0);
  const canGoPrev = currentPage > 1 && !loading;
  const canGoNext = currentPage < totalPages && !loading;
  const activeStatus = filters.index_status || 'all';

  // 收录时间快捷段的当前选中：由 filters 反推，保证与深链/清除一致。
  const activeFetchedQuick = useMemo(() => {
    const { fetched_date_start: s, fetched_date_end: e } = filters;
    if (!s && !e) return 'all';
    for (const days of [1, 7, 30]) {
      const r = quickFetchedRange(days);
      if (r.start === s && r.end === e) return String(days);
    }
    return 'custom';
  }, [filters]);

  // 列表加载：竞态由 runList 兜底（发新弃旧）。依赖 filters + appliedSearch。
  const loadArticles = useCallback(async (page = 1) => {
    setSelectedArticles(new Set());
    setLoading(true);
    const skip = (page - 1) * ARTICLE_PAGE_SIZE;
    // 知识台账只展示采集归档的原始内容；日报是 LLM 加工产物，从台账排除。
    const queryFilters = { ...filters, search: appliedSearch, exclude_source_ids: 'dorami_daily_brief' };
    let data;
    try {
      data = await runList((signal) =>
        apiFetchArticles(queryFilters, ARTICLE_PAGE_SIZE, skip, true, { signal, includeContent: false }));
    } catch (e) {
      showToast(e.message || '加载失败：后端未响应，请确认服务已启动后重试', 'error');
      setLoading(false);
      return;
    }
    if (data === undefined) return; // 被更新的请求取代
    const total = data.total || 0;
    const maxPage = Math.max(1, Math.ceil(total / ARTICLE_PAGE_SIZE));
    if (page > maxPage) {
      setArticlePageInfo({ total });
      setCurrentPage(maxPage);
      return;
    }
    setArticles(data.items || []);
    setArticlePageInfo({ total });
    setListVersion(v => v + 1);
    setLoading(false);
  }, [filters, appliedSearch, runList, showToast]);

  // 总账条计数：全局概览（仅排除日报源），与分面筛选无关。5-6 个轻请求，
  // 挂载时 + 文章增删/向量化后刷新。趋势 sparkline 本波不做（数据端点缺）。
  const loadLedgerStats = useCallback(async () => {
    const base = { exclude_source_ids: 'dorami_daily_brief' };
    const countFor = async (extra) => {
      try {
        const d = await apiFetchArticles({ ...base, ...extra }, 1, 0, true, { includeContent: false });
        return d.total ?? 0;
      } catch {
        return null;
      }
    };
    if (!showIndexBreakdown) {
      const total = await countFor({});
      setLedgerStats({ total });
      return;
    }
    const [total, indexed, pending, indexing, failed, stale] = await Promise.all([
      countFor({}),
      countFor({ index_status: 'indexed' }),
      countFor({ index_status: 'pending' }),
      countFor({ index_status: 'indexing' }),
      countFor({ index_status: 'failed' }),
      countFor({ index_status: 'stale' }),
    ]);
    setLedgerStats({ total, indexed, pending, indexing, failed, stale });
  }, [showIndexBreakdown]);

  const refreshAfterMutation = useCallback((page) => {
    loadArticles(page);
    loadLedgerStats();
  }, [loadArticles, loadLedgerStats]);

  const handleVectorize = async (id) => {
    setVectorizingId(id);
    await runAction(() => vectorizeArticle(id), {
      showToast,
      success: '已建立向量索引',
      onSuccess: () => {
        refreshAfterMutation(currentPage);
        setDrawer(prev => (prev.article?.id === id
          ? { ...prev, article: { ...prev.article, index_status: 'indexed', is_vectorized: true } }
          : prev));
      },
    });
    setVectorizingId(null);
  };

  const handleBatchVectorize = async () => {
    await runAction(() => batchVectorizeArticles(Array.from(selectedArticles)), {
      showToast,
      success: (data) => `已为 ${data.count} 条记录建立向量索引`,
      onSuccess: () => refreshAfterMutation(currentPage),
    });
  };

  const handleVectorizeAllPending = async () => {
    await runAction(() => vectorizeAllPending(), {
      showToast,
      success: (data) => `已向量化 ${data.count}/${data.total_pending} 篇待处理文章`,
      onSuccess: () => refreshAfterMutation(currentPage),
      setLoading: setVectorizingAll,
    });
  };

  const handleReindex = async () => {
    if (!(await confirm('全量重索引将清空并重建整个向量库（更换 Embedding 模型后使用）。确认继续？'))) return;
    await runAction(() => reindexAll(), {
      showToast,
      success: (data) => `已全量重索引 ${data.total_reindexed}/${data.total_articles} 篇`,
      onSuccess: () => refreshAfterMutation(currentPage),
      setLoading: setReindexing,
    });
  };

  const handleToggleAutoVec = async () => {
    const next = !autoVec;
    setAutoVec(next);
    try {
      await setAutoVectorize(next);
      showToast(next ? '已开启：抓取后自动向量化' : '已关闭自动向量化', 'success');
    } catch (error) {
      setAutoVec(!next);
      showToast(error.message || '设置失败，请重试', 'error');
    }
  };

  const refreshArticles = () => {
    if (currentPage === 1) loadArticles(1);
    else setCurrentPage(1);
    loadLedgerStats();
  };

  const handleSearchSubmit = () => setAppliedSearch(searchInput.trim());

  const setFetchedQuick = (key) => {
    if (key === 'all') {
      setFilters(prev => ({ ...prev, fetched_date_start: '', fetched_date_end: '' }));
      return;
    }
    const { start, end } = quickFetchedRange(Number(key));
    setFilters(prev => ({ ...prev, fetched_date_start: start, fetched_date_end: end }));
  };

  // 唯一列表加载驱动：区分「筛选/搜索变化」（回第 1 页）与「翻页」（加载该页）。
  useEffect(() => {
    const loaderChanged = loaderRef.current !== loadArticles;
    loaderRef.current = loadArticles;
    if (loaderChanged && currentPage !== 1) {
      setCurrentPage(1);
      return;
    }
    loadArticles(currentPage);
  }, [loadArticles, currentPage]);

  // 总账条计数：挂载 / rag·权限变化时加载一次。
  useEffect(() => { loadLedgerStats(); }, [loadLedgerStats]);

  // 自动向量化开关：读取当前配置（仅管理员 + RAG 开启）。
  useEffect(() => {
    if (!showIndexBreakdown) return;
    let alive = true;
    getAutoVectorize().then(d => { if (alive) setAutoVec(Boolean(d.enabled)); }).catch(() => {});
    return () => { alive = false; };
  }, [showIndexBreakdown]);

  useEffect(() => {
    if (isActive && articlesDirty) {
      refreshAfterMutation(currentPage);
      onArticlesRefreshed?.();
    }
  }, [isActive, articlesDirty, refreshAfterMutation, currentPage, onArticlesRefreshed]);

  useEffect(() => {
    if (!pendingFilter) return;
    setSearchInput('');
    setAppliedSearch('');
    setFilters(prev => ({
      ...prev,
      content_type: '',
      source_id: pendingFilter.source_id ?? prev.source_id,
      index_status: '',
      has_content: '',
      publish_date_start: '',
      publish_date_end: '',
      fetched_date_start: '',
      fetched_date_end: '',
    }));
    onPendingFilterApplied?.();
  }, [pendingFilter, onPendingFilterApplied]);

  const VECTOR_STATUS_LABELS = {
    indexed: '向量已构建', pending: '待索引', indexing: '构建中', failed: '构建失败', stale: '待重建',
  };
  const dateRangeText = (start, end) => `${start || '…'} ~ ${end || '…'}`;
  const activeFilterItems = [];
  if (appliedSearch) {
    activeFilterItems.push({
      key: 'search', label: '搜索', value: appliedSearch,
      onRemove: () => { setSearchInput(''); setAppliedSearch(''); },
    });
  }
  if (filters.source_id) {
    activeFilterItems.push({
      key: 'source', label: '数据来源', value: getFetcherName(filters.source_id),
      onRemove: () => setFilters(prev => ({ ...prev, source_id: '' })),
    });
  }
  if (filters.content_type) {
    activeFilterItems.push({
      key: 'type', label: '结构类型', value: contentTypeLabel(filters.content_type, filters.content_type),
      onRemove: () => setFilters(prev => ({ ...prev, content_type: '' })),
    });
  }
  if (filters.index_status) {
    activeFilterItems.push({
      key: 'vector', label: '向量状态', value: VECTOR_STATUS_LABELS[filters.index_status] || filters.index_status,
      onRemove: () => setFilters(prev => ({ ...prev, index_status: '' })),
    });
  }
  if (filters.has_content) {
    activeFilterItems.push({
      key: 'content', label: '正文', value: filters.has_content === 'true' ? '仅有正文' : '仅无正文（线索条目）',
      onRemove: () => setFilters(prev => ({ ...prev, has_content: '' })),
    });
  }
  if (filters.publish_date_start || filters.publish_date_end) {
    activeFilterItems.push({
      key: 'publish', label: '发布日期', value: dateRangeText(filters.publish_date_start, filters.publish_date_end),
      onRemove: () => setFilters(prev => ({ ...prev, publish_date_start: '', publish_date_end: '' })),
    });
  }
  if (filters.fetched_date_start || filters.fetched_date_end) {
    activeFilterItems.push({
      key: 'fetched', label: '收录时间', value: dateRangeText(filters.fetched_date_start, filters.fetched_date_end),
      onRemove: () => setFilters(prev => ({ ...prev, fetched_date_start: '', fetched_date_end: '' })),
    });
  }

  const clearAllFilters = () => {
    setSearchInput('');
    setAppliedSearch('');
    setFilters(prev => ({
      ...prev,
      content_type: '', source_id: '', index_status: '', has_content: '',
      publish_date_start: '', publish_date_end: '', fetched_date_start: '', fetched_date_end: '',
    }));
  };

  const toggleArticleSelection = (id) => {
    const newSet = new Set(selectedArticles);
    if (newSet.has(id)) newSet.delete(id);
    else newSet.add(id);
    setSelectedArticles(newSet);
  };

  const toggleAllArticles = () => {
    if (selectedArticles.size === articles.length && articles.length > 0) setSelectedArticles(new Set());
    else setSelectedArticles(new Set(articles.map(a => a.id)));
  };

  const handleBatchDeleteArticles = async () => {
    if (!(await confirm(`确定彻底删除选中的 ${selectedArticles.size} 条数据吗？`))) return;
    await runAction(() => batchDeleteArticles(Array.from(selectedArticles)), {
      showToast, success: `已删除 ${selectedArticles.size} 篇文章`, onSuccess: () => refreshAfterMutation(),
    });
  };

  const handleDeleteSingle = async (article) => {
    if (!(await confirm(`确定彻底删除「${article.title || article.id}」吗？`))) return;
    await runAction(() => deleteArticle(article.id), {
      showToast,
      success: '已删除文章',
      onSuccess: () => {
        setDrawer({ open: false, article: null });
        refreshAfterMutation(currentPage);
      },
    });
  };

  const handleUpdateArticle = async (id, updatedData) => {
    await runAction(() => updateArticle(id, updatedData), {
      showToast,
      success: '已保存修改',
      onSuccess: () => {
        setModalState({ isOpen: false, data: null, isEditing: false });
        setDrawer({ open: false, article: null });
        refreshAfterMutation(currentPage);
      },
    });
  };

  const handleManualAddSubmit = async (formData) => {
    const payload = {
      id: `manual_${Date.now()}`,
      title: formData.title,
      source_url: formData.source_url,
      publish_date: formData.publish_date || new Date().toISOString(),
      content_type: formData.content_type,
      source_id: formData.source_id,
      content: formData.content,
      extensions_json: formData.extensions_json || '{}',
    };
    await runAction(() => createArticle(payload), {
      showToast,
      success: '已录入文章',
      onSuccess: () => {
        setManualAddModal(false);
        refreshAfterMutation();
      },
    });
  };

  // 行点击 → 打开详情抽屉并拉取全文（竞态由 runDetail 兜底）。
  const openDrawer = async (article) => {
    setDrawer({ open: true, article });
    setDetailLoading(true);
    let detail;
    try {
      detail = await runDetail((signal) => fetchArticle(article.id, { signal }));
    } catch (e) {
      showToast(e.message || '获取文章详情失败', 'error');
      setDetailLoading(false);
      return;
    }
    if (detail === undefined) return; // 被更新的详情请求取代，丢弃
    setDrawer(prev => (
      prev.open && prev.article?.id === article.id
        ? { ...prev, article: { ...prev.article, ...detail } }
        : prev
    ));
    setDetailLoading(false);
  };

  const closeDrawer = () => {
    setDetailLoading(false);
    setDrawer({ open: false, article: null });
  };

  // 抽屉「编辑」→ 复用既有编辑模态（抽屉已载入全文，直接进编辑态）；
  // 编辑模态 z-index(90) 高于抽屉，收起抽屉避免叠层混乱。
  const openEditModal = (article) => {
    if (detailLoading) return;
    setDrawer({ open: false, article: null });
    setModalState({ isOpen: true, data: article, isEditing: true });
  };

  const closeEditModal = () => {
    setModalState({ isOpen: false, data: null, isEditing: false });
  };

  const renderStatValue = (key) => {
    if (!ledgerStats) return '…';
    const v = key === 'all' ? ledgerStats.total : ledgerStats[key];
    return v === null || v === undefined ? '—' : v.toLocaleString();
  };

  const coverage = ledgerStats && ledgerStats.total
    ? Math.round(((ledgerStats.indexed || 0) / ledgerStats.total) * 1000) / 10
    : null;

  return (
    <div className="ledger-shell">
      <div className="ledger-head">
        <h2 className="ledger-head-title">知识台账</h2>
        <div className="ledger-head-actions">
          {canManageArticles && (
            <button onClick={() => setManualAddModal(true)} className="action-button action-button-primary">
              <Plus /> 手工录入
            </button>
          )}
          <button onClick={refreshArticles} disabled={loading} className="action-button action-button-secondary">
            <RefreshCw className={loading ? 'animate-spin' : ''} /> 同步最新
          </button>
        </div>
      </div>

      <div className="ledger-work">
        {/* 分面栏：裸放画布 */}
        <aside className="ledger-facets" aria-label="筛选">
          <label className="ledger-searchbox">
            <Search className="ledger-searchbox-icon" />
            <input
              type="search"
              placeholder="标题 / 关键词检索…"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearchSubmit()}
              aria-label="检索标题关键词"
            />
          </label>


          <div className="ledger-facet">
            <h3 className="micro-label ledger-facet-title">内容类型</h3>
            <div className="ledger-facet-list">
              <button
                type="button"
                onClick={() => setFilters(prev => ({ ...prev, content_type: '' }))}
                className={`ledger-facet-item ${!filters.content_type ? 'is-on' : ''}`}
              >
                全部类型
              </button>
              {(() => {
                const labelCount = uniqueContentTypes.reduce((m, t) => {
                  const l = contentTypeLabel(t); m[l] = (m[l] || 0) + 1; return m;
                }, {});
                return uniqueContentTypes.map(t => {
                  const label = contentTypeLabel(t);
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setFilters(prev => ({ ...prev, content_type: t }))}
                      className={`ledger-facet-item ${filters.content_type === t ? 'is-on' : ''}`}
                      title={t}
                    >
                      {labelCount[label] > 1 ? `${label} · ${t}` : label}
                    </button>
                  );
                });
              })()}
            </div>
          </div>

          <div className="ledger-facet">
            <h3 className="micro-label ledger-facet-title">来源</h3>
            <div className="ledger-facet-list ledger-facet-scroll">
              <button
                type="button"
                onClick={() => setFilters(prev => ({ ...prev, source_id: '' }))}
                className={`ledger-facet-item ${!filters.source_id ? 'is-on' : ''}`}
              >
                全部节点
              </button>
              {sourceGroups.map(group => (
                <div key={group.name} className="ledger-facet-group">
                  <div className="ledger-facet-group-label">{group.name}</div>
                  {group.items.map(src => (
                    <button
                      key={src}
                      type="button"
                      onClick={() => setFilters(prev => ({ ...prev, source_id: src }))}
                      className={`ledger-facet-item ${filters.source_id === src ? 'is-on' : ''}`}
                      title={src}
                    >
                      {getFetcherName(src)}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>

          <div className="ledger-facet">
            <h3 className="micro-label ledger-facet-title">收录时间</h3>
            <div className="ledger-facet-list">
              {[
                { id: 'all', label: '全部时间' },
                { id: '1', label: '今天' },
                { id: '7', label: '近 7 天' },
                { id: '30', label: '近 30 天' },
              ].map(opt => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setFetchedQuick(opt.id)}
                  className={`ledger-facet-item ${activeFetchedQuick === opt.id ? 'is-on' : ''}`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="ledger-facet-range">
              <DateRangePicker
                startDate={filters.fetched_date_start}
                endDate={filters.fetched_date_end}
                onChange={(start, end) => setFilters({ ...filters, fetched_date_start: start, fetched_date_end: end })}
                placeholder="自定义收录区间"
              />
            </div>
          </div>

          <div className="ledger-facet">
            <h3 className="micro-label ledger-facet-title">正文</h3>
            <div className="ledger-facet-list">
              {[
                { id: '', label: '不限' },
                { id: 'true', label: '仅有正文' },
                { id: 'false', label: '仅无正文（线索条目）' },
              ].map(opt => (
                <button
                  key={opt.id || 'any'}
                  type="button"
                  onClick={() => setFilters(prev => ({ ...prev, has_content: opt.id }))}
                  className={`ledger-facet-item ${filters.has_content === opt.id ? 'is-on' : ''}`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div className="ledger-facet">
            <button
              type="button"
              onClick={() => setShowMore(v => !v)}
              className="ledger-facet-more"
              aria-expanded={showMore}
            >
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showMore ? 'rotate-180' : ''}`} />
              更多筛选
            </button>
            {showMore && (
              <div className="ledger-facet-range mt-2">
                <span className="micro-label mb-1 block text-[var(--dorami-faint)]">原始发布日期</span>
                <DateRangePicker
                  startDate={filters.publish_date_start}
                  endDate={filters.publish_date_end}
                  onChange={(start, end) => setFilters({ ...filters, publish_date_start: start, publish_date_end: end })}
                  placeholder="自定义发布区间"
                />
              </div>
            )}
          </div>
        </aside>

        {/* 主纸：总账条 + 工具栏 + 表格 + 批量条 + 表脚 */}
        <div className="ledger-paper surface-card">
          <div className="ledger-strip" role="group" aria-label="索引状态总览与筛选">
            <div className="ledger-strip-stats">
              {STAT_DEFS.filter(s => s.key === 'all' || showIndexBreakdown).map(stat => (
                <button
                  key={stat.key}
                  type="button"
                  onClick={() => setFilters(prev => ({ ...prev, index_status: stat.key === 'all' ? '' : stat.key }))}
                  className={`ledger-stat ${activeStatus === stat.key ? 'is-on' : ''}`}
                  aria-pressed={activeStatus === stat.key}
                >
                  <span className={`ledger-stat-num ${stat.tone}`}>{renderStatValue(stat.key)}</span>
                  <span className="ledger-stat-lbl">{stat.label}</span>
                  {stat.key === 'indexed' && coverage !== null && (
                    <>
                      <span className="ledger-stat-sub">覆盖率 {coverage}%</span>
                      <span className="ledger-coverbar"><i style={{ width: `${coverage}%` }} /></span>
                    </>
                  )}
                </button>
              ))}
            </div>
            {showVectorActions && (
              <div className="ledger-strip-actions">
                <button
                  type="button"
                  onClick={handleToggleAutoVec}
                  className="ledger-strip-toggle"
                  role="switch"
                  aria-checked={autoVec}
                >
                  <span className={`ledger-switch ${autoVec ? 'is-on' : ''}`} aria-hidden="true" />
                  随采自动向量化
                </button>
                <div className="ledger-strip-btns">
                  <button onClick={handleVectorizeAllPending} disabled={vectorizingAll} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
                    {vectorizingAll ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5 text-amber-500" />} 全量向量化
                  </button>
                  <button onClick={handleReindex} disabled={reindexing} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">
                    {reindexing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : null} 重建索引
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="ledger-toolbar">
            <span className="ledger-result">
              {ledgerStats && activeStatus === 'all' && !appliedSearch && activeFilterItems.length === 0
                ? `共 ${(ledgerStats.total ?? 0).toLocaleString()} 条`
                : `${articlePageInfo.total.toLocaleString()} 条 · 第 ${currentPage.toLocaleString()} / ${totalPages.toLocaleString()} 页`}
            </span>
            <ActiveFilterBar items={activeFilterItems} onClearAll={clearAllFilters} className="ledger-toolbar-filters" />
            <span className="flex-1" />
            <span className="micro-label text-[var(--dorami-faint)]">收录时间 ↓</span>
          </div>

          <div className="ledger-table-scroll">
            <table className="ledger-table w-full min-w-[980px] text-left">
              <colgroup>
                {canSelectArticles && <col className="ledger-col-select" />}
                <col className="ledger-col-title" />
                <col className="ledger-col-source" />
                <col className="ledger-col-type" />
                <col className="ledger-col-publish" />
                <col className="ledger-col-publish" />
                {showIndexBreakdown && <col className="ledger-col-vector" />}
                <col className="ledger-col-acts" />
              </colgroup>
              <thead>
                <tr>
                  {canSelectArticles && (
                    <th className="ledger-th px-4 text-center">
                      <input type="checkbox" aria-label="全选当前页记录" checked={selectedArticles.size === articles.length && articles.length > 0} onChange={toggleAllArticles} className="h-4 w-4 cursor-pointer rounded" />
                    </th>
                  )}
                  <th className="ledger-th px-4">条目</th>
                  <th className="ledger-th px-3">来源</th>
                  <th className="ledger-th px-3">类型</th>
                  <th className="ledger-th px-3 text-right">发布</th>
                  <th className="ledger-th px-3 text-right">收录</th>
                  {showIndexBreakdown && <th className="ledger-th px-3">索引状态</th>}
                  <th className="ledger-th px-3" />
                </tr>
              </thead>
              <tbody key={listVersion}>
                {loading && articles.length === 0 ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={`skeleton-${i}`} className="ledger-row">
                      {canSelectArticles && <td className="px-4"><div className="skeleton mx-auto h-4 w-4" /></td>}
                      <td className="px-4"><div className="skeleton h-4 w-3/4" /><div className="skeleton mt-2 h-3 w-1/2" /></td>
                      <td className="px-3"><div className="flex items-center gap-2.5"><div className="skeleton h-8 w-8 rounded-[var(--r-control)]" /><div className="skeleton h-4 w-20" /></div></td>
                      <td className="px-3"><div className="skeleton h-5 w-16 rounded-full" /></td>
                      <td className="px-3"><div className="skeleton ml-auto h-4 w-16" /></td>
                      <td className="px-3"><div className="skeleton ml-auto h-4 w-16" /></td>
                      {showIndexBreakdown && <td className="px-3"><div className="skeleton h-6 w-20 rounded-full" /></td>}
                      <td className="px-3" />
                    </tr>
                  ))
                ) : articles.length === 0 ? (
                  <tr><td colSpan={2 + (canSelectArticles ? 1 : 0) + (showIndexBreakdown ? 1 : 0) + 4} className="px-6 py-16 text-center font-medium text-slate-500">当前筛选条件下未查询到相关数据，试试放宽时间区间或清除筛选</td></tr>
                ) : articles.map((article) => {
                  const status = article.index_status || (article.is_vectorized ? 'indexed' : 'pending');
                  const busy = vectorizingId === article.id || status === 'indexing';
                  const isSel = drawer.open && drawer.article?.id === article.id;
                  return (
                    <tr
                      key={article.id}
                      className={`ledger-row ${isSel ? 'is-sel' : ''}`}
                      onClick={() => openDrawer(article)}
                      tabIndex={0}
                      onKeyDown={e => { if (e.key === 'Enter') openDrawer(article); }}
                    >
                      {canSelectArticles && (
                        <td className="px-4 text-center" onClick={e => e.stopPropagation()}>
                          <input type="checkbox" aria-label={`选择：${article.title || article.id}`} checked={selectedArticles.has(article.id)} onChange={() => toggleArticleSelection(article.id)} className="h-4 w-4 cursor-pointer rounded" />
                        </td>
                      )}
                      <td className="ledger-td-title px-4">
                        <div className="ledger-tt line-clamp-1">{article.title}</div>
                        <div className="ledger-ex line-clamp-1">{excerptOf(article.content_preview || article.content) || '暂无摘要内容'}</div>
                      </td>
                      <td className="px-3" onClick={e => e.stopPropagation()}>
                        {(() => {
                          const name = getFetcherName(article.source_id);
                          return (
                            <button
                              type="button"
                              disabled={!onFocusSource}
                              onClick={() => onFocusSource?.(article.source_id)}
                              className="ledger-source-link block min-w-0 max-w-full text-left"
                              title={onFocusSource ? `定位来源「${name}」（${article.source_id}）` : article.source_id}
                            >
                              <span className="ledger-source-name line-clamp-1 text-xs font-semibold text-slate-700">{name}</span>
                            </button>
                          );
                        })()}
                      </td>
                      <td className="px-3"><span className="ledger-type-chip max-w-full overflow-hidden text-ellipsis" title={article.content_type || ''}>{contentTypeLabel(article.content_type)}</span></td>
                      <td className="px-3 text-right"><span className="ledger-date">{article.publish_date?.split('T')[0] || '-'}</span></td>
                      <td className="px-3 text-right"><span className="ledger-date" title={`收录时间：${article.fetched_date?.replace('T', ' ').substring(0, 16) || '—'}`}>{article.fetched_date?.split('T')[0] || '-'}</span></td>
                      {showIndexBreakdown && (
                        <td className="px-3" onClick={e => e.stopPropagation()}>
                          {status === 'indexed' ? (
                            <span className="vector-status vector-status-done">
                              <CheckCircle className="vector-status-icon" strokeWidth={2.35} />
                              <span className="vector-status-label">向量已构建</span>
                            </span>
                          ) : !showVectorActions ? (
                            <span className={`vector-status vector-status-pending pointer-events-none${{ failed: ' vector-status-failed', stale: ' vector-status-stale' }[status] || ''}`}>
                              <Zap className="vector-status-icon" strokeWidth={2.35} />
                              <span className="vector-status-label">{VECTOR_STATUS_LABELS[status] || '待索引'}</span>
                            </span>
                          ) : (() => {
                            const restModifier = busy ? '' : { failed: ' vector-status-failed', stale: ' vector-status-stale' }[status] || '';
                            const restLabel = busy ? '构建中' : VECTOR_STATUS_LABELS[status] || '待索引';
                            const hoverLabel = status === 'failed' ? '重试构建' : (status === 'stale' ? '重建向量' : '构建向量');
                            return (
                              <button onClick={() => handleVectorize(article.id)} disabled={busy} className={`vector-status vector-status-pending${restModifier} group`}>
                                {busy ? <RefreshCw className="vector-status-icon animate-spin" strokeWidth={2.35} /> : <Zap className="vector-status-icon" strokeWidth={2.35} />}
                                <span className="vector-status-label vector-status-default">{restLabel}</span>
                                <span className="vector-status-label vector-status-hover">{busy ? '构建中' : hoverLabel}</span>
                              </button>
                            );
                          })()}
                        </td>
                      )}
                      <td className="px-3 text-right">
                        <span className="ledger-row-hint">查看 ›</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selectedArticles.size > 0 && canSelectArticles && (
            <div className="ledger-batchbar">
              <span className="ledger-batch-n">{selectedArticles.size} 条已选</span>
              {showIndexBreakdown && (
                <button onClick={handleBatchVectorize} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
                  <Zap className="h-3.5 w-3.5" /> 批量构建
                </button>
              )}
              <button onClick={handleBatchDeleteArticles} className="action-button action-button-danger min-h-[32px] px-3 text-xs">
                <Trash2 className="h-3.5 w-3.5" /> 批量删除
              </button>
              <span className="flex-1" />
              <button onClick={() => setSelectedArticles(new Set())} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消选择</button>
            </div>
          )}

          {articlePageInfo.total > 0 && (
            <div className="ledger-foot">
              <span className="ledger-foot-info">
                每页 {ARTICLE_PAGE_SIZE} 条，当前 {pageStart.toLocaleString()}-{pageEnd.toLocaleString()} 条
              </span>
              <div className="ledger-pager">
                <button type="button" onClick={() => setCurrentPage(1)} disabled={!canGoPrev} className="ledger-page-btn">首页</button>
                <button type="button" onClick={() => setCurrentPage(page => Math.max(1, page - 1))} disabled={!canGoPrev} className="ledger-page-btn">上一页</button>
                <span className="ledger-page-cur">第 {currentPage.toLocaleString()} / {totalPages.toLocaleString()} 页</span>
                <button type="button" onClick={() => setCurrentPage(page => Math.min(totalPages, page + 1))} disabled={!canGoNext} className="ledger-page-btn">下一页</button>
                <button type="button" onClick={() => setCurrentPage(totalPages)} disabled={!canGoNext} className="ledger-page-btn">末页</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <ArticleDetailDrawer
        open={drawer.open}
        article={drawer.article}
        loading={detailLoading}
        ragEnabled={showVectorActions}
        canManage={canManageArticles}
        getFetcherName={getFetcherName}
        vectorizing={vectorizingId === drawer.article?.id}
        onClose={closeDrawer}
        onVectorize={(a) => handleVectorize(a.id)}
        onEdit={openEditModal}
        onDelete={handleDeleteSingle}
      />

      <ArticleDetailModal
        isOpen={modalState.isOpen}
        data={modalState.data}
        isEditing={modalState.isEditing}
        isLoading={false}
        getFetcherName={getFetcherName}
        canEdit={canManageArticles}
        onClose={closeEditModal}
        onToggleEdit={() => setModalState(prev => ({ ...prev, isEditing: !prev.isEditing }))}
        onSave={handleUpdateArticle}
      />

      <ManualAddModal
        isOpen={manualAddModal}
        uniqueContentTypes={uniqueContentTypes}
        uniqueSourceIds={uniqueSourceIds}
        onClose={() => setManualAddModal(false)}
        onSubmit={handleManualAddSubmit}
      />
    </div>
  );
}
