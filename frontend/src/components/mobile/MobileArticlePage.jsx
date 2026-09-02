import { useEffect, useRef } from 'react';
import {
  ChevronLeft,
  ExternalLink,
  Loader2,
  MoreHorizontal,
  Share2,
  Sparkles,
  Star,
} from 'lucide-react';
import ReaderMarkdown from '../ReaderMarkdown';
import ShareMenu from '../ShareMenu';
import AnalysisTagChip from '../AnalysisTagChip';
import { PaneBodySkeleton } from '../ReaderTab';
import { formatRelativeTime, formatDateTime } from '../../utils/datetime';
import { contentTypeLabel } from '../../utils/contentType';
import { displayAnalysisTags, qualityScoreText } from '../../utils/analysis';

// 正文页(移动波 Wave2,样页画面②):push 全屏页——无底部 Tab,返回即出栈。
// 衬线标题/kicker/meta/速读卡/进度线/上一下一篇 全部复用阅读窗语法类
// (.reader-kicker/.reader-pane-title/.reader-ai-summary/markdown-body,单一事实来源);
// 顶栏动作 = 原文/收藏/分享/译/更多(更多=与长按同款动作单,由父级 onMore 装配)。
export default function MobileArticlePage({
  rs,
  aiEnabled,
  showToast,
  onBack,
  onMore,
}) {
  const {
    activeArticle, activeBody, activeBodyLoading,
    sourceNameMap, displayBody, displayTranslatedBody, bodyStats,
    favoriteIds, favTogglingId, handleToggleFavorite,
    shareOpen, setShareOpen,
    showTranslation, translating, translatedBody, handleTranslate,
    activeSummary, summarizing, handleSummarize,
    prevArticle, nextArticle, activeIndex, selectArticle, searchForLabel,
  } = rs;

  // 换篇即回顶(push 页语义:每篇都是新页)
  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [activeArticle?.id]);

  if (!activeArticle) return null;
  const isFav = favoriteIds.has(activeArticle.id);

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
            />
          )}
        </div>
        {aiEnabled && (
          <button
            type="button"
            className={`m-iconbtn ${showTranslation ? 'is-ai' : ''}`}
            onClick={handleTranslate}
            disabled={translating || activeBodyLoading || !activeBody}
            title={showTranslation ? '当前显示中文译文，点击切回原文' : '将正文译为中文'}
            aria-label={showTranslation ? '显示原文' : '译为中文'}
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

      <div className="m-read-scroll" ref={scrollRef} key={activeArticle.id}>
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
            {bodyStats && <span>阅读时长 {bodyStats.minutes} 分钟</span>}
            {typeof activeArticle.read_count === 'number' && activeArticle.read_count > 0 && (
              <span>阅读量 {activeArticle.read_count.toLocaleString()}</span>
            )}
          </div>
          {(activeArticle.quality_score != null || displayAnalysisTags(activeArticle).length > 0) && (
            <div className="reader-analysis-summary">
              <div className="reader-analysis-top">
                {activeArticle.quality_score != null && (
                  <span className="reader-analysis-score"><strong>{qualityScoreText(activeArticle.quality_score)}</strong><small>内容价值分</small></span>
                )}
                <span className="reader-analysis-tags">
                  {displayAnalysisTags(activeArticle).map((tag, index) => (
                    <AnalysisTagChip
                      key={`${tag.type || 'canonical'}-${tag.id || tag.code || tag.candidate_id || index}`}
                      tag={tag}
                      onTemporarySearch={(label) => {
                        searchForLabel(label);
                        onBack();
                      }}
                    />
                  ))}
                </span>
              </div>
              {activeArticle.score_reason && <p>{activeArticle.score_reason}</p>}
              <small>AI 内容价值评估，用于辅助筛选，不代表事实保证或你的个人评分</small>
            </div>
          )}
        </header>
        <div className="m-read-body markdown-body">
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
          {/* 用户自定源(v3.40):正文尾部一律附原文链接(与桌面阅读窗同口径) */}
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
          <nav className="m-read-pager" aria-label="上一篇 / 下一篇">
            <button
              type="button"
              className="m-pager-btn"
              disabled={!prevArticle}
              onClick={() => prevArticle && selectArticle(prevArticle)}
            >
              <span className="m-pager-k">上一篇</span>
              <span className="m-pager-t">{prevArticle ? (prevArticle.title || '（无标题）') : '已是最新一篇'}</span>
            </button>
            <button
              type="button"
              className="m-pager-btn is-next"
              disabled={!nextArticle}
              onClick={() => nextArticle && selectArticle(nextArticle)}
            >
              <span className="m-pager-k">下一篇</span>
              <span className="m-pager-t">{nextArticle ? (nextArticle.title || '（无标题）') : '已到列表末尾'}</span>
            </button>
          </nav>
        )}
      </div>
    </div>
  );
}
