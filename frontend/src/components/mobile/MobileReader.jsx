import { useEffect, useState } from 'react';
import {
  AtSign,
  CheckCheck,
  ChevronLeft,
  CloudOff,
  Compass,
  FileText,
  Inbox,
  Loader2,
  Menu,
  RefreshCw,
  Search,
  Star,
  UserRound,
  X,
  Zap,
} from 'lucide-react';
import { useReaderState } from '../../hooks/useReaderState';
import { useLongPress } from '../../hooks/useLongPress';
import { ArticleRow, ArticleCardsSkeleton } from '../ReaderTab';
import SocialFlow from '../SocialFlow';
import DiscoverPage from '../DiscoverPage';
import AnnouncementBanner from '../AnnouncementBanner';
import MobileArticlePage from './MobileArticlePage';
import MobileSourceDrawer from './MobileSourceDrawer';
import MobileMePage from './MobileMePage';
import ActionSheet from './ActionSheet';
import { dayKeyOf } from '../../utils/readerTime';

// 静态 noop:ArticleRow 的 onContextMenu 契约位——移动端 contextmenu 由外层
// .m-press 包装统一接管(useLongPress 的 onContextMenu 覆盖 Android 长按/桌面右键),
// 行内回调不再各自拦截。
const noopContextMenu = () => {};

/**
 * 移动壳(移动波 Wave2,样页 docs/design/dorami-mobile-quiet.html)。
 *
 * 桌面四带布局不下放——移动端是「页面栈 + 底部 TabBar」的原生形态翻译:
 * 底部 4-Tab(文章/动态/社交/我的)= 视图轨的移动翻译,三容器模型原样保留;
 * 源栏 → 过滤抽屉(§4.1.2 选源是过滤器不是目的地);正文页 = push 全屏页;
 * 桌面右键菜单 → 长按底部动作单(items 与 useReaderState 三份构建器同源,
 * 跨端肌肉记忆);「发现」是低频目的地,从 我的/抽屉 进入、不占 Tab。
 * 数据层全部来自 useReaderState(与桌面 ReaderTab 同一 hook),本文件只写
 * 移动 JSX 与触屏交互原语。
 */
export default function MobileReader({
  showToast,
  aiEnabled = false,
  account = null,
  themePref = 'system',
  onSetTheme,
  onOpenSettings,
  onLogout,
  feedbackUnread = 0,
  initialArticleId = '',
  onDeepLinkConsumed,
}) {
  const rs = useReaderState({ showToast, account, initialArticleId, onDeepLinkConsumed });
  const {
    // 源目录 / 订阅
    sourcesLoading, discoverSources, subscribedIds, sourceMap, sourceNameMap,
    sidebarGroups, hasNoSubscriptions, socialSources, platformCount, pinningId,
    handleSubscribe, handleUnsubscribe,
    // 视图 / 导航
    mode, activeSourceId, favOnly, discover, setDiscover,
    bulletinView, socialView, listTitle,
    goView, goSource, goContainerAll, goFavorites,
    activeSourceHidden, activeUnsubscribed, grouping,
    // 搜索
    searchOpen, searchInput, setSearchInput, searchQuery, toggleSearch,
    // 未读体系
    unreadBySource, unreadOnly, setUnreadOnly, scopeUnread,
    isArticleUnread, handleToggleSocialRead,
    handleMarkAllRead, markingRead, socialReadToggling,
    freshCount, handleRefreshFresh,
    // 列表
    articles, articlesLoading, loadingMore, hasMore, handleLoadMore,
    listRef, sentinelRef,
    // 选中文章
    activeArticle, selectArticle, schedulePrefetch, cancelPrefetch,
    // 收藏
    favoriteIds, favTogglingId, handleToggleFavorite,
    // 动作单 items(桌面右键三份构建器同源)
    buildArticleMenuItems, buildSourceMenuItems, buildSocialMenuItems,
    // 订阅数(我的页)
    subscribedSources,
  } = rs;

  // 底部 Tab:article|bulletin|social 与容器 mode 一一对应,me 是移动端独有落点
  const [tab, setTab] = useState('article');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sheet, setSheet] = useState(null); // { title, items, anchorKey }

  // mode 被深链/点源/发现页预览改变时,内容 Tab 跟随所属容器(停在「我的」则不动)
  useEffect(() => {
    setTab((cur) => (cur === 'me' || cur === mode ? cur : mode));
  }, [mode]);

  const goTab = (t) => {
    if (t === 'me') { setTab('me'); return; }
    setTab(t);
    // 与桌面视图轨同语义:点容器钮=回到该容器聚合(清源/收藏/搜索过滤)
    goView(t);
  };

  const openArticleSheet = (article) => setSheet({
    title: article.title || '（无标题）',
    items: buildArticleMenuItems(article),
    anchorKey: `article:${article.id}`,
  });
  const openSourceSheet = (source) => setSheet({
    title: source.name || source.source_id,
    items: buildSourceMenuItems(source),
    anchorKey: `source:${source.source_id}`,
  });
  const openSocialSheet = (article) => setSheet({
    title: article.title || '推文',
    items: buildSocialMenuItems(article),
    anchorKey: `social:${article.id}`,
  });

  const pressBind = useLongPress(openArticleSheet);

  const listView = tab !== 'me' && !socialView;
  // 正文页(push 全屏):文章/动态容器里选中了一篇即入栈;社交流直读不进正文页
  const readOpen = listView && !discover && Boolean(activeArticle);

  return (
    <div className="m-shell font-sans">
      <AnnouncementBanner />

      {/* ── 顶栏 ── */}
      {tab === 'me' ? (
        <div className="m-topbar"><span className="m-title m-title-solo">我的</span></div>
      ) : socialView ? (
        // 社交 Tab:SocialFlow 自带 seg/标读/搜索控件行,顶栏只承担 抽屉入口+标题
        <div className="m-topbar">
          <button type="button" className="m-iconbtn" onClick={() => setDrawerOpen(true)} aria-label="订阅源">
            <Menu />
          </button>
          <span className="m-title">{listTitle}</span>
        </div>
      ) : (
        <div className="m-topbar">
          <button type="button" className="m-iconbtn" onClick={() => setDrawerOpen(true)} aria-label="订阅源">
            <Menu />
          </button>
          {searchOpen ? (
            <div className="m-search-inline">
              <Search aria-hidden="true" />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="搜索我的阅读…"
                autoFocus
              />
            </div>
          ) : (
            <span className="m-title">{listTitle}</span>
          )}
          <div className="m-topbar-sp" />
          {!favOnly && !searchOpen && (
            <>
              <div className="mini-seg" role="tablist" aria-label="未读筛选">
                {[[false, '全部'], [true, '未读']].map(([value, label]) => (
                  <button
                    key={label}
                    type="button"
                    role="tab"
                    aria-selected={unreadOnly === value}
                    onClick={() => setUnreadOnly(value)}
                    className={`mini-seg-btn ${unreadOnly === value ? 'is-on' : ''}`}
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
                className="m-iconbtn"
              >
                {markingRead ? <Loader2 className="animate-spin" /> : <CheckCheck />}
              </button>
            </>
          )}
          <button
            type="button"
            onClick={toggleSearch}
            aria-pressed={searchOpen}
            aria-label={searchOpen ? '关闭搜索' : '搜索'}
            className={`m-iconbtn ${searchOpen ? 'is-blue' : ''}`}
          >
            {searchOpen ? <X /> : <Search />}
          </button>
        </div>
      )}

      {/* ── 内容区 ── */}
      <div className="m-content">
        {tab === 'me' ? (
          <MobileMePage
            account={account}
            subscribedCount={subscribedSources.length}
            favoriteCount={favoriteIds.size}
            feedbackUnread={feedbackUnread}
            themePref={themePref}
            onSetTheme={onSetTheme}
            onShowFavorites={() => { goFavorites(); setTab(mode); }}
            onOpenDiscover={() => setDiscover(true)}
            onOpenSettings={onOpenSettings}
            onLogout={onLogout}
          />
        ) : socialView ? (
          <div className="m-social-host">
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
              onPostContextMenu={(e, entity) => { e.preventDefault(); openSocialSheet(entity); }}
              ctxAnchorKey={sheet?.anchorKey || null}
              onMarkAllRead={handleMarkAllRead}
              markingRead={markingRead}
              loading={articlesLoading}
              hasMore={hasMore}
              loadingMore={loadingMore}
              onLoadMore={handleLoadMore}
              platformCount={platformCount}
              activeSourceId={activeSourceId}
              emptyHint={
                activeSourceHidden
                  ? '该账号暂时不可用'
                  : socialSources.length === 0 ? '还没有订阅社交账号，去「发现」看看' : '暂无动态'
              }
            />
          </div>
        ) : (
          <div className="m-list" ref={listRef}>
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
                  : null}
                订阅「{activeUnsubscribed.name || activeUnsubscribed.source_id}」
              </button>
            )}
            {!favOnly && !articlesLoading && freshCount > 0 && (
              <button type="button" className="reader-fresh-pill" onClick={handleRefreshFresh}>
                <RefreshCw className="h-3 w-3" />
                载入 {freshCount} 篇新文章
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
            ) : activeSourceHidden ? (
              <div className="reader-empty reader-empty-tall">
                <CloudOff className="h-7 w-7 text-slate-300" />
                <span>该来源暂时不可用</span>
              </div>
            ) : articles.length === 0 ? (
              <div className="reader-empty">
                {favOnly ? <Star className="h-6 w-6 text-slate-300" /> : <Inbox className="h-6 w-6 text-slate-300" />}
                <span>
                  {searchQuery
                    ? '没有匹配的文章'
                    : favOnly
                      ? '当前范围还没有收藏，阅读时点星标即可收藏'
                      : unreadOnly
                        ? '没有未读内容，都看完啦'
                        : activeSourceId
                          ? '该来源暂无内容'
                          : (mode === 'bulletin' ? '暂无动态' : '暂无文章')}
                </span>
              </div>
            ) : (
              <div key={`${activeSourceId ?? '__all__'}|${mode}|${favOnly ? 'fav' : 'flow'}`}>
                {articles.map((article, index) => {
                  const key = dayKeyOf(article);
                  return (
                    <div key={article.id} className="m-press" {...pressBind(article)}>
                      <ArticleRow
                        article={article}
                        active={false}
                        isUnread={isArticleUnread(article)}
                        isFav={favoriteIds.has(article.id)}
                        entryBulletin={bulletinView}
                        showLabel={grouping && (index === 0 || key !== dayKeyOf(articles[index - 1]))}
                        dayKey={key}
                        searchQuery={searchQuery}
                        source={sourceMap[article.source_id]}
                        sourceName={sourceNameMap[article.source_id] || article.source_id}
                        onSelect={selectArticle}
                        onPrefetchEnter={schedulePrefetch}
                        onPrefetchLeave={cancelPrefetch}
                        onToggleFavorite={handleToggleFavorite}
                        onContextMenu={noopContextMenu}
                        ctxAnchor={sheet?.anchorKey === `article:${article.id}`}
                      />
                    </div>
                  );
                })}
                {hasMore && (
                  <div ref={sentinelRef} className="m-load-sentinel" aria-hidden="true">
                    {loadingMore && <ArticleCardsSkeleton count={3} delayed={false} />}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 底部 TabBar(轨语言横向翻译:wash 块 + accent-ink + 文字标签) ── */}
      <nav className="m-tabbar" aria-label="阅读视图">
        {[
          ['article', '文章', FileText],
          ['bulletin', '动态', Zap],
          ['social', '社交', AtSign],
          ['me', '我的', UserRound],
        ].map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => goTab(id)}
            aria-label={label}
            aria-current={tab === id ? 'page' : undefined}
            className={`m-tab ${tab === id ? 'is-on' : ''}`}
          >
            <span className="m-tab-ic"><Icon /></span>
            <span className="m-tab-label">{label}</span>
          </button>
        ))}
      </nav>

      {/* ── 发现页(整页层:低频目的地,从 我的/抽屉 进入) ── */}
      {discover && (
        <div className="m-page" role="region" aria-label="发现">
          <div className="m-topbar on-pane">
            <button
              type="button"
              className="m-iconbtn"
              onClick={() => setDiscover(false)}
              aria-label="返回"
            >
              <ChevronLeft />
            </button>
            <span className="m-title">发现</span>
          </div>
          <div className="m-page-scroll">
            <DiscoverPage
              sources={discoverSources}
              subscribedIds={subscribedIds}
              loading={sourcesLoading}
              pinningId={pinningId}
              onSubscribe={handleSubscribe}
              onUnsubscribe={handleUnsubscribe}
              onPreview={(source) => goSource(source.source_id)}
            />
          </div>
        </div>
      )}

      {/* ── 正文页(push 全屏,无 TabBar) ── */}
      {readOpen && (
        <MobileArticlePage
          rs={rs}
          aiEnabled={aiEnabled}
          showToast={showToast}
          onBack={() => selectArticle(null)}
          onMore={() => openArticleSheet(activeArticle)}
        />
      )}

      {/* ── 源抽屉 ── */}
      <MobileSourceDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        mode={mode}
        socialView={socialView}
        sourcesLoading={sourcesLoading}
        sidebarGroups={sidebarGroups}
        unreadBySource={unreadBySource}
        activeSourceId={activeSourceId}
        favOnly={favOnly}
        hasNoSubscriptions={hasNoSubscriptions}
        activeUnsubscribed={activeUnsubscribed}
        sheetAnchorKey={sheet?.anchorKey || null}
        goContainerAll={goContainerAll}
        goFavorites={goFavorites}
        goSource={goSource}
        onOpenDiscover={() => setDiscover(true)}
        onSourcePress={openSourceSheet}
      />

      {/* ── 长按动作单(桌面右键菜单的 bottom-sheet 翻译) ── */}
      <ActionSheet
        open={Boolean(sheet)}
        title={sheet?.title || ''}
        items={sheet?.items || []}
        onClose={() => setSheet(null)}
      />
    </div>
  );
}
