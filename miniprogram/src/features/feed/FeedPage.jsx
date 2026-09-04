import { useCallback, useEffect, useRef, useState } from 'react';
import Taro, { useDidShow, usePullDownRefresh, useReachBottom, useShareAppMessage } from '@tarojs/taro';
import { View, Text } from '@tarojs/components';
import { fetchArticles, fetchSocialArticles, fetchFavorites, markAllRead, markArticleRead, markArticleUnread, PAGE_SIZE } from '../../api';
import { isAuthenticated } from '../../store/session';
import {
  useReaderStore, setFilter, isArticleUnread, overrideRead, applyUnreadCounts,
  toggleFavorite, sourceName, loadSources, subscribedSources,
} from '../../store/reader';
import { bootstrapSession } from '../bootstrap';
import { dayKeyOf } from '../../shared/readerTime';
import { SHAPE_ALL_LABEL } from '../../shared/sourceRole';
import TopBar from '../../components/TopBar';
import ArticleCard from '../../components/ArticleCard';
import SocialCard from '../../components/SocialCard';
import ListSkeleton from '../../components/ListSkeleton';

/**
 * 条目流页(四个 tabBar 页共用,shape 参数化):
 * - 过滤器(源/收藏/未读/搜索)存 store.filters[shape],源页/我的页改后回到本页由 listVersion 触发重拉;
 * - 分页:skip/limit=30,触底追加;下拉刷新重拉第一页并校准未读;
 * - 打开文章:navigateTo 正文页,本会话覆盖为已读(圆点即消);社交流全文直出、不进正文页。
 */
export default function FeedPage({ shape }) {
  const store = useReaderStore();
  const filter = store.filters[shape];
  const [articles, setArticles] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchInput, setSearchInput] = useState(filter.search || '');
  const [markingRead, setMarkingRead] = useState(false);
  const genRef = useRef(0);
  const seenVersion = useRef(-1);
  const isSocial = shape === 'social';

  const load = useCallback(async (skip = 0, append = false) => {
    const gen = ++genRef.current;
    if (append) setLoadingMore(true); else { setLoading(true); setError(''); }
    try {
      const f = store.filters[shape];
      let data;
      if (f.favOnly) {
        const filters = f.sourceId ? { source_id: f.sourceId } : { shape };
        if (f.search) filters.search = f.search;
        data = await fetchFavorites(filters, PAGE_SIZE, skip, isSocial);
      } else {
        const filters = f.sourceId ? { source_id: f.sourceId } : { subscribed_scope: 'only', shape };
        if (f.search) filters.search = f.search;
        filters.with_unread = true;
        if (f.unreadOnly) filters.unread_only = true;
        data = await (isSocial ? fetchSocialArticles : fetchArticles)(filters, PAGE_SIZE, skip);
      }
      if (gen !== genRef.current) return;
      const items = data.items || [];
      setTotal(data.total || 0);
      setArticles((prev) => (append ? [...prev, ...items] : items));
    } catch (err) {
      if (gen !== genRef.current) return;
      if (!append) { setArticles([]); setTotal(0); }
      setError(err.message || '获取列表失败');
    } finally {
      if (gen === genRef.current) { setLoading(false); setLoadingMore(false); }
    }
  }, [shape, isSocial, store]);

  // 首次进入 / 从源页、我的页回来(过滤器或订阅变了)→ 重拉
  useDidShow(() => {
    if (!isAuthenticated()) return;
    bootstrapSession().then((ok) => {
      if (!ok) return;
      if (seenVersion.current !== store.listVersion) {
        seenVersion.current = store.listVersion;
        load(0, false);
      }
    });
  });
  useEffect(() => {
    if (!isAuthenticated()) return;
    if (seenVersion.current !== store.listVersion) {
      seenVersion.current = store.listVersion;
      load(0, false);
    }
  }, [store.listVersion, load]);

  usePullDownRefresh(async () => {
    try {
      await Promise.all([load(0, false), bootstrapSession({ force: true })]);
    } finally {
      Taro.stopPullDownRefresh();
    }
  });
  useReachBottom(() => {
    if (loading || loadingMore || articles.length >= total) return;
    load(articles.length, true);
  });
  useShareAppMessage(() => ({ title: '哆啦美 · AI 资讯阅读器', path: '/pages/feed/article/index' }));

  const openArticle = (article) => {
    overrideRead(article.id, true);
    Taro.navigateTo({ url: `/pages/article/index?id=${encodeURIComponent(article.id)}` });
  };
  const onToggleFavorite = async (article) => {
    try {
      const on = await toggleFavorite(article.id);
      Taro.showToast({ title: on ? '已收藏' : '已取消收藏', icon: 'none', duration: 900 });
      if (filter.favOnly && !on) setArticles((prev) => prev.filter((a) => a.id !== article.id));
    } catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
  };
  const onToggleRead = async (article) => {
    const unread = isArticleUnread(article);
    try {
      const data = unread ? await markArticleRead(article.id) : await markArticleUnread(article.id);
      overrideRead(article.id, unread);
      if (data && data.by_source) applyUnreadCounts(data);
    } catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
  };
  const onMarkAllRead = async () => {
    setMarkingRead(true);
    try {
      const data = await markAllRead(filter.sourceId, filter.sourceId ? null : shape);
      applyUnreadCounts(data);
      articles.forEach((a) => overrideRead(a.id, true));
      Taro.showToast({ title: '已全部标为已读', icon: 'none', duration: 900 });
    } catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
    finally { setMarkingRead(false); }
  };
  const onSearchChange = (value, commit = false) => {
    setSearchInput(value);
    if (commit || value === '') setFilter(shape, { search: value.trim() });
  };
  const toggleSearch = () => {
    if (searchOpen) { setSearchOpen(false); setSearchInput(''); if (filter.search) setFilter(shape, { search: '' }); }
    else setSearchOpen(true);
  };

  const title = filter.favOnly ? '收藏' : filter.sourceId ? sourceName(filter.sourceId) : SHAPE_ALL_LABEL[shape];
  useEffect(() => { Taro.setNavigationBarTitle({ title }).catch(() => {}); }, [title]);
  const noSubs = store.sourcesLoaded && subscribedSources().filter((s) => s.shape === shape).length === 0 && !filter.sourceId;
  let lastKey = null;

  return (
    <View>
      <TopBar
        title={title}
        onOpenSources={() => Taro.navigateTo({ url: `/pages/sources/index?shape=${shape}` })}
        searchOpen={searchOpen}
        searchValue={searchInput}
        onSearchChange={onSearchChange}
        onToggleSearch={toggleSearch}
        unreadOnly={filter.unreadOnly}
        onUnreadOnlyChange={(v) => setFilter(shape, { unreadOnly: v })}
        onMarkAllRead={onMarkAllRead}
        markingRead={markingRead}
        favOnly={filter.favOnly}
      />
      {loading ? <ListSkeleton /> : error ? (
        <View className="empty"><Text>{error}</Text><Text className="empty-link" onClick={() => load(0, false)}>重试</Text></View>
      ) : articles.length === 0 ? (
        <View className="empty">
          <Text>{filter.favOnly ? '还没有收藏' : filter.search ? '没有匹配的内容' : filter.unreadOnly ? '没有未读了' : noSubs ? '还没有订阅来源' : '暂无内容'}</Text>
          {noSubs && !filter.favOnly && (
            <Text className="empty-link" onClick={() => Taro.navigateTo({ url: `/pages/discover/index?shape=${shape}` })}>去「发现」挑选来源</Text>
          )}
        </View>
      ) : (
        <View className="list">
          {articles.map((article) => {
            const key = dayKeyOf(article);
            const showLabel = key !== lastKey && !isSocial;
            lastKey = key;
            const name = sourceName(article.source_id);
            return isSocial ? (
              <SocialCard
                key={article.id}
                article={article}
                sourceName={name}
                isUnread={isArticleUnread(article)}
                isFav={store.favoriteIds.has(article.id)}
                onToggleFavorite={onToggleFavorite}
                onToggleRead={onToggleRead}
              />
            ) : (
              <ArticleCard
                key={article.id}
                article={article}
                shape={shape}
                sourceName={name}
                isUnread={isArticleUnread(article)}
                isFav={store.favoriteIds.has(article.id)}
                showLabel={showLabel}
                dayKey={key}
                onOpen={openArticle}
                onToggleFavorite={onToggleFavorite}
              />
            );
          })}
          {loadingMore && <View className="toast-inline">载入中…</View>}
          {!loadingMore && articles.length >= total && total > PAGE_SIZE && <View className="toast-inline">已经到底了</View>}
        </View>
      )}
    </View>
  );
}
