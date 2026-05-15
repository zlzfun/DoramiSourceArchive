import { useState, useEffect } from 'react';
import { Database, Search, RefreshCw, Copy, Check, ExternalLink, RotateCcw, Zap } from 'lucide-react';
import { fetchVectorStats, vectorSearch, ragContext, reindexAll, vectorizeAllPending } from '../api';
import DateRangePicker from './DateRangePicker';

export default function VectorTab({ availableFetchers, showToast }) {
  const [vectorStats, setVectorStats] = useState({ total: 0 });
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [topK, setTopK] = useState(5);
  const [filterSourceId, setFilterSourceId] = useState('');
  const [copyingContext, setCopyingContext] = useState(false);
  const [copiedContext, setCopiedContext] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [vectorizingPending, setVectorizingPending] = useState(false);
  const [rerank, setRerank] = useState(false);
  const [expandContext, setExpandContext] = useState(false);
  const [filterDateStart, setFilterDateStart] = useState('');
  const [filterDateEnd, setFilterDateEnd] = useState('');

  const loadStats = async () => {
    try {
      const data = await fetchVectorStats();
      setVectorStats({ total: data.total_vectors });
    } catch (e) { console.error(e); }
  };

  useEffect(() => { loadStats(); }, []);

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    setSearchResults([]);
    try {
      const opts = {};
      if (filterSourceId) opts.source_id = filterSourceId;
      if (filterDateStart) opts.publish_date_gte = filterDateStart;
      if (filterDateEnd) opts.publish_date_lte = filterDateEnd;
      if (rerank) opts.rerank = true;
      const data = await vectorSearch(searchQuery, topK, opts);
      setSearchResults(data.results || []);
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
    setSearching(false);
  };

  const handleCopyContext = async () => {
    if (!searchQuery.trim()) return;
    setCopyingContext(true);
    try {
      const opts = {};
      if (filterSourceId) opts.source_id = filterSourceId;
      if (filterDateStart) opts.publish_date_gte = filterDateStart;
      if (filterDateEnd) opts.publish_date_lte = filterDateEnd;
      if (rerank) opts.rerank = true;
      if (expandContext) opts.expand_context = true;
      const data = await ragContext(searchQuery, topK, opts);
      await navigator.clipboard.writeText(data.context_text);
      setCopiedContext(true);
      showToast(`已复制 RAG 上下文（${data.retrieved_count} 条来源，${data.total_chars} 字符）`, 'success');
      setTimeout(() => setCopiedContext(false), 2500);
    } catch (e) { showToast(e.message || '复制失败', 'error'); }
    setCopyingContext(false);
  };

  const handleReindexAll = async () => {
    if (!window.confirm('全量重索引将删除并重建整个向量库，适用于更换 Embedding 模型后使用。确认继续？')) return;
    setReindexing(true);
    try {
      const data = await reindexAll();
      showToast(`全量重索引完成：${data.total_reindexed}/${data.total_articles} 篇`, 'success');
      await loadStats();
    } catch (e) { showToast(e.message || '重索引失败', 'error'); }
    setReindexing(false);
  };

  const handleVectorizeAllPending = async () => {
    setVectorizingPending(true);
    try {
      const data = await vectorizeAllPending();
      showToast(`已向量化 ${data.count}/${data.total_pending} 篇待处理文章`, 'success');
      await loadStats();
    } catch (e) { showToast(e.message || '向量化失败', 'error'); }
    setVectorizingPending(false);
  };

  const getDistanceLabel = (dist) => {
    if (dist < 0.3) return { label: '极高', color: 'text-emerald-700 bg-emerald-100' };
    if (dist < 0.5) return { label: '高', color: 'text-blue-700 bg-blue-100' };
    if (dist < 0.7) return { label: '中', color: 'text-amber-700 bg-amber-100' };
    return { label: '低', color: 'text-slate-500 bg-slate-100' };
  };

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">向量雷达</h2>
          <p className="page-subtitle mt-3 max-w-3xl">ChromaDB 状态管理，语义检索测试，RAG 上下文导出，为知识召回质量提供可观察的调试界面。</p>
        </div>
        <div className="page-actions">
          <button onClick={handleVectorizeAllPending} disabled={vectorizingPending} className="action-button action-button-secondary">
            {vectorizingPending ? <RefreshCw className="animate-spin" /> : <Zap className="text-amber-500" />}
            索引待处理
          </button>
          <button onClick={handleReindexAll} disabled={reindexing} className="action-button action-button-primary">
            {reindexing ? <RefreshCw className="animate-spin" /> : <RotateCcw />}
            全量重索引
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[340px_1fr]">
        <div className="relative overflow-hidden rounded-[14px] bg-gradient-to-br from-[#174fff] via-[#5d5cff] to-[#8c5aff] p-6 text-white shadow-lg shadow-blue-500/20">
          <div className="absolute -right-14 -top-14 h-40 w-40 rounded-full bg-white/16" />
          <div className="relative">
            <h4 className="text-indigo-100 font-bold text-sm mb-2 flex items-center"><Database className="w-4 h-4 mr-1.5" /> ChromaDB 挂载块数</h4>
            <div className="text-5xl font-bold">{vectorStats.total} <span className="text-lg font-medium opacity-80">Chunks</span></div>
            <p className="mt-4 text-xs font-bold text-blue-100">向量库状态会随索引和重索引操作刷新。</p>
          </div>
        </div>

        <div className="surface-card rounded-[16px] p-6">
        <h3 className="font-bold text-lg mb-4 flex items-center"><Search className="w-5 h-5 mr-2 text-blue-500" /> 语义检索</h3>

        {/* 检索控制行 */}
        <div className="vector-search-layout mb-3">
          <label className="search-box min-h-[48px] flex-1">
            <Search className="mr-3 h-5 w-5 text-slate-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="输入自然语言查询（中英文均可）..."
            className="py-3"
          />
          </label>
          <button onClick={handleSearch} disabled={searching} className="action-button action-button-primary min-h-[48px] w-full justify-center px-6">
            {searching ? <RefreshCw className="animate-spin" /> : <><Search />检索</>}
          </button>
          <button onClick={handleCopyContext} disabled={copyingContext || !searchQuery.trim()} title="将检索结果组装为 RAG 上下文并复制到剪贴板（可直接粘贴到 Dify 等 LLM 工作流）" className="action-button action-button-secondary min-h-[48px] w-full justify-center disabled:opacity-40">
            {copiedContext ? <Check /> : copyingContext ? <RefreshCw className="animate-spin" /> : <Copy />}
            复制上下文
          </button>
        </div>

        {/* 过滤参数行 */}
        <div className="vector-filter-layout mb-5">
          <div className="vector-inline-filters">
            <div className="field-box">
              <span>Top-K</span>
              <input
                type="number"
                min={1} max={20}
                value={topK}
                onChange={e => setTopK(Math.max(1, Math.min(20, Number(e.target.value))))}
              />
            </div>
            <div className="field-box">
              <span>来源筛选</span>
              <select
                value={filterSourceId}
                onChange={e => setFilterSourceId(e.target.value)}
              >
                <option value="">全部来源</option>
                {availableFetchers.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
            </div>
            <div className="field-box">
              <span>发布日期</span>
                <DateRangePicker
                  startDate={filterDateStart}
                  endDate={filterDateEnd}
                  onChange={(start, end) => { setFilterDateStart(start); setFilterDateEnd(end); }}
                  placeholder="不限日期"
                />
            </div>
          </div>
          <div className="vector-option-box">
            <span>检索选项</span>
            <div className="vector-option-list">
              <label className="vector-option-chip" title="使用 Cross-Encoder 对 Bi-Encoder 检索结果重打分，精确率更高但耗时更长">
                <input type="checkbox" checked={rerank} onChange={e => setRerank(e.target.checked)} />
                <span>重排序</span>
              </label>
              <label className="vector-option-chip" title="复制上下文时，在命中片段前后拼接相邻段落，扩展上下文窗口">
                <input type="checkbox" checked={expandContext} onChange={e => setExpandContext(e.target.checked)} />
                <span>扩展上下文</span>
              </label>
            </div>
          </div>
        </div>

        {/* 结果列表 */}
        <div className="space-y-4">
          {searchResults.length === 0 && !searching && (
            <div className="empty-state py-8">
              输入查询句，探测语义检索效果
            </div>
          )}
          {searchResults.map((res, i) => {
            const { label, color } = getDistanceLabel(res.distance);
            const sourceUrl = res.metadata?.source_url;
            const pubDate = res.metadata?.publish_date?.split('T')[0];
            return (
              <div key={i} className="bg-white/72 border border-slate-200 p-5 rounded-[14px] shadow-sm">
                <div className="flex items-start justify-between mb-2">
                  <h4 className="font-bold text-slate-800 text-sm pr-4 line-clamp-1 flex-1">
                    {sourceUrl
                      ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="hover:text-blue-600 transition-colors flex items-center gap-1">
                          {res.metadata?.title || '未知片段'}<ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      : (res.metadata?.title || '未知片段')
                    }
                  </h4>
                  <span className={`status-badge shrink-0 ${color}`}>
                    {label} {res.distance.toFixed(3)}
                  </span>
                </div>
                <div className="text-xs text-slate-500 flex flex-wrap gap-1.5 mb-3">
                  <span className="bg-white px-2 py-0.5 border border-slate-200 rounded shadow-sm">{res.metadata?.content_type}</span>
                  <span className="bg-white px-2 py-0.5 border border-slate-200 rounded shadow-sm">{res.metadata?.source_id}</span>
                  {pubDate && <span className="bg-white px-2 py-0.5 border border-slate-200 rounded shadow-sm">{pubDate}</span>}
                </div>
                <p className="text-sm text-slate-600 bg-white p-4 rounded-xl border border-slate-100 leading-relaxed line-clamp-4">
                  {res.document}
                </p>
              </div>
            );
          })}
        </div>
        </div>
      </div>
    </div>
  );
}
