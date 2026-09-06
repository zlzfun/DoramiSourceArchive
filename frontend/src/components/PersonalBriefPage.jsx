import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowUpRight, ChevronDown, Clock3, Loader2, RefreshCw } from 'lucide-react';
import {
  ensurePersonalBrief,
  fetchPersonalBrief,
  fetchPersonalBriefs,
  fetchTodayPersonalBrief,
  rebuildPersonalBrief,
} from '../api';
import LogoMark from './LogoMark';
import { resolveCompany } from '../sourceTaxonomy';
import { formatDateTime, formatRelativeTime } from '../utils/datetime';
import { fmtDayKey, WEEKDAY_CHARS } from '../utils/readerTime';
import { qualityScoreText, SCORE_DISCLAIMER } from '../utils/analysis';

// ── 我的早报(issue #23 重做,二稿)──
// 桌面 = 视图轨 · 日期栏(顶替源栏槽位) · 报纸面(占条目列 + 阅读窗整幅):一份卡片式日报——
// 报头(日期/版次/篇数)+ 分节卡片网格(标题/完整摘要/评分/标签),点卡片跳站内原文
// (openArticleById:切到该篇所在容器并选中,早报页退场)。快照弹窗、选入理由长句退役。
// 历史按天分期:一天一行,同日多版折进报头「共 N 版」。移动壳:横向日期条 + 同一张报纸面。
// 三稿(目检返修):头条卡双栏(左标题/摘要,右评分与标签栏——去掉限行宽留下的右侧空白,不放图:
// 全库近 30 天带图文章不足一成,头条会在两种形态间跳);同行卡片等高、脚部钉底;
// 「外出返回」:点卡片进原文时把 {日期,版次,卷动位置,条目序列} 交给阅读器,阅读窗顶部出返回带
// (返回我的早报 / 早报下一条),返回时经 restore 落回同一版同一位置并短暂高亮刚读过的卡。

const TERMINAL = new Set(['ready', 'degraded', 'failed', 'superseded']);
const LIVE = new Set(['pending', 'generating']);
const HISTORY_LIMIT = 100;
const POLL_MS = 8000;

const tagName = (tag) => tag?.name_zh || tag?.name_en || tag?.label || tag?.code || '';
const weekdayOf = (key) => {
  const d = new Date(`${key}T00:00:00`);
  return Number.isNaN(d.getTime()) ? '' : `周${WEEKDAY_CHARS[d.getDay()]}`;
};
const dateTextOf = (key) => `${Number(key.slice(5, 7))}月${Number(key.slice(8, 10))}日`;
const clockOf = (iso) => (iso ? formatDateTime(iso).slice(11, 16) : '');

// 日期行的人话名:今天 / 昨天 / 9月3日
function dayNameOf(key, todayKey) {
  if (key === todayKey) return '今天';
  const y = new Date(`${todayKey}T00:00:00`);
  y.setDate(y.getDate() - 1);
  if (key === fmtDayKey(y)) return '昨天';
  return dateTextOf(key);
}

// 月分组签:同年只写「9 月」,跨年补年份
function monthLabelOf(key, todayKey) {
  const year = key.slice(0, 4);
  const month = Number(key.slice(5, 7));
  return year === todayKey.slice(0, 4) ? `${month} 月` : `${year} 年 ${month} 月`;
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

function BriefCard({ item, lead, wide = false, source, onOpen, flash = false }) {
  const snapshot = item.snapshot || {};
  const score = qualityScoreText(item.quality_score ?? snapshot.quality_score);
  // display_tags 是读者面投影(规范标签 + 灵活标签,codex 检视 P2);tags 只有规范指派,老快照回退用
  const tags = Array.isArray(snapshot.display_tags) ? snapshot.display_tags : (snapshot.tags || []);
  const interest = interestLabelOf(item, snapshot);
  const chips = [];
  if (interest) chips.push({ key: 'interest', text: `关注 · ${interest}`, cls: 'is-interest', title: '命中你关注的兴趣' });
  tags
    .filter((tag) => tagName(tag) && tagName(tag) !== interest)
    .slice(0, lead ? 3 : 2)
    .forEach((tag, index) => chips.push({
      key: `${tag.type || 'canonical'}-${tag.code || tag.id || tag.candidate_id || index}`,
      text: tagName(tag),
      cls: tag.type === 'extracted' ? 'is-extracted' : '',
    }));
  // one_sentence_summary 自 v3.45.1 取缔;历史 edition 快照仍带该键,保留回退读取
  const summary = snapshot.summary || snapshot.one_sentence_summary || '';
  const sourceName = snapshot.source_name || source?.name || snapshot.source_id || '未知来源';
  const company = source ? resolveCompany(source) : resolveCompany({ source_id: snapshot.source_id, name: sourceName, user_source: true });
  const chipNodes = chips.map((chip) => (
    <span key={chip.key} className={`reader-tag-chip ${chip.cls || ''}`} title={chip.title}>{chip.text}</span>
  ));
  const scoreNode = score && (
    <span className="brief-card-score" title={SCORE_DISCLAIMER} aria-label={`内容价值分 ${score}`}>
      <span className="ai-grad-text">{score}</span>
    </span>
  );
  const timeNode = (
    <span className="brief-card-time">
      {snapshot.publish_date && (
        <span title={formatDateTime(snapshot.publish_date)}>{formatRelativeTime(snapshot.publish_date, '')}</span>
      )}
      <ArrowUpRight className="brief-card-go" aria-hidden="true" />
    </span>
  );
  const srcNode = (
    <span className="brief-card-src">
      <LogoMark company={company} size="s17" emoji={source?.icon} />
      <span className="brief-card-srcname">{sourceName}</span>
    </span>
  );
  // 通栏双栏:头条(首节首张)与单卡分节(一张卡占三列网格的一格、旁边两格空着很怪)都通栏,
  // 左栏来源/标题/摘要/时刻,右栏评分(大号 + 「内容价值分」注脚)与标签竖排;头条只多放大字号。
  // 右栏有内容才双栏;无分无签(极少)退回普通卡形态。
  const split = (lead || wide) && (score || chips.length > 0);
  const cls = `brief-card ${lead ? 'is-lead' : ''} ${wide && !lead ? 'is-wide' : ''} ${split ? 'is-split' : ''} ${flash ? 'is-just-read' : ''}`;

  if (split) {
    return (
      <button type="button" className={cls} onClick={() => onOpen(item)}>
        <span className="brief-card-main">
          <span className="brief-card-head">{srcNode}</span>
          <span className="brief-card-title">{snapshot.title || '（无标题）'}</span>
          {summary && <span className="brief-card-sum">{summary}</span>}
          <span className="brief-card-foot">{timeNode}</span>
        </span>
        <span className="brief-card-aside">
          {score && (
            <span className="brief-card-aside-score">
              {scoreNode}
              <span className="brief-card-aside-cap">内容价值分</span>
            </span>
          )}
          {chips.length > 0 && <span className="brief-card-tags is-stack">{chipNodes}</span>}
        </span>
      </button>
    );
  }

  return (
    <button type="button" className={cls} onClick={() => onOpen(item)}>
      <span className="brief-card-head">
        {srcNode}
        {scoreNode}
      </span>
      <span className="brief-card-title">{snapshot.title || '（无标题）'}</span>
      {summary && <span className="brief-card-sum">{summary}</span>}
      <span className="brief-card-foot">
        {chips.length > 0 && <span className="brief-card-tags">{chipNodes}</span>}
        {timeNode}
      </span>
    </button>
  );
}

function CardsSkeleton() {
  const rows = [['w-3/4', 'w-full', 'w-5/6'], ['w-2/3', 'w-full', 'w-1/2'], ['w-4/5', 'w-11/12', 'w-2/3'], ['w-3/5', 'w-full', 'w-3/4']];
  return (
    <div className="brief-grid skeleton-delay" aria-hidden="true">
      {rows.map((r, i) => (
        <div key={i} className="brief-card is-skel">
          <div className="skeleton h-3 w-24" />
          <div className={`skeleton mt-4 h-4 ${r[0]}`} />
          <div className={`skeleton mt-3 h-3 ${r[1]}`} />
          <div className={`skeleton mt-2 h-3 ${r[2]}`} />
        </div>
      ))}
    </div>
  );
}

function RevisionStrip({ revisions, current, onPick }) {
  const label = { ready: '', degraded: '降级', failed: '失败', pending: '准备中', generating: '编排中' };
  return (
    <div className="brief-rev-strip" role="tablist" aria-label="同日版本">
      {revisions.map((rev) => (
        <button
          key={rev.revision}
          type="button"
          role="tab"
          aria-selected={rev.revision === current}
          className={`brief-rev ${rev.revision === current ? 'is-on' : ''}`}
          onClick={() => onPick(rev.revision)}
        >
          {`第 ${rev.revision} 版`}
          {(clockOf(rev.generated_at) || label[rev.status]) && (
            <small>{[clockOf(rev.generated_at), label[rev.status]].filter(Boolean).join(' ')}</small>
          )}
        </button>
      ))}
    </div>
  );
}

export default function PersonalBriefPage({
  showToast,
  onManageSubscriptions,
  onOpenArticle,
  sourceMap = {},
  interestVersion = 0,
  mobile = false,
  // 从阅读窗「返回我的早报」回来时带的外出上下文 {date, revision, scrollTop, itemId};挂载时一次性读取
  restore = null,
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
  const [revOpen, setRevOpen] = useState(false);
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
    setRevOpen(false);
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

  const months = useMemo(() => {
    const result = [];
    days.forEach((day) => {
      const key = day.key.slice(0, 7);
      const current = result[result.length - 1];
      if (current && current.key === key) current.days.push(day);
      else result.push({ key, label: monthLabelOf(day.key, todayKey), days: [day] });
    });
    return result;
  }, [days, todayKey]);

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

  const edition = usesToday ? todayEdition : (detail.key === viewKey ? detail.edition : null);
  const detailLoading = !usesToday && (detail.key !== viewKey || detail.loading);

  // 返回落位:卡片进 DOM 的同一提交里把卷动位置放回去(layout effect,不闪一帧顶部),
  // 刚读过的那张卡短暂高亮;只消费一次,之后本次挂载内换期照常回顶。
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

  const pickDay = (key) => {
    setSel({ date: key === todayKey ? null : key, revision: null });
    setRevOpen(false);
  };
  const pickRevision = (revision) => setSel((prev) => ({ ...prev, revision }));

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

  // 点卡片 → 站内原文;文章已不在库(article_id 空)时退到原链。
  // 随行交出外出上下文:所在版 + 卷动位置 + 本版可跳条目序列(阅读窗返回带据此「返回 / 下一条」)
  const openItem = (item) => {
    if (item.article_id && onOpenArticle) {
      const sequence = (edition?.items || [])
        .filter((row) => row.article_id)
        .map((row) => ({ id: row.id ?? row.position, article_id: row.article_id, title: row.snapshot?.title || '' }));
      onOpenArticle(item.article_id, {
        date: selDate,
        revision: edition?.revision ?? null,
        scrollTop: sheetRef.current?.scrollTop || 0,
        itemId: item.id ?? item.position,
        label: `${dateTextOf(selDate)}${isToday ? ' · 今天' : ''}`,
        sequence,
        index: sequence.findIndex((row) => row.article_id === item.article_id),
      });
      return;
    }
    const url = item.snapshot?.source_url;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
    else showToast?.('这条内容已不在库中', 'error');
  };

  // ── 分组:按 section 保序 ──
  const grouped = useMemo(() => {
    const result = [];
    (edition?.items || []).forEach((item) => {
      const key = item.section || (edition.degraded_reason ? '订阅源最新更新' : '今日精选');
      const current = result.find((group) => group.key === key);
      if (current) current.items.push(item);
      else result.push({ key, items: [item] });
    });
    return result;
  }, [edition]);

  const items = edition?.items || [];
  const interestCount = items.filter((item) => (item.matched_interest_codes || []).length > 0).length;
  const sourceCount = new Set(items.map((item) => item.snapshot?.source_id).filter(Boolean)).size;
  const live = isToday && LIVE.has(status);
  const readiness = edition?.readiness || {};
  const sourceReadiness = readiness.sources || {};
  const analysisReadiness = readiness.analysis || {};
  const pendingSourceNames = (sourceReadiness.pending_sources || []).map((s) => s.name).filter(Boolean);
  const readinessLine = [
    sourceReadiness.total > 0 ? `来源更新 ${sourceReadiness.completed || 0}/${sourceReadiness.total}` : null,
    analysisReadiness.total > 0 ? `文章分析 ${analysisReadiness.completed || 0}/${analysisReadiness.total}` : null,
  ].filter(Boolean).join(' · ');
  const ratioUnfillable = edition?.degraded_reason === 'insufficient_non_interest_content';

  // ── 报头 ──
  const kicker = [
    '我的早报',
    edition?.revision ? `第 ${edition.revision} 版` : null,
    edition?.generated_at ? `${clockOf(edition.generated_at)} 生成` : null,
    // 「降级生成」只指选篇降级(内容不足退到最新更新);来源/分析未就绪由报体的提示行说明
    edition?.degraded_reason ? '降级生成' : null,
  ].filter(Boolean).join(' · ');
  const subline = items.length > 0
    ? [
      `${items.length} 篇${edition?.degraded_reason ? '最新更新' : '精选'}`,
      interestCount > 0 ? `${interestCount} 篇命中你的兴趣` : null,
      sourceCount > 0 ? `来自 ${sourceCount} 个来源` : null,
    ].filter(Boolean).join(' · ')
    : '';
  const dayName = dayNameOf(selDate, todayKey);

  // ── 报体 ──
  let body;
  if (loading) {
    body = <CardsSkeleton />;
  } else if (error && isToday) {
    body = (
      <div className="brief-state is-error" role="alert">
        <span>{error}</span>
        <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => { setLoading(true); loadToday({ ensure: true }).finally(() => setLoading(false)); }}>重试</button>
      </div>
    );
  } else if (isToday && todayStatus === 'empty_subscriptions') {
    body = (
      <div className="brief-state">
        <span className="brief-state-title">还没有订阅来源</span>
        <span className="brief-state-meta">早报只在你订阅的来源里编排，先去发现页添加几个</span>
        <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={onManageSubscriptions}>去发现来源</button>
      </div>
    );
  } else if (live) {
    body = (
      <div className="brief-state" role="status" aria-live="polite">
        <Clock3 aria-hidden="true" />
        <span className="brief-state-title">{status === 'generating' ? '正在编排今日早报…' : '正在等待订阅源和文章分析就绪'}</span>
        {readinessLine && <span className="brief-state-meta">{readinessLine}</span>}
        {pendingSourceNames.length > 0 && (
          <span className="brief-state-meta">仍在等待：{pendingSourceNames.slice(0, 3).join('、')}{pendingSourceNames.length > 3 ? `等 ${pendingSourceNames.length} 个来源` : ''}</span>
        )}
        <span className="brief-state-meta">
          {readiness.check_started === false ? '08:30 后开始检查就绪状态' : '到最晚检查时间仍未全部就绪时，用已完成的内容生成'}
          {edition?.deadline_at ? ` · 最晚 ${clockOf(edition.deadline_at)}` : ''}
        </span>
        {edition?.rebuild_queued && <span className="brief-state-meta">期间的新变更已合并，本版完成后再编排一次</span>}
      </div>
    );
  } else if (detailLoading) {
    body = <CardsSkeleton />;
  } else if (!usesToday && detail.error) {
    body = <div className="brief-state is-error" role="alert"><span>{detail.error}</span></div>;
  } else if (status === 'failed') {
    body = (
      <div className="brief-state is-error">
        <span className="brief-state-title">{isToday ? '今日早报没有完成' : '这一版没有完成'}</span>
        <span className="brief-state-meta">{edition?.error || '生成过程遇到问题'}</span>
        {isToday && (
          <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={handleRebuild} disabled={working}>{working ? '重试中…' : '重试生成'}</button>
        )}
      </div>
    );
  } else if (!edition) {
    body = <div className="brief-state"><span className="brief-state-meta">这一天没有早报</span></div>;
  } else {
    const deadlineIncomplete = edition.sync_stale || edition.analysis_incomplete;
    body = (
      <>
        {deadlineIncomplete && (
          <div className="brief-note">
            <Clock3 aria-hidden="true" />
            <span>
              {edition.sync_stale && edition.analysis_incomplete ? '部分来源更新和文章分析' : edition.sync_stale ? '部分来源更新' : '部分文章分析'}
              未在截止前完成，本版按已就绪的内容生成
            </span>
          </div>
        )}
        {edition.rebuild_queued && (
          <div className="brief-note is-info">
            <RefreshCw aria-hidden="true" />
            <span>新的编排请求已记录，本版完成后会生成下一版</span>
          </div>
        )}
        {edition.degraded_reason && (
          <div className="brief-note">
            <AlertTriangle aria-hidden="true" />
            <span>
              {ratioUnfillable
                ? '今天缺少用于补齐的非兴趣内容，以下是订阅源最新更新，不计入正式精选'
                : '今天没有达到入选标准的内容，以下是订阅源最新更新，不计入正式精选'}
            </span>
          </div>
        )}
        {grouped.length === 0 ? (
          <div className="brief-state">
            <span className="brief-state-meta">{isToday ? '你的订阅源今天还没有可展示的更新' : '这一天的订阅源没有可展示的更新'}</span>
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={onManageSubscriptions}>管理订阅</button>
          </div>
        ) : grouped.map((group, groupIndex) => (
          <section key={group.key} className="brief-sec" aria-label={group.key}>
            <div className="brief-sec-head">
              <span className="brief-sec-title">{group.key}</span>
              <span className="brief-sec-count">{group.items.length}</span>
              <span className="brief-sec-rule" aria-hidden="true" />
            </div>
            <div className="brief-grid">
              {group.items.map((item, index) => (
                <BriefCard
                  key={item.id || item.position}
                  item={item}
                  lead={groupIndex === 0 && index === 0 && !edition.degraded_reason}
                  wide={group.items.length === 1}
                  flash={flashId != null && (item.id ?? item.position) === flashId}
                  source={sourceMap[item.snapshot?.source_id]}
                  onOpen={openItem}
                />
              ))}
            </div>
          </section>
        ))}
      </>
    );
  }

  const sheet = (
    <div className="brief-sheet-inner">
      <header className="brief-mast">
        <div className="brief-mast-main">
          <div className="brief-mast-kicker">{kicker}</div>
          <h1 className="brief-mast-title">
            {dateTextOf(selDate)}
            <small>{[weekdayOf(selDate), isToday ? '今天' : dayName === '昨天' ? '昨天' : null].filter(Boolean).join(' · ')}</small>
          </h1>
          {subline && <p className="brief-mast-sub">{subline}</p>}
        </div>
        <div className="brief-mast-actions">
          {selDay.revisions.length > 1 && (
            <button type="button" className="brief-rev-toggle" aria-expanded={revOpen} onClick={() => setRevOpen((v) => !v)}>
              {`共 ${selDay.revisions.length} 版`}
              <ChevronDown aria-hidden="true" />
            </button>
          )}
          {isToday && (
            <button
              type="button"
              className="icon-button"
              onClick={handleRebuild}
              disabled={working || loading || live}
              title={live ? '今日早报正在准备' : '重新编排今日早报'}
              aria-label={live ? '今日早报正在准备' : '重新编排今日早报'}
            >
              <RefreshCw className={`h-4 w-4 ${working || live ? 'animate-spin' : ''}`} />
            </button>
          )}
        </div>
      </header>
      {revOpen && selDay.revisions.length > 1 && (
        <RevisionStrip revisions={selDay.revisions} current={edition?.revision ?? selRevision} onPick={pickRevision} />
      )}
      {body}
    </div>
  );

  if (mobile) {
    return (
      <div className="brief-m" aria-label="我的早报">
        <div className="brief-m-days" role="tablist" aria-label="早报日期">
          {days.map((day) => {
            const on = day.key === selDate;
            const dayLive = day.key === todayKey ? live : LIVE.has(day.latest?.status);
            return (
              <button key={day.key} type="button" role="tab" aria-selected={on} className={`brief-m-day ${on ? 'is-on' : ''}`} onClick={() => pickDay(day.key)}>
                {dayNameOf(day.key, todayKey)}
                {dayLive && <Loader2 className="animate-spin" aria-label="编排中" />}
              </button>
            );
          })}
        </div>
        <div className="brief-sheet is-mobile" ref={sheetRef}>{sheet}</div>
      </div>
    );
  }

  return (
    <>
      <aside className="reader-col brief-days" aria-label="早报日期">
        <div className="reader-src-head"><span className="reader-src-title">我的早报</span></div>
        <div className="brief-days-scroll">
          {months.map((month) => (
            <div key={month.key}>
              <div className="reader-src-label">{month.label}</div>
              {month.days.map((day) => {
                const on = day.key === selDate;
                const dayLive = day.key === todayKey ? live : LIVE.has(day.latest?.status);
                const failed = !dayLive && day.latest?.status === 'failed';
                return (
                  <button
                    key={day.key}
                    type="button"
                    className={`brief-day ${on ? 'is-on' : ''}`}
                    aria-pressed={on}
                    onClick={() => pickDay(day.key)}
                  >
                    <span className="brief-day-name">{dayNameOf(day.key, todayKey)}</span>
                    {dayLive
                      ? <Loader2 className="brief-day-spin animate-spin" aria-label="编排中" />
                      : failed
                        ? <span className="brief-day-flag is-bad">失败</span>
                        : <span className="brief-day-meta">{weekdayOf(day.key)}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </aside>
      <section className="brief-sheet" aria-label="早报" ref={sheetRef}>{sheet}</section>
    </>
  );
}
