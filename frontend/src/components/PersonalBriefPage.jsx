import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, CalendarDays, ChevronDown, Clock3, Loader2, RefreshCw, Star, Undo2, Zap } from 'lucide-react';
import {
  completeInterestOnboarding,
  ensurePersonalBrief,
  fetchPersonalBrief,
  fetchPersonalBriefs,
  fetchTodayPersonalBrief,
  rebuildPersonalBrief,
} from '../api';
import { flushPendingInterestSaves } from '../utils/interestSaveQueue';
import { rebuildToast } from '../utils/briefRebuild';
import { guestEdition } from '../utils/guestCopy';
import LogoMark from './LogoMark';
import BriefHistoryCalendar from './BriefHistoryCalendar';
import { resolveCompany } from '../sourceTaxonomy';
import { formatDateTime, formatRelativeTime } from '../utils/datetime';
import { fmtDayKey, WEEKDAY_CHARS } from '../utils/readerTime';
import { latinLang } from '../utils/readerText';
import { titleView } from '../utils/titleDisplay';
import { useTitlePreference } from '../hooks/useTitlePreference';
import { qualityScoreText, scoreTierClass, scoreDisclaimer, SCORE_DIMENSION_LABELS } from '../utils/analysis';
import { scoreDimensionOf, scoreOf } from '../utils/briefScore';

// ── 我的早报 ──
// issue #184「夜航」版式(样页 docs/design/visual-refine-final/brief.html;用户拍板:保留左轨、取消日期中栏、
// 往期从右上角「往期」日历与页尾「本期编排」切换;目检返修把字号、英文原标题斜体衬线对回原「夜航」样页):
//   ① 海报 = 眉头(早报 · 日期)+ 头条(宋体大标题 / 原标题 / 来源与时间 / 摘要 / 阅读全文 · 收藏)+ 右侧评分栏
//      (维度名 · 宋体渐变大数字 · 评分依据 · 入选理由),背后一层 AI 渐变环境光;
//   ② 板块 = 左侧 sticky 板块名 + 右侧条目(宋体标题 / 两行摘要 / 来源 · 时间 · 分数 · 兴趣),板块顺序沿用后端
//      SECTION_ORDER、板块内分数降序;
//   ③ 本期编排 = 编排时刻与版次 / 入选事实句 + 调整兴趣 / 头条规则 / 往期日期。
// 头条:有「重大事件」取第一条,否则取新闻通道里新闻价值最高的一条;降级版面(订阅源最新更新)不设头条——
// 没达到入选标准的内容不该被印成头版,海报位改为一句如实说明。编排中 / 失败 / 没有早报等状态替换海报的头条位,
// 眉头与「往期」照常可用。状态提示(重编排队 / 来源或分析未完成 / 兴趣或订阅已更新)是眉头下的一行,带就地动作。
// 沿用的行为(issue #23 / #33 / #56 / #74 / #178 历次):外出返回(点条目进原文时交出 {日期, 版次, 卷动位置,
// 条目序列},阅读窗出返回带,回来时落回原位并短暂高亮刚读过的那条)、社交条目点开即原链、订阅外条目「+ 订阅」、
// 首登兴趣引导横幅、空态 / 失败态的「先去看文章」出口、深度阅读 / 播客精选两个按内容价值入选的板块。
// 桌面占「源栏 + 条目列 + 阅读窗」整幅(grid-column 2 / -1);移动壳同一张页面,单列。
// 游客(issue #213):看的是按默认订阅编排的共享游客版——眉头下恒为「游客版」说明行 + 登录入口,不出
// 「重新编排」「立即重编」「重试生成」(游客不能重编;失败的游客版由后端过一阵自动重试),选篇理由改说「默认订阅的」。

const TERMINAL = new Set(['ready', 'degraded', 'failed', 'superseded']);
// 额外于新闻精选之上的通道:重大事件 + 深度阅读 / 播客精选(「N 篇精选」不数它们)
const EXTRA_LANES = new Set(['breaking', 'read', 'listen']);
const VALUE_LANES = new Set(['read', 'listen']);
const LIVE = new Set(['pending', 'generating']);
const HISTORY_LIMIT = 100;
const POLL_MS = 8000;
const PAST_CHIPS = 7;

const tagName = (tag) => tag?.name_zh || tag?.name_en || tag?.label || tag?.code || '';
const weekdayOf = (key) => {
  const d = new Date(`${key}T00:00:00`);
  return Number.isNaN(d.getTime()) ? '' : `周${WEEKDAY_CHARS[d.getDay()]}`;
};
const dateTextOf = (key) => `${Number(key.slice(5, 7))}月${Number(key.slice(8, 10))}日`;
const clockOf = (iso) => (iso ? formatDateTime(iso).slice(11, 16) : '');

// 日期的人话名:今天 / 昨天 / 9月3日
function dayNameOf(key, todayKey) {
  if (key === todayKey) return '今天';
  const y = new Date(`${todayKey}T00:00:00`);
  y.setDate(y.getDate() - 1);
  if (key === fmtDayKey(y)) return '昨天';
  return dateTextOf(key);
}

// 兴趣命中的标签名:matched_interest_codes → 快照 tags 反查;老快照无 code 时从理由句里取「」内文字
function interestLabelOf(item, snapshot) {
  const codes = item.matched_interest_codes || [];
  if (codes.length === 0) return '';
  const hit = [...(snapshot.tags || []), ...(snapshot.display_tags || [])].find((tag) => tag.code === codes[0]);
  if (hit) return tagName(hit);
  const matched = /「([^」]+)」/.exec(item.selection_reason || snapshot.selection_reason || '');
  return matched ? matched[1] : '';
}

// 选篇理由(v3.54 §3):四个词回答同一个问题「它为什么在这」。前两个是「为你」的证据(accent),
// 后两个只是诚实交代(faint)。「今日最重要」一类说过头的词不用——一篇 6 分稿叫不出「最重要」。
function whyOf(item, snapshot, interest) {
  if (item.selection_lane === 'breaking') {
    const basis = item.breaking?.basis;
    const count = item.breaking?.source_count || 0;
    const detail = basis === 'official' ? '官方一手' : count >= 2 ? `${count} 家来源` : '';
    return { cls: 'is-breaking', icon: true, text: detail ? `重大事件 · ${detail}` : '重大事件', title: item.selection_reason || snapshot.selection_reason || '今日重大事件' };
  }
  if (snapshot.is_latest_update) return { cls: 'is-fallback', text: '最新更新', title: item.selection_reason || snapshot.selection_reason || '' };
  if (interest) return { cls: 'is-interest', text: `兴趣 · ${interest}`, title: item.selection_reason || snapshot.selection_reason || '命中你的兴趣' };
  const byValue = VALUE_LANES.has(item.selection_lane);
  return { cls: '', text: byValue ? '内容价值入选' : '新闻价值入选', title: item.selection_reason || snapshot.selection_reason || '' };
}

// 头条:有「重大事件」取第一条;否则取新闻通道里分数最高的一条(同分取靠前);只有深度阅读 / 播客精选时取其中最高分。
// 降级版面不设头条。
function pickHeadline(groups, degraded) {
  if (degraded) return null;
  const all = groups.flatMap((group) => group.items);
  const breaking = all.find((item) => item.selection_lane === 'breaking');
  if (breaking) return breaking;
  const news = all.filter((item) => !VALUE_LANES.has(item.selection_lane));
  const pool = news.length > 0 ? news : all;
  let best = null;
  let bestScore = -Infinity;
  pool.forEach((item) => {
    const score = scoreOf(item);
    const value = score == null ? -Infinity : score;
    if (best == null || value > bestScore) {
      best = item;
      bestScore = value;
    }
  });
  return best;
}

// 一条的展示要素:标题(按偏好选中文译名或原题)、摘要、来源、分数与维度、理由、订阅态——海报头条与板块条目共用
function viewOf(item, source, preferChinese) {
  const snapshot = item.snapshot || {};
  const dimension = scoreDimensionOf(item);
  const shownScore = scoreOf(item);
  const interest = interestLabelOf(item, snapshot);
  const sourceName = snapshot.source_name || source?.name || snapshot.source_id || '未知来源';
  // 中文标题(v3.52.1,issue #33 §4):编排后补的 title_zh 作主标题,原标题降为其下小字;
  // 「默认显示中文标题」关闭时只显示快照原题(issue #218,只选不改,版面快照不动)
  const titles = titleView(snapshot, preferChinese);
  return {
    snapshot,
    dimension,
    dimensionLabel: SCORE_DIMENSION_LABELS[dimension],
    score: qualityScoreText(shownScore),
    scoreTier: scoreTierClass(shownScore),   // issue #54
    // 评分依据随维度(issue #178 第二波):内容价值有自己的理由
    reason: (dimension === 'content' ? snapshot.content_value_reason : snapshot.score_reason) || snapshot.score_reason || '',
    interest,
    why: whyOf(item, snapshot, interest),
    // one_sentence_summary 自 v3.45.1 取缔;历史 edition 快照仍带该键,保留回退读取
    summary: snapshot.summary || snapshot.one_sentence_summary || '',
    sourceName,
    company: source ? resolveCompany(source) : resolveCompany({ source_id: snapshot.source_id, name: sourceName, user_source: true }),
    title: titles.title,
    titleOrig: titles.orig,
    // 订阅外(v3.54「订阅 ∪ 兴趣」):快照事实 subscribed=false,且当前仍未订阅(订阅后即不再画,不必等重编)
    unsubscribed: snapshot.subscribed === false && !(source && source.subscribed) && !!snapshot.source_id,
  };
}

function SubscribeChip({ view, onSubscribeSource }) {
  if (!view.unsubscribed) return null;
  return (
    <button
      type="button"
      className="bp-unsub"
      title="订阅这个来源"
      aria-label={`订阅 ${view.sourceName}`}
      onClick={() => onSubscribeSource?.(view.snapshot.source_id)}
    />
  );
}

// 「兴趣 · X」「重大事件 · …」两种是「为你」的证据,上元信息行;「新闻价值入选」之类只在悬停说明里
function WhyMark({ why }) {
  if (why.cls !== 'is-interest' && why.cls !== 'is-breaking') return null;
  return (
    <span className={`bp-why ${why.cls}`} title={why.title}>
      {why.icon && <Zap aria-hidden="true" />}
      {why.text}
    </span>
  );
}

// issue #103「另见 N 篇」:同一事件的其它报道(编排时的不可变快照,只含读者可见来源);
// N 数篇(用户目检拍板:按来源计会把同源两篇算成一,少报),列表逐篇;链接开新窗(是另一来源的稿,不是本条)
function relatedOf(snapshot) {
  const rows = Array.isArray(snapshot?.related_sources) ? snapshot.related_sources : [];
  return rows.filter((row) => row && row.url);
}

function RelatedChip({ links, open, onToggle }) {
  if (!links.length) return null;
  return (
    <button
      type="button"
      className={`bp-also ${open ? 'is-open' : ''}`}
      aria-expanded={open}
      title={open ? '收起同一事件的其它报道' : '同一事件的其它报道'}
      onClick={onToggle}
    >
      另见 {links.length} 篇
    </button>
  );
}

function RelatedList({ links, open }) {
  if (!open || !links.length) return null;
  return (
    <ul className="bp-also-list">
      {links.map((row) => {
        const title = row.title_zh || row.title || row.url;
        return (
          <li key={row.article_id || row.url}>
            <a className="bp-also-link" href={row.url} target="_blank" rel="noopener noreferrer" lang={latinLang(title)}>
              <span className="bp-also-src">{row.source_name || row.source_id || '来源'}</span>
              <span className="bp-also-title">{title}</span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function FavoriteButton({ articleId, favoriteIds, onToggleFavorite, label = false }) {
  if (!articleId || !onToggleFavorite) return null;
  const on = Boolean(favoriteIds?.has(articleId));
  return (
    <button
      type="button"
      className={`${label ? 'bp-btn' : 'bp-fav'} ${on ? 'is-on' : ''}`}
      aria-pressed={on}
      aria-label={on ? '取消收藏' : '收藏'}
      title={on ? '取消收藏' : '收藏'}
      onClick={() => onToggleFavorite(articleId)}
    >
      <Star aria-hidden="true" fill={on ? 'currentColor' : 'none'} />
      {label && (on ? '已收藏' : '收藏')}
    </button>
  );
}

function BriefItem({ item, source, flash, onOpen, onSubscribeSource, favoriteIds, onToggleFavorite }) {
  const { preferChinese } = useTitlePreference();
  const view = viewOf(item, source, preferChinese);
  const { snapshot } = view;
  const related = relatedOf(snapshot);
  const [showRelated, setShowRelated] = useState(false);
  return (
    <article className={`bp-item ${flash ? 'is-just-read' : ''}`}>
      {/* 标题按钮是唯一的「打开」控件:::after 拉伸覆盖整条,点条目任意处即打开;收藏与「+ 订阅」浮在覆盖层之上 */}
      <h3 className="bp-item-title" lang={latinLang(view.title)}>
        <button type="button" className="bp-item-open" onClick={() => onOpen(item)}>{view.title}</button>
      </h3>
      {view.titleOrig && <p className="bp-item-orig" lang={latinLang(view.titleOrig)}>{view.titleOrig}</p>}
      {view.summary && <p className="bp-item-sum">{view.summary}</p>}
      <div className="bp-item-meta">
        <span className="bp-src">
          <LogoMark company={view.company} size="s17" emoji={source?.icon} />
          <span className="bp-src-name">{view.sourceName}</span>
        </span>
        <SubscribeChip view={view} onSubscribeSource={onSubscribeSource} />
        {snapshot.publish_date && (
          <span className="bp-time" title={formatDateTime(snapshot.publish_date)}>{formatRelativeTime(snapshot.publish_date, '')}</span>
        )}
        {view.score && (
          <span className="bp-score" title={`${view.dimensionLabel} ${view.score}。${scoreDisclaimer(view.dimension)}`} aria-label={`${view.dimensionLabel}分 ${view.score}`}>
            <span className={`ai-grad-text ${view.scoreTier}`}>{view.score}</span>
          </span>
        )}
        <WhyMark why={view.why} />
        <RelatedChip links={related} open={showRelated} onToggle={() => setShowRelated((value) => !value)} />
        <FavoriteButton articleId={item.article_id} favoriteIds={favoriteIds} onToggleFavorite={onToggleFavorite} />
      </div>
      <RelatedList links={related} open={showRelated} />
    </article>
  );
}

function Headline({ item, kicker, source, flash, restCount, onOpen, onSubscribeSource, favoriteIds, onToggleFavorite }) {
  const { preferChinese } = useTitlePreference();
  const view = viewOf(item, source, preferChinese);
  const { snapshot } = view;
  const selectionReason = item.selection_reason || snapshot.selection_reason || '';
  const related = relatedOf(snapshot);
  const [showRelated, setShowRelated] = useState(false);
  return (
    <div className={`bp-head ${flash ? 'is-just-read' : ''}`}>
      <p className="bp-kicker">{kicker}</p>
      {/* 标题可点(鼠标的直觉);键盘与读屏走下方「阅读全文」,标题本身不重复成第二个按钮 */}
      <h1 className="bp-title" lang={latinLang(view.title)} onClick={() => onOpen(item)}>{view.title}</h1>
      {view.titleOrig && <p className="bp-orig" lang={latinLang(view.titleOrig)}>{view.titleOrig}</p>}
      <div className={`bp-lower ${view.score ? '' : 'is-solo'}`}>
        <div className="bp-lower-main">
          <p className="bp-meta">
            <span className="bp-src">
              <LogoMark company={view.company} size="s17" emoji={source?.icon} />
              <span className="bp-src-name">{view.sourceName}</span>
            </span>
            <SubscribeChip view={view} onSubscribeSource={onSubscribeSource} />
            {snapshot.publish_date && (
              <span className="bp-time" title={formatDateTime(snapshot.publish_date)}>{formatRelativeTime(snapshot.publish_date, '')}</span>
            )}
            {view.why.cls === 'is-interest' && <WhyMark why={view.why} />}
            <RelatedChip links={related} open={showRelated} onToggle={() => setShowRelated((value) => !value)} />
          </p>
          {view.summary && <p className="bp-sum">{view.summary}</p>}
          <RelatedList links={related} open={showRelated} />
          <div className="bp-actions">
            <button type="button" className="bp-btn is-primary" onClick={() => onOpen(item)}>
              阅读全文
              <ArrowRight aria-hidden="true" />
            </button>
            <FavoriteButton articleId={item.article_id} favoriteIds={favoriteIds} onToggleFavorite={onToggleFavorite} label />
          </div>
        </div>
        {view.score && (
          <aside className="bp-verdict" aria-label={view.dimensionLabel}>
            <p className="bp-verdict-label">{view.dimensionLabel}</p>
            <p className="bp-verdict-score" title={scoreDisclaimer(view.dimension)}>
              <span className={`ai-grad-text ${view.scoreTier}`}>{view.score}</span>
            </p>
            {view.reason && <p className="bp-verdict-reason">{view.reason}</p>}
            {(selectionReason || view.why.text) && (
              <p className="bp-verdict-why">{selectionReason || view.why.text}</p>
            )}
          </aside>
        )}
      </div>
      {restCount > 0 && (
        <span className="bp-cue">
          <ArrowDown aria-hidden="true" />
          还有 {restCount} 条
        </span>
      )}
    </div>
  );
}

function PosterSkeleton() {
  return (
    <div className="bp-skel skeleton-delay" aria-hidden="true">
      <div className="skeleton bp-skel-kicker" />
      <div className="skeleton bp-skel-title" />
      <div className="skeleton bp-skel-title is-short" />
      <div className="skeleton bp-skel-line" />
      <div className="skeleton bp-skel-line" />
      <div className="skeleton bp-skel-line is-short" />
    </div>
  );
}

export default function PersonalBriefPage({
  showToast,
  onManageSubscriptions,
  onManageInterests = null, // 「调整兴趣」深链发现页兴趣段(v3.54 §3)
  onSubscribeSource = null, // 订阅外条目「+ 订阅」就地订阅(source_id)
  onOpenArticle,
  supersedePendingOpen = null, // 直接开外链前作废在途的站内打开(慢网下先点内链条目再点社交条目)
  sourceMap = {},
  interestVersion = 0,
  mobile = false,
  // 收藏(issue #184 海报与条目的星):阅读器的收藏集合与切换,与条目列 / 阅读窗同一份状态
  favoriteIds = null,
  onToggleFavorite = null,
  // ── issue #56 首屏:首登引导横幅(onboarding)+ 设置兴趣/跳过回调 + 「去看文章」出口 ──
  onboarding = false,
  onSetupInterests = null,      // 「设置兴趣」→ 发现页兴趣段
  onOnboardingCompleted = null, // 「稍后再说」写完 complete_onboarding 后通知父级更新账号快照
  onBrowse = null,              // 「去看文章」→ 关早报页落文章容器
  // 从阅读窗「返回我的早报」回来时带的外出上下文 {date, revision, scrollTop, itemId};挂载时一次性读取
  restore = null,
  guest = false,
  onGuestLogin = null,
}) {
  const restoreRef = useRef(restore);
  const [flashId, setFlashId] = useState(null);
  const [history, setHistory] = useState([]);
  const [today, setToday] = useState(null); // {status, edition}
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [sel, setSel] = useState(() => (
    restore ? { date: restore.date, revision: restore.revision ?? null } : { date: null, revision: null }
  )); // date=null → 今天
  const [detail, setDetail] = useState({ key: '', edition: null, loading: false, error: '' });
  const [histOpen, setHistOpen] = useState(false);
  const histTriggerRef = useRef(null);
  const cacheRef = useRef(new Map()); // 终态 edition 不可变,按 date#revision 缓存
  const sheetRef = useRef(null);

  const loadHistory = useCallback(() => (
    fetchPersonalBriefs(HISTORY_LIMIT)
      .then((data) => setHistory(data.items || []))
      .catch(() => { /* 历史是辅助信息,失败不覆盖今日主状态 */ })
  ), []);

  const loadToday = useCallback(async ({ ensure = false } = {}) => {
    setError('');
    try {
      const data = ensure ? await ensurePersonalBrief() : await fetchTodayPersonalBrief();
      setToday(data);
      if (TERMINAL.has(data?.status)) loadHistory();
      return data;
    } catch (err) {
      setError(err.message || '加载今日早报失败，请重试');
      return null;
    }
  }, [loadHistory]);

  useEffect(() => {
    setLoading(true);
    const r = restoreRef.current;
    setSel(r ? { date: r.date, revision: r.revision ?? null } : { date: null, revision: null });
    setHistOpen(false);
    loadToday({ ensure: true }).finally(() => setLoading(false));
    loadHistory();
  }, [interestVersion, loadToday, loadHistory]);

  const todayStatus = today?.status;
  useEffect(() => {
    if (!LIVE.has(todayStatus)) return undefined;
    const timer = window.setInterval(() => loadToday(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [todayStatus, loadToday]);

  const todayEdition = today?.edition || null;
  const todayKey = todayEdition?.report_date || fmtDayKey(new Date());

  // 历史 → 按天分组(版次降序);今天恒在首位,并用 today 载荷里更新的那一版覆盖/补入
  const days = useMemo(() => {
    const byDate = new Map();
    const push = (edition) => {
      if (!edition?.report_date) return;
      const list = byDate.get(edition.report_date) || [];
      const idx = list.findIndex((row) => row.revision === edition.revision);
      if (idx >= 0) list[idx] = { ...list[idx], ...edition, items: undefined };
      else list.push({ ...edition, items: undefined });
      byDate.set(edition.report_date, list);
    };
    history.forEach(push);
    if (todayEdition) push(todayEdition);
    if (!byDate.has(todayKey)) byDate.set(todayKey, []);
    return [...byDate.entries()]
      .sort(([a], [b]) => (a < b ? 1 : -1))
      .map(([key, revisions]) => {
        revisions.sort((a, b) => b.revision - a.revision);
        return { key, revisions, latest: revisions[0] || null };
      });
  }, [history, todayEdition, todayKey]);

  const selDate = sel.date || todayKey;
  const isToday = selDate === todayKey;
  const selDay = days.find((day) => day.key === selDate) || { key: selDate, revisions: [], latest: null };
  const selRevision = sel.revision ?? selDay.latest?.revision ?? null;
  const usesToday = isToday && (sel.revision == null || sel.revision === todayEdition?.revision);
  const viewKey = `${selDate}#${selRevision ?? 'latest'}`;

  useEffect(() => {
    if (usesToday) return undefined;
    const cached = cacheRef.current.get(viewKey);
    if (cached) {
      setDetail({ key: viewKey, edition: cached, loading: false, error: '' });
      return undefined;
    }
    let cancelled = false;
    setDetail({ key: viewKey, edition: null, loading: true, error: '' });
    fetchPersonalBrief(selDate, selRevision)
      .then((edition) => {
        if (cancelled) return;
        if (TERMINAL.has(edition?.status)) cacheRef.current.set(viewKey, edition);
        setDetail({ key: viewKey, edition, loading: false, error: '' });
      })
      .catch((err) => {
        if (!cancelled) setDetail({ key: viewKey, edition: null, loading: false, error: err.message || '加载早报失败，请重试' });
      });
    return () => { cancelled = true; };
  }, [usesToday, viewKey, selDate, selRevision]);

  // 换期回顶
  useEffect(() => {
    if (sheetRef.current) sheetRef.current.scrollTop = 0;
  }, [viewKey]);

  const shownEdition = usesToday ? todayEdition : (detail.key === viewKey ? detail.edition : null);
  const edition = useMemo(() => (guest ? guestEdition(shownEdition) : shownEdition), [guest, shownEdition]);
  const detailLoading = !usesToday && (detail.key !== viewKey || detail.loading);

  // 返回落位:条目进 DOM 的同一提交里把卷动位置放回去(layout effect,不闪一帧顶部),
  // 刚读过的那条短暂高亮;只消费一次,之后本次挂载内换期照常回顶。
  const cardsReady = !loading && !detailLoading && (edition?.items?.length || 0) > 0;
  useLayoutEffect(() => {
    const r = restoreRef.current;
    if (!r || !cardsReady) return undefined;
    restoreRef.current = null;
    if (sheetRef.current) sheetRef.current.scrollTop = r.scrollTop || 0;
    if (r.itemId == null) return undefined;
    setFlashId(r.itemId);
    const timer = window.setTimeout(() => setFlashId(null), 1800);
    return () => window.clearTimeout(timer);
  }, [cardsReady]);
  const status = usesToday ? (todayStatus || edition?.status) : edition?.status;

  const closeHistory = useCallback(() => setHistOpen(false), []);
  const pickDay = (key) => {
    setSel({ date: key === todayKey ? null : key, revision: null });
    setHistOpen(false);
  };
  const pickRevision = (revision) => {
    setSel((prev) => ({ ...prev, revision }));
    setHistOpen(false);
  };

  const handleRebuild = async () => {
    setWorking(true);
    try {
      const data = await rebuildPersonalBrief();
      setToday(data);
      setSel({ date: null, revision: null });
      showToast?.('已开始重新编排今日早报', 'success');
      loadHistory();
    } catch (err) {
      showToast?.(err.message || '重新编排失败，请重试', 'error');
    } finally {
      setWorking(false);
    }
  };

  // 首登引导「稍后再说」:专用端点只做引导完成的条件迁移、不碰兴趣集合(此前 GET+整集 PUT 会把另一
  // 标签页刚保存的兴趣整集覆盖——codex 检视 P2);发出前先等本标签页排队中的兴趣保存落定,否则先于
  // 卸载时排入的 PUT 完成引导会永久错过首次兴趣重编。读者若已自动保存过兴趣,后端会就地重编,
  // 响应 brief_rebuild_status 非空时立即重拉今日与历史,别让已挂载的早报停在旧版
  const [skipping, setSkipping] = useState(false);
  const handleSkipOnboarding = async () => {
    setSkipping(true);
    try {
      await flushPendingInterestSaves();
      const res = await completeInterestOnboarding();
      onOnboardingCompleted?.();
      const rebuildStatus = res?.brief_rebuild_status ?? null;
      if (rebuildStatus) {
        const toast = rebuildToast(rebuildStatus);
        if (toast) showToast?.(toast.text, toast.kind);
        setSel({ date: null, revision: null });
        await Promise.all([loadToday(), loadHistory()]);
      }
    } catch (err) {
      showToast?.(err.message || '操作失败，请重试', 'error');
    } finally {
      setSkipping(false);
    }
  };

  // ── 分组:按 section 首次出现建组(后端自 issue #74 起按 SECTION_ORDER 落库,这里只是保序),
  //    板块内按分数降序(新版本落库已如此;历史版本按快照分数就地补排,缺分殿后、同分保序) ──
  const grouped = useMemo(() => {
    const result = [];
    (edition?.items || []).forEach((item) => {
      const key = item.section || (edition.degraded_reason ? '订阅源最新更新' : '今日精选');
      const current = result.find((group) => group.key === key);
      if (current) current.items.push(item);
      else result.push({ key, items: [item] });
    });
    result.forEach((group) => {
      group.items = group.items
        .map((item, index) => ({ item, index, score: scoreOf(item) }))
        .sort((a, b) => {
          if (a.score == null && b.score == null) return a.index - b.index;
          if (a.score == null) return 1;
          if (b.score == null) return -1;
          return (b.score - a.score) || (a.index - b.index);
        })
        .map((entry) => entry.item);
    });
    return result;
  }, [edition]);

  // 头条抽出后余下的板块(空板块不画);页面显示序 = 头条 + 各板块,「早报下一条」按这个序走
  const headline = useMemo(() => pickHeadline(grouped, Boolean(edition?.degraded_reason)), [grouped, edition]);
  const acts = useMemo(() => grouped
    .map((group) => ({ ...group, items: group.items.filter((item) => item !== headline) }))
    .filter((group) => group.items.length > 0), [grouped, headline]);
  const headSection = headline ? (grouped.find((group) => group.items.includes(headline))?.key || '') : '';

  // 社交条目(codex 检视 P2):社交容器是整幅卡片流,没有「选中一条」的落点——openArticleById
  // 只会切到该源的流而非那条推文,返回带也不出。推文本体即原链:点开直接开原链;
  // 返回带的「早报下一条」序列只收阅读窗能落位的条目,社交条目不入序列。
  const isSocialRow = (row) => {
    const snapshot = row?.snapshot || {};
    return sourceMap[snapshot.source_id]?.shape === 'social' || snapshot.content_type === 'social_post';
  };
  // 原链回退:同步路径(点击手势内)开新窗;await 站内请求之后的路径用户激活可能已过期,
  // window.open 会被当弹窗拦下(noopener 下返回值恒 null,拦没拦无从探测),改当前页跳转(codex 检视 P2)
  const openExternal = (snapshot, { afterAwait = false } = {}) => {
    const url = snapshot?.source_url;
    if (!url) { showToast?.('这条内容已不在库中', 'error'); return; }
    if (afterAwait) window.location.assign(url);
    else window.open(url, '_blank', 'noopener,noreferrer');
  };

  const openItem = async (item) => {
    const snapshot = item.snapshot || {};
    if (isSocialRow(item) && snapshot.source_url) {
      supersedePendingOpen?.(); // 之前点的内链条目若仍在途,作废它,别让迟到响应把早报换成那篇
      window.open(snapshot.source_url, '_blank', 'noopener,noreferrer');
      return;
    }
    if (item.article_id && onOpenArticle) {
      // 序列按页面显示序(头条 + 板块顺序 + 板块内分数序),「早报下一条」才与读者眼前的顺序一致
      const sequence = [...(headline ? [headline] : []), ...acts.flatMap((group) => group.items)]
        .filter((row) => row.article_id && !isSocialRow(row))
        .map((row) => ({
          id: row.id ?? row.position,
          article_id: row.article_id,
          title: row.snapshot?.title || '',
          // 「早报下一条」提示同样按标题偏好选快照里的中文译名或原题(issue #218)
          title_zh: row.snapshot?.title_zh || null,
          source_url: row.snapshot?.source_url || '',
        }));
      const opened = await onOpenArticle(item.article_id, {
        date: selDate,
        revision: edition?.revision ?? null,
        scrollTop: sheetRef.current?.scrollTop || 0,
        itemId: item.id ?? item.position,
        label: `${dateTextOf(selDate)}${isToday ? ' · 今天' : ''}`,
        sequence,
        index: sequence.findIndex((row) => row.article_id === item.article_id),
      });
      // false=站内已取不到(退订自定源 / 源被隐藏后 404),退到快照原链(codex 检视 P2);
      // null=被更晚的点击盖过,什么都不做
      if (opened === false) openExternal(snapshot, { afterAwait: true });
      return;
    }
    supersedePendingOpen?.();
    openExternal(snapshot);
  };

  const items = edition?.items || [];
  // 重大事件与深度阅读 / 播客精选额外于精选之上(前者可能来自订阅外),「N 篇精选」只数用户自己那份新闻精选
  const readCount = items.filter((item) => item.selection_lane === 'read').length;
  const listenCount = items.filter((item) => item.selection_lane === 'listen').length;
  const ownItems = items.filter((item) => !EXTRA_LANES.has(item.selection_lane));
  const interestCount = ownItems.filter((item) => (item.matched_interest_codes || []).length > 0).length;
  const live = isToday && LIVE.has(status);
  const ratioUnfillable = edition?.degraded_reason === 'insufficient_non_interest_content';
  // v3.54:选篇统计(编排时一次写入;历史版本无值则事实句省略「M 篇里」);订阅为空只按兴趣编排 = interest_only
  const stats = edition?.selection_stats || null;
  const scopeCount = (edition?.expected_source_ids || []).length;
  const interestOnly = !!edition && scopeCount === 0 && (stats ? !!stats.interest_only : items.length > 0);
  const followedCount = stats?.followed_count ?? null;
  // issue #103:同事件合并到代表条目的候选数(编排时一次写入;历史版本无值则不提)
  const mergedCount = Number(stats?.same_event_merged) > 0 ? Number(stats.same_event_merged) : 0;
  const externalHits = ownItems.filter((item) => (item.matched_interest_codes || []).length > 0 && item.snapshot?.subscribed === false).length;
  const valueCount = readCount + listenCount;
  const showEdition = !loading && !detailLoading && !live && !(error && isToday)
    && !(isToday && todayStatus === 'empty_subscriptions') && !(!usesToday && detail.error)
    && status !== 'failed' && !!edition;

  const n = (value) => <b>{value}</b>;
  const valueText = valueCount > 0 && (
    <>
      另有{readCount > 0 && <> {n(readCount)} 篇深度阅读</>}
      {readCount > 0 && listenCount > 0 && '、'}
      {listenCount > 0 && <> {n(listenCount)} 集播客</>}
    </>
  );

  // ── 入选事实句(v3.54 §3,原编排说明行):降级只说新闻那份(issue #178 第三波) ──
  let fact = null;
  if (showEdition) {
    const scopeText = scopeCount > 0 ? <> {n(scopeCount)} 个订阅来源</> : '订阅来源';
    if (edition.degraded_reason && valueCount > 0) {
      const head = interestOnly
        ? <>今天没有命中你兴趣的新闻{followedCount ? <>（{n(followedCount)} 个兴趣）</> : null}</>
        : (ratioUnfillable ? '今天缺少用于补齐的非兴趣新闻' : '今天没有达到入选标准的新闻');
      fact = ownItems.length > 0
        ? <>{head}，「订阅源最新更新」是{scopeText}的最新内容，不计入正式精选；{valueText}</>
        : <>{head}，{valueText}</>;
    } else if (edition.degraded_reason) {
      fact = interestOnly
        ? <>今天没有命中你兴趣的新内容{followedCount ? <>（{n(followedCount)} 个兴趣）</> : null}</>
        : <>{ratioUnfillable ? '今天缺少用于补齐的非兴趣内容' : '今天没有达到入选标准的内容'}，以下是{scopeText}的最新更新，不计入正式精选</>;
    } else if (interestOnly) {
      fact = (
        <>
          你还没有订阅来源，本期只按你的{followedCount ? <> {n(followedCount)} 个</> : null}兴趣编排
          {stats?.candidate_count != null && <> · {n(stats.candidate_count)} 篇里</>}选出 {n(ownItems.length)} 篇
          {mergedCount > 0 && <>，同事件合并 {n(mergedCount)} 篇</>}
        </>
      );
    } else {
      fact = (
        <>
          {guest ? '从默认订阅的' : '从你订阅的'} {n(scopeCount)} 个来源{followedCount ? <>和 {n(followedCount)} 个兴趣</> : null}
          {stats?.candidate_count != null && <> · {n(stats.candidate_count)} 篇里</>}选出 {n(ownItems.length)} 篇
          {interestCount > 0 && <>，{n(interestCount)} 篇命中你的兴趣{externalHits > 0 && <>（{n(externalHits)} 篇来自订阅外）</>}</>}
          {mergedCount > 0 && <>，同事件合并 {n(mergedCount)} 篇</>}
          {valueText && <>；{valueText}</>}
        </>
      );
    }
  }

  // ── 眉头下的状态提示(原编排说明行尾句,一次只显一条:排队 > 未完成 > 已更新) ──
  let notice = null;
  if (showEdition && guest) {
    notice = {
      cls: '',
      body: (
        <>
          <span className="guest-chip">游客版</span>按默认订阅编排 ·
          <button type="button" className="bp-notice-act" onClick={() => onGuestLogin?.()}>登录后按你的订阅与兴趣编排</button>
        </>
      ),
    };
  } else if (showEdition) {
    const deadlineIncomplete = edition.sync_stale || edition.analysis_incomplete;
    const stale = usesToday && (edition.interest_stale || edition.scope_stale);
    const rebuildAct = (text) => (
      <button type="button" className="bp-notice-act" onClick={handleRebuild} disabled={working}>{working ? '编排中…' : text}</button>
    );
    if (edition.rebuild_queued) {
      notice = { cls: '', body: <><RefreshCw aria-hidden="true" />新的编排请求已记录，本版完成后会生成下一版</> };
    } else if (deadlineIncomplete) {
      notice = {
        cls: 'is-partial',
        body: (
          <>
            <Clock3 aria-hidden="true" />
            {edition.sync_stale && edition.analysis_incomplete ? '部分来源更新和文章分析' : edition.sync_stale ? '部分来源更新' : '部分文章分析'}尚未完成，按当时已有内容编排
            {usesToday && rebuildAct('重新编排')}
          </>
        ),
      };
    } else if (stale) {
      // v3.51.1(issue #33 §5):改兴趣/订阅不再自动重编,今日版面落后时把主动权交给读者
      notice = {
        cls: '',
        body: (
          <>
            <RefreshCw aria-hidden="true" />
            {edition.interest_stale && edition.scope_stale ? '你的兴趣和订阅' : edition.interest_stale ? '你的兴趣' : '你的订阅'}已更新，下次编排生效
            {rebuildAct('立即重编')}
          </>
        ),
      };
    }
  }

  const act = interestOnly
    ? { label: '去发现来源', onClick: onManageSubscriptions }
    : { label: followedCount ? '调整兴趣' : '设置兴趣', onClick: onManageInterests || onManageSubscriptions };

  // ── 海报的头条位:状态块 / 头条 / 降级说明 ──
  const stateBlock = (title, { meta = null, actions = null, tone = '', icon = null, role } = {}) => (
    <div className={`bp-state ${tone}`} role={role} aria-live={role === 'status' ? 'polite' : undefined}>
      <p className="bp-state-title">{icon}{title}</p>
      {meta && <p className="bp-state-meta">{meta}</p>}
      {actions && <div className="bp-actions">{actions}</div>}
    </div>
  );
  const browseBtn = onBrowse && (
    <button type="button" className="bp-btn" onClick={onBrowse}>先去看文章</button>
  );
  let posterBody;
  if (loading) {
    posterBody = <PosterSkeleton />;
  } else if (error && isToday) {
    posterBody = stateBlock(error, {
      tone: 'is-error',
      role: 'alert',
      actions: <button type="button" className="bp-btn" onClick={() => { setLoading(true); loadToday({ ensure: true }).finally(() => setLoading(false)); }}>重试</button>,
    });
  } else if (isToday && todayStatus === 'empty_subscriptions') {
    posterBody = stateBlock('还没有订阅来源，也没有设置兴趣', {
      meta: '订阅几个来源，或者告诉哆啦美你关心什么，明早就有第一份早报',
      actions: (
        <>
          <button type="button" className="bp-btn is-primary" onClick={onManageSubscriptions}>订阅来源</button>
          {onManageInterests && <button type="button" className="bp-btn" onClick={onManageInterests}>设置兴趣</button>}
        </>
      ),
    });
  } else if (live) {
    posterBody = stateBlock('早报编排中…', {
      role: 'status',
      icon: <Loader2 className="animate-spin" aria-hidden="true" />,
      meta: edition?.rebuild_queued ? '期间的新变更已合并，本版完成后再编排一次' : null,
    });
  } else if (detailLoading) {
    posterBody = <PosterSkeleton />;
  } else if (!usesToday && detail.error) {
    posterBody = stateBlock(detail.error, { tone: 'is-error', role: 'alert' });
  } else if (status === 'failed') {
    posterBody = stateBlock(isToday ? '今日早报没有完成' : '这一版没有完成', {
      tone: 'is-error',
      meta: edition?.error || '生成过程遇到问题',
      actions: (
        <>
          {isToday && !guest && <button type="button" className="bp-btn is-primary" onClick={handleRebuild} disabled={working}>{working ? '重试中…' : '重试生成'}</button>}
          {browseBtn}
        </>
      ),
    });
  } else if (!edition) {
    posterBody = stateBlock('这一天没有早报');
  } else if (grouped.length === 0) {
    posterBody = stateBlock(
      interestOnly
        ? (isToday ? '今天还没有命中你兴趣的内容' : '这一天没有命中你兴趣的内容')
        : (isToday ? (guest ? '默认订阅的来源今天还没有可展示的更新' : '你的订阅源今天还没有可展示的更新') : '这一天的订阅源没有可展示的更新'),
      {
        actions: (
          <>
            <button type="button" className="bp-btn" onClick={onManageSubscriptions}>{interestOnly ? '去发现来源' : '管理订阅'}</button>
            {isToday && browseBtn}
          </>
        ),
      },
    );
  } else if (headline) {
    const headWhy = whyOf(headline, headline.snapshot || {}, '');
    posterBody = (
      <Headline
        item={headline}
        kicker={headline.selection_lane === 'breaking' ? `头条 · ${headWhy.text}` : `头条 · ${headSection}`}
        source={sourceMap[headline.snapshot?.source_id]}
        flash={flashId != null && (headline.id ?? headline.position) === flashId}
        restCount={items.length - 1}
        onOpen={openItem}
        onSubscribeSource={onSubscribeSource}
        favoriteIds={favoriteIds}
        onToggleFavorite={onToggleFavorite}
      />
    );
  } else {
    // 降级版面:不设头条,头条位如实说明
    posterBody = (
      <div className="bp-head is-note">
        <p className="bp-kicker">{ownItems.length > 0 ? '最新更新' : '今日早报'}</p>
        <h1 className="bp-title is-note">
          {interestOnly ? '今天没有命中你兴趣的新内容' : ratioUnfillable ? '今天缺少用于补齐的非兴趣内容' : '今天没有达到入选标准的内容'}
        </h1>
        <p className="bp-sum">
          {ownItems.length > 0 ? `下面是${guest ? '默认订阅' : '你订阅'}来源的最新更新，不计入正式精选` : '下面是按内容价值入选的阅读与收听'}
        </p>
      </div>
    );
  }

  // 首登引导横幅(issue #56 方案 B):与发现页兴趣段的 .interest-onb 同一形制,挂在海报之上——
  // 先给内容再邀请投入;不锁页,「稍后再说」即永久收起
  const onboardingBanner = onboarding && (
    <div className="interest-onb brief-onb" role="region" aria-label="初始设置">
      <div className="interest-onb-main">
        <div className="brief-mast-kicker">欢迎来到哆啦美</div>
        <p className="interest-onb-text">
          这是你的第一份早报。<b>设置兴趣</b>，早报会更懂你。
        </p>
      </div>
      <div className="brief-onb-acts">
        <button type="button" className="interest-btn" disabled={skipping} onClick={handleSkipOnboarding}>稍后再说</button>
        {onSetupInterests && <button type="button" className="interest-btn is-primary" disabled={skipping} onClick={onSetupInterests}>设置兴趣</button>}
      </div>
    </div>
  );

  const multiRev = selDay.revisions.length > 1;
  const topNote = showEdition
    ? [
      edition.generated_at ? `${clockOf(edition.generated_at)} 编排` : null,
      multiRev && edition.revision ? `第 ${edition.revision} 版` : null,
      items.length > 0 ? `${items.length} 条` : null,
    ].filter(Boolean).join(' · ')
    : '';
  const pastDays = days.filter((day) => day.key !== selDate).slice(0, PAST_CHIPS);

  const poster = (
    <section className={`bp-poster ${headline && showEdition ? 'has-head' : ''}`} aria-label="头条">
      <div className="bp-light" aria-hidden="true" />
      <div className="bp-wrap">
        {onboardingBanner}
        <div className="bp-top">
          <p className="bp-eyebrow">
            <b>早报</b>
            <i aria-hidden="true" />
            <span>{dateTextOf(selDate)} {weekdayOf(selDate)}{isToday ? ' · 今天' : ''}</span>
          </p>
          <div className="bp-top-r">
            {topNote && <span className="bp-top-note">{topNote}</span>}
            <button
              ref={histTriggerRef}
              type="button"
              className="bp-hist"
              aria-haspopup="dialog"
              aria-expanded={histOpen}
              onClick={() => setHistOpen((open) => !open)}
            >
              <CalendarDays aria-hidden="true" />
              往期
              <ChevronDown className="bp-hist-caret" aria-hidden="true" />
            </button>
            {!isToday && (
              <button type="button" className="bp-lbtn" onClick={() => pickDay(todayKey)} aria-label="回到今天" title="回到今天">
                <Undo2 aria-hidden="true" />
                <span className="bp-lbtn-label">回到今天</span>
              </button>
            )}
            {isToday && !guest && (
              <button
                type="button"
                className="bp-lbtn"
                onClick={handleRebuild}
                disabled={working || loading || live}
                title={live ? '今日早报正在准备' : '重新编排今日早报'}
                aria-label={live ? '今日早报正在准备' : '重新编排今日早报'}
              >
                <RefreshCw className={working || live ? 'animate-spin' : ''} aria-hidden="true" />
                <span className="bp-lbtn-label">重新编排</span>
              </button>
            )}
            {histOpen && (
              <BriefHistoryCalendar
                days={days}
                todayKey={todayKey}
                selDate={selDate}
                selRevision={edition?.revision ?? selRevision}
                live={live}
                onPickDay={pickDay}
                onPickRevision={pickRevision}
                onClose={closeHistory}
                triggerRef={histTriggerRef}
              />
            )}
          </div>
        </div>
        {notice && <p className={`bp-notice ${notice.cls}`} role="status">{notice.body}</p>}
        {posterBody}
      </div>
    </section>
  );

  const credits = !loading && (fact || pastDays.length > 0) && (
    <section className="bp-credits" aria-label="本期编排">
      <h2 className="bp-credits-title">本期编排</h2>
      <dl>
        {showEdition && (
          <>
            <dt>编排</dt>
            <dd>
              <b className="ai-grad-text bp-by">哆啦美</b>
              {' · '}{dateTextOf(selDate)}{edition.generated_at ? ` ${clockOf(edition.generated_at)}` : ''}
              {edition.revision ? ` · 第 ${edition.revision} 版` : ''}
              {/* 「降级生成」只指选篇降级(内容不足退到最新更新);来源 / 分析未就绪由眉头下的提示行说明 */}
              {edition.degraded_reason ? ' · 降级生成' : ''}
            </dd>
          </>
        )}
        {fact && (
          <>
            <dt>入选</dt>
            <dd>
              {fact}
              {act.onClick && (
                <button type="button" className="bp-credits-act" onClick={act.onClick}>
                  {act.label}
                  <ArrowUpRight aria-hidden="true" />
                </button>
              )}
            </dd>
          </>
        )}
        {showEdition && headline && (
          <>
            <dt>头条</dt>
            <dd>
              {VALUE_LANES.has(headline.selection_lane)
                ? '本期没有新闻入选，取深度阅读与播客精选里内容价值最高的一条'
                : '有重大事件时取重大事件，否则取本期新闻价值最高的一条'}
              ；其余按板块排列，板块内按分数
            </dd>
          </>
        )}
        {pastDays.length > 0 && (
          <>
            <dt>往期</dt>
            <dd>
              <span className="bp-chips">
                {pastDays.map((day) => (
                  <button key={day.key} type="button" className="bp-chip" onClick={() => pickDay(day.key)}>
                    {dayNameOf(day.key, todayKey)}
                  </button>
                ))}
              </span>
            </dd>
          </>
        )}
      </dl>
    </section>
  );

  return (
    <section className={`bp-sheet ${mobile ? 'is-mobile' : ''}`} aria-label="我的早报" ref={sheetRef}>
      {poster}
      <div className="bp-wrap">
        {showEdition && acts.map((group) => (
          <section key={group.key} className="bp-act" aria-label={group.key}>
            <div className="bp-act-head">
              <h2 className="bp-act-name">{group.key}</h2>
              <p className="bp-act-count">{group.items.length} 条</p>
            </div>
            <div className="bp-items">
              {group.items.map((item) => (
                <BriefItem
                  key={item.id || item.position}
                  item={item}
                  source={sourceMap[item.snapshot?.source_id]}
                  flash={flashId != null && (item.id ?? item.position) === flashId}
                  onOpen={openItem}
                  onSubscribeSource={onSubscribeSource}
                  favoriteIds={favoriteIds}
                  onToggleFavorite={onToggleFavorite}
                />
              ))}
            </div>
          </section>
        ))}
        {credits}
      </div>
    </section>
  );
}
