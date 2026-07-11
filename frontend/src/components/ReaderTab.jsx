import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Search,
  Plus,
  Minus,
  ExternalLink,
  ChevronDown,
  Loader2,
  Inbox,
  Compass,
  BookOpenText,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Star,
  Languages,
} from 'lucide-react';
import LogoMark from './LogoMark';
import ReaderMarkdown from './ReaderMarkdown';
import ReaderAiPanel from './ReaderAiPanel';
import { resolveCompany } from '../sourceTaxonomy';
import { excerptOf } from '../utils/readerText';
import { formatRelativeTime, formatDateTime } from '../utils/datetime';
import { contentTypeLabel } from '../utils/contentType';
import { useAbortableLoad } from '../hooks/useAbortableLoad';
import {
  fetchReaderSources,
  fetchArticles,
  fetchArticle,
  subscribeSource,
  unsubscribeSource,
  fetchFavorites,
  addFavorite,
  removeFavorite,
  translateArticle,
  recordArticleRead,
} from '../api';

const PAGE_SIZE = 30;

// ── 骨架屏 · 大块加载态形状占位 ──
// 形状贴近真实内容，替代居中 spinner；条数固定、宽度错落，纯装饰故 aria-hidden。

// 侧栏来源行：图标块 + 名称条 + 篇数条
function SourceRowsSkeleton() {
  const nameWidths = ['w-3/4', 'w-2/3', 'w-4/5', 'w-1/2', 'w-3/5'];
  return (
    <div className="reader-group-body" aria-hidden="true">
      {nameWidths.map((w, i) => (
        <div key={i} className="flex items-center gap-2.5 px-2.5 py-2">
          <div className="skeleton h-7 w-7 rounded-[var(--r-control)]" />
          <div className="min-w-0 flex-1">
            <div className={`skeleton h-3.5 ${w}`} />
            <div className="skeleton mt-1.5 h-2.5 w-10" />
          </div>
        </div>
      ))}
    </div>
  );
}

// 文章卡：标题条 + 摘要两行 + foot 短条（形状贴近 .reader-article-card）
function ArticleCardsSkeleton() {
  const cards = [
    { title: 'w-3/4', excerpt: 'w-1/2' },
    { title: 'w-5/6', excerpt: 'w-2/3' },
    { title: 'w-2/3', excerpt: 'w-3/5' },
    { title: 'w-4/5', excerpt: 'w-1/2' },
    { title: 'w-3/5', excerpt: 'w-2/3' },
  ];
  return (
    <div aria-hidden="true">
      {cards.map((c, i) => (
        <div key={i} className="px-3.5 py-3">
          <div className={`skeleton h-3.5 ${c.title}`} />
          <div className="skeleton mt-2.5 h-3 w-full" />
          <div className={`skeleton mt-1.5 h-3 ${c.excerpt}`} />
          <div className="skeleton mt-2.5 h-2.5 w-16" />
        </div>
      ))}
    </div>
  );
}

// 阅读窗格正文：若干段落条（真实 meta/标题已在 header 中渲染，此处只占正文位）
function PaneBodySkeleton() {
  const lines = ['w-full', 'w-full', 'w-11/12', 'w-full', 'w-4/5', 'w-full', 'w-full', 'w-2/3'];
  return (
    <div aria-hidden="true">
      {lines.map((w, i) => (
        <div key={i} className={`skeleton h-4 ${w} ${i > 0 ? 'mt-3' : ''}`} />
      ))}
    </div>
  );
}

export default function ReaderTab({ showToast, aiEnabled = false }) {
  const [sources, setSources] = useState([]);
  const [subscribedIds, setSubscribedIds] = useState(() => new Set());
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [activeSourceId, setActiveSourceId] = useState(null); // null = 「我的订阅」聚合
  const [showFavorites, setShowFavorites] = useState(false); // true = 「我的收藏」视图
  const [favoriteIds, setFavoriteIds] = useState(() => new Set());
  const [favTogglingId, setFavTogglingId] = useState(null);
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [sourcesCollapsed, setSourcesCollapsed] = useState(false);
  const [listCollapsed, setListCollapsed] = useState(false);

  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  const [articles, setArticles] = useState([]);
  const [articlesTotal, setArticlesTotal] = useState(0);
  const [articlesLoading, setArticlesLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [activeArticle, setActiveArticle] = useState(null);  // 轻量列表项（meta/标题/收藏态即时渲染）
  const [activeBody, setActiveBody] = useState(null);        // 选中文章的全文正文（按需拉取）
  const [activeBodyLoading, setActiveBodyLoading] = useState(false);
  const [pinningId, setPinningId] = useState(null);

  // ── 用户面 AI · 译为中文（问答浮层已抽到 ReaderAiPanel，自持其状态）──
  const [showTranslation, setShowTranslation] = useState(false);  // 右栏是否展示译文
  const [translating, setTranslating] = useState(false);
  const [translatedBody, setTranslatedBody] = useState(null);
  const translationCacheRef = useRef(new Map());                  // id → 译文

  const listRef = useRef(null);
  // 正文缓存（id → content）+ 「最新选中 id」防竞态：快速连点时丢弃晚到的过期正文响应
  const bodyCacheRef = useRef(new Map());
  const activeIdRef = useRef(null);
  // 列表加载的竞态安全器：切源/搜索时慢的旧请求若晚返回会「后发先至」覆盖当前源列表，
  // runList 发新请求前 abort 掉旧的（与 DataTab 同一约定）。
  const runList = useAbortableLoad();

  // ── 源目录 ──
  const loadSources = useCallback(async () => {
    setSourcesLoading(true);
    try {
      const data = await fetchReaderSources();
      setSources(data.sources || []);
      setSubscribedIds(new Set(data.subscribed_source_ids || []));
    } catch (error) {
      showToast(error.message || '获取内容源目录失败', 'error');
    } finally {
      setSourcesLoading(false);
    }
  }, [showToast]);

  useEffect(() => { loadSources(); }, [loadSources]);

  // 进入阅读器先取一次收藏 ID 集合，让订阅/来源视图的文章卡也能显示收藏态。
  const loadFavoriteIds = useCallback(async () => {
    try {
      const data = await fetchFavorites({}, 1, 0);
      setFavoriteIds(new Set(data.favorite_ids || []));
    } catch { /* 收藏态非关键路径，静默失败 */ }
  }, []);

  useEffect(() => { loadFavoriteIds(); }, [loadFavoriteIds]);

  // 搜索防抖
  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const sourceNameMap = useMemo(() => {
    const map = {};
    for (const s of sources) map[s.source_id] = s.name || s.source_id;
    return map;
  }, [sources]);

  const subscribedSources = useMemo(
    () => sources
      .filter(s => subscribedIds.has(s.source_id))
      .sort((a, b) => (b.count || 0) - (a.count || 0)),
    [sources, subscribedIds],
  );

  const discoverSources = useMemo(
    () => sources
      .filter(s => !subscribedIds.has(s.source_id))
      .sort((a, b) => (b.count || 0) - (a.count || 0)),
    [sources, subscribedIds],
  );

  const hasNoSubscriptions = !sourcesLoading && subscribedSources.length === 0;

  // 零订阅时自动展开「发现更多来源」，引导用户添加
  useEffect(() => {
    if (hasNoSubscriptions) setDiscoverOpen(true);
  }, [hasNoSubscriptions]);

  // ── 选中文章 → 按需拉全文 ──
  // 列表项已不含正文（include_content=false），仅 meta 即时渲染；正文命中缓存直接用，
  // 否则拉 GET /api/articles/{id}，回来时比对最新选中 id，丢弃过期响应。
  const selectArticle = useCallback((article) => {
    const prevId = activeIdRef.current;
    setActiveArticle(article);
    const id = article?.id || null;
    activeIdRef.current = id;
    // 主动打开一篇新文章即记一次阅读（同篇连点不重复计；fire-and-forget）。
    if (id && id !== prevId) recordArticleRead(id);
    // 切文章即回到原文视图；译文若已缓存则备好，等用户主动点「译为中文」再显示。
    setShowTranslation(false);
    setTranslating(false);
    setTranslatedBody(id ? (translationCacheRef.current.get(id) ?? null) : null);
    if (!id) { setActiveBody(null); setActiveBodyLoading(false); return; }
    // 兜底：若列表项偶然已带正文（如详情接口回填），直接用
    if (article.content != null) { setActiveBody(article.content); setActiveBodyLoading(false); return; }
    const cached = bodyCacheRef.current.get(id);
    if (cached !== undefined) { setActiveBody(cached); setActiveBodyLoading(false); return; }
    setActiveBody(null);
    setActiveBodyLoading(true);
    fetchArticle(id)
      .then((data) => {
        const body = data?.content || '';
        bodyCacheRef.current.set(id, body);
        if (activeIdRef.current === id) { setActiveBody(body); setActiveBodyLoading(false); }
      })
      .catch((error) => {
        if (activeIdRef.current === id) {
          setActiveBody(null);
          setActiveBodyLoading(false);
          showToast(error.message || '获取文章正文失败', 'error');
        }
      });
  }, [showToast]);

  // ── AI · 一键译为中文（结果按 id 缓存，再次切回直接复用）──
  const handleTranslate = useCallback(async () => {
    const id = activeArticle?.id;
    if (!id) return;
    if (showTranslation) { setShowTranslation(false); return; }
    const cached = translationCacheRef.current.get(id);
    if (cached) { setTranslatedBody(cached); setShowTranslation(true); return; }
    setTranslating(true);
    try {
      const data = await translateArticle(id);
      translationCacheRef.current.set(id, data.translation);
      if (activeIdRef.current === id) { setTranslatedBody(data.translation); setShowTranslation(true); }
    } catch (error) {
      showToast(error.message || '翻译失败，请稍后重试', 'error');
    } finally {
      if (activeIdRef.current === id) setTranslating(false);
    }
  }, [activeArticle, showTranslation, showToast]);

  // ── 文章列表 ──
  const loadArticles = useCallback(async (skip = 0, append = false) => {
    // 竞态由 runList 兜底：发新弃旧，杜绝乱序晚到的响应覆盖当前列表。
    if (append) setLoadingMore(true); else { setArticlesLoading(true); setLoadingMore(false); }
    let data;
    try {
      // 列表只渲染摘要（content_preview），故请求统一不带全文，正文按需懒加载（见 selectArticle）。
      // 「我的订阅」聚合由后端 subscribed_scope=only 自行解析范围，前端无需先拿到订阅集合即可发请求。
      data = await runList((signal) => {
        if (showFavorites) {
          const filters = {};
          if (activeSourceId) filters.source_id = activeSourceId; // 收藏视图跟随当前来源
          if (searchQuery) filters.search = searchQuery;
          return fetchFavorites(filters, PAGE_SIZE, skip, { signal, includeContent: false });
        }
        const filters = {};
        if (activeSourceId) filters.source_id = activeSourceId;
        else filters.subscribed_scope = 'only'; // 「我的订阅」聚合：后端硬过滤到已订阅源
        if (searchQuery) filters.search = searchQuery;
        return fetchArticles(filters, PAGE_SIZE, skip, true, { signal, includeContent: false });
      });
    } catch (error) {
      showToast(error.message || '获取文章列表失败', 'error');
      if (append) setLoadingMore(false); else setArticlesLoading(false);
      return;
    }
    if (data === undefined) return; // 被更新的请求取代，loading 交给新请求，不在此清除
    if (showFavorites && data.favorite_ids) setFavoriteIds(new Set(data.favorite_ids));
    const items = data.items || [];
    setArticlesTotal(data.total || 0);
    setArticles(prev => (append ? [...prev, ...items] : items));
    // 不再自动展开第一篇——避免「被动打开」污染阅读计量；右栏停在提示态，
    // 等用户主动点选一篇才加载正文并计一次阅读（见 selectArticle）。
    if (append) setLoadingMore(false); else setArticlesLoading(false);
  }, [activeSourceId, searchQuery, showFavorites, showToast, runList]);

  // 切换来源/搜索 → 重置列表、回顶、清空右栏
  // 用 useLayoutEffect：在绘制前同步进入加载态，避免「切源瞬间旧列表被画出一帧」的陈旧帧闪现
  useLayoutEffect(() => {
    setActiveArticle(null);
    setActiveBody(null);
    setActiveBodyLoading(false);
    activeIdRef.current = null;
    if (listRef.current) listRef.current.scrollTop = 0;
    loadArticles(0, false);
  }, [loadArticles]);

  const hasMore = articles.length < articlesTotal;
  const handleLoadMore = () => loadArticles(articles.length, true);

  // ── 订阅 / 取消订阅 ──
  const applyResult = (result) => setSubscribedIds(new Set(result.subscribed_source_ids || []));

  // 订阅集合变化后，若正看「我的订阅」聚合视图需显式重拉（loadArticles 已不依赖 subscribedIds，
  // 故不会自动刷新）；看具体来源时由 activeSourceId 变化驱动，无需在此处理。
  const refreshAggregateIfActive = () => {
    if (!showFavorites && !activeSourceId) loadArticles(0, false);
  };

  const handleSubscribe = async (source) => {
    setPinningId(source.source_id);
    try {
      applyResult(await subscribeSource(source.source_id));
      refreshAggregateIfActive();
      showToast(`已订阅 ${source.name}`, 'success');
    } catch (error) {
      showToast(error.message || '订阅失败', 'error');
    } finally {
      setPinningId(null);
    }
  };

  const handleUnsubscribe = async (source) => {
    setPinningId(source.source_id);
    try {
      applyResult(await unsubscribeSource(source.source_id));
      if (activeSourceId === source.source_id) setActiveSourceId(null);  // 改 activeSourceId → 自动重拉
      else refreshAggregateIfActive();
      showToast(`已取消订阅 ${source.name}`, 'success');
    } catch (error) {
      showToast(error.message || '取消订阅失败', 'error');
    } finally {
      setPinningId(null);
    }
  };

  const subscribedTotal = useMemo(
    () => subscribedSources.reduce((sum, s) => sum + (s.count || 0), 0),
    [subscribedSources],
  );

  // ── 收藏 / 取消收藏 ──
  const handleToggleFavorite = async (article, event) => {
    event?.stopPropagation();
    const id = article.id;
    if (!id || favTogglingId === id) return;
    const wasFav = favoriteIds.has(id);
    setFavTogglingId(id);
    // 乐观更新收藏态
    setFavoriteIds((prev) => {
      const next = new Set(prev);
      if (wasFav) next.delete(id); else next.add(id);
      return next;
    });
    try {
      const result = wasFav ? await removeFavorite(id) : await addFavorite(id);
      if (result.favorite_ids) setFavoriteIds(new Set(result.favorite_ids));
      // 收藏视图里取消收藏 → 从当前列表移除
      if (showFavorites && wasFav) {
        setArticles((prev) => prev.filter((a) => a.id !== id));
        setArticlesTotal((t) => Math.max(0, t - 1));
        if (activeArticle?.id === id) selectArticle(null);  // 移除的正是当前阅读项 → 清空右栏
      }
      showToast(wasFav ? '已取消收藏' : '已收藏', 'success');
    } catch (error) {
      // 回滚乐观更新
      setFavoriteIds((prev) => {
        const next = new Set(prev);
        if (wasFav) next.add(id); else next.delete(id);
        return next;
      });
      showToast(error.message || '操作失败', 'error');
    } finally {
      setFavTogglingId(null);
    }
  };

  // 视图切换：订阅聚合 / 单个来源（左栏导航，离开收藏视图）
  const goSubscribed = () => { setShowFavorites(false); setActiveSourceId(null); };
  const goSource = (sourceId) => { setShowFavorites(false); setActiveSourceId(sourceId); };
  // 收藏星标：仅在「当前范围」上叠加/取消收藏过滤，保留 activeSourceId
  // （在某来源 → 看该来源收藏；在「我的订阅」聚合 → 看全部收藏）
  const toggleFavorites = () => setShowFavorites((v) => !v);

  return (
    <div
      className={`reader-shell ${sourcesCollapsed ? 'is-l-collapsed' : ''} ${listCollapsed ? 'is-m-collapsed' : ''}`}
      style={{
        '--col-l': sourcesCollapsed ? '0px' : '300px',
        '--col-m': listCollapsed ? '0px' : '420px',
      }}
    >
      {/* ── 分隔线把手 · 两类折叠互不耦合 ──
         · handle-l（左/中分隔线）：仅切换左栏，保留「全栏 ↔ 仅隐藏左栏」的分级能力。
         · handle-m（中/右分隔线）：一键直达——同时收起左栏+中栏，进入纯净阅读（仅右栏）。
         纯净态下中栏宽为 0，两把手会重叠，故只在最左缘渲染单个还原把手一键恢复全栏。 */}
      {!listCollapsed ? (
        <>
          <button
            type="button"
            title={sourcesCollapsed ? '展开来源栏' : '收起来源栏'}
            onClick={() => setSourcesCollapsed(c => !c)}
            className="reader-handle reader-handle-l"
          >
            {sourcesCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
          <button
            type="button"
            title="专注阅读（仅显示正文）"
            onClick={() => { setSourcesCollapsed(true); setListCollapsed(true); }}
            className="reader-handle reader-handle-m"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        </>
      ) : (
        <button
          type="button"
          title="退出专注阅读（恢复栏目）"
          onClick={() => { setSourcesCollapsed(false); setListCollapsed(false); }}
          className="reader-handle reader-handle-l"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      )}

      {/* ── 左栏 · 我的订阅 ── */}
      <aside className="reader-col reader-col-sources" aria-hidden={sourcesCollapsed} inert={sourcesCollapsed || undefined}>
        <div className="reader-sources-inner">
        {/* 左栏聚焦「来源」一类对象：订阅来源的聚合入口 + 下方逐源列表。
           「我的收藏」是文章级集合（非来源），已移到中栏文章列表头，避免与订阅来源混淆。 */}
        <nav className="reader-nav-group">
          <button
            type="button"
            onClick={goSubscribed}
            className={`reader-source-row reader-nav-row ${activeSourceId === null && !showFavorites ? 'reader-source-row-active' : ''}`}
          >
            <span className="reader-all-icon"><BookOpenText className="h-4 w-4" /></span>
            <div className="min-w-0 flex-1 text-left">
              <p className="reader-source-name">我的订阅</p>
              <p className="reader-source-meta">{subscribedTotal} 篇 · {subscribedSources.length} 个来源</p>
            </div>
          </button>
        </nav>

        <div className="reader-source-scroll">
          {sourcesLoading ? (
            <SourceRowsSkeleton />
          ) : (
            <>
              {subscribedSources.length > 0 && (
                <section className="reader-subs">
                  <div className="reader-group-band">
                    <span>订阅来源</span>
                    <span className="reader-group-count">{subscribedSources.length}</span>
                  </div>
                  <div className="reader-group-body">
                  {subscribedSources.map((source) => {
                    const active = activeSourceId === source.source_id;
                    return (
                      <div
                        key={source.source_id}
                        role="button"
                        tabIndex={0}
                        onClick={() => goSource(source.source_id)}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goSource(source.source_id); } }}
                        className={`reader-source-row ${active ? 'reader-source-row-active' : ''}`}
                      >
                        <LogoMark company={resolveCompany(source)} size="sm" emoji={source.icon} />
                        <div className="min-w-0 flex-1">
                          <p className="reader-source-name">{source.name || source.source_id}</p>
                          <p className="reader-source-meta">{source.count || 0} 篇</p>
                        </div>
                        <button
                          type="button"
                          title="取消订阅"
                          onClick={(e) => { e.stopPropagation(); handleUnsubscribe(source); }}
                          disabled={pinningId === source.source_id}
                          className="reader-pin reader-pin-on"
                        >
                          {pinningId === source.source_id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <Minus className="h-3.5 w-3.5" />}
                        </button>
                      </div>
                    );
                  })}
                  </div>
                </section>
              )}

              {hasNoSubscriptions && (
                <p className="reader-side-hint">还没有订阅任何来源，从下方「发现更多来源」开始添加。</p>
              )}

              {/* ── 发现更多来源 ── */}
              {discoverSources.length > 0 && (
                <section className="reader-discover">
                  <button
                    type="button"
                    onClick={() => setDiscoverOpen(o => !o)}
                    className="reader-group-band reader-group-band-toggle"
                  >
                    <Compass className="h-3.5 w-3.5" />
                    <span>发现更多来源</span>
                    <span className="reader-group-count">{discoverSources.length}</span>
                    <ChevronDown className={`reader-group-chevron h-4 w-4 ${discoverOpen ? '' : 'reader-group-chevron-collapsed'}`} />
                  </button>
                  {discoverOpen && (
                    <div className="reader-group-body">
                      {discoverSources.map((source) => (
                        <div key={source.source_id} className="reader-source-row reader-discover-row">
                          <LogoMark company={resolveCompany(source)} size="sm" emoji={source.icon} />
                          <div className="min-w-0 flex-1">
                            <p className="reader-source-name">{source.name || source.source_id}</p>
                            <p className="reader-source-meta">{source.count || 0} 篇 · {source.category || '其他来源'}</p>
                          </div>
                          <button
                            type="button"
                            title="订阅"
                            onClick={() => handleSubscribe(source)}
                            disabled={pinningId === source.source_id}
                            className="reader-pin reader-pin-off"
                          >
                            {pinningId === source.source_id
                              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              : <Plus className="h-3.5 w-3.5" />}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
        </div>
      </aside>

      {/* ── 中栏 · 文章列表 ── */}
      <section className="reader-col reader-col-list" aria-hidden={listCollapsed} inert={listCollapsed || undefined}>
        <div className="reader-list-inner">
        <div className="reader-search">
          <Search className="h-4 w-4 text-slate-500" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="搜索我的阅读…"
            className="reader-search-input"
          />
        </div>
        <div className="reader-list-head">
          <span className="reader-list-title">
            {showFavorites
              ? (activeSourceId ? `${sourceNameMap[activeSourceId] || activeSourceId} · 收藏` : '我的收藏')
              : activeSourceId ? (sourceNameMap[activeSourceId] || activeSourceId) : '我的订阅'}
          </span>
          {/* 收藏是文章级集合：中栏头部的星标切换，与逐篇卡片星标同色呼应。
              在某来源时只看该来源收藏；在「我的订阅」聚合时看全部收藏。 */}
          <button
            type="button"
            onClick={toggleFavorites}
            aria-pressed={showFavorites}
            aria-label={showFavorites ? '退出收藏' : (activeSourceId ? '只看本来源收藏' : '只看我的收藏')}
            title={showFavorites ? '退出收藏' : (activeSourceId ? '只看本来源收藏' : '只看我的收藏')}
            className={`reader-fav-icon ${showFavorites ? 'reader-fav-icon-on' : ''}`}
          >
            <Star className="h-4 w-4" fill={showFavorites ? 'currentColor' : 'none'} />
          </button>
          <span className="reader-list-count">{articlesTotal} 篇</span>
        </div>

        <div className="reader-list-scroll" ref={listRef}>
          {articlesLoading ? (
            <ArticleCardsSkeleton />
          ) : !showFavorites && hasNoSubscriptions && !activeSourceId ? (
            <div className="reader-empty reader-empty-tall">
              <Compass className="h-7 w-7 text-slate-300" />
              <span>你还没有订阅任何来源</span>
              <button type="button" className="action-button action-button-primary" onClick={() => setDiscoverOpen(true)}>
                去发现来源
              </button>
            </div>
          ) : articles.length === 0 ? (
            <div className="reader-empty">
              {showFavorites ? <Star className="h-6 w-6 text-slate-300" /> : <Inbox className="h-6 w-6 text-slate-300" />}
              <span>
                {searchQuery
                  ? '没有匹配的文章'
                  : showFavorites
                    ? (activeSourceId ? '该来源还没有收藏的文章' : '还没有收藏任何文章，点文章上的星标即可收藏')
                    : '该来源暂无文章'}
              </span>
            </div>
          ) : (
            <div>
              {articles.map((article) => {
                const active = activeArticle?.id === article.id;
                const favored = favoriteIds.has(article.id);
                return (
                  <div key={article.id} className="reader-article-wrap">
                    <button
                      type="button"
                      onClick={() => selectArticle(article)}
                      className={`reader-article-card ${active ? 'reader-article-card-active' : ''}`}
                    >
                      <p className="reader-article-title">{article.title || '（无标题）'}</p>
                      {excerptOf(article.content_preview || article.content) && (
                        <p className="reader-article-excerpt">{excerptOf(article.content_preview || article.content)}</p>
                      )}
                      <div className="reader-article-foot">
                        <span className="reader-article-source">{sourceNameMap[article.source_id] || article.source_id}</span>
                        {article.publish_date && (
                          <span className="reader-article-date" title={formatDateTime(article.publish_date)}>{formatRelativeTime(article.publish_date)}</span>
                        )}
                      </div>
                    </button>
                    <button
                      type="button"
                      title={favored ? '取消收藏' : '收藏'}
                      aria-label={favored ? '取消收藏' : '收藏'}
                      onClick={(e) => handleToggleFavorite(article, e)}
                      disabled={favTogglingId === article.id}
                      className={`reader-fav-toggle ${favored ? 'reader-fav-toggle-on' : ''}`}
                    >
                      {favTogglingId === article.id
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Star className="h-3.5 w-3.5" fill={favored ? 'currentColor' : 'none'} />}
                    </button>
                  </div>
                );
              })}
              {hasMore && (
                <button
                  type="button"
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                  className="reader-load-more"
                >
                  {loadingMore ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {loadingMore ? '加载中…' : `加载更多（剩余 ${articlesTotal - articles.length}）`}
                </button>
              )}
            </div>
          )}
        </div>
        </div>
      </section>

      {/* ── 右栏 · 阅读面板 ── */}
      <section className="reader-col reader-col-read">
        {activeArticle ? (
          <>
            {/* 阅读进度线：仅正文非空时显示；CSS scroll() 滚动驱动、切文章天然归零，
                不支持 scroll() 的浏览器由 @supports 直接隐藏（渐进增强，无 JS 兜底）。 */}
            {!activeBodyLoading && activeBody ? (
              <div className="reader-progress" aria-hidden="true" />
            ) : null}
          <article className="reader-pane">
            <header className="reader-pane-head">
              <div className="reader-pane-meta">
                <span className="reader-pane-source">{sourceNameMap[activeArticle.source_id] || activeArticle.source_id}</span>
                {activeArticle.publish_date && (
                  <span className="reader-pane-date" title={formatDateTime(activeArticle.publish_date)}>
                    <CalendarDays className="h-3.5 w-3.5" /> {formatRelativeTime(activeArticle.publish_date)}
                  </span>
                )}
                {activeArticle.content_type && (
                  <span className="data-chip">{contentTypeLabel(activeArticle.content_type, activeArticle.content_type)}</span>
                )}
              </div>
              <h1 className="reader-pane-title">{activeArticle.title || '（无标题）'}</h1>
              <div className="reader-pane-actions">
                {activeArticle.source_url && (
                  <a href={activeArticle.source_url} target="_blank" rel="noreferrer" className="reader-pane-link">
                    <ExternalLink className="h-3.5 w-3.5" /> 查看来源
                  </a>
                )}
                <button
                  type="button"
                  onClick={(e) => handleToggleFavorite(activeArticle, e)}
                  disabled={favTogglingId === activeArticle.id}
                  className={`reader-pane-fav ${favoriteIds.has(activeArticle.id) ? 'reader-pane-fav-on' : ''}`}
                >
                  {favTogglingId === activeArticle.id
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Star className="h-3.5 w-3.5" fill={favoriteIds.has(activeArticle.id) ? 'currentColor' : 'none'} />}
                  {favoriteIds.has(activeArticle.id) ? '已收藏' : '收藏'}
                </button>
                {aiEnabled && (
                  <button
                    type="button"
                    onClick={handleTranslate}
                    disabled={translating || activeBodyLoading || !activeBody}
                    title={showTranslation ? '当前显示中文译文，点击切回原文' : '将正文译为中文'}
                    aria-pressed={showTranslation}
                    className={`reader-pane-link ${showTranslation ? 'reader-pane-link-on' : ''}`}
                  >
                    {translating
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Languages className="h-3.5 w-3.5" />}
                    {showTranslation ? '显示原文' : '译为中文'}
                  </button>
                )}
              </div>
            </header>
            <div className="reader-pane-body markdown-body">
              {activeBodyLoading ? (
                <PaneBodySkeleton />
              ) : (showTranslation && translatedBody) ? (
                <ReaderMarkdown>{translatedBody}</ReaderMarkdown>
              ) : activeBody ? (
                <ReaderMarkdown>{activeBody}</ReaderMarkdown>
              ) : (
                '该文章暂无正文内容，点击「查看原文」阅读完整内容。'
              )}
            </div>
          </article>
          </>
        ) : (
          <div className="reader-empty reader-empty-read">
            <BookOpenText className="h-8 w-8 text-slate-300" />
            <span>从中间选择一篇文章开始阅读</span>
          </div>
        )}
      </section>

      <ReaderAiPanel aiEnabled={aiEnabled} activeArticle={activeArticle} showToast={showToast} />
    </div>
  );
}
