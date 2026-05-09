import React, { useState, useEffect } from 'react';
import { Database, RefreshCw, CheckCircle, Zap, Search, Plus, Trash2, CheckSquare, FileText, Link as LinkIcon, Calendar, Box, ExternalLink, Edit2, Save, X, AlertCircle } from 'lucide-react';
import DateRangePicker from './DateRangePicker';
import ArticleDetailModal from './ArticleDetailModal';
import ManualAddModal from './ManualAddModal';
import {
  fetchArticles as apiFetchArticles,
  deleteArticle,
  batchDeleteArticles,
  vectorizeArticle,
  batchVectorizeArticles,
  updateArticle,
  createArticle,
} from '../api';

export default function DataTab({ availableFetchers, showToast }) {
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [vectorizingId, setVectorizingId] = useState(null);
  const [selectedArticles, setSelectedArticles] = useState(new Set());
  const [modalState, setModalState] = useState({ isOpen: false, data: null, isEditing: false });
  const [manualAddModal, setManualAddModal] = useState(false);

  const [filters, setFilters] = useState({
    content_type: '',
    source_id: '',
    is_vectorized: '',
    search: '',
    publish_date_start: '',
    publish_date_end: '',
    fetched_date_start: '',
    fetched_date_end: '',
  });

  const getFetcherName = (id) => {
    const fetcher = availableFetchers.find(f => f.id === id);
    return fetcher ? fetcher.name : id;
  };

  const uniqueContentTypes = [...new Set(articles.map(a => a.content_type).filter(Boolean))];
  const uniqueSourceIds = [...new Set(articles.map(a => a.source_id).filter(Boolean))];

  const loadArticles = async () => {
    setLoading(true);
    setSelectedArticles(new Set());
    try {
      const data = await apiFetchArticles(filters);
      setArticles(data);
    } catch (e) {
      showToast(e.message || '后端服务未启动或网络错误', 'error');
    }
    setLoading(false);
  };

  useEffect(() => {
    loadArticles();
  }, [
    filters.content_type, filters.source_id, filters.is_vectorized,
    filters.publish_date_start, filters.publish_date_end,
    filters.fetched_date_start, filters.fetched_date_end,
  ]);

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

  const handleBatchVectorize = async () => {
    try {
      const data = await batchVectorizeArticles(Array.from(selectedArticles));
      showToast(`成功处理，${data.count} 条记录新建了向量索引`, 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
  };

  const handleDeleteArticle = async (id) => {
    if (!window.confirm('确定要彻底删除这条数据吗？')) return;
    try {
      await deleteArticle(id);
      showToast('删除成功', 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
  };

  const handleVectorize = async (id) => {
    setVectorizingId(id);
    try {
      await vectorizeArticle(id);
      showToast('建立索引成功', 'success');
      loadArticles();
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
    setVectorizingId(null);
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
    <div className="space-y-6 animate-in fade-in">
      <div className="flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-bold">知识台账</h2>
          <p className="text-sm text-slate-500 mt-1">沉浸式多维过滤。支持点击日历极速框选范围。</p>
        </div>
        <div className="flex space-x-3 items-center">
          {selectedArticles.size > 0 && (
            <div className="flex space-x-2 mr-2 animate-in slide-in-from-right-4">
              <button onClick={handleBatchVectorize} className="text-sm text-white bg-slate-800 hover:bg-slate-900 shadow-md px-4 py-2 rounded-lg transition-all flex items-center font-bold">
                <Zap className="w-4 h-4 mr-1.5" /> 批量构建 ({selectedArticles.size})
              </button>
              <button onClick={handleBatchDeleteArticles} className="text-sm text-white bg-red-600 hover:bg-red-700 shadow-md px-4 py-2 rounded-lg transition-all flex items-center font-bold">
                <Trash2 className="w-4 h-4 mr-1.5" /> 彻底删除 ({selectedArticles.size})
              </button>
            </div>
          )}
          <button onClick={() => setManualAddModal(true)} className="text-sm text-white bg-blue-600 hover:bg-blue-700 shadow-md px-4 py-2 rounded-lg transition-all flex items-center font-bold">
            <Plus className="w-4 h-4 mr-1.5" /> 手工录入
          </button>
          <button onClick={loadArticles} disabled={loading} className="text-sm text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 shadow-sm px-4 py-2 rounded-lg transition-all flex items-center font-bold">
            <RefreshCw className={`w-4 h-4 mr-2 text-blue-600 ${loading ? 'animate-spin' : ''}`} /> 同步
          </button>
        </div>
      </div>

      <div className="bg-white/80 p-3 rounded-2xl shadow-sm border border-slate-100 flex items-center">
        <Search className="w-5 h-5 text-slate-400 ml-2" />
        <input type="text" placeholder="全局检索文章标题..." value={filters.search} onChange={e => setFilters({ ...filters, search: e.target.value })} onKeyDown={e => e.key === 'Enter' && loadArticles()} className="w-full bg-transparent border-none text-sm px-4 py-2 outline-none font-medium" />
      </div>

      <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-visible">
        <table className="w-full text-left border-collapse">
          <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs tracking-wider">
            <tr>
              <th className="px-4 py-3 w-12 text-center align-top pt-4">
                <input type="checkbox" checked={selectedArticles.size === articles.length && articles.length > 0} onChange={toggleAllArticles} className="w-4 h-4 text-blue-600 rounded cursor-pointer" />
              </th>
              <th className="px-3 py-3 w-32 align-top">
                <div className="flex flex-col items-start space-y-1.5">
                  <span className="font-bold opacity-70 mb-0.5">结构类型</span>
                  <select value={filters.content_type} onChange={e => setFilters({ ...filters, content_type: e.target.value })} className="bg-slate-100/50 rounded px-1.5 py-1 text-blue-700 font-bold outline-none text-xs w-full cursor-pointer">
                    <option value="">全部 (All)</option>
                    {uniqueContentTypes.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </th>
              <th className="px-3 py-3 w-36 align-top">
                <div className="flex flex-col items-start space-y-1.5">
                  <span className="font-bold opacity-70 mb-0.5">数据来源</span>
                  <select value={filters.source_id} onChange={e => setFilters({ ...filters, source_id: e.target.value })} className="bg-slate-100/50 rounded px-1.5 py-1 text-blue-700 font-bold outline-none text-xs w-full cursor-pointer truncate">
                    <option value="">全部节点 (All)</option>
                    {uniqueSourceIds.map(src => <option key={src} value={src}>{getFetcherName(src)}</option>)}
                  </select>
                </div>
              </th>
              <th className="px-4 py-3 align-top pt-4"><span className="font-bold opacity-70">内容标题 (点击展开详情)</span></th>
              <th className="px-3 py-3 w-[160px] align-top">
                <div className="flex flex-col items-start space-y-1.5">
                  <span className="font-bold opacity-70 mb-0.5">原始发布时间</span>
                  <DateRangePicker
                    startDate={filters.publish_date_start}
                    endDate={filters.publish_date_end}
                    onChange={(start, end) => setFilters({ ...filters, publish_date_start: start, publish_date_end: end })}
                    placeholder="选择日期范围"
                  />
                </div>
              </th>
              <th className="px-3 py-3 w-[160px] align-top">
                <div className="flex flex-col items-start space-y-1.5">
                  <span className="font-bold opacity-70 mb-0.5">中枢收录时间</span>
                  <DateRangePicker
                    startDate={filters.fetched_date_start}
                    endDate={filters.fetched_date_end}
                    onChange={(start, end) => setFilters({ ...filters, fetched_date_start: start, fetched_date_end: end })}
                    placeholder="选择日期范围"
                  />
                </div>
              </th>
              <th className="px-3 py-3 w-28 align-top">
                <div className="flex flex-col items-start space-y-1.5">
                  <span className="font-bold opacity-70 mb-0.5">向量状态</span>
                  <select value={filters.is_vectorized} onChange={e => setFilters({ ...filters, is_vectorized: e.target.value })} className="bg-slate-100/50 rounded px-1.5 py-1 text-blue-700 font-bold outline-none text-xs w-full cursor-pointer">
                    <option value="">全部</option>
                    <option value="true">已索引</option>
                    <option value="false">待处理</option>
                  </select>
                </div>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-sm">
            {articles.length === 0 ? (
              <tr><td colSpan="7" className="px-6 py-16 text-center text-slate-400 font-medium">当前时间区间或过滤条件下，未查询到相关数据</td></tr>
            ) : articles.map((article) => (
              <tr key={article.id} className="hover:bg-blue-50/40 transition-colors group">
                <td className="px-4 py-4 text-center">
                  <input type="checkbox" checked={selectedArticles.has(article.id)} onChange={() => toggleArticleSelection(article.id)} className="w-4 h-4 text-blue-600 rounded cursor-pointer" />
                </td>
                <td className="px-3 py-4"><span className="px-2 py-1 rounded bg-slate-100 text-slate-600 text-[11px] font-bold uppercase">{article.content_type || '未知'}</span></td>
                <td className="px-3 py-4"><span className="font-bold text-slate-700 text-xs line-clamp-2" title={article.source_id}>{getFetcherName(article.source_id)}</span></td>
                <td className="px-4 py-4 font-extrabold text-slate-800 line-clamp-2 cursor-pointer hover:text-blue-600 transition-colors" onClick={() => openDetailModal(article)}>
                  {article.title}
                </td>
                <td className="px-3 py-4 text-slate-400 text-[11px] font-mono">{article.publish_date?.split('T')[0] || '-'}</td>
                <td className="px-3 py-4 text-emerald-600 text-[11px] font-mono font-medium">{article.fetched_date?.split('T')[0] || '-'}</td>
                <td className="px-3 py-4">
                  {article.is_vectorized ? (
                    <span className="flex items-center text-xs font-bold text-emerald-600"><CheckCircle className="w-3.5 h-3.5 mr-1" /> 已建索引</span>
                  ) : (
                    <button onClick={() => handleVectorize(article.id)} disabled={vectorizingId === article.id} className="text-xs font-bold text-slate-500 hover:text-blue-600 flex items-center transition-colors">
                      {vectorizingId === article.id ? <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Zap className="w-3.5 h-3.5 mr-1" />} 构建向量
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ArticleDetailModal
        isOpen={modalState.isOpen}
        data={modalState.data}
        isEditing={modalState.isEditing}
        getFetcherName={getFetcherName}
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
