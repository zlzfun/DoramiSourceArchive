/**
 * 运维首页(issue #114,样页 v4「A 精修」):首页即目录。12 列不等大 bento——
 * 行 1 = 账户 L(7)+ 阅读与榜单 L(5);行 2 = 读者功能 / 播客 / 分析与标签 M(各 3)+ 状态(3,纵跨两行);
 * 行 3 = 存储与外部资源 / 自定源 / 反馈与公告 S(各 3)。整块磁贴是入口(页名链接的 ::after 铺满),图表点进对应板块。
 * 首页零说明句:每块只留 1 个主角数字(L 另有 ≤ 3 个次级数字)+ 1 张图,被削的数进 hover 浮层。
 * 告警只在「状态」磁贴出现一次;其它磁贴有待处理时只给一个状态点。
 * 数据:各接口独立加载、各自代次守卫,一条失败只让那一块显示「—」(conventions §8.x loader 纪律)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import {
  fetchAdminHome, fetchAdminAccountGrowth, fetchAdminGuest, fetchAdminContent, fetchAdminRankingStatus,
  fetchAiUsage, getAiBetaGlobal, fetchPodcastArtifactStats, fetchPodcastPremiumTasks, fetchAnalysisMetrics,
  fetchMediaStats, fetchStorageStatus, getXApiQuota, fetchAdminUserSources, fetchAdminFeedback,
  fetchAdminAnnouncements, fetchTaxonomyState, fetchPodcastAsrQuota, fetchReaderOndemandGlobal, fetchPublicShareGlobal,
} from '../../api';
import { OPS_PAGES } from '../../utils/opsNav';
import {
  aiReaderSplit, budgetRatio, bytesParts, growthWindow, pageMarks, pendingItems, readTop, stageParts, switchDots, windowDays,
} from '../../utils/opsHome';
import { rankingSnapshotStatusMeta } from '../../utils/rankingAdmin';
import { OpsIcon } from './opsIcons';
import { BarList, DailyBars, Donut, OpsTipProvider, RollNumber, ScoreHistogram, StepLine } from './OpsCharts';
import { useAnchorTip } from './opsChartKit';

const PAGE = Object.fromEntries(OPS_PAGES.map((p) => [p.id, p]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const DASH = '—';

// 首页数据:窗口类随 days 重取,其余只随 refreshTick 重取;每个键独立代次守卫,迟到响应不覆盖新快照。
function useHomeData(days, refreshTick) {
  const [snap, setSnap] = useState({});
  const gens = useRef({});
  const load = useCallback((key, fn) => {
    const gen = (gens.current[key] || 0) + 1;
    gens.current[key] = gen;
    fn()
      .then((v) => { if (gens.current[key] === gen) setSnap((s) => ({ ...s, [key]: v, [`${key}Err`]: false })); })
      .catch(() => { if (gens.current[key] === gen) setSnap((s) => ({ ...s, [`${key}Err`]: true })); });
  }, []);
  useEffect(() => {
    load('home', () => fetchAdminHome(days, 8));
    load('guest', () => fetchAdminGuest(days));
    load('usage', () => fetchAiUsage(days));
    load('analysis', () => fetchAnalysisMetrics(days));
  }, [load, days, refreshTick]);
  useEffect(() => {
    load('growth', () => fetchAdminAccountGrowth());
    load('content', () => fetchAdminContent());
    load('rank', () => fetchAdminRankingStatus());
    load('aiGlobal', () => getAiBetaGlobal());
    load('podStats', () => fetchPodcastArtifactStats());
    load('podTasks', () => fetchPodcastPremiumTasks({ page_size: 1 }));
    load('media', () => fetchMediaStats());
    load('storage', () => fetchStorageStatus());
    load('xQuota', () => getXApiQuota());
    load('usrc', () => fetchAdminUserSources());
    load('feedback', () => fetchAdminFeedback(null, { limit: 1 }));
    load('notices', () => fetchAdminAnnouncements());
    load('taxonomy', () => fetchTaxonomyState());
    load('asr', () => fetchPodcastAsrQuota());
    load('ondemand', () => fetchReaderOndemandGlobal());
    load('share', () => fetchPublicShareGlobal());
  }, [load, refreshTick]);
  return snap;
}

/* ── 磁贴件 ── */
function TileMark({ tone, title }) {
  if (!tone) return null;
  return <span className="ops-tile-dot" title={title} aria-label={title}><i className={`ops-mark${tone === 'bad' ? ' is-bad' : ''}`} /></span>;
}

function TileHead({ page, onGo, meta = null, mark = null, markTitle = '', tipBuild = null }) {
  const tipProps = useAnchorTip(tipBuild || (() => null));
  const p = PAGE[page];
  return (
    <div className="ops-tile-head">
      <OpsIcon name={p.icon} className="ops-tile-icon" />
      <a
        className="card-title ops-tile-name"
        href={`#/admin/${page}`}
        onClick={(e) => { e.preventDefault(); onGo(page); }}
        {...(tipBuild ? tipProps : {})}
      >
        {p.name}
      </a>
      <span className="ops-tile-meta">{meta}<TileMark tone={mark} title={markTitle} /></span>
    </div>
  );
}

// 数字 + 标签:标签在上、数字在下(S 磁贴同一行);tip = 被削掉的说明(hover / 焦点浮层)。
function Num({ label, value, hero = false, tone = '', tipBuild = null, unit = '', roll = null }) {
  const tipProps = useAnchorTip(tipBuild || (() => null));
  const hasTip = Boolean(tipBuild);
  return (
    <span className={`ops-kpi${hero ? ' is-hero' : ''}${hasTip ? ' has-tip' : ''}`} {...(hasTip ? { ...tipProps, tabIndex: 0 } : {})}>
      <span className="ops-kpi-lbl">{label}</span>
      <span className={`ops-kpi-num${tone ? ` ${tone}` : ''}`}>
        {roll ? <RollNumber value={roll.value} format={roll.format} /> : value}
        {unit ? <small>{unit}</small> : null}
      </span>
    </span>
  );
}

/* ── 状态磁贴:按严重度排的待处理 + 8 个全站开关点(只读,点击跳所在板块) ── */
function StatusTile({ items, dots, onGo }) {
  return (
    <section className={`surface-card ops-tile ops-status t-status${items.length ? '' : ' is-calm'}`} aria-label="状态">
      <div className="ops-tile-head">
        <OpsIcon name="activity" className="ops-tile-icon" />
        <h2 className="card-title ops-status-title">状态</h2>
        {items.length > 0 && <span className="ops-count" title="待处理">{items.length}</span>}
      </div>
      {items.length ? (
        <div className="ops-pendlist">
          {items.map((it) => (
            <button key={it.key} type="button" className={`ops-pend is-${it.tone}`} title={`前往 ${PAGE[it.page].name}`} onClick={() => onGo(it.page, it.block)}>
              <i className={`ops-mark${it.tone === 'bad' ? ' is-bad' : ''}`} aria-hidden="true" />
              <span className="ops-pend-name">{it.name}</span>
              {it.value !== '' && <span className="ops-pend-num">{it.value}</span>}
              <ChevronRight aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <div className="ops-pend-none"><span className="ai-light" aria-hidden="true" />无待处理</div>
      )}
      <div className="ops-dots" aria-label="全站开关">
        {dots.map((d) => {
          const where = `${PAGE[d.page].name}`;
          const state = d.on === null ? '读取中' : d.on ? '开' : '关';
          return (
            <a
              key={d.key}
              className={`ops-dot${d.on === false ? ' is-off' : ''}`}
              href={`#/admin/${d.page}/${d.block}`}
              aria-label={`${d.name} · ${state} · 前往 ${where}`}
              onClick={(e) => { e.preventDefault(); onGo(d.page, d.block); }}
            >
              <span className={`ai-light${d.on === false ? ' is-off' : ''}${d.on === null ? ' is-unknown' : ''}`} aria-hidden="true" />
              <span className="ops-dot-name">{d.name}</span>
            </a>
          );
        })}
      </div>
    </section>
  );
}

export default function OpsHome({ days, refreshTick, onGo }) {
  const s = useHomeData(days, refreshTick);
  const today = windowDays(1)[0];
  const items = useMemo(() => pendingItems(s), [s]);
  const marks = useMemo(() => pageMarks(items), [items]);
  const markTitle = (page) => {
    const hits = items.filter((x) => x.page === page).map((x) => x.name);
    return hits.length ? `有待处理:${hits.join('、')}` : '';
  };
  const dots = useMemo(() => switchDots(s), [s]);
  const win = <span className="ops-win">近 {days} 天</span>;

  /* 账户 L:主角 = 近 N 天活跃账户(聚合端点);次级 账户 · 新增 · 游客访客;图 = 逐日活跃账户(端点失败退回增长阶梯线) */
  const g = useMemo(() => growthWindow(s.growth, days), [s.growth, days]);
  const active = s.home?.active;
  const totals = s.growth?.totals;
  const acctTip = useCallback(() => ({
    head: `近 ${days} 天 · 活跃账户`,
    rows: [
      { name: '占账户', value: active && totals?.accounts ? pct(active.accounts / totals.accounts) : DASH },
      { name: '管理员', value: totals ? Number(totals.admins) || 0 : DASH },
      { name: '停用', value: totals ? Number(totals.disabled) || 0 : DASH },
    ],
  }), [days, active, totals]);
  const acctChart = active?.series
    ? <DailyBars id="dauTile" days={active.series.map((r) => r.day)} values={active.series.map((r) => [r.accounts])} names={['活跃账户']} tones={[]} unit="个" todayIso={today} label="逐日活跃账户" onOpen={(e) => { e.stopPropagation(); onGo('accounts', 'growth'); }} />
    : g ? <StepLine id="growthTile" days={g.days} news={g.news} start={g.start} todayIso={today} onOpen={(e) => { e.stopPropagation(); onGo('accounts', 'growth'); }} /> : <div className="ops-chart-box is-static" />;

  /* 阅读与榜单 L:主角 = 近 N 天阅读次数(聚合端点;失败退回累计);次级 收藏 · 订阅 · 榜单;图 = 各源阅读 Top N */
  const reads = s.home?.reads;
  const top = useMemo(() => readTop(s.home, s.content, 8), [s.home, s.content]);
  const rankMeta = s.rank ? rankingSnapshotStatusMeta(s.rank) : null;
  const rankAt = s.rank?.snapshot?.generated_at ? new Date(s.rank.snapshot.generated_at) : null;
  const rankTime = rankAt && !Number.isNaN(rankAt.getTime()) ? `${String(rankAt.getHours()).padStart(2, '0')}:${String(rankAt.getMinutes()).padStart(2, '0')}` : (rankMeta?.label || DASH);
  const readTip = useCallback(() => ({
    head: reads ? `近 ${days} 天 · 阅读次数` : '累计阅读',
    rows: reads
      ? [{ name: '日均', value: (reads.total / Math.max(1, days)).toFixed(1) }, { name: '累计阅读', value: s.content ? Number(s.content.totals?.reads) || 0 : DASH }]
      : [{ name: '归档文章', value: s.content ? Number(s.content.totals?.articles) || 0 : DASH }, { name: '内容源', value: s.content ? Number(s.content.totals?.sources) || 0 : DASH }],
  }), [reads, days, s.content]);

  /* 读者功能 M:主角 = 近 N 天 AI 调用;小图 = 逐日两档柱(读者 / 系统);tokens、读者占比、今日预算进 hover */
  const ai = useMemo(() => aiReaderSplit(s.usage, days), [s.usage, days]);
  const ratio = budgetRatio(s.aiGlobal);
  const aiTip = useCallback(() => ({
    head: `近 ${days} 天 · AI 调用`,
    rows: [
      { name: 'tokens', value: ai ? ai.totalTokens : DASH },
      { name: '读者用途', value: ai ? pct(ai.readerShare) : DASH },
      { name: '今日预算', value: ratio === null ? '不限' : `${fmt(s.aiGlobal?.tokens_used_today)} / ${fmt(s.aiGlobal?.daily_token_budget)} · ${pct(ratio)}`, tone: ratio !== null && ratio >= 0.8 ? 'warn' : '' },
    ],
  }), [days, ai, ratio, s.aiGlobal]);

  /* 播客 M:主角 = 已发布音频;小图 = 阶段构成环图(中心 = 单集总数) */
  const parts = useMemo(() => stageParts(s.podTasks?.breakdown?.stage), [s.podTasks]);
  const episodes = parts.reduce((a, p) => a + p.value, 0);
  const podTip = useCallback(() => ({
    head: '中文精简音频',
    rows: [
      { name: '待发布', value: s.podStats ? Number(s.podStats.ready) || 0 : DASH },
      { name: '已下架', value: s.podStats ? Number(s.podStats.withdrawn) || 0 : DASH },
      { name: '播客单集', value: s.podTasks ? episodes : DASH },
    ],
  }), [s.podStats, s.podTasks, episodes]);

  /* 分析与标签 M:主角 = 新闻价值 P50;小图 = 分布直方图(P50 标线);P90 / 篇数 / 分析待处理进 hover */
  const aa = s.analysis?.article_analysis;
  const hist = useMemo(() => Array.from({ length: 10 }, (_, i) => Number(aa?.score_histogram?.[String(i + 1)]) || 0), [aa]);
  const histTotal = hist.reduce((a, b) => a + b, 0);
  const p50 = aa?.score_p50;
  const anaTip = useCallback(() => ({
    head: `近 ${days} 天 · 新闻价值`,
    rows: [
      { name: 'P90', value: aa?.score_p90 != null ? Number(aa.score_p90).toFixed(1) : DASH },
      { name: '篇数', value: aa ? histTotal : DASH },
      { name: '分析待处理', value: aa ? Number(aa.status_counts?.pending) || 0 : DASH },
    ],
  }), [days, aa, histTotal]);

  /* S 磁贴:被削的说明挂在页名 hover 浮层 */
  const backupFailed = s.storage?.backup?.status === 'failed';
  const extTip = useCallback(() => ({
    head: '存储与外部资源',
    rows: [
      { name: backupFailed ? '备份最近成功' : '备份最近完成', value: s.storage?.backup?.last_success_at ? String(s.storage.backup.last_success_at).slice(5, 16).replace('T', ' ') : DASH },
      { name: s.xQuota?.month ? `X API · ${s.xQuota.month}` : 'X API', value: s.xQuota ? `$${Number(s.xQuota.estimated_cost_usd || 0).toFixed(2)} / $${s.xQuota.monthly_budget_usd ?? 0}` : DASH },
    ],
  }), [s.storage, s.xQuota, backupFailed]);
  const [diskNum, diskUnit] = s.media ? bytesParts(s.media.disk_bytes) : [DASH, ''];
  const usrcTip = useCallback(() => ({
    head: '自定源',
    rows: [
      { name: '读者', value: s.usrc ? Number(s.usrc.kpi?.covered_users) || 0 : DASH },
      { name: '篇数', value: s.usrc ? Number(s.usrc.kpi?.article_count) || 0 : DASH },
      { name: '抓取间隔', value: s.usrc?.config?.refresh_minutes ? `${s.usrc.config.refresh_minutes} 分` : DASH },
    ],
  }), [s.usrc]);
  const fbOpen = s.feedback ? Number(s.feedback.counts?.open) || 0 : null;
  const inboxTip = useCallback(() => ({
    head: '反馈与公告',
    rows: [
      { name: '已解决', value: s.feedback ? Number(s.feedback.counts?.resolved) || 0 : DASH },
      { name: '生效公告', value: s.notices ? (s.notices.items || []).filter((n) => n.is_active).length : DASH },
    ],
  }), [s.feedback, s.notices]);

  const backupDot = s.storage
    ? (backupFailed
      ? <span className="ops-tile-dot" title="自动备份未完成" aria-label="自动备份未完成"><i className="ops-mark is-bad" /></span>
      : <span className="ops-tile-dot" title="自动备份正常" aria-label="自动备份正常"><span className="ai-light" /></span>)
    : null;

  return (
    <OpsTipProvider>
      <nav className="ops-bento" aria-label="运维功能页">
        <section className="surface-card ops-tile is-l t-accounts" aria-label="账户">
          <TileHead page="accounts" onGo={onGo} meta={win} mark={marks.accounts} markTitle={markTitle('accounts')} />
          <div className="ops-kpis">
            <Num hero label="活跃账户" value={DASH} roll={active ? { value: active.accounts } : null} tipBuild={acctTip} />
            <Num label="账户" value={totals ? fmt(totals.accounts) : DASH} />
            <Num label="新增" value={g ? `+${g.added}` : DASH} />
            <Num label="游客访客" value={s.guest ? fmt(s.guest.stats?.kpi?.visitors) : DASH} />
          </div>
          <div className="ops-tile-chart">{acctChart}</div>
        </section>

        <section className="surface-card ops-tile is-l t-reading" aria-label="阅读与榜单">
          <TileHead page="reading" onGo={onGo} meta={<span className="ops-win">{reads ? `近 ${days} 天` : '累计'}</span>} mark={marks.reading} markTitle={markTitle('reading')} />
          <div className="ops-kpis">
            <Num hero label={reads ? '阅读次数' : '累计阅读'} value={reads ? DASH : (s.content ? fmt(s.content.totals?.reads) : DASH)} roll={reads ? { value: reads.total } : null} tipBuild={readTip} />
            <Num label="收藏" value={s.content ? fmt(s.content.totals?.favorites) : DASH} />
            <Num label="订阅" value={s.content ? fmt(s.content.totals?.subscriptions) : DASH} />
            <span className="ops-kpi is-stamp" title={rankMeta ? `榜单快照 · ${rankMeta.label}` : '榜单快照'}>
              <span className="ops-kpi-lbl">榜单</span>
              <span className="ops-kpi-num">{rankMeta && <i className={`ops-okdot is-${rankMeta.tone}`} aria-label={rankMeta.label} />}{rankTime}</span>
            </span>
          </div>
          <div className="ops-tile-chart">
            <BarList id="readTop" rows={top.rows} unit="次" metric={top.windowed ? `近 ${days} 天阅读` : '累计阅读'} total={top.windowed ? reads?.total : Number(s.content?.totals?.reads) || 0} onOpen={(e) => { e.stopPropagation(); onGo('reading', 'heat'); }} />
          </div>
        </section>

        <section className="surface-card ops-tile is-m t-reader" aria-label="读者功能">
          <TileHead page="reader" onGo={onGo} meta={win} mark={marks.reader} markTitle={markTitle('reader')} />
          <div className="ops-mrow">
            <Num hero label="AI 调用" value={DASH} roll={ai ? { value: ai.totalCalls } : null} tipBuild={aiTip} />
            <div className="ops-tile-chart">
              {ai && <DailyBars id="aiTile" mini days={ai.days} values={ai.rows} names={['读者用途', '系统用途']} tones={['is-reader', 'is-system']} unit="次" todayIso={today} label="逐日 AI 调用" onOpen={(e) => { e.stopPropagation(); onGo('reader', 'usage'); }} />}
            </div>
          </div>
        </section>

        <section className="surface-card ops-tile is-m t-podcast" aria-label="播客">
          <TileHead page="podcast" onGo={onGo} mark={marks.podcast} markTitle={markTitle('podcast')} />
          <div className="ops-mrow">
            <Num hero label="已发布音频" value={s.podStats ? fmt(s.podStats.published) : DASH} tipBuild={podTip} />
            <div className="ops-tile-chart">
              {parts.length > 0 && episodes > 0 && <Donut id="podTile" parts={parts} total={episodes} label="单集" unit="期" onOpen={(e) => { e.stopPropagation(); onGo('podcast', 'tasks'); }} />}
            </div>
          </div>
        </section>

        <section className="surface-card ops-tile is-m t-analysis" aria-label="分析与标签">
          <TileHead page="analysis" onGo={onGo} meta={win} mark={marks.analysis} markTitle={markTitle('analysis')} />
          <div className="ops-mrow">
            <Num hero label="新闻价值 P50" value={DASH} roll={p50 != null ? { value: Number(p50), format: (v) => Number(v).toFixed(1) } : null} tipBuild={anaTip} />
            <div className="ops-tile-chart">
              {aa && <ScoreHistogram id="histTile" counts={hist} p50={p50 != null ? Number(p50) : null} onOpen={(e) => { e.stopPropagation(); onGo('analysis', 'metrics'); }} />}
            </div>
          </div>
        </section>

        <StatusTile items={items} dots={dots} onGo={onGo} />

        <section className="surface-card ops-tile is-s t-external" aria-label="存储与外部资源">
          <TileHead page="external" onGo={onGo} meta={backupDot} tipBuild={extTip} />
          <div className="ops-srt"><Num hero label="媒体库" value={diskNum} unit={diskUnit} /></div>
        </section>
        <section className="surface-card ops-tile is-s t-usrc" aria-label="自定源">
          <TileHead page="user-sources" onGo={onGo} tipBuild={usrcTip} />
          <div className="ops-srt"><Num hero label="源" value={s.usrc ? fmt(s.usrc.kpi?.source_count) : DASH} /></div>
        </section>
        <section className="surface-card ops-tile is-s t-inbox" aria-label="反馈与公告">
          <TileHead page="inbox" onGo={onGo} tipBuild={inboxTip} />
          <div className="ops-srt"><Num hero label="待处理" value={fbOpen === null ? DASH : fmt(fbOpen)} tone={fbOpen ? 'is-warn' : ''} /></div>
        </section>
      </nav>
    </OpsTipProvider>
  );
}
