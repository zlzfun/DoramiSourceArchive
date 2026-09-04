import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { looksChinese } from '../utils/readerText';
import {
  Check,
  CheckCheck,
  ExternalLink,
  Eye,
  EyeOff,
  Link,
  Star,
  StarOff,
  Trash2,
  Undo2,
} from 'lucide-react';
import { copyText } from '../utils/clipboard';
import { articleDeepLink } from '../utils/shareLink';
import { stripDuplicateLeadingHeading } from '../utils/markdownTitle';
import {
  analysisItemsFromResponse,
  analysisNeedsPolling,
  preferredAnalysisSummary,
} from '../utils/analysis';
import { SOURCE_ROLES, sourceRoleOf } from '../sourceTaxonomy';
import { usePolling } from './usePolling';
import { useDebouncedValue } from './useDebouncedValue';
import { useAbortableLoad } from './useAbortableLoad';
import {
  fetchReaderSources,
  fetchReaderCollections,
  subscribeCollection,
  unsubscribeCollection,
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
  setSourceVisibility,
  createCustomSource,
  removeCustomSource,
} from '../api';

const PAGE_SIZE = 30;
const UNREAD_POLL_MS = 60000; // 未读轻轮询间隔（标签页可见时才真正请求）
const ANALYSIS_POLL_MS = 30000;
const ANALYSIS_PROJECTION_KEYS = [
  'analysis_status',
  'tagging_status',
  'analysis_has_result',
  'analysis_next_attempt_at',
  'quality_score',
  'score_reason',
  'summary_zh',
  'content_genre',
  'primary_tag',
  'tags',
  'display_tags',
];

function withFreshAnalysis(article, incoming) {
  if (!article || !incoming) return article;
  const next = { ...article };
  for (const key of ANALYSIS_PROJECTION_KEYS) next[key] = incoming[key];
  return next;
}

// crumb 的「源名 · 域名」域名段(样页:Simon Willison · simonwillison.net)
const hostOf = (url) => {
  if (!url) return '';
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
};

/**
 * 阅读器数据层 hook(移动波 Wave1 抽取,原逻辑整段来自 ReaderTab.jsx)。
 *
 * 职责边界:数据与动作——源目录/订阅、未读体系、收藏、列表加载(容器模型/搜索/
 * 无限滚动)、正文与 AI 缓存、深链落地、上下文菜单 items 构建。**视图胶水不在此**:
 * overlay 滚动条、memo 行的 latest-ref 稳定回调、右键弹层定位(useContextMenu)、
 * 品牌图回退等仍留在各视图组件——桌面(ReaderTab 四带式)与移动壳(页面栈)
 * 消费同一份本 hook,只各写自己的 JSX 与交互原语(hover/右键 vs 常显/长按)。
 *
 * listRef/sentinelRef 由本 hook 持有并返回:listRef 是切作用域时 scrollTop 归零的
 * 滚动容器,sentinelRef 是无限滚动哨兵(IO 未传 root,视口即根,两种布局通用);
 * 视图不挂它们也不会坏——归零与哨兵观察都对 null 容忍。
 */
export function useReaderState({
  showToast,
  account = null,
  // ── 站内分享深链(#/reader/a/{id}):带 id 进来时直接开这篇,消费后回调清空 ──
  initialArticleId = '',
  onDeepLinkConsumed,
  onBeforeOpenArticle,
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
  // 无论从哪个内容容器进入，发现页都展示完整来源目录；内容形态由页内筛选切换。
  const openDiscover = useCallback(() => setDiscover(true), []);
  const closeDiscover = useCallback(() => {
    setDiscover(false);
  }, []);
  // ── 源合集(策展合集):发现页「源 ⇄ 合集」视图的合集半边 ──
  // 目录来自 GET /api/reader/collections(轻载荷,成员卡数据由前端与 sources join);
  // discoverCollectionId 提升到本 hook(而非 DiscoverPage 局部态)是为移动端返回键:
  // MobileReader 需要把「合集详情」注册为独立历史层,返回先退详情再退发现页。
  const [collections, setCollections] = useState([]);
  const [discoverCollectionId, setDiscoverCollectionId] = useState(null);
  const [collectionPinningId, setCollectionPinningId] = useState(null);

  const [searchInput, setSearchInputState] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [displayTagQuery, setDisplayTagQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false); // 「搜索」开合中栏搜索行

  const setSearchInput = useCallback((value) => {
    setSearchInputState(value);
    setDisplayTagQuery('');
  }, []);
  const searchForLabel = useCallback((label) => {
    const value = String(label || '').trim();
    if (!value) return;
    setSearchOpen(true);
    setSearchInputState(value);
    setSearchQuery(value);
    setDisplayTagQuery(value);
  }, []);

  // ── 容器模型(Folo 语义):文章/播客/动态/社交是四个内容宇宙,各自渲染形态不同 ──
  // 'article'(默认) | 'podcast' | 'bulletin' | 'social'。选中源=在容器内收窄(mode 与 activeSourceId
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
  const [shareOpen, setShareOpen] = useState(false);   // 分享浮层（切文章时关闭，见下方 effect）
  const deepLinkKeepRef = useRef(false);               // 深链落地中:作用域清场保留右栏(见 useLayoutEffect)

  // ── 用户面 AI · 译为中文（问答浮层已抽到 ReaderAiPanel，自持其状态）──
  const [showTranslation, setShowTranslation] = useState(false);  // 右栏是否展示译文
  const [translating, setTranslating] = useState(false);
  const [translatedBody, setTranslatedBody] = useState(null);
  const [translatedTitle, setTranslatedTitle] = useState(null);  // v3.45:中文标题随译文一起
  const translationCacheRef = useRef(new Map());                  // id → { body, title }

  // ── 用户面 AI · 要点摘要(正文顶部「哆啦美速读」卡;缓存 id → 摘要)──
  const [activeSummary, setActiveSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const summaryCacheRef = useRef(new Map());

  const listRef = useRef(null);      // 列表滚动容器(切作用域 scrollTop 归零)
  const sentinelRef = useRef(null);  // 无限滚动哨兵:进入视口即追加下一页
  // 正文缓存（id → content）+ 「最新选中 id」防竞态：快速连点时丢弃晚到的过期正文响应
  const bodyCacheRef = useRef(new Map());
  const activeIdRef = useRef(null);
  // hover 预取(体验二波 A4):悬停 150ms 即预拉正文进缓存,点击零等待——「丝滑」的实质。
  const prefetchTimerRef = useRef(null);
  const prefetchingIdsRef = useRef(new Set());
  // 列表加载的竞态安全器：切源/搜索时慢的旧请求若晚返回会「后发先至」覆盖当前源列表，
  // runList 发新请求前 abort 掉旧的（与 DataTab 同一约定）。
  const runList = useAbortableLoad();
  const analysisPollContextRef = useRef(null);

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

  // 合集目录(策展注册表直出,低频变化):挂载拉一次;失败静默——合集是发现页的
  // 增强视图,目录取不到时「源」视图完整可用,不值得打断。
  useEffect(() => {
    let cancelled = false;
    fetchReaderCollections()
      .then((data) => { if (!cancelled) setCollections(data.collections || []); })
      .catch(() => { /* 非关键路径,静默 */ });
    return () => { cancelled = true; };
  }, []);

  // 离开发现页即退出合集详情(下次进入回到发现页默认视图)
  useEffect(() => {
    if (!discover) {
      setDiscoverCollectionId(null);
    }
  }, [discover]);

  // 进入阅读器先取一次收藏 ID 集合，让订阅/来源视图的文章卡也能显示收藏态。
  const loadFavoriteIds = useCallback(async () => {
    try {
      const data = await fetchFavorites({}, 1, 0);
      setFavoriteIds(new Set(data.favorite_ids || []));
    } catch { /* 收藏态非关键路径，静默失败 */ }
  }, []);

  useEffect(() => { loadFavoriteIds(); }, [loadFavoriteIds]);

  // source_id → 形态('article'|'bulletin'|'social'|'podcast'),目录未含的源按文章形兜底
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
      return ['bulletin', 'social', 'podcast'].includes(shape) ? shape : 'article';
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
  usePolling(loadUnreadCounts, UNREAD_POLL_MS);

  // 搜索防抖
  const debouncedSearchInput = useDebouncedValue(searchInput, 300);
  useEffect(() => { setSearchQuery(debouncedSearchInput.trim()); }, [debouncedSearchInput]);

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

  // 当前选中源被临时隐藏(「暂不可用」):已订阅条目留在源栏,内容不再交付——
  // 列表请求就地短路成空态,保证 admin/读者两种会话在阅读器里同观感。
  const activeSourceHidden = !!(activeSourceId && sourceMap[activeSourceId]?.hidden);

  // 发现页目录不含暂不可用源(已订阅者仅在源栏保留退订/等待入口,不再对外招订)
  const discoverSources = useMemo(() => sources.filter((s) => !s.hidden), [sources]);

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
    const totals = { article: 0, bulletin: 0, social: 0, podcast: 0 };
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
  // 播客容器仍采用「条目列 + 详情窗」，但条目和详情顶部使用音频专属呈现。
  const podcastView = mode === 'podcast';

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
    if (hasNoSubscriptions) openDiscover();
  }, [hasNoSubscriptions, openDiscover]);

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
    setShareOpen(false);   // 分享浮层属于「上一篇」,换篇即收

    const id = article?.id || null;
    activeIdRef.current = id;
    // 主动打开一篇新文章即记一次阅读（同篇连点不重复计；fire-and-forget）。
    // 后端同一请求里双写计量+逐篇已读状态;此处同步做乐观清点:圆点即消、未读数-1。
    if (id && id !== prevId) {
      // 上报成功即回填全站累计阅读数(含本次),阅读窗 meta 行就地刷新;失败静默。
      recordArticleRead(id).then((res) => {
        if (typeof res?.read_count === 'number') {
          setActiveArticle((prev) => (prev?.id === id ? { ...prev, read_count: res.read_count } : prev));
        }
      }).catch(() => {});
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
    {
      const cachedTr = id ? translationCacheRef.current.get(id) : null;
      setTranslatedBody(cachedTr?.body ?? null);
      setTranslatedTitle(cachedTr?.title ?? null);
    }
    // 摘要:会话缓存 → 列表条目自带的 summary_zh(服务端缓存)→ 空(显示生成入口)
    setSummarizing(false);
    setActiveSummary(id ? preferredAnalysisSummary(
      summaryCacheRef.current.get(id),
      article.summary_zh,
    ) : null);
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
        if (activeIdRef.current === id) {
          // 详情响应同时补齐收藏列表未必具备的媒体/分析投影；列表项已有的
          // 乐观 read_count 等字段仍由浅合并保留。
          setActiveArticle((prev) => (
            prev?.id === id
              ? withFreshAnalysis({ ...prev, ...(data?.podcast ? { podcast: data.podcast } : {}) }, data)
              : prev
          ));
          setArticles((prev) => prev.map((item) => (
            item.id === id ? withFreshAnalysis(item, data) : item
          )));
          setActiveSummary(preferredAnalysisSummary(
            summaryCacheRef.current.get(id),
            data?.summary_zh,
          ));
          setActiveBody(body);
          setActiveBodyLoading(false);
        }
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

  // ── AI · 一键译为中文（结果按 id 缓存，再次切回直接复用）──
  const handleTranslate = useCallback(async () => {
    const id = activeArticle?.id;
    if (!id) return;
    if (showTranslation) { setShowTranslation(false); return; }
    const cached = translationCacheRef.current.get(id);
    // 译名缺失(上次标题翻译失败,服务端 title=null 且未缓存)→ 不定格回退值,再调一次:
    // 正文在服务端命中缓存,只补译标题(codex 检视:此前把回退当译名缓存,整会话不再重试)
    if (cached && cached.title) { setTranslatedBody(cached.body); setTranslatedTitle(cached.title); setShowTranslation(true); return; }
    if (cached) { setTranslatedBody(cached.body); setTranslatedTitle(null); setShowTranslation(true); }
    setTranslating(true);
    try {
      const data = await translateArticle(id);
      const entry = { body: data.translation, title: data.title || null };
      translationCacheRef.current.set(id, entry);
      if (activeIdRef.current === id) {
        setTranslatedBody(entry.body);
        setTranslatedTitle(entry.title);
        setShowTranslation(true);
      }
    } catch (error) {
      showToast(error.message || '翻译失败，请稍后重试', 'error');
    } finally {
      if (activeIdRef.current === id) setTranslating(false);
    }
  }, [activeArticle, showTranslation, showToast]);

  // ── 文章列表 ──
  const loadArticles = useCallback(async (skip = 0, append = false) => {
    if (activeSourceHidden) {
      // 暂不可用源不发请求:读者侧服务端本就返回空,admin 会话的按源查询不过滤,
      // 前端统一短路,空态与文案由渲染层给出。
      setArticles([]);
      setArticlesTotal(0);
      setArticlesLoading(false);
      setLoadingMore(false);
      setFreshCount(0);
      return;
    }
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
          if (displayTagQuery) filters.display_tag = displayTagQuery;
          else if (searchQuery) filters.search = searchQuery;
          // 社交收藏也走卡片流,需要 extensions(引用推/转推/头像)——与非收藏分支一致
          return fetchFavorites(filters, PAGE_SIZE, skip, { signal, includeContent: mode === 'social' });
        }
        const filters = {};
        if (activeSourceId) filters.source_id = activeSourceId;
        else {
          filters.subscribed_scope = 'only'; // 聚合视图：后端硬过滤到已订阅源
          filters.shape = mode; // 容器分流(文章/动态/社交各取自己那类)
        }
        if (displayTagQuery) filters.display_tag = displayTagQuery;
        else if (searchQuery) filters.search = searchQuery;
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
  }, [activeSourceId, activeSourceHidden, searchQuery, displayTagQuery, favOnly, unreadOnly, mode, showToast, runList]);

  // 分析任务与采集解耦：列表首拉可能拿到 pending/running。只在确有在途项时
  // 每 30 秒静默重取当前已加载窗口，既不闪骨架屏也不弹失败 toast；响应回写前
  // 校验 scopeKey，避免切源/搜索后旧轮询污染新列表。
  const analysisScopeKey = JSON.stringify([
    activeSourceId,
    activeSourceHidden,
    searchQuery,
    displayTagQuery,
    favOnly,
    unreadOnly,
    mode,
  ]);
  const analysisPollingEnabled = !discover && !activeSourceHidden && (
    articles.some(analysisNeedsPolling)
    || analysisNeedsPolling(activeArticle)
  );
  useEffect(() => {
    analysisPollContextRef.current = {
      activeArticle,
      activeSourceId,
      articles,
      displayTagQuery,
      favOnly,
      mode,
      scopeKey: analysisScopeKey,
      searchQuery,
      unreadOnly,
    };
  }, [
    activeArticle,
    activeSourceId,
    analysisScopeKey,
    articles,
    displayTagQuery,
    favOnly,
    mode,
    searchQuery,
    unreadOnly,
  ]);
  const refreshAnalysisStates = useCallback(async () => {
    const context = analysisPollContextRef.current;
    if (!context) return;
    const activeIds = new Set(
      context.articles
        .filter(analysisNeedsPolling)
        .map((article) => article.id),
    );
    const selectedNeedsRefresh = analysisNeedsPolling(context.activeArticle)
      && !activeIds.has(context.activeArticle.id);
    if (activeIds.size === 0 && !selectedNeedsRefresh) return;

    const filters = {};
    if (context.activeSourceId) filters.source_id = context.activeSourceId;
    else if (!context.favOnly) {
      filters.subscribed_scope = 'only';
      filters.shape = context.mode;
    } else {
      filters.shape = context.mode;
    }
    if (context.displayTagQuery) filters.display_tag = context.displayTagQuery;
    else if (context.searchQuery) filters.search = context.searchQuery;
    if (!context.favOnly) {
      filters.with_unread = 'true';
      if (context.unreadOnly) filters.unread_only = 'true';
    }

    try {
      const listRequest = context.favOnly
        ? fetchFavorites(
            filters,
            Math.max(PAGE_SIZE, context.articles.length),
            0,
            { includeContent: false },
          )
        : fetchArticles(
            filters,
            Math.max(PAGE_SIZE, context.articles.length),
            0,
            false,
            { includeContent: false },
          );
      const [data, selected] = await Promise.all([
        listRequest,
        selectedNeedsRefresh ? fetchArticle(context.activeArticle.id) : Promise.resolve(null),
      ]);
      if (analysisPollContextRef.current?.scopeKey !== context.scopeKey) return;
      const updates = new Map(
        analysisItemsFromResponse(data).map((article) => [article.id, article]),
      );
      if (selected?.id) updates.set(selected.id, selected);
      setArticles((current) => current
        .filter((article) => (
          !context.displayTagQuery || !activeIds.has(article.id) || updates.has(article.id)
        ))
        .map((article) => (
          updates.has(article.id) ? withFreshAnalysis(article, updates.get(article.id)) : article
        )));
      setActiveArticle((current) => (
        current?.id && updates.has(current.id)
          ? withFreshAnalysis(current, updates.get(current.id))
          : current
      ));
      const selectedId = activeIdRef.current;
      if (selectedId && updates.has(selectedId)) {
        setActiveSummary(preferredAnalysisSummary(
          summaryCacheRef.current.get(selectedId),
          updates.get(selectedId)?.summary_zh,
        ));
      }
    } catch {
      // 状态轮询是增强路径；失败保留当前可读结果，等下一周期。
    }
  }, []);
  usePolling(refreshAnalysisStates, ANALYSIS_POLL_MS, {
    immediate: false,
    enabled: analysisPollingEnabled,
  });

  // 切换来源/搜索 → 重置列表、回顶、清空右栏
  // 用 useLayoutEffect：在绘制前同步进入加载态，避免「切源瞬间旧列表被画出一帧」的陈旧帧闪现
  useLayoutEffect(() => {
    // 深链落地保护:分享深链要「切作用域 + 选中该篇」一次完成,而本清场会把刚
    // selectArticle 的右栏立刻抹掉(深链效果 setActiveSourceId → loadArticles 变引用
    // → 本 effect 触发)。深链发起的那一次作用域切换只重置列表、保留右栏选中。
    if (deepLinkKeepRef.current) {
      deepLinkKeepRef.current = false;
      if (listRef.current) listRef.current.scrollTop = 0;
      prevScopeUnreadRef.current = null;
      setFreshCount(0);
      setArticles([]);
      setArticlesTotal(0);
      loadArticles(0, false);
      return;
    }
    setActiveArticle(null);
    setActiveBody(null);
    setActiveBodyLoading(false);
    activeIdRef.current = null;
    if (listRef.current) listRef.current.scrollTop = 0;
    prevScopeUnreadRef.current = null; // 切视图:新内容增量检测重新起算
    setFreshCount(0);
    // 切作用域先清列表:加载中本有骨架屏遮挡,但若请求失败(loading 复位)会把上一
    // 容器的旧条目画进新容器(如文章列表残留被 SocialFlow 当推文渲染,卡片显示
    // 非社交源的源名)。同作用域内的刷新失败仍保留旧列表(不走本 effect)。
    setArticles([]);
    setArticlesTotal(0);
    loadArticles(0, false);
  }, [loadArticles]);

  const hasMore = articles.length < articlesTotal;
  const handleLoadMore = useCallback(
    () => loadArticles(articles.length, true),
    [loadArticles, articles.length],
  );

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

  // ── 订阅 / 取消订阅 ──
  const applyResult = (result) => {
    const nextSubscribedIds = new Set(result.subscribed_source_ids || []);
    setSubscribedIds(nextSubscribedIds);

    // 发现页直接渲染 sources 里的全站订阅人数；同步目标源的订阅态与计数，
    // 避免按钮已经切换、人数仍停留在目录首次加载时的快照。
    setSources((current) => current.map((source) => {
      if (source.source_id !== result.source_id) return source;
      const wasSubscribed = Boolean(source.subscribed);
      const subscribed = nextSubscribedIds.has(source.source_id);
      if (wasSubscribed === subscribed) return source;
      return {
        ...source,
        subscribed,
        subscriber_count: Math.max(
          0,
          Number(source.subscriber_count || 0) + (subscribed ? 1 : -1),
        ),
      };
    }));
  };

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
    // 用户自定源的「退订」= 移除(退订+无人订阅即清理配置与文章);普通退订会留下
    // 无人订阅的孤儿配置行,故两个入口(源栏减号/右键菜单)统一走移除路径。
    if (source.user_source) return handleRemoveCustomSource(source);
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

  // ── 用户自定源(v3.40):添加(建源+订阅+首抓)与移除(退订+无人订阅即清) ──
  // 添加错误直接上抛:AddCustomSourceModal 就地渲染错误文案(比 toast 更贴近表单)。
  const handleAddCustomSource = async (url, name) => {
    const result = await createCustomSource(url, name);
    if (result.status === 'exists') {
      // 撞中已收录的系统源:转普通订阅引导(该来源已在目录里)
      const existing = result.existing || {};
      if (!subscribedIds.has(existing.source_id)) {
        applyResult(await subscribeSource(existing.source_id));
        loadUnreadCounts();
        showToast(`该来源已收录,已订阅 ${existing.name || existing.source_id}`, 'success');
      } else {
        showToast(`该来源已收录且已订阅:${existing.name || existing.source_id}`, 'success');
      }
    } else {
      // 首抓已同步完成(v3.40 返修:添加响应等首抓落库,关浮层即可读),toast 如实带篇数
      showToast(
        result.first_fetch === 'failed'
          ? `已添加自定源 ${result.name},首次抓取失败,稍后自动重试`
          : `已添加自定源 ${result.name},收录 ${result.saved_count ?? 0} 篇`,
        'success',
      );
    }
    loadSources();
    refreshAggregateIfActive();
    loadUnreadCounts();
    return result;
  };

  const handleRemoveCustomSource = async (source) => {
    setPinningId(source.source_id);
    try {
      const result = await removeCustomSource(source.source_id);
      setSubscribedIds(new Set(result.subscribed_source_ids || []));
      if (activeSourceId === source.source_id) setActiveSourceId(null);
      else refreshAggregateIfActive();
      loadSources();
      loadUnreadCounts();
      showToast(`已移除自定源 ${source.name || source.source_id}`, 'success');
    } catch (error) {
      showToast(error.message || '移除自定源失败', 'error');
    } finally {
      setPinningId(null);
    }
  };

  // ── 合集批量订阅/退订(批量动作,非持久绑定;见 docs/source-collections-wave-plan.md)──
  // 批量结果不做逐源乐观调整(涉及多源订阅人数),直接重拉源目录校准订阅态与计数。
  const handleSubscribeCollection = async (collection) => {
    setCollectionPinningId(collection.collection_id);
    try {
      const result = await subscribeCollection(collection.collection_id);
      setSubscribedIds(new Set(result.subscribed_source_ids || []));
      loadSources();
      refreshAggregateIfActive();
      loadUnreadCounts();
      const n = (result.added || []).length;
      showToast(n > 0 ? `已订阅「${collection.name}」的 ${n} 个源` : '合集内的源已全部订阅', 'success');
    } catch (error) {
      showToast(error.message || '订阅合集失败', 'error');
    } finally {
      setCollectionPinningId(null);
    }
  };

  const handleUnsubscribeCollection = async (collection) => {
    setCollectionPinningId(collection.collection_id);
    try {
      const result = await unsubscribeCollection(collection.collection_id);
      setSubscribedIds(new Set(result.subscribed_source_ids || []));
      const removed = result.removed || [];
      if (activeSourceId && removed.includes(activeSourceId)) setActiveSourceId(null);
      else refreshAggregateIfActive();
      loadSources();
      loadUnreadCounts();
      showToast(`已退订「${collection.name}」的 ${removed.length} 个源`, 'success');
    } catch (error) {
      showToast(error.message || '退订合集失败', 'error');
    } finally {
      setCollectionPinningId(null);
    }
  };

  // ── 在读者面隐藏/恢复源(admin 会话的右键菜单入口;与节点管理检视器同一 API)──
  // 操作后重拉源目录:hidden 标记驱动源栏灰显与内容门控,不做本地乐观改。
  const handleToggleSourceHidden = async (source) => {
    const toHidden = !source.hidden;
    try {
      await setSourceVisibility(source.source_id, toHidden);
      showToast(toHidden ? '已在读者面隐藏该源' : '已恢复该源', 'success');
      loadSources();
      loadUnreadCounts();
    } catch (error) {
      showToast(error.message || '操作失败', 'error');
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

  // ── 按源全部标读(右键菜单入口:不依赖当前视图范围,按条目/源行携带的 source_id)──
  const markSourceAllRead = async (sourceId) => {
    if (!sourceId) return;
    try {
      const data = await markAllRead(sourceId, null);
      prevScopeUnreadRef.current = null;
      applyUnreadCounts(data);
      for (const a of articles) {
        if (a.source_id === sourceId) readOverridesRef.current.set(a.id, true);
      }
      setReadOverrides(new Map(readOverridesRef.current));
      if (unreadOnly) loadArticles(0, false);
      showToast('已全部标为已读', 'success');
    } catch (error) {
      showToast(error.message || '标记已读失败', 'error');
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

  // ── 上下文菜单 items(v3.28 桌面右键 / 移动长按动作单共用) ──
  // 三份清单同类项同位置:读态/收藏首组 → 链接类次组 → 重操作/破坏性末组(跨落点
  // 肌肉记忆)。动作全部复用既有函数(乐观更新/回滚/Toast 原样),菜单只是并联入口;
  // items 在 open 时闭包构建,天然携带最新读态/收藏态。弹出定位/长按手势由视图层持有。
  const isAdminSession = account?.role === 'admin';

  const copyWithToast = async (text, doneMessage) => {
    try {
      await copyText(text);
      showToast(doneMessage, 'success');
    } catch (error) {
      showToast(error.message || '复制失败', 'error');
    }
  };

  const buildArticleMenuItems = (article) => {
    const unread = isArticleUnread(article);
    const fav = favoriteIds.has(article.id);
    const isPodcast = Boolean(article?.podcast);
    return [
      { key: 'read', label: unread ? '标为已读' : '标为未读', icon: unread ? Check : Undo2, onClick: () => toggleArticleRead(article) },
      { key: 'fav', label: fav ? '取消收藏' : isPodcast ? '收藏播客' : '收藏文章', icon: fav ? StarOff : Star, onClick: () => handleToggleFavorite(article) },
      { type: 'sep' },
      { key: 'copy', label: '复制站内链接', icon: Link, onClick: () => copyWithToast(articleDeepLink(article.id), '已复制站内链接') },
      // disabled 不隐藏:菜单结构稳定,无 source_url 时降透明(§2 状态不靠颜色单独传达)
      { key: 'open', label: isPodcast ? '打开节目页面' : '打开原文', icon: ExternalLink, disabled: !article.source_url, onClick: () => window.open(article.source_url, '_blank', 'noopener') },
      { type: 'sep' },
      { key: 'markall', label: '标记该源全部已读', icon: CheckCheck, onClick: () => markSourceAllRead(article.source_id) },
    ];
  };

  const buildSourceMenuItems = (source) => {
    const items = [
      { key: 'markall', label: '全部标为已读', icon: CheckCheck, onClick: () => markSourceAllRead(source.source_id) },
      { key: 'open', label: '打开原站', icon: ExternalLink, disabled: !source.base_url, onClick: () => window.open(source.base_url, '_blank', 'noopener') },
    ];
    if (isAdminSession) {
      items.push(
        { type: 'sep' },
        { type: 'label', text: '管理' },
        { key: 'hide', label: source.hidden ? '在读者面恢复' : '在读者面隐藏', icon: source.hidden ? Eye : EyeOff, onClick: () => handleToggleSourceHidden(source) },
      );
    }
    // 退订=破坏性末组红项;与源行悬停减号完全同行为,不加确认弹窗
    // (同一动作两个入口不该有两种确认策略)。用户自定源如实标「移除」——
    // 无其他订阅者时会连带删除其收录内容,handleUnsubscribe 内部已分流。
    items.push(
      { type: 'sep' },
      {
        key: 'unsub',
        label: source.user_source ? '移除自定源' : '退订此源',
        icon: Trash2, danger: true,
        onClick: () => handleUnsubscribe(source),
      },
    );
    return items;
  };

  const buildSocialMenuItems = (article) => {
    const unread = isArticleUnread(article);
    const fav = favoriteIds.has(article.id);
    return [
      { key: 'read', label: unread ? '标为已读' : '标为未读', icon: unread ? Check : Undo2, onClick: () => handleToggleSocialRead(article) },
      { key: 'fav', label: fav ? '取消收藏' : '收藏推文', icon: fav ? StarOff : Star, onClick: () => handleToggleFavorite(article) },
      { type: 'sep' },
      // 链接组复制/打开的都是原推外链(社交卡无详情页语义),与卡上时间戳外链同目标
      { key: 'copy', label: '复制推文链接', icon: Link, disabled: !article.source_url, onClick: () => copyWithToast(article.source_url, '已复制推文链接') },
      { key: 'open', label: '打开原推', icon: ExternalLink, disabled: !article.source_url, onClick: () => window.open(article.source_url, '_blank', 'noopener') },
    ];
  };

  // ── 视图导航(容器语义):点容器钮=进入该容器聚合(源内时=回到聚合);搜索是叠加开关 ──
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
  // 站内分享深链:分享出来的 #/reader/a/{id},进来直接开这篇。
  // 等 sources 到位再执行——要靠 shapeOfSource 把容器切到这篇所属的宇宙(文章/动态/社交),
  // 否则会出现「在动态容器里显示一篇文章」的错位。取不到(已删/无权限)就静默留在默认视图,
  // 不弹错——收到链接的人对这篇失效与否无能为力,报错只是噪声。
  // ⚠️ deps 只留真正的触发条件,回调经 ref 取最新:selectArticle/shapeOfSource 会随无关
  // 状态(未读轮询等)重建,若列入 deps,fetch 在途中的任何一次重建都会 cleanup 掉本次
  // 落地(done 已置真不再重试)——表现为深链时灵时不灵的竞态。
  const deepLinkDoneRef = useRef('');
  const deepLinkCtxRef = useRef(null);
  useEffect(() => {   // 每轮渲染后同步最新回调(声明在深链 effect 之前,同轮先执行)
    deepLinkCtxRef.current = { shapeOfSource, selectArticle, onDeepLinkConsumed };
  });
  // 「按 id 打开一篇」的可复用落地(深链初始播种与 AI 问答引用跳转共用同一路):
  // 切作用域+选中该篇一次完成,deepLinkKeepRef 通知清场 effect 保留右栏。
  // silent=true(深链)取不到时静默——收到链接的人对失效无能为力,报错只是噪声;
  // 默认(引用跳转)取不到时 Toast 说明,因为点击者正在等待跳转发生。
  const openArticleById = useCallback(async (articleId, { silent = false } = {}) => {
    if (!articleId) return false;
    try {
      const article = await fetchArticle(articleId);
      if (!article?.id) throw new Error('empty');
      const ctx = deepLinkCtxRef.current;
      onBeforeOpenArticle?.();
      deepLinkKeepRef.current = true; // 通知作用域清场 effect:这次切换保留右栏(见 useLayoutEffect)
      setDiscover(false);
      setFavOnly(false);
      setActiveSourceId(article.source_id || null);
      setMode(ctx.shapeOfSource(article.source_id));
      ctx.selectArticle(article);
      return true;
    } catch {
      if (!silent) showToast('这条内容已不在库中', 'error');
      return false;
    }
  }, [onBeforeOpenArticle, showToast]);
  // doneRef 存「已启动消费的 id」而非布尔:同一 id 在消费在途时 deps 重建重跑 effect
  // 不会双跳;消费完成后 App 清空 initialArticleId,这里同步清 doneRef——运行时深链
  // (粘进已开 tab,经 App 的 onPop 再次设值,含同一篇重复粘贴)才能再次落地。
  useEffect(() => {
    if (!initialArticleId) { deepLinkDoneRef.current = ''; return; }
    if (deepLinkDoneRef.current === initialArticleId || sourcesLoading) return;
    deepLinkDoneRef.current = initialArticleId;
    openArticleById(initialArticleId, { silent: true })
      .finally(() => { deepLinkCtxRef.current?.onDeepLinkConsumed?.(); });
  }, [initialArticleId, sourcesLoading, openArticleById]);

  // 收藏入口(源栏,与「全部XX」并列):看本容器全部收藏(容器级、不逐源)。
  // Folo 语义——收藏是与「全部」并列的一级过滤,不再挂在列头逐源。
  const goContainerAll = () => { setDiscover(false); setActiveSourceId(null); setFavOnly(false); };
  const goFavorites = () => { setDiscover(false); setActiveSourceId(null); setFavOnly(true); };
  // 搜索开关(条目列头就地展开):关闭即清词(searchQuery 经防抖同步清空,列表回到无过滤)。
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
      : mode === 'article' ? '文章' : mode === 'social' ? '社交媒体' : mode === 'podcast' ? '播客' : '动态';

  // ── 翻页(上一篇/下一篇):沿当前列表序 ──
  const activeIndex = useMemo(
    () => (activeArticle ? articles.findIndex((a) => a.id === activeArticle.id) : -1),
    [articles, activeArticle],
  );
  const prevArticle = activeIndex > 0 ? articles[activeIndex - 1] : null;
  const nextArticle = activeIndex >= 0 && activeIndex < articles.length - 1 ? articles[activeIndex + 1] : null;

  // ── 阅读窗 crumb ──
  const crumbSource = activeArticle ? sourceMap[activeArticle.source_id] : null;
  const crumbHost = activeArticle ? hostOf(activeArticle.source_url) : '';
  const crumbName = activeArticle
    ? `${sourceNameMap[activeArticle.source_id] || activeArticle.source_id}${crumbHost ? ` · ${crumbHost}` : ''}`
    : '';
  // 阅读窗已用 reader-pane-title 画了标题,正文首行若是同名标题(哆啦美日报的
  // 「# 🤖 哆啦美 AI 资讯日报 · 日期」是典型)就会连看两遍——渲染侧剥离。
  // 只动阅读窗:归档正文原样保留,导出/同步的独立 markdown 首行标题仍是正确的。
  const displayBody = useMemo(
    () => stripDuplicateLeadingHeading(activeBody, activeArticle?.title),
    [activeBody, activeArticle?.title],
  );
  // 译文首行若是标题(原文或译名)同样剥离
  const displayTranslatedBody = useMemo(
    () => stripDuplicateLeadingHeading(
      stripDuplicateLeadingHeading(translatedBody, activeArticle?.title),
      translatedTitle,
    ),
    [translatedBody, activeArticle?.title, translatedTitle],
  );
  // 中文源判定(v3.45):标题+正文开头以中文为主 → 不画「译为中文」二段(正文未到位时只看标题)
  const activeIsChinese = useMemo(
    () => looksChinese(`${activeArticle?.title || ''}\n${activeBody || ''}`),
    [activeArticle?.title, activeBody],
  );

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

  return {
    // 源目录 / 订阅
    sources, subscribedIds, sourcesLoading, discoverSources, subscribedSources,
    sourceMap, sourceNameMap, shapeOfSource, sidebarGroups, hasNoSubscriptions,
    socialSources, platformCount, pinningId,
    handleSubscribe, handleUnsubscribe, handleToggleSourceHidden,
    // 用户自定源
    handleAddCustomSource, handleRemoveCustomSource,
    // 源合集
    collections, discoverCollectionId, setDiscoverCollectionId,
    collectionPinningId, handleSubscribeCollection, handleUnsubscribeCollection,
    // 视图 / 导航
    mode, activeSourceId, favOnly, discover, openDiscover, closeDiscover,
    bulletinView, socialView, podcastView, railActive, listTitle,
    goView, goSource, goContainerAll, goFavorites,
    activeSourceHidden, activeUnsubscribed, grouping,
    // 搜索
    searchOpen, searchInput, setSearchInput, searchQuery, toggleSearch, searchForLabel,
    // 未读体系
    unreadBySource, unreadOnly, setUnreadOnly, scopeUnread, unreadByShape,
    isArticleUnread, toggleArticleRead, handleTogglePaneRead, handleToggleSocialRead,
    handleMarkAllRead, markSourceAllRead, markingRead, paneReadToggling, socialReadToggling,
    freshCount, handleRefreshFresh,
    // 列表
    articles, articlesTotal, articlesLoading, loadingMore, hasMore, handleLoadMore,
    listRef, sentinelRef,
    // 选中文章 / 正文
    activeArticle, activeBody, activeBodyLoading, selectArticle, openArticleById,
    schedulePrefetch, cancelPrefetch,
    activeIndex, prevArticle, nextArticle,
    crumbSource, crumbHost, crumbName, displayBody, displayTranslatedBody, bodyStats,
    // 收藏
    favoriteIds, favTogglingId, handleToggleFavorite,
    // 分享
    shareOpen, setShareOpen,
    // AI(翻译 / 速读)
    showTranslation, translating, translatedBody, translatedTitle, activeIsChinese, handleTranslate,
    activeSummary, summarizing, handleSummarize,
    // 上下文菜单 items(桌面右键 / 移动长按共用)
    isAdminSession, buildArticleMenuItems, buildSourceMenuItems, buildSocialMenuItems,
  };
}
