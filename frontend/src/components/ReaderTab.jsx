import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  Podcast,
  Newspaper,
  Tags,
  ChevronLeft,
  ChevronRight,
  Rss,
  Ban,
} from 'lucide-react';
import LogoMark from './LogoMark';
import BrandLogoImage from './BrandLogoImage';
import RailLogoutAvatar from './RailLogoutAvatar';
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
import { PodcastCover } from './PodcastAudioPanel';
import PodcastExperiencePanel from './PodcastExperiencePanel';
import PersonalBriefPage from './PersonalBriefPage';
import InterestPage from './InterestPage';
import AnalysisTagChip from './AnalysisTagChip';
import { excerptOf, hostOf } from '../utils/readerText';
import { highlightMatch } from '../utils/highlight';
import { dayLabelOf } from '../utils/readerTime';
import { buildListPlan } from '../utils/listPlan';
import { formatRelativeTime, formatDateTime, formatPublishDate } from '../utils/datetime';
import { formatPodcastDuration, podcastOf, podcastProcessingMeta } from '../utils/podcast';
import {
  SCORE_DISCLAIMER,
  analysisStatusMeta,
  contentGenreLabel,
  displayAnalysisTags,
  podcastAssessmentMeta,
  podcastFullProcessingMeta,
  primaryAnalysisLabel,
  qualityScoreText,
  scoreTierClass,
  shouldShowAiReadingCard,
} from '../utils/analysis';
import AiReadingCard from './AiReadingCard';
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
/* ── 三谓词过滤面板(issue #27 兴趣即透镜,样页 dorami-interest-lens-quiet ①):
   订阅 / 兴趣 / 收藏 三枚可多选开关,按源 / 按标签 / 按篇两两正交,AND 联合;
   选中态 = wash 底 + 勾(多选语义),与单选源行的浮白 + 弱高程分家;兴趣未设时灰掉,点它直落发现页兴趣段。
   桌面源栏与移动抽屉共用。 */
/* 栏头二段 = 轴切换(issue #27 五稿):订阅(其下列已订阅源)| 兴趣(其下列关注的标签),互斥;
   单选语义——选中态浮白 + 弱高程,与其下源行 / 标签行同一语法(整栏只有一种语义:单选)。
   点已点亮的段 = 回该轴全集。桌面源栏与移动抽屉共用。 */
export function AxisSeg({ axis, onChange, className = '' }) {
  const rows = [['subscribed', '订阅', Rss], ['interest', '兴趣', Tags]];
  return (
    <div className={`reader-axis ${className}`} role="radiogroup" aria-label="按什么切分">
      {rows.map(([key, label, Icon]) => {
        const on = axis === key;
        return (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(key)}
            className={`reader-axis-btn ${on ? 'is-on' : ''}`}
          >
            <Icon className="h-3 w-3" aria-hidden="true" />
            <span>{label}</span>
          </button>
        );
      })}
    </div>
  );
}

/* 兴趣轴的列表:关注的标签按目录面分组,行语法与源行一致(图标位是标签圆点);
   没设兴趣时一句引导直落发现页兴趣段(不锁栏、不弹层——拍板 3 另议,此为占位形态)。 */
export function TagRows({ groups, activeTagId, onPick, hasInterests, onOpenInterests }) {
  if (!hasInterests) {
    return (
      <div className="reader-axis-empty">
        还没有设置兴趣。
        <button type="button" className="reader-axis-empty-link" onClick={onOpenInterests}>去发现页选几个感兴趣的方向 →</button>
      </div>
    );
  }
  return groups.map(({ key, label, list }) => (
    <section className="reader-subs" key={key}>
      <div className="reader-src-label">{label}</div>
      <div className="reader-group-body">
        {list.map((tag) => {
          const active = activeTagId === tag.id;
          return (
            <div
              key={tag.id}
              role="button"
              tabIndex={0}
              onClick={() => onPick(tag.id)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(tag.id); } }}
              className={`reader-source-row reader-tag-row ${active ? 'reader-source-row-active' : ''}`}
            >
              <span className="reader-tag-dot" aria-hidden="true" />
              <p className="reader-source-name min-w-0 flex-1">{tag.name}</p>
            </div>
          );
        })}
      </div>
    </section>
  ));
}

/* 屏蔽折叠行:「已屏蔽 · 机器人技术、具身智能 · 展开」——写屏蔽了什么,不写几篇 */
export function MutedFoldRow({ tags, expanded, onToggle, showLabel = false, dayKey = '' }) {
  return (
    <>
      {showLabel && <div className="reader-date-label">{dayLabelOf(dayKey)}</div>}
      <button type="button" className={`reader-fold ${expanded ? 'is-open' : ''}`} onClick={onToggle} aria-expanded={expanded}>
        <Ban className="h-3 w-3" aria-hidden="true" />
        <span className="reader-fold-text">已屏蔽 · <b>{tags.join('、')}</b></span>
        <span className="reader-fold-act">{expanded ? '收起' : '展开'}</span>
      </button>
    </>
  );
}

export const ArticleRow = memo(function ArticleRow({
  article, active, isUnread, isFav, entryBulletin, entryPodcast, showLabel, dayKey, searchQuery,
  source, sourceName, onSelect, onPrefetchEnter, onPrefetchLeave, onToggleFavorite,
  onContextMenu, ctxAnchor,
  // issue #27 兴趣即透镜:命中的兴趣标签名(顶行一枚胶囊)/ 订阅外标记(悬停翻「+ 订阅」)/ 屏蔽项展开态
  interestHit = '', labelSuppress = '', unsubscribed = false, onSubscribeSource = null, muted = false,
}) {
  const excerpt = entryBulletin
    ? ''
    : excerptOf(article.summary_zh || article.content_preview || article.content);
  const podcast = entryPodcast ? podcastOf(article) : null;
  const podcastFullStatus = entryPodcast ? podcastFullProcessingMeta(article) : null;
  const podcastStatus = podcastFullStatus || podcastProcessingMeta(
    podcast?.processing_status,
    Boolean(podcast?.condensed_audio_url),
  );
  const analysisLabel = primaryAnalysisLabel(article);
  const score = qualityScoreText(article.quality_score);
  const scoreTier = scoreTierClass(article.quality_score);   // issue #54:按分值分档着色
  const analysisStatus = analysisStatusMeta(article, { podcast: entryPodcast });
  const podcastAssessment = entryPodcast ? podcastAssessmentMeta(article) : null;
  const favoriteControl = (
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
  );
  return (
    <>
      {showLabel && <div className="reader-date-label">{dayLabelOf(dayKey)}</div>}
      <button
        type="button"
        onClick={() => onSelect(article)}
        onMouseEnter={() => onPrefetchEnter(article)}
        onMouseLeave={onPrefetchLeave}
        onContextMenu={(e) => onContextMenu(e, article, 'article')}
        className={`reader-entry ${entryBulletin ? 'is-bulletin' : ''} ${entryPodcast ? 'is-podcast' : ''} ${active ? 'is-active' : ''} ${isUnread ? '' : 'is-read'} ${isFav ? 'is-fav' : ''} ${ctxAnchor ? 'is-ctx-anchor' : ''} ${muted ? 'is-muted' : ''}`}
      >
        {entryPodcast ? (
          <span className="reader-podcast-layout">
            <PodcastCover src={podcast?.image_url} className="reader-podcast-cover" />
            <span className="reader-podcast-copy">
              <span className="reader-entry-top">
                <span className="reader-entry-src">{podcast?.show_title || sourceName}</span>
                {score && (
                  <span className={`reader-entry-score ai-grad-text ${scoreTier}`} title={SCORE_DISCLAIMER}>
                    {score}
                  </span>
                )}
                <span
                  className="reader-entry-time"
                  title={formatDateTime(article.publish_date || article.fetched_date)}
                >
                  {formatRelativeTime(article.publish_date || article.fetched_date, '')}
                </span>
              </span>
              <span className="reader-entry-titlerow">
                <span className={`reader-unread-dot ${isUnread ? '' : 'is-off'}`} aria-hidden="true" />
                <span className="reader-entry-title">{searchQuery ? highlightMatch(article.title || '（无标题）', searchQuery) : (article.title || '（无标题）')}</span>
                {article.is_premium_podcast && <span className="podcast-premium-badge">优质播客</span>}
                {favoriteControl}
              </span>
              <span className="reader-podcast-meta">
                {formatPodcastDuration(podcast?.duration_seconds) && (
                  <span>{formatPodcastDuration(podcast.duration_seconds)}</span>
                )}
                <span className={`podcast-status is-${podcastStatus.tone}`} role={podcastFullStatus ? 'status' : undefined}>
                  {podcastStatus.label}
                </span>
                {podcastAssessment && <span className="stamp stamp-idle" role="status">{podcastAssessment.label}</span>}
                {analysisStatus && <span className={`stamp ${analysisStatus.cls}`} role="status">{analysisStatus.label}</span>}
              </span>
            </span>
          </span>
        ) : (
          <>
            <span className="reader-entry-top">
              {source && (
                <span className="reader-entry-logo" aria-hidden="true">
                  <LogoMark company={resolveCompany(source)} size="s15" emoji={source.icon} />
                </span>
              )}
              <span className="reader-entry-src">{sourceName}</span>
              {/* 订阅外文章(订阅谓词关着的全站范围):源名后 faint「未订阅」,悬停卡片翻成「+ 订阅」——
                  发现的动作发生在阅读现场,不必去发现页 */}
              {unsubscribed && (
                <span
                  role="button"
                  tabIndex={0}
                  className="reader-entry-unsub"
                  title="订阅这个来源"
                  onClick={(e) => { e.stopPropagation(); onSubscribeSource?.(article.source_id); }}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onSubscribeSource?.(article.source_id); } }}
                />
              )}
              {/* 命中兴趣:只挂一枚胶囊,写命中的那个兴趣标签(命中多个取第一个);它是透镜的产物,与开关无关。
                  与分析主签同名时顶替它(不并列两枚同名签)。 */}
              {interestHit && <span className="reader-entry-hit">{interestHit}</span>}
              {/* 分析结果归入元信息行(issue #23 第三项):分类是源名后的一段元信息文字,
                  分数是衬线数字落在时间之前——不再独占一行,晚到只横向填字、标题不动;
                  没有可读结果且分析在途时,分数槽先以「分析中」占位,落地即换成数。 */}
              {/* 分析主签与命中胶囊同名时让位;单标签视图里与当前标签同名时也让位(每行都写同一个词是重复信息) */}
              {analysisLabel && analysisLabel !== interestHit && analysisLabel !== labelSuppress && <span className="reader-entry-tag">{analysisLabel}</span>}
              {score
                ? <span className={`reader-entry-score ai-grad-text ${scoreTier}`} title={SCORE_DISCLAIMER}>{score}</span>
                : (analysisStatus && <span className="reader-entry-score is-pending" role="status">分析中</span>)}
              <span
                className="reader-entry-time"
                title={formatDateTime(article.publish_date || article.fetched_date)}
              >
                {formatRelativeTime(article.publish_date || article.fetched_date, '')}
              </span>
            </span>
            {/* 标题行内收藏控件复用 span role=button，避免 button 嵌套。 */}
            <span className="reader-entry-titlerow">
              <span className={`reader-unread-dot ${isUnread ? '' : 'is-off'}`} aria-hidden="true" />
              <span className="reader-entry-title">{searchQuery ? highlightMatch(article.title || '（无标题）', searchQuery) : (article.title || '（无标题）')}</span>
              {favoriteControl}
            </span>
            {/* 摘要行:AI 要点摘要(summary_zh)优先——正文截断对英文长文几乎无信息量 */}
            {excerpt && <span className="reader-entry-excerpt">{searchQuery ? highlightMatch(excerpt, searchQuery) : excerpt}</span>}
          </>
        )}
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
  // 早报「外出」上下文(issue #23 三稿):从早报点卡片进原文后阅读窗顶部出返回带——
  // {date, revision, scrollTop, itemId, label, sequence[{id,article_id,title}], index}。
  // 用户主动改作用域(视图轨/源栏/发现页)即清;同列表内翻篇保留。restoreRef 把它交还早报页落位。
  const [briefReturn, setBriefReturn] = useState(null);
  const [briefRestore, setBriefRestore] = useState(null); // 返回时交还早报页落位的那份上下文
  const leaveBriefTrail = useCallback(() => setBriefReturn(null), []);
  // 兴趣(issue #27 三稿「兴趣不是地方」):编辑面并入发现页第三段「兴趣」,视图轨不再有兴趣钮;
  // 读东西永远在容器里,兴趣以源栏过滤面板的「兴趣」开关作透镜。首登引导 = 发现页兴趣段顶部一条横幅
  // (不锁页,视图轨照常可走),完成即落早报。发现页的段位提升到这里:引导/「我的」入口要能指定落到兴趣段。
  const [discoverTab, setDiscoverTab] = useState('sources'); // sources | collections | interests
  const [interestVersion, setInterestVersion] = useState(0);
  const onboardingRequired = personalDigestEnabled
    && account?.role === 'user'
    && account?.interest_onboarding_completed === false;
  const pageOpen = briefOpen;

  useEffect(() => {
    if (!personalDigestEnabled) setBriefOpen(false);
  }, [personalDigestEnabled]);
  const closeBriefBeforeArticleOpen = useCallback(() => { setBriefOpen(false); }, []);

  const {
    // 源目录 / 订阅
    sourcesLoading, discoverSources, subscribedIds, sourceMap, sourceNameMap,
    sidebarGroups, hasNoSubscriptions, socialSources, platformCount, pinningId,
    handleSubscribe, handleUnsubscribe, handleAddCustomSource,
    collections, discoverCollectionId, setDiscoverCollectionId,
    collectionPinningId, handleSubscribeCollection, handleUnsubscribeCollection,
    // 视图 / 导航
    mode, activeSourceId, favOnly, discover, openDiscover, closeDiscover, discoverShape, setDiscoverShape,
    bulletinView, socialView, podcastView, railActive, listTitle, listSubtitle,
    goView, goSource, goTag,
    scope, setAxis, toggleFavoriteScope, activeTagId, activeTagName, interestGroups, hasInterests, refreshInterests,
    interestAxisEnabled, showUnsubscribedMark, showInterestHit,
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
    activeArticle, activeBody, activeBodyLoading, selectArticle, openArticleById, supersedePendingOpen,
    schedulePrefetch, cancelPrefetch,
    activeIndex, prevArticle, nextArticle,
    crumbSource, crumbName, displayBody, displayTranslatedBody, bodyStats,
    // 收藏
    favoriteIds, favTogglingId, handleToggleFavorite,
    // 分享
    shareOpen, setShareOpen,
    // AI(翻译 / 速读)
    showTranslation, translating, translatedBody, translatedTitle, activeIsChinese, handleTranslate,
    activeSummary, summarizing, handleSummarize,
    // 上下文菜单 items(桌面右键在此装配弹层)
    buildArticleMenuItems, buildSourceMenuItems, buildSocialMenuItems,
  } = useReaderState({
    showToast,
    account,
    initialArticleId,
    onDeepLinkConsumed,
    onBeforeOpenArticle: closeBriefBeforeArticleOpen,
    interestAxisEnabled: personalDigestEnabled,
  });

  // 首登引导自动落到发现页兴趣段一次(不锁页;发现钮挂点直到完成或跳过)
  const onboardingOpenedRef = useRef(false);
  useEffect(() => {
    if (!onboardingRequired || onboardingOpenedRef.current) return;
    onboardingOpenedRef.current = true;
    setBriefOpen(false);
    setDiscoverTab('interests');
    openDiscover({ shape: 'all' });
  }, [onboardingRequired, openDiscover]);
  // 「发现更多来源」类入口(源栏底 / 条目列与阅读窗空态):明说的是「来源」,段位必须落「源」——
  // 发现页段位是粘性的(首登引导落过兴趣段后会一直停在那),不切回会让「发现更多来源」开到标签清单;
  // 形态随 openDiscover 缺省取当前容器(issue #55)。视图轨 Compass 是全局入口,沿用上次段位不动。
  const openDiscoverSources = useCallback((opts) => { setDiscoverTab('sources'); openDiscover(opts); }, [openDiscover]);
  // 「兴趣」的编辑入口(源栏开关灰态提示 / 引导):直落发现页兴趣段
  const openInterests = useCallback(() => {
    supersedePendingOpen();
    setBriefOpen(false);
    leaveBriefTrail();
    setDiscoverTab('interests');
    openDiscover({ shape: 'all' });
  }, [supersedePendingOpen, leaveBriefTrail, openDiscover]);
  // 屏蔽折叠行的展开态:按日期组记(切作用域时随列表重挂载归零)
  const [expandedMutedDays, setExpandedMutedDays] = useState(() => new Set());
  const toggleMutedDay = useCallback((dayKey) => {
    setExpandedMutedDays((prev) => {
      const next = new Set(prev);
      if (next.has(dayKey)) next.delete(dayKey); else next.add(dayKey);
      return next;
    });
  }, []);
  useEffect(() => { setExpandedMutedDays(new Set()); }, [activeSourceId, activeTagId, mode, scope, searchQuery]);
  const listPlan = useMemo(
    () => buildListPlan(articles, grouping, expandedMutedDays),
    [articles, grouping, expandedMutedDays],
  );
  // 栏头轴切换:社交容器没有标签不出;「个人早报」能力位关闭时兴趣端点不可用、也不出
  const showAxisSeg = !socialView && interestAxisEnabled;
  // 左栏列源还是列标签:兴趣轴列标签;社交容器 / 预览单源(临时在来源轴上)列源
  const showSourceRows = scope.axis !== 'interest' || socialView || Boolean(activeSourceId);
  const markAllLabel = activeSourceId
    ? '本来源全部标为已读'
    : activeTagId ? '这个兴趣全部标为已读' : scope.axis === 'interest' ? '兴趣全部标为已读' : '本容器全部标为已读';
  // 兴趣页保存回调在 PUT 在途时可能已随发现页卸载,闭包里的 discover 是旧值——经 ref 读最新(codex 检视 P2)
  const discoverRef = useRef(discover);
  useEffect(() => { discoverRef.current = discover; }, [discover]);

  const [podcastSelection, setPodcastSelection] = useState({ articleId: '', variant: 'original' });
  const activePodcast = podcastOf(activeArticle);
  const defaultPodcastVariant = activePodcast?.audio_url
    ? 'original'
    : activePodcast?.condensed_audio_url ? 'digest' : 'original';
  const podcastVariant = podcastSelection.articleId === activeArticle?.id
    ? podcastSelection.variant
    : defaultPodcastVariant;
  const podcastGuideActive = podcastView
    && podcastVariant === 'digest'
    && Boolean(activePodcast?.condensed_audio_url);
  const handlePodcastVariantChange = (variant) => {
    setPodcastSelection({ articleId: activeArticle?.id || '', variant });
  };

  const listThumbRef = useRef(null); // 浮层滚动条滑块(压在卡片上,内容满宽)
  // 文章/动态中栏会被发现页与社交流整段卸载；active 让自绘滚动条在 DOM 重建后
  // 重新绑定到新节点，避免监听器滞留在旧节点、出现“内容滚动但滑块不动”。
  const resyncListScrollbar = useOverlayScrollbar(
    listRef,
    listThumbRef,
    !pageOpen && !discover && mode !== 'social',
  );

  // 列表内容高度变化(切源/追加/加载态)后重算浮层滚动条滑块
  useEffect(() => { resyncListScrollbar(); }, [articles, articlesLoading, activeArticle, resyncListScrollbar]);

  const activeAnalysisStatus = analysisStatusMeta(activeArticle, { podcast: podcastView });
  // 标题区尾行动作(语言二段 + 查看原文):有标签时住尾行右缘,无标签时并入署名行右缘——
  // 标题下不留一片只挂着两个钮的空白(样页「分析在途」帧即此形态)。
  const paneTags = activeArticle ? displayAnalysisTags(activeArticle) : [];
  const paneActions = activeArticle
    && (activeArticle.source_url || activeArticle.is_premium_podcast || (aiEnabled && !activeIsChinese))
    && (
    <div className="reader-pane-actions">
      {aiEnabled && !activeIsChinese && (
        <div className="reader-tr-seg" role="group" aria-label="正文语言">
          <button
            type="button"
            className={`reader-tr-seg-btn ${showTranslation ? '' : 'is-on'}`}
            aria-pressed={!showTranslation}
            onClick={() => { if (showTranslation) handleTranslate(); }}
          >
            原语言
          </button>
          <button
            type="button"
            className={`reader-tr-seg-btn ${showTranslation ? 'is-on is-ai' : ''}`}
            aria-pressed={showTranslation}
            disabled={translating || activeBodyLoading || !activeBody}
            title={showTranslation ? '当前显示中文译文' : '将正文译为中文'}
            onClick={() => { if (!showTranslation) handleTranslate(); }}
          >
            {translating
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              : <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
            <span className={showTranslation ? 'ai-grad-text' : ''}>
              {translating ? '翻译中…' : showTranslation ? '中文' : '译为中文'}
            </span>
          </button>
        </div>
      )}
      {/* 顺序:语言二段在前、跳原网页在后(目检拍板);「原语言」与「查看原文」用词分家——
          前者是本页正文的语言档位,后者是跳出站外 */}
      {activeArticle.source_url && (
        <a
          href={activeArticle.source_url}
          target="_blank"
          rel="noreferrer"
          className="reader-pane-act"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          查看原文
        </a>
      )}
      {activeArticle.is_premium_podcast && (
        <span className="podcast-premium-badge is-action">优质播客</span>
      )}
    </div>
  );

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
  const onRowSubscribeSource = useCallback((sid) => rowHandlersRef.current.subscribeSource(sid), []);
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
      // 条目行「+ 订阅」:按 source_id 取目录里的源对象(目录未含时用最小对象,Toast 名字回退 id)
      subscribeSource: (sid) => handleSubscribe(sourceMap[sid] || { source_id: sid, name: sourceNameMap[sid] || sid }),
    };
  });

  return (
    <div className="reader-shell">
      {/* ── 管理员公告横幅(v3.18):无公告时渲染 null,:has 不命中,四带布局逐像素不变 ── */}
      <AnnouncementBanner />
      {/* ── 视图轨 · 一级视图导航(样页:品牌标 + 图标下常显微标签 + 轨底头像)。
          可发现性波 v3.45:icon-only + 悬停 tooltip 对新用户读不出页面组织(上量后实证),
          改图标下 11px 微标签常显(Slack 式,永久而非「新账号前几次会话展开」——收起时机是
          启发式、收起是一次布局跳变);tooltip 只在全名长于标签时补充。 ── */}
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
              onClick={() => { supersedePendingOpen(); closeDiscover(); setBriefRestore(null); leaveBriefTrail(); setBriefOpen(true); }}
              className={`reader-vrail-btn ${briefOpen ? 'is-on' : ''}`}
            >
              <Newspaper className="h-[18px] w-[18px]" />
              <span className="reader-vrail-label">早报</span>
              <span className="reader-vrail-tip">我的早报</span>
            </button>
            <span className="reader-vrail-divider" aria-hidden="true" />
          </>
        )}
        {/* 四个内容容器:文章 / 播客 / 动态 / 社交媒体。收藏降为容器内过滤器(条目列头星标)。
            社交独立成容器(v3.12):动态装的是 changelog/release notes/GitHub 趋势——短条目扫读形态,
            推文是卡片流直读形态,渲染差异大到要在容器内再分叉,就说明本不该是同一个容器。 */}
        {[
          ['article', '文章', FileText, '文章'],
          ['podcast', '播客', Podcast, '播客'],
          ['bulletin', '动态', Zap, '动态'],
          ['social', '社交媒体', AtSign, '社交'],
        ].map(([view, label, Icon, short]) => (
          <button
            key={view}
            type="button"
            aria-label={label}
            aria-pressed={!pageOpen && railActive === view}
            onClick={() => { setBriefOpen(false); leaveBriefTrail(); goView(view); }}
            className={`reader-vrail-btn ${!pageOpen && railActive === view ? 'is-on' : ''}`}
          >
            <Icon className="h-[18px] w-[18px]" />
            <span className="reader-vrail-label">{short}</span>
            {short !== label && <span className="reader-vrail-tip">{label}</span>}
          </button>
        ))}
        {/* 发现:整页源目录(取代源栏内联「发现更多来源」)。与上方三个内容容器
            语义有别(读内容 vs 找内容),以分隔线分组。 */}
        <span className="reader-vrail-divider" aria-hidden="true" />
        <button
          type="button"
          aria-label={onboardingRequired && !discover ? '发现(兴趣待设置)' : '发现'}
          aria-pressed={!pageOpen && discover}
          onClick={() => { setBriefOpen(false); leaveBriefTrail(); openDiscover(); }}
          className={`reader-vrail-btn ${!pageOpen && discover ? 'is-on' : ''}`}
        >
          <Compass className="h-[18px] w-[18px]" />
          {onboardingRequired && !discover && <span className="vrail-btn-dot" aria-hidden="true" />}
          <span className="reader-vrail-label">发现</span>
        </button>

        {/* 轨底(standalone,可发现性波 v3.45):工具钮常态可见——hover 滑出菜单
            (2026-07-24 拍板)退役,上量后实证「常态只见头像」让新用户找不到反馈/设置,
            且 hover 触发对触控板外接屏/触屏本/键盘用户都是坏的;头像只剩退出(两击防呆)。 */}
        {standalone && (
          <>
            <div className="reader-vrail-spring" />
            {/* 返回管理台(v3.19):与应用导轨轨底「进入阅读器」对称的隐藏切换钮,仅 admin 有 */}
            {onExitReader && (
              <button
                type="button"
                onClick={onExitReader}
                className="reader-vrail-btn"
                aria-label="返回管理台"
              >
                <LayoutDashboard className="h-[18px] w-[18px]" />
                <span className="reader-vrail-label">管理台</span>
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
              <span className="reader-vrail-label">主题</span>
              <span className="reader-vrail-tip">{themeDark ? '切换亮色' : '切换暗色'}</span>
            </button>
            {/* 反馈与建议(仅读者账号;admin 的设置柜没有反馈分区):v3.20 自设置柜二级分区
                提为轨底一级钮,深链直达该分区;新回复的通知点挂钮本体(钮已常态可见)。 */}
            {!onExitReader && (
              <button
                type="button"
                onClick={() => onOpenSettings?.('feedback')}
                className="reader-vrail-btn"
                aria-label={feedbackUnread > 0 ? '反馈与建议(有新回复)' : '反馈与建议'}
              >
                <MessageSquare className="h-[18px] w-[18px]" />
                {feedbackUnread > 0 && <span className="vrail-btn-dot" aria-hidden="true" />}
                <span className="reader-vrail-label">反馈</span>
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
              <span className="reader-vrail-label">设置</span>
            </button>
            <RailLogoutAvatar
              avatar={account?.avatar}
              username={account?.username}
              onLogout={onLogout}
              onLogoutHint={() => showToast('再次点击以退出登录', 'info')}
            />
          </>
        )}
      </nav>

      {/* ── 源栏 · 我的订阅 ── */}
      {!pageOpen && <aside className="reader-col reader-col-sources">
        <div className="reader-sources-inner">
        {/* 栏头 = 容器名 + 轴切换(issue #27 五稿):左栏是一根轴,栏头二选一决定其下列源还是列标签。
            社交容器没有标签(推文不打标),不出轴切换,只列账号。 */}
        <div className={`reader-src-head ${showAxisSeg ? 'is-axis' : ''}`}>
          <span className="reader-src-title">
            {mode === 'bulletin' ? '动态' : socialView ? '社交媒体' : podcastView ? '播客' : '文章'}
          </span>
          {showAxisSeg && (
            <AxisSeg axis={scope.axis} onChange={(axis) => { leaveBriefTrail(); setAxis(axis); }} />
          )}
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

              {/* 兴趣轴:其下列关注的标签(与源行同一形制);点一行 = 收窄到该标签 */}
              {!showSourceRows && (
                <TagRows
                  groups={interestGroups}
                  activeTagId={activeTagId}
                  onPick={(id) => { leaveBriefTrail(); goTag(id); }}
                  hasInterests={hasInterests}
                  onOpenInterests={openInterests}
                />
              )}

              {/* 订阅轴:来源按编辑分层分组(样页):官方·一手信息 / 媒体·观察 / 个人·洞见 / 榜单·动态。
                  源栏跟随容器(层级化):文章容器只列文章形源,动态容器只列榜单·动态。
                  组头=样页 .src-label 细字距灰签。退订钮浮层化:绝对定位悬停现,不占布局。
                  (预览未订源时临时在来源轴上,故也按此列) */}
              {showSourceRows && sidebarGroups.map(({ key, label, list }) => (
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
                        onClick={() => { leaveBriefTrail(); goSource(source.source_id); }}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); leaveBriefTrail(); goSource(source.source_id); } }}
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

              {showSourceRows && hasNoSubscriptions && (
                <p className="reader-side-hint">还没有订阅任何来源，在「发现」页挑选并添加。</p>
              )}

              {/* 「发现更多来源」内联子列表已退役——发现升格为整页视图(视图轨 Compass 钮) */}
              {showSourceRows && !hasNoSubscriptions && (
                <button
                  type="button"
                  onClick={openDiscoverSources}
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

      {/* ── 我的早报(issue #23 重做):日期栏顶替源栏槽位 + 报纸面占条目列/阅读窗整幅;
             点卡片 = openArticleById 跳站内原文(切到所在容器并选中,早报页退场) ── */}
      {briefOpen && (
        <PersonalBriefPage
          showToast={showToast}
          interestVersion={interestVersion}
          sourceMap={sourceMap}
          restore={briefRestore}
          supersedePendingOpen={supersedePendingOpen}
          onManageSubscriptions={() => { setBriefOpen(false); leaveBriefTrail(); openDiscoverSources({ shape: 'all' }); }}
          onOpenArticle={async (articleId, ctx) => {
            // 结果回传早报页:false=不在库(早报页退到原链),null=被更晚的点击盖过(不动)
            const opened = await openArticleById(articleId, { silent: true });
            if (!opened) return opened;
            setBriefOpen(false);
            setBriefReturn(ctx && ctx.sequence?.length ? ctx : null);
            return true;
          }}
        />
      )}


      {/* ── 发现页:占据 条目列+阅读窗 的整片区域(源栏保持在场,订阅结果即时可见) ── */}
      {!pageOpen && discover && (
        <DiscoverPage
          sources={discoverSources}
          subscribedIds={subscribedIds}
          loading={sourcesLoading}
          pinningId={pinningId}
          onSubscribe={handleSubscribe}
          onUnsubscribe={handleUnsubscribe}
          onPreview={(source) => { leaveBriefTrail(); goSource(source.source_id); }}
          collections={collections}
          activeCollectionId={discoverCollectionId}
          onOpenCollection={(c) => setDiscoverCollectionId(c.collection_id)}
          onCloseCollection={() => setDiscoverCollectionId(null)}
          collectionPinningId={collectionPinningId}
          onSubscribeCollection={handleSubscribeCollection}
          onUnsubscribeCollection={handleUnsubscribeCollection}
          userSourcesEnabled={userSourcesEnabled}
          onAddCustomSource={handleAddCustomSource}
          tab={discoverTab}
          onTabChange={setDiscoverTab}
          shape={discoverShape}
          onShapeChange={setDiscoverShape}
          interestsPanel={personalDigestEnabled ? (
            <InterestPage
              embedded
              onboarding={onboardingRequired}
              showToast={showToast}
              onSaved={({ onboardingCompleted } = {}) => {
                setInterestVersion((value) => value + 1);
                refreshInterests();
                if (onboardingCompleted) {
                  onUserUpdated?.({ interest_onboarding_completed: true });
                  // 引导完成即落早报——读者立刻看到兴趣起了作用(在途时若已走开,只记完成)
                  if (!discoverRef.current) return;
                  closeDiscover();
                  setBriefRestore(null);
                  setBriefOpen(true);
                }
              }}
            />
          ) : null}
        />
      )}

      {/* ── 社交媒体流(第三容器):占「条目列 + 阅读窗」整幅,取代四带式 ── */}
      {!pageOpen && !discover && socialView && (
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
          onToggleFavOnly={toggleFavoriteScope}
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
      {!pageOpen && !discover && !socialView && (
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
            <span className="reader-list-title">
              {listTitle}
              {listSubtitle && <span className="reader-list-subtitle">{listSubtitle}</span>}
            </span>
          )}
          {/* 未读筛选(全部/未读)+ 全部标读:搜索展开时让位(三谓词面板下未读对任何组合都成立)。 */}
          {!searchOpen && (
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
              {/* 收藏星(issue #27 五稿):逐篇状态,与未读 seg 同类归同类;开着时琥珀实心 */}
              <button
                type="button"
                onClick={toggleFavoriteScope}
                aria-pressed={favOnly}
                aria-label={favOnly ? '取消只看收藏' : '只看收藏'}
                title={favOnly ? '取消只看收藏' : '只看收藏'}
                className={`reader-unread-icon reader-fav-toggle ${favOnly ? 'is-on' : ''}`}
              >
                <Star className="h-4 w-4" fill={favOnly ? 'currentColor' : 'none'} />
              </button>
              {/* 全部标读两根轴同一枚钮:订阅轴推源水位,兴趣轴按范围逐篇写行(hook 内分流) */}
              <button
                type="button"
                onClick={handleMarkAllRead}
                disabled={markingRead}
                aria-label={markAllLabel}
                title={markAllLabel}
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
          {!favOnly && scope.axis === 'subscribed' && !articlesLoading && freshCount > 0 && (
            <button type="button" className="reader-fresh-pill" onClick={handleRefreshFresh}>
              <RefreshCw className="h-3 w-3" />
              {podcastView ? `载入 ${freshCount} 期新播客` : `载入 ${freshCount} 篇新文章`}
            </button>
          )}
          {articlesLoading ? (
            <ArticleCardsSkeleton />
          ) : scope.axis === 'subscribed' && !favOnly && hasNoSubscriptions && !activeSourceId ? (
            <div className="reader-empty reader-empty-tall">
              <Compass className="h-7 w-7 text-slate-300" />
              <span>你还没有订阅任何来源</span>
              <button type="button" className="action-button action-button-primary" onClick={openDiscoverSources}>
                去发现来源
              </button>
            </div>
          ) : scope.axis === 'interest' && !favOnly && !hasInterests && !activeSourceId ? (
            <div className="reader-empty reader-empty-tall">
              <Tags className="h-7 w-7 text-slate-300" />
              <span>还没有设置兴趣</span>
              <button type="button" className="action-button action-button-primary" onClick={openInterests}>
                去选几个感兴趣的方向
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
                  ? (podcastView ? '没有匹配的播客' : '没有匹配的文章')
                  : favOnly
                    ? '当前范围还没有收藏，阅读时点右上角星标即可收藏'
                    : activeTagId
                      ? '还没有命中这个兴趣的文章'
                    : scope.axis === 'interest'
                      ? '还没有命中兴趣的文章'
                    : unreadOnly
                      ? '没有未读内容，都看完啦'
                      : activeSourceId
                        ? '该来源暂无内容'
                        : (mode === 'bulletin' ? '暂无动态' : mode === 'podcast' ? '暂无播客' : mode === 'article' ? '暂无文章' : '暂无内容')}
              </span>
            </div>
          ) : (
            /* key 按视图范围重挂载,切源/切容器时列表整体淡入(A1) */
            <div key={`${activeSourceId ?? '__all__'}|${activeTagId ?? ''}|${mode}|${scope.axis}${scope.favorite ? '+f' : ''}`} className="reader-list-enter">
              {listPlan.map((entry) => {
                if (entry.type === 'fold') {
                  /* 屏蔽折叠行(issue #27):同一日期组内命中屏蔽标签的条目折成一行——写屏蔽了什么,不写几篇;
                     点开就地摊开(条目降调),再点收回。屏蔽是全局透镜,任何谓词组合都生效。 */
                  return (
                    <MutedFoldRow
                      key={`fold:${entry.dayKey}`}
                      tags={entry.tags}
                      expanded={entry.expanded}
                      showLabel={entry.showLabel}
                      dayKey={entry.dayKey}
                      onToggle={() => toggleMutedDay(entry.dayKey)}
                    />
                  );
                }
                const { article } = entry;
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
                    entryPodcast={podcastView}
                    showLabel={entry.showLabel}
                    dayKey={entry.dayKey}
                    searchQuery={searchQuery}
                    source={sourceMap[article.source_id]}
                    sourceName={sourceNameMap[article.source_id] || article.source_id}
                    onSelect={onRowSelect}
                    onPrefetchEnter={onRowPrefetchEnter}
                    onPrefetchLeave={onRowPrefetchLeave}
                    onToggleFavorite={onRowToggleFavorite}
                    onContextMenu={onRowContextMenu}
                    ctxAnchor={ctxMenu?.anchorKey === `article:${article.id}`}
                    interestHit={showInterestHit ? (article.interest_hits?.[0] || '') : ''}
                    labelSuppress={activeTagName}
                    unsubscribed={showUnsubscribedMark && !subscribedIds.has(article.source_id)}
                    onSubscribeSource={onRowSubscribeSource}
                    muted={entry.muted}
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
      {!pageOpen && !discover && !socialView && (
      <section className="reader-col reader-col-read">
        {/* 早报外出返回带(issue #23 三稿):从早报点进来的这一程里常驻阅读窗顶部,不随文章切换重绘。
            左=返回我的早报(落回同一版同一卷动位置),右=早报下一条(顺着本版读完不必回早报)。 */}
        {briefReturn && (() => {
          const seq = briefReturn.sequence || [];
          const next = seq[briefReturn.index + 1] || null;
          return (
            <nav className="reader-brief-trail" aria-label="来自我的早报">
              <button
                type="button"
                className="reader-brief-trail-btn"
                onClick={() => {
                  supersedePendingOpen(); // 「下一条」尚在途时点返回:作废它,别让迟到的响应又把早报关掉
                  setBriefRestore(briefReturn);
                  setBriefReturn(null);
                  setBriefOpen(true);
                }}
              >
                <ChevronLeft aria-hidden="true" />
                <span>返回我的早报</span>
                <small>{briefReturn.label}</small>
              </button>
              {next ? (
                <button
                  type="button"
                  className="reader-brief-trail-btn is-next"
                  title={next.title || undefined}
                  onClick={async () => {
                    const opened = await openArticleById(next.article_id, { silent: true });
                    if (opened) setBriefReturn((prev) => (prev ? { ...prev, index: prev.index + 1, itemId: next.id } : prev));
                    // 站内取不到(源被隐藏 / 退订)与点卡同款回退:序列带着快照原链;await 之后用户激活可能已过期,当前页跳转
                    else if (opened === false) {
                      if (next.source_url) window.location.assign(next.source_url);
                      else showToast('这条内容已不在库中', 'error');
                    }
                  }}
                >
                  <span>早报下一条</span>
                  <small>{`${briefReturn.index + 2} / ${seq.length}`}</small>
                  <ChevronRight aria-hidden="true" />
                </button>
              ) : (
                <span className="reader-brief-trail-end">已到本版末尾</span>
              )}
            </nav>
          );
        })()}
        {activeArticle ? (
          <>
            {/* 阅读进度线：仅正文非空时显示；CSS scroll() 滚动驱动、切文章天然归零，
                不支持 scroll() 的浏览器由 @supports 直接隐藏（渐进增强，无 JS 兜底）。 */}
            {!activeBodyLoading && activeBody ? (
              <div className="reader-progress" aria-hidden="true" />
            ) : null}

            {/* 顶部工具条:crumb + 读后元动作(收藏/标读/分享;常驻,不随正文滚走)。
                可发现性波 v3.45:「查看原文」与「译为中文」是关于这篇正文本身的动作,
                下沉到标题下方的动作行文字化——右上角一排灰图标曾被新用户当成「没有
                跳原文/翻译功能」;收藏/标读/分享是读完后的元动作,留在常驻条读完顺手点。 */}
            <div className="reader-pane-bar">
              <div className="reader-crumb">
                {crumbSource ? (
                  <LogoMark company={resolveCompany(crumbSource)} size="s17" emoji={crumbSource.icon} />
                ) : null}
                <span className="reader-crumb-name">{crumbName}</span>
              </div>
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
            </div>

          {/* key 按文章 id 重挂载,触发 reader-enter 淡入+轻上移(体验二波 A1) */}
          <article className="reader-pane reader-enter" key={activeArticle.id}>
            {/* 标题区(issue #23 第三项延伸,样页 docs/design/dorami-pane-head-quiet.html):
                四行三语言——眉头「源 · 体裁」/ 衬线标题 / 署名行(只放事实:日期·时长·阅读量·分析状态)/
                尾行「左标签小签 · 右动作」贴着分隔线。内容类型是数据形态(容器已交代),眉头改画分析出的
                体裁,与条目行分类同一件事。 */}
            <header className="reader-pane-head">
              <div className="reader-kicker">
                {(sourceNameMap[activeArticle.source_id] || activeArticle.source_id)}
                {contentGenreLabel(activeArticle.content_genre)
                  ? ` · ${contentGenreLabel(activeArticle.content_genre)}`
                  : ''}
                {/* 全站范围里读到订阅外的文章:眉头挂一枚「+ 订阅」胶囊(源被收窄预览时列头已有横幅,不重复) */}
                {!activeUnsubscribed && !subscribedIds.has(activeArticle.source_id) && !sourceMap[activeArticle.source_id]?.hidden && (
                  <button
                    type="button"
                    className="reader-pane-sub"
                    disabled={pinningId === activeArticle.source_id}
                    onClick={() => onRowSubscribeSource(activeArticle.source_id)}
                  >
                    {pinningId === activeArticle.source_id
                      ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      : <Plus className="h-3 w-3" aria-hidden="true" />}
                    订阅{sourceNameMap[activeArticle.source_id] ? ` ${sourceNameMap[activeArticle.source_id]}` : ''}
                  </button>
                )}
              </div>
              {/* 译文态(v3.45):大标题换中文译名,原标题降为其下一行小字 */}
              <h1 className="reader-pane-title">
                {(showTranslation && translatedTitle) ? translatedTitle : (activeArticle.title || '（无标题）')}
              </h1>
              {showTranslation && translatedTitle && activeArticle.title && translatedTitle !== activeArticle.title && (
                <div className="reader-pane-title-orig">{activeArticle.title}</div>
              )}
              <div className="reader-pane-byline">
              <div className="reader-pane-meta">
                {activeArticle.publish_date && (
                  <span title={formatDateTime(activeArticle.publish_date)}>
                    {formatPublishDate(activeArticle.publish_date)}
                  </span>
                )}
                {/* 字数与时长信息冗余(时长即由字数换算),只留时长;
                    阅读量 = 全站累计阅读次数(跨读者;含本次打开,由 /read 响应回填) */}
                {bodyStats && (
                  <span>{podcastView ? '简介阅读约' : '阅读约'} {bodyStats.minutes} 分钟</span>
                )}
                {podcastView && formatPodcastDuration(activeArticle.podcast?.duration_seconds) && (
                  <span>原节目 {formatPodcastDuration(activeArticle.podcast.duration_seconds)}</span>
                )}
                {typeof activeArticle.read_count === 'number' && activeArticle.read_count > 0 && (
                  <span>阅读量 {activeArticle.read_count.toLocaleString()}</span>
                )}
                {/* 分析生命周期是署名行末尾的一段事实,不是一枚章 */}
                {activeAnalysisStatus && <span role="status">{activeAnalysisStatus.label}</span>}
              </div>
              {paneTags.length === 0 && paneActions}
              </div>
              {/* 尾行:左标签小签(规范实线 / 灵活虚线可点检索),右动作——「原语言 | 译为中文」二段 +
                  查看原文文字链(v3.45 拍板的「标题 → 正文」视线路径不变,只是不再独占一行);
                  译文二段激活态沿 AI 渐变身份(v3.33),AI 未开启只余原文。 */}
              {paneTags.length > 0 && (
                <div className="reader-pane-foot">
                  <div className="reader-pane-tags">
                    {paneTags.map((tag, index) => (
                      <AnalysisTagChip
                        key={`${tag.type || 'canonical'}-${tag.id || tag.code || tag.candidate_id || index}`}
                        tag={tag}
                        onTemporarySearch={searchForLabel}
                      />
                    ))}
                  </div>
                  {paneActions}
                </div>
              )}
            </header>
            <div className="reader-pane-body markdown-body">
              {podcastView && (
                <PodcastExperiencePanel
                  article={activeArticle}
                  variant={podcastVariant}
                  onVariantChange={handlePodcastVariantChange}
                />
              )}
              {/* 已落库分析始终可读；本端 AI 开启时才额外给现场生成入口。 */}
              {!podcastGuideActive && !activeBodyLoading && shouldShowAiReadingCard(activeArticle, {
                summary: activeSummary,
                aiEnabled,
                body: activeBody,
              }) && (
                <AiReadingCard
                  article={activeArticle}
                  summary={activeSummary}
                  summarizing={summarizing}
                  canGenerate={aiEnabled && Boolean(activeBody)}
                  onGenerate={handleSummarize}
                  podcast={podcastView}
                />
              )}
              {podcastView && !podcastGuideActive && !activeBodyLoading && activeBody && (
                <div className="podcast-show-notes-head">
                  <h2 className="section-title">节目简介</h2>
                  <span>来源方提供</span>
                </div>
              )}
              {podcastGuideActive ? null : activeBodyLoading ? (
                <PaneBodySkeleton />
              ) : (showTranslation && translatedBody) ? (
                <ReaderMarkdown>{displayTranslatedBody}</ReaderMarkdown>
              ) : activeBody ? (
                <ReaderMarkdown>{displayBody}</ReaderMarkdown>
              ) : (
                podcastView
                  ? '该播客暂无文字内容，可收听上方原节目音频。'
                  : '该文章暂无正文内容，点击「查看原文」阅读完整内容。'
              )}
              {/* 正文尾部原文行(v3.40 自定源首创,v3.45 推全站):读完想看原文正是最自然的
                  时刻;摘要型源读完即达原文,全文源多一个出口也无碍。无 source_url 不画。 */}
              {!podcastGuideActive && !activeBodyLoading && activeArticle.source_url && (
                <p className="reader-pane-origin">
                  <a href={activeArticle.source_url} target="_blank" rel="noreferrer">
                    查看原文 ↗
                  </a>
                  {hostOf(activeArticle.source_url) && (
                    <span className="reader-pane-origin-host"> · {hostOf(activeArticle.source_url)}</span>
                  )}
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
            <span>{bulletinView ? '选择一条动态以开始阅读' : podcastView ? '选择一期播客以开始收听' : '选择一篇文章以开始阅读'}</span>
            {/* 新老用户通用的轻引导:空态下一行小字直达发现页(欢迎卡方案已否决——太啰嗦) */}
            <button type="button" className="reader-empty-link" onClick={openDiscoverSources}>
              去「发现」添加订阅
            </button>
          </div>
        )}
      </section>
      )}

      {!pageOpen && !discover && (
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
    </div>
  );
}
