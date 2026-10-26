import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AtSign,
  BookOpenText,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  FileText,
  Inbox,
  Loader2,
  Podcast,
  Search,
  Settings,
  Star,
  SunMoon,
  UserRound,
  X,
} from 'lucide-react';
import { ArticleRow } from '../../components/ReaderTab';
import AiReadingCard from '../../components/AiReadingCard';
import PaperContentNote from '../../components/PaperContentNote';
import { useLayerHistory } from '../../hooks/useLayerHistory';
import { buildListPlan } from '../../utils/listPlan';
import { shouldShowAiReadingCard } from '../../utils/analysis';
import { READABLE_MAP, SOURCE_MAP, SOURCE_NAME_MAP } from './fixtures';
import { emptyListText, kindOptionsFor, listTitleOf, neighborsOf } from './readerModel';
import { isActiveJob, shownKey } from './translationSim';
import ArticleBody, { ScrollAnchor } from './ArticleBody';
import { PaneHead, PreviewSocialFlow, ReadingSection } from './ReaderPieces';

const noop = () => {};

// 早报 / 榜单依赖接口,样页不放这两个 Tab(不画点了没反应的入口)
const TABS = [
  ['article', '文章', FileText],
  ['podcast', '播客', Podcast],
  ['social', '社交', AtSign],
  ['me', '我的', UserRound],
];

// 手机单列:结构与类名照搬 MobileReader / MobileArticlePage / MobileSettings。
// 选中一篇即压入正文页(默认就停在正文页),返回 = 回列表;返回键经 useLayerHistory 关层。
export default function ReaderMobile({ m }) {
  const tab = m.meOpen ? 'me' : m.mode;
  const listView = tab === 'article' || tab === 'podcast';
  const readOpen = listView && Boolean(m.activeId);
  const [settingsSection, setSettingsSection] = useState(null);
  const { select, setSettingsOpen } = m;

  const closeArticle = useCallback(() => select(null), [select]);
  const closeSettings = useCallback(() => {
    setSettingsOpen(false);
    setSettingsSection(null);
  }, [setSettingsOpen]);
  useLayerHistory(readOpen, closeArticle);
  useLayerHistory(m.settingsOpen, closeSettings);
  useLayerHistory(m.settingsOpen && Boolean(settingsSection), () => setSettingsSection(null));

  const socialSearchOpen = tab === 'social' && m.socialSearch.open;

  return (
    <div className="m-shell font-sans">
      {tab === 'me' ? (
        <div className="m-topbar"><span className="m-title m-title-solo">我的</span></div>
      ) : (
        <div className="m-topbar">
          {socialSearchOpen ? (
            <div className="m-search-inline">
              <Search aria-hidden="true" />
              <input
                type="text"
                value={m.socialSearch.input}
                onChange={(e) => m.setSocialSearch((current) => ({ ...current, input: e.target.value }))}
                placeholder="搜索我的阅读…"
                aria-label="搜索社交动态"
                autoFocus
              />
            </div>
          ) : (
            <span className="m-title m-title-solo">
              {listTitleOf(m.sourceFilter)}
              {m.favOnly && <span className="m-title-sub">收藏</span>}
            </span>
          )}
          <div className="m-topbar-sp" />
          {!socialSearchOpen && (
            <>
              <div className="mini-seg" role="tablist" aria-label="未读筛选">
                {[[false, '全部'], [true, '未读']].map(([value, label]) => (
                  <button
                    key={label}
                    type="button"
                    role="tab"
                    aria-selected={m.unreadOnly === value}
                    onClick={() => m.setUnreadOnly(value)}
                    className={`mini-seg-btn ${m.unreadOnly === value ? 'is-on' : ''}`}
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
                className={`m-iconbtn ${m.favOnly ? 'is-amber' : ''}`}
              >
                <Star fill={m.favOnly ? 'currentColor' : 'none'} />
              </button>
              {!m.kindFilter && (
                <button
                  type="button"
                  onClick={m.markAllRead}
                  aria-label={m.sourceFilter ? '本来源全部标为已读' : '本容器全部标为已读'}
                  className="m-iconbtn"
                >
                  <CheckCheck />
                </button>
              )}
            </>
          )}
          {tab === 'social' && (
            <button
              type="button"
              onClick={() => m.setSocialSearch((current) => ({ open: !current.open, input: '' }))}
              aria-pressed={socialSearchOpen}
              aria-label={socialSearchOpen ? '关闭搜索' : '搜索'}
              className={`m-iconbtn ${socialSearchOpen ? 'is-blue' : ''}`}
            >
              {socialSearchOpen ? <X /> : <Search />}
            </button>
          )}
        </div>
      )}

      <div className="m-content">
        {tab === 'me' ? (
          <MePage m={m} onOpenSettings={() => setSettingsOpen(true)} />
        ) : tab === 'social' ? (
          <div className="m-social-host">
            <PreviewSocialFlow m={m} headless />
          </div>
        ) : (
          <MobileList m={m} />
        )}
      </div>

      <nav className="m-tabbar" aria-label="阅读视图">
        {TABS.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => (id === 'me' ? m.setMeOpen(true) : m.goView(id))}
            aria-label={label}
            aria-current={tab === id ? 'page' : undefined}
            className={`m-tab ${tab === id ? 'is-on' : ''}`}
          >
            <span className="m-tab-ic"><Icon /></span>
            <span className="m-tab-label">{label}</span>
          </button>
        ))}
      </nav>

      {readOpen && <MobileArticle m={m} onBack={closeArticle} />}

      {m.settingsOpen && (
        <div className="m-page m-sett" role="dialog" aria-modal="true" aria-label="设置">
          {settingsSection === 'reading' ? (
            <>
              <div className="m-topbar on-pane">
                <button type="button" className="m-iconbtn" onClick={() => setSettingsSection(null)} aria-label="返回设置">
                  <ChevronLeft />
                </button>
                <span className="m-title">阅读</span>
              </div>
              <div className="m-page-scroll m-sett-body">
                <ReadingSection zhTitle={m.zhTitle} onChange={m.setZhTitle} />
              </div>
            </>
          ) : (
            <>
              <div className="m-topbar on-pane">
                <button type="button" className="m-iconbtn" onClick={closeSettings} aria-label="关闭设置">
                  <X />
                </button>
                <span className="m-title">设置</span>
              </div>
              <div className="m-page-scroll m-sett-list">
                <section>
                  <div className="m-sett-group">通用</div>
                  <div className="m-group">
                    <button type="button" className="m-row" onClick={() => setSettingsSection('reading')}>
                      <BookOpenText aria-hidden="true" />
                      <span className="m-row-label">阅读</span>
                      <span className="m-row-meta m-sett-hint">标题语言等阅读偏好</span>
                      <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
                    </button>
                  </div>
                </section>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MobileList({ m }) {
  const { mode, visibleItems, displayFor, select, toggleFav } = m;
  const podcastView = mode === 'podcast';
  const kindOptions = kindOptionsFor(mode, m.sourceFilter);
  // 生产 ArticleRow 会直接读 title_zh,样页 fixture 的 title_zh 要看 title_state,故交给它前清掉
  const rows = useMemo(
    () => visibleItems.map((item) => ({ ...item, title: displayFor(item).text, title_zh: null })),
    [visibleItems, displayFor],
  );
  const plan = useMemo(() => buildListPlan(rows, true), [rows]);
  const onSelect = useCallback((row) => select(row.id), [select]);
  const onToggleFavorite = useCallback((row) => toggleFav(row.id), [toggleFav]);
  return (
    <div className="m-list">
      {kindOptions.length > 0 && (
        <div className="reader-kind-band m-kind-band" role="group" aria-label="按内容类型筛选">
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
      {rows.length === 0 ? (
        <div className="reader-empty">
          {m.favOnly ? <Star className="h-6 w-6 text-slate-300" /> : <Inbox className="h-6 w-6 text-slate-300" />}
          <span>{emptyListText({ mode, favOnly: m.favOnly, unreadOnly: m.unreadOnly, sourceFilter: m.sourceFilter, kindFilter: m.kindFilter })}</span>
        </div>
      ) : (
        <div key={`${m.sourceFilter ?? '__all__'}|${mode}|${m.favOnly ? 'f' : ''}|${m.kindFilter}`}>
          {plan.map((entry) => (
            <div key={entry.article.id} className="m-press">
              <ArticleRow
                article={entry.article}
                active={false}
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
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MobileArticle({ m, onBack }) {
  const scrollRef = useRef(null);
  const backRef = useRef(null);
  const active = READABLE_MAP[m.activeId];
  const { keyboardRef } = m;
  // 键盘打开正文页时把焦点放到返回钮(指针操作不移焦点,避免 Safari 画多余焦点环)
  useEffect(() => {
    if (keyboardRef.current) backRef.current?.focus();
  }, [m.activeId, keyboardRef]);
  if (!active) return null;

  const podcast = active.content_type === 'podcast_episode';
  const job = m.jobs[active.id];
  const zhView = m.zhView?.id === active.id;
  const translatable = active.body.lang === 'en';
  const running = isActiveJob(job);
  const display = m.displayFor(active);
  const sourceName = SOURCE_NAME_MAP[active.source_id] || active.source_id;
  const isFav = m.fav.has(active.id);
  const { index, prev, next } = neighborsOf(m.visibleItems, active.id);
  const view = zhView && job ? 'zh' : 'orig';
  const version = [view, m.displayMode, shownKey(view, m.displayMode, job), job?.status, display.text].join('|');
  const translateLabel = zhView ? '显示原文' : job ? '显示中文' : podcast ? '将节目简介译为中文' : '译为中文';

  return (
    <div className="m-read" role="region" aria-label="正文">
      <div className="m-topbar on-pane">
        <button ref={backRef} type="button" className="m-iconbtn" onClick={onBack} aria-label="返回列表">
          <ChevronLeft />
        </button>
        <div className="m-topbar-sp" />
        <button
          type="button"
          className={`m-iconbtn ${isFav ? 'is-amber' : ''}`}
          onClick={() => m.toggleFav(active.id)}
          title={isFav ? '取消收藏' : '收藏'}
          aria-label={isFav ? '取消收藏' : '收藏'}
        >
          <Star fill={isFav ? 'currentColor' : 'none'} />
        </button>
        {/* 译钮:切的是正文语言;标题跟着「默认显示中文标题」走,不随这里变化。
            后台任务排队 / 进行中时圈圈替换「译」字(不论当前看原文还是中文),停止即恢复 */}
        {translatable && (
          <button
            type="button"
            className={`m-iconbtn ${zhView ? 'is-ai' : ''}`}
            onClick={zhView ? m.showOriginal : () => m.showChinese(active)}
            title={translateLabel}
            aria-label={running ? `${translateLabel}（翻译中）` : translateLabel}
            aria-pressed={zhView}
          >
            {running ? <Loader2 className="animate-spin" /> : <span className="reader-tr-glyph" aria-hidden="true">译</span>}
          </button>
        )}
      </div>

      <div className="m-read-progress" aria-hidden="true" />

      <div className="m-read-scroll" key={`${active.id}:${m.paneEpoch}`} ref={scrollRef}>
        <ScrollAnchor scrollRef={scrollRef} version={version} view={view}>
          <PaneHead article={active} sourceName={sourceName} display={display} podcast={podcast} mobile />
          <div className="m-read-body">
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
          <nav className="m-read-pager" aria-label="上一篇 / 下一篇">
            <button type="button" className="m-pager-btn" disabled={!prev} onClick={() => prev && m.select(prev.id)}>
              <span className="m-pager-k">上一篇</span>
              <span className="m-pager-t">{prev ? m.displayFor(prev).text : '已是最新一篇'}</span>
            </button>
            <button type="button" className="m-pager-btn is-next" disabled={!next} onClick={() => next && m.select(next.id)}>
              <span className="m-pager-k">下一篇</span>
              <span className="m-pager-t">{next ? m.displayFor(next).text : '已到列表末尾'}</span>
            </button>
          </nav>
        )}
      </div>
    </div>
  );
}

function MePage({ m, onOpenSettings }) {
  return (
    <div className="m-me" role="region" aria-label="我的">
      <div className="m-group">
        <div className="m-row">
          <SunMoon aria-hidden="true" />
          <span className="m-row-label">外观</span>
          <span className="m-seg3" role="radiogroup" aria-label="外观">
            {[['light', '亮'], ['dark', '暗'], ['system', '跟随']].map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={m.theme.pref === value}
                className={m.theme.pref === value ? 'is-on' : ''}
                onClick={() => m.theme.setPref(value)}
              >
                {label}
              </button>
            ))}
          </span>
        </div>
        <button type="button" className="m-row" onClick={onOpenSettings}>
          <Settings aria-hidden="true" />
          <span className="m-row-label">设置</span>
          <span className="m-row-meta">阅读</span>
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
      </div>
    </div>
  );
}
