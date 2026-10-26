import {
  ChevronLeft,
  ExternalLink,
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
import { displayTitle, titleView } from '../../utils/titleDisplay';
import { useTitlePreference } from '../../hooks/useTitlePreference';

// 正文页(移动波 Wave2,样页画面②):push 全屏页——无底部 Tab,返回即出栈。
// 衬线标题/kicker/meta/速读卡/进度线/上一下一篇 全部复用阅读窗语法类
// (.reader-kicker/.reader-pane-title/.reader-ai-summary/markdown-body,单一事实来源);
// 顶栏动作 = 原文/收藏/分享/译/更多(更多=与长按同款动作单,由父级 onMore 装配)。
// 收听入口(issue #199 方案 C)在署名行下:移动端没有动作行;播放条是全局的,不在这一页里。
export default function MobileArticlePage({
  rs,
  guestAccessEnabled = false,
  aiEnabled,
  ondemand = {},   // 点播能力位(issue #137):{podcast, article}
  showToast,
  onBack,
  onLeaveForSearch = null, // 标签检索离开正文页的专用出口(不走早报返回)
  onMore,
}) {
  const {
    activeArticle, activeBody, activeBodyLoading,
    sourceNameMap, displayBody, displayTranslatedBody, bodyStats, podcastView,
    favoriteIds, favTogglingId, handleToggleFavorite,
    shareOpen, setShareOpen,
    showTranslation, translating, hasTranslation, translationReady, translation, activeIsChinese,
    handleTranslate, resumeTranslation, restartTranslation,
    activeSummary, summarizing, handleSummarize,
    prevArticle, nextArticle, activeIndex, selectArticle, searchForTag,
    refreshActiveArticle,
  } = rs;
  const { preferChinese } = useTitlePreference();

  if (!activeArticle) return null;
  const isFav = favoriteIds.has(activeArticle.id);
  const analysisStatus = analysisStatusMeta(activeArticle, { podcast: podcastView });
  const listenList = podcastView
    ? podcastListenEntries(activeArticle, { aiEnabled, ondemandEnabled: ondemand.podcast === true })
    : articleListenEntries(activeArticle, { aiEnabled, ondemandEnabled: ondemand.article === true });
  const durationOnEntry = listenList.some((entry) => entry.track === 'original');
  // 标题语言(issue #218):只看「默认显示中文标题」偏好,不随顶栏「译」切换
  const paneTitle = titleView(activeArticle, preferChinese);
  // 顶栏译钮切的是正文语言;后台任务排队 / 进行中时圈圈替换「译」字(不论当前看原文还是中文),停止即恢复
  const translateLabel = showTranslation ? '显示原文'
    : hasTranslation ? '显示中文' : podcastView ? '将节目简介译为中文' : '译为中文';
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
    <div className="m-read" role="region" aria-label="正文">
      <div className="m-topbar on-pane">
        <button type="button" className="m-iconbtn" onClick={onBack} aria-label="返回列表">
          <ChevronLeft />
        </button>
        <div className="m-topbar-sp" />
        {activeArticle.source_url && (
          <a
            href={activeArticle.source_url}
            target="_blank"
            rel="noreferrer"
            className="m-iconbtn"
            title="查看来源"
            aria-label="查看来源"
          >
            <ExternalLink />
          </a>
        )}
        <button
          type="button"
          className={`m-iconbtn ${isFav ? 'is-amber' : ''}`}
          onClick={(e) => handleToggleFavorite(activeArticle, e)}
          disabled={favTogglingId === activeArticle.id}
          title={isFav ? '取消收藏' : '收藏'}
          aria-label={isFav ? '取消收藏' : '收藏'}
        >
          {favTogglingId === activeArticle.id
            ? <Loader2 className="animate-spin" />
            : <Star fill={isFav ? 'currentColor' : 'none'} />}
        </button>
        <div className="reader-share-anchor">
          <button
            type="button"
            className={`m-iconbtn ${shareOpen ? 'is-blue' : ''}`}
            onClick={() => setShareOpen((v) => !v)}
            title="分享"
            aria-label="分享"
            aria-expanded={shareOpen}
          >
            <Share2 />
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
        {aiEnabled && !activeIsChinese && (
          <button
            type="button"
            className={`m-iconbtn ${showTranslation ? 'is-ai' : ''}`}
            onClick={handleTranslate}
            disabled={activeBodyLoading || !activeBody}
            title={translateLabel}
            aria-label={translating ? `${translateLabel}（翻译中）` : translateLabel}
            aria-pressed={showTranslation}
          >
            {translating ? <Loader2 className="animate-spin" /> : <span className="reader-tr-glyph" aria-hidden="true">译</span>}
          </button>
        )}
        <button type="button" className="m-iconbtn" onClick={onMore} title="更多操作" aria-label="更多操作">
          <MoreHorizontal />
        </button>
      </div>

      {/* 阅读进度线(scroll-timeline 渐进增强,同阅读窗;不支持的浏览器隐形) */}
      {!activeBodyLoading && activeBody ? <div className="m-read-progress" aria-hidden="true" /> : null}

      {/* 换篇由 key 重建滚动区域；跨版式同篇的进度由 ReaderWorkspace 恢复。 */}
      <div className="m-read-scroll" key={activeArticle.id}>
        <header className="reader-pane-head">
          {/* 标题区与桌面同源(眉头「源 · 类型」/ 标题 / 署名行 / 标签小签);动作行不渲染,顶栏译钮承担 */}
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
          {listenList.length > 0 && (
            <div className="m-listen">
              <ListenEntries
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
          {displayAnalysisTags(activeArticle).length > 0 && (
            <div className="reader-pane-foot"><div className="reader-pane-tags">
              {displayAnalysisTags(activeArticle).map((tag, index) => (
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
          {/* 速读卡常驻:正文与音轨解耦,在听哪条音轨都不收起它(issue #199) */}
          {!activeBodyLoading && shouldShowAiReadingCard(activeArticle, {
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
          {/* 正文尾部原文行(v3.45 推全站,与桌面阅读窗同口径):无 source_url 不画 */}
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
        {/* 上一篇/下一篇:沿当前列表序的真实翻页(选中项不在列表时隐藏) */}
        {activeIndex >= 0 && (prevArticle || nextArticle) && (
          <nav className="m-read-pager" aria-label="上一篇 / 下一篇">
            <button
              type="button"
              className="m-pager-btn"
              disabled={!prevArticle}
              onClick={() => prevArticle && selectArticle(prevArticle)}
            >
              <span className="m-pager-k">上一篇</span>
              <span className="m-pager-t">{prevArticle ? displayTitle(prevArticle, preferChinese) : '已是最新一篇'}</span>
            </button>
            <button
              type="button"
              className="m-pager-btn is-next"
              disabled={!nextArticle}
              onClick={() => nextArticle && selectArticle(nextArticle)}
            >
              <span className="m-pager-k">下一篇</span>
              <span className="m-pager-t">{nextArticle ? displayTitle(nextArticle, preferChinese) : '已到列表末尾'}</span>
            </button>
          </nav>
        )}
      </div>
    </div>
  );
}
