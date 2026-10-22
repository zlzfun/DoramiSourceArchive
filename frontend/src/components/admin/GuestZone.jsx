/**
 * 游客区(issue #213,样页 docs/design/guest-mode/ 场景「运维 · 游客区」):运维「用户」子页账户增长之下。
 * 聚合口径,全体管理员可见——上方账户 KPI / 活跃榜 / 账户增长都不含游客,游客只在这一区计。
 *
 * - 开关条:游客访问总闸 +「游客 AI」(只管游客,正式读者不受影响);写操作走 /api/admin,自动入审计。
 * - 五格 KPI:访客(新访客)/ 访问(日均)/ 阅读(人均)/ AI 调用(tokens)/ 转化(转化率)。
 * - 每日走势:访客柱 + 当天有人登录(转化)的点;入口分布条按「访客当天首个入口」计。
 * - 区头齿轮开「游客设置」抽屉:四类 AI 用途每位游客的日额度(缺省与读者同额,0 = 不限),
 *   默认订阅名单只读展示(在「内容」子页「新账号默认订阅」里改)。
 * 时间窗吃运维页头的近 N 天;设置写回的响应只取 settings / default_sources,统计仍按当前窗口。
 * 刷新失败而旧快照还在时(检视 F8):区头挂 StaleNotice,所有派生数字(日均分母、走势日期、区头「近 N 天」)
 * 一律用快照自带的 stats.days,快照与它的窗口始终自洽,不会把七天的数据套上三十天的分母。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Settings, X } from 'lucide-react';
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { fetchAdminGuest, updateAdminGuestSettings } from '../../api';
import { useMotionReduced } from '../../motion';
import { useModalA11y } from '../../hooks/useModalA11y';
import { C_OTHER, C_PRIMARY, CHART_SLOTS, AXIS, GRID, fmtNumLocale as fmt } from '../charts/chartUtils';
import { Kpi, KpiState } from './Kpi';
import StaleNotice from './StaleNotice';

const AXIS_TICK = { fill: AXIS, fontSize: 12 };
const C_CONVERSION = 'var(--state-ok)';
const LIMIT_MAX = 100000;

// 四类用途:键与后端 GUEST_AI_LIMIT_KEYS 同序
const LIMIT_ROWS = [
  ['translate', '翻译', '文章 / 社交 / 逐字稿 / 长文后台翻译，按调用计'],
  ['ask', '问答', '按提问计'],
  ['summarize', '速读', '按生成计'],
  ['ondemand', '点播', '播客导读 + 文章旁白，按新开一次生成计'],
];

// 入口:后端 ENTRIES;回访是中性槽
const ENTRY_ROWS = [
  ['deep_link', '站内链接', CHART_SLOTS[0]],
  ['login_page', '登录页「游客登录」', CHART_SLOTS[1]],
  ['share_page', '公开分享页', CHART_SLOTS[2]],
  ['return', '回访', C_OTHER],
];

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// 近 days 天(含服务端「今天」)逐日补零:[{day, label, visitors, visits, conversions}]
function dailyRows(byDay, days, asOfDay) {
  const map = new Map((byDay || []).map((r) => [r.day, r]));
  const [y, m, d] = (asOfDay || isoDate(new Date())).split('-').map(Number);
  const end = new Date(y, m - 1, d);
  const out = [];
  for (let i = Math.max(1, days) - 1; i >= 0; i -= 1) {
    const cur = new Date(end);
    cur.setDate(end.getDate() - i);
    const key = isoDate(cur);
    const row = map.get(key) || {};
    out.push({
      day: key,
      label: `${cur.getMonth() + 1}-${cur.getDate()}`,
      visitors: row.visitors || 0,
      visits: row.visits || 0,
      conversions: row.conversions || 0,
    });
  }
  return out;
}

function GuestTooltip({ active, payload }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-card-solid)] px-3 py-2 shadow-[var(--sh-2)]">
      <p className="mb-1 text-xs font-semibold text-slate-700">{row.day}</p>
      <div className="space-y-0.5">
        {[['访客', row.visitors], ['访问', row.visits], ['转化', row.conversions]].map(([name, value]) => (
          <div key={name} className="flex items-center justify-between gap-4 text-xs">
            <span className="text-slate-500">{name}</span>
            <span className="font-semibold tabular-nums text-slate-700">{fmt(value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// 转化点:骑在当天访客柱顶上方;没有转化的日子不画
function ConversionDot({ cx, cy, payload }) {
  if (!payload?.conversions || !Number.isFinite(cx) || !Number.isFinite(cy)) return null;
  return <circle cx={cx} cy={cy - 9} r={4.5} fill={C_CONVERSION} stroke="var(--dorami-surface)" strokeWidth={2} />;
}

// 分母为 0 时写「—」:没有访客就谈不上转化率,不能显示成 0%
const pct = (part, whole) => (whole > 0 ? `${((part / whole) * 100).toFixed(part / whole >= 0.1 ? 0 : 1)}%` : '—');
// 平均数保留一位小数;非零但不足 0.1 时写「<0.1」,免得「阅读 3 · 人均 0」这种自相矛盾的读数
const avg = (total, count) => {
  if (!count || !total) return '0';
  const value = total / count;
  return value < 0.1 ? '<0.1' : value.toFixed(1).replace(/\.0$/, '');
};

function limitDrafts(settings) {
  const limits = settings?.ai_limits || {};
  return Object.fromEntries(LIMIT_ROWS.map(([key]) => [key, limits[key] == null ? '' : String(limits[key])]));
}

export default function GuestZone({ days = 30, showToast, refreshTick = 0 }) {
  const animate = !useMotionReduced();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drafts, setDrafts] = useState({});
  const panelRef = useRef(null);
  const genRef = useRef(0);

  const load = useCallback(() => {
    const gen = genRef.current + 1;
    genRef.current = gen;
    setError('');
    return fetchAdminGuest(days)
      .then((res) => { if (genRef.current === gen) setData(res); })
      .catch((err) => { if (genRef.current === gen) setError(err.message || '获取游客统计失败'); });
  }, [days]);
  useEffect(() => { load(); }, [load, refreshTick]);

  const settings = data?.settings || null;
  const stats = data?.stats || null;
  const kpi = stats?.kpi || {};
  const shownDays = stats?.days || days; // 快照自己的窗口
  const rows = useMemo(() => dailyRows(stats?.by_day, shownDays, stats?.as_of_day), [stats, shownDays]);
  const maxVisitors = rows.reduce((acc, r) => Math.max(acc, r.visitors), 0);
  const entries = stats?.entries || {};
  const entryTotal = ENTRY_ROWS.reduce((acc, [key]) => acc + (entries[key] || 0), 0);
  const readerLimits = settings?.reader_ai_limits || {};
  const limitsAsReader = LIMIT_ROWS.every(([key]) => settings?.ai_limits?.[key] === readerLimits[key]);

  // 写回:响应带的是近 30 天统计,只取设置与名单,统计保持当前窗口
  const applySettings = (res) => setData((prev) => (prev
    ? { ...prev, settings: res.settings, default_sources: res.default_sources }
    : res));

  const save = async (body, okText) => {
    setBusy(true);
    try {
      const res = await updateAdminGuestSettings(body);
      applySettings(res);
      showToast?.(okText(res.settings), 'success');
      return true;
    } catch (err) {
      showToast?.(err.message || '更新游客设置失败', 'error');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const toggleAccess = () => save(
    { enabled: !settings?.enabled },
    (s) => (s.enabled ? '已开启游客访问' : '已关闭游客访问（在线游客会话立即失效）'),
  );
  const toggleAi = () => save(
    { ai_enabled: !settings?.ai_enabled },
    (s) => (s.ai_enabled ? '已开启游客 AI' : '已关闭游客 AI'),
  );

  const openDrawer = () => {
    setDrafts(limitDrafts(settings));
    setDrawerOpen(true);
  };
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  useModalA11y(drawerOpen, closeDrawer, panelRef);

  const saveLimits = async () => {
    const aiLimits = {};
    for (const [key, label] of LIMIT_ROWS) {
      const raw = String(drafts[key] ?? '').trim();
      if (raw === '') { aiLimits[key] = null; continue; }
      const value = Number(raw);
      if (!Number.isInteger(value) || value < 0 || value > LIMIT_MAX) {
        showToast?.(`${label}的额度须为 0–${LIMIT_MAX} 的整数（0 = 不限）`, 'error');
        return;
      }
      // 与读者同额即恢复缺省(不留覆盖),以后读者额度调整时游客自动跟随
      aiLimits[key] = value === readerLimits[key] ? null : value;
    }
    const ok = await save({ ai_limits: aiLimits }, () => '游客 AI 额度已保存');
    if (ok) setDrawerOpen(false);
  };

  const meta = !settings
    ? '正在读取…'
    : !settings.enabled
      ? '已关闭：未登录打开站内链接回到登录页，在线游客会话立即失效'
      : settings.ai_enabled
        ? `未登录打开站内链接直接以游客浏览 · 每位游客的 AI 日额度${limitsAsReader ? '与读者相同' : '见游客设置'}`
        : '游客照常浏览；AI 入口对游客隐藏';

  const sources = data?.default_sources || [];

  return (
    <>
      <div className="zone-head">
        <span className="zone-title">游客</span>
        <span className="zone-hint">近 {shownDays} 天 · 不计入上方账户口径</span>
        {stats && <StaleNotice status={error ? 'error' : 'ok'} error={error} onRetry={load} label="游客统计" />}
        <span className="guest-zone-acts">
          <button type="button" className="rowact-btn" title="游客设置" aria-label="游客设置" onClick={openDrawer} disabled={!settings}>
            <Settings />
          </button>
        </span>
      </div>

      <section className="surface-card ai-switchboard is-wrap rounded-[var(--r-card)] mb-4" aria-label="游客开关">
        <span className={`ai-light ${settings?.enabled ? '' : 'is-off'}`} />
        <div className="ai-switch-lbl" title="总闸：关闭后在线游客会话立即失效，未登录打开站内链接回到登录页">游客访问</div>
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(settings?.enabled)}
          aria-label="游客访问总闸"
          disabled={!settings || busy}
          onClick={toggleAccess}
          className={`ledger-switch ${settings?.enabled ? 'is-on' : ''}`}
        />
        <span className="ai-divider" />
        <div className="ai-switch-lbl" title="只关游客的 AI；正式读者不受影响">游客 AI</div>
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(settings?.ai_enabled)}
          aria-label="游客 AI"
          disabled={!settings || busy}
          onClick={toggleAi}
          className={`ledger-switch ${settings?.ai_enabled ? 'is-on' : ''}`}
        />
        <span className="ai-divider" />
        <span className="tiny-meta">{meta}</span>
      </section>

      {!stats ? (
        <section className="surface-card kpi-strip" aria-label="游客概览">
          <KpiState label="游客统计" error={error} onRetry={load} />
        </section>
      ) : (
        <>
          <section className="surface-card kpi-strip" aria-label="游客概览">
            <Kpi num={fmt(kpi.visitors)} label="访客" sub={`新访客 ${fmt(kpi.new_visitors)}`} />
            <Kpi num={fmt(kpi.visits)} label="访问" sub={`日均 ${avg(kpi.visits, shownDays)}`} />
            <Kpi num={fmt(kpi.reads)} label="阅读" sub={`人均 ${avg(kpi.reads, kpi.visitors)}`} />
            <Kpi num={fmt(kpi.ai_calls)} label="AI 调用" sub={`tokens ${fmt(kpi.ai_tokens)}`} />
            <Kpi num={fmt(kpi.conversions)} label="转化" sub={`转化率 ${pct(kpi.conversions || 0, kpi.visitors || 0)}`} />
          </section>

          <section className="surface-card card-pad rounded-[var(--r-card)] guest-trend" aria-label="游客走势">
            <div style={{ height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={rows} margin={{ top: 16, right: 16, bottom: 0, left: 0 }} barCategoryGap="30%">
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
                  <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} tickCount={3} allowDecimals={false} domain={[0, Math.max(4, maxVisitors + 1)]} />
                  <Tooltip cursor={{ fill: 'var(--dorami-hover)' }} content={<GuestTooltip />} isAnimationActive={animate} />
                  <Bar dataKey="visitors" name="每日访客" fill={C_PRIMARY} maxBarSize={14} radius={[2, 2, 0, 0]} isAnimationActive={animate} />
                  <Line dataKey="visitors" name="转化" stroke="none" strokeWidth={0} dot={<ConversionDot />} activeDot={false} legendType="none" tooltipType="none" isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="guest-legend">
              <span><i style={{ background: C_PRIMARY }} />每日访客</span>
              <span><i className="is-dot" style={{ background: C_CONVERSION }} />当日登录（转化）</span>
            </div>
            <div className="guest-entry">
              <div className="guest-entry-head">入口<span className="zone-hint">按访客当天首个入口</span></div>
              <div className="guest-entry-bar" role="img" aria-label={ENTRY_ROWS.map(([key, label]) => `${label} ${pct(entries[key] || 0, entryTotal)}`).join('，')}>
                {entryTotal > 0 && ENTRY_ROWS.map(([key, , color]) => (entries[key] || 0) > 0 && (
                  <span key={key} style={{ width: `${((entries[key] || 0) / entryTotal) * 100}%`, background: color }} />
                ))}
              </div>
              <div className="guest-legend">
                {ENTRY_ROWS.map(([key, label, color]) => (
                  <span key={key}><i style={{ background: color }} />{label} {pct(entries[key] || 0, entryTotal)}</span>
                ))}
              </div>
            </div>
          </section>
        </>
      )}

      {/* 游客设置抽屉(ledger-drawer 语法,同运维用户抽屉) */}
      <div className={`ledger-scrim ${drawerOpen ? 'is-open' : ''}`} onClick={closeDrawer} aria-hidden="true" />
      <aside
        ref={panelRef}
        className={`ledger-drawer ${drawerOpen ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="游客设置"
        aria-hidden={!drawerOpen}
        tabIndex={-1}
      >
        <div className="ledger-drawer-head">
          <span className="ledger-drawer-title">
            游客设置
            <small className="drawer-title-sub">每位游客各自计数，按服务端日期零点重置</small>
          </span>
          <button type="button" className="icon-button shrink-0" onClick={closeDrawer} aria-label="关闭"><X className="h-5 w-5" /></button>
        </div>
        <div className="ledger-drawer-body">
          <div>
            <div className="drawer-sec-title">每位游客的 AI 日额度 · 缺省与读者相同，0 = 不限</div>
            {LIMIT_ROWS.map(([key, label, sub]) => (
              <label key={key} className="guest-limit-row">
                <span>{label}<small>{sub}</small></span>
                <input
                  className="guest-limit-input"
                  inputMode="numeric"
                  value={drafts[key] ?? ''}
                  placeholder={readerLimits[key] != null ? String(readerLimits[key]) : ''}
                  onChange={(e) => setDrafts((prev) => ({ ...prev, [key]: e.target.value }))}
                  aria-label={`${label}每日上限`}
                />
              </label>
            ))}
          </div>
          <div>
            <div className="drawer-sec-title">游客的订阅 = 新账号默认订阅（{sources.length} 个来源）</div>
            <div className="guest-defsrc">
              {sources.map((s) => <span key={s.source_id}>{s.name}</span>)}
            </div>
            <p className="tiny-meta guest-defsrc-note">在「内容」子页的「新账号默认订阅」里改，游客与此后新建的账号同时生效；改动当天游客早报会自动重编。</p>
          </div>
        </div>
        <div className="ledger-drawer-foot">
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs ml-auto" onClick={closeDrawer}>取消</button>
          <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={saveLimits} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} 保存
          </button>
        </div>
      </aside>
    </>
  );
}
