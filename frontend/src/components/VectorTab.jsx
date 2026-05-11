import { useState, useEffect } from 'react';
import { Database, Search, RefreshCw } from 'lucide-react';
import { fetchVectorStats, vectorSearch } from '../api';

export default function VectorTab({ availableFetchers, showToast }) {
  const [vectorStats, setVectorStats] = useState({ total: 0 });
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  const getFetcherName = (id) => {
    const fetcher = availableFetchers.find(f => f.id === id);
    return fetcher ? fetcher.name : id;
  };

  const loadStats = async () => {
    try {
      const data = await fetchVectorStats();
      setVectorStats({ total: data.total_vectors });
    } catch (e) { console.error(e); }
  };

  useEffect(() => { loadStats(); }, []);

  const handleSearch = async () => {
    if (!searchQuery) return;
    setSearching(true);
    try {
      const data = await vectorSearch(searchQuery);
      setSearchResults(data.results || []);
    } catch (e) { showToast(e.message || '网络异常', 'error'); }
    setSearching(false);
  };

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="flex justify-between items-end mb-6">
        <div><h2 className="text-2xl font-bold">向量数据库雷达</h2><p className="text-sm text-slate-500 mt-2">洞察 ChromaDB 状态，并提供语义检索测试窗口。</p></div>
      </div>

      <div className="bg-gradient-to-br from-indigo-500 to-purple-600 p-6 rounded-3xl text-white shadow-lg w-fit pr-20">
        <h4 className="text-indigo-100 font-bold text-sm mb-2 flex items-center"><Database className="w-4 h-4 mr-1.5" /> ChromaDB 挂载块数</h4>
        <div className="text-4xl font-black">{vectorStats.total} <span className="text-lg font-medium opacity-80">Chunks</span></div>
      </div>

      <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100 max-w-4xl">
        <h3 className="font-bold text-lg mb-4 flex items-center"><Search className="w-5 h-5 mr-2 text-blue-500" /> 语义相似度召回测试</h3>
        <div className="flex space-x-3 mb-6">
          <input type="text" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSearch()} placeholder="输入任意自然语言句子进行特征检索..." className="flex-1 bg-slate-50 border border-slate-200 px-4 py-3 rounded-xl outline-none focus:ring-1 focus:ring-blue-500 font-medium text-sm" />
          <button onClick={handleSearch} disabled={searching} className="bg-slate-800 text-white px-8 py-3 rounded-xl font-bold hover:bg-slate-700 transition-all flex justify-center shrink-0">{searching ? <RefreshCw className="w-5 h-5 animate-spin" /> : '发起检索'}</button>
        </div>

        <div className="space-y-4">
          {searchResults.length === 0 && !searching && <div className="text-center py-8 text-slate-400 font-bold bg-slate-50 rounded-xl border-dashed border-2 border-slate-200 text-sm">输入文本探测大模型语义边界</div>}
          {searchResults.map((res, i) => (
            <div key={i} className="bg-slate-50 border border-slate-200 p-5 rounded-2xl relative">
              <div className="absolute top-4 right-4 bg-emerald-100 text-emerald-700 text-xs font-black px-2 py-1 rounded">Score: {res.distance.toFixed(4)}</div>
              <h4 className="font-bold text-slate-800 text-sm mb-2 pr-24 line-clamp-1" title={res.metadata?.title}>{res.metadata?.title || '未知片段'}</h4>
              <div className="text-xs text-slate-500 flex space-x-2 mb-2">
                <span className="bg-white px-2 py-0.5 border border-slate-200 rounded shadow-sm">{res.metadata?.content_type}</span>
                <span className="bg-white px-2 py-0.5 border border-slate-200 rounded shadow-sm">{getFetcherName(res.metadata?.source_id)}</span>
              </div>
              <p className="text-sm text-slate-600 bg-white p-4 rounded-xl border border-slate-100 mt-3 leading-relaxed transition-all">{res.document}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
