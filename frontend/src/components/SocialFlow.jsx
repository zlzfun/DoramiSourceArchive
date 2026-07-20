import { Fragment, useCallback, useMemo, useState } from 'react';
import { AtSign, Check, CheckCheck, Circle, Loader2, Repeat2, Star } from 'lucide-react';
import LogoMark from './LogoMark';
import { platformLabelOf, resolveCompany } from '../sourceTaxonomy';
import { dayKeyOf, dayLabelOf } from '../utils/readerTime';
import { formatRelativeTime, formatDateTime } from '../utils/datetime';
import { mediaProxyUrl } from '../api';

/* 社交媒体流 —— 阅读器第三容器(shape=social)的呈现形态。
   按 docs/design/dorami-social-quiet.html 样页复刻;设计决策见
   docs/social-x-wave-plan.md 第 3 节。要点:
   · 占「条目列 + 阅读窗」整幅、单列宽卡、全文直出(推文太短,配阅读窗会空旷);
   · 不展示点赞/转发/评论数 —— 抓取时刻的数字是永久快照,三天后仍显示当时数值
     等于主动展示错误信息(字段照常入库,只是不展示);
   · 时间戳即原推链接,右上角只放收藏/标读 —— 本系统对平台只有读权限;
   · 平台角标仅在订阅了 >=2 个平台时挂载(单平台时每卡同一图标是纯噪声)。 */

const parseExt = (article) => {
  try {
    const ext = JSON.parse(article?.extensions_json || '{}');
    return ext && typeof ext === 'object' ? ext : {};
  } catch {
    return {};
  }
};

// X 品牌标(平台角标用)。其它平台后续在此扩展。
function PlatformGlyph({ platform }) {
  if (platform === 'x') {
    return (
      <svg viewBox="0 0 24 24" fill="currentColor" width="9" height="9" aria-hidden="true">
        <path d="M18.9 1.2h3.7l-8 9.2 9.4 12.4h-7.4l-5.8-7.6-6.6 7.6H.5l8.6-9.8L0 1.2h7.6l5.2 6.9zM17.6 20.6h2L6.5 3.3H4.3z" />
      </svg>
    );
  }
  return <AtSign width="9" height="9" aria-hidden="true" />;
}

/* 图片网格:1/2/3/4 张四种布局。图一律经 mediaProxyUrl(媒体库),
   失败时回退原链直连,再失败才隐去 —— 与 ReaderMarkdown 的三层降级同构。 */
function SocialMedia({ urls }) {
  const list = useMemo(() => (Array.isArray(urls) ? urls.filter(Boolean).slice(0, 4) : []), [urls]);
  if (!list.length) return null;
  return (
    <div className={`social-media n${list.length}`}>
      {list.map((url, i) => (
        <SocialMediaItem key={`${url}-${i}`} url={url} />
      ))}
    </div>
  );
}

function SocialMediaItem({ url }) {
  const [src, setSrc] = useState(() => mediaProxyUrl(url));
  const [failed, setFailed] = useState(false);
  const handleError = useCallback(() => {
    setSrc((current) => {
      if (current !== url && url) return url; // 代理失败 → 原链直连
      setFailed(true);
      return current;
    });
  }, [url]);
  if (failed) return <span className="social-media-item is-failed" aria-hidden="true" />;
  return (
    <span className="social-media-item">
      <img src={src} alt="" loading="lazy" decoding="async" onError={handleError} />
    </span>
  );
}

/* 作者头像:真实平台头像优先(经媒体库代理缓存),失败逐级降级到
   原链 → 源的 LogoMark → handle 首字母色块。
   注:v3.12 初版曾决定「不引外链 avatar、一律用 LogoMark」,但社交源在
   LogoMark 里没有品牌条目,结果所有账号都退化成同一个 X 图标 —— 既不是品牌
   也不是头像。图床(v3.11)落地后代理链路已经现成,故改为用真实头像。 */
function SocialAvatar({ avatarUrl, source, fallbackName, className = '' }) {
  const [src, setSrc] = useState(() => (avatarUrl ? mediaProxyUrl(avatarUrl) : ''));
  const [failed, setFailed] = useState(false);
  const handleError = useCallback(() => {
    setSrc((current) => {
      if (avatarUrl && current !== avatarUrl) return avatarUrl; // 代理失败 → 原链直连
      setFailed(true);
      return current;
    });
  }, [avatarUrl]);

  if (avatarUrl && !failed) {
    return <img className={`social-avatar-img ${className}`} src={src} alt="" loading="lazy" decoding="async" onError={handleError} />;
  }
  if (source) {
    return <LogoMark company={resolveCompany(source)} size="s34" emoji={source.icon} className="social-avatar" />;
  }
  return <span className="social-initial" aria-hidden="true">{(fallbackName || '?').slice(0, 1).toUpperCase()}</span>;
}

/* 引用推:内缩嵌套卡。与转推的「顶部归属行」形态必须可区分,
   否则读者判断不了「这话是谁说的」。 */
function SocialQuote({ quoted }) {
  if (!quoted || (!quoted.text && !quoted.author_name)) return null;
  const quoteAvatar = quoted.author_avatar_url_large || quoted.author_avatar_url || '';
  const body = (
    <>
      <span className="social-quote-head">
        {quoteAvatar && (
          <img className="social-quote-avatar" src={mediaProxyUrl(quoteAvatar)} alt="" loading="lazy" decoding="async" />
        )}
        <span className="social-quote-name">{quoted.author_name || quoted.author_handle || '原推'}</span>
        {quoted.author_handle && <span className="social-quote-meta">@{quoted.author_handle}</span>}
      </span>
      {quoted.text && <span className="social-quote-text">{quoted.text}</span>}
    </>
  );
  if (!quoted.url) return <div className="social-quote">{body}</div>;
  return (
    <a className="social-quote" href={quoted.url} target="_blank" rel="noreferrer noopener">
      {body}
    </a>
  );
}

const CLAMP_CHARS = 360; // 超过此长度折叠(约样页的 7 行)

function SocialPost({
  article,
  source,
  sourceName,
  unread,
  favorite,
  favPending,
  readPending,
  showPlatform,
  onToggleFavorite,
  onToggleRead,
}) {
  const ext = useMemo(() => parseExt(article), [article]);
  const [expanded, setExpanded] = useState(false);

  // 转推:顶层 author_* 是转推者,reposted.* 是原作者(后端契约)。
  // 顶层 text 在转推时是 "RT @xxx: …" 截断形式,故正文取 reposted.text。
  const reposted = ext.reposted && typeof ext.reposted === 'object' ? ext.reposted : null;
  const quoted = ext.quoted && typeof ext.quoted === 'object' ? ext.quoted : null;

  const authorName = reposted?.author_name || ext.author_name || sourceName || article.source_id;
  const authorHandle = reposted?.author_handle || ext.author_handle || '';
  // 头像:大图优先(X 默认 _normal 是 48px,后端已派生 _400x400);转推取原作者的
  const avatarUrl = reposted
    ? (reposted.author_avatar_url_large || reposted.author_avatar_url || '')
    : (ext.author_avatar_url_large || ext.author_avatar_url || source?.avatar_url || '');
  const text = (reposted?.text || article.content || '').trim();
  const mediaUrls = reposted?.media_urls?.length ? reposted.media_urls : ext.media_urls;

  const clamped = !expanded && text.length > CLAMP_CHARS;
  const stamp = article.publish_date || article.fetched_date;

  return (
    <article className={`social-post ${unread ? '' : 'is-read'}`}>
      {reposted && (
        <p className="social-repost">
          <Repeat2 className="h-[13px] w-[13px]" aria-hidden="true" />
          {sourceName || article.source_id} 转推
        </p>
      )}
      <div className="social-body">
        <span className="social-avatar-wrap">
          {/* 转推时头像跟着「原作者」走,与名字/handle 保持同一主体 */}
          <SocialAvatar
            avatarUrl={avatarUrl}
            source={reposted ? null : source}
            fallbackName={authorName}
          />
          {showPlatform && (
            <span className="social-plat" title={platformLabelOf(ext.platform || source?.platform)}>
              <PlatformGlyph platform={ext.platform || source?.platform} />
            </span>
          )}
        </span>

        <div className="min-w-0">
          <div className="social-post-head">
            <span className="social-name">{authorName}</span>
            {authorHandle && <span className="social-handle">@{authorHandle}</span>}
            <span className="social-dot">·</span>
            {/* 时间戳即原推链接 —— 社交媒体通用语汇,零学习成本 */}
            {article.source_url ? (
              <a
                className="social-time"
                href={article.source_url}
                target="_blank"
                rel="noreferrer noopener"
                title={`在原平台打开 · ${formatDateTime(stamp)}`}
              >
                {formatRelativeTime(stamp, '')}
              </a>
            ) : (
              <span className="social-handle" title={formatDateTime(stamp)}>{formatRelativeTime(stamp, '')}</span>
            )}
            <span className="social-head-sp" />
            <span className="social-acts">
              <button
                type="button"
                className={`social-act ${favorite ? 'is-fav' : ''}`}
                aria-label={favorite ? '取消收藏' : '收藏'}
                title={favorite ? '取消收藏' : '收藏'}
                disabled={favPending}
                onClick={() => onToggleFavorite?.(article)}
              >
                {favPending
                  ? <Loader2 className="h-[15px] w-[15px] animate-spin" />
                  : <Star className="h-[15px] w-[15px]" fill={favorite ? 'currentColor' : 'none'} />}
              </button>
              <button
                type="button"
                className="social-act"
                aria-label={unread ? '标为已读' : '标为未读'}
                title={unread ? '标为已读' : '标为未读'}
                disabled={readPending}
                onClick={() => onToggleRead?.(article, unread)}
              >
                {readPending
                  ? <Loader2 className="h-[15px] w-[15px] animate-spin" />
                  : unread
                    ? <Check className="h-[15px] w-[15px]" />
                    : <Circle className="h-[15px] w-[15px]" />}
              </button>
            </span>
          </div>

          {text && <p className={`social-text ${clamped ? 'is-clamped' : ''}`}>{text}</p>}
          {text.length > CLAMP_CHARS && (
            <button type="button" className="social-more" onClick={() => setExpanded((v) => !v)}>
              {expanded ? '收起' : '显示更多'}
            </button>
          )}

          <SocialMedia urls={mediaUrls} />
          <SocialQuote quoted={quoted} />
        </div>
      </div>
    </article>
  );
}

export default function SocialFlow({
  articles = [],
  sourceMap = {},
  sourceNameMap = {},
  subscribedCount = 0,
  unreadCount = 0,
  unreadOnly = false,
  onUnreadOnlyChange,
  isArticleUnread,
  favoriteIds,
  favTogglingId,
  onToggleFavorite,
  favOnly = false,
  onFavOnlyChange,
  readTogglingId,
  onToggleRead,
  onMarkAllRead,
  markingRead = false,
  loading = false,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  platformCount = 1,
  activeSourceId = null,
  emptyHint = '暂无动态',
}) {
  const showPlatform = platformCount > 1;
  const scopeName = activeSourceId ? (sourceNameMap[activeSourceId] || activeSourceId) : null;

  return (
    <section className="reader-col reader-social" aria-label="社交媒体">
      <header className="reader-social-head">
        <span className="reader-social-title">{scopeName || '社交媒体'}</span>
        <span className="reader-social-sub">
          {favOnly
            ? '只看收藏'
            : scopeName
              ? (unreadCount > 0 ? `${unreadCount} 条未读` : '已读完')
              : `${subscribedCount} 个账号${unreadCount > 0 ? ` · ${unreadCount} 条未读` : ''}`}
        </span>
        {/* 与条目列头统一顺序:全部/未读 seg → 全部标读 → 只看收藏(收藏钮恒在最右)。
            收藏过滤时未读语义关闭,seg 与标读钮让位隐藏,只留收藏钮。 */}
        <div className="reader-social-actions">
          {!favOnly && (
            <>
              <div className="reader-seg" role="group" aria-label="未读过滤">
                <button
                  type="button"
                  className={`reader-seg-btn ${unreadOnly ? '' : 'is-on'}`}
                  aria-pressed={!unreadOnly}
                  onClick={() => onUnreadOnlyChange?.(false)}
                >
                  全部
                </button>
                <button
                  type="button"
                  className={`reader-seg-btn ${unreadOnly ? 'is-on' : ''}`}
                  aria-pressed={unreadOnly}
                  onClick={() => onUnreadOnlyChange?.(true)}
                >
                  未读
                </button>
              </div>
              <button
                type="button"
                className="reader-unread-icon"
                aria-label={activeSourceId ? '本来源全部标为已读' : '本容器全部标为已读'}
                title={activeSourceId ? '本来源全部标为已读' : '本容器全部标为已读'}
                disabled={markingRead || unreadCount === 0}
                onClick={() => onMarkAllRead?.()}
              >
                {markingRead
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <CheckCheck className="h-4 w-4" />}
              </button>
            </>
          )}
          <button
            type="button"
            aria-pressed={favOnly}
            aria-label={favOnly ? '退出收藏过滤' : '只看收藏'}
            title={favOnly ? '退出收藏过滤' : '只看收藏'}
            className={`reader-fav-icon ${favOnly ? 'is-on' : ''}`}
            onClick={() => onFavOnlyChange?.(!favOnly)}
          >
            <Star className="h-4 w-4" fill={favOnly ? 'currentColor' : 'none'} />
          </button>
        </div>
      </header>

      <div className="reader-social-scroll">
        <div className="reader-social-col">
          {loading ? (
            <div className="reader-empty reader-empty-tall">
              <Loader2 className="h-5 w-5 animate-spin text-slate-300" />
            </div>
          ) : articles.length === 0 ? (
            <div className="reader-empty reader-empty-tall">
              {favOnly ? <Star className="h-6 w-6 text-slate-300" /> : <AtSign className="h-6 w-6 text-slate-300" />}
              <span>
                {favOnly
                  ? '这里还没有收藏，卡片右上角点星标即可收藏'
                  : unreadOnly ? '没有未读动态，都看完啦' : emptyHint}
              </span>
            </div>
          ) : (
            <>
              {articles.map((article, index) => {
                const key = dayKeyOf(article);
                const showLabel = index === 0 || key !== dayKeyOf(articles[index - 1]);
                return (
                  <Fragment key={article.id}>
                    {showLabel && <div className="reader-social-day">{dayLabelOf(key)}</div>}
                    <SocialPost
                      article={article}
                      source={sourceMap[article.source_id]}
                      sourceName={sourceNameMap[article.source_id]}
                      unread={isArticleUnread ? isArticleUnread(article) : false}
                      favorite={favoriteIds?.has(article.id)}
                      favPending={favTogglingId === article.id}
                      readPending={readTogglingId === article.id}
                      showPlatform={showPlatform}
                      onToggleFavorite={onToggleFavorite}
                      onToggleRead={onToggleRead}
                    />
                  </Fragment>
                );
              })}
              {hasMore && (
                <button type="button" onClick={onLoadMore} disabled={loadingMore} className="reader-load-more">
                  {loadingMore ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {loadingMore ? '加载中…' : '加载更多'}
                </button>
              )}
              {!hasMore && articles.length > 0 && (
                <p className="reader-social-end">已到底部</p>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
