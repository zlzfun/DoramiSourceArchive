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
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import LogoMark from './LogoMark';
import { resolveCompany } from '../sourceTaxonomy';
import {
  fetchReaderSources,
  fetchArticles,
  subscribeSource,
  unsubscribeSource,
  fetchFavorites,
  addFavorite,
  removeFavorite,
} from '../api';

const PAGE_SIZE = 30;

// react-markdown 默认不渲染原始 HTML（无 rehype-raw），无 XSS 风险
const MARKDOWN_PLUGINS = [remarkGfm, remarkBreaks];

// 正文图：外链直连加载，图床/代理已评估后明确不做（生产由各用户 IP 分散直连）。
// 这里只兜底裂图——源站删图/防盗链时给出体面占位，而非浏览器默认破图标。不重试、不代理。
function MarkdownImage({ node, alt, ...props }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="markdown-img-fallback" role="img" aria-label={alt || '图片加载失败'}>
        <span className="micro-label">图片加载失败</span>
        {alt ? <span className="markdown-img-fallback-alt">{alt}</span> : null}
      </span>
    );
  }
  // 阅读窗格只展示一篇文章，正文图即时加载（不用 lazy，避免滚动时「现拉现出」）
  return (
    <img
      {...props}
      alt={alt || ''}
      loading="eager"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

const MARKDOWN_COMPONENTS = {
  img: MarkdownImage,
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
};

function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').substring(0, 10);
}

// 卡片摘要：去掉裸 markdown 标记（图片/标题/列表/强调），避免摘要里出现 ![](url) 等
function excerptOf(content) {
  if (!content) return '';
  const plain = content
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')        // 图片
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')      // 链接 → 文本
    .replace(/^#{1,6}\s+/gm, '')                  // 标题
    .replace(/^\s*[-*+]\s+/gm, '')                // 列表项
    .replace(/^\s*>\s?/gm, '')                    // 引用
    .replace(/[*_`~]/g, '');                      // 强调/代码标记
  return plain.replace(/\s+/g, ' ').trim().slice(0, 140);
}

export default function ReaderTab({ showToast }) {
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
  const [activeArticle, setActiveArticle] = useState(null);
  const [pinningId, setPinningId] = useState(null);

  const listRef = useRef(null);
  // 取消上一笔仍在飞行的列表请求：切源/搜索时慢的旧请求若晚返回会「后发先至」
  // 覆盖当前选中源的列表，故发新请求前 abort 掉旧的（与 DataTab 同一约定）
  const listAbortRef = useRef(null);

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

  // ── 文章列表 ──
  const loadArticles = useCallback(async (skip = 0, append = false) => {
    // 发新请求前取消上一笔在飞行的请求，杜绝乱序晚到的响应覆盖当前列表
    listAbortRef.current?.abort();
    // 没有任何订阅且看的是订阅聚合视图 → 直接空列表，省一次请求（收藏视图不受此约束）
    if (!showFavorites && !activeSourceId && subscribedIds.size === 0) {
      setArticles([]);
      setArticlesTotal(0);
      return;
    }
    const controller = new AbortController();
    listAbortRef.current = controller;
    if (append) setLoadingMore(true); else { setArticlesLoading(true); setLoadingMore(false); }
    try {
      let data;
      if (showFavorites) {
        const filters = {};
        if (searchQuery) filters.search = searchQuery;
        data = await fetchFavorites(filters, PAGE_SIZE, skip, { signal: controller.signal });
        if (data.favorite_ids) setFavoriteIds(new Set(data.favorite_ids));
      } else {
        const filters = {};
        if (activeSourceId) filters.source_id = activeSourceId;
        else filters.subscribed_scope = 'only'; // 「我的订阅」聚合：后端硬过滤到已订阅源
        if (searchQuery) filters.search = searchQuery;
        data = await fetchArticles(filters, PAGE_SIZE, skip, true, { signal: controller.signal });
      }
      const items = data.items || [];
      setArticlesTotal(data.total || 0);
      setArticles(prev => (append ? [...prev, ...items] : items));
      // 首次加载（非追加）自动展示第一篇，省去手动点选
      if (!append) setActiveArticle(items[0] || null);
    } catch (error) {
      if (error.name === 'AbortError') return; // 被更新的请求取消，静默丢弃
      showToast(error.message || '获取文章列表失败', 'error');
    } finally {
      if (!controller.signal.aborted) {
        if (append) setLoadingMore(false); else setArticlesLoading(false);
      }
    }
  }, [activeSourceId, searchQuery, subscribedIds, showFavorites, showToast]);

  // 切换来源/搜索 → 重置列表、回顶、清空右栏
  // 用 useLayoutEffect：在绘制前同步进入加载态，避免「切源瞬间旧列表被画出一帧」的陈旧帧闪现
  useLayoutEffect(() => {
    setActiveArticle(null);
    if (listRef.current) listRef.current.scrollTop = 0;
    loadArticles(0, false);
  }, [loadArticles]);

  const hasMore = articles.length < articlesTotal;
  const handleLoadMore = () => loadArticles(articles.length, true);

  // ── 订阅 / 取消订阅 ──
  const applyResult = (result) => setSubscribedIds(new Set(result.subscribed_source_ids || []));

  const handleSubscribe = async (source) => {
    setPinningId(source.source_id);
    try {
      applyResult(await subscribeSource(source.source_id));
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
      if (activeSourceId === source.source_id) setActiveSourceId(null);
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
        setActiveArticle((prev) => (prev?.id === id ? null : prev));
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

  // 视图切换：订阅聚合 / 单个来源 / 我的收藏 三者互斥
  const goSubscribed = () => { setShowFavorites(false); setActiveSourceId(null); };
  const goSource = (sourceId) => { setShowFavorites(false); setActiveSourceId(sourceId); };
  const goFavorites = () => { setShowFavorites(true); setActiveSourceId(null); };

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
      <aside className="reader-col reader-col-sources" aria-hidden={sourcesCollapsed}>
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
            <div className="reader-empty">
              <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
              <span>正在载入来源…</span>
            </div>
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
      <section className="reader-col reader-col-list" aria-hidden={listCollapsed}>
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
            {showFavorites ? '我的收藏' : activeSourceId ? (sourceNameMap[activeSourceId] || activeSourceId) : '我的订阅'}
          </span>
          {/* 收藏是文章级集合：中栏头部的星标切换，与逐篇卡片星标同色呼应 */}
          <button
            type="button"
            onClick={() => (showFavorites ? goSubscribed() : goFavorites())}
            aria-pressed={showFavorites}
            aria-label={showFavorites ? '返回我的订阅' : '只看我的收藏'}
            title={showFavorites ? '返回我的订阅' : '只看我的收藏'}
            className={`reader-fav-icon ${showFavorites ? 'reader-fav-icon-on' : ''}`}
          >
            <Star className="h-4 w-4" fill={showFavorites ? 'currentColor' : 'none'} />
          </button>
          <span className="reader-list-count">{articlesTotal} 篇</span>
        </div>

        <div className="reader-list-scroll" ref={listRef}>
          {articlesLoading ? (
            <div className="reader-empty">
              <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
              <span>正在载入文章…</span>
            </div>
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
                    ? '还没有收藏任何文章，点文章上的星标即可收藏'
                    : '该来源暂无文章'}
              </span>
            </div>
          ) : (
            <div className="row-stagger">
              {articles.map((article) => {
                const active = activeArticle?.id === article.id;
                const favored = favoriteIds.has(article.id);
                return (
                  <div key={article.id} className="reader-article-wrap">
                    <button
                      type="button"
                      onClick={() => setActiveArticle(article)}
                      className={`reader-article-card ${active ? 'reader-article-card-active' : ''}`}
                    >
                      <p className="reader-article-title">{article.title || '（无标题）'}</p>
                      {excerptOf(article.content) && (
                        <p className="reader-article-excerpt">{excerptOf(article.content)}</p>
                      )}
                      <div className="reader-article-foot">
                        <span className="reader-article-source">{sourceNameMap[article.source_id] || article.source_id}</span>
                        {article.publish_date && (
                          <span className="reader-article-date">{formatDate(article.publish_date)}</span>
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
          <article className="reader-pane">
            <header className="reader-pane-head">
              <div className="reader-pane-meta">
                <span className="reader-pane-source">{sourceNameMap[activeArticle.source_id] || activeArticle.source_id}</span>
                {activeArticle.publish_date && (
                  <span className="reader-pane-date">
                    <CalendarDays className="h-3.5 w-3.5" /> {formatDate(activeArticle.publish_date)}
                  </span>
                )}
                {activeArticle.content_type && (
                  <span className="data-chip">{activeArticle.content_type}</span>
                )}
              </div>
              <h1 className="reader-pane-title">{activeArticle.title || '（无标题）'}</h1>
              <div className="reader-pane-actions">
                {activeArticle.source_url && (
                  <a href={activeArticle.source_url} target="_blank" rel="noreferrer" className="reader-pane-link">
                    <ExternalLink className="h-3.5 w-3.5" /> 查看原文
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
              </div>
            </header>
            <div className="reader-pane-body markdown-body">
              {activeArticle.content ? (
                <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS}>
                  {activeArticle.content}
                </ReactMarkdown>
              ) : (
                '该文章暂无正文内容，点击「查看原文」阅读完整内容。'
              )}
            </div>
          </article>
        ) : (
          <div className="reader-empty reader-empty-read">
            <BookOpenText className="h-8 w-8 text-slate-300" />
            <span>从中间选择一篇文章开始阅读</span>
          </div>
        )}
      </section>
    </div>
  );
}
