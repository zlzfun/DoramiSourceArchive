import { useLayoutEffect, useRef, useState } from 'react';
import {
  CheckCheck,
  ExternalLink,
  Languages,
  Link,
  Loader2,
  MoreHorizontal,
  Share2,
  Star,
} from 'lucide-react';
import ArticleCopy from '../ArticleCopy';
import PaperContentNote from '../PaperContentNote';
import ShareMenu from '../ShareMenu';
import PodcastExperiencePanel from '../PodcastExperiencePanel';
import ListenEntries from '../player/ListenEntries';
import AnalysisTagChip from '../AnalysisTagChip';
import { PaneBodySkeleton } from '../ReaderTab';
import { formatPublishDate, formatPublishDateTime } from '../../utils/datetime';
import { formatPodcastDuration } from '../../utils/podcast';
import {
  analysisStatusMeta,
  displayAnalysisTags,
  shouldShowAiReadingCard,
} from '../../utils/analysis';
import { articleListenEntries, podcastListenEntries } from '../../utils/listenEntries';
import AiReadingCard from '../AiReadingCard';
import { hostOf, latinLang } from '../../utils/readerText';
import { kindKickerLabel } from '../../utils/contentKind';
import { titleView } from '../../utils/titleDisplay';
import { useTitlePreference } from '../../hooks/useTitlePreference';
import { articleDeepLink } from '../../utils/shareLink';
import { readHeadHidden, readMenuSpec } from '../../utils/readPage';
import {
  installReadOriginTracker,
  playReadEnter,
  playReadExit,
  takeReadOrigin,
} from '../../utils/readTransition';
import { motionReduced } from '../../motion';
import LayerPage from '../layer/LayerPage';
import ActionSheet from './ActionSheet';
import { HeadBack } from './MobileHeader';

// 入栈动效的「来处」要在点卡片那一下记下(正文页那时还没挂载):模块加载时装一次捕获阶段监听(幂等)
installReadOriginTracker();

const MENU_ICONS = {
  translate: Languages,
  open: ExternalLink,
  copy: Link,
  sysshare: Share2,
  markall: CheckCheck,
};
const INTENT_MS = 1000;

// 正文页(移动波 Wave2 起;issue #230 批 3c 按夜航样页「正文 / 读到文末 / ⋯ 菜单」屏重做):push 全屏页,无底部 Tab。
// - 没有顶栏:左上返回钮在头像原位,右上一枚磨砂胶囊 收藏 · 分享 · ⋯(拍板 7);「译」撤出顶栏(拍板 3),
//   翻译与查看原文进「⋯」底页(只出菜单、不带预览卡;抓手真按钮「关闭更多操作」,拍板 33)。
// - 往下读时右上与返回钮退场、往上一划回来(样页镀层退场;减少动效时常显)。
// - 入栈 = 从被点的卡片矩形展开,返回收回原卡(拍板 8,utils/readTransition;减少动效时无动效)。
// - 文末只留「查看原文 ↗ · 域名」(第 5 轮),没有上一篇 / 下一篇、没有底部工具条(拍板 26,审计 M-45)。
// - 文章「听导读」住 AI 速读卡卡头之下一行(#238);播客的收听入口仍在署名行下。播放条是全局的,不在这一页里。
// 标题区 / 速读卡 / 正文排印复用阅读窗语法类(.reader-kicker / .reader-pane-title / .reader-ai-summary / .markdown-body),
// 手机档的尺寸在 mobile.css「批 3c」段覆盖。层的行为走层原语 LayerPage(返回键 / Esc / 焦点 / 下层 inert)。
export default function MobileArticlePage({
  rs,
  guestAccessEnabled = false,
  aiEnabled,
  ondemand = {},   // 点播能力位(issue #137):{podcast, article}
  showToast,
  onBack,
  onLeaveForSearch = null, // 标签检索离开正文页的专用出口(不走早报返回,也不播收回动效)
}) {
  const {
    activeArticle, activeBody, activeBodyLoading,
    sourceNameMap, displayBody, displayTranslatedBody, bodyStats, podcastView,
    favoriteIds, favTogglingId, handleToggleFavorite,
    shareOpen, setShareOpen,
    showTranslation, translating, hasTranslation, translationReady, translation, activeIsChinese,
    handleTranslate, resumeTranslation, restartTranslation,
    activeSummary, summarizing, handleSummarize,
    searchForTag, isArticleUnread, buildArticleMenuItems,
    refreshActiveArticle,
  } = rs;
  const { preferChinese } = useTitlePreference();
  const [moreOpen, setMoreOpen] = useState(false);
  const headRef = useRef(null);
  // 入栈的来处(挂载时取走一次;StrictMode 重跑 effect 时沿用同一份)。只对打开时的那一篇有效
  const originRef = useRef(undefined);
  const mountIdRef = useRef(activeArticle?.id ?? null);
  // 右上镀层钮退场:滚动位置、是否已藏、最近一次读者输入的时刻(程序性滚动不算)
  const headState = useRef({ y: 0, hidden: false, inputAt: -Infinity });

  const panelOf = () => headRef.current?.closest('.m-read') || null;

  useLayoutEffect(() => {
    const panel = headRef.current?.closest('.m-read');
    if (originRef.current === undefined) {
      const origin = takeReadOrigin();
      originRef.current = origin ? { ...origin, articleId: mountIdRef.current } : null;
    }
    const animations = playReadEnter(panel, originRef.current);
    return () => animations.forEach((animation) => animation.cancel());
  }, []);

  // 换篇(滚动区按 id 重建)= 回到顶部:镀层钮回来
  const articleId = activeArticle?.id;
  useLayoutEffect(() => {
    headState.current.y = 0;
    headState.current.hidden = false;
    headRef.current?.closest('.m-read')?.removeAttribute('data-immersed');
  }, [articleId]);

  if (!activeArticle) return null;

  // 所有出口(返回钮 / Esc / 系统返回)都经这里:先留一个替身收回原卡,再让列表回来
  const leave = () => {
    const origin = originRef.current;
    playReadExit(panelOf(), origin && origin.articleId === activeArticle.id ? origin : null);
    onBack?.();
  };

  const markIntent = () => { headState.current.inputAt = performance.now(); };
  const onReadScroll = (event) => {
    const el = event.currentTarget;
    const state = headState.current;
    const y = el.scrollTop;
    const dy = y - state.y;
    state.y = y;
    const next = readHeadHidden(state.hidden, {
      y,
      dy,
      reduced: motionReduced(),
      intent: performance.now() - state.inputAt < INTENT_MS,
    });
    if (next === state.hidden) return;
    state.hidden = next;
    panelOf()?.toggleAttribute('data-immersed', next);
  };

  const isFav = favoriteIds.has(activeArticle.id);
  const analysisStatus = analysisStatusMeta(activeArticle, { podcast: podcastView });
  const listenList = podcastView
    ? podcastListenEntries(activeArticle, { aiEnabled, ondemandEnabled: ondemand.podcast === true })
    : articleListenEntries(activeArticle, { aiEnabled, ondemandEnabled: ondemand.article === true });
  const durationOnEntry = listenList.some((entry) => entry.track === 'original');
  // 标题语言(issue #218):只看「默认显示中文标题」偏好,不随正文的译文 / 原文切换
  const paneTitle = titleView(activeArticle, preferChinese);
  const tags = displayAnalysisTags(activeArticle);

  // 「⋯」菜单:项与顺序见 utils/readPage.readMenuSpec;动作与长按动作单同源(buildArticleMenuItems)
  const buildMenu = () => {
    const base = Object.fromEntries(buildArticleMenuItems(activeArticle)
      .filter((item) => item.key)
      .map((item) => [item.key, item]));
    const canShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function';
    const actions = {
      translate: handleTranslate,
      open: base.open?.onClick,
      copy: base.copy?.onClick,
      sysshare: () => {
        navigator.share({ title: activeArticle.title || '哆啦美', url: articleDeepLink(activeArticle.id) }).catch(() => {});
      },
      read: base.read?.onClick,
      markall: base.markall?.onClick,
    };
    return readMenuSpec({
      translatable: Boolean(aiEnabled) && !activeIsChinese,
      translationOn: showTranslation,
      hasTranslation,
      translating,
      bodyReady: !activeBodyLoading && Boolean(activeBody),
      podcast: podcastView,
      canShare,
      unread: isArticleUnread(activeArticle),
      hasSource: Boolean(activeArticle.source_url),
    }).map((item) => (item.type ? item : {
      ...item,
      icon: item.key === 'read' ? base.read?.icon : MENU_ICONS[item.key],
      onClick: actions[item.key],
    }));
  };

  const copy = (
    <ArticleCopy
      title={activeArticle.title}
      body={activeBody ? displayBody : ''}
      rawBody={activeBody}
      loading={activeBodyLoading}
      loadingFallback={<PaneBodySkeleton />}
      emptyText={podcastView
        ? '该播客暂无文字内容，可收听上方原节目音频。'
        : '该文章暂无正文内容，点击「查看原文」阅读完整内容。'}
      sourceUrl={activeArticle.source_url}
      zhView={showTranslation && Boolean(activeBody)}
      translation={translation}
      translatedBody={displayTranslatedBody}
      onResume={resumeTranslation}
      onRestart={restartTranslation}
    />
  );

  return (
    <LayerPage className="m-read" label="正文" onClose={leave}>
      {/* 浮在正文上的镀层钮:返回(头像原位)· 收藏 · 分享 · ⋯ */}
      <div className="m-head m-read-head" ref={headRef}>
        <HeadBack label="返回列表" onClick={leave} />
        <span className="m-head-sp" />
        <div className="m-read-acts">
          <button
            type="button"
            className={`m-read-act ${isFav ? 'is-on' : ''}`}
            onClick={(e) => handleToggleFavorite(activeArticle, e)}
            disabled={favTogglingId === activeArticle.id}
            aria-label={isFav ? '取消收藏' : '收藏'}
          >
            {favTogglingId === activeArticle.id
              ? <Loader2 className="animate-spin" aria-hidden="true" />
              : <Star fill={isFav ? 'currentColor' : 'none'} aria-hidden="true" />}
          </button>
          {/* 分享仍是锚定浮层(样页同;分享去留归 #215) */}
          <div className="reader-share-anchor">
            <button
              type="button"
              className={`m-read-act ${shareOpen ? 'is-open' : ''}`}
              onClick={() => setShareOpen((v) => !v)}
              aria-label="分享"
              aria-expanded={shareOpen}
            >
              <Share2 aria-hidden="true" />
            </button>
            {shareOpen && (
              <ShareMenu
                articleId={activeArticle.id}
                onClose={() => setShareOpen(false)}
                showToast={showToast}
                guestAccessEnabled={guestAccessEnabled}
              />
            )}
          </div>
          <button
            type="button"
            className="m-read-act"
            onClick={() => setMoreOpen(true)}
            aria-label="更多操作"
            aria-haspopup="dialog"
            aria-expanded={moreOpen}
          >
            <MoreHorizontal aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* 阅读进度线(scroll-timeline 渐进增强,同阅读窗;不支持的浏览器隐形) */}
      {!activeBodyLoading && activeBody ? <div className="m-read-progress" aria-hidden="true" /> : null}

      {/* 换篇由 key 重建滚动区域;跨版式同篇的进度由 ReaderWorkspace 恢复。 */}
      <div
        className="m-read-scroll"
        key={activeArticle.id}
        onScroll={onReadScroll}
        onWheel={markIntent}
        onTouchMove={markIntent}
        onKeyDown={markIntent}
        onPointerDown={markIntent}
      >
        <header className="reader-pane-head">
          {/* 标题区与桌面同源(眉头「源 · 类型」/ 标题 / 原标题 / 署名行 / 标签);动作行不渲染,动作在右上镀层钮与「⋯」 */}
          <div className="reader-kicker">
            {(sourceNameMap[activeArticle.source_id] || activeArticle.source_id)}
            {kindKickerLabel(activeArticle) ? ` · ${kindKickerLabel(activeArticle)}` : ''}
          </div>
          <h1 className="reader-pane-title" lang={latinLang(paneTitle.title)}>{paneTitle.title}</h1>
          {activeArticle.is_premium_podcast && <span className="podcast-premium-badge is-title">优质播客</span>}
          {paneTitle.orig && (
            <div className="reader-pane-title-orig" lang={latinLang(paneTitle.orig)}>{paneTitle.orig}</div>
          )}
          <div className="reader-pane-meta">
            {activeArticle.publish_date && (
              <span title={formatPublishDateTime(activeArticle.publish_date)}>
                {formatPublishDate(activeArticle.publish_date)}
              </span>
            )}
            {bodyStats && (
              <span>{podcastView ? '简介阅读约' : '阅读约'} {bodyStats.minutes} 分钟</span>
            )}
            {podcastView && !durationOnEntry && formatPodcastDuration(activeArticle.podcast?.duration_seconds) && (
              <span>原节目 {formatPodcastDuration(activeArticle.podcast.duration_seconds)}</span>
            )}
            {typeof activeArticle.read_count === 'number' && activeArticle.read_count > 0 && (
              <span>阅读量 {activeArticle.read_count.toLocaleString()}</span>
            )}
            {analysisStatus && <span role="status">{analysisStatus.label}</span>}
          </div>
          {podcastView && listenList.length > 0 && (
            <div className="m-listen">
              <ListenEntries
                key={activeArticle.id}
                article={activeArticle}
                podcast={podcastView}
                aiEnabled={aiEnabled}
                ondemand={ondemand}
                sourceName={sourceNameMap[activeArticle.source_id] || ''}
                showToast={showToast}
                onRefresh={refreshActiveArticle}
              />
            </div>
          )}
          {tags.length > 0 && (
            <div className="reader-pane-foot"><div className="reader-pane-tags">
              {tags.map((tag, index) => (
                <AnalysisTagChip
                  key={`${tag.type || 'canonical'}-${tag.id || tag.code || tag.candidate_id || index}`}
                  tag={tag}
                  onSearch={(selectedTag) => {
                    searchForTag(selectedTag);
                    (onLeaveForSearch || onBack)();
                  }}
                />
              ))}
            </div></div>
          )}
        </header>
        <div className="m-read-body">
          {/* 速读卡常驻:正文与音轨解耦,在听哪条音轨都不收起它(issue #199);文章听导读在卡头之下一行(#238) */}
          {!activeBodyLoading && ((!podcastView && listenList.length > 0) || shouldShowAiReadingCard(activeArticle, {
            summary: activeSummary,
            aiEnabled,
            body: activeBody,
          })) && (
            <AiReadingCard
              article={activeArticle}
              summary={activeSummary}
              summarizing={summarizing}
              canGenerate={aiEnabled && Boolean(activeBody)}
              onGenerate={handleSummarize}
              listen={!podcastView && listenList.length > 0 ? (
                <ListenEntries
                  key={activeArticle.id}
                  article={activeArticle}
                  aiEnabled={aiEnabled}
                  ondemand={ondemand}
                  sourceName={sourceNameMap[activeArticle.source_id] || ''}
                  showToast={showToast}
                  onRefresh={refreshActiveArticle}
                />
              ) : null}
              podcast={podcastView}
            />
          )}
          {!podcastView && (
            <PaperContentNote article={activeArticle} />
          )}
          {podcastView ? (
            <PodcastExperiencePanel
              article={activeArticle}
              showTranslation={translationReady}
              showNotesHead={!activeBodyLoading && Boolean(activeBody)}
            >
              {copy}
            </PodcastExperiencePanel>
          ) : copy}
          {/* 读到文末(样页第 5 轮):只留「查看原文 ↗ · 域名」;无 source_url 不画 */}
          {!activeBodyLoading && activeArticle.source_url && (
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
      </div>

      {/* 「⋯」底页:只有菜单(样页 read-menu);浮动磨砂卡,抓手念「关闭更多操作」 */}
      <ActionSheet
        open={moreOpen}
        label="更多操作"
        className="is-float"
        items={buildMenu()}   // 关着也给:底页关掉后还要播退场(批 4a),退场期间不画成空菜单
        onClose={() => setMoreOpen(false)}
      />
    </LayerPage>
  );
}
