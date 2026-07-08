import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { RefreshCw, CheckCircle, Zap, Search, Plus, Trash2, SlidersHorizontal } from 'lucide-react';
import DateRangePicker from './DateRangePicker';
import ArticleDetailModal from './ArticleDetailModal';
import ManualAddModal from './ManualAddModal';
import ActiveFilterBar from './ActiveFilterBar';
import LogoMark from './LogoMark';
import { resolveCompany } from '../sourceTaxonomy';
import {
  fetchArticles as apiFetchArticles,
  fetchArticle,
  batchDeleteArticles,
  vectorizeArticle,
  batchVectorizeArticles,
  vectorizeAllPending,
  updateArticle,
  createArticle,
} from '../api';
import { runAction } from '../utils/runAction';
import { excerptOf } from '../utils/readerText';
import { contentTypeLabel } from '../utils/contentType';
import { useConfirm } from '../hooks/useConfirm';
import { useAbortableLoad } from '../hooks/useAbortableLoad';

const ARTICLE_PAGE_SIZE = 30;

export default function DataTab({
  availableFetchers,
  showToast,
  isActive = true,
  canManageArticles = true,
  isReader = true,
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
  const [showAdvanced, setShowAdvanced] = useState(false);
  // 整刷一次自增（loadArticles），作为 tbody 的 key：查询/筛选/分页一变即整体重挂载，
  // 让行入场动画对每次切换都触发。
  const [listVersion, setListVersion] = useState(0);

  // 搜索是提交式：searchInput 为输入框即时值（不触发加载），appliedSearch 为已提交值
  // （回车/清除时更新，进入 loadArticles 依赖 → 触发一次加载并回到第 1 页）。
  const [searchInput, setSearchInput] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [filters, setFilters] = useState({
    content_type: '',
    source_id: '',
    index_status: '',
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

  const uniqueContentTypes = [...new Set(articles.map(a => a.content_type).filter(Boolean))];
  const uniqueSourceIds = [...new Set([
    ...availableFetchers.map(f => f.id).filter(Boolean),
    ...articles.map(a => a.source_id).filter(Boolean),
    ...(filters.source_id ? [filters.source_id] : []),
  ])];

  // 数据来源下拉：按公司分组（optgroup），让来源更易定位
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

  const advancedCount = [
    filters.content_type,
    filters.index_status,
    filters.publish_date_start || filters.publish_date_end,
    filters.fetched_date_start || filters.fetched_date_end,
  ].filter(Boolean).length;

  const canSelectArticles = canManageArticles;
  const totalPages = Math.max(1, Math.ceil((articlePageInfo.total || 0) / ARTICLE_PAGE_SIZE));
  const pageStart = articlePageInfo.total === 0 ? 0 : (currentPage - 1) * ARTICLE_PAGE_SIZE + 1;
  const pageEnd = Math.min(currentPage * ARTICLE_PAGE_SIZE, articlePageInfo.total || 0);
  const canGoPrev = currentPage > 1 && !loading;
  const canGoNext = currentPage < totalPages && !loading;

  // 列表加载：竞态由 runList 兜底（发新弃旧）。依赖 filters + appliedSearch —— 二者一变
  // loadArticles 身份即变，驱动 effect 重载并回到第 1 页；搜索输入（searchInput）不在此列，
  // 故打字不触发加载（提交式搜索）。
  const loadArticles = useCallback(async (page = 1) => {
    setSelectedArticles(new Set());
    setLoading(true);
    const skip = (page - 1) * ARTICLE_PAGE_SIZE;
    // 知识台账只展示采集归档的原始内容；日报是 LLM 加工产物，从台账排除
    // （阅读器订阅侧不带此参数，用户订阅日报后仍可正常查看）。
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
    if (data === undefined) return; // 被更新的请求取代：loading 归新请求所有，不在此清除
    const total = data.total || 0;
    const maxPage = Math.max(1, Math.ceil(total / ARTICLE_PAGE_SIZE));
    if (page > maxPage) {
      // 越界页：修正 currentPage 触发再次加载（loading 保持，直到修正后的加载完成）
      setArticlePageInfo({ total });
      setCurrentPage(maxPage);
      return;
    }
    setArticles(data.items || []);
    setArticlePageInfo({ total });
    setListVersion(v => v + 1);
    setLoading(false);
  }, [filters, appliedSearch, runList, showToast]);

  const handleVectorize = async (id) => {
    setVectorizingId(id);
    await runAction(() => vectorizeArticle(id), {
      showToast, success: '已建立向量索引', onSuccess: () => loadArticles(currentPage),
    });
    setVectorizingId(null);
  };

  const handleBatchVectorize = async () => {
    await runAction(() => batchVectorizeArticles(Array.from(selectedArticles)), {
      showToast,
      success: (data) => `已为 ${data.count} 条记录建立向量索引`,
      onSuccess: () => loadArticles(currentPage),
    });
  };

  const handleVectorizeAllPending = async () => {
    await runAction(() => vectorizeAllPending(), {
      showToast,
      success: (data) => `已向量化 ${data.count}/${data.total_pending} 篇待处理文章`,
      onSuccess: () => loadArticles(currentPage),
      setLoading: setVectorizingAll,
    });
  };

  const refreshArticles = () => {
    if (currentPage === 1) loadArticles(1);
    else setCurrentPage(1);
  };

  // 回车提交搜索：把输入值提交为 appliedSearch → loadArticles 变身 → 加载并回到第 1 页。
  const handleSearchSubmit = () => setAppliedSearch(searchInput.trim());

  // 唯一列表加载驱动：区分「筛选/搜索变化」（loadArticles 身份变，回第 1 页）与「翻页」
  // （仅 currentPage 变，加载该页），取代旧的 activeFilterKey 串 + searchReloadTick 补丁。
  useEffect(() => {
    const loaderChanged = loaderRef.current !== loadArticles;
    loaderRef.current = loadArticles;
    if (loaderChanged && currentPage !== 1) {
      setCurrentPage(1); // 会再次触发本 effect，届时以第 1 页加载
      return;
    }
    loadArticles(currentPage);
  }, [loadArticles, currentPage]);

  useEffect(() => {
    if (isActive && articlesDirty) {
      loadArticles(currentPage);
      onArticlesRefreshed?.();
    }
  }, [isActive, articlesDirty, loadArticles, currentPage, onArticlesRefreshed]);

  useEffect(() => {
    if (!pendingFilter) return;
    setSearchInput('');
    setAppliedSearch('');
    setFilters(prev => ({
      ...prev,
      content_type: '',
      source_id: pendingFilter.source_id ?? prev.source_id,
      index_status: '',
      publish_date_start: '',
      publish_date_end: '',
      fetched_date_start: '',
      fetched_date_end: '',
    }));
    onPendingFilterApplied?.();
  }, [pendingFilter, onPendingFilterApplied]);

  // 当前生效筛选（用于「当前筛选」条的可移除胶囊）。键控筛选清除后由 activeFilterKey effect 自动重载；
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
      content_type: '', source_id: '', index_status: '',
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
      showToast, success: `已删除 ${selectedArticles.size} 篇文章`, onSuccess: () => loadArticles(),
    });
  };

  const handleUpdateArticle = async (id, updatedData) => {
    await runAction(() => updateArticle(id, updatedData), {
      showToast,
      success: '已保存修改',
      onSuccess: () => {
        setModalState({ isOpen: false, data: null, isEditing: false });
        loadArticles();
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
        loadArticles();
      },
    });
  };

  const openDetailModal = async (article) => {
    setModalState({ isOpen: true, data: article, isEditing: false });
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
    setModalState(prev => (
      prev.isOpen && prev.data?.id === article.id
        ? { ...prev, data: { ...prev.data, ...detail } }
        : prev
    ));
    setDetailLoading(false);
  };

  const closeDetailModal = () => {
    // 在飞行的详情请求由 runDetail 在下次调用/卸载时中止；此处仅复位 UI，
    // 迟到的响应被 openDetailModal 里 prev.isOpen 的守卫挡下，不会污染已关闭的弹窗。
    setDetailLoading(false);
    setModalState({ isOpen: false, data: null, isEditing: false });
  };

  return (
    <div className={`space-y-6 animate-in fade-in ${selectedArticles.size > 0 ? 'pb-24' : ''}`}>
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">知识台账</h2>
          <p className="page-subtitle mt-3 max-w-4xl">按类型、来源、日期与关键词过滤，查找并管理全部归档内容。</p>
        </div>
        <div className="page-actions">
          {canManageArticles && ragEnabled && (
            <button onClick={handleVectorizeAllPending} disabled={vectorizingAll} className="action-button action-button-secondary">
              {vectorizingAll ? <RefreshCw className="animate-spin" /> : <Zap className="text-amber-500" />} 全量向量化
            </button>
          )}
          {canManageArticles && (
            <button onClick={() => setManualAddModal(true)} className="action-button action-button-primary">
              <Plus /> 手工录入
            </button>
          )}
          <button onClick={refreshArticles} disabled={loading} className="action-button action-button-secondary">
            <RefreshCw className={`text-[var(--dorami-blue)] ${loading ? 'animate-spin' : ''}`} /> 同步最新
          </button>
        </div>
      </div>

      <div className="surface-card relative z-30 rounded-[var(--r-overlay)] p-5">
        <div className="flex flex-col gap-4">
          <div className="ledger-filter-row flex flex-col gap-3 lg:flex-row lg:items-center">
            <label className="search-box min-h-[52px] flex-1">
              <Search className="mr-3 h-5 w-5 text-slate-500" />
              <input type="text" placeholder="搜索标题、内容、来源网站、标签等关键词..." value={searchInput} onChange={e => setSearchInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSearchSubmit()} className="py-3" />
              <span className="hidden rounded-[var(--r-sm)] border border-[var(--dorami-border)] px-2 py-1 text-xs font-bold text-slate-500 sm:inline-flex">⌘ /</span>
            </label>
            <div className="field-box lg:w-64">
              <span>数据来源</span>
              <select value={filters.source_id} onChange={e => setFilters({ ...filters, source_id: e.target.value })}>
                <option value="">全部节点</option>
                {sourceGroups.map(group => (
                  <optgroup key={group.name} label={group.name}>
                    {group.items.map(src => <option key={src} value={src}>{getFetcherName(src)}</option>)}
                  </optgroup>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => setShowAdvanced(v => !v)}
              className={`action-button action-button-secondary min-h-[52px] ${showAdvanced ? 'text-[var(--dorami-blue)]' : ''}`}
            >
              <SlidersHorizontal /> 高级筛选{advancedCount > 0 && <span className="ml-1 rounded-full bg-indigo-100 px-1.5 micro-label font-black text-indigo-700">{advancedCount}</span>}
            </button>
          </div>

          {isReader && (
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs font-bold text-slate-500">个性化视图</span>
              <div className="segmented-control">
                {[
                  { id: 'off', label: '全部内容' },
                  { id: 'prioritize', label: '我的订阅优先' },
                  { id: 'only', label: '仅看我的订阅' },
                ].map(opt => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setFilters({ ...filters, subscribed_scope: opt.id })}
                    className={`segmented-option ${filters.subscribed_scope === opt.id ? 'segmented-option-active' : ''}`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {showAdvanced && (
            <div className="grid grid-cols-1 gap-3 border-t border-[var(--dorami-border)] pt-4 animate-in fade-in slide-in-from-top-1 md:grid-cols-2 xl:grid-cols-[1fr_1.35fr_1.35fr_1fr]">
              <div className="field-box">
                <span>结构类型</span>
                <select value={filters.content_type} onChange={e => setFilters({ ...filters, content_type: e.target.value })}>
                  <option value="">全部类型</option>
                  {uniqueContentTypes.map(t => <option key={t} value={t}>{contentTypeLabel(t)}</option>)}
                </select>
              </div>
              <div className="field-box">
                <span>原始发布日期</span>
                <DateRangePicker
                  startDate={filters.publish_date_start}
                  endDate={filters.publish_date_end}
                  onChange={(start, end) => setFilters({ ...filters, publish_date_start: start, publish_date_end: end })}
                  placeholder="开始日期 → 结束日期"
                />
              </div>
              <div className="field-box">
                <span>抓取 / 收录时间</span>
                <DateRangePicker
                  startDate={filters.fetched_date_start}
                  endDate={filters.fetched_date_end}
                  onChange={(start, end) => setFilters({ ...filters, fetched_date_start: start, fetched_date_end: end })}
                  placeholder="开始日期 → 结束日期"
                />
              </div>
              {canManageArticles && ragEnabled && (
                <div className="field-box">
                  <span>向量状态</span>
                  <select value={filters.index_status} onChange={e => setFilters({ ...filters, index_status: e.target.value })}>
                    <option value="">全部状态</option>
                    <option value="indexed">向量已构建</option>
                    <option value="pending">待索引</option>
                    <option value="indexing">构建中</option>
                    <option value="failed">构建失败</option>
                    <option value="stale">待重建</option>
                  </select>
                </div>
              )}
            </div>
          )}

          <ActiveFilterBar items={activeFilterItems} onClearAll={clearAllFilters} />
        </div>
      </div>

      <div className="surface-card relative z-10 rounded-[var(--r-overlay)] overflow-x-auto overflow-y-visible">
        <div className="toolbar-card min-w-[980px]">
          <div className="toolbar-title">
            <span>
              显示 {pageStart.toLocaleString()}-{pageEnd.toLocaleString()} / 共 {articlePageInfo.total.toLocaleString()} 条记录
            </span>
            <span className="text-slate-500">第 {currentPage.toLocaleString()} / {totalPages.toLocaleString()} 页</span>
            <span className="text-slate-500">已选择 {selectedArticles.size} 条</span>
          </div>
        </div>

        <table className="data-table ledger-table w-full min-w-[980px] text-left">
          <colgroup>
            <col className="ledger-col-select" />
            <col className="ledger-col-type" />
            <col className="ledger-col-source" />
            <col className="ledger-col-title" />
            <col className="ledger-col-publish" />
            {canManageArticles && ragEnabled && <col className="ledger-col-vector" />}
          </colgroup>
          <thead className="bg-[var(--dorami-well)] border-b border-[var(--dorami-border)] text-slate-500 text-xs tracking-wider">
            <tr>
              <th className="px-4 py-3 w-12 text-center">
                {canSelectArticles && (
                  <input type="checkbox" aria-label="全选当前页记录" checked={selectedArticles.size === articles.length && articles.length > 0} onChange={toggleAllArticles} className="w-4 h-4 text-[var(--dorami-blue)] rounded cursor-pointer" />
                )}
              </th>
              <th className="px-3 py-4 w-36 font-bold">内容类型</th>
              <th className="px-3 py-4 w-44 font-bold">数据来源</th>
              <th className="px-4 py-4 font-bold">标题 / 内容摘要</th>
              <th className="px-3 py-4 w-[150px] font-bold">发布 / 收录</th>
              {canManageArticles && ragEnabled && <th className="px-3 py-4 w-36 font-bold">向量状态</th>}
            </tr>
          </thead>
          <tbody key={listVersion} className="row-stagger divide-y divide-[var(--dorami-border)] text-sm">
            {loading && articles.length === 0 ? (
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={`skeleton-${i}`}>
                  <td className="px-4 py-4"><div className="skeleton mx-auto h-4 w-4" /></td>
                  <td className="px-3 py-4"><div className="skeleton h-5 w-20 rounded-full" /></td>
                  <td className="px-3 py-4"><div className="flex items-center gap-2.5"><div className="skeleton h-8 w-8 rounded-[var(--r-control)]" /><div className="skeleton h-4 w-24" /></div></td>
                  <td className="px-4 py-4"><div className="skeleton h-4 w-3/4" /><div className="skeleton mt-2 h-3 w-1/2" /></td>
                  <td className="px-3 py-4"><div className="skeleton h-4 w-20" /><div className="skeleton mt-1.5 h-3 w-24" /></td>
                  {canManageArticles && ragEnabled && <td className="px-3 py-4"><div className="skeleton h-6 w-24 rounded-full" /></td>}
                </tr>
              ))
            ) : articles.length === 0 ? (
              <tr><td colSpan={canManageArticles && ragEnabled ? 6 : 5} className="px-6 py-16 text-center text-slate-500 font-medium">当前时间区间或过滤条件下，未查询到相关数据</td></tr>
            ) : articles.map((article) => (
              <tr key={article.id} className="hover:bg-[var(--dorami-wash)] transition-colors group">
                <td className="px-4 py-4 text-center">
                  {canSelectArticles && (
                    <input type="checkbox" aria-label={`选择：${article.title || article.id}`} checked={selectedArticles.has(article.id)} onChange={() => toggleArticleSelection(article.id)} className="w-4 h-4 text-[var(--dorami-blue)] rounded cursor-pointer" />
                  )}
                </td>
                <td className="px-3 py-4"><span className="data-chip max-w-full overflow-hidden text-ellipsis" title={article.content_type || ''}>{contentTypeLabel(article.content_type)}</span></td>
                <td className="px-3 py-4">
                  {(() => {
                    const company = companyFor(article.source_id);
                    const name = getFetcherName(article.source_id);
                    const showCompany = company.name && company.name !== name && !company.key.startsWith('sid:');
                    return (
                      <button
                        type="button"
                        disabled={!onFocusSource}
                        onClick={() => onFocusSource?.(article.source_id)}
                        className="ledger-source-link flex max-w-full items-center gap-2.5 min-w-0 text-left"
                        title={onFocusSource ? `定位来源「${name}」` : article.source_id}
                      >
                        <LogoMark company={company} size="sm" />
                        <div className="min-w-0">
                          <div className="ledger-source-name font-bold text-slate-700 text-xs line-clamp-1" title={article.source_id}>{name}</div>
                          {showCompany && <div className="text-xs text-slate-500 truncate">{company.name}</div>}
                        </div>
                      </button>
                    );
                  })()}
                </td>
                <td className="px-4 py-4 font-bold text-slate-800 cursor-pointer hover:text-[var(--dorami-blue)] transition-colors" onClick={() => openDetailModal(article)}>
                  <div className="line-clamp-1">{article.title}</div>
                  <div className="mt-1 line-clamp-1 text-xs font-medium text-slate-500">{excerptOf(article.content_preview || article.content) || '暂无摘要内容'}</div>
                </td>
                <td className="px-3 py-4">
                  <div className="text-slate-500 text-xs font-mono">{article.publish_date?.split('T')[0] || '-'}</div>
                  <div className="tiny-meta mt-0.5 font-mono" title={`收录时间：${article.fetched_date?.replace('T', ' ').substring(0, 16) || '—'}`}>{article.fetched_date?.split('T')[0] || '-'}</div>
                </td>
                {canManageArticles && ragEnabled && (
                  <td className="px-3 py-4">
                    {(() => {
                      // index_status 优先；缺省回退到旧布尔位（向后兼容）。
                      const status = article.index_status || (article.is_vectorized ? 'indexed' : 'pending');
                      const busy = vectorizingId === article.id || status === 'indexing';
                      if (status === 'indexed') {
                        return (
                          <span className="vector-status vector-status-done">
                            <CheckCircle className="vector-status-icon" strokeWidth={2.35} />
                            <span className="vector-status-label">向量已构建</span>
                          </span>
                        );
                      }
                      // 待索引/失败/陈旧/索引中：可点击（重）构建；失败/陈旧带语义色。
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
              </tr>
            ))}
          </tbody>
        </table>
        {articlePageInfo.total > 0 && (
          <div className="flex min-w-[980px] flex-wrap items-center justify-between gap-3 border-t border-[var(--dorami-border)] bg-[var(--dorami-soft)] p-4">
            <div className="text-xs font-bold text-slate-500">
              每页 {ARTICLE_PAGE_SIZE} 条，当前 {pageStart.toLocaleString()}-{pageEnd.toLocaleString()} 条
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setCurrentPage(1)}
                disabled={!canGoPrev}
                className="action-button action-button-secondary min-h-[36px] px-3 text-xs"
              >
                首页
              </button>
              <button
                type="button"
                onClick={() => setCurrentPage(page => Math.max(1, page - 1))}
                disabled={!canGoPrev}
                className="action-button action-button-secondary min-h-[36px] px-3 text-xs"
              >
                上一页
              </button>
              <span className="rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-[var(--dorami-surface)] px-3 py-2 text-xs font-bold text-slate-500">
                第 {currentPage.toLocaleString()} / {totalPages.toLocaleString()} 页
              </span>
              <button
                type="button"
                onClick={() => setCurrentPage(page => Math.min(totalPages, page + 1))}
                disabled={!canGoNext}
                className="action-button action-button-secondary min-h-[36px] px-3 text-xs"
              >
                下一页
              </button>
              <button
                type="button"
                onClick={() => setCurrentPage(totalPages)}
                disabled={!canGoNext}
                className="action-button action-button-secondary min-h-[36px] px-3 text-xs"
              >
                末页
              </button>
            </div>
          </div>
        )}
      </div>

      {selectedArticles.size > 0 && canSelectArticles && (
        <div className="selection-bar animate-in slide-in-from-bottom-4">
          <div className="selection-bar-info">
            <CheckCircle /> 已选择 {selectedArticles.size} 条记录
          </div>
          <div className="selection-bar-actions">
            {canManageArticles && ragEnabled && (
              <button onClick={handleBatchVectorize} className="action-button action-button-secondary text-blue-700">
                <Zap /> 批量构建
              </button>
            )}
            {canManageArticles && (
              <button onClick={handleBatchDeleteArticles} className="action-button action-button-danger">
                <Trash2 /> 批量删除
              </button>
            )}
          </div>
        </div>
      )}

      <ArticleDetailModal
        isOpen={modalState.isOpen}
        data={modalState.data}
        isEditing={modalState.isEditing}
        isLoading={detailLoading}
        getFetcherName={getFetcherName}
        canEdit={canManageArticles}
        onClose={closeDetailModal}
        onToggleEdit={() => {
          if (detailLoading) return;
          setModalState({ ...modalState, isEditing: !modalState.isEditing });
        }}
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
