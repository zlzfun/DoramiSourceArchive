import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Search,
  Star,
  Plus,
  ExternalLink,
  ChevronDown,
  Loader2,
  Inbox,
  Compass,
  BookOpenText,
  CalendarDays,
} from 'lucide-react';
import LogoMark from './LogoMark';
import { resolveCompany } from '../sourceTaxonomy';
import { fetchReaderSources, fetchArticles, subscribeSource, unsubscribeSource } from '../api';

const PAGE_SIZE = 30;

function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').substring(0, 10);
}

function excerptOf(content) {
  if (!content) return '';
  return content.replace(/\s+/g, ' ').trim().slice(0, 140);
}

export default function ReaderTab({ showToast }) {
  const [sources, setSources] = useState([]);
  const [subscribedIds, setSubscribedIds] = useState(() => new Set());
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [activeSourceId, setActiveSourceId] = useState(null); // null = 「我的订阅」聚合
  const [discoverOpen, setDiscoverOpen] = useState(false);

  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  const [articles, setArticles] = useState([]);
  const [articlesTotal, setArticlesTotal] = useState(0);
  const [articlesLoading, setArticlesLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [activeArticle, setActiveArticle] = useState(null);
  const [pinningId, setPinningId] = useState(null);

  const listRef = useRef(null);

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
    // 没有任何订阅且看的是聚合视图 → 直接空列表，省一次请求
    if (!activeSourceId && subscribedIds.size === 0) {
      setArticles([]);
      setArticlesTotal(0);
      return;
    }
    if (append) setLoadingMore(true); else setArticlesLoading(true);
    try {
      const filters = {};
      if (activeSourceId) filters.source_id = activeSourceId;
      else filters.subscribed_scope = 'only'; // 「我的订阅」聚合：后端硬过滤到已订阅源
      if (searchQuery) filters.search = searchQuery;
      const data = await fetchArticles(filters, PAGE_SIZE, skip, true);
      const items = data.items || [];
      setArticlesTotal(data.total || 0);
      setArticles(prev => (append ? [...prev, ...items] : items));
    } catch (error) {
      showToast(error.message || '获取文章列表失败', 'error');
    } finally {
      if (append) setLoadingMore(false); else setArticlesLoading(false);
    }
  }, [activeSourceId, searchQuery, subscribedIds, showToast]);

  // 切换来源/搜索 → 重置列表、回顶、清空右栏
  useEffect(() => {
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

  return (
    <div className="reader-shell">
      {/* ── 左栏 · 我的订阅 ── */}
      <aside className="reader-col reader-col-sources">
        <div className="reader-search">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="搜索我的阅读…"
            className="reader-search-input"
          />
        </div>

        <button
          type="button"
          onClick={() => setActiveSourceId(null)}
          className={`reader-source-row reader-all-row ${activeSourceId === null ? 'reader-source-row-active' : ''}`}
        >
          <span className="reader-all-icon"><BookOpenText className="h-4 w-4" /></span>
          <div className="min-w-0 flex-1 text-left">
            <p className="reader-source-name">我的订阅</p>
            <p className="reader-source-meta">{subscribedTotal} 篇 · {subscribedSources.length} 个来源</p>
          </div>
        </button>

        <div className="reader-source-scroll">
          {sourcesLoading ? (
            <div className="reader-empty">
              <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
              <span>正在载入来源…</span>
            </div>
          ) : (
            <>
              {subscribedSources.length > 0 && (
                <div className="reader-group-body">
                  {subscribedSources.map((source) => {
                    const active = activeSourceId === source.source_id;
                    return (
                      <div
                        key={source.source_id}
                        role="button"
                        tabIndex={0}
                        onClick={() => setActiveSourceId(source.source_id)}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActiveSourceId(source.source_id); } }}
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
                            : <Star className="h-3.5 w-3.5" fill="currentColor" />}
                        </button>
                      </div>
                    );
                  })}
                </div>
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
                            className="reader-add"
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
      </aside>

      {/* ── 中栏 · 文章列表 ── */}
      <section className="reader-col reader-col-list">
        <div className="reader-list-head">
          <span className="reader-list-title">
            {activeSourceId ? (sourceNameMap[activeSourceId] || activeSourceId) : '我的订阅'}
          </span>
          <span className="reader-list-count">{articlesTotal} 篇</span>
        </div>

        <div className="reader-list-scroll" ref={listRef}>
          {articlesLoading ? (
            <div className="reader-empty">
              <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
              <span>正在载入文章…</span>
            </div>
          ) : hasNoSubscriptions && !activeSourceId ? (
            <div className="reader-empty reader-empty-tall">
              <Compass className="h-7 w-7 text-slate-300" />
              <span>你还没有订阅任何来源</span>
              <button type="button" className="action-button action-button-primary" onClick={() => setDiscoverOpen(true)}>
                去发现来源
              </button>
            </div>
          ) : articles.length === 0 ? (
            <div className="reader-empty">
              <Inbox className="h-6 w-6 text-slate-300" />
              <span>{searchQuery ? '没有匹配的文章' : '该来源暂无归档文章'}</span>
            </div>
          ) : (
            <div className="row-stagger">
              {articles.map((article) => {
                const active = activeArticle?.id === article.id;
                return (
                  <button
                    key={article.id}
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
              {activeArticle.source_url && (
                <a href={activeArticle.source_url} target="_blank" rel="noreferrer" className="reader-pane-link">
                  <ExternalLink className="h-3.5 w-3.5" /> 查看原文
                </a>
              )}
            </header>
            <div className="reader-pane-body">
              {activeArticle.content
                ? activeArticle.content
                : '该文章未归档正文内容，点击「查看原文」阅读完整内容。'}
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
