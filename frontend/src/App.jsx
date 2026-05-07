import React, { useState, useEffect } from 'react';
import { Database, RefreshCw, CloudDownload, CheckCircle, Clock, Server, Play, AlertCircle, Bot, Activity, CheckSquare, Settings, Search, Trash2, Calendar, X, BarChart2, Plus, ExternalLink, Save, Edit2, Zap, FileText, Link as LinkIcon, Box, ChevronLeft, ChevronRight } from 'lucide-react';

const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://127.0.0.1:8888/api'
  : `http://${window.location.hostname}:8888/api`;

const CUSTOM_LOGO_PATH = '/logo.png';

// ============================================================================
// ✨ 核心组件：高定版双轴交互式日期范围选择器 (无依赖、极简交互)
// ============================================================================
const parseDateStr = (dateStr) => {
  if (!dateStr) return null;
  const [y, m, d] = dateStr.split('-');
  return new Date(parseInt(y, 10), parseInt(m, 10) - 1, parseInt(d, 10));
};

const formatDate = (date) => {
  if (!date) return '';
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
};

const SimpleDateRangePicker = ({ startDate, endDate, onChange, placeholder = "选择起止时间" }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [viewDate, setViewDate] = useState(new Date());
  const [tempStart, setTempStart] = useState(null);
  const [tempEnd, setTempEnd] = useState(null);
  const [hoverDate, setHoverDate] = useState(null);
  const popoverRef = React.useRef();

  useEffect(() => {
    setTempStart(parseDateStr(startDate));
    setTempEnd(parseDateStr(endDate));
    if (startDate && !isOpen) {
      setViewDate(parseDateStr(startDate));
    }
  }, [startDate, endDate, isOpen]);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target)) {
        setIsOpen(false);
        setTempStart(parseDateStr(startDate));
        setTempEnd(parseDateStr(endDate));
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [startDate, endDate]);

  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDay = new Date(year, month, 1).getDay();

  const days = [];
  for (let i = 0; i < firstDay; i++) days.push(null);
  for (let i = 1; i <= daysInMonth; i++) days.push(i);

  const handleDayClick = (day) => {
    if (!day) return;
    const clickedDate = new Date(year, month, day);
    // 第一次点击，或已完成选择后再次点击 -> 重新设定起点
    if (!tempStart || (tempStart && tempEnd)) {
      setTempStart(clickedDate);
      setTempEnd(null);
    } else {
      // 第二次点击 -> 设定终点
      if (clickedDate < tempStart) {
        setTempStart(clickedDate); // 如果点得比起点还早，自动替换起点
      } else {
        setTempEnd(clickedDate);
        setIsOpen(false); // 选完自动优雅闭合
        onChange(formatDate(tempStart), formatDate(clickedDate));
      }
    }
  };

  const isDateEqual = (d1, d2) => d1 && d2 && d1.getFullYear() === d2.getFullYear() && d1.getMonth() === d2.getMonth() && d1.getDate() === d2.getDate();
  const isDateBetween = (target, start, end) => target && start && end && target > start && target < end;

  let displayStr = placeholder;
  if (startDate && endDate) {
    displayStr = startDate === endDate ? startDate : `${startDate.slice(5)} ~ ${endDate.slice(5)}`;
  } else if (startDate) {
    displayStr = `${startDate.slice(5)} ~ 结束点`;
  }

  return (
    <div className="relative w-full" ref={popoverRef}>
      <div
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center justify-between bg-slate-100/70 hover:bg-slate-200/70 rounded px-2.5 py-1.5 cursor-pointer w-full transition-colors border shadow-sm ${isOpen ? 'border-blue-400 bg-blue-50/50' : 'border-transparent hover:border-slate-300'}`}
      >
        <span className={`text-[11px] font-bold truncate ${startDate ? 'text-blue-700' : 'text-slate-400'}`}>
          {displayStr}
        </span>
        {startDate ? (
          <X className="w-3.5 h-3.5 text-slate-400 hover:text-red-500 transition-colors" onClick={(e) => { e.stopPropagation(); onChange('', ''); setTempStart(null); setTempEnd(null); }} />
        ) : (
          <Calendar className={`w-3.5 h-3.5 ${isOpen ? 'text-blue-500' : 'text-slate-400'}`} />
        )}
      </div>

      {isOpen && (
        <div className="absolute top-full left-0 mt-2 bg-white border border-slate-200 rounded-2xl shadow-xl z-50 p-4 w-[260px] animate-in fade-in zoom-in-95 origin-top-left">
          <div className="flex justify-between items-center mb-4 px-1">
            <button onClick={() => setViewDate(new Date(year, month - 1, 1))} className="p-1 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"><ChevronLeft className="w-4 h-4"/></button>
            <span className="text-sm font-extrabold text-slate-800 tracking-wide">{year}年 {month + 1}月</span>
            <button onClick={() => setViewDate(new Date(year, month + 1, 1))} className="p-1 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"><ChevronRight className="w-4 h-4"/></button>
          </div>
          <div className="grid grid-cols-7 gap-y-2 mb-2">
            {['日', '一', '二', '三', '四', '五', '六'].map(d => (
              <div key={d} className="text-[10px] font-bold text-slate-400 text-center">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-y-1" onMouseLeave={() => setHoverDate(null)}>
            {days.map((day, idx) => {
              if (!day) return <div key={idx} className="h-8 w-full"></div>;
              const currentDate = new Date(year, month, day);
              const isStart = isDateEqual(currentDate, tempStart);
              const isEnd = isDateEqual(currentDate, tempEnd);
              const inRange = isDateBetween(currentDate, tempStart, tempEnd || hoverDate);
              const hasRangeForward = tempEnd || (hoverDate && hoverDate > tempStart);

              let cellClass = "w-full h-8 flex items-center justify-center transition-colors";
              let textClass = "w-7 h-7 flex items-center justify-center rounded-lg text-[11.5px] font-medium cursor-pointer transition-all";

              if (isStart && isEnd) {
                textClass += " bg-blue-600 text-white font-bold shadow-md";
              } else if (isStart) {
                cellClass += hasRangeForward ? " bg-blue-50 rounded-l-xl" : "";
                textClass += " bg-blue-600 text-white font-bold shadow-md scale-105";
              } else if (isEnd) {
                cellClass += " bg-blue-50 rounded-r-xl";
                textClass += " bg-blue-600 text-white font-bold shadow-md scale-105";
              } else if (inRange) {
                cellClass += " bg-blue-50";
                textClass += " text-blue-700 font-bold";
              } else {
                textClass += " text-slate-700 hover:bg-slate-100";
              }

              return (
                <div key={idx} className={cellClass} onMouseEnter={() => tempStart && !tempEnd && setHoverDate(currentDate)}>
                  <div onClick={() => handleDayClick(day)} className={textClass}>
                    {day}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
// ============================================================================


export default function App() {
  const [activeTab, setActiveTab] = useState('data');
  const [toast, setToast] = useState({ show: false, message: '', type: 'info' });
  const [logoError, setLogoError] = useState(false);

  const showToast = (message, type = 'info') => {
    const safeMessage = typeof message === 'string' ? message : JSON.stringify(message);
    setToast({ show: true, message: safeMessage, type });
    setTimeout(() => setToast({ show: false, message: '', type: 'info' }), 3000);
  };

  const handleApiError = async (response, defaultErrorMsg) => {
    let msg = defaultErrorMsg;
    try {
      const data = await response.json();
      if (data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch (e) {}
    showToast(msg, 'error');
  };

  // ==================== 0. 全局动态注册中心数据初始化 ====================
  const [availableFetchers, setAvailableFetchers] = useState([]);
  const [fetchConfigs, setFetchConfigs] = useState({});

  useEffect(() => {
    const fetchFetchers = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/fetchers`);
        if (res.ok) {
          const data = await res.json();
          setAvailableFetchers(data);

          const initialConfigs = {};
          data.forEach(f => {
            initialConfigs[f.id] = {};
            (f.parameters || []).forEach(p => {
              initialConfigs[f.id][p.field] = p.default;
            });
          });
          setFetchConfigs(initialConfigs);
        } else {
            await handleApiError(res, "获取抓取器注册表失败");
        }
      } catch(e) { showToast(`网络连接异常，无法获取后端数据。请求地址: ${API_BASE_URL}`, 'error'); }
    };
    fetchFetchers();
  }, []);

  const getFetcherName = (id) => {
    const fetcher = availableFetchers.find(f => f.id === id);
    return fetcher ? fetcher.name : id;
  };

  // ==================== 模块 1: 关系库数据台账 ====================
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [vectorizingId, setVectorizingId] = useState(null);

  const [filters, setFilters] = useState({
      content_type: '',
      source_id: '',
      is_vectorized: '',
      search: '',
      publish_date_start: '',
      publish_date_end: '',
      fetched_date_start: '',
      fetched_date_end: ''
  });

  const [modalState, setModalState] = useState({ isOpen: false, data: null, isEditing: false });
  const [manualAddModal, setManualAddModal] = useState(false);
  const [selectedArticles, setSelectedArticles] = useState(new Set());

  const uniqueContentTypes = [...new Set(articles.map(a => a.content_type).filter(Boolean))];
  const uniqueSourceIds = [...new Set(articles.map(a => a.source_id).filter(Boolean))];

  const fetchArticles = async () => {
    setLoading(true);
    setSelectedArticles(new Set());
    try {
      const queryParams = new URLSearchParams({ limit: 100 });
      if (filters.content_type) queryParams.append('content_type', filters.content_type);
      if (filters.source_id) queryParams.append('source_id', filters.source_id);
      if (filters.is_vectorized !== '') queryParams.append('is_vectorized', filters.is_vectorized);
      if (filters.search) queryParams.append('search', filters.search);

      if (filters.publish_date_start) queryParams.append('publish_date_start', filters.publish_date_start);
      if (filters.publish_date_end) queryParams.append('publish_date_end', filters.publish_date_end);
      if (filters.fetched_date_start) queryParams.append('fetched_date_start', filters.fetched_date_start);
      if (filters.fetched_date_end) queryParams.append('fetched_date_end', filters.fetched_date_end);

      const response = await fetch(`${API_BASE_URL}/articles?${queryParams.toString()}`);
      if (response.ok) {
        const fetchedArticles = await response.json();
        setArticles(fetchedArticles);
      } else {
         await handleApiError(response, "获取文章列表失败");
      }
    } catch (error) { showToast('后端服务未启动或网络错误', 'error'); }
    setLoading(false);
  };

  useEffect(() => {
      if (activeTab === 'data') fetchArticles();
  }, [
      activeTab, filters.content_type, filters.source_id, filters.is_vectorized,
      filters.publish_date_start, filters.publish_date_end,
      filters.fetched_date_start, filters.fetched_date_end
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
    if(!window.confirm(`确定彻底删除选中的 ${selectedArticles.size} 条数据吗？`)) return;
    try {
      const res = await fetch(`${API_BASE_URL}/articles/batch-delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: Array.from(selectedArticles) })
      });
      if(res.ok) { showToast('批量删除成功', 'success'); fetchArticles(); }
      else await handleApiError(res, '批量删除异常');
    } catch(e) { showToast('网络异常', 'error'); }
  };

  const handleBatchVectorize = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/vectorize/batch`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: Array.from(selectedArticles) })
      });
      if(res.ok) {
        const data = await res.json();
        showToast(`成功处理，${data.count} 条记录新建了向量索引`, 'success');
        fetchArticles();
      } else await handleApiError(res, '批量构建异常');
    } catch(e) { showToast('网络异常', 'error'); }
  };

  const handleDeleteArticle = async (id) => {
    if(!window.confirm("确定要彻底删除这条数据吗？")) return;
    try {
      const response = await fetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (response.ok) { showToast('删除成功', 'success'); fetchArticles(); }
      else await handleApiError(response, '删除异常');
    } catch (e) { showToast('网络异常', 'error'); }
  };

  const handleUpdateArticle = async (id, updatedData) => {
    try {
      const response = await fetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updatedData)
      });
      if (response.ok) {
        showToast('数据修改成功', 'success');
        setModalState({ isOpen: false, data: null, isEditing: false }); fetchArticles();
      } else await handleApiError(response, '修改异常');
    } catch (e) { showToast('网络异常', 'error'); }
  };

  const handleManualAddSubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData.entries());

    const payload = {
        id: `manual_${Date.now()}`,
        title: data.title,
        source_url: data.source_url,
        publish_date: data.publish_date || new Date().toISOString(),
        content_type: data.content_type,
        source_id: data.source_id,
        content: data.content,
        extensions_json: data.extensions_json || "{}"
    };

    try {
        const res = await fetch(`${API_BASE_URL}/articles`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast('手工录入成功', 'success');
            setManualAddModal(false);
            fetchArticles();
        } else {
            await handleApiError(res, '录入失败，请确保后端已实现 POST /api/articles');
        }
    } catch(err) { showToast('网络请求异常', 'error'); }
  };

  const handleVectorize = async (id) => {
    setVectorizingId(id);
    try {
      const response = await fetch(`${API_BASE_URL}/vectorize/${encodeURIComponent(id)}`, { method: 'POST' });
      if (response.ok) { showToast('建立索引成功', 'success'); fetchArticles(); }
      else await handleApiError(response, '向量化遭遇异常(404等)');
    } catch (e) { showToast('网络异常', 'error'); }
    setVectorizingId(null);
  };

  const openDetailModal = (article) => {
      setModalState({ isOpen: true, data: article, isEditing: false });
  };

  // ==================== 模块 2&3: 节点与调度 (高密度批量版) ====================
  const [fetchLoading, setFetchLoading] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [selectedFetchers, setSelectedFetchers] = useState([]);
  const [cronExpr, setCronExpr] = useState('0 8 * * *');

  const fetchTasks = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/tasks`);
      if (res.ok) setTasks(await res.json());
    } catch (e) { console.error(e); }
  };

  useEffect(() => { if (activeTab === 'fetch') fetchTasks(); }, [activeTab]);

  const toggleFetcherSelection = (id) => {
    setSelectedFetchers(prev => prev.includes(id) ? prev.filter(fid => fid !== id) : [...prev, id]);
  };

  const handleConfigChange = (fetcherId, field, value) => {
    setFetchConfigs(prev => ({ ...prev, [fetcherId]: { ...prev[fetcherId], [field]: value } }));
  };

  const handleBatchFetch = async () => {
    setFetchLoading(true);
    let successCount = 0;
    for (const fId of selectedFetchers) {
      try {
        const payload = fetchConfigs[fId] || {};
        const res = await fetch(`${API_BASE_URL}/fetch/${fId}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        if (res.ok) successCount++;
        else await handleApiError(res, `[${getFetcherName(fId)}] 抓取失败`);
      } catch (e) { showToast(`[${getFetcherName(fId)}] 网络异常`, 'error'); }
    }
    setFetchLoading(false);
    if (successCount > 0) {
      showToast(`已向 ${successCount} 个节点下发立即抓取指令！`, 'success');
      setSelectedFetchers([]);
    }
  };

  const handleBatchSchedule = async () => {
    let successCount = 0;
    for (const fId of selectedFetchers) {
      try {
        const payload = { fetcher_id: fId, cron_expr: cronExpr, params: fetchConfigs[fId] || {} };
        const res = await fetch(`${API_BASE_URL}/tasks`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        if (res.ok) successCount++;
        else await handleApiError(res, `[${getFetcherName(fId)}] 挂载失败`);
      } catch (e) { showToast('创建任务网络异常', 'error'); }
    }
    if (successCount > 0) {
      showToast(`成功为您挂载了 ${successCount} 个定时轮询计划！`, 'success');
      setSelectedFetchers([]);
      fetchTasks();
    }
  };

  const handleDeleteTask = async (id) => {
    if(!window.confirm("确定移除该定时计划？")) return;
    try {
      const res = await fetch(`${API_BASE_URL}/tasks/${id}`, { method: 'DELETE' });
      if (res.ok) { showToast('已取消该调度任务', 'success'); fetchTasks(); }
      else await handleApiError(res, '删除失败');
    } catch (e) { showToast('网络异常', 'error'); }
  };


  // ==================== 模块 4: 向量雷达可观测 ====================
  const [vectorStats, setVectorStats] = useState({ total: 0 });
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  const fetchVectorStats = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/vector/stats`);
      if (res.ok) setVectorStats({ total: (await res.json()).total_vectors });
    } catch (e) { console.error(e); }
  };

  useEffect(() => { if (activeTab === 'vector') fetchVectorStats(); }, [activeTab]);

  const handleVectorSearch = async () => {
    if (!searchQuery) return;
    setSearching(true);
    try {
      const payload = { query: searchQuery, top_k: 5 };
      const res = await fetch(`${API_BASE_URL}/vector/search`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
      });
      if (res.ok) setSearchResults((await res.json()).results || []);
      else await handleApiError(res, '检索失败');
    } catch (e) { showToast('网络异常', 'error'); }
    setSearching(false);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/40 to-indigo-50/60 text-slate-800 font-sans pb-32">

      <header className="bg-white/80 backdrop-blur-lg border-b border-slate-200/60 shadow-sm px-6 py-4 flex items-center justify-between sticky top-0 z-40">
        <div className="flex items-center space-x-4">
          {!logoError ? (
            <img src={CUSTOM_LOGO_PATH} alt="Logo" className="h-10 w-auto object-contain" onError={() => setLogoError(true)} />
          ) : (
            <div className="bg-blue-600 p-1.5 rounded-xl shadow flex items-center justify-center w-11 h-11"><Bot className="text-white w-6 h-6" /></div>
          )}
          <div>
            <h1 className="text-xl font-extrabold tracking-tight">哆啦美<span className="text-blue-600">·</span>归档中枢</h1>
            <p className="text-[11px] font-medium text-slate-500 flex items-center mt-0.5"><Activity className="w-3 h-3 mr-1 text-emerald-500" /> Dorami Agent Archive</p>
          </div>
        </div>
        <nav className="flex space-x-1 bg-slate-100/80 p-1.5 rounded-xl border border-slate-200/50">
          {[
            { id: 'data', icon: Database, label: '知识台账' },
            { id: 'fetch', icon: CloudDownload, label: '节点与调度' },
            { id: 'vector', icon: BarChart2, label: '向量雷达' }
          ].map(tab => (
            <button key={tab.id} onClick={() => setActiveTab(tab.id)} className={`px-4 py-2 rounded-lg text-sm font-bold transition-all flex items-center ${activeTab === tab.id ? 'bg-white shadow text-blue-700' : 'text-slate-500 hover:text-slate-800'}`}>
              <tab.icon className="w-4 h-4 mr-2" /> {tab.label}
            </button>
          ))}
        </nav>
      </header>

      {toast.show && (
        <div className={`fixed top-24 right-8 px-5 py-4 rounded-xl shadow-2xl flex items-center space-x-3 z-50 text-white transition-all transform animate-in fade-in slide-in-from-top-4 ${toast.type === 'error' ? 'bg-red-500' : 'bg-slate-800'}`}>
          <AlertCircle className="w-5 h-5" />
          <span className="text-sm font-medium">{toast.message}</span>
        </div>
      )}

      <main className="max-w-[1400px] mx-auto px-4 py-8 relative">

        {/* ===================== 1. 归档台账 ===================== */}
        {activeTab === 'data' && (
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
                  <button onClick={fetchArticles} disabled={loading} className="text-sm text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 shadow-sm px-4 py-2 rounded-lg transition-all flex items-center font-bold">
                    <RefreshCw className={`w-4 h-4 mr-2 text-blue-600 ${loading ? 'animate-spin' : ''}`} /> 同步
                  </button>
              </div>
            </div>

            <div className="bg-white/80 p-3 rounded-2xl shadow-sm border border-slate-100 flex items-center">
                 <Search className="w-5 h-5 text-slate-400 ml-2" />
                 <input type="text" placeholder="全局检索文章标题..." value={filters.search} onChange={e => setFilters({...filters, search: e.target.value})} onKeyDown={e => e.key === 'Enter' && fetchArticles()} className="w-full bg-transparent border-none text-sm px-4 py-2 outline-none font-medium" />
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
                          <select value={filters.content_type} onChange={e => setFilters({...filters, content_type: e.target.value})} className="bg-slate-100/50 rounded px-1.5 py-1 text-blue-700 font-bold outline-none text-xs w-full cursor-pointer">
                            <option value="">全部 (All)</option>
                            {uniqueContentTypes.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                       </div>
                    </th>
                    <th className="px-3 py-3 w-36 align-top">
                       <div className="flex flex-col items-start space-y-1.5">
                          <span className="font-bold opacity-70 mb-0.5">数据来源</span>
                          <select value={filters.source_id} onChange={e => setFilters({...filters, source_id: e.target.value})} className="bg-slate-100/50 rounded px-1.5 py-1 text-blue-700 font-bold outline-none text-xs w-full cursor-pointer truncate">
                            <option value="">全部节点 (All)</option>
                            {uniqueSourceIds.map(src => <option key={src} value={src}>{getFetcherName(src)}</option>)}
                          </select>
                       </div>
                    </th>
                    <th className="px-4 py-3 align-top pt-4"><span className="font-bold opacity-70">内容标题 (点击展开详情)</span></th>

                    {/* ✨ 将两个丑陋的 native input 替换为您专属定制的高配悬浮日历 */}
                    <th className="px-3 py-3 w-[160px] align-top">
                       <div className="flex flex-col items-start space-y-1.5">
                          <span className="font-bold opacity-70 mb-0.5">原始发布时间</span>
                          <SimpleDateRangePicker
                              startDate={filters.publish_date_start}
                              endDate={filters.publish_date_end}
                              onChange={(start, end) => setFilters({...filters, publish_date_start: start, publish_date_end: end})}
                              placeholder="选择日期范围"
                          />
                       </div>
                    </th>

                    <th className="px-3 py-3 w-[160px] align-top">
                       <div className="flex flex-col items-start space-y-1.5">
                          <span className="font-bold opacity-70 mb-0.5">中枢收录时间</span>
                          <SimpleDateRangePicker
                              startDate={filters.fetched_date_start}
                              endDate={filters.fetched_date_end}
                              onChange={(start, end) => setFilters({...filters, fetched_date_start: start, fetched_date_end: end})}
                              placeholder="选择日期范围"
                          />
                       </div>
                    </th>

                    <th className="px-3 py-3 w-28 align-top">
                       <div className="flex flex-col items-start space-y-1.5">
                          <span className="font-bold opacity-70 mb-0.5">向量状态</span>
                          <select value={filters.is_vectorized} onChange={e => setFilters({...filters, is_vectorized: e.target.value})} className="bg-slate-100/50 rounded px-1.5 py-1 text-blue-700 font-bold outline-none text-xs w-full cursor-pointer">
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
          </div>
        )}

        {/* --- 详情/编辑 融合模态框 --- */}
        {modalState.isOpen && (
          <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center animate-in fade-in p-4">
            <div className="bg-white rounded-3xl w-full max-w-4xl shadow-2xl flex flex-col max-h-full overflow-hidden">
              <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-center bg-slate-50">
                <div className="flex items-center space-x-3">
                   <h3 className="font-bold text-lg text-slate-800">数据全景档案</h3>
                   <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-700 text-xs font-bold">{modalState.data.content_type}</span>
                </div>
                <div className="flex items-center space-x-2">
                   <button onClick={() => setModalState({...modalState, isEditing: !modalState.isEditing})} className={`px-3 py-1.5 rounded-lg text-sm font-bold flex items-center transition-colors ${modalState.isEditing ? 'bg-amber-100 text-amber-700' : 'bg-slate-200 text-slate-700 hover:bg-slate-300'}`}>
                      {modalState.isEditing ? <X className="w-4 h-4 mr-1"/> : <Edit2 className="w-4 h-4 mr-1"/>}
                      {modalState.isEditing ? '取消编辑' : '进入编辑模式'}
                   </button>
                   <button onClick={() => setModalState({ isOpen: false, data: null, isEditing: false })} className="p-1.5 text-slate-400 hover:text-slate-700 bg-white rounded-lg shadow-sm"><X className="w-5 h-5" /></button>
                </div>
              </div>

              <div className="p-6 overflow-y-auto flex-1 space-y-5 bg-white">
                <div>
                  <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center mb-1"><FileText className="w-3.5 h-3.5 mr-1"/> 文章标题</label>
                  {modalState.isEditing ? (
                    <input type="text" defaultValue={modalState.data.title} id="edit-title" className="w-full p-2.5 bg-slate-50 border border-slate-200 rounded-lg font-bold outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all" />
                  ) : <div className="font-extrabold text-xl text-slate-800 leading-snug">{modalState.data.title}</div>}
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center mb-1"><LinkIcon className="w-3.5 h-3.5 mr-1"/> 原始来源链接 (URL)</label>
                      {modalState.isEditing ? (
                        <input type="text" defaultValue={modalState.data.source_url} id="edit-url" className="w-full p-2 bg-slate-50 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" />
                      ) : (
                          modalState.data.source_url ?
                          <a href={modalState.data.source_url} target="_blank" rel="noreferrer" className="text-sm font-medium text-blue-600 hover:text-blue-800 flex items-center break-all"><ExternalLink className="w-3.5 h-3.5 mr-1 shrink-0"/> {modalState.data.source_url}</a>
                          : <span className="text-sm text-slate-400">无链接</span>
                      )}
                    </div>
                    <div>
                      <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center mb-1"><Calendar className="w-3.5 h-3.5 mr-1"/> 来源节点与收录时间</label>
                      <div className="text-sm font-medium text-slate-700 flex items-center space-x-2">
                          <span className="bg-slate-100 px-2 py-0.5 rounded text-slate-600">{getFetcherName(modalState.data.source_id)}</span>
                          <span className="text-slate-400">|</span>
                          <span className="font-mono">{modalState.data.fetched_date?.replace('T', ' ').substring(0, 19)}</span>
                      </div>
                    </div>
                </div>

                <div>
                  <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center mb-1"><Database className="w-3.5 h-3.5 mr-1"/> 正文核心/摘要 (用于向量检索)</label>
                  {modalState.isEditing ? (
                    <textarea defaultValue={modalState.data.content} id="edit-content" rows="8" className="w-full p-3 bg-slate-50 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500 leading-relaxed" />
                  ) : <div className="text-sm bg-slate-50 p-4 rounded-2xl border border-slate-100 whitespace-pre-wrap leading-relaxed text-slate-700 shadow-inner max-h-64 overflow-y-auto">{modalState.data.content || '无正文内容'}</div>}
                </div>

                <div>
                  <label className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center mb-1"><Box className="w-3.5 h-3.5 mr-1"/> 扩展元数据 (Extensions JSON)</label>
                  {modalState.isEditing ? (
                    <textarea defaultValue={modalState.data.extensions_json} id="edit-extensions" rows="6" className="w-full p-3 bg-slate-800 text-emerald-400 border border-slate-700 rounded-lg font-mono text-xs outline-none focus:ring-1 focus:ring-blue-500" />
                  ) : <pre className="text-xs bg-slate-800 text-emerald-400 p-4 rounded-2xl overflow-x-auto shadow-inner">{JSON.stringify(JSON.parse(modalState.data.extensions_json || '{}'), null, 2)}</pre>}
                </div>
              </div>

              {modalState.isEditing && (
                <div className="p-4 bg-slate-50 border-t border-slate-200 flex justify-end space-x-3">
                  <span className="text-xs text-amber-600 flex items-center mr-auto px-2"><AlertCircle className="w-3.5 h-3.5 mr-1"/> 修改内容后系统将自动抹除旧的向量索引，需重新构建。</span>
                  <button onClick={() => setModalState({...modalState, isEditing: false})} className="px-5 py-2 rounded-lg font-bold text-slate-600 bg-white border border-slate-300 hover:bg-slate-50 transition-colors">取消</button>
                  <button onClick={() => handleUpdateArticle(modalState.data.id, {
                      title: document.getElementById('edit-title').value,
                      source_url: document.getElementById('edit-url').value,
                      content: document.getElementById('edit-content').value,
                      extensions_json: document.getElementById('edit-extensions').value
                    })} className="px-5 py-2 rounded-lg font-bold text-white bg-blue-600 hover:bg-blue-700 flex items-center transition-colors shadow-md">
                    <Save className="w-4 h-4 mr-2" /> 确认保存修改
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* --- 手工录入 独立模态框 --- */}
        {manualAddModal && (
          <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center animate-in fade-in p-4">
            <div className="bg-white rounded-3xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
              <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-center bg-slate-50">
                <h3 className="font-bold text-lg text-slate-800 flex items-center"><Plus className="w-5 h-5 mr-2 text-blue-600"/> 手工录入知识数据</h3>
                <button onClick={() => setManualAddModal(false)} className="text-slate-400 hover:text-slate-700"><X className="w-5 h-5" /></button>
              </div>
              <form onSubmit={handleManualAddSubmit} className="flex flex-col overflow-hidden">
                  <div className="p-6 overflow-y-auto flex-1 space-y-4">
                      <div><label className="text-xs font-bold text-slate-500 mb-1 block">文章标题 *</label><input required name="title" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500"/></div>
                      <div className="grid grid-cols-2 gap-4">
                          <div>
                              <label className="text-xs font-bold text-slate-500 mb-1 block">结构类型 (Content Type) *</label>
                              <input required name="content_type" placeholder="例如: tech_news" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" list="ct-list" />
                              <datalist id="ct-list">{uniqueContentTypes.map(t => <option key={t} value={t} />)}</datalist>
                          </div>
                          <div>
                              <label className="text-xs font-bold text-slate-500 mb-1 block">来源通道 (Source ID) *</label>
                              <input required name="source_id" placeholder="例如: manual_entry" defaultValue="manual_entry" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" list="src-list" />
                              <datalist id="src-list">{uniqueSourceIds.map(t => <option key={t} value={t} />)}</datalist>
                          </div>
                      </div>
                      <div><label className="text-xs font-bold text-slate-500 mb-1 block">文章链接 (URL)</label><input name="source_url" type="url" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500"/></div>
                      <div><label className="text-xs font-bold text-slate-500 mb-1 block">发布时间 (ISO格式，留空为当前)</label><input name="publish_date" type="datetime-local" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500"/></div>
                      <div><label className="text-xs font-bold text-slate-500 mb-1 block">核心正文/摘要</label><textarea required name="content" rows="4" className="w-full p-3 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500"/></div>
                      <div><label className="text-xs font-bold text-slate-500 mb-1 block">任意扩展元数据 (严格的 JSON 格式)</label><textarea name="extensions_json" defaultValue='{}' rows="4" className="w-full p-3 border border-slate-200 rounded-lg text-sm font-mono bg-slate-50 outline-none focus:border-blue-500"/></div>
                  </div>
                  <div className="p-5 bg-slate-50 border-t border-slate-200 flex justify-end">
                      <button type="submit" className="px-6 py-2.5 bg-blue-600 text-white font-bold rounded-xl hover:bg-blue-700 shadow-md transition-all">确认写入数据库</button>
                  </div>
              </form>
            </div>
          </div>
        )}

        {/* ===================== 2&3. 节点与调度 (高密度批量重构版) ===================== */}
        {activeTab === 'fetch' && (
          <div className="space-y-6 animate-in fade-in">
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
               <div>
                 <h2 className="text-2xl font-bold flex items-center"><Server className="w-6 h-6 mr-2 text-indigo-500"/> 抓取节点与自动化调度</h2>
                 <p className="text-sm text-slate-500 mt-2">勾选节点唤起批量指挥台，可直接在卡片上修改参数实现一键下发。</p>
               </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {availableFetchers.length === 0 ? <div className="col-span-full py-10 text-center text-slate-400 font-bold border-2 border-dashed border-slate-200 rounded-2xl">未探测到可用节点</div> : null}
              {availableFetchers.map(fetcher => {
                const isSelected = selectedFetchers.includes(fetcher.id);
                const hasTask = tasks.some(t => t.fetcher_id === fetcher.id);
                return (
                <div key={fetcher.id} className={`bg-white border-2 rounded-2xl flex flex-col transition-all group shadow-sm ${isSelected ? 'border-blue-500 ring-4 ring-blue-500/10' : 'border-slate-200 hover:border-blue-300'}`}>

                   {/* 头部区域 (可点击勾选) */}
                   <div onClick={() => toggleFetcherSelection(fetcher.id)} className="p-4 flex items-start gap-3 cursor-pointer border-b border-slate-100 bg-slate-50/50 rounded-t-xl hover:bg-slate-100/70 transition-colors">
                       <div className={`mt-1.5 w-5 h-5 shrink-0 rounded border flex items-center justify-center transition-colors ${isSelected ? 'bg-blue-600 border-blue-600' : 'border-slate-300 group-hover:border-blue-400'}`}>
                           {isSelected && <CheckSquare className="w-4 h-4 text-white" />}
                       </div>
                       <div className="w-11 h-11 shrink-0 bg-white border border-slate-200 rounded-xl flex items-center justify-center text-2xl shadow-sm">{fetcher.icon}</div>
                       <div className="flex-1 min-w-0">
                          <h3 className="font-extrabold text-slate-800 text-sm leading-snug">{fetcher.name}</h3>
                          <div className="flex items-center space-x-2 mt-1.5">
                             {hasTask && <Clock className="w-3.5 h-3.5 text-emerald-500 shrink-0" title="已有定时任务"/>}
                             <span className="text-[10px] text-slate-500 font-mono bg-slate-200/50 px-1.5 py-0.5 rounded truncate">{fetcher.id}</span>
                          </div>
                       </div>
                   </div>

                   {/* 参数主体区域 (直接外露) */}
                   <div className="p-4 bg-white rounded-b-xl flex-1 flex flex-col justify-center space-y-3">
                       {fetcher.parameters?.length > 0 ? (
                           fetcher.parameters.map(param => (
                               <div key={param.field} className="flex items-center justify-between gap-3">
                                   <label className="text-xs font-bold text-slate-500 truncate" title={param.label}>{param.label}</label>
                                   <input
                                       type={param.type || 'text'}
                                       value={(fetchConfigs[fetcher.id] && fetchConfigs[fetcher.id][param.field]) ?? param.default ?? ''}
                                       onChange={(e) => handleConfigChange(fetcher.id, param.field, e.target.value)}
                                       className="w-1/2 max-w-[140px] px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg focus:ring-1 focus:ring-blue-500 outline-none text-xs font-bold text-slate-700 transition-all text-right"
                                   />
                               </div>
                           ))
                       ) : (
                           <div className="text-xs text-slate-400 font-medium italic text-center py-2">无需扩展参数</div>
                       )}
                   </div>

                </div>
              )})}
            </div>

            {/* 当前活跃任务台账 */}
            <div className="mt-8 bg-white rounded-3xl shadow-sm border border-slate-200 overflow-hidden">
               <div className="bg-slate-50 px-5 py-4 border-b border-slate-200 flex items-center">
                  <Calendar className="w-5 h-5 text-emerald-600 mr-2"/>
                  <h3 className="font-bold text-slate-700 text-sm">正在后台巡检的自动化计划</h3>
               </div>
               <table className="w-full text-left">
                 <thead className="bg-white text-[11px] font-bold text-slate-400 uppercase border-b border-slate-100">
                   <tr><th className="p-4">数据节点</th><th className="p-4">Cron 频率</th><th className="p-4">执行参数</th><th className="p-4 text-right">管理</th></tr>
                 </thead>
                 <tbody className="divide-y divide-slate-50 text-sm">
                   {tasks.length === 0 ? <tr><td colSpan="4" className="p-8 text-center text-slate-400 text-sm font-medium">当前无自动调度任务</td></tr> : tasks.map(t => (
                     <tr key={t.id} className="hover:bg-slate-50 transition-colors">
                       <td className="p-4 font-bold text-slate-700 flex items-center"><span className="w-2 h-2 rounded-full bg-emerald-400 mr-2 animate-pulse shadow-[0_0_8px_rgba(52,211,153,0.8)]"></span> {getFetcherName(t.fetcher_id)}</td>
                       <td className="p-4"><span className="font-mono bg-slate-100 border border-slate-200 px-2.5 py-1 rounded text-slate-600 text-xs">{t.cron_expr}</span></td>
                       <td className="p-4 text-xs text-slate-500 max-w-xs truncate" title={t.params_json}>{t.params_json}</td>
                       <td className="p-4 text-right"><button onClick={()=>handleDeleteTask(t.id)} className="text-red-500 hover:text-red-700 font-bold text-xs bg-red-50 hover:bg-red-100 px-3 py-1.5 rounded-lg transition-colors">移除任务</button></td>
                     </tr>
                   ))}
                 </tbody>
               </table>
            </div>

            {/* 选定节点后自动升起的【悬浮批量指挥台】 */}
            {selectedFetchers.length > 0 && (
              <div className="fixed bottom-0 left-0 w-full bg-white/90 backdrop-blur-xl border-t border-slate-200 p-4 z-40 flex justify-center items-center shadow-[0_-10px_40px_-15px_rgba(0,0,0,0.1)] animate-in slide-in-from-bottom-full">
                 <div className="max-w-7xl w-full flex flex-col md:flex-row justify-between items-center px-6 gap-4">
                    <span className="font-extrabold text-blue-700 flex items-center bg-blue-50 px-4 py-2 rounded-xl"><CheckSquare className="w-5 h-5 mr-2"/> 已蓄势 {selectedFetchers.length} 个抓取节点</span>

                    <div className="flex items-center space-x-4">
                       <div className="flex items-center space-x-2 border-r border-slate-200 pr-4">
                          <Clock className="w-4 h-4 text-emerald-600" />
                          <input type="text" value={cronExpr} onChange={e=>setCronExpr(e.target.value)} className="w-28 text-sm px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg font-mono outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all text-emerald-700 font-bold" placeholder="0 8 * * *"/>
                          <button onClick={handleBatchSchedule} className="px-5 py-2.5 bg-emerald-600 text-white font-bold rounded-xl hover:bg-emerald-700 text-sm flex items-center shadow-md transition-all">
                             <Calendar className="w-4 h-4 mr-1.5"/> 批量挂载定时
                          </button>
                       </div>
                       <button onClick={handleBatchFetch} disabled={fetchLoading} className="px-6 py-2.5 bg-blue-600 text-white font-extrabold rounded-xl hover:bg-blue-700 text-sm flex items-center shadow-md transition-all">
                          {fetchLoading ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin"/> : <Play className="w-4 h-4 mr-1.5 fill-current"/>} {fetchLoading ? '指挥执行中...' : '立即批量抓取'}
                       </button>
                    </div>
                 </div>
              </div>
            )}
          </div>
        )}

        {/* ===================== 4. 向量雷达可观测 ===================== */}
        {activeTab === 'vector' && (
           <div className="space-y-6 animate-in fade-in">
             <div className="flex justify-between items-end mb-6">
                <div><h2 className="text-2xl font-bold">向量数据库雷达</h2><p className="text-sm text-slate-500 mt-2">洞察 ChromaDB 状态，并提供语义检索测试窗口。</p></div>
             </div>

             <div className="bg-gradient-to-br from-indigo-500 to-purple-600 p-6 rounded-3xl text-white shadow-lg w-fit pr-20">
                 <h4 className="text-indigo-100 font-bold text-sm mb-2 flex items-center"><Database className="w-4 h-4 mr-1.5"/> ChromaDB 挂载块数</h4>
                 <div className="text-4xl font-black">{vectorStats.total} <span className="text-lg font-medium opacity-80">Chunks</span></div>
             </div>

             <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-100 max-w-4xl">
               <h3 className="font-bold text-lg mb-4 flex items-center"><Search className="w-5 h-5 mr-2 text-blue-500" /> 语义相似度召回测试</h3>
               <div className="flex space-x-3 mb-6">
                 <input type="text" value={searchQuery} onChange={e=>setSearchQuery(e.target.value)} onKeyDown={e=>e.key==='Enter'&&handleVectorSearch()} placeholder="输入任意自然语言句子进行特征检索..." className="flex-1 bg-slate-50 border border-slate-200 px-4 py-3 rounded-xl outline-none focus:ring-1 focus:ring-blue-500 font-medium text-sm" />
                 <button onClick={handleVectorSearch} disabled={searching} className="bg-slate-800 text-white px-8 py-3 rounded-xl font-bold hover:bg-slate-700 transition-all flex justify-center shrink-0">{searching ? <RefreshCw className="w-5 h-5 animate-spin" /> : '发起检索'}</button>
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
        )}

      </main>
    </div>
  );
}