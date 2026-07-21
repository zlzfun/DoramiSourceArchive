import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Search,
  X,
  Minus,
  Plus,
  ExternalLink,
  Loader2,
  Inbox,
  Compass,
  BookOpenText,
  FileText,
  Zap,
  AtSign,
  Star,
  CheckCheck,
  CircleDot,
  RefreshCw,
  Sparkles,
  Settings,
  Sun,
  Moon,
} from 'lucide-react';
import LogoMark from './LogoMark';
import BrandLogoImage from './BrandLogoImage';
import ReaderMarkdown from './ReaderMarkdown';
import ReaderAiPanel from './ReaderAiPanel';
import { SOURCE_ROLES, sourceRoleOf, resolveCompany } from '../sourceTaxonomy';
import DiscoverPage from './DiscoverPage';
import SocialFlow from './SocialFlow';
import { excerptOf } from '../utils/readerText';
import { highlightMatch } from '../utils/highlight';
import { WEEKDAY_CHARS, fmtDayKey, dayKeyOf, dayLabelOf, timeOfDay } from '../utils/readerTime';
import { formatRelativeTime, formatDateTime } from '../utils/datetime';
import { contentTypeLabel } from '../utils/contentType';
import { useAbortableLoad } from '../hooks/useAbortableLoad';
import { useOverlayScrollbar } from '../hooks/useOverlayScrollbar';
import {
  fetchReaderSources,
  mediaProxyUrl,
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

// 日期分组 & 条目时刻的实现已上移 utils/readerTime.js —— 社交媒体流(SocialFlow)
// 与条目列共用同一套组头语法,复制一份会漂移。

// 样页日报卡日期:「07-18 · 六」
const briefDateOf = (raw) => {
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} · ${WEEKDAY_CHARS[d.getDay()]}`;
};

// 样页日报报头日期:「2026-07-18 · 星期六」
const briefMastDateOf = (raw) => {
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return '';
  return `${fmtDayKey(d)} · 星期${WEEKDAY_CHARS[d.getDay()]}`;
};

// crumb 的「源名 · 域名」域名段(样页:Simon Willison · simonwillison.net)
const hostOf = (url) => {
  if (!url) return '';
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
};

// 日报的源标识(置顶卡/报头形态判定)
const BRIEF_SOURCE_ID = 'dorami_daily_brief';

// ── 源栏分类:统一「信息角色」单轴(官方 / 媒体 / 个人 / 榜单) ──
// 判定(sourceRoleOf/SOURCE_ROLES)在 sourceTaxonomy.js,与发现页、管理面共用同一套词汇。

// ── 骨架屏 · 大块加载态形状占位 ──
// 形状贴近真实内容，替代居中 spinner；条数固定、宽度错落，纯装饰故 aria-hidden。

// 侧栏来源行：图标块 + 名称条
function SourceRowsSkeleton() {
  const nameWidths = ['w-3/4', 'w-2/3', 'w-4/5', 'w-1/2', 'w-3/5'];
  return (
    <div className="reader-group-body skeleton-delay" aria-hidden="true">
      {nameWidths.map((w, i) => (
        <div key={i} className="flex items-center gap-2.5 px-2.5 py-2">
          <div className="skeleton h-5 w-5 rounded-[var(--r-sm)]" />
          <div className={`skeleton h-3.5 ${w}`} />
        </div>
      ))}
    </div>
  );
}

// 条目卡：首行短条 + 标题条 + 摘要条（形状贴近 .reader-entry）
// count/delayed 可调:初次加载走 5 条 + 150ms 延迟显现(快路径不闪);
// 无限滚动追加走少量、即时(已在触发点,给即时反馈)。
function ArticleCardsSkeleton({ count = 5, delayed = true }) {
  const cards = [
    { title: 'w-3/4', excerpt: 'w-1/2' },
    { title: 'w-5/6', excerpt: 'w-2/3' },
    { title: 'w-2/3', excerpt: 'w-3/5' },
    { title: 'w-4/5', excerpt: 'w-1/2' },
    { title: 'w-3/5', excerpt: 'w-2/3' },
  ].slice(0, count);
  return (
    <div className={delayed ? 'skeleton-delay' : ''} aria-hidden="true">
      {cards.map((c, i) => (
        <div key={i} className="px-3 py-2.5">
          <div className="skeleton h-2.5 w-24" />
          <div className={`skeleton mt-2 h-3.5 ${c.title}`} />
          <div className={`skeleton mt-1.5 h-3 ${c.excerpt}`} />
        </div>
      ))}
    </div>
  );
}

// 阅读窗格正文：若干段落条（真实 meta/标题已在 header 中渲染，此处只占正文位）
function PaneBodySkeleton() {
  const lines = ['w-full', 'w-full', 'w-11/12', 'w-full', 'w-4/5', 'w-full', 'w-full', 'w-2/3'];
  return (
    <div className="skeleton-delay" aria-hidden="true">
      {lines.map((w, i) => (
        <div key={i} className={`skeleton h-4 ${w} ${i > 0 ? 'mt-3' : ''}`} />
      ))}
    </div>
  );
}

export default function ReaderTab({
  showToast,
  aiEnabled = false,
  // ── standalone(读者账号):应用导轨已隐藏,视图轨独占——轨底并入用户菜单 ──
  standalone = false,
  account = null,
  avatarText = '',
  themeDark = false,
  onToggleTheme,
  onOpenSettings,
}) {
  const [sources, setSources] = useState([]);
  const [subscribedIds, setSubscribedIds] = useState(() => new Set());
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [activeSourceId, setActiveSourceId] = useState(null); // null = 当前容器的聚合流
  // 收藏 = 容器内的正交过滤器(Folo 语义:各视图有自己的收藏钮),不再是独立视图
  const [favOnly, setFavOnly] = useState(false);
  const [favoriteIds, setFavoriteIds] = useState(() => new Set());
  const [favTogglingId, setFavTogglingId] = useState(null);
  // 发现页(整页视图,取代源栏内联「发现更多来源」):true 时 条目列+阅读窗 被发现页取代
  const [discover, setDiscover] = useState(false);
  const [brandFailed, setBrandFailed] = useState(false); // 品牌 logo 加载失败 → 回退铃铛

  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false); // 视图轨「搜索」开合中栏搜索行

  // ── 容器模型(Folo 语义):文章/动态/社交是三个内容宇宙,各自渲染形态不同 ──
  // 'article'(默认) | 'bulletin' | 'social'。选中源=在容器内收窄(mode 与 activeSourceId
  // 共存,轨钮保持点亮);点源自动跳入该源所属容器。
  // (「今日」跨宇宙混合流已取缔:它用文章形态渲染推文,违反容器模型的前提——
  //  三个宇宙渲染形态不同才需要分容器;各容器默认倒序 + 未读体系已能回答「最近/未看」。)
  const [mode, setMode] = useState('article');

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
  const [socialReadToggling, setSocialReadToggling] = useState(null); // 社交流按条标读中的 id
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

  // ── 用户面 AI · 要点摘要(正文顶部「哆啦美速读」卡;缓存 id → 摘要)──
  const [activeSummary, setActiveSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const summaryCacheRef = useRef(new Map());

  // ── 日报置顶卡:最新一期 AI 资讯日报(独立拉取,不依赖订阅关系)──
  const [latestBrief, setLatestBrief] = useState(null);

  const listRef = useRef(null);
  const listThumbRef = useRef(null); // 浮层滚动条滑块(压在卡片上,内容满宽)
  const resyncListScrollbar = useOverlayScrollbar(listRef, listThumbRef);
  const sentinelRef = useRef(null); // 无限滚动哨兵:进入视口即追加下一页
  // 正文缓存（id → content）+ 「最新选中 id」防竞态：快速连点时丢弃晚到的过期正文响应
  const bodyCacheRef = useRef(new Map());
  const activeIdRef = useRef(null);
  // hover 预取(体验二波 A4):悬停 150ms 即预拉正文进缓存,点击零等待——「丝滑」的实质。
  const prefetchTimerRef = useRef(null);
  const prefetchingIdsRef = useRef(new Set());
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

  // source_id → 形态('article'|'bulletin'|'social'),目录未含的源按文章形兜底
  // (声明在未读逻辑之前:applyUnreadCounts 的视图口径依赖它)
  // 三态自 v3.12 社交波起:social 是与文章/动态并列的第三容器,不可再按
  // 「非 bulletin 即 article」二分——那会把社交源误归文章容器。
  const sourceShapeMap = useMemo(() => {
    const map = {};
    for (const s of sources) map[s.source_id] = s.shape || 'article';
    return map;
  }, [sources]);

  const shapeOfSource = useCallback(
    (sid) => {
      const shape = sourceShapeMap[sid];
      return shape === 'bulletin' || shape === 'social' ? shape : 'article';
    },
    [sourceShapeMap],
  );

  // ── 未读计数:应用响应 + 正增量检测(驱动「有 N 篇新文章」提示条)──
  const applyUnreadCounts = useCallback((data) => {
    const bySource = data.by_source || {};
    setUnreadBySource(bySource);
    if (favOnly) return; // 收藏过滤中不做新内容提示
    // 范围口径:单源看该源;容器聚合看本容器形态;今日=全形态
    const scope = activeSourceId
      ? (bySource[activeSourceId] || 0)
      : Object.entries(bySource).reduce(
          (sum, [sid, n]) => sum + (shapeOfSource(sid) === mode ? n : 0),
          0,
        );
    const prev = prevScopeUnreadRef.current;
    prevScopeUnreadRef.current = scope;
    if (prev !== null && scope > prev) setFreshCount((c) => c + (scope - prev));
  }, [activeSourceId, favOnly, mode, shapeOfSource]);

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

  // source_id → 完整源对象(条目首行/crumb 的 LogoMark 锚点用)
  const sourceMap = useMemo(() => {
    const map = {};
    for (const s of sources) map[s.source_id] = s;
    return map;
  }, [sources]);

  const subscribedSources = useMemo(
    () => sources
      .filter(s => subscribedIds.has(s.source_id))
      .sort((a, b) => (b.count || 0) - (a.count || 0)),
    [sources, subscribedIds],
  );

  // 社交源集合(仅用于平台角标计数;分组统一走 sidebarGroups)
  const socialSources = useMemo(
    () => subscribedSources.filter((s) => shapeOfSource(s.source_id) === 'social'),
    [subscribedSources, shapeOfSource],
  );

  // 平台由 source 透出,兜底取 source_id 前缀(x_karpathy / mastodon_xxx)
  const platformOfSource = useCallback(
    (s) => s.platform || (s.source_id || '').split('_')[0] || '',
    [],
  );

  // 已订阅的平台数 —— 决定卡片是否挂平台角标(单平台时每卡同一图标是纯噪声)
  const platformCount = useMemo(
    () => new Set(socialSources.map(platformOfSource).filter(Boolean)).size,
    [socialSources, platformOfSource],
  );

  // 未读按形态拆分:驱动视图轨口径与源栏头的未读总数
  const unreadByShape = useMemo(() => {
    const totals = { article: 0, bulletin: 0, social: 0 };
    for (const [sid, n] of Object.entries(unreadBySource)) totals[shapeOfSource(sid)] += n;
    return totals;
  }, [unreadBySource, shapeOfSource]);

  // 当前列表范围的未读小计(条目列头读数)
  const scopeUnread = useMemo(() => {
    if (favOnly) return 0;
    if (activeSourceId) return unreadBySource[activeSourceId] || 0;
    return unreadByShape[mode] || 0;
  }, [favOnly, activeSourceId, unreadBySource, unreadByShape, mode]);

  // 动态容器整体呈紧凑形(源在容器内形态同质,单源由 goSource 归位到所属容器)
  const bulletinView = mode === 'bulletin';
  // 社交容器:整幅卡片流(SocialFlow),不走条目列+阅读窗的四带式
  const socialView = mode === 'social';

  // 源栏分组(全站统一「信息角色」单轴):当前容器(shape=mode)的源按角色分组,空组不渲染。
  // 形态已由左侧视图轨容器承担,组头只表角色——文章=官方/媒体/个人/榜单,
  // 动态=官方/榜单,社交=官方/个人。三容器共用一套逻辑,不再各写一份。
  const sidebarGroups = useMemo(() => {
    const buckets = {};
    for (const s of subscribedSources) {
      if (shapeOfSource(s.source_id) !== mode) continue;
      const role = sourceRoleOf(s);
      (buckets[role] ||= []).push(s);
    }
    return SOURCE_ROLES
      .map((r) => ({ ...r, list: buckets[r.key] || [] }))
      .filter((g) => g.list.length > 0);
  }, [subscribedSources, shapeOfSource, mode]);

  const hasNoSubscriptions = !sourcesLoading && subscribedSources.length === 0;

  // 零订阅时自动进入发现页,引导用户添加第一个订阅
  useEffect(() => {
    if (hasNoSubscriptions) setDiscover(true);
  }, [hasNoSubscriptions]);

  // ── hover 预取正文(A4):150ms 去抖;命中缓存/进行中/无 id 都不发 ──
  const cancelPrefetch = useCallback(() => {
    if (prefetchTimerRef.current) {
      clearTimeout(prefetchTimerRef.current);
      prefetchTimerRef.current = null;
    }
  }, []);

  const schedulePrefetch = useCallback((article) => {
    const id = article?.id;
    if (!id || article.content != null) return;
    if (bodyCacheRef.current.has(id) || prefetchingIdsRef.current.has(id)) return;
    cancelPrefetch();
    prefetchTimerRef.current = setTimeout(() => {
      prefetchTimerRef.current = null;
      if (bodyCacheRef.current.has(id) || prefetchingIdsRef.current.has(id)) return;
      prefetchingIdsRef.current.add(id);
      fetchArticle(id)
        .then((data) => { bodyCacheRef.current.set(id, data?.content || ''); })
        .catch(() => { /* 预取失败静默:点击时正常路径兜底 */ })
        .finally(() => { prefetchingIdsRef.current.delete(id); });
    }, 150);
  }, [cancelPrefetch]);

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
    fetchArticles({ source_id: BRIEF_SOURCE_ID }, 1, 0, false, { includeContent: false })
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
        if (favOnly) {
          // 容器内收藏:范围跟随当前容器/源
          const filters = {};
          if (activeSourceId) filters.source_id = activeSourceId;
          else filters.shape = mode;
          if (searchQuery) filters.search = searchQuery;
          // 社交收藏也走卡片流,需要 extensions(引用推/转推/头像)——与非收藏分支一致
          return fetchFavorites(filters, PAGE_SIZE, skip, { signal, includeContent: mode === 'social' });
        }
        const filters = {};
        if (activeSourceId) filters.source_id = activeSourceId;
        else {
          filters.subscribed_scope = 'only'; // 聚合视图：后端硬过滤到已订阅源
          filters.shape = mode; // 容器分流(文章/动态/社交各取自己那类)
        }
        if (searchQuery) filters.search = searchQuery;
        filters.with_unread = 'true';           // 条目附页级未读标记（水位由 unread-counts 校准）
        if (unreadOnly) filters.unread_only = 'true';
        // 社交流全文直出(推文正文 2~4 行,取回零负担),且卡片要 extensions
        // (引用推/转推/图链)——那只在 include_content=true 时随列表返回。
        return fetchArticles(filters, PAGE_SIZE, skip, true, { signal, includeContent: mode === 'social' });
      });
    } catch (error) {
      showToast(error.message || '获取文章列表失败', 'error');
      if (append) setLoadingMore(false); else setArticlesLoading(false);
      return;
    }
    if (data === undefined) return; // 被更新的请求取代，loading 交给新请求，不在此清除
    if (favOnly && data.favorite_ids) setFavoriteIds(new Set(data.favorite_ids));
    const items = data.items || [];
    setArticlesTotal(data.total || 0);
    setArticles(prev => (append ? [...prev, ...items] : items));
    // 不再自动展开第一篇——避免「被动打开」污染阅读计量；右栏停在提示态，
    // 等用户主动点选一篇才加载正文并计一次阅读（见 selectArticle）。
    if (!append) setFreshCount(0); // 列表已刷新,新内容提示归零
    if (append) setLoadingMore(false); else setArticlesLoading(false);
  }, [activeSourceId, searchQuery, favOnly, unreadOnly, mode, showToast, runList]);

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

  // 无限滚动:哨兵进入视口(提前 400px)即自动追加下一页,取代「加载更多」按钮。
  // 依赖变化即重建 observer——追加后 articles.length 变、loadingMore 置真都会重新求值,
  // 天然防重入(loadingMore 时不触发)。
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingMore && !articlesLoading) {
          loadArticles(articles.length, true);
        }
      },
      { rootMargin: '400px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loadingMore, articlesLoading, articles.length, loadArticles]);

  // 列表内容高度变化(切源/追加/加载态)后重算浮层滚动条滑块
  useEffect(() => { resyncListScrollbar(); }, [articles, articlesLoading, activeArticle, resyncListScrollbar]);

  // ── 订阅 / 取消订阅 ──
  const applyResult = (result) => setSubscribedIds(new Set(result.subscribed_source_ids || []));

  // 订阅集合变化后，若正看聚合视图需显式重拉（loadArticles 已不依赖 subscribedIds，
  // 故不会自动刷新）；看具体来源时由 activeSourceId 变化驱动，无需在此处理。
  const refreshAggregateIfActive = () => {
    if (!favOnly && !activeSourceId) loadArticles(0, false);
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
      // 收藏过滤中取消收藏 → 从当前列表移除
      if (favOnly && wasFav) {
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

  // ── 全部标读(当前范围:某来源 / 本容器 / 今日全订阅)──
  const handleMarkAllRead = async () => {
    if (markingRead) return;
    setMarkingRead(true);
    try {
      const data = await markAllRead(activeSourceId, activeSourceId ? null : mode);
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

  // ── 手动标为已读/未读(显式覆盖,可撤销误触;不计阅读量)──
  // 阅读窗与社交流共用:社交流全文直出、没有「打开」动作,标读是它唯一的读态入口。
  const toggleArticleRead = useCallback(async (article) => {
    const id = article?.id;
    if (!id) return;
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
    try {
      await (toUnread ? markArticleUnread(id) : markArticleRead(id));
    } catch (error) {
      readOverridesRef.current.set(id, toUnread);
      setReadOverrides(new Map(readOverridesRef.current));
      bump(toUnread ? -1 : 1);
      showToast(error.message || '操作失败', 'error');
    }
  }, [isArticleUnread, activeSourceId, showToast]);

  const handleTogglePaneRead = async () => {
    if (!activeArticle?.id || paneReadToggling) return;
    setPaneReadToggling(true);
    try {
      await toggleArticleRead(activeArticle);
    } finally {
      setPaneReadToggling(false);
    }
  };

  const handleToggleSocialRead = useCallback(async (article) => {
    if (!article?.id || socialReadToggling) return;
    setSocialReadToggling(article.id);
    try {
      await toggleArticleRead(article);
    } finally {
      setSocialReadToggling(null);
    }
  }, [socialReadToggling, toggleArticleRead]);

  // ── 视图轨导航(容器语义):点容器钮=进入该容器聚合(源内时=回到聚合);搜索是叠加开关 ──
  // 任何内容导航都退出发现页(发现是与容器并列的一级视图,占据 条目列+阅读窗)
  const goView = (v) => {
    setDiscover(false);
    setMode(v);
    setActiveSourceId(null);
    setFavOnly(false);
    setSearchOpen(false);
    setSearchInput('');
  };
  // 单源=容器内收窄:源所属容器自动点亮(今日不承担单源,从今日点源即跳入所属容器)
  const goSource = (sourceId) => {
    setDiscover(false);
    setActiveSourceId(sourceId);
    setMode(shapeOfSource(sourceId));
    setFavOnly(false);
  };
  // 收藏入口(源栏,与「全部XX」并列):看本容器全部收藏(容器级、不逐源)。
  // Folo 语义——收藏是与「全部」并列的一级过滤,不再挂在列头逐源。
  const goContainerAll = () => { setDiscover(false); setActiveSourceId(null); setFavOnly(false); };
  const goFavorites = () => { setDiscover(false); setActiveSourceId(null); setFavOnly(true); };
  // 搜索开关(条目列头就地展开):关闭即清词(searchQuery 经防抖同步清空,列表回到无过滤)。
  // 已从视图轨降为条目列内的过滤器,只在文章/动态容器列头出现,故无需再退发现页。
  const toggleSearch = () => {
    setSearchOpen((open) => {
      if (open) setSearchInput('');
      return !open;
    });
  };
  // 视图轨激活态 = 发现页 或 当前容器(源内保持点亮——层级关系,不再互斥)
  const railActive = discover ? 'discover' : mode;

  const listTitle = favOnly
    ? '收藏'
    : activeSourceId
      ? (sourceNameMap[activeSourceId] || activeSourceId)
      : mode === 'article' ? '文章' : mode === 'social' ? '社交媒体' : '动态';

  // ── 翻页(上一篇/下一篇):沿当前列表序 ──
  const activeIndex = useMemo(
    () => (activeArticle ? articles.findIndex((a) => a.id === activeArticle.id) : -1),
    [articles, activeArticle],
  );
  const prevArticle = activeIndex > 0 ? articles[activeIndex - 1] : null;
  const nextArticle = activeIndex >= 0 && activeIndex < articles.length - 1 ? articles[activeIndex + 1] : null;

  // ── 阅读窗 crumb / 日报报头判定 ──
  const isBrief = !!activeArticle
    && (activeArticle.source_id === BRIEF_SOURCE_ID || activeArticle.content_type === 'daily_brief');
  const crumbSource = activeArticle ? sourceMap[activeArticle.source_id] : null;
  const crumbHost = activeArticle && !isBrief ? hostOf(activeArticle.source_url) : '';
  const crumbName = !activeArticle
    ? ''
    : isBrief
      ? '每日 AI 资讯日报 · 哆啦美整理'
      : `${sourceNameMap[activeArticle.source_id] || activeArticle.source_id}${crumbHost ? ` · ${crumbHost}` : ''}`;
  // 样页 meta:约 N 字 · 阅读 X 分钟(正文到位后计算;中文阅读速率取 ~400 字/分)
  const bodyStats = useMemo(() => {
    if (!activeBody) return null;
    const chars = activeBody.replace(/\s/g, '').length;
    if (chars < 100) return null;
    return { chars, minutes: Math.max(1, Math.round(chars / 400)) };
  }, [activeBody]);

  // 日期分组只在「到货序」列表上有意义;收藏过滤按收藏时间排序,不分组
  const grouping = !favOnly;

  // 预览中的未订阅源(发现页「预览」跳入,Folo 语义):源栏顶浮现锚点行,
  // 条目列头下给显眼「＋ 订阅」横幅;订阅成功后两者自然消失、源落入所属分组。
  const activeUnsubscribed = activeSourceId && !subscribedIds.has(activeSourceId)
    ? (sourceMap[activeSourceId] || { source_id: activeSourceId, name: activeSourceId })
    : null;

  return (
    <div className="reader-shell">
      {/* ── 视图轨 · 一级视图导航(样页:品牌标 + 自绘右侧 tooltip + 轨底头像) ── */}
      <nav className="reader-vrail" aria-label="阅读视图">
        {!brandFailed ? (
          <BrandLogoImage
            displaySize={32}
            alt="哆啦美"
            className="reader-vrail-brand-img"
            onError={() => setBrandFailed(true)}
          />
        ) : (
          <div className="reader-vrail-brand" title="哆啦美阅读器" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3c-3.6 0-6 2.6-6 6v4l-1.8 3h15.6L18 13V9c0-3.4-2.4-6-6-6z" />
              <path d="M10 19a2 2 0 0 0 4 0" />
            </svg>
          </div>
        )}
        {/* 四个容器:今日(混合时间线) / 文章 / 动态 / 社交媒体。收藏降为容器内过滤器(条目列头星标)。
            社交独立成容器(v3.12):动态装的是 changelog/release notes/GitHub 趋势——短条目扫读形态,
            推文是卡片流直读形态,渲染差异大到要在容器内再分叉,就说明本不该是同一个容器。 */}
        {[
          ['article', '文章', FileText],
          ['bulletin', '动态', Zap],
          ['social', '社交媒体', AtSign],
        ].map(([view, label, Icon]) => (
          <button
            key={view}
            type="button"
            aria-label={label}
            aria-pressed={railActive === view}
            onClick={() => goView(view)}
            className={`reader-vrail-btn ${railActive === view ? 'is-on' : ''}`}
          >
            <Icon className="h-[18px] w-[18px]" />
            <span className="reader-vrail-tip">{label}</span>
          </button>
        ))}
        {/* 发现:整页源目录(取代源栏内联「发现更多来源」),与容器并列的一级视图 */}
        <button
          type="button"
          aria-label="发现"
          aria-pressed={discover}
          onClick={() => setDiscover(true)}
          className={`reader-vrail-btn ${discover ? 'is-on' : ''}`}
        >
          <Compass className="h-[18px] w-[18px]" />
          <span className="reader-vrail-tip">发现</span>
        </button>

        {/* 轨底(standalone):主题/设置 直排 + 头像(点击进设置·账户)——头像菜单已退役
            (与设置页功能重复,用户拍板);接入集成/退出登录都在设置柜内 */}
        {standalone && (
          <>
            <div className="reader-vrail-spring" />
            <button
              type="button"
              onClick={() => onToggleTheme?.()}
              className="reader-vrail-btn"
              aria-label={themeDark ? '切换到亮色' : '切换到暗色'}
            >
              {themeDark ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
              <span className="reader-vrail-tip">{themeDark ? '切换亮色' : '切换暗色'}</span>
            </button>
            <button
              type="button"
              onClick={() => onOpenSettings?.()}
              className="reader-vrail-btn"
              aria-label="设置"
            >
              <Settings className="h-[18px] w-[18px]" />
              <span className="reader-vrail-tip">设置</span>
            </button>
            <button
              type="button"
              className="reader-vrail-avatar"
              title={account?.username || '账号'}
              aria-label="账号设置"
              onClick={() => onOpenSettings?.()}
            >
              {account?.avatar
                ? <img src={account.avatar} alt="" />
                : <span>{avatarText || (account?.username || '?').slice(0, 2).toUpperCase()}</span>}
            </button>
          </>
        )}
      </nav>

      {/* ── 源栏 · 我的订阅 ── */}
      <aside className="reader-col reader-col-sources">
        <div className="reader-sources-inner">
        <div className="reader-src-head">
          <span className="reader-src-title">我的订阅</span>
        </div>

        <div className="reader-source-scroll">
          {sourcesLoading ? (
            <SourceRowsSkeleton />
          ) : (
            <>
              {/* 预览锚点行(Folo):正在预览的未订阅源浮现在源栏顶部,交代「你在哪」 */}
              {activeUnsubscribed && (
                <div className="reader-subs">
                  <div className="reader-source-row reader-source-row-active">
                    <LogoMark company={resolveCompany(activeUnsubscribed)} size="s20" emoji={activeUnsubscribed.icon} />
                    <p className="reader-source-name min-w-0 flex-1">{activeUnsubscribed.name || activeUnsubscribed.source_id}</p>
                    <span className="reader-src-preview-tag">预览</span>
                  </div>
                </div>
              )}

              {/* 容器聚合入口 + 收藏入口(Folo 语义:收藏与「全部」并列,容器级过滤) */}
              <div className="reader-subs">
                <div
                  role="button"
                  tabIndex={0}
                  onClick={goContainerAll}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goContainerAll(); } }}
                  className={`reader-source-row ${activeSourceId === null && !favOnly ? 'reader-source-row-active' : ''} ${scopeUnread > 0 && activeSourceId === null && !favOnly ? 'has-unread' : ''}`}
                >
                  <span className="reader-src-allicon" aria-hidden="true">
                    {mode === 'bulletin' ? <Zap className="h-3.5 w-3.5" />
                      : socialView ? <AtSign className="h-3.5 w-3.5" />
                        : <FileText className="h-3.5 w-3.5" />}
                  </span>
                  <p className="reader-source-name min-w-0 flex-1">
                    {mode === 'bulletin' ? '全部动态' : socialView ? '全部社媒' : '全部文章'}
                  </p>
                </div>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={goFavorites}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goFavorites(); } }}
                  className={`reader-source-row ${favOnly ? 'reader-source-row-active' : ''}`}
                >
                  <span className="reader-src-allicon reader-src-allicon-fav" aria-hidden="true">
                    <Star className="h-3.5 w-3.5" fill={favOnly ? 'currentColor' : 'none'} />
                  </span>
                  <p className="reader-source-name min-w-0 flex-1">只看收藏</p>
                </div>
              </div>

              {/* 订阅来源按编辑分层分组(样页):官方·一手信息 / 媒体·观察 / 个人·洞见 / 榜单·动态。
                  源栏跟随容器(层级化):文章容器只列文章形源,动态容器只列榜单·动态,今日列全部。
                  组头=样页 .src-label 细字距灰签。退订钮浮层化:绝对定位悬停现,不占布局。 */}
              {sidebarGroups.map(({ key, label, list }) => (
                <section className="reader-subs" key={key}>
                  <div className="reader-src-label">{label}</div>
                  <div className="reader-group-body">
                  {list.map((source) => {
                    const active = activeSourceId === source.source_id;
                    const unread = unreadBySource[source.source_id] || 0;
                    return (
                      <div
                        key={source.source_id}
                        role="button"
                        tabIndex={0}
                        onClick={() => goSource(source.source_id)}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goSource(source.source_id); } }}
                        className={`reader-source-row ${active ? 'reader-source-row-active' : ''} ${unread > 0 ? 'has-unread' : ''}`}
                      >
                        {/* 社交源用真实头像(它们在 LogoMark 品牌表里没有条目,
                            否则整列会退化成同一个平台图标);图经媒体库代理 */}
                        {source.avatar_url ? (
                          <img className="reader-src-avatar" src={mediaProxyUrl(source.avatar_url)} alt="" loading="lazy" decoding="async" />
                        ) : (
                          <LogoMark company={resolveCompany(source)} size="s20" emoji={source.icon} />
                        )}
                        {/* 每源未读数字已撤(减噪 + 名字铺满右侧);未读靠行整体加粗(has-unread)示意,
                            总数看顶部「我的订阅 · N 未读」。退订钮浮层化,不占布局。 */}
                        <p className="reader-source-name min-w-0 flex-1">{source.name || source.source_id}</p>
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
                <p className="reader-side-hint">还没有订阅任何来源，在「发现」页挑选并添加。</p>
              )}

              {/* 「发现更多来源」内联子列表已退役——发现升格为整页视图(视图轨 Compass 钮) */}
              {!hasNoSubscriptions && (
                <button
                  type="button"
                  onClick={() => setDiscover(true)}
                  className="reader-src-more"
                >
                  <Compass className="h-3.5 w-3.5" />
                  <span>发现更多来源</span>
                </button>
              )}
            </>
          )}
        </div>
        </div>
      </aside>

      {/* ── 发现页:占据 条目列+阅读窗 的整片区域(源栏保持在场,订阅结果即时可见) ── */}
      {discover && (
        <DiscoverPage
          sources={sources}
          subscribedIds={subscribedIds}
          loading={sourcesLoading}
          pinningId={pinningId}
          onSubscribe={handleSubscribe}
          onUnsubscribe={handleUnsubscribe}
          onPreview={(source) => goSource(source.source_id)}
        />
      )}

      {/* ── 社交媒体流(第三容器):占「条目列 + 阅读窗」整幅,取代四带式 ── */}
      {!discover && socialView && (
        <SocialFlow
          articles={articles}
          sourceMap={sourceMap}
          sourceNameMap={sourceNameMap}
          unreadCount={scopeUnread}
          unreadOnly={unreadOnly}
          onUnreadOnlyChange={setUnreadOnly}
          isArticleUnread={isArticleUnread}
          favoriteIds={favoriteIds}
          favTogglingId={favTogglingId}
          onToggleFavorite={handleToggleFavorite}
          favOnly={favOnly}
          searchOpen={searchOpen}
          searchInput={searchInput}
          searchQuery={searchQuery}
          onSearchInputChange={setSearchInput}
          onToggleSearch={toggleSearch}
          readTogglingId={socialReadToggling}
          onToggleRead={handleToggleSocialRead}
          onMarkAllRead={handleMarkAllRead}
          markingRead={markingRead}
          loading={articlesLoading}
          hasMore={hasMore}
          loadingMore={loadingMore}
          onLoadMore={handleLoadMore}
          platformCount={platformCount}
          activeSourceId={activeSourceId}
          emptyHint={socialSources.length === 0 ? '还没有订阅社交账号，去「发现」看看' : '暂无动态'}
        />
      )}

      {/* ── 条目列 ── */}
      {!discover && !socialView && (
      <section className="reader-col reader-col-list">
        <div className="reader-list-inner">
        <div className="reader-list-head">
          {/* 搜索就地展开:输入框顶替标题+未读 seg,占满列头左侧(不新增控件,防拥挤) */}
          {searchOpen ? (
            <div className="reader-search-inline">
              <Search className="h-4 w-4 shrink-0 text-slate-500" />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="搜索我的阅读…"
                className="reader-search-input"
                autoFocus
              />
            </div>
          ) : (
            <span className="reader-list-title">{listTitle}</span>
          )}
          {/* 未读筛选(全部/未读)+ 全部标读:搜索展开或收藏过滤时让位(未读语义此时关闭)。 */}
          {!favOnly && !searchOpen && (
            <>
              <div className="reader-seg" role="tablist" aria-label="未读筛选">
                {[[false, '全部'], [true, '未读']].map(([value, label]) => (
                  <button
                    key={label}
                    type="button"
                    role="tab"
                    aria-selected={unreadOnly === value}
                    onClick={() => setUnreadOnly(value)}
                    className={`reader-seg-btn ${unreadOnly === value ? 'is-on' : ''}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={handleMarkAllRead}
                disabled={markingRead}
                aria-label={activeSourceId ? '本来源全部标为已读' : '本容器全部标为已读'}
                title={activeSourceId ? '本来源全部标为已读' : '本容器全部标为已读'}
                className="reader-unread-icon"
              >
                {markingRead ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
              </button>
            </>
          )}
          {/* 搜索开关(就地展开:图标 ↔ ✕):由视图轨降级而来的条目列过滤器,与未读/收藏同维度 */}
          <button
            type="button"
            onClick={toggleSearch}
            aria-pressed={searchOpen}
            aria-label={searchOpen ? '关闭搜索' : '搜索'}
            title={searchOpen ? '关闭搜索' : '搜索'}
            className={`reader-search-icon ${searchOpen ? 'is-on' : ''}`}
          >
            {searchOpen ? <X className="h-4 w-4" /> : <Search className="h-4 w-4" />}
          </button>
          {/* 收藏过滤器已移出列头 → 源栏「收藏」入口(容器级,与「全部XX」并列) */}
        </div>

        {/* 预览未订阅源:显眼订阅横幅(Folo 的「＋ 订阅」条),订阅成功即消失 */}
        {activeUnsubscribed && (
          <button
            type="button"
            className="reader-sub-banner"
            disabled={pinningId === activeUnsubscribed.source_id}
            onClick={() => handleSubscribe(activeUnsubscribed)}
          >
            {pinningId === activeUnsubscribed.source_id
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Plus className="h-3.5 w-3.5" />}
            订阅「{activeUnsubscribed.name || activeUnsubscribed.source_id}」
          </button>
        )}

        <div className="reader-scrollwrap">
        <div className="reader-list-scroll" ref={listRef}>
          {/* 日报置顶卡 · 报头形态:今日/文章 容器聚合流顶部的一等公民入口;过滤中(收藏/搜索/只看未读)让位 */}
          {!favOnly && !activeSourceId && mode !== 'bulletin' && !searchQuery && !unreadOnly
            && !articlesLoading && latestBrief && (
            <button type="button" className="reader-brief-card" onClick={() => selectArticle(latestBrief)}>
              <span className="reader-brief-head">
                <span className="reader-brief-name">每日 AI 资讯日报</span>
                {latestBrief.publish_date && (
                  <span className="reader-brief-date" title={formatDateTime(latestBrief.publish_date)}>
                    {briefDateOf(latestBrief.publish_date)}
                  </span>
                )}
              </span>
              <span className="reader-brief-title">{latestBrief.title || '（无标题）'}</span>
            </button>
          )}
          {/* 新内容提示条:轮询发现未读正增量时出现,点击刷新——不自动插入打断阅读 */}
          {!favOnly && !articlesLoading && freshCount > 0 && (
            <button type="button" className="reader-fresh-pill" onClick={handleRefreshFresh}>
              <RefreshCw className="h-3.5 w-3.5" />
              有 {freshCount} 篇新文章 · 点击刷新
            </button>
          )}
          {articlesLoading ? (
            <ArticleCardsSkeleton />
          ) : !favOnly && hasNoSubscriptions && !activeSourceId ? (
            <div className="reader-empty reader-empty-tall">
              <Compass className="h-7 w-7 text-slate-300" />
              <span>你还没有订阅任何来源</span>
              <button type="button" className="action-button action-button-primary" onClick={() => setDiscover(true)}>
                去发现来源
              </button>
            </div>
          ) : articles.length === 0 ? (
            <div className="reader-empty">
              {favOnly ? <Star className="h-6 w-6 text-slate-300" /> : <Inbox className="h-6 w-6 text-slate-300" />}
              <span>
                {searchQuery
                  ? '没有匹配的文章'
                  : favOnly
                    ? '当前范围还没有收藏，阅读时点右上角星标即可收藏'
                    : unreadOnly
                      ? '没有未读内容，都看完啦'
                      : activeSourceId
                        ? '该来源暂无内容'
                        : (mode === 'bulletin' ? '暂无动态' : mode === 'article' ? '暂无文章' : '暂无内容')}
              </span>
            </div>
          ) : (
            /* key 按视图范围重挂载,切源/切容器时列表整体淡入(A1) */
            <div key={`${activeSourceId ?? '__all__'}|${mode}|${favOnly ? 'fav' : 'flow'}`} className="reader-list-enter">
              {articles.map((article, index) => {
                const active = activeArticle?.id === article.id;
                const isUnread = isArticleUnread(article);
                // 条目列只在文章/动态容器渲染(社交走 SocialFlow),容器内形态同质:
                // 动态容器整条呈紧凑形(无独立标题,不挂摘要),不再需要逐条形态 chip。
                const entryBulletin = bulletinView;
                const excerpt = entryBulletin
                  ? ''
                  : excerptOf(article.summary_zh || article.content_preview || article.content);
                const isFav = favoriteIds.has(article.id);
                const key = dayKeyOf(article);
                const showLabel = grouping && (index === 0 || key !== dayKeyOf(articles[index - 1]));
                return (
                  <Fragment key={article.id}>
                    {showLabel && <div className="reader-date-label">{dayLabelOf(key)}</div>}
                    <button
                      type="button"
                      onClick={() => selectArticle(article)}
                      onMouseEnter={() => schedulePrefetch(article)}
                      onMouseLeave={cancelPrefetch}
                      className={`reader-entry ${entryBulletin ? 'is-bulletin' : ''} ${active ? 'is-active' : ''} ${isUnread ? '' : 'is-read'} ${isFav ? 'is-fav' : ''}`}
                    >
                      <span className="reader-entry-top">
                        {sourceMap[article.source_id] && (
                          <span className="reader-entry-logo" aria-hidden="true">
                            <LogoMark company={resolveCompany(sourceMap[article.source_id])} size="s15" emoji={sourceMap[article.source_id].icon} />
                          </span>
                        )}
                        <span className="reader-entry-src">{sourceNameMap[article.source_id] || article.source_id}</span>
                        <span
                          className="reader-entry-time"
                          title={formatDateTime(article.publish_date || article.fetched_date)}
                        >
                          {timeOfDay(article.fetched_date || article.publish_date)}
                        </span>
                      </span>
                      {/* 标题行:标题占位 + 右缘收藏星标(Folo 式)。星内联于标题行,
                          正文/摘要照旧铺满整宽,只标题让出星位——不再整卡右缩(修右侧留白)。
                          卡本身是 <button>,故收藏钮用 role=button 的 span,避免按钮嵌套;
                          已收藏常显琥珀实星,未收藏悬停浮出空心星、点击切换。 */}
                      <span className="reader-entry-titlerow">
                        {/* 未读小蓝点移到标题左侧栏(与右缘收藏星标错开——两者同现时不再挤在右侧);
                            绝对定位于左槽,不挤占标题宽度,已读缩零淡出 */}
                        <span className={`reader-unread-dot ${isUnread ? '' : 'is-off'}`} aria-hidden="true" />
                        <span className="reader-entry-title">{searchQuery ? highlightMatch(article.title || '（无标题）', searchQuery) : (article.title || '（无标题）')}</span>
                        <span
                          role="button"
                          tabIndex={-1}
                          aria-label={isFav ? '取消收藏' : '收藏'}
                          title={isFav ? '取消收藏' : '收藏'}
                          onClick={(e) => { e.stopPropagation(); handleToggleFavorite(article, e); }}
                          className={`reader-entry-fav ${isFav ? 'is-on' : ''}`}
                        >
                          <Star className="h-[15px] w-[15px]" fill={isFav ? 'currentColor' : 'none'} />
                        </span>
                      </span>
                      {/* 摘要行:AI 要点摘要(summary_zh)优先——正文截断对英文长文几乎无信息量 */}
                      {excerpt && <span className="reader-entry-excerpt">{searchQuery ? highlightMatch(excerpt, searchQuery) : excerpt}</span>}
                    </button>
                  </Fragment>
                );
              })}
              {/* 无限滚动:哨兵进入视口即自动追加,加载中以骨架条占位(不再有「加载更多」按钮) */}
              {hasMore && (
                <div ref={sentinelRef} className="reader-load-sentinel" aria-hidden="true">
                  {loadingMore && <ArticleCardsSkeleton count={3} delayed={false} />}
                </div>
              )}
            </div>
          )}
        </div>
        <div ref={listThumbRef} className="ovl-thumb" aria-hidden="true" />
        </div>
        </div>
      </section>
      )}

      {/* ── 阅读窗 ── */}
      {!discover && !socialView && (
      <section className="reader-col reader-col-read">
        {activeArticle ? (
          <>
            {/* 阅读进度线：仅正文非空时显示；CSS scroll() 滚动驱动、切文章天然归零，
                不支持 scroll() 的浏览器由 @supports 直接隐藏（渐进增强，无 JS 兜底）。 */}
            {!activeBodyLoading && activeBody ? (
              <div className="reader-progress" aria-hidden="true" />
            ) : null}

            {/* 顶部工具条:crumb + 动作图标组(常驻,不随正文滚走) */}
            <div className="reader-pane-bar">
              <div className="reader-crumb">
                {isBrief ? (
                  <Sparkles className="h-4 w-4 flex-none text-[var(--dorami-accent)]" aria-hidden="true" />
                ) : crumbSource ? (
                  <LogoMark company={resolveCompany(crumbSource)} size="s17" emoji={crumbSource.icon} />
                ) : null}
                <span className="reader-crumb-name">{crumbName}</span>
              </div>
              {activeArticle.source_url && (
                <a
                  href={activeArticle.source_url}
                  target="_blank"
                  rel="noreferrer"
                  title="查看来源"
                  aria-label="查看来源"
                  className="reader-pane-iconbtn"
                >
                  <ExternalLink className="h-4 w-4" />
                </a>
              )}
              <button
                type="button"
                onClick={(e) => handleToggleFavorite(activeArticle, e)}
                disabled={favTogglingId === activeArticle.id}
                title={favoriteIds.has(activeArticle.id) ? '取消收藏' : '收藏'}
                aria-label={favoriteIds.has(activeArticle.id) ? '取消收藏' : '收藏'}
                className={`reader-pane-iconbtn ${favoriteIds.has(activeArticle.id) ? 'is-amber' : ''}`}
              >
                {favTogglingId === activeArticle.id
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Star className="h-4 w-4" fill={favoriteIds.has(activeArticle.id) ? 'currentColor' : 'none'} />}
              </button>
              {/* 手动标读/标未读:撤销误触的已读,单篇切换;不计阅读量 */}
              <button
                type="button"
                onClick={handleTogglePaneRead}
                disabled={paneReadToggling}
                title={isArticleUnread(activeArticle) ? '标为已读' : '标为未读(撤销已读)'}
                aria-label={isArticleUnread(activeArticle) ? '标为已读' : '标为未读'}
                className="reader-pane-iconbtn"
              >
                {paneReadToggling
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : isArticleUnread(activeArticle)
                    ? <CheckCheck className="h-4 w-4" />
                    : <CircleDot className="h-4 w-4" />}
              </button>
              {aiEnabled && (
                <button
                  type="button"
                  onClick={handleTranslate}
                  disabled={translating || activeBodyLoading || !activeBody}
                  title={showTranslation ? '当前显示中文译文，点击切回原文' : '将正文译为中文'}
                  aria-label={showTranslation ? '显示原文' : '译为中文'}
                  aria-pressed={showTranslation}
                  className={`reader-pane-iconbtn ${showTranslation ? 'is-blue' : ''}`}
                >
                  {translating
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <span className="reader-tr-glyph" aria-hidden="true">译</span>}
                </button>
              )}
            </div>

          {/* key 按文章 id 重挂载,触发 reader-enter 淡入+轻上移(体验二波 A1) */}
          <article className="reader-pane reader-enter" key={activeArticle.id}>
            {isBrief ? (
              /* 日报报头(样页):衬线居中刊名 + 双细线,整页唯一的「报纸时刻」 */
              <header className="reader-brief-mast">
                <h1>每日 AI 资讯日报</h1>
                <div className="reader-brief-mast-date">
                  {activeArticle.publish_date ? briefMastDateOf(activeArticle.publish_date) : (activeArticle.title || '')} · 由哆啦美整理
                </div>
              </header>
            ) : (
              <header className="reader-pane-head">
                <div className="reader-kicker">
                  {(sourceNameMap[activeArticle.source_id] || activeArticle.source_id)}
                  {activeArticle.content_type
                    ? ` · ${contentTypeLabel(activeArticle.content_type, activeArticle.content_type)}`
                    : ''}
                </div>
                <h1 className="reader-pane-title">{activeArticle.title || '（无标题）'}</h1>
                <div className="reader-pane-meta">
                  {activeArticle.publish_date && (
                    <span title={formatRelativeTime(activeArticle.publish_date)}>
                      {formatDateTime(activeArticle.publish_date)}
                    </span>
                  )}
                  {bodyStats && <span>约 {bodyStats.chars.toLocaleString()} 字</span>}
                  {bodyStats && <span>阅读 {bodyStats.minutes} 分钟</span>}
                </div>
              </header>
            )}
            <div className="reader-pane-body markdown-body">
              {/* 哆啦美速读:有缓存直接展示;无缓存给低调的生成入口(MVP 不自动生成,控成本) */}
              {aiEnabled && !activeBodyLoading && (activeSummary || activeBody) && (
                <div className="reader-ai-summary">
                  <div className="reader-ai-summary-head">
                    <Sparkles className="h-3.5 w-3.5" /> 哆啦美速读
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
                        : '生成本文要点速读'}
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
                '该文章暂无正文内容，点击「查看来源」阅读完整内容。'
              )}
            </div>
            {/* 上一篇/下一篇:沿当前列表序的真实翻页(选中项不在列表时隐藏,如日报置顶卡) */}
            {activeIndex >= 0 && (prevArticle || nextArticle) && (
              <nav className="reader-pager" aria-label="上一篇 / 下一篇">
                <button
                  type="button"
                  className="reader-pager-btn"
                  disabled={!prevArticle}
                  onClick={() => prevArticle && selectArticle(prevArticle)}
                >
                  <span className="reader-pager-dir">← 上一篇</span>
                  <span className="reader-pager-title">{prevArticle ? (prevArticle.title || '（无标题）') : '已是最新一篇'}</span>
                </button>
                <button
                  type="button"
                  className="reader-pager-btn reader-pager-next"
                  disabled={!nextArticle}
                  onClick={() => nextArticle && selectArticle(nextArticle)}
                >
                  <span className="reader-pager-dir">下一篇 →</span>
                  <span className="reader-pager-title">{nextArticle ? (nextArticle.title || '（无标题）') : '已到列表末尾'}</span>
                </button>
              </nav>
            )}
          </article>
          </>
        ) : (
          <div className="reader-empty reader-empty-read">
            <BookOpenText className="h-8 w-8 text-slate-300" />
            <span>从中间选择一篇文章开始阅读</span>
          </div>
        )}
      </section>
      )}

      {!discover && <ReaderAiPanel aiEnabled={aiEnabled} activeArticle={activeArticle} showToast={showToast} />}
    </div>
  );
}
