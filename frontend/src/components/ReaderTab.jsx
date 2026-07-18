import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
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
  FileText,
  Zap,
  Star,
  CheckCheck,
  CircleDot,
  RefreshCw,
  Sparkles,
  Plug2,
  Settings,
  Sun,
  Moon,
  LogOut,
} from 'lucide-react';
import LogoMark from './LogoMark';
import BrandLogoImage from './BrandLogoImage';
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

// ── 日期分组 & 条目时刻(重构:条目列按到货日分组,组内条目只标 HH:mm) ──
// 分组轴 = fetched_date(列表排序字段,与未读水位同轴,组序天然单调);publish 兜底。
const fmtDayKey = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const dayKeyOf = (article) => {
  const raw = article?.fetched_date || article?.publish_date;
  if (!raw) return '';
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? '' : fmtDayKey(d);
};

const WEEKDAY_CHARS = ['日', '一', '二', '三', '四', '五', '六'];

// 样页组头格式:「今天 · 07-18」「昨天 · 07-17」「07-16 · 四」
const dayLabelOf = (key) => {
  if (!key) return '更早';
  const now = new Date();
  const mmdd = `${key.slice(5, 7)}-${key.slice(8, 10)}`;
  if (key === fmtDayKey(now)) return `今天 · ${mmdd}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === fmtDayKey(yesterday)) return `昨天 · ${mmdd}`;
  const d = new Date(`${key}T00:00:00`);
  return Number.isNaN(d.getTime()) ? mmdd : `${mmdd} · ${WEEKDAY_CHARS[d.getDay()]}`;
};

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

const timeOfDay = (raw) => {
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

// 日报的源标识(置顶卡/报头形态判定)
const BRIEF_SOURCE_ID = 'dorami_daily_brief';

// ── 源栏编辑分层(样页:官方·一手信息 / 媒体·观察 / 个人·洞见 / 榜单·动态) ──
// 由策展元数据推导:动态形先归「榜单·动态」;个人层看 tier2/个人评论 scope;
// 媒体层看媒体/社区 scope 或 tier1;其余(tier0 官方博客/发布厅)归「官方」。
const EDITORIAL_GROUPS = [
  { key: 'official', label: '官方 · 一手信息' },
  { key: 'media', label: '媒体 · 观察' },
  { key: 'personal', label: '个人 · 洞见' },
  { key: 'bulletin', label: '榜单 · 动态' },
];

const MEDIA_SCOPES = new Set([
  'ai_media', 'tech_media', 'community', 'developer_community', 'research_community', 'forum',
]);

const editorialGroupOf = (source) => {
  if ((source.shape || 'article') === 'bulletin') return 'bulletin';
  if (source.provenance_tier === 'tier2_personal_social' || source.source_scope === 'personal_commentary') return 'personal';
  if (MEDIA_SCOPES.has(source.source_scope) || source.provenance_tier === 'tier1_curated') return 'media';
  return 'official';
};

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
function ArticleCardsSkeleton() {
  const cards = [
    { title: 'w-3/4', excerpt: 'w-1/2' },
    { title: 'w-5/6', excerpt: 'w-2/3' },
    { title: 'w-2/3', excerpt: 'w-3/5' },
    { title: 'w-4/5', excerpt: 'w-1/2' },
    { title: 'w-3/5', excerpt: 'w-2/3' },
  ];
  return (
    <div className="skeleton-delay" aria-hidden="true">
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
  onOpenIntegrations,
  onLogout,
}) {
  const [sources, setSources] = useState([]);
  const [subscribedIds, setSubscribedIds] = useState(() => new Set());
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [activeSourceId, setActiveSourceId] = useState(null); // null = 聚合视图(今日/文章/动态)
  const [showFavorites, setShowFavorites] = useState(false); // true = 「收藏」视图
  const [favoriteIds, setFavoriteIds] = useState(() => new Set());
  const [favTogglingId, setFavTogglingId] = useState(null);
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [brandFailed, setBrandFailed] = useState(false); // 品牌 logo 加载失败 → 回退铃铛
  const [userMenuOpen, setUserMenuOpen] = useState(false); // 轨底头像菜单
  const userMenuRef = useRef(null);

  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false); // 视图轨「搜索」开合中栏搜索行

  // ── 内容形态视图轴(重构升格为视图轨一级导航) ──
  // 'all' = 今日(混合流,默认) / 'article' = 文章 / 'bulletin' = 动态。
  // 单源视图不需要该轴(源是形态同质的),由源自身 shape 决定卡片密度。
  const [shape, setShape] = useState('all');

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

  // ── 用户面 AI · 要点摘要(正文顶部「哆啦美速读」卡;缓存 id → 摘要)──
  const [activeSummary, setActiveSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const summaryCacheRef = useRef(new Map());

  // ── 日报置顶卡:最新一期 AI 资讯日报(独立拉取,不依赖订阅关系)──
  const [latestBrief, setLatestBrief] = useState(null);

  const listRef = useRef(null);
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
    // 视图范围:单源看该源;聚合按形态轴口径(今日=全形态,文章/动态各自独立提示)
    const scope = activeSourceId
      ? (bySource[activeSourceId] || 0)
      : Object.entries(bySource).reduce(
          (sum, [sid, n]) =>
            sum + (shape === 'all' || (sourceShapeMap[sid] === 'bulletin' ? 'bulletin' : 'article') === shape ? n : 0),
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

  // 订阅源按编辑分层分组(样页),空组不渲染
  const subscribedGroups = useMemo(() => {
    const buckets = { official: [], media: [], personal: [], bulletin: [] };
    for (const s of subscribedSources) buckets[editorialGroupOf(s)].push(s);
    return EDITORIAL_GROUPS
      .map((g) => ({ ...g, list: buckets[g.key] }))
      .filter((g) => g.list.length > 0);
  }, [subscribedSources]);

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

  // 未读按形态拆分:驱动视图轨口径与源栏头的未读总数
  const unreadByShape = useMemo(() => {
    const totals = { article: 0, bulletin: 0 };
    for (const [sid, n] of Object.entries(unreadBySource)) {
      totals[sourceShapeMap[sid] === 'bulletin' ? 'bulletin' : 'article'] += n;
    }
    return totals;
  }, [unreadBySource, sourceShapeMap]);

  const unreadTotal = unreadByShape.article + unreadByShape.bulletin;

  // 当前列表范围的未读小计(条目列头读数)
  const scopeUnread = useMemo(() => {
    if (showFavorites) return 0;
    if (activeSourceId) return unreadBySource[activeSourceId] || 0;
    if (shape === 'all') return unreadTotal;
    return unreadByShape[shape] || 0;
  }, [showFavorites, activeSourceId, unreadBySource, unreadByShape, unreadTotal, shape]);

  // 当前列表是否整体呈动态形(决定卡片密度):单源看源的 shape,聚合看视图轴
  const bulletinView = !showFavorites && (
    activeSourceId ? sourceShapeMap[activeSourceId] === 'bulletin' : shape === 'bulletin'
  );
  // 今日混合流:逐条判形态,动态条目带「动态」chip
  const mixedFlow = !showFavorites && !activeSourceId && shape === 'all';

  const hasNoSubscriptions = !sourcesLoading && subscribedSources.length === 0;

  // 零订阅时自动展开「发现更多来源」，引导用户添加
  useEffect(() => {
    if (hasNoSubscriptions) setDiscoverOpen(true);
  }, [hasNoSubscriptions]);

  // 轨底用户菜单:点外/Esc 关闭
  useEffect(() => {
    if (!userMenuOpen) return undefined;
    const onDown = (e) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target)) setUserMenuOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setUserMenuOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [userMenuOpen]);

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
        if (showFavorites) {
          const filters = {};
          if (activeSourceId) filters.source_id = activeSourceId; // 收藏视图跟随当前来源
          if (searchQuery) filters.search = searchQuery;
          return fetchFavorites(filters, PAGE_SIZE, skip, { signal, includeContent: false });
        }
        const filters = {};
        if (activeSourceId) filters.source_id = activeSourceId;
        else {
          filters.subscribed_scope = 'only'; // 聚合视图：后端硬过滤到已订阅源
          if (shape !== 'all') filters.shape = shape; // 文章/动态分流;今日=混合不传
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

  // 订阅集合变化后，若正看聚合视图需显式重拉（loadArticles 已不依赖 subscribedIds，
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

  // ── 视图轨导航:今日/文章/动态/收藏 互斥,搜索是叠加开关 ──
  const goView = (v) => {
    if (v === 'fav') { setShowFavorites(true); setActiveSourceId(null); return; }
    setShowFavorites(false);
    setActiveSourceId(null);
    setShape(v === 'today' ? 'all' : v);
  };
  // 单源视图(左栏导航,离开收藏视图)
  const goSource = (sourceId) => { setShowFavorites(false); setActiveSourceId(sourceId); };
  // 搜索开关:关闭即清词(searchQuery 经防抖同步清空,列表回到无过滤)
  const toggleSearch = () => {
    setSearchOpen((open) => {
      if (open) setSearchInput('');
      return !open;
    });
  };
  // 视图轨激活态:选中单源时轨上不点亮(范围属于左栏)
  const railActive = activeSourceId ? null : (showFavorites ? 'fav' : shape === 'all' ? 'today' : shape);

  const listTitle = showFavorites
    ? '收藏'
    : activeSourceId
      ? (sourceNameMap[activeSourceId] || activeSourceId)
      : shape === 'all' ? '今日' : shape === 'article' ? '文章' : '动态';

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

  // 日期分组只在「到货序」列表上有意义;收藏按收藏时间排序,不分组
  const grouping = !showFavorites;

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
        {[
          ['today', '今日', Inbox],
          ['article', '文章', FileText],
          ['bulletin', '动态', Zap],
          ['fav', '收藏', Star],
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
        <button
          type="button"
          aria-label="搜索"
          aria-pressed={searchOpen}
          onClick={toggleSearch}
          className={`reader-vrail-btn ${searchOpen ? 'is-on' : ''}`}
        >
          <Search className="h-[18px] w-[18px]" />
          <span className="reader-vrail-tip">搜索</span>
        </button>

        {/* 轨底(standalone):应用导轨的 设置/主题/退出/接入集成 并入单一头像菜单(样页头像位) */}
        {standalone && (
          <>
            <div className="reader-vrail-spring" />
            <div className="reader-vrail-user" ref={userMenuRef}>
              <button
                type="button"
                className="reader-vrail-avatar"
                aria-haspopup="menu"
                aria-expanded={userMenuOpen}
                title={account?.username || '账号'}
                onClick={() => setUserMenuOpen((o) => !o)}
              >
                {account?.avatar
                  ? <img src={account.avatar} alt="" />
                  : <span>{avatarText || (account?.username || '?').slice(0, 2).toUpperCase()}</span>}
              </button>
              {userMenuOpen && (
                <div className="reader-user-menu" role="menu" aria-label="账号菜单">
                  <div className="reader-user-menu-head">
                    {account?.username || '读者'}
                    <span>读者</span>
                  </div>
                  <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); onOpenIntegrations?.(); }}>
                    <Plug2 className="h-[15px] w-[15px]" /> 接入集成
                  </button>
                  <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); onOpenSettings?.(); }}>
                    <Settings className="h-[15px] w-[15px]" /> 设置
                  </button>
                  <button type="button" role="menuitem" onClick={() => onToggleTheme?.()}>
                    {themeDark ? <Sun className="h-[15px] w-[15px]" /> : <Moon className="h-[15px] w-[15px]" />}
                    {themeDark ? '切换亮色' : '切换暗色'}
                  </button>
                  <div className="reader-user-menu-sep" aria-hidden="true" />
                  <button type="button" role="menuitem" className="is-danger" onClick={() => onLogout?.()}>
                    <LogOut className="h-[15px] w-[15px]" /> 退出登录
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </nav>

      {/* ── 源栏 · 我的订阅 ── */}
      <aside className="reader-col reader-col-sources">
        <div className="reader-sources-inner">
        <div className="reader-src-head">
          <span className="reader-src-title">我的订阅</span>
          {unreadTotal > 0 && (
            <span className="reader-src-unread" title={`${unreadTotal} 篇未读`}>{formatBadge(unreadTotal)} 未读</span>
          )}
        </div>

        <div className="reader-source-scroll">
          {sourcesLoading ? (
            <SourceRowsSkeleton />
          ) : (
            <>
              {/* 订阅来源按编辑分层分组(样页):官方·一手信息 / 媒体·观察 / 个人·洞见 / 榜单·动态。
                  组头=样页 .src-label 细字距灰签。退订钮浮层化:绝对定位悬停现,不占布局。 */}
              {subscribedGroups.map(({ key, label, list }) => (
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
                        <LogoMark company={resolveCompany(source)} size="s20" emoji={source.icon} />
                        <p className="reader-source-name min-w-0 flex-1">{source.name || source.source_id}</p>
                        {unread > 0 && (
                          <span className="reader-src-count" title={`${unread} 篇未读`}>
                            {formatBadge(unread)}
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

              {/* ── 发现更多来源(样页 .src-more 虚线幽灵行) ── */}
              {discoverSources.length > 0 && (
                <section className="reader-discover">
                  <button
                    type="button"
                    onClick={() => setDiscoverOpen(o => !o)}
                    className="reader-src-more"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    <span>发现更多来源</span>
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
                              <LogoMark company={resolveCompany(source)} size="s20" emoji={source.icon} />
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

      {/* ── 条目列 ── */}
      <section className="reader-col reader-col-list">
        <div className="reader-list-inner">
        <div className="reader-list-head">
          <span className="reader-list-title">
            {listTitle}
            <span className="reader-list-sub">
              {articlesTotal} 条{scopeUnread > 0 ? ` · ${formatBadge(scopeUnread)} 未读` : ''}
            </span>
          </span>
          {/* 未读筛选(全部/未读)+ 全部标读。收藏视图下不适用。 */}
          {!showFavorites && (
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
                aria-label={activeSourceId ? '本来源全部标为已读' : '全部订阅标为已读'}
                title={activeSourceId ? '本来源全部标为已读' : '全部订阅标为已读'}
                className="reader-unread-icon"
              >
                {markingRead ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
              </button>
            </>
          )}
        </div>

        {/* 搜索行:视图轨「搜索」开合 */}
        {searchOpen && (
          <div className="reader-search-row">
            <Search className="h-4 w-4 text-slate-500" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="搜索我的阅读…"
              className="reader-search-input"
              autoFocus
            />
          </div>
        )}

        <div className="reader-list-scroll" ref={listRef}>
          {/* 日报置顶卡 · 报头形态:今日/文章 流顶部的一等公民入口;过滤中(搜索/只看未读)让位 */}
          {!showFavorites && !activeSourceId && shape !== 'bulletin' && !searchQuery && !unreadOnly
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
                    ? '还没有收藏任何文章，阅读时点右上角星标即可收藏'
                    : unreadOnly
                      ? '没有未读内容，都看完啦'
                      : activeSourceId
                        ? '该来源暂无内容'
                        : (shape === 'bulletin' ? '暂无动态' : shape === 'article' ? '暂无文章' : '暂无内容')}
              </span>
            </div>
          ) : (
            /* key 按视图范围重挂载,切源/切视图时列表整体淡入(A1) */
            <div key={`${activeSourceId ?? '__all__'}|${shape}|${showFavorites ? 'fav' : 'flow'}`} className="reader-list-enter">
              {articles.map((article, index) => {
                const active = activeArticle?.id === article.id;
                const isUnread = isArticleUnread(article);
                const entryBulletin = bulletinView
                  || (mixedFlow && sourceShapeMap[article.source_id] === 'bulletin');
                const excerpt = entryBulletin
                  ? ''
                  : excerptOf(article.summary_zh || article.content_preview || article.content);
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
                      className={`reader-entry ${entryBulletin ? 'is-bulletin' : ''} ${active ? 'is-active' : ''} ${isUnread ? '' : 'is-read'}`}
                    >
                      <span className="reader-entry-top">
                        {sourceMap[article.source_id] && (
                          <span className="reader-entry-logo" aria-hidden="true">
                            <LogoMark company={resolveCompany(sourceMap[article.source_id])} size="s15" emoji={sourceMap[article.source_id].icon} />
                          </span>
                        )}
                        <span className="reader-entry-src">{sourceNameMap[article.source_id] || article.source_id}</span>
                        {mixedFlow && entryBulletin && <span className="reader-shape-chip">动态</span>}
                        <span
                          className="reader-entry-time"
                          title={formatDateTime(article.publish_date || article.fetched_date)}
                        >
                          {timeOfDay(article.fetched_date || article.publish_date)}
                        </span>
                        {/* 圆点常驻渲染:已读时缩零淡出(A2;条件卸载无法过渡) */}
                        <span className={`reader-unread-dot ${isUnread ? '' : 'is-off'}`} aria-hidden="true" />
                      </span>
                      <span className="reader-entry-title">{article.title || '（无标题）'}</span>
                      {/* 摘要行:AI 要点摘要(summary_zh)优先——正文截断对英文长文几乎无信息量 */}
                      {excerpt && <span className="reader-entry-excerpt">{excerpt}</span>}
                    </button>
                  </Fragment>
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

      {/* ── 阅读窗 ── */}
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

      <ReaderAiPanel aiEnabled={aiEnabled} activeArticle={activeArticle} showToast={showToast} />
    </div>
  );
}
