import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { fetchArticle, fetchArticles } from '../api';
import { useAbortableLoad } from './useAbortableLoad';
import { usePolling } from './usePolling';
import { analysisItemsFromResponse, analysisNeedsPolling } from '../utils/analysis';
import { withFreshAnalysis } from '../utils/analysisProjection';
import { applyPolledFacetDelta, polledKindChanges, polledMembershipChanged } from '../utils/contentKind';
import { LIST_PAGE_SIZE, buildListFilters } from '../utils/articleList';

const ANALYSIS_POLL_MS = 30000;

/**
 * 条目列表实例(issue #230 阶段 3 批 3a,自 useReaderState 拆出;纯逻辑在 utils/articleList.js)。
 *
 * 一条列表 = 一份查询(query)+ 一扇窗口(已载入的条目、总数、加载态、滚动容器与无限滚动哨兵)。
 * 阅读器主列表(内容 Tab / 桌面条目列)与手机订阅 Tab 的推入页各调一次,状态互不相干:推入页换视图、
 * 只看未读、翻页、滚动都只动自己的窗口,主列表的查询与滚动位置不被改写。
 *
 * 分两段,是为了让 useReaderState 原有的声明顺序不变(窗口的 setter 在它的正文回读 / 摘要回填里早早就要用,
 * 加载器要等类型筛选这些派生值算完):
 *   useListWindow()            窗口的状态;
 *   useListLoader(win, opts)   首拉 / 翻页 / 换作用域清场 / 分析状态轮询 / 无限滚动。
 * 独立实例(推入页)直接用 useArticleList(query, opts) = 两段合一,并可登记到数据层的实例登记处。
 *
 * query 是调用方 useMemo 的同一个对象:**身份变 = 换作用域**(回顶、清窗口、重拉),与拆分前
 * 「loadArticles 依赖变化即清场」的语义一致——主列表点同一个容器也会换出新的 scope 对象,照旧刷新。
 */
export function useListWindow() {
  const [articles, setArticles] = useState([]);
  const [articlesTotal, setArticlesTotal] = useState(0);
  const [articlesLoading, setArticlesLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const listRef = useRef(null);      // 列表滚动容器(切作用域 scrollTop 归零)
  // 回调 ref 跟随视图替换的 DOM;缩放或切页后不会继续观察已卸载的哨兵。
  const [sentinel, sentinelRef] = useState(null);
  // 列表版本:每次列表加载(首页 / 翻页 / 轮询整窗重拉)提交前 +1。分析轮询整次提交前核对,
  // 被同作用域刷新或翻页取代的旧轮询整体丢弃——不回滚新条目,也不重复累加分面(codex R2-1)。
  const listVersionRef = useRef(0);
  return {
    articles, setArticles, articlesTotal, setArticlesTotal,
    articlesLoading, setArticlesLoading, loadingMore, setLoadingMore,
    listRef, sentinel, sentinelRef, listVersionRef,
  };
}

/**
 * opts:
 *   query            见上
 *   hidden           暂不可用源:不发请求,就地空态(读者 / admin 两种会话同观感)
 *   showToast
 *   kindFacets       主列表:{ enabled, key, state, setState } —— 类型分面随首页取回、随轮询增量校正
 *   pollPaused       发现页开着时不轮询(主列表)
 *   selected / selectedIdRef / onPolled   主列表:正在读的那篇随分析轮询一起刷新
 *   onWindowFresh    窗口换成最新(首页 / 快照):主列表据此把「N 篇新文章」归零
 *   onScopeReset     换作用域清场前:主列表清阅读窗(深链落地例外)与新内容基线
 */
export function useListLoader(win, {
  query,
  hidden = false,
  showToast,
  kindFacets = null,
  pollPaused = false,
  selected = null,
  selectedIdRef = null,
  onPolled = null,
  onWindowFresh = null,
  onScopeReset = null,
}) {
  const {
    articles, setArticles, articlesTotal, setArticlesTotal, setArticlesLoading,
    loadingMore, setLoadingMore, articlesLoading, listRef, sentinel, listVersionRef,
  } = win;
  // 列表加载的竞态安全器：切源/搜索时慢的旧请求若晚返回会「后发先至」覆盖当前源列表，
  // runList 发新请求前 abort 掉旧的（与 DataTab 同一约定）。
  const runList = useAbortableLoad();
  const facetsOn = Boolean(kindFacets?.enabled);
  const facetsKey = kindFacets?.key ?? '';
  const setFacets = kindFacets?.setState;
  // 回调经 ref 读最新值(布局副作用先于清场副作用声明:同一次提交里清场读到的是这一轮的回调)
  const hooksRef = useRef({ onPolled, onWindowFresh, onScopeReset, setFacets });
  useLayoutEffect(() => { hooksRef.current = { onPolled, onWindowFresh, onScopeReset, setFacets }; });

  // ── 首拉 / 翻页 ──
  const loadArticles = useCallback(async (skip = 0, append = false) => {
    if (hidden) {
      // 暂不可用源不发请求:读者侧服务端本就返回空,admin 会话的按源查询不过滤,
      // 前端统一短路,空态与文案由渲染层给出。
      setArticles([]);
      setArticlesTotal(0);
      setArticlesLoading(false);
      setLoadingMore(false);
      hooksRef.current.onWindowFresh?.();
      return;
    }
    // 竞态由 runList 兜底：发新弃旧，杜绝乱序晚到的响应覆盖当前列表。
    if (append) setLoadingMore(true); else { setArticlesLoading(true); setLoadingMore(false); }
    const versionAtStart = listVersionRef.current;
    let data;
    try {
      // 列表只渲染摘要（content_preview），故请求统一不带全文，正文按需懒加载（见 selectArticle）。
      // 「我的订阅」聚合由后端 subscribed_scope=only 自行解析范围，前端无需先拿到订阅集合即可发请求。
      data = await runList((signal) => {
        // 三谓词面板 + 源轴 + 读态 + 搜索,一份拼装(分析轮询复用同一函数,不再分叉)
        const filters = buildListFilters(query);
        // 首页顺带取类型分面(文章容器才有筛选条);翻页不重复计算
        if (facetsOn && !append) filters.kind_facets = 'true';
        // 社交流全文直出(推文正文 2~4 行,取回零负担),且卡片要 extensions
        // (引用推/转推/图链)——那只在 include_content=true 时随列表返回。
        return fetchArticles(filters, LIST_PAGE_SIZE, skip, true, { signal, includeContent: query.mode === 'social' });
      });
    } catch (error) {
      showToast(error.message || '获取文章列表失败', 'error');
      if (append) setLoadingMore(false); else setArticlesLoading(false);
      return;
    }
    if (data === undefined) return; // 被更新的请求取代，loading 交给新请求，不在此清除
    // 翻页的偏移取自发起时的窗口:期间窗口被分析轮询的快照整体替换过,这一页已对不上,丢弃;
    // 哨兵随 loadingMore 复位重新求值,按新窗口长度再取(codex R3-O1)
    if (append && listVersionRef.current !== versionAtStart) {
      setLoadingMore(false);
      return;
    }
    const items = data.items || [];
    listVersionRef.current += 1;
    if (!append && facetsOn) {
      hooksRef.current.setFacets?.((prev) => ({
        key: facetsKey,
        gen: prev.gen + 1,
        items: Array.isArray(data.kind_facets) ? data.kind_facets : [],
      }));
    }
    setArticlesTotal(data.total || 0);
    setArticles(prev => (append ? [...prev, ...items] : items));
    // 不再自动展开第一篇——避免「被动打开」污染阅读计量；右栏停在提示态，
    // 等用户主动点选一篇才加载正文并计一次阅读（见 selectArticle）。
    if (!append) hooksRef.current.onWindowFresh?.(); // 列表已刷新,新内容提示归零
    if (append) setLoadingMore(false); else setArticlesLoading(false);
  }, [query, hidden, facetsOn, facetsKey, showToast, runList,
    setArticles, setArticlesTotal, setArticlesLoading, setLoadingMore, listVersionRef]);

  // ── 分析状态轮询 ──
  // 分析任务与采集解耦：列表首拉可能拿到 pending/running。只在确有在途项时
  // 每 30 秒静默重取当前已加载窗口，既不闪骨架屏也不弹失败 toast；响应回写前
  // 校验 scopeKey，避免切源/搜索后旧轮询污染新列表。
  const analysisScopeKey = JSON.stringify([query, hidden]);
  const analysisPollingEnabled = !pollPaused && !hidden && (
    articles.some(analysisNeedsPolling)
    || analysisNeedsPolling(selected)
  );
  const analysisPollContextRef = useRef(null);
  const facetsBasisKey = kindFacets?.state?.key ?? '';
  const facetsBasisGen = kindFacets?.state?.gen ?? 0;
  useEffect(() => {
    analysisPollContextRef.current = {
      selected,
      query,
      articles,
      facetsOn,
      scopeKey: analysisScopeKey,
      kindScopeKey: facetsKey,
      listVersion: listVersionRef.current,
      facetsBasis: { key: facetsBasisKey, gen: facetsBasisGen },
    };
  }, [selected, query, articles, facetsOn, analysisScopeKey, facetsKey, facetsBasisKey, facetsBasisGen, listVersionRef]);
  const refreshAnalysisStates = useCallback(async () => {
    const context = analysisPollContextRef.current;
    if (!context) return;
    const activeIds = new Set(
      context.articles
        .filter(analysisNeedsPolling)
        .map((article) => article.id),
    );
    const selectedNeedsRefresh = analysisNeedsPolling(context.selected)
      && !activeIds.has(context.selected.id);
    if (activeIds.size === 0 && !selectedNeedsRefresh) return;

    const filters = buildListFilters(context.query);

    try {
      const listRequest = fetchArticles(
        filters,
        Math.max(LIST_PAGE_SIZE, context.articles.length),
        0,
        false,
        { includeContent: false },
      );
      const [data, selectedDetail] = await Promise.all([
        listRequest,
        selectedNeedsRefresh ? fetchArticle(context.selected.id) : Promise.resolve(null),
      ]);
      // 作用域变了,或期间有过列表加载(同作用域刷新 / 翻页):本轮结果整体作废,等下一轮
      const superseded = () => (
        analysisPollContextRef.current?.scopeKey !== context.scopeKey
        || listVersionRef.current !== context.listVersion
      );
      if (superseded()) return;
      const updates = new Map(
        analysisItemsFromResponse(data).map((article) => [article.id, article]),
      );
      if (selectedDetail?.id) updates.set(selectedDetail.id, selectedDetail);
      if (polledMembershipChanged(context.articles, activeIds, updates, context.query)) {
        // 筛选下窗口成员变了:不在本地移出、不在本地扣总数(翻页可能已拿到服务端新总数,codex R2-2),
        // 按同一窗口静默重拉带总数与分面的权威快照整体替换——成员、总数、下一页位置、分面一起对齐。
        const snapshotFilters = { ...filters };
        if (context.facetsOn) snapshotFilters.kind_facets = 'true';
        const snapshot = await fetchArticles(
          snapshotFilters,
          Math.max(LIST_PAGE_SIZE, context.articles.length),
          0,
          true,
          { includeContent: context.query.mode === 'social' },
        );
        if (superseded()) return;
        const items = snapshot?.items || [];
        for (const item of items) updates.set(item.id, item);
        // 正在阅读的条目被移出窗口:快照里没有它的新结果,补取一次详情,阅读窗当轮更新(codex R3-O2)
        const readingId = selectedIdRef?.current ?? null;
        if (readingId && !updates.has(readingId) && context.articles.some((article) => article.id === readingId)) {
          const detail = await fetchArticle(readingId).catch(() => null);
          if (superseded()) return;
          if (detail?.id === readingId) updates.set(readingId, detail);
        }
        listVersionRef.current += 1;
        if (context.facetsOn) {
          hooksRef.current.setFacets?.((prev) => ({
            key: context.kindScopeKey,
            gen: prev.gen + 1,
            items: Array.isArray(snapshot?.kind_facets) ? snapshot.kind_facets : [],
          }));
        }
        setArticlesTotal(snapshot?.total || 0);
        setArticles(items);
        hooksRef.current.onWindowFresh?.(); // 与首页刷新同口径:窗口已是最新
      } else {
        const changes = polledKindChanges(context.articles, updates);
        if (changes.length && context.facetsOn) {
          hooksRef.current.setFacets?.((state) => applyPolledFacetDelta(state, context.facetsBasis, changes));
        }
        setArticles((current) => current.map((article) => (
          updates.has(article.id) ? withFreshAnalysis(article, updates.get(article.id)) : article
        )));
      }
      hooksRef.current.onPolled?.(updates);
    } catch {
      // 状态轮询是增强路径；失败保留当前可读结果，等下一周期。
    }
  }, [selectedIdRef, setArticles, setArticlesTotal, listVersionRef]);
  usePolling(refreshAnalysisStates, ANALYSIS_POLL_MS, {
    immediate: false,
    enabled: analysisPollingEnabled,
  });

  // 换作用域 → 重置列表、回顶(主列表另清阅读窗)
  // 用 useLayoutEffect：在绘制前同步进入加载态，避免「切源瞬间旧列表被画出一帧」的陈旧帧闪现
  useLayoutEffect(() => {
    hooksRef.current.onScopeReset?.();
    if (listRef.current) listRef.current.scrollTop = 0;
    // 切作用域先清列表:加载中本有骨架屏遮挡,但若请求失败(loading 复位)会把上一
    // 容器的旧条目画进新容器(如文章列表残留被 SocialFlow 当推文渲染,卡片显示
    // 非社交源的源名)。同作用域内的刷新失败仍保留旧列表(不走本 effect)。
    setArticles([]);
    setArticlesTotal(0);
    loadArticles(0, false);
  }, [loadArticles, listRef, setArticles, setArticlesTotal]);

  const hasMore = articles.length < articlesTotal;
  const handleLoadMore = useCallback(
    () => loadArticles(articles.length, true),
    [loadArticles, articles.length],
  );

  // 无限滚动:哨兵进入视口(提前 400px)即自动追加下一页,取代「加载更多」按钮。
  // 依赖变化即重建 observer——追加后 articles.length 变、loadingMore 置真都会重新求值,
  // 天然防重入(loadingMore 时不触发)。
  useEffect(() => {
    if (!sentinel || !hasMore) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingMore && !articlesLoading) {
          loadArticles(articles.length, true);
        }
      },
      { rootMargin: '400px' },
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, [sentinel, hasMore, loadingMore, articlesLoading, articles.length, loadArticles]);

  return { loadArticles, handleLoadMore, hasMore };
}

/**
 * 独立实例(推入页):窗口 + 加载器,可选登记到数据层的实例登记处(utils/articleList.createListRegistry)——
 * 正文回读 / 收藏 / 按源标读这类改动经它同步到本实例,查询与滚动位置只归本实例自己。
 */
export function useArticleList(query, { registry = null, ...opts } = {}) {
  const win = useListWindow();
  const loader = useListLoader(win, { query, ...opts });
  const latestRef = useRef({ query, articles: win.articles, reload: loader.loadArticles });
  useEffect(() => { latestRef.current = { query, articles: win.articles, reload: loader.loadArticles }; });
  const { setArticles, setArticlesTotal } = win;
  useEffect(() => {
    if (!registry) return undefined;
    return registry.add({
      query: () => latestRef.current.query,
      getArticles: () => latestRef.current.articles,
      setArticles,
      setArticlesTotal,
      reload: () => latestRef.current.reload(0, false),
    });
  }, [registry, setArticles, setArticlesTotal]);
  return { ...win, ...loader };
}
