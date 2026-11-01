import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  ChartColumn,
  CheckCheck,
  Circle,
  CircleDot,
  Compass,
  Inbox,
  Layers,
  Library,
  Loader2,
  Newspaper,
  Plus,
  RefreshCw,
  Search,
  Share2,
} from 'lucide-react';
import { useLongPress } from '../../hooks/useLongPress';
import { useLayerHistory } from '../../hooks/useLayerHistory';
import { articleDeepLink } from '../../utils/shareLink';
import { displayTitle } from '../../utils/titleDisplay';
import { useTitlePreference } from '../../hooks/useTitlePreference';
import { ArticleRow, ArticleCardsSkeleton } from '../ReaderTab';
import { buildListPlan } from '../../utils/listPlan';
import { dayLabelOf } from '../../utils/readerTime';
import { kindFilterOptions } from '../../utils/contentKind';
import SocialFlow, { SocialPeek } from '../SocialFlow';
import DiscoverPage from '../DiscoverPage';
import AnnouncementBanner from '../AnnouncementBanner';
import MobileArticlePage from './MobileArticlePage';
import MobileSubsPage from './MobileSubsPage';
import MobileSubsFlow from './MobileSubsFlow';
import EntryPeek from './EntryPeek';
import { pushSpecKey } from '../../utils/articleList';
import { GlassBtn, HeadAvatar, HeadBack, HeadTitle, MobileHeader, TitleDial } from './MobileHeader';
import { requestLogin } from '../../utils/loginPrompt';
import { SOURCE_ROLES, sourceRoleOf } from '../../sourceTaxonomy';
import { subscriptionGroups } from '../../utils/subsGroups';
import { CHROME, localStore, markSettingsHintSeen, nextChrome, settingsHintPending } from '../../utils/mobileChrome';
import { motionReduced } from '../../motion';
import ActionSheet from './ActionSheet';
import LayerPage from '../layer/LayerPage';
import { rebuildToast } from '../../utils/briefRebuild';
import { briefMenuItems } from '../../utils/briefMenu';
import PersonalBriefPage from '../PersonalBriefPage';
import InterestPage from '../InterestPage';
import RankingsPage from '../RankingsPage';
import { usePlayer } from '../player/playerContext';
import PlayerDock from '../player/PlayerDock';
import { nowPlayingFor } from '../../utils/playerCore';
import { installThemeColorSync } from '../../utils/themeColor';
import { EMPTY_ACTION_LABELS, emptyActionKind } from '../../utils/emptyAction';

// 静态 noop:ArticleRow 的 onContextMenu 契约位——移动端 contextmenu 由外层
// .m-press 包装统一接管(useLongPress 的 onContextMenu 覆盖 Android 长按/桌面右键),
// 行内回调不再各自拦截。
const noopContextMenu = () => {};

// 页头标题位转盘的三组项(拍板 31 B):内容 = 三个容器;订阅 = 订阅 / 兴趣;榜单 = 文章榜 / 播客榜
const VIEW_ITEMS = [['article', '文章'], ['podcast', '播客'], ['social', '社交']];
const SUBS_ITEMS = [['subs', '订阅'], ['interest', '兴趣']];
const RANK_ITEMS = [['article', '文章榜'], ['podcast', '播客榜']];
// 镀层随滚动看的滚动区:内容列表 / 社交流 / 早报 / 榜单 / 订阅
const CHROME_SCROLLERS = '.m-list, .reader-social-scroll, .bp-sheet, .rankings-page, .m-subs-scroll';
// 内容 Tab 的条目行(列表 / 社交流)与「页头下沿」:离开时按页头下第一条记位置
const CONTENT_ROWS = '.m-list > div > .m-press, .reader-social-col > article';
const CONTENT_ANCHOR_LINE = 60;

/**
 * 移动壳(移动波 Wave2 起;issue #230 夜航样页 docs/design/issue-230-mobile/ 第 2 批改壳与页头)。
 *
 * 桌面四带布局不下放——移动端是「页面栈 + 底部 TabBar」的原生形态翻译:
 * 底栏 = 功能模块 早报 · 内容 · 订阅 · 榜单(拍板 16);内容 Tab 的三个容器(文章 / 播客 / 社交)在页头标题位转盘里切,
 * 四容器模型原样保留;每个 Tab 只有一行页头(拍板 31 B:头像 · 标题位 · 动作),页头与底栏是浮在内容上的镀层,
 * 滚过 4px 页头换磨砂底、往下读时底栏退场(样页 10 / 11);设置从页头头像推入(拍板 27,「我的」并进设置页)。
 * 订阅 Tab 列出全部订阅与兴趣(拍板 19 / 25),点来源 / 全部 / 收藏 / 兴趣推入它自己的条目流(批 3a:MobileSubsFlow,
 * 第二个列表实例;内容 Tab 只放订阅聚合,不再挂来源 / 收藏筛选);正文页 = push 全屏页;
 * 桌面右键菜单 → 长按底部动作单(items 与 useReaderState 三份构建器同源,跨端肌肉记忆)。
 * ReaderWorkspace 持有共享数据与阅读位置，本文件只写移动 JSX 与触屏交互。
 */
export default function MobileReader({
  showToast,
  aiEnabled = false,
  userSourcesEnabled = false,
  personalDigestEnabled = false,
  ondemand = {},   // 点播能力位(issue #137):{podcast, article}
  account = null,
  onUserUpdated,
  onOpenSettings,
  // 游客(issue #213):订阅 Tab 的登录卡 / 分享菜单的站内链接说明
  onGuestLogin = null,
  onVisibleArticleChange = null,
  guestAccessEnabled = false,
  rs,
  view,
}) {
  const {
    tab, setTab, briefReturn, setBriefReturn, briefRestore, setBriefRestore, briefPlace,
    discoverTab, setDiscoverTab, interestVersion, setInterestVersion, playerHostRef, openPlayingItem,
  } = view;
  // 全局播放条(issue #199):列表行「播放中 / 已暂停」
  const { state: player } = usePlayer();
  const playingId = player.form !== 'none' ? (player.item?.articleId || '') : '';
  const playingState = nowPlayingFor(player, playingId);
  const playingAi = Boolean(player.item?.ai);
  const { preferChinese } = useTitlePreference();
  const [sheet, setSheet] = useState(null); // { title, items, anchorKey, preview }
  // 关掉之后底页还要播一小段退场(Sheet 的 is-leaving,批 4a):退场期间照旧画上一份内容,不画成空菜单
  const [lastSheet, setLastSheet] = useState(null);
  if (sheet && sheet !== lastSheet) setLastSheet(sheet);
  const shownSheet = sheet || lastSheet;
  // 底栏四个 Tab(拍板 16):早报 / 订阅 / 榜单是页,内容 = 当前容器(tab 即 rs.mode)
  const mTab = tab === 'brief' || tab === 'subs' || tab === 'rankings' ? tab : 'content';
  const contentTab = mTab === 'content';
  const [subsSeg, setSubsSeg] = useState('subs');   // 订阅 Tab 页头转盘:订阅 / 兴趣
  // 订阅 Tab 的推入页(issue #230 批 3a):{ kind: source | all | fav | interest | tag, id?, name?, facet?, count? };
  // 页内是独立的列表实例(MobileSubsFlow),不动内容 Tab
  const [flow, setFlow] = useState(null);
  const {
    // 源目录 / 订阅
    sourcesLoading, discoverSources, subscribedIds, sourceMap, sourceNameMap, shapeOfSource,
    hasNoSubscriptions, socialSources, platformCount, pinningId,
    handleSubscribe, handleUnsubscribe, handleAddCustomSource,
    collections, discoverCollectionId, setDiscoverCollectionId,
    collectionPinningId, handleSubscribeCollection, handleUnsubscribeCollection,
    shapePinning, handleSubscribeShape,
    // 视图 / 导航
    mode, activeSourceId, discover, openDiscover, closeDiscover, discoverShape, setDiscoverShape,
    socialView, podcastView,
    goView, goContainerAll, alignModeToArticle,
    kindFilter, setKindFilter, kindFacetItems,
    scope, activeTagId, refreshInterests,
    grouping,
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
    activeArticle, selectArticle, openArticleById, supersedePendingOpen, schedulePrefetch, cancelPrefetch,
    // 收藏
    favoriteIds, favTogglingId, handleToggleFavorite,
    // 推文翻译(issue #84):逐卡状态与开关,与桌面同源
    socialTranslations, toggleSocialTranslation,
    // 动作单 items(桌面右键三份构建器同源)
    buildArticleMenuItems, buildSourceMenuItems, buildSocialMenuItems,
    // 订阅数(设置页资料卡 / 订阅 Tab「全部」)
    subscribedSources,
  } = rs;
  const [rankShape, setRankShape] = useState(() => (mode === 'podcast' ? 'podcast' : 'article'));   // 榜单 Tab 页头转盘
  // 按 id 打开一篇(早报卡 / 榜单 / 播放条 / 深链)会把主列表临时收窄到那篇的来源(useReaderState.openArticleById);
  // 手机内容 Tab 没有来源筛选(收窄都在订阅 Tab 的推入页,issue #230 批 3a),离开正文时把这层收窄撤掉,
  // 回到那个容器的订阅聚合——否则内容 Tab 会悄悄只剩一个来源、又没有任何地方写着
  const residualNarrowing = Boolean(activeSourceId || activeTagId);
  const closeArticle = useCallback(() => {
    supersedePendingOpen(); // 返回时作废在途的按 id 打开(早报卡 / 深链),迟到响应不再把页面拉回去
    if (briefReturn) {
      setBriefRestore(briefReturn);
      setBriefReturn(null);
      setTab('brief');
    }
    selectArticle(null);
    if (residualNarrowing) goContainerAll();
  }, [selectArticle, supersedePendingOpen, briefReturn, setBriefRestore, setBriefReturn, setTab, residualNarrowing, goContainerAll]);
  // 正文页点标签检索(codex 检视 P2):目的地是过滤后的内容列表,不是早报——从早报进来的也丢掉
  // 返回带、落到所属容器 Tab,否则检索结果被早报页盖住看不见。
  // 推入页里读的那篇形态可能与内容 Tab 当前视图不同:检索落在它所属的容器(codex 检视第 1 轮 P1-2;检索条件不动)
  const leaveArticleForSearch = useCallback(() => {
    setBriefReturn(null);
    setFlow(null);   // 从推入页读进来的:检索结果在内容 Tab,推入页一并出栈
    setTab('content');
    alignModeToArticle(activeArticle);
    selectArticle(null);
    if (residualNarrowing) goContainerAll();   // 只撤收窄,不动刚设下的检索
  }, [selectArticle, setBriefReturn, setTab, residualNarrowing, goContainerAll, alignModeToArticle, activeArticle]);

  const onboardingRequired = personalDigestEnabled
    && account?.role === 'user'
    && account?.interest_onboarding_completed === false;
  // 首登引导(issue #56 方案 B):不再强制落发现页兴趣段,横幅挂在早报页顶;「我的」的兴趣入口红点照旧
  // issue #230:兴趣的家在订阅 Tab 的「兴趣」段(早报「调整兴趣」、首登引导「设置兴趣」都落这里)
  const openInterests = useCallback(() => {
    // 游客(issue #213)不能设兴趣:弹登录提示,不进兴趣页
    if (rs.isGuest) { requestLogin('interest'); return; }
    supersedePendingOpen();
    setBriefReturn(null);
    setSubsSeg('interest');
    setTab('subs');
  }, [rs.isGuest, supersedePendingOpen, setBriefReturn, setTab]);
  // 「发现更多来源」入口落「源」段(段位粘性,见桌面 ReaderTab 同名函数注释);形态:抽屉=当前容器,「我的」=全部
  const openDiscoverSources = useCallback((opts) => { setDiscoverTab('sources'); openDiscover(opts); }, [openDiscover, setDiscoverTab]);
  // 兴趣页保存回调经 ref 读最新的 discover(PUT 在途时读者可能已走开,闭包值陈旧——codex 检视 P2)
  const discoverRef = useRef(discover);
  useEffect(() => { discoverRef.current = discover; }, [discover]);
  const listPlan = useMemo(
    () => buildListPlan(articles, grouping),
    [articles, grouping],
  );
  const kindOptions = useMemo(
    () => (mode === 'article' ? kindFilterOptions(kindFacetItems, kindFilter) : []),
    [mode, kindFacetItems, kindFilter],
  );
  const subscribeSourceById = (sid) => handleSubscribe(sourceMap[sid] || { source_id: sid, name: sourceNameMap[sid] || sid });

  const goTab = (t) => {
    if (contentTab && t !== 'content') saveContentPlace();
    supersedePendingOpen(); // 任何主动切 Tab 都作废在途的按 id 打开,迟到响应不再把人拉走
    setBriefReturn(null); // 主动切 Tab = 结束这一程外出
    if (t === 'brief') setBriefRestore(null); // 点 Tab 进早报是新开,不落回旧位
    // 已在内容 Tab 再点「内容」= 回到当前视图的聚合(清来源 / 标签 / 搜索,与桌面视图轨同语义);从别的 Tab 回来保留原状
    if (t === 'content' && contentTab) goView(mode);
    setTab(t);
  };

  // ── 订阅 Tab:点来源 / 全部 / 收藏 / 兴趣 = 推入它自己的条目流(第二个列表实例,不动内容 Tab;拍板 19 / 24)──
  const subsGroups = useMemo(
    () => subscriptionGroups(subscribedSources, { shapeOf: shapeOfSource, roleOf: sourceRoleOf, roles: SOURCE_ROLES }),
    [subscribedSources, shapeOfSource],
  );
  const openFlow = useCallback((spec) => {
    supersedePendingOpen();
    setFlow(spec);
  }, [supersedePendingOpen]);
  const closeFlow = useCallback(() => setFlow(null), []);
  // 订阅 Tab 的「+」与「发现更多来源」:游客改订阅要登录(拍板 21),不进发现页
  const discoverFor = (shape) => (rs.isGuest ? requestLogin('subscribeAll') : openDiscoverSources({ shape }));
  const discoverFromSubs = () => discoverFor('all');
  // 发现页滚过 4px 页头换镀层底(与设置页 / 推入页同一规则);关掉即复位
  const [discScrolled, setDiscScrolled] = useState(false);
  useEffect(() => { if (!discover) setDiscScrolled(false); }, [discover]);
  const subsActions = {
    onAll: () => openFlow({ kind: 'all' }),
    onFavorites: () => {
      if (rs.isGuest) { requestLogin('favlist'); return; }
      openFlow({ kind: 'fav' });
    },
    onSource: (sid) => openFlow({ kind: 'source', id: sid }),
  };
  // 兴趣段「我的兴趣」:每行读它的条目流,「全部兴趣内容」读全站兴趣命中(InterestPage 的手机嵌入档传上来)
  const readInterestTag = (tag) => openFlow({ kind: 'tag', id: tag.id, name: tag.name_zh, facet: tag.kind });
  const readInterestAll = (count) => openFlow({ kind: 'interest', count });
  const tabRef = useRef(mTab);
  useEffect(() => { tabRef.current = mTab; }, [mTab]);
  const onSubsInterestSaved = ({ onboardingCompleted, briefRebuildStatus } = {}) => {
    setInterestVersion((value) => value + 1);
    refreshInterests();
    if (!onboardingCompleted) return;
    onUserUpdated?.({ interest_onboarding_completed: true });
    const toast = rebuildToast(briefRebuildStatus);
    if (toast) showToast?.(toast.text, toast.kind);
    if (tabRef.current !== 'subs') return;   // 保存在途时读者已走开:不把人拉回早报
    setBriefRestore(null);
    setTab('brief');
  };

  // ── 头像 → 设置页(拍板 27):资料卡的订阅数随打开带过去;老读者迁移提示(拍板 30)在头像下提示一次「设置在这里」 ──
  const [settingsHint, setSettingsHint] = useState(() => account?.role !== 'guest' && settingsHintPending(localStore()));
  const dismissSettingsHint = useCallback(() => {
    setSettingsHint(false);
    markSettingsHintSeen(localStore());
  }, []);
  useEffect(() => {
    if (!settingsHint) return undefined;
    // 点任意处即消失(捕获阶段,先于头像的点击)
    document.addEventListener('pointerdown', dismissSettingsHint, true);
    return () => document.removeEventListener('pointerdown', dismissSettingsHint, true);
  }, [settingsHint, dismissSettingsHint]);
  const openSettings = () => {
    if (settingsHint) dismissSettingsHint();
    onOpenSettings?.('account', { subscribedCount: subscribedSources.length });
  };
  const avatar = <HeadAvatar account={account} onClick={openSettings} />;

  // 长按预览卡(批 4a,样页拍板 5 / 22):被按的那一条原样抬起来(EntryPeek = 同一个 ArticleRow,文章 / 播客两种版式)
  const articlePeek = (article) => (
    <EntryPeek
      article={article}
      podcast={shapeOfSource(article.source_id) === 'podcast'}
      source={sourceMap[article.source_id]}
      sourceName={sourceNameMap[article.source_id] || article.source_id}
      isUnread={isArticleUnread(article)}
      isFav={favoriteIds.has(article.id)}
    />
  );
  const openArticleSheet = (article, { preview } = {}) => {
    const items = buildArticleMenuItems(article);
    // 系统分享(Wave3):支持 Web Share 的环境(手机浏览器/微信内)在链接组补一项
    // 「分享给…」——调起系统分享面板,载荷即站内深链;不支持则不画(呈现的诚实)。
    if (typeof navigator !== 'undefined' && navigator.share) {
      const openIdx = items.findIndex((it) => it.key === 'open');
      items.splice(openIdx < 0 ? items.length : openIdx, 0, {
        key: 'sysshare',
        label: '分享给…',
        icon: Share2,
        onClick: () => {
          navigator.share({ title: article.title || '哆啦美', url: articleDeepLink(article.id) }).catch(() => {});
        },
      });
    }
    // 动作单标题随「默认显示中文标题」(issue #218);系统分享面板仍带原题(对外内容,分享页也只有原题)
    setSheet({
      title: displayTitle(article, preferChinese),
      items,
      anchorKey: `article:${article.id}`,
      preview: preview === undefined ? articlePeek(article) : preview,
    });
  };
  // 早报卡长按(issue #230 批 3b,拍板 13 / 5):同一张文章动作单;早报快照不带读态,读态一项不给;上方是抬起的预览卡
  const openBriefSheet = (article, { anchorKey, preview }) => {
    openArticleSheet(article, { preview });
    setSheet((current) => current && { ...current, items: briefMenuItems(current.items), anchorKey });
  };
  const openSourceSheet = (source) => setSheet({
    title: source.name || source.source_id,
    items: buildSourceMenuItems(source),
    anchorKey: `source:${source.source_id}`,
  });
  const openSocialSheet = (article) => setSheet({
    title: article.title || '推文',
    items: buildSocialMenuItems(article),
    anchorKey: `social:${article.id}`,
    preview: <SocialPeek article={article} source={sourceMap[article.source_id]} sourceName={sourceNameMap[article.source_id]} />,
  });

  const pressBind = useLongPress(openArticleSheet);
  // 社交卡长按(审计 M-04;批 4a):与文章行同一条路径——iOS 不派发 contextmenu,靠触摸计时
  const socialPressBind = useLongPress(openSocialSheet);

  const listView = contentTab && !socialView;
  // 正文页(push 全屏):文章/动态容器里选中了一篇即入栈;社交流直读不进正文页。
  // 订阅 Tab 的推入页里点开的一篇同样入栈(叠在推入页之上,返回后推入页原位还在);
  // 推入页随版式切换卸载后,订阅 Tab 上仍选中的那篇照样显示,不留看不见的阅读态
  const readFromFlow = Boolean(flow) || mTab === 'subs';
  const readOpen = !discover && Boolean(activeArticle) && (listView || readFromFlow);
  // 推入页里读的那篇不在主列表里:正文页按这篇自己的形态画(播客 / 文章,不看内容 Tab 当前视图),不画上一篇 / 下一篇
  const readRs = readOpen && readFromFlow
    ? {
      ...rs,
      podcastView: shapeOfSource(activeArticle.source_id) === 'podcast',
      activeIndex: -1,
      prevArticle: null,
      nextArticle: null,
    }
    : rs;
  // 游客(issue #213 检视 F3):向 App 上报眼前正在看的那篇——登录后回到它;正文页没开就是 null(落早报)
  const visibleArticleId = readOpen ? activeArticle.id : null;
  useEffect(() => { onVisibleArticleChange?.(visibleArticleId); }, [visibleArticleId, onVisibleArticleChange]);

  // 播放条的底边:列表页在底部 Tab 栏之上;正文页 / 发现页(整页层盖住 Tab 栏)在安全区之上
  const playerPage = readOpen || discover || flow ? 'page' : 'tabs';

  // ── 返回键握手(Wave3;issue #230 起正文页 / 发现页 / 抽屉 / 动作单都走层原语 components/layer,
  //    各自在打开时入层栈:系统返回先关最上层,不退出站点)。合集详情是发现页里的子页,仍单独登记一层:
  //    发现页先开、详情后开,返回先退详情、再退发现页。──
  useLayerHistory(Boolean(discoverCollectionId), () => setDiscoverCollectionId(null));
  // 页头就地搜索(审计 M-05):展开着按返回 = 收起搜索(同「取消」,清空输入),不退出站点;
  // 只在内容 Tab 登记,切去早报 / 订阅 / 榜单即消费掉这一步,回来再登记。
  useLayerHistory(searchOpen && contentTab, () => { if (searchOpen) toggleSearch(); });

  // ── 镀层随滚动(样页 10 / 11):滚过 4px 页头换磨砂底(data-scrolled);越过 140px 往下读底栏退场、往上一划回来(data-receded)。
  //    直接写壳上的属性,不为每次滚动重渲染;减少动效时底栏常显 ──
  const shellRef = useRef(null);
  const chromeRef = useRef({ el: null, y: 0, scrolled: false, receded: false });
  // 读者自己滚的才让底栏退场:记最近一次滚轮 / 触摸 / 按键 / 按下(拖滚动条)的时刻;回到列表时的位置恢复不算
  const inputAtRef = useRef(-Infinity);
  const markScrollIntent = useCallback(() => { inputAtRef.current = performance.now(); }, []);
  const applyChrome = useCallback((next) => {
    const state = chromeRef.current;
    const shell = shellRef.current;
    if (shell && next.scrolled !== state.scrolled) shell.toggleAttribute('data-scrolled', next.scrolled);
    if (shell && next.receded !== state.receded) shell.toggleAttribute('data-receded', next.receded);
    state.scrolled = next.scrolled;
    state.receded = next.receded;
  }, []);
  // ── 公告带(审计 M-11,方案 §8「占位式」;批 4a):贴在页头下沿,Tab 根页的页头让位加上它的高度(--m-ann-h,内容从横幅
  //    下面开始、不被盖住);随当前滚动区一起往上走(直接写 transform,不为滚动重渲染),滚过去就藏到页头镀层底下。
  //    整页层(正文 / 发现 / 推入页)开着时照旧让开(mobile.css)。上滑横幅收起、关闭钮不再提示,见 AnnouncementBanner ──
  const followAnn = useCallback(() => {
    const band = shellRef.current?.querySelector(':scope > .reader-ann-band');
    if (!band) return;
    const y = chromeRef.current.el ? chromeRef.current.el.scrollTop : 0;
    band.style.transform = y > 0 ? `translateY(${-Math.round(y)}px)` : '';
  }, []);
  const setAnnHeight = useCallback((height) => {
    const shell = shellRef.current;
    if (!shell) return;
    if (height > 0) shell.style.setProperty('--m-ann-h', `${Math.ceil(height) + 8}px`);
    else shell.style.removeProperty('--m-ann-h');
    followAnn();
  }, [followAnn]);
  const onContentScroll = useCallback((event) => {
    const el = event.target;
    if (!(el instanceof Element) || !el.matches(CHROME_SCROLLERS)) return;
    const state = chromeRef.current;
    const y = el.scrollTop;
    const dy = state.el === el ? y - state.y : 0;
    state.el = el;
    state.y = y;
    const intent = performance.now() - inputAtRef.current < 1000;
    applyChrome(nextChrome(state, { y, dy, reduced: motionReduced(), intent }));
    followAnn();
  }, [applyChrome, followAnn]);
  // ── 内容 Tab 的列表位置(批 3a):切去早报 / 订阅 / 榜单时列表不渲染,回来按同一份查询落回原处——推入页与内容 Tab 各记各的。
  //    离开时记下页头下沿第一条的序号与离滚动区顶的距离;回来先落 scrollTop,再按那一条对齐。刚挂载的几帧里版面还有几像素的微动,
  //    浏览器的滚动锚定会跟着把位置挪开约 10px:对齐窗口内先关掉锚定,版面落定后再对齐一次、还原;要在下面的镀层重算之前 ──
  const contentListKey = JSON.stringify([mode, activeSourceId, activeTagId, scope, searchQuery, unreadOnly, kindFilter]);
  const contentScrollRef = useRef(null);
  const contentScroller = () => shellRef.current?.querySelector('.m-content :is(.m-list, .reader-social-scroll)') || null;
  const saveContentPlace = () => {
    const el = contentScroller();
    if (!el) { contentScrollRef.current = null; return; }
    const top = el.getBoundingClientRect().top;
    const rows = el.querySelectorAll(CONTENT_ROWS);
    let anchor = null;
    for (let i = 0; i < rows.length; i += 1) {
      const box = rows[i].getBoundingClientRect();
      if (box.bottom > top + CONTENT_ANCHOR_LINE) { anchor = { i, offset: box.top - top }; break; }
    }
    contentScrollRef.current = { key: contentListKey, top: el.scrollTop, anchor };
  };
  const lastTabRef = useRef(mTab);
  useLayoutEffect(() => {
    const entered = mTab === 'content' && lastTabRef.current !== 'content';
    lastTabRef.current = mTab;
    const saved = contentScrollRef.current;
    if (!entered || articlesLoading || !saved?.top || saved.key !== contentListKey) return undefined;
    const el = contentScroller();
    if (!el) return undefined;
    const align = () => {
      const row = saved.anchor && el.querySelectorAll(CONTENT_ROWS)[saved.anchor.i];
      if (!row) return;
      el.scrollTop += (row.getBoundingClientRect().top - el.getBoundingClientRect().top) - saved.anchor.offset;
    };
    el.style.overflowAnchor = 'none';
    el.scrollTop = saved.top;
    align();
    const frame = requestAnimationFrame(align);
    const settle = window.setTimeout(() => { align(); el.style.overflowAnchor = ''; }, 240);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
      el.style.overflowAnchor = '';
    };
  }, [mTab, contentListKey, articlesLoading]);
  // 切 Tab / 视图 / 订阅段 / 搜索:底栏叫回来,页头镀层按新滚动区的位置重算
  useLayoutEffect(() => {
    const el = shellRef.current?.querySelector(`.m-content :is(${CHROME_SCROLLERS})`) || null;
    chromeRef.current.el = el;
    chromeRef.current.y = el ? el.scrollTop : 0;
    applyChrome({ scrolled: (el?.scrollTop || 0) > CHROME.scrolledAt, receded: false });
    followAnn();
  }, [mTab, mode, subsSeg, searchOpen, applyChrome, followAnn]);
  // 浏览器地址栏 / PWA 状态栏的颜色跟手机壳底色(审计 M-32;utils/themeColor):壳挂着时盯主题与中性色,卸下即还原
  useEffect(() => installThemeColorSync(), []);

  // ── 空态指向第一个动作(审计 M-41;conventions §1,批 4a):搜索无果 → 清除搜索;只看未读 → 显示全部;
  //    按类型筛 → 显示全部类型;这个视图还没有内容 → 去发现页找来源(游客点了提示登录)──
  const emptyActionOf = ({ noSources = false, social = false } = {}) => {
    const kind = emptyActionKind({ searchQuery, unreadOnly, kindFilter: social ? '' : kindFilter, noSources });
    const run = {
      clearSearch: () => setSearchInput(''),
      showAll: () => setUnreadOnly(false),
      allKinds: () => setKindFilter(''),
    }[kind] || (() => discoverFor(mode));
    return { label: EMPTY_ACTION_LABELS[kind], onClick: run };
  };
  const listEmptyAction = emptyActionOf();

  // ── 内容 Tab 列表顶一行(拍板 31 第 9 条):只看未读开着时「◉ 只看未读」(页头一像素不动)。
  //    批 2 过渡形态里这一行还列来源 / 收藏 / 兴趣筛选胶囊;批 3a 起这些收窄都在订阅 Tab 的推入页,内容 Tab 只剩订阅聚合 ──
  const scopeRow = unreadOnly ? (
    <div className="m-scope" role="group" aria-label="当前筛选">
      <span className="m-only"><CircleDot aria-hidden="true" />只看未读</span>
    </div>
  ) : null;

  // 内容 Tab 页头:头像 · 文章 / 播客 / 社交(标题位转盘)· 只看未读 / 全部标读 / 搜索;搜索态整行换成搜索框 +「取消」
  const contentHead = searchOpen ? (
    <>
      <div className="m-veil is-on" aria-hidden="true" />
      <div className="m-head m-head-search">
        <label className="m-search-field">
          <Search aria-hidden="true" />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="搜索我的阅读…"
            aria-label="搜索我的阅读"
            enterKeyHint="search"
            autoFocus
          />
        </label>
        <button type="button" className="m-textbtn" data-search-cancel="" onClick={toggleSearch}>取消</button>
      </div>
    </>
  ) : (
    <MobileHeader
      lead={avatar}
      title={<TitleDial items={VIEW_ITEMS} value={mode} onChange={goView} label="内容视图" actions={3} />}
      actions={(
        <>
          <GlassBtn
            className={`m-unread ${unreadOnly ? 'is-on' : ''}`}
            label="只看未读"
            pressed={unreadOnly}
            onClick={() => setUnreadOnly((value) => !value)}
          >
            {unreadOnly ? <CircleDot aria-hidden="true" /> : <Circle aria-hidden="true" />}
          </GlassBtn>
          {/* 全部标读两根轴同一枚钮(hook 内按轴分流:推水位 / 逐篇写行);
              按内容类型筛选时收起——推水位会连同没显示的其它类型一并标掉 */}
          {!kindFilter && (
            <GlassBtn
              label="本视图全部标为已读"
              onClick={handleMarkAllRead}
              disabled={markingRead}
            >
              {markingRead ? <Loader2 className="animate-spin" aria-hidden="true" /> : <CheckCheck aria-hidden="true" />}
            </GlassBtn>
          )}
          <GlassBtn label="搜索" onClick={toggleSearch}><Search aria-hidden="true" /></GlassBtn>
        </>
      )}
    />
  );
  const tabs = [
    ...(personalDigestEnabled ? [['brief', '早报', Newspaper]] : []),
    ['content', '内容', Layers],
    ['subs', '订阅', Library],
    ['rankings', '榜单', ChartColumn],
  ];
  const tabIndex = Math.max(0, tabs.findIndex(([id]) => id === mTab));

  return (
    <div className="m-shell font-sans" ref={shellRef} data-tab={mTab}>
      <AnnouncementBanner mobile onHeight={setAnnHeight} />

      {/* 老读者迁移提示(拍板 30):升级后第一次进来,在头像下提示一次;点任意处或进一次设置即消失 */}
      {settingsHint && !readOpen && !discover && !flow && !(contentTab && searchOpen) && (
        <div className="m-hint" role="status">设置在这里</div>
      )}

      {/* ── 内容区:每个 Tab 一行页头(浮在内容上)+ 自己的滚动区 ── */}
      <div
        className="m-content"
        onScrollCapture={onContentScroll}
        onWheelCapture={markScrollIntent}
        onTouchMoveCapture={markScrollIntent}
        onKeyDownCapture={markScrollIntent}
        onPointerDownCapture={markScrollIntent}
      >
        {mTab === 'brief' ? (
          <PersonalBriefPage
            mobile
            mobileLead={avatar}
            sourceMap={sourceMap}
            showToast={showToast}
            interestVersion={interestVersion}
            restore={briefRestore}
            placeKeeper={briefPlace}
            supersedePendingOpen={supersedePendingOpen}
            onManageSubscriptions={() => openDiscoverSources({ shape: 'all' })}
            onManageInterests={openInterests}
            onboarding={onboardingRequired}
            onSetupInterests={openInterests}
            onOnboardingCompleted={() => onUserUpdated?.({ interest_onboarding_completed: true })}
            onBrowse={() => setTab(mode)}
            favoriteIds={favoriteIds}
            onToggleFavorite={(articleId) => handleToggleFavorite({ id: articleId })}
            onItemMenu={openBriefSheet}
            menuAnchorKey={sheet?.anchorKey || null}
            onSubscribeSource={subscribeSourceById}
            guest={rs.isGuest}
            onGuestLogin={() => onGuestLogin?.()}
            onOpenArticle={async (articleId, ctx) => {
              const opened = await openArticleById(articleId, { silent: true });
              if (!opened) return opened;
              setBriefReturn(ctx || null);
              setTab(mode);
              return true;
            }}
          />
        ) : mTab === 'rankings' ? (
          <>
            <MobileHeader
              lead={avatar}
              title={<TitleDial items={RANK_ITEMS} value={rankShape} onChange={setRankShape} label="榜单类型" />}
            />
            <RankingsPage
              mobile
              sourceMap={sourceMap}
              shape={rankShape}
              onShapeChange={setRankShape}
              onOpenArticle={async (articleId) => {
                const opened = await openArticleById(articleId, { silent: true });
                if (opened) setTab(mode);
              }}
            />
          </>
        ) : mTab === 'subs' ? (
          <>
            <MobileHeader
              lead={avatar}
              title={personalDigestEnabled
                ? <TitleDial items={SUBS_ITEMS} value={subsSeg} onChange={setSubsSeg} label="订阅与兴趣" actions={1} />
                : <HeadTitle>订阅</HeadTitle>}
              actions={(
                <GlassBtn label="发现更多来源" onClick={discoverFromSubs}>
                  <Plus aria-hidden="true" />
                </GlassBtn>
              )}
            />
            <MobileSubsPage
              seg={personalDigestEnabled ? subsSeg : 'subs'}
              guest={rs.isGuest}
              onGuestLogin={() => onGuestLogin?.()}
              sourcesLoading={sourcesLoading}
              groups={subsGroups}
              unreadBySource={unreadBySource}
              subscribedCount={subscribedSources.length}
              favoriteCount={favoriteIds.size}
              hasNoSubscriptions={hasNoSubscriptions}
              sheetAnchorKey={sheet?.anchorKey || null}
              onSourcePress={openSourceSheet}
              onDiscover={discoverFromSubs}
              interestPanel={personalDigestEnabled && !rs.isGuest ? (
                <InterestPage
                  mobile
                  embedded
                  onboarding={onboardingRequired}
                  showToast={showToast}
                  onSaved={onSubsInterestSaved}
                  onReadTag={readInterestTag}
                  onReadAll={readInterestAll}
                />
              ) : null}
              {...subsActions}
            />
          </>
        ) : (
          <>
          {/* 页头放在社交 / 列表两种视图之外的同一位置:切视图时转盘不重挂,方向键的焦点不丢 */}
          {contentHead}
          {socialView ? (
          <div className="m-social-host">
            <SocialFlow
              topSlot={scopeRow}
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
              searchOpen={searchOpen}
              searchInput={searchInput}
              searchQuery={searchQuery}
              onSearchInputChange={setSearchInput}
              onToggleSearch={toggleSearch}
              readTogglingId={socialReadToggling}
              onToggleRead={handleToggleSocialRead}
              pressBind={socialPressBind}
              ctxAnchorKey={sheet?.anchorKey || null}
              onMarkAllRead={handleMarkAllRead}
              markingRead={markingRead}
              loading={articlesLoading}
              hasMore={hasMore}
              loadingMore={loadingMore}
              onLoadMore={handleLoadMore}
              platformCount={platformCount}
              headless
              emptyHint={socialSources.length === 0 ? '还没有订阅社交账号，去「发现」看看' : searchQuery ? '没有匹配的动态' : '暂无动态'}
              emptyAction={emptyActionOf({ noSources: socialSources.length === 0, social: true })}
              translateEnabled={aiEnabled}
              translations={socialTranslations}
              onToggleTranslation={toggleSocialTranslation}
            />
          </div>
          ) : (
          <div className="m-list" ref={listRef}>
            {scopeRow}
            {/* 内容类型筛选带(issue #178):与桌面同一组件语法,放在列表顶而非顶栏(顶栏须保持单行) */}
            {kindOptions.length > 0 && (
              <div className="reader-kind-band m-kind-band" role="group" aria-label="按内容类型筛选">
                <button
                  type="button"
                  aria-pressed={!kindFilter}
                  onClick={() => setKindFilter('')}
                  className={`reader-kind-chip ${!kindFilter ? 'is-on' : ''}`}
                >
                  全部类型
                </button>
                {kindOptions.map((option) => (
                  <button
                    key={option.kind}
                    type="button"
                    aria-pressed={kindFilter === option.kind}
                    onClick={() => setKindFilter(kindFilter === option.kind ? '' : option.kind)}
                    className={`reader-kind-chip ${kindFilter === option.kind ? 'is-on' : ''}`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            )}
            {/* 预览未订阅的来源改在订阅 Tab 的推入页(批 3a):内容 Tab 只放订阅聚合 */}
            {scope.axis === 'subscribed' && !articlesLoading && freshCount > 0 && (
              <button type="button" className="reader-fresh-pill" onClick={handleRefreshFresh}>
                <RefreshCw className="h-3 w-3" />
                {podcastView ? `载入 ${freshCount} 期新播客` : `载入 ${freshCount} 篇新文章`}
              </button>
            )}
            {articlesLoading ? (
              <ArticleCardsSkeleton card={podcastView ? 'podcast' : 'article'} />
            ) : hasNoSubscriptions && !activeSourceId ? (
              <div className="reader-empty reader-empty-tall">
                <Compass className="h-7 w-7 text-slate-300" />
                <span>你还没有订阅任何来源</span>
                <button type="button" className="action-button action-button-primary" onClick={() => openDiscoverSources()}>
                  去发现来源
                </button>
              </div>
            ) : articles.length === 0 ? (
              <div className="reader-empty">
                <Inbox className="h-6 w-6 text-slate-300" />
                <span>
                  {searchQuery
                    ? (podcastView ? '没有匹配的播客' : '没有匹配的文章')
                    : unreadOnly
                      ? '没有未读内容，都看完啦'
                      : kindFilter
                        ? '当前范围没有这一类内容'
                        : (mode === 'podcast' ? '暂无播客' : '暂无文章')}
                </span>
                {listEmptyAction && (
                  <button type="button" className="action-button action-button-secondary reader-empty-act" onClick={listEmptyAction.onClick}>
                    {listEmptyAction.label}
                  </button>
                )}
              </div>
            ) : (
              <div key={`${activeSourceId ?? '__all__'}|${activeTagId ?? ''}|${mode}|${scope.axis}${scope.favorite ? '+f' : ''}|${kindFilter}`}>
                {listPlan.map((entry) => {
                  const { article } = entry;
                  // 夜航卡(批 4c,样页 c-card / c-day):日期组头在长按包装之外、不吸顶;卡与卡间距由 .m-press 给
                  return (
                    <Fragment key={article.id}>
                      {entry.showLabel && <div className="m-day">{dayLabelOf(entry.dayKey)}</div>}
                      <div className="m-press" {...pressBind(article)}>
                        <ArticleRow
                          article={article}
                          active={false}
                          isUnread={isArticleUnread(article)}
                          isFav={favoriteIds.has(article.id)}
                          entryPodcast={podcastView}
                          showLabel={false}
                          dayKey={entry.dayKey}
                          searchQuery={searchQuery}
                          source={sourceMap[article.source_id]}
                          sourceName={sourceNameMap[article.source_id] || article.source_id}
                          onSelect={selectArticle}
                          onPrefetchEnter={schedulePrefetch}
                          onPrefetchLeave={cancelPrefetch}
                          onToggleFavorite={handleToggleFavorite}
                          onContextMenu={noopContextMenu}
                          ctxAnchor={sheet?.anchorKey === `article:${article.id}`}
                          favIndicator
                          card
                          interestHit={article.interest_hits?.[0] || ''}
                          unsubscribed={false}
                          onSubscribeSource={subscribeSourceById}
                          nowPlaying={article.id === playingId ? playingState : ''}
                          nowPlayingAi={article.id === playingId && playingAi}
                        />
                      </div>
                    </Fragment>
                  );
                })}
                {hasMore && (
                  <div ref={sentinelRef} className="m-load-sentinel" aria-hidden="true">
                    {loadingMore && <ArticleCardsSkeleton count={3} delayed={false} card={podcastView ? 'podcast' : 'article'} />}
                  </div>
                )}
              </div>
            )}
          </div>
          )}
          </>
        )}
      </div>

      {/* ── 底栏(拍板 16 / 样页「浮动磨砂胶囊 + 镜片拇指」):功能模块 早报 · 内容 · 订阅 · 榜单;往下读时退场 ── */}
      <nav className="m-tabbar" aria-label="功能模块" style={{ '--n': tabs.length, '--i': tabIndex }}>
        <span className="m-tab-lens" aria-hidden="true" />
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => goTab(id)}
            aria-label={label}
            aria-current={mTab === id ? 'page' : undefined}
            className={`m-tab ${mTab === id ? 'is-on' : ''}`}
          >
            <Icon aria-hidden="true" />
            <span className="m-tab-label">{label}</span>
          </button>
        ))}
      </nav>

      {/* ── 发现页(整页层:低频目的地,从订阅 Tab 的「+」/「发现更多来源」进入) ── */}
      {discover && (
        <LayerPage className={`m-page m-disc ${discScrolled ? 'is-scrolled' : ''}`} label="发现" onClose={closeDiscover}>
          {/* 页头与设置页 / 推入页同一件(审计 M-21:原顶栏「发现」+ 页头「发现」双标题;返回钮落在头像原位) */}
          <MobileHeader lead={<HeadBack label="返回" onClick={closeDiscover} />} title={<HeadTitle>发现</HeadTitle>} />
          <div className="m-page-scroll" onScroll={(event) => setDiscScrolled(event.currentTarget.scrollTop > 4)}>
            <DiscoverPage
              sources={discoverSources}
              subscribedIds={subscribedIds}
              loading={sourcesLoading}
              pinningId={pinningId}
              onSubscribe={handleSubscribe}
              onUnsubscribe={handleUnsubscribe}
              onPreview={(source) => { closeDiscover(); openFlow({ kind: 'source', id: source.source_id }); }}
              collections={collections}
              activeCollectionId={discoverCollectionId}
              onOpenCollection={(c) => setDiscoverCollectionId(c.collection_id)}
              onCloseCollection={() => setDiscoverCollectionId(null)}
              collectionPinningId={collectionPinningId}
              onSubscribeCollection={handleSubscribeCollection}
              onUnsubscribeCollection={handleUnsubscribeCollection}
              shapePinning={shapePinning}
              onSubscribeShape={handleSubscribeShape}
              userSourcesEnabled={userSourcesEnabled}
              onAddCustomSource={handleAddCustomSource}
              guest={rs.isGuest}
              tab={discoverTab}
              onTabChange={(next) => (next === 'interests' && rs.isGuest
                ? requestLogin('interest')
                : setDiscoverTab(next))}
              shape={discoverShape}
              onShapeChange={setDiscoverShape}
              onOpenRankings={() => { closeDiscover(); setTab('rankings'); }}
              interestsPanel={personalDigestEnabled ? (
                <InterestPage
                  mobile
                  embedded
                  onboarding={onboardingRequired}
                  showToast={showToast}
                  onSaved={({ onboardingCompleted, briefRebuildStatus } = {}) => {
                    setInterestVersion((value) => value + 1);
                    refreshInterests();
                    if (onboardingCompleted) {
                      onUserUpdated?.({ interest_onboarding_completed: true });
                      const toast = rebuildToast(briefRebuildStatus);
                      if (toast) showToast?.(toast.text, toast.kind);
                      if (!discoverRef.current) return;
                      closeDiscover();
                      setBriefRestore(null);
                      setTab('brief');
                    }
                  }}
                />
              ) : null}
            />
          </div>
        </LayerPage>
      )}

      {/* ── 订阅 Tab 的推入页(批 3a):来源 / 全部 / 收藏 / 兴趣各一条流,独立的列表实例;盖住底栏,正文页叠在它之上 ── */}
      {flow && (
        <MobileSubsFlow
          key={pushSpecKey(flow)}
          spec={flow}
          rs={rs}
          showToast={showToast}
          onBack={closeFlow}
          aiEnabled={aiEnabled}
          pressBind={pressBind}
          socialPressBind={socialPressBind}
          sheetAnchorKey={sheet?.anchorKey || null}
          playingId={playingId}
          playingState={playingState}
          playingAi={playingAi}
          subscribeSourceById={subscribeSourceById}
        />
      )}

      {/* ── 正文页(push 全屏,无 TabBar) ── */}
      {readOpen && (
        <MobileArticlePage
          rs={readRs}
          guestAccessEnabled={guestAccessEnabled}
          aiEnabled={aiEnabled}
          ondemand={ondemand}
          showToast={showToast}
          onBack={closeArticle}
          onLeaveForSearch={leaveArticleForSearch}
        />
      )}

      {/* ── 长按动作单(桌面右键菜单的 bottom-sheet 翻译) ── */}
      <ActionSheet
        open={Boolean(sheet)}
        title={shownSheet?.title || ''}
        items={shownSheet?.items || []}
        preview={shownSheet?.preview || null}
        className="is-float"
        onClose={() => setSheet(null)}
      />

      {/* ── 全局播放条(issue #199):挂在本外壳里,层级在正文页(40)之上、抽屉 / 动作单(50 / 60)之下 ── */}
      {/* routeKey = 底栏 Tab(mTab):只在切底栏 Tab 时悬浮卡一并收起;showToast:停止播放后的「撤销」提示条(issue #230 批 3d) */}
      <PlayerDock mobile hostRef={playerHostRef} mobilePage={playerPage} onOpenItem={openPlayingItem} routeKey={mTab} showToast={showToast} />
    </div>
  );
}
