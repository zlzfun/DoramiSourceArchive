import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { RefreshCw, Zap, Search, Plus, Trash2, Edit2, ChevronDown } from 'lucide-react';
import DateRangePicker from './DateRangePicker';
import ArticleDetailModal from './ArticleDetailModal';
import ArticleDetailDrawer from './ArticleDetailDrawer';
import ManualAddModal from './ManualAddModal';
import {
  fetchArticles as apiFetchArticles,
  fetchArticle,
  fetchArticleFacets,
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
import { contentTypeLabel, CONTENT_TYPE_GROUPS } from '../utils/contentType';
import { useConfirm } from '../hooks/useConfirm';
import { useAbortableLoad } from '../hooks/useAbortableLoad';

const ARTICLE_PAGE_SIZE = 30;

// 总账条（RAG 开）：每格 = 一个 index_status，点击即按该状态筛选表格。
const STAT_DEFS = [
  { key: 'all', label: '总收录', tone: '' },
  { key: 'indexed', label: '已入索引', tone: 'is-ok' },
  { key: 'pending', label: '待处理', tone: '' },
  { key: 'indexing', label: '索引中', tone: 'is-run' },
  { key: 'failed', label: '失败', tone: 'is-bad' },
  { key: 'stale', label: '陈旧', tone: 'is-warn' },
];

// 总账条（RAG 关）：向量索引维度失去意义，退化为三格收录量看板，
// 点击即应用对应 fetched_date 快捷区间（quick=setFetchedQuick 的键）。
const PLAIN_STAT_DEFS = [
  { key: 'all', quick: 'all', label: '总收录', tone: '' },
  { key: 'today', quick: '1', label: '今日收录', tone: '' },
  { key: 'week', quick: '7', label: '近 7 天收录', tone: '' },
];

// 索引状态 → .stamp 范式（淡底+深字+形状点，见 index.css .stamp-*）。
const INDEX_STAMP = {
  indexed: { cls: 'stamp-ok', label: '已入索引' },
  pending: { cls: 'stamp-idle', label: '待索引' },
  indexing: { cls: 'stamp-run', label: '索引中' },
  failed: { cls: 'stamp-bad', label: '失败' },
  stale: { cls: 'stamp-warn', label: '陈旧' },
};

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
  // 表格密度:舒适(含摘录行)/紧凑(仅标题,44px 行高)。持久化偏好。
  const [density, setDensity] = useState(() => localStorage.getItem('dorami-ledger-density') || 'comfortable');
  const setDensityPersist = (d) => { setDensity(d); localStorage.setItem('dorami-ledger-density', d); };
  // 总账条计数（全局概览，不随分面筛选变化；见 loadLedgerStats）。
  const [ledgerStats, setLedgerStats] = useState(null);
  // 分面目录：{total, content_types:[{value,count}], source_ids:[{value,count}]}（计数降序）。
  // 台账分面栏的单一数据源——全量 group-by，挂载拉取一次，增删/录入后随列表刷新。
  const [facets, setFacets] = useState(null);
  // 详情抽屉：查看 + 快捷操作（编辑仍走 ArticleDetailModal）。
  const [drawer, setDrawer] = useState({ open: false, article: null });
  // 整刷一次自增（loadArticles），作为 tbody 的 key：查询/筛选/分页一变即整体重挂载。
  const [listVersion, setListVersion] = useState(0);

  // 搜索是提交式：searchInput 为输入框即时值（不触发加载），appliedSearch 为已提交值。
  const [searchInput, setSearchInput] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [filters, setFilters] = useState({
    content_types: '', // CSV：类型归组分面下发的多类型筛选（单类型组也走 CSV）
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

  // 内容类型分面：按 CONTENT_TYPE_GROUPS 归组（组内计数求和），未归组类型落「其他」；
  // count=0 的组不显示。选项/计数均来自 facets（全量 group-by），不从当前页推导。
  const typeGroups = useMemo(() => {
    const counts = new Map((facets?.content_types ?? []).map(c => [c.value, c.count]));
    const claimed = new Set();
    const grouped = [];
    for (const g of CONTENT_TYPE_GROUPS) {
      let sum = 0;
      for (const t of g.types) { if (counts.has(t)) sum += counts.get(t); claimed.add(t); }
      if (sum > 0) grouped.push({ label: g.label, csv: g.types.join(','), count: sum });
    }
    let otherSum = 0;
    const otherTypes = [];
    for (const [value, count] of counts) {
      if (!claimed.has(value)) { otherSum += count; otherTypes.push(value); }
    }
    if (otherSum > 0) grouped.push({ label: '其他', csv: otherTypes.join(','), count: otherSum });
    return grouped;
  }, [facets]);

  // 来源分面：取消厂商分组，扁平列表按计数降序（后端已降序），显示 fetcher 名 + 计数。
  const sourceFacets = facets?.source_ids ?? [];

  // 来源分面标题截断检测:只有名字真被省略号截断的行,才在 hover 停留后隐去计数、
  // 让标题占满整行(motion 服务于信息获取,非装饰;未截断的行不参与,计数保持稳定)。
  const sourceListRef = useRef(null);
  useEffect(() => {
    const el = sourceListRef.current;
    if (!el) return;
    const measure = () => {
      el.querySelectorAll('.ledger-facet-item').forEach(btn => {
        const name = btn.querySelector('.ledger-facet-name');
        if (!name) return;
        const clipped = name.scrollWidth > name.clientWidth + 1;
        btn.classList.toggle('is-clipped', clipped);
        // 揭示后可用宽 = 按钮内宽(计数已让位);仍不够的部分作为滑移距离,
        // CSS 侧沿用同一延时把文字整体左移,尾部进入可视区。
        const available = btn.clientWidth - 18; // padding 9px × 2
        const shift = clipped ? Math.max(0, name.scrollWidth - available + 2) : 0;
        btn.style.setProperty('--reveal-shift', `-${shift}px`);
      });
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [facets, fetchersById]);

  // 手工录入的 datalist 选项：类型/来源同样吃 facets（全量目录），并入已注册 fetcher。
  const manualContentTypes = useMemo(() => (facets?.content_types ?? []).map(c => c.value), [facets]);
  const uniqueSourceIds = useMemo(() => [...new Set([
    ...availableFetchers.map(f => f.id).filter(Boolean),
    ...(facets?.source_ids ?? []).map(s => s.value),
  ])], [availableFetchers, facets]);

  const canSelectArticles = canManageArticles;
  // 向量化「动作」(自动开关/全量/重建/单条构建)与索引状态列都依赖 RAG:
  // 向量子系统关闭时相关端点 503,列直接隐藏,总账条退化为收录量三格。
  const showVectorActions = canManageArticles && ragEnabled;
  const totalPages = Math.max(1, Math.ceil((articlePageInfo.total || 0) / ARTICLE_PAGE_SIZE));
  const canGoPrev = currentPage > 1 && !loading;
  const canGoNext = currentPage < totalPages && !loading;
  const activeStatus = filters.index_status || 'all';

  // 页码窗口：首页、当前±1、末页；间断处以省略号占位（.pager 范式）。
  const pageWindow = useMemo(() => {
    const wanted = [1, currentPage - 1, currentPage, currentPage + 1, totalPages]
      .filter(p => p >= 1 && p <= totalPages);
    const uniq = [...new Set(wanted)].sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    for (const p of uniq) {
      if (p - prev > 1) out.push({ ellipsis: true, key: `e${p}` });
      out.push({ page: p, key: p });
      prev = p;
    }
    return out;
  }, [currentPage, totalPages]);

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

  // 总账条计数：全局概览（仅排除日报源），与分面筛选无关。挂载时 + 文章增删/向量化后刷新。
  //  · RAG 开：总收录 + 5 个 index_status 计数（6 格）。
  //  · RAG 关：向量维度失效，只查总收录 / 今日 / 近 7 天（后两格用 fetched_date 快捷区间），
  //    5 个状态计数不再发起。
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
    if (showVectorActions) {
      const [total, indexed, pending, indexing, failed, stale] = await Promise.all([
        countFor({}),
        countFor({ index_status: 'indexed' }),
        countFor({ index_status: 'pending' }),
        countFor({ index_status: 'indexing' }),
        countFor({ index_status: 'failed' }),
        countFor({ index_status: 'stale' }),
      ]);
      setLedgerStats({ total, indexed, pending, indexing, failed, stale });
      return;
    }
    const day1 = quickFetchedRange(1);
    const day7 = quickFetchedRange(7);
    const [total, today, week] = await Promise.all([
      countFor({}),
      countFor({ fetched_date_start: day1.start, fetched_date_end: day1.end }),
      countFor({ fetched_date_start: day7.start, fetched_date_end: day7.end }),
    ]);
    setLedgerStats({ total, today, week });
  }, [showVectorActions]);

  const loadFacets = useCallback(async () => {
    try {
      const d = await fetchArticleFacets({ exclude_source_ids: 'dorami_daily_brief' });
      setFacets(d);
    } catch { /* 分面加载失败不阻断列表 */ }
  }, []);

  const refreshAfterMutation = useCallback((page) => {
    loadArticles(page);
    loadLedgerStats();
    loadFacets();
  }, [loadArticles, loadLedgerStats, loadFacets]);

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
    loadFacets();
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

  // 分面目录：挂载拉取一次（增删/录入后随 refreshAfterMutation 刷新）。
  useEffect(() => { loadFacets(); }, [loadFacets]);

  // 自动向量化开关：读取当前配置（仅管理员 + RAG 开启）。
  useEffect(() => {
    if (!showVectorActions) return;
    let alive = true;
    getAutoVectorize().then(d => { if (alive) setAutoVec(Boolean(d.enabled)); }).catch(() => {});
    return () => { alive = false; };
  }, [showVectorActions]);

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
      content_types: '',
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

  // 行内「编辑」：列表项无全文，编辑模态需完整记录才可进编辑态——先拉详情再打开。
  const handleEditRow = async (article) => {
    let detail;
    try {
      detail = await fetchArticle(article.id);
    } catch (e) {
      showToast(e.message || '获取文章详情失败', 'error');
      return;
    }
    setModalState({ isOpen: true, data: { ...article, ...detail }, isEditing: true });
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
                onClick={() => setFilters(prev => ({ ...prev, content_types: '' }))}
                className={`ledger-facet-item ${!filters.content_types ? 'is-on' : ''}`}
              >
                全部类型
              </button>
              {typeGroups.map(g => (
                <button
                  key={g.label}
                  type="button"
                  onClick={() => setFilters(prev => ({ ...prev, content_types: g.csv }))}
                  className={`ledger-facet-item ${filters.content_types === g.csv ? 'is-on' : ''}`}
                  title={g.csv}
                >
                  <span className="ledger-facet-name">{g.label}</span>
                  <span className="n">{g.count.toLocaleString()}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="ledger-facet">
            <h3 className="micro-label ledger-facet-title">来源</h3>
            <div className="ledger-facet-list ledger-facet-scroll" ref={sourceListRef}>
              <button
                type="button"
                onClick={() => setFilters(prev => ({ ...prev, source_id: '' }))}
                className={`ledger-facet-item ${!filters.source_id ? 'is-on' : ''}`}
              >
                全部节点
              </button>
              {sourceFacets.map(s => (
                <button
                  key={s.value}
                  type="button"
                  onClick={() => setFilters(prev => ({ ...prev, source_id: s.value }))}
                  className={`ledger-facet-item ${filters.source_id === s.value ? 'is-on' : ''}`}
                  title={s.value}
                >
                  <span className="ledger-facet-name">{getFetcherName(s.value)}</span>
                  <span className="n">{s.count.toLocaleString()}</span>
                </button>
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
              <DateRangePicker compact
                startDate={filters.fetched_date_start}
                endDate={filters.fetched_date_end}
                onChange={(start, end) => setFilters({ ...filters, fetched_date_start: start, fetched_date_end: end })}
                placeholder="自定义收录区间"
              />
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
              <>
                <h3 className="micro-label ledger-facet-title mt-2">原始发布日期</h3>
                <div className="ledger-facet-range">
                  <DateRangePicker compact
                    startDate={filters.publish_date_start}
                    endDate={filters.publish_date_end}
                    onChange={(start, end) => setFilters({ ...filters, publish_date_start: start, publish_date_end: end })}
                    placeholder="自定义发布区间"
                  />
                </div>
              </>
            )}
          </div>
        </aside>

        {/* 主纸：总账条 + 表格 + 批量条 + 表脚 */}
        <div className="ledger-paper surface-card">
          <div className="ledger-strip" role="group" aria-label={showVectorActions ? '索引状态总览与筛选' : '收录量总览与筛选'}>
            <div className="ledger-strip-stats">
              {(showVectorActions ? STAT_DEFS : PLAIN_STAT_DEFS).map(stat => {
                // RAG 开：格 = index_status 筛选；RAG 关：格 = fetched_date 快捷区间筛选。
                const on = showVectorActions
                  ? activeStatus === stat.key
                  : activeFetchedQuick === stat.quick;
                const onClickStat = () => (showVectorActions
                  ? setFilters(prev => ({ ...prev, index_status: stat.key === 'all' ? '' : stat.key }))
                  : setFetchedQuick(stat.quick));
                return (
                  <button
                    key={stat.key}
                    type="button"
                    onClick={onClickStat}
                    className={`ledger-stat ${on ? 'is-on' : ''}`}
                    aria-pressed={on}
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
                );
              })}
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

          <div className="ledger-table-scroll">
            <table className={`ledger-table w-full min-w-[980px] text-left ${density === 'compact' ? 'is-compact' : ''}`}>
              <colgroup>
                {canSelectArticles && <col className="ledger-col-select" />}
                <col className="ledger-col-title" />
                <col className="ledger-col-source" />
                <col className="ledger-col-type" />
                <col className="ledger-col-publish" />
                <col className="ledger-col-publish" />
                {showVectorActions && <col className="ledger-col-vector" />}
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
                  {showVectorActions && <th className="ledger-th px-3">索引状态</th>}
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
                      {showVectorActions && <td className="px-3"><div className="skeleton h-6 w-20 rounded-full" /></td>}
                      <td className="px-3" />
                    </tr>
                  ))
                ) : articles.length === 0 ? (
                  <tr><td colSpan={2 + (canSelectArticles ? 1 : 0) + (showVectorActions ? 1 : 0) + 4} className="px-6 py-16 text-center font-medium text-slate-500">当前筛选条件下未查询到相关数据，试试放宽时间区间或清除筛选</td></tr>
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
                        <div className="ledger-tt">{article.title}</div>
                        <div className="ledger-ex">{excerptOf(article.content_preview || article.content) || '暂无摘要内容'}</div>
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
                      {showVectorActions && (
                        <td className="px-3" onClick={e => e.stopPropagation()}>
                          {status === 'indexed' ? (
                            <span className="stamp stamp-ok">已入索引</span>
                          ) : (() => {
                            // 非 indexed 态 = 可点构建章（busy 禁用、文案「构建中」）。
                            const def = INDEX_STAMP[status] || INDEX_STAMP.pending;
                            return (
                              <button
                                type="button"
                                onClick={() => handleVectorize(article.id)}
                                disabled={busy}
                                className={`stamp ${busy ? 'stamp-run' : def.cls}`}
                                title={busy ? '构建中' : `点击构建向量索引（当前：${def.label}）`}
                              >
                                {busy ? '构建中' : def.label}
                              </button>
                            );
                          })()}
                        </td>
                      )}
                      <td className="px-3" onClick={e => e.stopPropagation()}>
                        <div className="ledger-rowacts">
                          {showVectorActions && (
                            <button
                              type="button"
                              className="ledger-iconbtn"
                              title="向量化"
                              aria-label={`向量化：${article.title || article.id}`}
                              disabled={busy}
                              onClick={() => handleVectorize(article.id)}
                            >
                              {busy ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
                            </button>
                          )}
                          <button
                            type="button"
                            className="ledger-iconbtn"
                            title="编辑"
                            aria-label={`编辑：${article.title || article.id}`}
                            onClick={() => handleEditRow(article)}
                          >
                            <Edit2 className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            className="ledger-iconbtn is-danger"
                            title="删除"
                            aria-label={`删除：${article.title || article.id}`}
                            onClick={() => handleDeleteSingle(article)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
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
              {showVectorActions && (
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
                共 {(articlePageInfo.total || 0).toLocaleString()} 条 · 每页 {ARTICLE_PAGE_SIZE} 条
              </span>
              <div className="mini-seg" role="group" aria-label="表格密度">
                {[['comfortable', '舒适'], ['compact', '紧凑']].map(([d, label]) => (
                  <button key={d} type="button" onClick={() => setDensityPersist(d)}
                    className={`mini-seg-btn ${density === d ? 'is-on' : ''}`} aria-pressed={density === d}>
                    {label}
                  </button>
                ))}
              </div>
              <div className="pager">
                <button type="button" onClick={() => setCurrentPage(page => Math.max(1, page - 1))} disabled={!canGoPrev} className="pager-btn" aria-label="上一页">«</button>
                {pageWindow.map(item => (item.ellipsis ? (
                  <span key={item.key} className="pager-ellipsis">…</span>
                ) : (
                  <button
                    type="button"
                    key={item.key}
                    onClick={() => setCurrentPage(item.page)}
                    disabled={loading}
                    className={`pager-btn ${item.page === currentPage ? 'is-on' : ''}`}
                    aria-current={item.page === currentPage ? 'page' : undefined}
                  >
                    {item.page}
                  </button>
                )))}
                <button type="button" onClick={() => setCurrentPage(page => Math.min(totalPages, page + 1))} disabled={!canGoNext} className="pager-btn" aria-label="下一页">»</button>
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
        uniqueContentTypes={manualContentTypes}
        uniqueSourceIds={uniqueSourceIds}
        onClose={() => setManualAddModal(false)}
        onSubmit={handleManualAddSubmit}
      />
    </div>
  );
}
