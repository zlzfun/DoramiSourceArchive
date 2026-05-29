import { useState, useEffect, useMemo } from 'react';
import { RefreshCw, CheckCircle, Zap, Search, Plus, Trash2, SlidersHorizontal } from 'lucide-react';
import DateRangePicker from './DateRangePicker';
import ArticleDetailModal from './ArticleDetailModal';
import ManualAddModal from './ManualAddModal';
import LogoMark from './LogoMark';
import { resolveCompany } from '../sourceTaxonomy';
import {
  fetchArticles as apiFetchArticles,
  batchDeleteArticles,
  vectorizeArticle,
  batchVectorizeArticles,
  vectorizeAllPending,
  updateArticle,
  createArticle,
} from '../api';

const ARTICLE_PAGE_SIZE = 100;

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
}) {
  const [articles, setArticles] = useState([]);
  const [articlePageInfo, setArticlePageInfo] = useState({ total: 0, nextSkip: null });
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedArticles, setSelectedArticles] = useState(new Set());
  const [modalState, setModalState] = useState({ isOpen: false, data: null, isEditing: false });
  const [manualAddModal, setManualAddModal] = useState(false);
  const [vectorizingId, setVectorizingId] = useState(null);
  const [vectorizingAll, setVectorizingAll] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [filters, setFilters] = useState({
    content_type: '',
    source_id: '',
    is_vectorized: '',
    search: '',
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
    filters.is_vectorized,
    filters.publish_date_start || filters.publish_date_end,
    filters.fetched_date_start || filters.fetched_date_end,
  ].filter(Boolean).length;

  const canSelectArticles = canManageArticles;

  const handleVectorize = async (id) => {
    setVectorizingId(id);
    try {
      await vectorizeArticle(id);
      showToast('建立索引成功', 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
    setVectorizingId(null);
  };

  const handleBatchVectorize = async () => {
    try {
      const data = await batchVectorizeArticles(Array.from(selectedArticles));
      showToast(`成功处理，${data.count} 条记录新建了向量索引`, 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
  };

  const handleVectorizeAllPending = async () => {
    setVectorizingAll(true);
    try {
      const data = await vectorizeAllPending();
      showToast(`已向量化 ${data.count}/${data.total_pending} 篇待处理文章`, 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
    setVectorizingAll(false);
  };

  const loadArticles = async () => {
    setLoading(true);
    setSelectedArticles(new Set());
    try {
      const data = await apiFetchArticles(filters, ARTICLE_PAGE_SIZE, 0, true);
      setArticles(data.items || []);
      setArticlePageInfo({ total: data.total || 0, nextSkip: data.next_skip ?? null });
    } catch (e) {
      showToast(e.message || '后端服务未启动或网络错误', 'error');
    }
    setLoading(false);
  };

  const loadMoreArticles = async () => {
    if (articlePageInfo.nextSkip === null) return;
    setLoadingMore(true);
    try {
      const data = await apiFetchArticles(filters, ARTICLE_PAGE_SIZE, articlePageInfo.nextSkip, true);
      setArticles(prev => [...prev, ...(data.items || [])]);
      setArticlePageInfo({ total: data.total || 0, nextSkip: data.next_skip ?? null });
    } catch (e) {
      showToast(e.message || '加载更多失败', 'error');
    }
    setLoadingMore(false);
  };

  useEffect(() => {
    loadArticles();
  }, [
    filters.content_type, filters.source_id, filters.is_vectorized,
    filters.publish_date_start, filters.publish_date_end,
    filters.fetched_date_start, filters.fetched_date_end,
    filters.subscribed_scope,
  ]);

  useEffect(() => {
    if (isActive && articlesDirty) {
      loadArticles();
      onArticlesRefreshed?.();
    }
  }, [isActive, articlesDirty]);

  useEffect(() => {
    if (!pendingFilter) return;
    setFilters(prev => ({
      ...prev,
      content_type: '',
      source_id: pendingFilter.source_id ?? prev.source_id,
      is_vectorized: '',
      search: '',
      publish_date_start: '',
      publish_date_end: '',
      fetched_date_start: '',
      fetched_date_end: '',
    }));
    onPendingFilterApplied?.();
  }, [pendingFilter]);

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
    if (!window.confirm(`确定彻底删除选中的 ${selectedArticles.size} 条数据吗？`)) return;
    try {
      await batchDeleteArticles(Array.from(selectedArticles));
      showToast('批量删除成功', 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
  };

  const handleUpdateArticle = async (id, updatedData) => {
    try {
      await updateArticle(id, updatedData);
      showToast('数据修改成功', 'success');
      setModalState({ isOpen: false, data: null, isEditing: false });
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
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
    try {
      await createArticle(payload);
      showToast('手工录入成功', 'success');
      setManualAddModal(false);
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
  };

  const openDetailModal = (article) => {
    setModalState({ isOpen: true, data: article, isEditing: false });
  };

  return (
    <div className={`space-y-6 animate-in fade-in ${selectedArticles.size > 0 ? 'pb-24' : ''}`}>
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">知识台账</h2>
          <p className="page-subtitle mt-3 max-w-4xl">沉浸式多维过滤，支持点击日期极速框选范围，快速查找与管理全部抓取内容。</p>
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
          <button onClick={loadArticles} disabled={loading} className="action-button action-button-secondary">
            <RefreshCw className={`text-blue-600 ${loading ? 'animate-spin' : ''}`} /> 同步最新
          </button>
        </div>
      </div>

      <div className="surface-card relative z-30 rounded-[16px] p-5">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <label className="search-box min-h-[52px] flex-1">
              <Search className="mr-3 h-5 w-5 text-slate-400" />
              <input type="text" placeholder="搜索标题、内容、来源网站、标签等关键词..." value={filters.search} onChange={e => setFilters({ ...filters, search: e.target.value })} onKeyDown={e => e.key === 'Enter' && loadArticles()} className="py-3" />
              <span className="hidden rounded-md border border-slate-200 px-2 py-1 text-xs font-bold text-slate-400 sm:inline-flex">⌘ /</span>
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
              className={`action-button action-button-secondary min-h-[52px] ${showAdvanced ? 'text-indigo-700' : ''}`}
            >
              <SlidersHorizontal /> 高级筛选{advancedCount > 0 && <span className="ml-1 rounded-full bg-indigo-100 px-1.5 text-[11px] font-black text-indigo-700">{advancedCount}</span>}
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
            <div className="grid grid-cols-1 gap-3 border-t border-slate-100 pt-4 animate-in fade-in slide-in-from-top-1 md:grid-cols-2 xl:grid-cols-[1fr_1.35fr_1.35fr_1fr]">
              <div className="field-box">
                <span>结构类型</span>
                <select value={filters.content_type} onChange={e => setFilters({ ...filters, content_type: e.target.value })}>
                  <option value="">全部类型</option>
                  {uniqueContentTypes.map(t => <option key={t} value={t}>{t}</option>)}
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
                  <select value={filters.is_vectorized} onChange={e => setFilters({ ...filters, is_vectorized: e.target.value })}>
                    <option value="">全部状态</option>
                    <option value="true">向量已构建</option>
                    <option value="false">向量未构建</option>
                  </select>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="surface-card relative z-10 rounded-[16px] overflow-x-auto overflow-y-visible">
        <div className="toolbar-card min-w-[980px]">
          <div className="toolbar-title">
            <span>
              显示 {articles.length.toLocaleString()} / 共 {articlePageInfo.total.toLocaleString()} 条记录
            </span>
            <span className="text-slate-500">已选择 {selectedArticles.size} 条</span>
          </div>
        </div>

        <table className="data-table w-full min-w-[980px] text-left">
          <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs tracking-wider">
            <tr>
              <th className="px-4 py-3 w-12 text-center">
                {canSelectArticles && (
                  <input type="checkbox" checked={selectedArticles.size === articles.length && articles.length > 0} onChange={toggleAllArticles} className="w-4 h-4 text-blue-600 rounded cursor-pointer" />
                )}
              </th>
              <th className="px-3 py-4 w-36 font-bold">内容类型</th>
              <th className="px-3 py-4 w-44 font-bold">数据来源</th>
              <th className="px-4 py-4 font-bold">标题 / 内容摘要</th>
              <th className="px-3 py-4 w-[150px] font-bold">原始发布日期</th>
              <th className="px-3 py-4 w-[150px] font-bold">抓取 / 收录时间</th>
              {canManageArticles && ragEnabled && <th className="px-3 py-4 w-36 font-bold">向量状态</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-sm">
            {articles.length === 0 ? (
              <tr><td colSpan={canManageArticles && ragEnabled ? 7 : 6} className="px-6 py-16 text-center text-slate-400 font-medium">当前时间区间或过滤条件下，未查询到相关数据</td></tr>
            ) : articles.map((article) => (
              <tr key={article.id} className="hover:bg-blue-50/40 transition-colors group">
                <td className="px-4 py-4 text-center">
                  {canSelectArticles && (
                    <input type="checkbox" checked={selectedArticles.has(article.id)} onChange={() => toggleArticleSelection(article.id)} className="w-4 h-4 text-blue-600 rounded cursor-pointer" />
                  )}
                </td>
                <td className="px-3 py-4"><span className="data-chip">{article.content_type || '未知'}</span></td>
                <td className="px-3 py-4">
                  {(() => {
                    const company = companyFor(article.source_id);
                    const name = getFetcherName(article.source_id);
                    const showCompany = company.name && company.name !== name && !company.key.startsWith('sid:');
                    return (
                      <div className="flex items-center gap-2.5 min-w-0">
                        <LogoMark company={company} size="sm" />
                        <div className="min-w-0">
                          <div className="font-bold text-slate-700 text-xs line-clamp-1" title={article.source_id}>{name}</div>
                          {showCompany && <div className="text-[11px] text-slate-400 truncate">{company.name}</div>}
                        </div>
                      </div>
                    );
                  })()}
                </td>
                <td className="px-4 py-4 font-bold text-slate-800 cursor-pointer hover:text-blue-600 transition-colors" onClick={() => openDetailModal(article)}>
                  <div className="line-clamp-1">{article.title}</div>
                  <div className="mt-1 line-clamp-1 text-xs font-semibold text-slate-400">{article.content || '暂无摘要内容'}</div>
                </td>
                <td className="px-3 py-4 text-slate-500 text-xs font-mono">{article.publish_date?.split('T')[0] || '-'}</td>
                <td className="px-3 py-4 text-slate-600 text-xs font-mono">{article.fetched_date?.replace('T', ' ').substring(0, 16) || '-'}</td>
                {canManageArticles && ragEnabled && (
                  <td className="px-3 py-4">
                    {article.is_vectorized ? (
                      <span className="vector-status vector-status-done">
                        <CheckCircle className="vector-status-icon" strokeWidth={2.35} />
                        <span className="vector-status-label">向量已构建</span>
                      </span>
                    ) : (
                      <button onClick={() => handleVectorize(article.id)} disabled={vectorizingId === article.id} className="vector-status vector-status-pending group">
                        {vectorizingId === article.id ? <RefreshCw className="vector-status-icon animate-spin" strokeWidth={2.35} /> : <Zap className="vector-status-icon" strokeWidth={2.35} />}
                        <span className="vector-status-label vector-status-default">{vectorizingId === article.id ? '构建中' : '向量未构建'}</span>
                        <span className="vector-status-label vector-status-hover">{vectorizingId === article.id ? '构建中' : '构建向量'}</span>
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {articlePageInfo.nextSkip !== null && (
          <div className="flex justify-center border-t border-slate-100 bg-slate-50/60 p-4">
            <button
              type="button"
              onClick={loadMoreArticles}
              disabled={loadingMore}
              className="action-button action-button-secondary"
            >
              <RefreshCw className={loadingMore ? 'animate-spin' : ''} />
              {loadingMore ? '加载中...' : `加载更多（剩余 ${(articlePageInfo.total - articles.length).toLocaleString()} 条）`}
            </button>
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
        getFetcherName={getFetcherName}
        canEdit={canManageArticles}
        onClose={() => setModalState({ isOpen: false, data: null, isEditing: false })}
        onToggleEdit={() => setModalState({ ...modalState, isEditing: !modalState.isEditing })}
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
