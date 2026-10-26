import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  AtSign,
  BookOpenText,
  CheckCheck,
  CircleDot,
  FileText,
  Inbox,
  Moon,
  Podcast,
  Settings,
  Star,
  Sun,
  X,
} from 'lucide-react';
import { ArticleRow } from '../../components/ReaderTab';
import Modal from '../../components/Modal';
import LogoMark from '../../components/LogoMark';
import BrandLogoImage from '../../components/BrandLogoImage';
import AiReadingCard from '../../components/AiReadingCard';
import PaperContentNote from '../../components/PaperContentNote';
import { useOverlayScrollbar } from '../../hooks/useOverlayScrollbar';
import { resolveCompany } from '../../sourceTaxonomy';
import { buildListPlan } from '../../utils/listPlan';
import { shouldShowAiReadingCard } from '../../utils/analysis';
import { READABLE_MAP, SOURCE_MAP, SOURCE_NAME_MAP } from './fixtures';
import { emptyListText, kindOptionsFor, listTitleOf, neighborsOf, sourceGroupsFor, unreadBySource } from './readerModel';
import { isActiveJob, shownKey } from './translationSim';
import ArticleBody, { ScrollAnchor } from './ArticleBody';
import { LanguageSeg, PaneHead, PreviewSocialFlow, ReadingSection } from './ReaderPieces';

const noop = () => {};

const VIEWS = [
  ['article', '文章', FileText, '文章'],
  ['podcast', '播客', Podcast, '播客'],
  ['social', '社交媒体', AtSign, '社交'],
];

// 桌面四栏(视图轨 · 源栏 · 条目列 · 阅读窗):结构与类名照搬 ReaderTab。
// 只保留样页里真能用的入口——早报、发现、榜单、分享、问哆啦美、收听依赖接口或另有页面,不画空按钮。
export default function ReaderDesktop({ m }) {
  const { mode } = m;
  return (
    <div className="reader-shell">
      <nav className="reader-vrail" aria-label="阅读视图">
        <BrandLogoImage displaySize={32} alt="哆啦美" className="reader-vrail-brand-img" />
        {VIEWS.map(([view, label, Icon, short]) => (
          <button
            key={view}
            type="button"
            aria-label={label}
            aria-pressed={mode === view}
            onClick={() => m.goView(view)}
            className={`reader-vrail-btn ${mode === view ? 'is-on' : ''}`}
          >
            <Icon className="h-[18px] w-[18px]" />
            <span className="reader-vrail-label">{short}</span>
            {short !== label && <span className="reader-vrail-tip">{label}</span>}
          </button>
        ))}
        <div className="reader-vrail-spring" />
        <button
          type="button"
          onClick={m.theme.toggle}
          className="reader-vrail-btn"
          aria-label={m.theme.effective === 'dark' ? '切换到亮色' : '切换到暗色'}
        >
          {m.theme.effective === 'dark' ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
          <span className="reader-vrail-label">主题</span>
          <span className="reader-vrail-tip">{m.theme.effective === 'dark' ? '切换亮色' : '切换暗色'}</span>
        </button>
        <button type="button" onClick={() => m.setSettingsOpen(true)} className="reader-vrail-btn" aria-label="设置">
          <Settings className="h-[18px] w-[18px]" />
          <span className="reader-vrail-label">设置</span>
        </button>
      </nav>

      <SourceColumn m={m} />
      {mode === 'social' ? <PreviewSocialFlow m={m} /> : (
        <>
          <ListColumn m={m} />
          <ReadColumn m={m} />
        </>
      )}

      <Modal
        open={m.settingsOpen}
        onClose={() => m.setSettingsOpen(false)}
        closeOnOverlay
        size="none"
        panelClassName="sett-cab"
        ariaLabel="设置"
      >
        <nav className="sett-nav" aria-label="设置分区">
          <div className="sett-nav-title">设置</div>
          <div className="sett-nav-sec">
            <div className="sett-nav-group">通用</div>
            <button type="button" className="sett-nav-btn is-on" aria-current="page">
              <BookOpenText /> 阅读
            </button>
          </div>
          <div className="sett-nav-spacer" />
          <div className="sett-nav-foot">哆啦美 · AI 资讯阅读器</div>
        </nav>
        <div className="sett-body">
          <div className="sett-head">
            <span className="sett-head-title">阅读</span>
            <span className="sett-head-hint">标题语言等阅读偏好</span>
            <button onClick={() => m.setSettingsOpen(false)} className="icon-button ml-auto h-8 w-8" aria-label="关闭设置">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="sett-scroll">
            <ReadingSection zhTitle={m.zhTitle} onChange={m.setZhTitle} />
          </div>
        </div>
      </Modal>
    </div>
  );
}

function SourceColumn({ m }) {
  const { mode } = m;
  const groups = sourceGroupsFor(mode);
  const counts = unreadBySource(mode, m.unread);
  const pick = (id) => m.setSourceFilter((current) => (current === id ? null : id));
  return (
    <aside className="reader-col reader-col-sources">
      <div className="reader-sources-inner">
        <div className="reader-src-head">
          <span className="reader-src-title">{mode === 'social' ? '社交媒体' : mode === 'podcast' ? '播客' : '文章'}</span>
        </div>
        <div className="reader-source-scroll">
          {groups.map(({ key, label, list }) => (
            <section className="reader-subs" key={key}>
              <div className="reader-src-label">{label}</div>
              <div className="reader-group-body">
                {list.map((source) => {
                  const active = m.sourceFilter === source.source_id;
                  const unread = counts[source.source_id] || 0;
                  return (
                    <div
                      key={source.source_id}
                      role="button"
                      tabIndex={0}
                      aria-pressed={active}
                      onClick={() => pick(source.source_id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(source.source_id); }
                      }}
                      className={`reader-source-row ${active ? 'reader-source-row-active' : ''} ${unread > 0 ? 'has-unread' : ''}`}
                    >
                      <LogoMark company={resolveCompany(source)} size="s20" emoji={source.icon} />
                      <p className="reader-source-name min-w-0 flex-1">{source.name}</p>
                      {unread > 0 && <span className="sr-only">，有未读</span>}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </div>
    </aside>
  );
}

function ListColumn({ m }) {
  const { mode, visibleItems, displayFor, select, toggleFav } = m;
  const podcastView = mode === 'podcast';
  const listRef = useRef(null);
  const thumbRef = useRef(null);
  const resync = useOverlayScrollbar(listRef, thumbRef, true);
  const kindOptions = kindOptionsFor(mode, m.sourceFilter);
  // 条目行是现网 ArticleRow 原件:只把标题换成 displayTitleOf 的结果,其余字段原样。
  // 生产 ArticleRow 会直接读 title_zh(服务端只给有效译名),样页 fixture 的 title_zh 要看 title_state,故这里清掉
  const rows = useMemo(
    () => visibleItems.map((item) => ({ ...item, title: displayFor(item).text, title_zh: null })),
    [visibleItems, displayFor],
  );
  const plan = useMemo(() => buildListPlan(rows, true), [rows]);
  useEffect(() => { resync(); }, [rows, resync]);
  const onSelect = useCallback((row) => select(row.id), [select]);
  const onToggleFavorite = useCallback((row) => toggleFav(row.id), [toggleFav]);
  const markAllLabel = m.sourceFilter ? '本来源全部标为已读' : '本容器全部标为已读';

  return (
    <section className="reader-col reader-col-list">
      <div className="reader-list-inner">
        <div className="reader-list-head">
          <span className="reader-list-title">
            {listTitleOf(m.sourceFilter)}
            {m.favOnly && <span className="reader-list-subtitle">收藏</span>}
          </span>
          <div className="reader-seg" role="tablist" aria-label="未读筛选">
            {[[false, '全部'], [true, '未读']].map(([value, label]) => (
              <button
                key={label}
                type="button"
                role="tab"
                aria-selected={m.unreadOnly === value}
                onClick={() => m.setUnreadOnly(value)}
                className={`reader-seg-btn ${m.unreadOnly === value ? 'is-on' : ''}`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => m.setFavOnly((value) => !value)}
            aria-pressed={m.favOnly}
            aria-label={m.favOnly ? '取消只看收藏' : '只看收藏'}
            title={m.favOnly ? '取消只看收藏' : '只看收藏'}
            className={`reader-unread-icon reader-fav-toggle ${m.favOnly ? 'is-on' : ''}`}
          >
            <Star className="h-4 w-4" fill={m.favOnly ? 'currentColor' : 'none'} />
          </button>
          {!m.kindFilter && (
            <button
              type="button"
              onClick={m.markAllRead}
              aria-label={markAllLabel}
              title={markAllLabel}
              className="reader-unread-icon"
            >
              <CheckCheck className="h-4 w-4" />
            </button>
          )}
        </div>

        {kindOptions.length > 0 && (
          <div className="reader-kind-band" role="group" aria-label="按内容类型筛选">
            <button
              type="button"
              aria-pressed={!m.kindFilter}
              onClick={() => m.setKindFilter('')}
              className={`reader-kind-chip ${!m.kindFilter ? 'is-on' : ''}`}
            >
              全部类型
            </button>
            {kindOptions.map((option) => (
              <button
                key={option.kind}
                type="button"
                aria-pressed={m.kindFilter === option.kind}
                onClick={() => m.setKindFilter(m.kindFilter === option.kind ? '' : option.kind)}
                className={`reader-kind-chip ${m.kindFilter === option.kind ? 'is-on' : ''}`}
              >
                {option.label}
              </button>
            ))}
          </div>
        )}

        <div className="reader-scrollwrap">
          <div className="reader-list-scroll" ref={listRef}>
            {rows.length === 0 ? (
              <div className="reader-empty">
                {m.favOnly ? <Star className="h-6 w-6 text-slate-300" /> : <Inbox className="h-6 w-6 text-slate-300" />}
                <span>{emptyListText({ mode, favOnly: m.favOnly, unreadOnly: m.unreadOnly, sourceFilter: m.sourceFilter, kindFilter: m.kindFilter })}</span>
              </div>
            ) : (
              <div key={`${m.sourceFilter ?? '__all__'}|${mode}|${m.favOnly ? 'f' : ''}|${m.kindFilter}`} className="reader-list-enter">
                {plan.map((entry) => (
                  <ArticleRow
                    key={entry.article.id}
                    article={entry.article}
                    active={m.activeId === entry.article.id}
                    isUnread={m.unread.has(entry.article.id)}
                    isFav={m.fav.has(entry.article.id)}
                    entryPodcast={podcastView}
                    showLabel={entry.showLabel}
                    dayKey={entry.dayKey}
                    searchQuery=""
                    source={SOURCE_MAP[entry.article.source_id]}
                    sourceName={SOURCE_NAME_MAP[entry.article.source_id] || entry.article.source_id}
                    onSelect={onSelect}
                    onPrefetchEnter={noop}
                    onPrefetchLeave={noop}
                    onToggleFavorite={onToggleFavorite}
                    onContextMenu={noop}
                    ctxAnchor={false}
                  />
                ))}
              </div>
            )}
          </div>
          <div ref={thumbRef} className="ovl-thumb" aria-hidden="true" />
        </div>
      </div>
    </section>
  );
}

function ReadColumn({ m }) {
  const paneRef = useRef(null);
  const active = m.activeId ? READABLE_MAP[m.activeId] : null;
  if (!active) {
    return (
      <section className="reader-col reader-col-read">
        <div className="reader-empty reader-empty-read">
          <BookOpenText className="h-8 w-8 text-slate-300" />
          <span>{m.mode === 'podcast' ? '选择一期播客以开始收听' : '选择一篇文章以开始阅读'}</span>
        </div>
      </section>
    );
  }

  const podcast = active.content_type === 'podcast_episode';
  const job = m.jobs[active.id];
  const zhView = m.zhView?.id === active.id;
  const translatable = active.body.lang === 'en';
  const display = m.displayFor(active);
  const source = SOURCE_MAP[active.source_id];
  const sourceName = SOURCE_NAME_MAP[active.source_id] || active.source_id;
  const isFav = m.fav.has(active.id);
  const isUnread = m.unread.has(active.id);
  const { index, prev, next } = neighborsOf(m.visibleItems, active.id);
  // 锚点只在「读者看得见的变化」时介入:视图、显示方式、已显示哪些片、任务状态、标题文字
  const view = zhView && job ? 'zh' : 'orig';
  const version = [view, m.displayMode, shownKey(view, m.displayMode, job), job?.status, display.text].join('|');
  const actions = translatable ? (
    <div className="reader-pane-actions">
      <LanguageSeg
        podcast={podcast}
        hasJob={Boolean(job)}
        running={isActiveJob(job)}
        zhView={zhView}
        onOriginal={m.showOriginal}
        onChinese={() => m.showChinese(active)}
      />
    </div>
  ) : null;

  return (
    <section className="reader-col reader-col-read">
      <div className="reader-progress" aria-hidden="true" />
      <div className="reader-pane-bar">
        <div className="reader-crumb">
          <LogoMark company={resolveCompany(source)} size="s17" emoji={source?.icon} />
          <span className="reader-crumb-name">{sourceName}</span>
        </div>
        <button
          type="button"
          onClick={() => m.toggleFav(active.id)}
          title={isFav ? '取消收藏' : '收藏'}
          aria-label={isFav ? '取消收藏' : '收藏'}
          className={`reader-pane-iconbtn ${isFav ? 'is-amber' : ''}`}
        >
          <Star className="h-4 w-4" fill={isFav ? 'currentColor' : 'none'} />
        </button>
        <button
          type="button"
          onClick={() => m.toggleRead(active.id)}
          title={isUnread ? '标为已读' : '标为未读(撤销已读)'}
          aria-label={isUnread ? '标为已读' : '标为未读'}
          className="reader-pane-iconbtn"
        >
          {isUnread ? <CheckCheck className="h-4 w-4" /> : <CircleDot className="h-4 w-4" />}
        </button>
      </div>

      <article className="reader-pane reader-enter" key={`${active.id}:${m.paneEpoch}`} ref={paneRef}>
        <ScrollAnchor scrollRef={paneRef} version={version} view={view}>
          <PaneHead article={active} sourceName={sourceName} display={display} actions={actions} podcast={podcast} />
          <div className="reader-pane-body">
            {shouldShowAiReadingCard(active, { summary: active.summary_zh, aiEnabled: false }) && (
              <AiReadingCard
                article={active}
                summary={active.summary_zh}
                summarizing={false}
                canGenerate={false}
                onGenerate={noop}
                podcast={podcast}
              />
            )}
            {!podcast && <PaperContentNote article={active} />}
            {podcast && (
              <div className="podcast-show-notes-head">
                <h2 className="section-title">节目简介</h2>
                <span>来源方提供</span>
              </div>
            )}
            <ArticleBody
              article={active}
              job={job}
              zhView={zhView}
              zhEntry={zhView ? m.zhView : null}
              displayMode={m.displayMode}
              onResume={() => m.actions.resume(active.id)}
            />
          </div>
        </ScrollAnchor>
        {index >= 0 && (prev || next) && (
          <nav className="reader-pager" aria-label="上一篇 / 下一篇">
            <button type="button" className="reader-pager-btn" disabled={!prev} onClick={() => prev && m.select(prev.id)}>
              <span className="reader-pager-dir">← 上一篇</span>
              <span className="reader-pager-title">{prev ? m.displayFor(prev).text : '已是最新一篇'}</span>
            </button>
            <button type="button" className="reader-pager-btn reader-pager-next" disabled={!next} onClick={() => next && m.select(next.id)}>
              <span className="reader-pager-dir">下一篇 →</span>
              <span className="reader-pager-title">{next ? m.displayFor(next).text : '已到列表末尾'}</span>
            </button>
          </nav>
        )}
      </article>
    </section>
  );
}
