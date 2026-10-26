import { Loader2, Sparkles } from 'lucide-react';
import AnalysisTagChip from '../../components/AnalysisTagChip';
import SocialFlow from '../../components/SocialFlow';
import { analysisStatusMeta, displayAnalysisTags } from '../../utils/analysis';
import { kindKickerLabel } from '../../utils/contentKind';
import { latinLang } from '../../utils/readerText';
import { formatPublishDate, formatPublishDateTime } from '../../utils/datetime';
import { formatPodcastDuration } from '../../utils/podcast';
import { SOURCE_MAP, SOURCE_NAME_MAP } from './fixtures';
import { emptyListText } from './readerModel';

// 阅读窗 / 手机正文页共用的标题区与语言控件:结构与类名照搬 ReaderTab / MobileArticlePage 的四行标题区,
// 唯一改动是标题来自 displayTitleOf(只看标题开关),不再跟着正文语言走。

// 「原语言 | 中文」二段(v3.45 语法):已有任务(进行中 / 暂停 / 失败 / 完成)时第二段叫「中文」,只切视图;
// 没有任务时叫「译为中文」,点了才开始翻译。翻译进行中两段都可点,切回原文不取消后台任务。
// 小圈圈 = 后台任务排队或进行中,不论当前看原文还是中文;完成、失败、暂停、取消即停,回到 ✦。
// 这是正常进行中唯一的进度表达(不出进度文字、段数或完成提示)。
export function LanguageSeg({ podcast = false, hasJob, running, zhView, onOriginal, onChinese }) {
  const zhLabel = hasJob ? '中文' : '译为中文';
  return (
    <div className="reader-tr-seg" role="group" aria-label={podcast ? '节目简介语言' : '正文语言'}>
      <button
        type="button"
        className={`reader-tr-seg-btn ${zhView ? '' : 'is-on'}`}
        aria-pressed={!zhView}
        onClick={() => { if (zhView) onOriginal(); }}
      >
        原语言
      </button>
      <button
        type="button"
        className={`reader-tr-seg-btn ${zhView ? 'is-on is-ai' : ''}`}
        aria-pressed={zhView}
        title={zhView ? '当前显示中文' : hasJob ? '查看中文' : podcast ? '将节目简介译为中文' : '将正文译为中文'}
        onClick={() => { if (!zhView) onChinese(); }}
      >
        {running
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          : <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
        <span className={zhView ? 'ai-grad-text' : ''}>{zhLabel}</span>
        {running && <span className="sr-only">，翻译中</span>}
      </button>
    </div>
  );
}

export function PaneHead({ article, sourceName, display, actions = null, podcast = false, mobile = false }) {
  const tags = displayAnalysisTags(article);
  const kicker = kindKickerLabel(article);
  const status = analysisStatusMeta(article, { podcast });
  const duration = podcast ? formatPodcastDuration(article.podcast?.duration_seconds) : '';
  const meta = (
    <div className="reader-pane-meta">
      {article.publish_date && (
        <span title={formatPublishDateTime(article.publish_date)}>{formatPublishDate(article.publish_date)}</span>
      )}
      {article.read_minutes ? <span>{podcast ? '简介阅读约' : '阅读约'} {article.read_minutes} 分钟</span> : null}
      {duration && <span>原节目 {duration}</span>}
      {article.read_count > 0 && <span>阅读量 {article.read_count.toLocaleString()}</span>}
      {status && <span role="status">{status.label}</span>}
    </div>
  );
  return (
    <header className="reader-pane-head">
      <div className="reader-kicker">
        {sourceName}
        {kicker ? ` · ${kicker}` : ''}
      </div>
      <h1 className="reader-pane-title" lang={latinLang(display.text)}>{display.text}</h1>
      {/* 中文标题在上、原标题降一行(沿用 v3.45 译文态原标题的位置与西文衬线斜体),原文随时可见 */}
      {display.translated && (
        <div className="reader-pane-title-orig" lang={latinLang(display.original)}>{display.original}</div>
      )}
      {mobile ? meta : (
        <div className="reader-pane-byline">
          {meta}
          {tags.length === 0 && actions}
        </div>
      )}
      {tags.length > 0 && (
        <div className="reader-pane-foot">
          <div className="reader-pane-tags">
            {tags.map((tag, index) => (
              <AnalysisTagChip key={`${tag.type || 'canonical'}-${tag.id || tag.label || index}`} tag={tag} />
            ))}
          </div>
          {!mobile && actions}
        </div>
      )}
    </header>
  );
}

// 社交容器:现网 SocialFlow 原件,喂样页数据。社交卡没有独立标题区,本需求不给它加;
// 推文与引用推的译文仍是逐卡手动「✦ 译为中文」,不自动展开。headless = 手机壳(控件归顶栏)。
export function PreviewSocialFlow({ m, headless = false }) {
  const unreadCount = m.visibleItems.filter((item) => m.unread.has(item.id)).length;
  const searching = m.socialSearch.open && Boolean(m.socialSearch.input.trim());
  return (
    <SocialFlow
      articles={m.visibleItems}
      sourceMap={SOURCE_MAP}
      sourceNameMap={SOURCE_NAME_MAP}
      unreadCount={unreadCount}
      unreadOnly={m.unreadOnly}
      onUnreadOnlyChange={m.setUnreadOnly}
      isArticleUnread={(item) => m.unread.has(item.id)}
      favoriteIds={m.fav}
      favTogglingId={null}
      onToggleFavorite={(item) => m.toggleFav(item.id)}
      favOnly={m.favOnly}
      onToggleFavOnly={() => m.setFavOnly((value) => !value)}
      searchOpen={m.socialSearch.open}
      searchInput={m.socialSearch.input}
      searchQuery={m.socialSearch.open ? m.socialSearch.input.trim() : ''}
      onSearchInputChange={(input) => m.setSocialSearch((current) => ({ ...current, input }))}
      onToggleSearch={() => m.setSocialSearch((current) => ({ open: !current.open, input: '' }))}
      readTogglingId={null}
      onToggleRead={(item) => m.toggleRead(item.id)}
      onPostContextMenu={null}
      onMarkAllRead={m.markAllRead}
      platformCount={1}
      activeSourceId={m.sourceFilter}
      emptyHint={emptyListText({ mode: 'social', sourceFilter: m.sourceFilter, searching })}
      translateEnabled
      translations={m.socialTr}
      onToggleTranslation={m.toggleSocialTranslation}
      headless={headless}
    />
  );
}

// 设置「阅读」分区的唯一一行(桌面设置柜与手机设置栈共用,行范式同外观分区的「减少动效」)
export function ReadingSection({ zhTitle, onChange }) {
  return (
    <div className="sett-row">
      <span className="sett-id">
        <span className="sett-lbl">默认显示中文标题</span>
        <div className="sett-sub">有中文译名的标题显示中文，阅读页保留原标题；正文语言不受影响</div>
      </span>
      <span className="sett-ctl">
        <button
          type="button"
          role="switch"
          aria-checked={zhTitle}
          aria-label="默认显示中文标题"
          onClick={() => onChange(!zhTitle)}
          className={`ledger-switch ${zhTitle ? 'is-on' : ''}`}
        />
      </span>
    </div>
  );
}
