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
  CheckCheck,
  CircleDot,
  RefreshCw,
  Sparkles,
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
  fetchUnreadCounts,
  markAllRead,
  markArticleRead,
  markArticleUnread,
  summarizeArticle,
} from '../api';

const PAGE_SIZE = 30;
const UNREAD_POLL_MS = 60000; // 未读轻轮询间隔（标签页可见时才真正请求）

// 未读徽标数显示上限
const formatBadge = (n) => (n > 99 ? '99+' : String(n));

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

  // ── 内容形态视图轴(迭代 2):我的订阅聚合分「文章 / 动态」两个流 ──
  // 文章 = 阅读内容(默认);动态 = changelog/Release/仓库/模型监控等短条目。
  // 单源视图不需要该轴(源是形态同质的),由源自身 shape 决定卡片密度。
  const [shape, setShape] = useState('article');

  // ── 未读体系 ──
  // 计数来自 GET /api/reader/unread-counts(挂载即拉一次以校准水位,随后 60s 轻轮询);
  // 条目未读标记来自列表接口的 with_unread 页级标记 + 本会话逐篇覆盖(readOverrides)。
  const [unreadBySource, setUnreadBySource] = useState({});
  const [unreadOnly, setUnreadOnly] = useState(false);   // 只看未读
  // 本会话逐篇覆盖:id → true(已读)/false(未读)。打开=覆盖已读(圆点即消);
  // 手动「标为未读」=覆盖未读(圆点复现)。无覆盖时以服务端 article.unread 为准。
  const [readOverrides, setReadOverrides] = useState(() => new Map());
  const readOverridesRef = useRef(new Map());
  const [markingRead, setMarkingRead] = useState(false);
  const [paneReadToggling, setPaneReadToggling] = useState(false);
  // 「有 N 篇新文章」提示:相邻两次轮询同视图未读数的正增量累计;切视图/刷新列表归零。
  const [freshCount, setFreshCount] = useState(0);
  const prevScopeUnreadRef = useRef(null);

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

  // ── 用户面 AI · 要点摘要(正文顶部「AI 总结」卡;缓存 id → 摘要)──
  const [activeSummary, setActiveSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const summaryCacheRef = useRef(new Map());

  // ── 日报置顶卡:最新一期 AI 资讯日报(独立拉取,不依赖订阅关系)──
  const [latestBrief, setLatestBrief] = useState(null);

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

  // source_id → 形态('article'|'bulletin'),目录未含的源按文章形兜底
  // (声明在未读逻辑之前:applyUnreadCounts 的视图口径依赖它)
  const sourceShapeMap = useMemo(() => {
    const map = {};
    for (const s of sources) map[s.source_id] = s.shape || 'article';
    return map;
  }, [sources]);

  // ── 未读计数:应用响应 + 正增量检测(驱动「有 N 篇新文章」提示条)──
  const applyUnreadCounts = useCallback((data) => {
    const bySource = data.by_source || {};
    setUnreadBySource(bySource);
    if (showFavorites) return; // 收藏视图不做新内容提示
    // 视图范围:单源看该源;聚合只累计当前形态轴下的源(文章/动态两个流独立提示)
    const scope = activeSourceId
      ? (bySource[activeSourceId] || 0)
      : Object.entries(bySource).reduce(
          (sum, [sid, n]) =>
            sum + ((sourceShapeMap[sid] === 'bulletin' ? 'bulletin' : 'article') === shape ? n : 0),
          0,
        );
    const prev = prevScopeUnreadRef.current;
    prevScopeUnreadRef.current = scope;
    if (prev !== null && scope > prev) setFreshCount((c) => c + (scope - prev));
  }, [activeSourceId, showFavorites, shape, sourceShapeMap]);

  const loadUnreadCounts = useCallback(async () => {
    try {
      applyUnreadCounts(await fetchUnreadCounts());
    } catch { /* 未读计数非关键路径,静默失败,等下个轮询周期 */ }
  }, [applyUnreadCounts]);

  // 挂载即拉一次(顺带校准存量订阅的水位),此后 60s 轻轮询;标签页不可见时跳过请求。
  useEffect(() => {
    let timer = null;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      if (!document.hidden) await loadUnreadCounts();
      if (!cancelled) timer = setTimeout(tick, UNREAD_POLL_MS);
    };
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [loadUnreadCounts]);

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

  const subscribedArticleSources = useMemo(
    () => subscribedSources.filter(s => (s.shape || 'article') === 'article'),
    [subscribedSources],
  );
  const subscribedBulletinSources = useMemo(
    () => subscribedSources.filter(s => (s.shape || 'article') === 'bulletin'),
    [subscribedSources],
  );

  const discoverSources = useMemo(
    () => sources
      .filter(s => !subscribedIds.has(s.source_id))
      .sort((a, b) => (b.count || 0) - (a.count || 0)),
    [sources, subscribedIds],
  );
  const discoverArticleSources = useMemo(
    () => discoverSources.filter(s => (s.shape || 'article') === 'article'),
    [discoverSources],
  );
  const discoverBulletinSources = useMemo(
    () => discoverSources.filter(s => (s.shape || 'article') === 'bulletin'),
    [discoverSources],
  );

  // 未读按形态拆分:文章未读是主徽标(我的订阅行),动态未读弱化为分组小计
  const unreadByShape = useMemo(() => {
    const totals = { article: 0, bulletin: 0 };
    for (const [sid, n] of Object.entries(unreadBySource)) {
      totals[sourceShapeMap[sid] === 'bulletin' ? 'bulletin' : 'article'] += n;
    }
    return totals;
  }, [unreadBySource, sourceShapeMap]);

  // 当前列表是否呈现动态形(决定卡片密度):单源看源的 shape,聚合看视图轴
  const bulletinView = !showFavorites && (
    activeSourceId ? sourceShapeMap[activeSourceId] === 'bulletin' : shape === 'bulletin'
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
    // 后端同一请求里双写计量+逐篇已读状态;此处同步做乐观清点:圆点即消、未读数-1。
    if (id && id !== prevId) {
      recordArticleRead(id);
      const override = readOverridesRef.current.get(id);
      const wasUnread = override === undefined ? !!article.unread : !override;
      if (wasUnread) {
        readOverridesRef.current.set(id, true);
        setReadOverrides(new Map(readOverridesRef.current));
        const sid = article.source_id;
        setUnreadBySource((prev) => {
          const n = prev[sid] || 0;
          return n > 0 ? { ...prev, [sid]: n - 1 } : prev;
        });
      }
    }
    // 切文章即回到原文视图；译文若已缓存则备好，等用户主动点「译为中文」再显示。
    setShowTranslation(false);
    setTranslating(false);
    setTranslatedBody(id ? (translationCacheRef.current.get(id) ?? null) : null);
    // 摘要:会话缓存 → 列表条目自带的 summary_zh(服务端缓存)→ 空(显示生成入口)
    setSummarizing(false);
    setActiveSummary(id ? (summaryCacheRef.current.get(id) ?? article.summary_zh ?? null) : null);
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

  // ── AI · 要点摘要(结果双层缓存:服务端 extensions_json + 本会话 Map)──
  const handleSummarize = useCallback(async () => {
    const id = activeArticle?.id;
    if (!id || summarizing) return;
    setSummarizing(true);
    try {
      const data = await summarizeArticle(id);
      summaryCacheRef.current.set(id, data.summary);
      if (activeIdRef.current === id) setActiveSummary(data.summary);
      // 列表条目同步带上摘要,卡片摘要行即时更新
      setArticles((prev) => prev.map((a) => (a.id === id ? { ...a, summary_zh: data.summary } : a)));
    } catch (error) {
      showToast(error.message || '摘要生成失败，请稍后重试', 'error');
    } finally {
      if (activeIdRef.current === id) setSummarizing(false);
    }
  }, [activeArticle, summarizing, showToast]);

  // ── 日报置顶卡:进入阅读器拉一次最新一期(无日报则整卡隐藏)──
  useEffect(() => {
    let cancelled = false;
    fetchArticles({ source_id: 'dorami_daily_brief' }, 1, 0, false, { includeContent: false })
      .then((items) => {
        if (!cancelled) setLatestBrief(Array.isArray(items) && items.length > 0 ? items[0] : null);
      })
      .catch(() => { /* 日报卡非关键路径,静默失败 */ });
    return () => { cancelled = true; };
  }, []);

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
        else {
          filters.subscribed_scope = 'only'; // 「我的订阅」聚合：后端硬过滤到已订阅源
          filters.shape = shape;             // 聚合流按形态轴分流(文章/动态);单源无需
        }
        if (searchQuery) filters.search = searchQuery;
        filters.with_unread = 'true';           // 条目附页级未读标记（水位由 unread-counts 校准）
        if (unreadOnly) filters.unread_only = 'true';
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
    if (!append) setFreshCount(0); // 列表已刷新,新内容提示归零
    if (append) setLoadingMore(false); else setArticlesLoading(false);
  }, [activeSourceId, searchQuery, showFavorites, unreadOnly, shape, showToast, runList]);

  // 切换来源/搜索 → 重置列表、回顶、清空右栏
  // 用 useLayoutEffect：在绘制前同步进入加载态，避免「切源瞬间旧列表被画出一帧」的陈旧帧闪现
  useLayoutEffect(() => {
    setActiveArticle(null);
    setActiveBody(null);
    setActiveBodyLoading(false);
    activeIdRef.current = null;
    if (listRef.current) listRef.current.scrollTop = 0;
    prevScopeUnreadRef.current = null; // 切视图:新内容增量检测重新起算
    setFreshCount(0);
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
      loadUnreadCounts(); // 订阅集合变化,未读统计随之刷新
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
      loadUnreadCounts();
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

  // ── 全部标读（当前范围：某来源 / 全部订阅）──
  const handleMarkAllRead = async () => {
    if (markingRead) return;
    setMarkingRead(true);
    try {
      const data = await markAllRead(activeSourceId);
      // 后端返回更新后的统计;本页在列条目全部乐观清点(圆点即消)。
      prevScopeUnreadRef.current = null;
      applyUnreadCounts(data);
      for (const a of articles) readOverridesRef.current.set(a.id, true);
      setReadOverrides(new Map(readOverridesRef.current));
      if (unreadOnly) loadArticles(0, false); // 只看未读视图下列表应清空重拉
      showToast('已全部标为已读', 'success');
    } catch (error) {
      showToast(error.message || '标记已读失败', 'error');
    } finally {
      setMarkingRead(false);
    }
  };

  // 刷新新到内容:重拉列表 + 未读统计(提示条点击)
  const handleRefreshFresh = () => {
    loadArticles(0, false);
    loadUnreadCounts();
  };

  // 逐篇已读态(覆盖优先,服务端标记兜底)
  const isArticleUnread = useCallback((article) => {
    if (!article?.id) return false;
    const override = readOverrides.get(article.id);
    return override === undefined ? !!article.unread : !override;
  }, [readOverrides]);

  // ── 阅读窗格:手动标为已读/未读(显式覆盖,可撤销误触;不计阅读量)──
  const handleTogglePaneRead = async () => {
    const article = activeArticle;
    const id = article?.id;
    if (!id || paneReadToggling) return;
    const toUnread = !isArticleUnread(article); // 当前已读 → 标为未读;反之标为已读
    const sid = article.source_id;
    const bump = (delta) => {
      setUnreadBySource((prev) => {
        const n = Math.max(0, (prev[sid] || 0) + delta);
        return { ...prev, [sid]: n };
      });
      // 同步校正轮询基线,避免手动标未读被误判为「新文章到达」
      if (prevScopeUnreadRef.current !== null && (!activeSourceId || activeSourceId === sid)) {
        prevScopeUnreadRef.current = Math.max(0, prevScopeUnreadRef.current + delta);
      }
    };
    // 乐观更新 + 失败回滚
    readOverridesRef.current.set(id, !toUnread);
    setReadOverrides(new Map(readOverridesRef.current));
    bump(toUnread ? 1 : -1);
    setPaneReadToggling(true);
    try {
      await (toUnread ? markArticleUnread(id) : markArticleRead(id));
    } catch (error) {
      readOverridesRef.current.set(id, toUnread);
      setReadOverrides(new Map(readOverridesRef.current));
      bump(toUnread ? -1 : 1);
      showToast(error.message || '操作失败', 'error');
    } finally {
      setPaneReadToggling(false);
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
            {unreadByShape.article > 0 && (
              <span className="reader-unread-badge" title={`${unreadByShape.article} 篇文章未读`}>
                {formatBadge(unreadByShape.article)}
              </span>
            )}
          </button>
        </nav>

        <div className="reader-source-scroll">
          {sourcesLoading ? (
            <SourceRowsSkeleton />
          ) : (
            <>
              {/* 订阅来源按形态分组:文章来源(主流)在前,动态来源(changelog/发布监控)在后;
                  动态组的组头带弱化的未读小计,主未读徽标只属于文章(见「我的订阅」行)。 */}
              {[
                { key: 'article', label: '文章来源', list: subscribedArticleSources, groupUnread: 0 },
                { key: 'bulletin', label: '动态来源', list: subscribedBulletinSources, groupUnread: unreadByShape.bulletin },
              ].map(({ key, label, list, groupUnread }) => list.length > 0 && (
                <section className="reader-subs" key={key}>
                  <div className="reader-group-band">
                    <span>{label}</span>
                    <span className="reader-group-count">{list.length}</span>
                    {groupUnread > 0 && (
                      <span className="reader-unread-badge" title={`${groupUnread} 条未读动态`}>
                        {formatBadge(groupUnread)}
                      </span>
                    )}
                  </div>
                  <div className="reader-group-body">
                  {list.map((source) => {
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
                        {(unreadBySource[source.source_id] || 0) > 0 && (
                          <span className="reader-unread-badge" title={`${unreadBySource[source.source_id]} 篇未读`}>
                            {formatBadge(unreadBySource[source.source_id])}
                          </span>
                        )}
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
              ))}

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
                      {[
                        { key: 'article', label: '文章', list: discoverArticleSources },
                        { key: 'bulletin', label: '动态', list: discoverBulletinSources },
                      ].map(({ key, label, list }) => list.length > 0 && (
                        <div key={key}>
                          <p className="reader-subgroup-label">{label}</p>
                          {list.map((source) => (
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
          {/* 形态视图轴:仅「我的订阅」聚合流需要(文章=阅读内容 / 动态=发布类短条目) */}
          {!showFavorites && !activeSourceId && (
            <div className="mini-seg" role="tablist" aria-label="内容形态">
              {[['article', '文章'], ['bulletin', '动态']].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={shape === value}
                  onClick={() => setShape(value)}
                  className={`mini-seg-btn ${shape === value ? 'is-on' : ''}`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          {/* 未读动作对：只看未读（过滤切换）+ 全部标读（当前范围）。收藏视图下不适用。 */}
          {!showFavorites && (
            <>
              <button
                type="button"
                onClick={() => setUnreadOnly((v) => !v)}
                aria-pressed={unreadOnly}
                aria-label={unreadOnly ? '显示全部文章' : '只看未读'}
                title={unreadOnly ? '显示全部文章' : '只看未读'}
                className={`reader-unread-icon ${unreadOnly ? 'reader-unread-icon-on' : ''}`}
              >
                <CircleDot className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={handleMarkAllRead}
                disabled={markingRead}
                aria-label={activeSourceId ? '本来源全部标为已读' : '全部订阅标为已读'}
                title={activeSourceId ? '本来源全部标为已读' : '全部订阅标为已读'}
                className="reader-unread-icon"
              >
                {markingRead ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
              </button>
            </>
          )}
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
          {/* 日报置顶卡:「我的订阅·文章」流顶部的一等公民入口;过滤中(搜索/只看未读)让位 */}
          {!showFavorites && !activeSourceId && shape === 'article' && !searchQuery && !unreadOnly
            && !articlesLoading && latestBrief && (
            <button type="button" className="reader-brief-card" onClick={() => selectArticle(latestBrief)}>
              <span className="reader-brief-head">
                <Sparkles className="h-3.5 w-3.5" />
                AI 资讯日报
                {latestBrief.publish_date && (
                  <span className="reader-brief-date" title={formatDateTime(latestBrief.publish_date)}>
                    {formatRelativeTime(latestBrief.publish_date)}
                  </span>
                )}
              </span>
              <span className="reader-brief-title">{latestBrief.title || '（无标题）'}</span>
            </button>
          )}
          {/* 新内容提示条:轮询发现未读正增量时出现,点击刷新——不自动插入打断阅读 */}
          {!showFavorites && !articlesLoading && freshCount > 0 && (
            <button type="button" className="reader-fresh-pill" onClick={handleRefreshFresh}>
              <RefreshCw className="h-3.5 w-3.5" />
              有 {freshCount} 篇新文章 · 点击刷新
            </button>
          )}
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
                    : unreadOnly
                      ? (bulletinView ? '没有未读动态，都看完啦' : '没有未读文章，都读完啦')
                      : activeSourceId
                        ? '该来源暂无内容'
                        : (bulletinView ? '暂无动态' : '暂无文章')}
              </span>
            </div>
          ) : (
            <div>
              {articles.map((article) => {
                const active = activeArticle?.id === article.id;
                const favored = favoriteIds.has(article.id);
                const isUnread = isArticleUnread(article);
                return (
                  <div key={article.id} className="reader-article-wrap">
                    {isUnread && <span className="reader-unread-dot" aria-hidden="true" />}
                    <button
                      type="button"
                      onClick={() => selectArticle(article)}
                      className={`reader-article-card ${bulletinView ? 'reader-bulletin-card' : ''} ${active ? 'reader-article-card-active' : ''}`}
                    >
                      <p className="reader-article-title">{article.title || '（无标题）'}</p>
                      {/* 摘要行:AI 要点摘要(summary_zh)优先——正文截断对英文长文几乎无信息量 */}
                      {!bulletinView && excerptOf(article.summary_zh || article.content_preview || article.content) && (
                        <p className="reader-article-excerpt">{excerptOf(article.summary_zh || article.content_preview || article.content)}</p>
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
                {/* 手动标读/标未读:撤销误触的已读(打开即读),Folo 式单篇切换;不计阅读量 */}
                <button
                  type="button"
                  onClick={handleTogglePaneRead}
                  disabled={paneReadToggling}
                  title={isArticleUnread(activeArticle) ? '标为已读' : '标为未读(撤销已读)'}
                  className="reader-pane-link"
                >
                  {paneReadToggling
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : isArticleUnread(activeArticle)
                      ? <CheckCheck className="h-3.5 w-3.5" />
                      : <CircleDot className="h-3.5 w-3.5" />}
                  {isArticleUnread(activeArticle) ? '标为已读' : '标为未读'}
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
              {/* AI 总结卡:有缓存直接展示;无缓存给低调的生成入口(MVP 不自动生成,控成本) */}
              {aiEnabled && !activeBodyLoading && (activeSummary || activeBody) && (
                <div className="reader-ai-summary">
                  <div className="reader-ai-summary-head">
                    <Sparkles className="h-3.5 w-3.5" /> AI 总结
                  </div>
                  {activeSummary ? (
                    <p className="reader-ai-summary-text">{activeSummary}</p>
                  ) : (
                    <button
                      type="button"
                      onClick={handleSummarize}
                      disabled={summarizing}
                      className="reader-ai-summary-generate"
                    >
                      {summarizing
                        ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在总结…</>
                        : '生成本文要点总结'}
                    </button>
                  )}
                </div>
              )}
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
