import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUpRight, ChevronLeft, ChevronRight, FileText, Flame, Headphones, Loader2 } from 'lucide-react';
import { fetchReaderEvent, fetchReaderEvents, fetchReaderPicks } from '../api';
import {
  EVENT_RANGES,
  RANKING_SEGMENTS,
  beijingStamp,
  beijingTime,
  eventKicker,
  eventMetaParts,
  eventMovement,
  eventWindowHours,
  latestUpdate,
  logoStack,
  memberTime,
  membersByTime,
  normalizeEventRange,
  normalizeRankingSegment,
  picksShapeOf,
  rankingDataKey,
  rankingEmptyText,
  rankingScopeNote,
  representativeOf,
  showRecentFlag,
  timelineDays,
} from '../utils/rankings';
import { displayTitle, titleView } from '../utils/titleDisplay';
import { useTitlePreference } from '../hooks/useTitlePreference';
import { podcastReaderBasisText, qualityScoreText, scoreTierClass } from '../utils/analysis';
import { latinLang } from '../utils/readerText';
import { formatPublishDateTime, formatRelativeTime } from '../utils/datetime';
import { SOURCE_ROLES, resolveCompany } from '../sourceTaxonomy';
import LogoMark from './LogoMark';
import Sheet from './layer/Sheet';

// ── 榜单(issue #221 收敛版,2026-11-03 拍板;设计稿 docs/design/issue-221-rankings/converge.html 第 2 节「收敛后」)──
// 三段 热点事件 · 深度阅读 · 播客精选,三段同一套卡片语法(方案 §3):名次列(宋体齐线数字 + 下方变化)·
// 主体(标题两行 → 一行摘要 → 一行元信息)· 右栏(来源徽标叠放 + 一个度量)。整行点开:命中钮 .rk-hit 铺满整行给焦点与读屏名,点击由行接住。
// 热点事件点开 = 事件详情,同一份内容两种容器:桌面在榜单页内原地换成事件页(版心与列表同宽,「返回榜单」/ Esc 回到列表原位,
// 2026-11-04 目检由右侧抽屉改来——抽屉 500 宽局促);手机是底部层(层原语 Sheet)。文章与单集直接进阅读窗。
// 段与热点事件的时间范围由外层持有(ReaderWorkspace 的 view:关掉再打开回到上次的段,桌面 ⇄ 手机互切也不丢);
// 手机:页头标题位转盘就是三段切换(MobileReader),这里只画范围说明(页头下第一块)与列表(夜航卡面)。

const SEGMENT_ICONS = { events: Flame, reads: FileText, listens: Headphones };
const ROLE_LABEL = Object.fromEntries(SOURCE_ROLES.map((role) => [role.key, role.label]));

// 来源徽标:目录里有这个源用它的品牌标识;没有(未订阅 / 目录未载入)按源名派生字母徽标,与早报同一兜底
function SourceLogo({ sourceMap, sourceId, name, size = 's18' }) {
  const source = sourceId ? sourceMap?.[sourceId] : null;
  const company = source
    ? resolveCompany(source)
    : resolveCompany({ source_id: sourceId || name || 'source', name: name || sourceId || '?', user_source: true });
  return <LogoMark company={company} size={size} emoji={source?.icon} />;
}

function LogoStack({ sourceMap, logos }) {
  const { shown, rest } = logoStack(logos);
  if (!shown.length) return null;
  return (
    <span className="rk-logos" aria-hidden="true">
      {shown.map((logo) => (
        <SourceLogo key={logo.key} sourceMap={sourceMap} sourceId={logo.sourceId} name={logo.name} size="s18" />
      ))}
      {rest > 0 && <span className="rk-logos-more">+{rest}</span>}
    </span>
  );
}

// 读者面相对时间(超 7 天回落日期),悬停给完整时间
function RelTime({ value, className }) {
  if (!value) return null;
  return (
    <time className={className} dateTime={value} title={formatPublishDateTime(value)}>
      {formatRelativeTime(value, '')}
    </time>
  );
}

function MetaLine({ parts }) {
  const list = parts.filter(Boolean);
  if (!list.length) return null;
  return (
    <p className="rk-meta">
      {list.map((part, index) => (
        <Fragment key={index}>
          {index > 0 && <span className="rk-dot" aria-hidden="true">·</span>}
          <span>{part}</span>
        </Fragment>
      ))}
    </p>
  );
}

// 行骨架(样页 CARDS.row):名次列与右栏只是视觉,读屏的完整说法在整行命中钮的名字里。
// 点击由 <li> 接住(命中钮的 click 冒泡上来,键盘 / 读屏激活只开一次),行内名次变化、时间、分数照常接指针,title 悬停可见。
// 鼠标点在行内文字上时先把焦点交给本行命中钮:详情层关掉后焦点回到这一行(层原语按打开时的焦点还焦;检视 R2 P3)
function RankRow({ rank, move = null, title, summary, meta, side, label, onOpen, rowKey, className = '' }) {
  const onClick = (event) => {
    event.currentTarget.querySelector('.rk-hit')?.focus({ preventScroll: true });
    onOpen?.();
  };
  return (
    <li className={`rk-row ${className}`.trim()} data-row={rowKey} onClick={onClick}>
      <button type="button" className="rk-hit" aria-label={label} />
      <div className="rk-rank" aria-hidden="true">
        <span className="rk-num">{rank}</span>
        {move}
      </div>
      <div className="rk-main">
        <h3 className="rk-title"><span lang={latinLang(title)}>{title}</span></h3>
        {summary ? <p className="rk-sum"><span lang={latinLang(summary)}>{summary}</span></p> : null}
        {meta}
      </div>
      <div className="rk-side">{side}</div>
    </li>
  );
}

// 热点事件卡:元信息 =「近 2 天」签 · 最新一篇的来源 · 相对时间;右栏 = 各发布主体徽标 + N 家报道
function EventRow({ item, range, snapshotDate, sourceMap, onOpen }) {
  const move = eventMovement(item, snapshotDate);
  const latest = item.latest || null;
  const logos = (item.publishers || []).map((p, index) => ({ key: p.key || `p${index}`, sourceId: p.source_id, name: p.name }));
  const count = Number(item.publisher_count) || logos.length;
  return (
    <RankRow
      className="is-event"
      rowKey={item.event_id}
      rank={item.rank}
      move={move ? <span className={`rk-move is-${move.direction}`} title={move.title}>{move.label}</span> : null}
      title={item.title}
      summary={item.summary}
      meta={(
        <MetaLine
          parts={[
            showRecentFlag(range, item) ? <span className="rk-new" title="首次出现在近 48 小时内">近 2 天</span> : null,
            latest?.source_name ? `最新 ${latest.source_name}` : null,
            latest?.published_at ? <RelTime value={latest.published_at} /> : null,
          ]}
        />
      )}
      side={(
        <>
          <LogoStack sourceMap={sourceMap} logos={logos} />
          <span className="rk-metric"><b>{count}</b>家报道</span>
        </>
      )}
      label={`第 ${item.rank} 件：${item.title}，${count} 家报道${move ? `，${move.title}` : ''}，打开详情`}
      onOpen={() => onOpen(item)}
    />
  );
}

// 深度阅读 / 播客精选卡:元信息 = 来源 · 相对时间 · 内容类型(播客不写);右栏 = 来源徽标 + 内容价值分(身份线)
function PickRow({ item, rank, shape, sourceMap, preferChinese, onOpen }) {
  const title = displayTitle(item, preferChinese, '');
  const score = qualityScoreText(item.content_value);
  const sourceName = item.source_name || sourceMap?.[item.source_id]?.name || item.source_id || '';
  const kind = shape !== 'podcast' && item.kind_label && item.kind_label !== '资讯' ? item.kind_label : null;
  // 播客的评分依据(「仅基于节目简介 / 基于整期内容」)随分数 title 与读屏名给出,不占元信息行(conventions §播客条目)
  const basis = shape === 'podcast'
    ? podcastReaderBasisText({ content_type: 'podcast_episode', podcast: { full_analysis_ready: item.full_analysis_ready === true } })
    : '';
  const scoreNote = score ? `内容价值 ${score}${basis ? ` · ${basis}` : ''}` : '';
  return (
    <RankRow
      className={`is-content ${shape === 'podcast' ? 'is-podcast' : ''}`}
      rank={rank}
      title={title}
      summary={item.summary}
      meta={<MetaLine parts={[sourceName, item.publish_date ? <RelTime value={item.publish_date} /> : null, kind]} />}
      side={(
        <>
          <LogoStack sourceMap={sourceMap} logos={[{ key: item.source_id || 'source', sourceId: item.source_id, name: sourceName }]} />
          {score && (
            <span className={`rk-score ai-grad-text ${scoreTierClass(item.content_value)}`} title={scoreNote}>{score}</span>
          )}
        </>
      )}
      label={`第 ${rank} ${shape === 'podcast' ? '集' : '篇'}：${title}${scoreNote ? `，${scoreNote}` : ''}，打开`}
      onOpen={() => onOpen(item.id)}
    />
  );
}

// 本周 / 近 2 天:桌面在册微型分段 .mini-seg;手机用榜单 Tab 的镜片宽分段(.ranking-axis,命中 44,拇指按第几项定位)
function RangeSwitch({ range, onChange, mobile }) {
  const index = Math.max(0, EVENT_RANGES.findIndex(([key]) => key === range));
  if (mobile) {
    return (
      <div className="ranking-axis rk-range" role="group" aria-label="时间范围" style={{ '--n': EVENT_RANGES.length, '--i': index }}>
        {EVENT_RANGES.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={range === key ? 'is-on' : ''}
            aria-pressed={range === key}
            onClick={() => { if (range !== key) onChange?.(key); }}
          >{label}</button>
        ))}
      </div>
    );
  }
  return (
    <div className="mini-seg rk-range" role="group" aria-label="时间范围">
      {EVENT_RANGES.map(([key, label]) => (
        <button
          key={key}
          type="button"
          className={`mini-seg-btn ${range === key ? 'is-on' : ''}`}
          aria-pressed={range === key}
          onClick={() => { if (range !== key) onChange?.(key); }}
        >{label}</button>
      ))}
    </div>
  );
}

// 桌面事件页打开时焦点落在标题上(titleRef;读屏先听到这件事是什么),手机底部层的焦点由层原语管
function EventDetailHead({ detail, titleRef = null }) {
  const shown = detail.data || detail.item || {};
  const rank = detail.data ? detail.data.rank : detail.item?.rank;
  const meta = eventMetaParts(shown);
  return (
    <div className="rk-dh">
      <p className="rk-kicker">{eventKicker(detail.range, rank)}</p>
      <h2 className="rk-dtitle" lang={latinLang(shown.title)} ref={titleRef} tabIndex={titleRef ? -1 : undefined}>{shown.title}</h2>
      {meta.length > 0 && <p className="rk-dmeta">{meta.join(' · ')}</p>}
    </div>
  );
}

// 事件详情正文(拍板 9;2026-11-04 目检修订):代表报道固定首位 → 最新进展(一张卡,标题为主、摘要截两行)→ 时间线
// (按北京时间分日,左侧纵线串起每一篇)。「来源」节取消:谁报道了在时间线里逐篇可见,家数在抬头。
// 「阅读全文」是安静的文字动作(与卡片同一套墨色),不用实心主按钮
function EventDetailBody({ detail, sourceMap, preferChinese, onOpenArticle }) {
  if (detail.status === 'loading') {
    return <p className="rk-sec-state"><Loader2 className="animate-spin" aria-hidden="true" />详情加载中…</p>;
  }
  if (detail.status === 'error') {
    return <p className="rk-sec-state is-error" role="alert">{detail.error}</p>;
  }
  const data = detail.data || {};
  const rep = representativeOf(data);
  const repTitle = rep ? titleView(rep, preferChinese, '') : null;
  const repSummary = data.summary || rep?.summary || '';
  const repTime = rep ? memberTime(rep) : '';
  const members = membersByTime(data);
  const days = timelineDays(members);
  const latest = latestUpdate(data);
  const latestTitle = latest ? displayTitle(latest, preferChinese, '') : '';
  const open = (articleId) => { if (articleId) onOpenArticle?.(articleId); };
  return (
    <>
      {rep && (
        <section className="rk-sec rk-rep" aria-label="代表报道">
          <p className="rk-rep-src">
            <SourceLogo sourceMap={sourceMap} sourceId={rep.source_id} name={rep.source_name} size="s20" />
            <span className="rk-rep-name">{rep.source_name}</span>
            {ROLE_LABEL[rep.source_role] && <span className="rk-role">{ROLE_LABEL[rep.source_role]}</span>}
            <span className="rk-card-k">代表报道</span>
          </p>
          <p className="rk-rep-title" lang={latinLang(repTitle.title)}>{repTitle.title}</p>
          {repTitle.orig && <p className="rk-rep-orig" lang={latinLang(repTitle.orig)}>{repTitle.orig}</p>}
          {repSummary && <p className="rk-rep-sum" lang={latinLang(repSummary)}>{repSummary}</p>}
          <div className="rk-rep-actions">
            <button type="button" className="rk-read" data-read-origin="" onClick={() => open(rep.article_id)}>
              阅读全文<ChevronRight aria-hidden="true" />
            </button>
            {rep.url && (
              <a className="rk-link" href={rep.url} target="_blank" rel="noopener noreferrer">
                {rep.source_role === 'official' ? '官方原文' : '原文'}<ArrowUpRight aria-hidden="true" />
              </a>
            )}
            {repTime && <time className="rk-rep-time" dateTime={repTime}>{beijingStamp(repTime)}</time>}
          </div>
        </section>
      )}

      {latest && (
        <section className="rk-sec" aria-label="最新进展">
          <button
            type="button"
            className="rk-latest"
            data-read-origin=""
            aria-label={`最新进展：${latestTitle}，${latest.source_name || ''}，打开`}
            onClick={() => open(latest.article_id)}
          >
            <span className="rk-latest-src">
              <SourceLogo sourceMap={sourceMap} sourceId={latest.source_id} name={latest.source_name} size="s15" />
              <span className="rk-latest-name">{latest.source_name}</span>
              <span className="rk-dot" aria-hidden="true">·</span>
              <RelTime value={memberTime(latest)} />
              <span className="rk-card-k">最新进展</span>
            </span>
            <span className="rk-latest-title" lang={latinLang(latestTitle)}>{latestTitle}</span>
            {latest.summary && <span className="rk-latest-sum" lang={latinLang(latest.summary)}>{latest.summary}</span>}
          </button>
        </section>
      )}

      {days.length > 0 && (
        <section className="rk-sec">
          <h3 className="drawer-sec-title">时间线 · {Number(data.member_count) || members.length} 篇</h3>
          <ol className="rk-tl">
            {days.map((day) => (
              <li key={day.key} className="rk-tl-day">
                <p className="rk-tl-daylabel"><span className="rk-tl-node" aria-hidden="true" /><span>{day.label}</span></p>
                <ol>
                  {day.items.map((member) => {
                    const title = displayTitle(member, preferChinese, '');
                    return (
                      <li key={member.article_id}>
                        <button
                          type="button"
                          className={`rk-tl-item ${member.is_representative ? 'is-rep' : ''}`}
                          data-read-origin=""
                          onClick={() => open(member.article_id)}
                        >
                          <span className="rk-tl-time">{beijingTime(memberTime(member))}</span>
                          <span className="rk-tl-dot" aria-hidden="true" />
                          <span className="rk-tl-main">
                            <span className="rk-tl-src">
                              <SourceLogo sourceMap={sourceMap} sourceId={member.source_id} name={member.source_name} size="s15" />
                              {member.source_name}
                              {member.is_representative && <span className="rk-tl-tag">代表报道</span>}
                            </span>
                            <span className="rk-tl-title" lang={latinLang(title)}>{title}</span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

export default function RankingsPage({
  segment,
  onSegmentChange,
  range,
  onRangeChange,
  onLoaded,
  sourceMap = {},
  onOpenArticle,
  mobile = false,
}) {
  const seg = normalizeRankingSegment(segment);
  const rng = normalizeEventRange(range);
  const key = rankingDataKey(seg, rng);
  // 标题语言(issue #218):条目带 title_zh 且偏好开时显示中文译名,否则原题
  const { preferChinese } = useTitlePreference();

  // 列表:每个视图一份,本次打开期间缓存(来回切段不再闪加载)
  const cacheRef = useRef(new Map());
  const onLoadedRef = useRef(onLoaded);
  useEffect(() => { onLoadedRef.current = onLoaded; });
  const [list, setList] = useState({ key: '', status: 'loading', data: null, error: '' });
  useEffect(() => {
    const cached = cacheRef.current.get(key);
    if (cached) {
      setList({ key, status: 'ok', data: cached, error: '' });
      return undefined;
    }
    const controller = new AbortController();
    setList({ key, status: 'loading', data: null, error: '' });
    const request = seg === 'events'
      ? fetchReaderEvents(eventWindowHours(rng), { signal: controller.signal })
      : fetchReaderPicks(picksShapeOf(seg), { signal: controller.signal });
    request.then((data) => {
      if (controller.signal.aborted) return;
      cacheRef.current.set(key, data);
      setList({ key, status: 'ok', data, error: '' });
      onLoadedRef.current?.(key, Array.isArray(data?.items) ? data.items.length : 0);
    }).catch((reason) => {
      if (controller.signal.aborted || reason?.name === 'AbortError') return;
      setList({ key, status: 'error', data: null, error: reason?.message || '榜单加载失败，请稍后重试' });
    });
    return () => controller.abort();
  }, [key, seg, rng]);
  const status = list.key === key ? list.status : 'loading';
  const items = status === 'ok' && Array.isArray(list.data?.items) ? list.data.items : [];

  // 事件详情:关的时候只收层、不清内容(底部层要带着内容播退场)
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState(null);   // { seq, id, range, item, status, data, error }
  const seqRef = useRef(0);
  // 桌面事件页:进页时记下列表滚到哪、点的是哪一行;返回时回到原位,焦点回到该行
  const mainRef = useRef(null);
  const titleRef = useRef(null);
  const listScrollRef = useRef(0);
  const returnRowRef = useRef(null);
  const openEvent = useCallback((item) => {
    seqRef.current += 1;
    listScrollRef.current = mainRef.current?.scrollTop || 0;
    returnRowRef.current = item.event_id;
    setDetail({ seq: seqRef.current, id: item.event_id, range: rng, item, status: 'loading', data: null, error: '' });
    setDetailOpen(true);
  }, [rng]);
  const closeEvent = useCallback(() => setDetailOpen(false), []);
  const detailSeq = detail?.seq;
  const detailId = detail?.id;
  const detailRange = detail?.range;
  useEffect(() => {
    if (detailSeq == null || detailId == null) return undefined;
    const controller = new AbortController();
    const settle = (patch) => setDetail((current) => (current && current.seq === detailSeq ? { ...current, ...patch } : current));
    fetchReaderEvent(detailId, eventWindowHours(detailRange), { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) settle({ status: 'ok', data, error: '' });
      })
      .catch((reason) => {
        if (controller.signal.aborted || reason?.name === 'AbortError') return;
        settle({
          status: 'error',
          error: reason?.status === 404 ? '这件事已不在近期的热点事件里' : (reason?.message || '详情加载失败，请稍后重试'),
        });
      });
    return () => controller.abort();
  }, [detailSeq, detailId, detailRange]);

  const pageOpen = !mobile && detailOpen && detail != null;
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (mobile || !main) return;
    if (pageOpen) {
      main.scrollTop = 0;
      titleRef.current?.focus({ preventScroll: true });
      return;
    }
    const row = returnRowRef.current;
    if (row == null) return;
    returnRowRef.current = null;
    main.scrollTop = listScrollRef.current;
    main.querySelector(`.rk-row[data-row="${row}"] .rk-hit`)?.focus({ preventScroll: true });
  }, [pageOpen, mobile]);
  // Esc 返回榜单;上面若还有弹窗,它的层栈先在 document 捕获阶段消费掉 Esc,这里收不到。输入框里的 Esc 留给输入框
  useEffect(() => {
    if (!pageOpen) return undefined;
    const onKey = (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      closeEvent();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pageOpen, closeEvent]);

  const openArticle = (articleId) => { if (articleId) onOpenArticle?.(articleId); };
  const note = rankingScopeNote(seg, rng);
  const shape = picksShapeOf(seg);

  let body;
  if (status === 'loading') {
    body = <div className="ranking-state"><Loader2 className="animate-spin" aria-hidden="true" />榜单加载中…</div>;
  } else if (status === 'error') {
    body = <div className="ranking-state is-error" role="alert">{list.error}</div>;
  } else if (!items.length) {
    body = <div className="rk-empty"><p>{rankingEmptyText(seg, rng)}</p></div>;
  } else {
    const rows = seg === 'events'
      ? items.map((item) => (
        <EventRow
          key={item.event_id}
          item={item}
          range={rng}
          snapshotDate={list.data?.snapshot_date || null}
          sourceMap={sourceMap}
          onOpen={openEvent}
        />
      ))
      : items.map((item, index) => (
        <PickRow
          key={item.id}
          item={item}
          rank={index + 1}
          shape={shape}
          sourceMap={sourceMap}
          preferChinese={preferChinese}
          onOpen={openArticle}
        />
      ));
    const listEl = <ol className={`rk-list ${seg === 'events' ? 'is-hot' : ''}`.trim()} aria-label={RANKING_SEGMENTS.find(([k]) => k === seg)[1]}>{rows}</ol>;
    // 手机:每段列表是一张夜航卡面;桌面直接铺在页面上,靠细线与留白分隔
    body = mobile ? <div className="rk-card">{listEl}</div> : listEl;
  }
  const range_ = seg === 'events' ? <RangeSwitch range={rng} onChange={onRangeChange} mobile={mobile} /> : null;

  const detailBody = detail ? (
    <EventDetailBody detail={detail} sourceMap={sourceMap} preferChinese={preferChinese} onOpenArticle={openArticle} />
  ) : null;
  const detailTitle = (detail?.data || detail?.item)?.title || '';

  return (
    <main ref={mainRef} className={`rankings-page rk-page ${mobile ? 'is-mobile' : ''}`.trim()} aria-label="榜单">
      {mobile ? (
        <>
          <p className="ranking-head-note">{note}</p>
          {range_}
          {body}
        </>
      ) : pageOpen ? (
        <article className="rk-wrap rk-event" aria-label="热点事件详情">
          <button type="button" className="rk-back" onClick={closeEvent}>
            <ChevronLeft aria-hidden="true" />返回榜单
          </button>
          <EventDetailHead detail={detail} titleRef={titleRef} />
          <div className="rk-ebody">{detailBody}</div>
        </article>
      ) : (
        <div className="rk-wrap">
          <header className="ranking-head rk-head">
            <div className="rk-head-l">
              <span className="reader-disc-title">榜单</span>
              <p className="ranking-head-note">{note}</p>
            </div>
            <div className="reader-seg" role="group" aria-label="榜单分段">
              {RANKING_SEGMENTS.map(([value, label]) => {
                const Icon = SEGMENT_ICONS[value];
                return (
                  <button
                    key={value}
                    type="button"
                    className={`reader-seg-btn ${seg === value ? 'is-on' : ''}`}
                    aria-pressed={seg === value}
                    onClick={() => { if (seg !== value) onSegmentChange?.(value); }}
                  >
                    <Icon aria-hidden="true" />{label}
                  </button>
                );
              })}
            </div>
          </header>
          {range_}
          {body}
        </div>
      )}

      {mobile && detail && (
        <Sheet
          open={detailOpen}
          onClose={closeEvent}
          label="热点事件详情"
          grabLabel={`关闭「${detailTitle}」的详情`}
          className="rk-sheet"
          head={<div className="rk-shead-row"><EventDetailHead detail={detail} /></div>}
        >
          <div className="rk-sbody">{detailBody}</div>
        </Sheet>
      )}
    </main>
  );
}
