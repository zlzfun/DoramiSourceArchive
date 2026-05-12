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
      <div className="flex justify-between items-end mb-6">
        <div>
          <h2 className="text-2xl font-bold">向量数据库雷达</h2>
          <p className="text-sm text-slate-500 mt-2">ChromaDB 状态管理，语义检索测试，RAG 上下文导出。</p>
        </div>
        <div className="flex space-x-2">
          <button onClick={handleVectorizeAllPending} disabled={vectorizingPending} className="text-sm text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 shadow-sm px-3 py-2 rounded-lg transition-all flex items-center font-bold">
            {vectorizingPending ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Zap className="w-4 h-4 mr-1.5 text-amber-500" />}
            索引待处理
          </button>
          <button onClick={handleReindexAll} disabled={reindexing} className="text-sm text-white bg-slate-700 hover:bg-slate-800 shadow-sm px-3 py-2 rounded-lg transition-all flex items-center font-bold">
            {reindexing ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <RotateCcw className="w-4 h-4 mr-1.5" />}
            全量重索引
          </button>
        </div>
      </div>

      <div className="bg-gradient-to-br from-indigo-500 to-purple-600 p-6 rounded-3xl text-white shadow-lg w-fit pr-20">
        <h4 className="text-indigo-100 font-bold text-sm mb-2 flex items-center"><Database className="w-4 h-4 mr-1.5" /> ChromaDB 挂载块数</h4>
        <div className="text-4xl font-black">{vectorStats.total} <span className="text-lg font-medium opacity-80">Chunks</span></div>
      </div>

      <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100 max-w-4xl">
        <h3 className="font-bold text-lg mb-4 flex items-center"><Search className="w-5 h-5 mr-2 text-blue-500" /> 语义检索</h3>

        {/* 检索控制行 */}
        <div className="flex space-x-3 mb-3">
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="输入自然语言查询（中英文均可）..."
            className="flex-1 bg-slate-50 border border-slate-200 px-4 py-3 rounded-xl outline-none focus:ring-1 focus:ring-blue-500 font-medium text-sm"
          />
          <button onClick={handleSearch} disabled={searching} className="bg-slate-800 text-white px-6 py-3 rounded-xl font-bold hover:bg-slate-700 transition-all flex items-center shrink-0">
            {searching ? <RefreshCw className="w-5 h-5 animate-spin" /> : <><Search className="w-4 h-4 mr-1.5" />检索</>}
          </button>
          <button onClick={handleCopyContext} disabled={copyingContext || !searchQuery.trim()} title="将检索结果组装为 RAG 上下文并复制到剪贴板（可直接粘贴到 Dify 等 LLM 工作流）" className="bg-indigo-600 text-white px-4 py-3 rounded-xl font-bold hover:bg-indigo-700 transition-all flex items-center shrink-0 disabled:opacity-40">
            {copiedContext ? <Check className="w-4 h-4 mr-1.5" /> : copyingContext ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Copy className="w-4 h-4 mr-1.5" />}
            复制上下文
          </button>
        </div>

        {/* 过滤参数行 */}
        <div className="flex space-x-3 mb-5">
          <div className="flex items-center space-x-2 text-sm text-slate-600">
            <span className="font-medium">Top-K</span>
            <input
              type="number"
              min={1} max={20}
              value={topK}
              onChange={e => setTopK(Math.max(1, Math.min(20, Number(e.target.value))))}
              className="w-16 bg-slate-50 border border-slate-200 px-2 py-1.5 rounded-lg text-center font-bold outline-none focus:ring-1 focus:ring-blue-500 text-sm"
            />
          </div>
          <div className="flex items-center space-x-2 text-sm text-slate-600">
            <span className="font-medium">来源筛选</span>
            <select
              value={filterSourceId}
              onChange={e => setFilterSourceId(e.target.value)}
              className="bg-slate-50 border border-slate-200 px-2 py-1.5 rounded-lg font-bold outline-none focus:ring-1 focus:ring-blue-500 text-sm text-slate-700"
            >
              <option value="">全部来源</option>
              {availableFetchers.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </div>
          <div className="flex items-center space-x-2 text-sm text-slate-600">
            <span className="font-medium shrink-0">发布日期</span>
            <div className="w-44">
              <DateRangePicker
                startDate={filterDateStart}
                endDate={filterDateEnd}
                onChange={(start, end) => { setFilterDateStart(start); setFilterDateEnd(end); }}
                placeholder="不限日期"
              />
            </div>
          </div>
          <label className="flex items-center space-x-1.5 text-sm text-slate-600 cursor-pointer select-none" title="使用 Cross-Encoder 对 Bi-Encoder 检索结果重打分，精确率更高但耗时更长">
            <input type="checkbox" checked={rerank} onChange={e => setRerank(e.target.checked)} className="w-3.5 h-3.5 rounded text-indigo-600 cursor-pointer" />
            <span className="font-medium">重排序</span>
          </label>
          <label className="flex items-center space-x-1.5 text-sm text-slate-600 cursor-pointer select-none" title="复制上下文时，在命中片段前后拼接相邻段落，扩展上下文窗口">
            <input type="checkbox" checked={expandContext} onChange={e => setExpandContext(e.target.checked)} className="w-3.5 h-3.5 rounded text-indigo-600 cursor-pointer" />
            <span className="font-medium">扩展上下文</span>
          </label>
          <div className="text-xs text-slate-400 flex items-center ml-auto">相关度越高距离越小</div>
        </div>

        {/* 结果列表 */}
        <div className="space-y-4">
          {searchResults.length === 0 && !searching && (
            <div className="text-center py-8 text-slate-400 font-bold bg-slate-50 rounded-xl border-dashed border-2 border-slate-200 text-sm">
              输入查询句，探测语义检索效果
            </div>
          )}
          {searchResults.map((res, i) => {
            const { label, color } = getDistanceLabel(res.distance);
            const sourceUrl = res.metadata?.source_url;
            const pubDate = res.metadata?.publish_date?.split('T')[0];
            return (
              <div key={i} className="bg-slate-50 border border-slate-200 p-5 rounded-2xl">
                <div className="flex items-start justify-between mb-2">
                  <h4 className="font-bold text-slate-800 text-sm pr-4 line-clamp-1 flex-1">
                    {sourceUrl
                      ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="hover:text-blue-600 transition-colors flex items-center gap-1">
                          {res.metadata?.title || '未知片段'}<ExternalLink className="w-3 h-3 shrink-0" />
                        </a>
                      : (res.metadata?.title || '未知片段')
                    }
                  </h4>
                  <span className={`text-xs font-black px-2 py-0.5 rounded shrink-0 ${color}`}>
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
  );
}
