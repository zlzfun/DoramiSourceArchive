import { memo, useCallback, useEffect, useRef, useState } from 'react';
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
  LayoutDashboard,
  MessageSquare,
  CloudOff,
  Share2,
  Newspaper,
  Tags,
} from 'lucide-react';
import LogoMark from './LogoMark';
import BrandLogoImage from './BrandLogoImage';
import RailUserFlyout from './RailUserFlyout';
import ReaderMarkdown from './ReaderMarkdown';
import ReaderAiPanel from './ReaderAiPanel';
import ShareMenu from './ShareMenu';
import ContextMenu from './ContextMenu';
import { useContextMenu } from '../hooks/useContextMenu';
import { useReaderState } from '../hooks/useReaderState';
import { resolveCompany } from '../sourceTaxonomy';
import DiscoverPage from './DiscoverPage';
import SocialFlow from './SocialFlow';
import AnnouncementBanner from './AnnouncementBanner';
import PersonalBriefTab from './PersonalBriefTab';
import InterestManager from './InterestManager';
import AnalysisTagChip from './AnalysisTagChip';
import { excerptOf } from '../utils/readerText';
import { highlightMatch } from '../utils/highlight';
import { dayKeyOf, dayLabelOf } from '../utils/readerTime';
import { formatRelativeTime, formatDateTime } from '../utils/datetime';
import { contentTypeLabel } from '../utils/contentType';
import { displayAnalysisTags, primaryAnalysisLabel, qualityScoreText } from '../utils/analysis';
import { useOverlayScrollbar } from '../hooks/useOverlayScrollbar';
import { mediaProxyUrl } from '../api';

// 数据层逻辑(源目录/订阅/未读/收藏/列表/正文缓存/AI 缓存/深链/菜单 items)已抽入
// hooks/useReaderState.js(移动波 Wave1)——本文件只余桌面四带式的 JSX 与视图胶水:
// overlay 滚动条、memo 行的 latest-ref 稳定回调、右键弹层(useContextMenu)、品牌图回退。
// 移动壳消费同一份 useReaderState,各写各的交互原语(hover/右键 vs 常显/长按)。

// 日期分组 & 条目时刻的实现已上移 utils/readerTime.js —— 社交媒体流(SocialFlow)
// 与条目列共用同一套组头语法,复制一份会漂移。

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
export function ArticleCardsSkeleton({ count = 5, delayed = true }) {
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
export function PaneBodySkeleton() {
  const lines = ['w-full', 'w-full', 'w-11/12', 'w-full', 'w-4/5', 'w-full', 'w-full', 'w-2/3'];
  return (
    <div className="skeleton-delay" aria-hidden="true">
      {lines.map((w, i) => (
        <div key={i} className={`skeleton h-4 ${w} ${i > 0 ? 'mt-3' : ''}`} />
      ))}
    </div>
  );
}

// 条目行(memo):未读轮询、搜索键入、hover 预取等高频父级渲染下,只有 props 实际
// 变化的行才重渲(此前整列随任意父级 state 重渲)。回调经父级 latest-ref 稳定包装,
// article/source 对象引用在增量追加下保持不变,memo 浅比较即可生效。
// (export 供移动壳复用同一张条目卡——语法/收藏星/未读点单一事实来源。)
export const ArticleRow = memo(function ArticleRow({
  article, active, isUnread, isFav, entryBulletin, showLabel, dayKey, searchQuery,
  source, sourceName, onSelect, onPrefetchEnter, onPrefetchLeave, onToggleFavorite,
  onContextMenu, ctxAnchor,
}) {
  const excerpt = entryBulletin
    ? ''
    : excerptOf(article.summary_zh || article.content_preview || article.content);
  const analysisLabel = primaryAnalysisLabel(article);
  const score = qualityScoreText(article.quality_score);
  return (
    <>
      {showLabel && <div className="reader-date-label">{dayLabelOf(dayKey)}</div>}
      <button
        type="button"
        onClick={() => onSelect(article)}
        onMouseEnter={() => onPrefetchEnter(article)}
        onMouseLeave={onPrefetchLeave}
        onContextMenu={(e) => onContextMenu(e, article, 'article')}
        className={`reader-entry ${entryBulletin ? 'is-bulletin' : ''} ${active ? 'is-active' : ''} ${isUnread ? '' : 'is-read'} ${isFav ? 'is-fav' : ''} ${ctxAnchor ? 'is-ctx-anchor' : ''}`}
      >
        <span className="reader-entry-top">
          {source && (
            <span className="reader-entry-logo" aria-hidden="true">
              <LogoMark company={resolveCompany(source)} size="s15" emoji={source.icon} />
            </span>
          )}
          <span className="reader-entry-src">{sourceName}</span>
          <span
            className="reader-entry-time"
            title={formatDateTime(article.publish_date || article.fetched_date)}
          >
            {formatRelativeTime(article.publish_date || article.fetched_date, '')}
          </span>
        </span>
        {(score || analysisLabel) && (
          <span className="reader-entry-analysis">
            {score && <span className="reader-score-chip" title="AI 内容价值评估，不代表事实保证或用户评分">{score}</span>}
            {analysisLabel && <span className="reader-tag-chip">{analysisLabel}</span>}
          </span>
        )}
        {/* 标题行:标题占位 + 右缘收藏星标(Folo 式)。星内联于标题行,
            正文/摘要照旧铺满整宽,只标题让出星位——不再整卡右缩(修右侧留白)。
            卡本身是 <button>,故收藏钮用 role=button 的 span,避免按钮嵌套;
            已收藏常显琥珀实星,未收藏悬停浮出空心星、点击切换。
            键盘可达:tabIndex=0 + 回车/空格触发(不动 stopPropagation 语义)。 */}
        <span className="reader-entry-titlerow">
          {/* 未读小蓝点移到标题左侧栏(与右缘收藏星标错开——两者同现时不再挤在右侧);
              绝对定位于左槽,不挤占标题宽度,已读缩零淡出 */}
          <span className={`reader-unread-dot ${isUnread ? '' : 'is-off'}`} aria-hidden="true" />
          <span className="reader-entry-title">{searchQuery ? highlightMatch(article.title || '（无标题）', searchQuery) : (article.title || '（无标题）')}</span>
          <span
            role="button"
            tabIndex={0}
            aria-label={isFav ? '取消收藏' : '收藏'}
            title={isFav ? '取消收藏' : '收藏'}
            onClick={(e) => { e.stopPropagation(); onToggleFavorite(article, e); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                e.stopPropagation();
                onToggleFavorite(article, e);
              }
            }}
            className={`reader-entry-fav ${isFav ? 'is-on' : ''}`}
          >
            <Star className="h-[15px] w-[15px]" fill={isFav ? 'currentColor' : 'none'} />
          </span>
        </span>
        {/* 摘要行:AI 要点摘要(summary_zh)优先——正文截断对英文长文几乎无信息量 */}
        {excerpt && <span className="reader-entry-excerpt">{searchQuery ? highlightMatch(excerpt, searchQuery) : excerpt}</span>}
      </button>
    </>
  );
});

export default function ReaderTab({
  showToast,
  aiEnabled = false,
  userSourcesEnabled = false,
  personalDigestEnabled = false,
  // ── standalone(读者账号):应用导轨已隐藏,视图轨独占——轨底并入用户菜单 ──
  standalone = false,
  account = null,
  onUserUpdated,
  themeDark = false,
  onToggleTheme,
  onOpenSettings,
  onLogout,
  // ── v3.19 多管理员波:admin 从管理台切入阅读器时传入,轨底浮现「返回管理台」;读者账号恒 undefined ──
  onExitReader = null,
  // 反馈有未读管理员回复(读者账号):轨底头像/设置钮挂轻通知点
  feedbackUnread = 0,
  // ── 站内分享深链(#/reader/a/{id}):带 id 进来时直接开这篇,消费后回调清空 ──
  initialArticleId = '',
  onDeepLinkConsumed,
}) {
  const [brandFailed, setBrandFailed] = useState(false); // 品牌 logo 加载失败 → 回退铃铛
  const [briefOpen, setBriefOpen] = useState(false);
  const [interestOpen, setInterestOpen] = useState(false);
  const [interestVersion, setInterestVersion] = useState(0);
  const onboardingRequired = personalDigestEnabled
    && account?.role === 'user'
    && account?.interest_onboarding_completed === false;

  useEffect(() => {
    if (!personalDigestEnabled) setBriefOpen(false);
  }, [personalDigestEnabled]);
  const closeBriefBeforeArticleOpen = useCallback(() => setBriefOpen(false), []);

  const {
    // 源目录 / 订阅
    sourcesLoading, discoverSources, subscribedIds, sourceMap, sourceNameMap,
    sidebarGroups, hasNoSubscriptions, socialSources, platformCount, pinningId,
    handleSubscribe, handleUnsubscribe, handleAddCustomSource,
    collections, discoverCollectionId, setDiscoverCollectionId,
    collectionPinningId, handleSubscribeCollection, handleUnsubscribeCollection,
    // 视图 / 导航
    mode, activeSourceId, favOnly, discover, setDiscover,
    bulletinView, socialView, railActive, listTitle,
    goView, goSource, goContainerAll, goFavorites,
    activeSourceHidden, activeUnsubscribed, grouping,
    // 搜索
    searchOpen, searchInput, setSearchInput, searchQuery, toggleSearch, searchForLabel,
    // 未读体系
    unreadBySource, unreadOnly, setUnreadOnly, scopeUnread,
    isArticleUnread, handleTogglePaneRead, handleToggleSocialRead,
    handleMarkAllRead, markingRead, paneReadToggling, socialReadToggling,
    freshCount, handleRefreshFresh,
    // 列表
    articles, articlesLoading, loadingMore, hasMore, handleLoadMore,
    listRef, sentinelRef,
    // 选中文章 / 正文
    activeArticle, activeBody, activeBodyLoading, selectArticle, openArticleById,
    schedulePrefetch, cancelPrefetch,
    activeIndex, prevArticle, nextArticle,
    crumbSource, crumbName, displayBody, displayTranslatedBody, bodyStats,
    // 收藏
    favoriteIds, favTogglingId, handleToggleFavorite,
    // 分享
    shareOpen, setShareOpen,
    // AI(翻译 / 速读)
    showTranslation, translating, translatedBody, handleTranslate,
    activeSummary, summarizing, handleSummarize,
    // 上下文菜单 items(桌面右键在此装配弹层)
    buildArticleMenuItems, buildSourceMenuItems, buildSocialMenuItems,
  } = useReaderState({
    showToast,
    account,
    initialArticleId,
    onDeepLinkConsumed,
    onBeforeOpenArticle: closeBriefBeforeArticleOpen,
  });

  const listThumbRef = useRef(null); // 浮层滚动条滑块(压在卡片上,内容满宽)
  // 文章/动态中栏会被发现页与社交流整段卸载；active 让自绘滚动条在 DOM 重建后
  // 重新绑定到新节点，避免监听器滞留在旧节点、出现“内容滚动但滑块不动”。
  const resyncListScrollbar = useOverlayScrollbar(
    listRef,
    listThumbRef,
    !briefOpen && !discover && mode !== 'social',
  );

  // 列表内容高度变化(切源/追加/加载态)后重算浮层滚动条滑块
  useEffect(() => { resyncListScrollbar(); }, [articles, articlesLoading, activeArticle, resyncListScrollbar]);

  // ── 右键上下文菜单(v3.28,样页 dorami-context-menu-quiet) ──
  // items 构建在 useReaderState(桌面右键/移动长按共用);弹出定位与开合是桌面视图胶水。
  const { menu: ctxMenu, openMenu: openCtxMenu, closeMenu: closeCtxMenu } = useContextMenu();

  const openRowContextMenu = (e, entity, kind) => {
    const items = kind === 'source'
      ? buildSourceMenuItems(entity)
      : kind === 'social'
        ? buildSocialMenuItems(entity)
        : buildArticleMenuItems(entity);
    const anchorId = kind === 'source' ? entity.source_id : entity.id;
    openCtxMenu(e, items, `${kind}:${anchorId}`);
  };

  // memo 行(ArticleRow/SocialPost)的稳定回调:latest-ref 模式——传给行的引用永不变,
  // 内部转发到最新实现,行组件不因父级回调重建而整列重渲。
  const rowHandlersRef = useRef({});
  const onRowSelect = useCallback((a) => rowHandlersRef.current.select(a), []);
  const onRowPrefetchEnter = useCallback((a) => rowHandlersRef.current.prefetchEnter(a), []);
  const onRowPrefetchLeave = useCallback(() => rowHandlersRef.current.prefetchLeave(), []);
  const onRowToggleFavorite = useCallback((a, e) => rowHandlersRef.current.toggleFav(a, e), []);
  const onRowToggleSocialRead = useCallback((a) => rowHandlersRef.current.toggleSocialRead(a), []);
  // 右键菜单(v3.28):同走 latest-ref——onContextMenu 是新的一列级 prop,引用漂移会让
  // ArticleRow/SocialPost 的 memo 整列失效。
  const onRowContextMenu = useCallback((e, entity, kind) => rowHandlersRef.current.contextMenu(e, entity, kind), []);

  // latest-ref 稳定回调的实现同步(每次渲染后更新为最新闭包;事件回调只在渲染完成后触发,useEffect 时序上足够)
  useEffect(() => {
    rowHandlersRef.current = {
      select: selectArticle,
      prefetchEnter: schedulePrefetch,
      prefetchLeave: cancelPrefetch,
      toggleFav: handleToggleFavorite,
      toggleSocialRead: handleToggleSocialRead,
      contextMenu: openRowContextMenu,
    };
  });

  return (
    <div className="reader-shell">
      {/* ── 管理员公告横幅(v3.18):无公告时渲染 null,:has 不命中,四带布局逐像素不变 ── */}
      <AnnouncementBanner />
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
        {personalDigestEnabled && (
          <>
            <button
              type="button"
              aria-label="我的早报"
              aria-pressed={briefOpen}
              onClick={() => { setDiscover(false); setBriefOpen(true); }}
              className={`reader-vrail-btn ${briefOpen ? 'is-on' : ''}`}
            >
              <Newspaper className="h-[18px] w-[18px]" />
              <span className="reader-vrail-tip">我的早报</span>
            </button>
            <span className="reader-vrail-divider" aria-hidden="true" />
          </>
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
            aria-pressed={!briefOpen && railActive === view}
            onClick={() => { setBriefOpen(false); goView(view); }}
            className={`reader-vrail-btn ${!briefOpen && railActive === view ? 'is-on' : ''}`}
          >
            <Icon className="h-[18px] w-[18px]" />
            <span className="reader-vrail-tip">{label}</span>
          </button>
        ))}
        {/* 发现:整页源目录(取代源栏内联「发现更多来源」)。与上方三个内容容器
            语义有别(读内容 vs 找内容),以分隔线分组。 */}
        <span className="reader-vrail-divider" aria-hidden="true" />
        <button
          type="button"
          aria-label="发现"
          aria-pressed={!briefOpen && discover}
          onClick={() => { setBriefOpen(false); setDiscover(true); }}
          className={`reader-vrail-btn ${!briefOpen && discover ? 'is-on' : ''}`}
        >
          <Compass className="h-[18px] w-[18px]" />
          <span className="reader-vrail-tip">发现</span>
        </button>
        {personalDigestEnabled && (
          <button
            type="button"
            aria-label="管理个人兴趣"
            aria-expanded={interestOpen || onboardingRequired}
            onClick={() => setInterestOpen(true)}
            className="reader-vrail-btn"
          >
            <Tags className="h-[18px] w-[18px]" />
            <span className="reader-vrail-tip">我的兴趣</span>
          </button>
        )}

        {/* 轨底(standalone):用户滑出菜单(2026-07-24 拍板)——常态只见头像,
            hover 滑出 返回管理台(仅 admin)/主题/设置,头像同帧变关机退出钮点击即退。 */}
        {standalone && (
          <>
            <div className="reader-vrail-spring" />
            <RailUserFlyout
              avatar={account?.avatar}
              username={account?.username}
              onLogout={onLogout}
              onLogoutHint={() => showToast('再次点击以退出登录', 'info')}
              notify={feedbackUnread > 0}
            >
              {/* 返回管理台(v3.19):与应用导轨轨底「进入阅读器」对称的隐藏切换钮,仅 admin 有 */}
              {onExitReader && (
                <button
                  type="button"
                  onClick={onExitReader}
                  className="reader-vrail-btn"
                  aria-label="返回管理台"
                >
                  <LayoutDashboard className="h-[18px] w-[18px]" />
                  <span className="reader-vrail-tip">返回管理台</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => onToggleTheme?.()}
                className="reader-vrail-btn"
                aria-label={themeDark ? '切换到亮色' : '切换到暗色'}
              >
                {themeDark ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
                <span className="reader-vrail-tip">{themeDark ? '切换亮色' : '切换暗色'}</span>
              </button>
              {/* 反馈与建议(仅读者账号;admin 的设置柜没有反馈分区):原先只藏在
                  设置柜二级分区里入口太深,提为轨底一级钮,深链直达该分区。 */}
              {!onExitReader && (
                <button
                  type="button"
                  onClick={() => onOpenSettings?.('feedback')}
                  className="reader-vrail-btn"
                  aria-label={feedbackUnread > 0 ? '反馈与建议(有新回复)' : '反馈与建议'}
                >
                  <MessageSquare className="h-[18px] w-[18px]" />
                  {feedbackUnread > 0 && <span className="vrail-btn-dot" aria-hidden="true" />}
                  <span className="reader-vrail-tip">{feedbackUnread > 0 ? '反馈与建议 · 有新回复' : '反馈与建议'}</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => onOpenSettings?.()}
                className="reader-vrail-btn"
                aria-label="设置"
              >
                <Settings className="h-[18px] w-[18px]" />
                <span className="reader-vrail-tip">设置</span>
              </button>
            </RailUserFlyout>
          </>
        )}
      </nav>

      {/* ── 源栏 · 我的订阅 ── */}
      {!briefOpen && <aside className="reader-col reader-col-sources">
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
                        onContextMenu={(e) => onRowContextMenu(e, source, 'source')}
                        className={`reader-source-row ${active ? 'reader-source-row-active' : ''} ${unread > 0 ? 'has-unread' : ''} ${source.hidden ? 'is-unavailable' : ''} ${ctxMenu?.anchorKey === `source:${source.source_id}` ? 'is-ctx-anchor' : ''}`}
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
                        {/* 临时隐藏的已订阅源:条目保留但内容停发,标记说明状态;悬停退订钮照常浮出 */}
                        {source.hidden && <span className="reader-src-off">暂不可用</span>}
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
      </aside>}

      {briefOpen && (
        <PersonalBriefTab
          showToast={showToast}
          interestVersion={interestVersion}
          onManageSubscriptions={() => { setBriefOpen(false); setDiscover(true); }}
          onOpenArticle={async (articleId) => {
            const opened = await openArticleById(articleId);
            if (opened) setBriefOpen(false);
          }}
        />
      )}

      {/* ── 发现页:占据 条目列+阅读窗 的整片区域(源栏保持在场,订阅结果即时可见) ── */}
      {!briefOpen && discover && (
        <DiscoverPage
          sources={discoverSources}
          subscribedIds={subscribedIds}
          loading={sourcesLoading}
          pinningId={pinningId}
          onSubscribe={handleSubscribe}
          onUnsubscribe={handleUnsubscribe}
          onPreview={(source) => goSource(source.source_id)}
          collections={collections}
          activeCollectionId={discoverCollectionId}
          onOpenCollection={(c) => setDiscoverCollectionId(c.collection_id)}
          onCloseCollection={() => setDiscoverCollectionId(null)}
          collectionPinningId={collectionPinningId}
          onSubscribeCollection={handleSubscribeCollection}
          onUnsubscribeCollection={handleUnsubscribeCollection}
          userSourcesEnabled={userSourcesEnabled}
          onAddCustomSource={handleAddCustomSource}
        />
      )}

      {/* ── 社交媒体流(第三容器):占「条目列 + 阅读窗」整幅,取代四带式 ── */}
      {!briefOpen && !discover && socialView && (
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
          onToggleFavorite={onRowToggleFavorite}
          favOnly={favOnly}
          searchOpen={searchOpen}
          searchInput={searchInput}
          searchQuery={searchQuery}
          onSearchInputChange={setSearchInput}
          onToggleSearch={toggleSearch}
          readTogglingId={socialReadToggling}
          onToggleRead={onRowToggleSocialRead}
          onPostContextMenu={onRowContextMenu}
          ctxAnchorKey={ctxMenu?.anchorKey || null}
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
      )}

      {/* ── 条目列 ── */}
      {!briefOpen && !discover && !socialView && (
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
          {/* 新内容提示条:轮询发现未读正增量时出现,点击刷新——不自动插入打断阅读 */}
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
                const key = dayKeyOf(article);
                return (
                  <ArticleRow
                    key={article.id}
                    article={article}
                    active={activeArticle?.id === article.id}
                    isUnread={isArticleUnread(article)}
                    isFav={favoriteIds.has(article.id)}
                    /* 条目列只在文章/动态容器渲染(社交走 SocialFlow),容器内形态同质:
                       动态容器整条呈紧凑形(无独立标题,不挂摘要),不再需要逐条形态 chip。 */
                    entryBulletin={bulletinView}
                    showLabel={grouping && (index === 0 || key !== dayKeyOf(articles[index - 1]))}
                    dayKey={key}
                    searchQuery={searchQuery}
                    source={sourceMap[article.source_id]}
                    sourceName={sourceNameMap[article.source_id] || article.source_id}
                    onSelect={onRowSelect}
                    onPrefetchEnter={onRowPrefetchEnter}
                    onPrefetchLeave={onRowPrefetchLeave}
                    onToggleFavorite={onRowToggleFavorite}
                    onContextMenu={onRowContextMenu}
                    ctxAnchor={ctxMenu?.anchorKey === `article:${article.id}`}
                  />
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
      {!briefOpen && !discover && !socialView && (
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
                {crumbSource ? (
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
              {/* 分享:站内深链 + 公开只读链接(浮层锚定本钮,见 ShareMenu) */}
              <div className="reader-share-anchor">
                <button
                  type="button"
                  onClick={() => setShareOpen(v => !v)}
                  title="分享"
                  aria-label="分享"
                  aria-expanded={shareOpen}
                  className={`reader-pane-iconbtn ${shareOpen ? 'is-blue' : ''}`}
                >
                  <Share2 className="h-4 w-4" />
                </button>
                {shareOpen && (
                  <ShareMenu
                    articleId={activeArticle.id}
                    onClose={() => setShareOpen(false)}
                    showToast={showToast}
                  />
                )}
              </div>
              {aiEnabled && (
                <button
                  type="button"
                  onClick={handleTranslate}
                  disabled={translating || activeBodyLoading || !activeBody}
                  title={showTranslation ? '当前显示中文译文，点击切回原文' : '将正文译为中文'}
                  aria-label={showTranslation ? '显示原文' : '译为中文'}
                  aria-pressed={showTranslation}
                  className={`reader-pane-iconbtn ${showTranslation ? 'is-ai' : ''}`}
                >
                  {translating
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <span className="reader-tr-glyph" aria-hidden="true">译</span>}
                </button>
              )}
            </div>

          {/* key 按文章 id 重挂载,触发 reader-enter 淡入+轻上移(体验二波 A1) */}
          <article className="reader-pane reader-enter" key={activeArticle.id}>
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
                {/* 字数与时长信息冗余(时长即由字数换算),只留时长;
                    阅读量 = 全站累计阅读次数(跨读者;含本次打开,由 /read 响应回填) */}
                {bodyStats && <span>阅读时长 {bodyStats.minutes} 分钟</span>}
                {typeof activeArticle.read_count === 'number' && activeArticle.read_count > 0 && (
                  <span>阅读量 {activeArticle.read_count.toLocaleString()}</span>
                )}
              </div>
              {(activeArticle.quality_score != null || displayAnalysisTags(activeArticle).length > 0) && (
                <div className="reader-analysis-summary">
                  <div className="reader-analysis-top">
                    {activeArticle.quality_score != null && (
                      <span className="reader-analysis-score">
                        <strong>{qualityScoreText(activeArticle.quality_score)}</strong>
                        <small>内容价值分</small>
                      </span>
                    )}
                    <span className="reader-analysis-tags">
                      {displayAnalysisTags(activeArticle).map((tag, index) => (
                        <AnalysisTagChip
                          key={`${tag.type || 'canonical'}-${tag.id || tag.code || tag.candidate_id || index}`}
                          tag={tag}
                          onTemporarySearch={searchForLabel}
                        />
                      ))}
                    </span>
                  </div>
                  {activeArticle.score_reason && <p>{activeArticle.score_reason}</p>}
                  <small>AI 内容价值评估，用于辅助筛选，不代表事实保证或你的个人评分</small>
                </div>
              )}
            </header>
            <div className="reader-pane-body markdown-body">
              {/* 哆啦美速读:有缓存直接展示;无缓存给低调的生成入口(MVP 不自动生成,控成本) */}
              {aiEnabled && !activeBodyLoading && (activeSummary || activeBody) && (
                <div className="reader-ai-summary">
                  <div className="reader-ai-summary-head">
                    <Sparkles className="h-3.5 w-3.5" /> <span className="ai-grad-text">哆啦美速读</span>
                  </div>
                  {activeSummary ? (
                    <p className="reader-ai-summary-text">{activeSummary}</p>
                  ) : summarizing ? (
                    <div className="reader-ai-summary-skel" role="status" aria-label="正在生成速读">
                      <span className="skeleton" /><span className="skeleton" /><span className="skeleton" />
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={handleSummarize}
                      className="reader-ai-summary-generate"
                    >
                      生成本文要点速读
                    </button>
                  )}
                </div>
              )}
              {activeBodyLoading ? (
                <PaneBodySkeleton />
              ) : (showTranslation && translatedBody) ? (
                <ReaderMarkdown>{displayTranslatedBody}</ReaderMarkdown>
              ) : activeBody ? (
                <ReaderMarkdown>{displayBody}</ReaderMarkdown>
              ) : (
                '该文章暂无正文内容，点击「查看来源」阅读完整内容。'
              )}
              {/* 用户自定源(v3.40):正文尾部一律附原文链接——feed 给什么存什么的
                  最简正文口径下,摘要型源读完即达原文;全文源多一个出口也无碍 */}
              {!activeBodyLoading && String(activeArticle.source_id || '').startsWith('user_rss_')
                && activeArticle.source_url && (
                <p className="reader-pane-origin">
                  <a href={activeArticle.source_url} target="_blank" rel="noreferrer">
                    阅读原文 ↗
                  </a>
                </p>
              )}
            </div>
            {/* 上一篇/下一篇:沿当前列表序的真实翻页(选中项不在列表时隐藏) */}
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
            <span>{bulletinView ? '选择一条动态以开始阅读' : '选择一篇文章以开始阅读'}</span>
            {/* 新老用户通用的轻引导:空态下一行小字直达发现页(欢迎卡方案已否决——太啰嗦) */}
            <button type="button" className="reader-empty-link" onClick={() => setDiscover(true)}>
              去「发现」添加订阅
            </button>
          </div>
        )}
      </section>
      )}

      {!briefOpen && !discover && (
        <ReaderAiPanel
          aiEnabled={aiEnabled}
          activeArticle={activeArticle}
          showToast={showToast}
          onOpenArticle={openArticleById}
        />
      )}

      {/* 右键上下文菜单(单例,portal 到 body):关闭即 ctxMenu 置空,锚定态随之消失 */}
      {ctxMenu && (
        <ContextMenu x={ctxMenu.x} y={ctxMenu.y} items={ctxMenu.items} onClose={closeCtxMenu} />
      )}
      <InterestManager
        open={interestOpen || onboardingRequired}
        onboarding={onboardingRequired}
        onClose={() => setInterestOpen(false)}
        onSaved={({ onboardingCompleted } = {}) => {
          setInterestVersion((value) => value + 1);
          setInterestOpen(false);
          if (onboardingCompleted) onUserUpdated?.({ interest_onboarding_completed: true });
        }}
        showToast={showToast}
      />
    </div>
  );
}
