import { Fragment, useMemo, useState } from 'react';
import { CheckCheck, Circle, CircleDot, CloudOff, Inbox, Loader2, Star, Tags } from 'lucide-react';
import LayerPage from '../layer/LayerPage';
import LogoMark from '../LogoMark';
import SocialFlow from '../SocialFlow';
import { ArticleRow, ArticleCardsSkeleton } from '../ReaderTab';
import { HeadBack, MobileHeader, TitleDial } from './MobileHeader';
import { useArticleList } from '../../hooks/useArticleList';
import { buildListPlan } from '../../utils/listPlan';
import { dayLabelOf } from '../../utils/readerTime';
import { pushListQuery, pushMarkReadPlan, pushMode, pushViewItems } from '../../utils/articleList';
import { resolveCompany, roleLabelOf } from '../../sourceTaxonomy';
import { mediaProxyUrl } from '../../api';

const noopContextMenu = () => {};
const SHAPE_LABEL = { article: '文章', podcast: '播客', social: '社交' };
const FACET_LABEL = { topic: '主题', industry: '行业', entity: '实体' };
// 页头与列表各自重画的滚动区:条目流 / 社交流
const FLOW_SCROLLERS = '.m-flow-scroll, .reader-social-scroll';

/**
 * 订阅 Tab 的推入页(issue #230 阶段 3 批 3a;样页「订阅 · 来源流」,拍板 19 / 24):
 * 点来源 / 全部 / 收藏 / 兴趣,从右推入整页(盖住底栏),页内是**第二个列表实例**(hooks/useArticleList)——
 * 视图、只看未读、全部标读、翻页与滚动都只归这一页,内容 Tab 的列表状态与滚动位置不被改写。
 * 页头一行:返回钮在头像原位 ·(全部 / 收藏 / 兴趣)标题位转盘 · 右侧胶囊(只看未读 · 全部标读);
 * 切视图只换列表与胶囊开关态,页头元素不重挂,转盘的原位放大才有过渡。来源头图(logo + 名称 + 角色)是内容身份,在列表最上面。
 * 读一篇 = 主数据层的 selectArticle(正文页由 MobileReader 叠在本页之上;返回后本页原位还在)。
 * 出口:返回钮 / Esc / 系统返回(层原语 LayerPage 入层栈)。
 */
export default function MobileSubsFlow({
  spec,
  rs,
  showToast,
  onBack,
  aiEnabled = false,
  pressBind,
  socialPressBind = null,   // 社交卡长按(useLongPress 的 bind;批 4a,审计 M-04)
  sheetAnchorKey = null,
  playingId = '',
  playingState = '',
  playingAi = false,
  subscribeSourceById,
}) {
  const {
    isGuest, shapeOfSource, sourceMap, sourceNameMap, subscribedIds, subscribedSources, favoriteIds, favTogglingId,
    handleToggleFavorite, handleSubscribe, pinningId, isArticleUnread, selectArticle, schedulePrefetch, cancelPrefetch,
    socialReadToggling, handleToggleSocialRead, markingRead, markListAllRead, platformCount,
    socialTranslations, toggleSocialTranslation, listRegistry,
  } = rs;
  const kind = spec.kind;
  const views = pushViewItems(kind);
  const [view, setView] = useState(views ? views[0][0] : 'article');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const mode = pushMode(spec, view, shapeOfSource);
  const query = useMemo(() => pushListQuery(spec, { mode, unreadOnly }), [spec, mode, unreadOnly]);
  const source = kind === 'source' ? (sourceMap[spec.id] || null) : null;
  const hidden = Boolean(source?.hidden);
  const list = useArticleList(query, { registry: listRegistry, hidden, showToast });
  const { articles, articlesLoading, loadingMore, hasMore, handleLoadMore, listRef, sentinelRef } = list;
  const listPlan = useMemo(() => buildListPlan(articles, true), [articles]);
  const plan = pushMarkReadPlan(spec, mode);
  const interestish = kind === 'interest' || kind === 'tag';
  const social = mode === 'social';
  const podcast = mode === 'podcast';

  const changeView = (next) => {
    if (next === view) return;
    setView(next);
    setScrolled(false);   // 换视图回到顶(列表实例清场时滚动区归零)
  };
  const onScrollCapture = (event) => {
    const el = event.target;
    if (el instanceof Element && el.matches(FLOW_SCROLLERS)) setScrolled(el.scrollTop > 4);
  };
  // 标完各实例(含内容 Tab 主列表)由数据层一起对齐、只看未读的重拉(useReaderState.markListAllRead)
  const markAll = () => markListAllRead(plan, { listArticles: articles });

  // ── 标题与副标(来源头图 / 「全部」「收藏」「兴趣」的名字与一行说明) ──
  let title;
  let sub;
  if (kind === 'source') {
    title = sourceNameMap[spec.id] || source?.name || spec.id;
    const state = hidden ? '暂不可用'
      : subscribedIds.has(spec.id) ? (isGuest ? '默认订阅' : '已订阅') : '未订阅';
    sub = [source ? roleLabelOf(source) : '', SHAPE_LABEL[mode] || '文章', state].filter(Boolean).join(' · ');
  } else if (kind === 'fav') {
    title = '收藏';
    sub = `共 ${favoriteIds.size} 条`;
  } else if (kind === 'interest') {
    title = '全部兴趣内容';
    sub = spec.count ? `全站 · ${spec.count} 个兴趣` : '全站';
  } else if (kind === 'tag') {
    title = spec.name || '兴趣';
    sub = `兴趣 · ${FACET_LABEL[spec.facet] || '标签'}`;
  } else {
    title = '全部';
    sub = `${subscribedSources.length} 个来源`;
  }
  let lead = null;
  if (kind === 'source') {
    lead = source?.avatar_url
      ? <img className="m-flow-avatar" src={mediaProxyUrl(source.avatar_url)} alt="" draggable={false} />
      : <LogoMark company={resolveCompany(source || { source_id: spec.id, name: title })} size="md" emoji={source?.icon} />;
  } else if (kind === 'tag') {
    lead = <span className="m-flow-tagdot" aria-hidden="true" />;
  }
  const hero = (
    <div className="m-flow-hero">
      {lead}
      <h1 className="m-flow-title">{title}</h1>
      <p className="m-flow-sub">
        <span>{sub}</span>
        {unreadOnly && <em>只看未读</em>}
      </p>
    </div>
  );
  // 预览未订阅的来源(发现页「预览」进来):显眼「＋ 订阅」横幅,订阅成功即消失
  const banner = kind === 'source' && !subscribedIds.has(spec.id) && !hidden ? (
    <button
      type="button"
      className="reader-sub-banner m-flow-banner"
      disabled={pinningId === spec.id}
      onClick={() => handleSubscribe(source || { source_id: spec.id, name: title })}
    >
      {pinningId === spec.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
      订阅「{title}」
    </button>
  ) : null;

  let emptyText;
  if (hidden) emptyText = kind === 'source' && social ? '该账号暂时不可用' : '该来源暂时不可用';
  else if (unreadOnly) emptyText = social ? '没有未读动态，都看完啦' : '没有未读内容，都看完啦';
  else if (kind === 'source') emptyText = social ? '暂无动态' : '该来源暂无内容';
  else if (kind === 'fav') emptyText = '当前范围还没有收藏，阅读时点星标即可收藏';
  else if (kind === 'tag') emptyText = '还没有命中这个兴趣的内容';
  else if (kind === 'interest') emptyText = '还没有命中兴趣的内容';
  else emptyText = social ? '暂无动态' : podcast ? '暂无播客' : '暂无文章';
  const EmptyIcon = hidden ? CloudOff : kind === 'fav' ? Star : interestish ? Tags : Inbox;

  return (
    <LayerPage
      className={`m-page m-flow ${scrolled ? 'is-scrolled' : ''}`}
      role="dialog"
      modal
      label={title}
      onClose={onBack}
      onScrollCapture={onScrollCapture}
      data-own-list=""
      exit="push"
    >
      <MobileHeader
        lead={<HeadBack label="返回订阅" onClick={onBack} />}
        title={views ? <TitleDial items={views} value={mode} onChange={changeView} label="内容视图" actions={2} /> : null}
        actions={(
          <div className="m-gpill">
            <button
              type="button"
              className={`m-gpill-btn m-unread ${unreadOnly ? 'is-on' : ''}`}
              aria-label="只看未读"
              aria-pressed={unreadOnly}
              onClick={() => setUnreadOnly((value) => !value)}
            >
              {unreadOnly ? <CircleDot aria-hidden="true" /> : <Circle aria-hidden="true" />}
            </button>
            {/* 收藏页没有这枚钮:后端没有「收藏范围」的标读,推水位会把没收藏的一并标掉(utils/articleList.pushMarkReadPlan) */}
            {plan && (
              <button
                type="button"
                className="m-gpill-btn"
                aria-label="全部标为已读"
                disabled={markingRead}
                onClick={markAll}
              >
                {markingRead ? <Loader2 className="animate-spin" aria-hidden="true" /> : <CheckCheck aria-hidden="true" />}
              </button>
            )}
          </div>
        )}
      />
      {social ? (
        <div key="social" className="m-social-host m-flow-social">
          <SocialFlow
            topSlot={<>{hero}{banner}</>}
            articles={articles}
            sourceMap={sourceMap}
            sourceNameMap={sourceNameMap}
            unreadOnly={unreadOnly}
            onUnreadOnlyChange={setUnreadOnly}
            isArticleUnread={isArticleUnread}
            favoriteIds={favoriteIds}
            favTogglingId={favTogglingId}
            onToggleFavorite={handleToggleFavorite}
            favOnly={kind === 'fav'}
            readTogglingId={socialReadToggling}
            onToggleRead={handleToggleSocialRead}
            pressBind={socialPressBind}
            ctxAnchorKey={sheetAnchorKey}
            onMarkAllRead={markAll}
            markingRead={markingRead}
            loading={articlesLoading}
            hasMore={hasMore}
            loadingMore={loadingMore}
            onLoadMore={handleLoadMore}
            platformCount={platformCount}
            activeSourceId={kind === 'source' ? spec.id : null}
            headless
            emptyHint={emptyText}
            translateEnabled={aiEnabled}
            translations={socialTranslations}
            onToggleTranslation={toggleSocialTranslation}
          />
        </div>
      ) : (
        <div key="list" className="m-flow-scroll" ref={listRef}>
          {hero}
          {banner}
          <div key={mode} className="m-flow-body">
            {articlesLoading ? (
              <ArticleCardsSkeleton card={podcast ? 'podcast' : 'article'} />
            ) : hidden || articles.length === 0 ? (
              <div className="reader-empty">
                <EmptyIcon className="h-6 w-6 text-slate-300" />
                <span>{emptyText}</span>
              </div>
            ) : (
              <>
                {listPlan.map((entry) => {
                  const { article } = entry;
                  // 夜航卡(批 4c):与内容 Tab 同一种卡与日期组头(组头在长按包装之外)
                  return (
                    <Fragment key={article.id}>
                      {entry.showLabel && <div className="m-day">{dayLabelOf(entry.dayKey)}</div>}
                      <div className="m-press" {...pressBind(article)}>
                        <ArticleRow
                          article={article}
                          active={false}
                          isUnread={isArticleUnread(article)}
                          isFav={favoriteIds.has(article.id)}
                          entryPodcast={podcast}
                          showLabel={false}
                          dayKey={entry.dayKey}
                          searchQuery=""
                          source={sourceMap[article.source_id]}
                          sourceName={sourceNameMap[article.source_id] || article.source_id}
                          onSelect={selectArticle}
                          onPrefetchEnter={schedulePrefetch}
                          onPrefetchLeave={cancelPrefetch}
                          onToggleFavorite={handleToggleFavorite}
                          onContextMenu={noopContextMenu}
                          ctxAnchor={sheetAnchorKey === `article:${article.id}`}
                          favIndicator
                          card
                          interestHit={kind !== 'tag' ? (article.interest_hits?.[0] || '') : ''}
                          unsubscribed={interestish && !subscribedIds.has(article.source_id)}
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
                    {loadingMore && <ArticleCardsSkeleton count={3} delayed={false} card={podcast ? 'podcast' : 'article'} />}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </LayerPage>
  );
}
