/**
 * 账户增长（v3.55 issue #31）：把 `/api/admin/account-growth` 的「按创建日新增」序列画成一张组合图——
 * 柱 = 当日新增、折线 = 累计账户(用户验收拍板:两量合一张图,不拆两卡;粒度不另设,直接吃运维页头的
 * 近 N 天时间窗)。两个量级差一个数量级,柱与折线各挂一根 y 轴(左=新增、右=累计),图例常备标明归属。
 *
 * 累计值从全量序列前缀和推出(视野之前的账户算进起点),视野末端恒等于现存总数。
 * 「今天」取服务端 as_of_day(与 created_at 同一时钟),浏览器与服务端跨日时不漏当天新增。
 * 口径是「现存账户」——已删除账户不在 users 表里,曲线画的是「今天还在的账户何时加入」。
 */
import { useMemo } from 'react';
import { useMotionReduced } from '../../motion';
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
import { C_PRIMARY, CHART_SLOTS, AXIS, GRID, fmtNumLocale as fmt } from '../charts/chartUtils';

const AXIS_TICK = { fill: AXIS, fontSize: 11 };
const C_NEW = C_PRIMARY;
const C_TOTAL = CHART_SLOTS[1];

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function parseDay(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}
function shortDay(d) {
  return `${d.getMonth() + 1}-${d.getDate()}`;
}

/**
 * 近 days 天(含 asOf 当天)逐日:新增 + 前缀累计。返回 [{day, label, new, total}]。
 */
function dailyGrowth(series, days, asOf) {
  const rows = (series || []).filter((r) => /^\d{4}-\d{2}-\d{2}$/.test(r.day));
  const byDay = new Map();
  for (const r of rows) byDay.set(r.day, (byDay.get(r.day) || 0) + (r.new || 0));
  const start = new Date(asOf);
  start.setDate(start.getDate() - (Math.max(1, days) - 1));
  const startKey = isoDate(start);
  let total = 0;
  for (const [day, n] of byDay) if (day < startKey) total += n;
  const out = [];
  const endKey = isoDate(asOf);
  for (let cur = new Date(start); isoDate(cur) <= endKey; cur.setDate(cur.getDate() + 1)) {
    const key = isoDate(cur);
    const added = byDay.get(key) || 0;
    total += added;
    out.push({ day: key, label: shortDay(cur), new: added, total });
  }
  return out;
}

function GrowthTooltip({ active, payload }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-card-solid)] px-3 py-2 shadow-[var(--sh-2)]">
      <p className="mb-1 text-xs font-bold text-slate-700">{row.day}</p>
      <div className="space-y-0.5">
        {[['新增账户', row.new, C_NEW], ['累计账户', row.total, C_TOTAL]].map(([name, value, color]) => (
          <div key={name} className="flex items-center justify-between gap-4 text-xs">
            <span className="inline-flex items-center gap-1.5 text-slate-500">
              <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
              {name}
            </span>
            <span className="font-bold tabular-nums text-slate-700">{fmt(value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const intFormatter = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);

export default function AccountGrowth({ growth, days = 30, height = 230 }) {
  // 动效偏好显式传给 recharts(issue #73):不让其 'auto' 档自己读 OS 查询
  const animate = !useMotionReduced();
  const asOf = useMemo(() => (growth?.as_of_day ? parseDay(growth.as_of_day) : new Date()), [growth]);
  const data = useMemo(() => dailyGrowth(growth?.series, days, asOf), [growth, days, asOf]);
  const windowNew = data.reduce((acc, r) => acc + r.new, 0);
  return (
    <>
      <div className="zone-head">
        <span className="zone-title">账户增长</span>
        <span className="zone-hint">近 {days} 天 · 新增 {fmt(windowNew)} · 现存账户按加入日回溯,已删除不计</span>
      </div>
      <section className="surface-card card-pad rounded-[var(--r-card)]" aria-label="账户增长">
        <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
          {[['新增账户', C_NEW], ['累计账户', C_TOTAL]].map(([name, color]) => (
            <span key={name} className="inline-flex items-center gap-1 tiny-meta text-slate-500">
              <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
              {name}
            </span>
          ))}
        </div>
        <div style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap="30%">
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
              <YAxis yAxisId="new" tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} tickCount={3} tickFormatter={intFormatter} allowDecimals={false} />
              <YAxis yAxisId="total" orientation="right" tick={AXIS_TICK} axisLine={false} tickLine={false} width={44} tickCount={3} tickFormatter={intFormatter} allowDecimals={false} />
              <Tooltip cursor={{ fill: 'var(--dorami-wash)' }} content={<GrowthTooltip />} isAnimationActive={animate} />
              <Bar yAxisId="new" dataKey="new" name="新增账户" fill={C_NEW} radius={[4, 4, 0, 0]} maxBarSize={14} isAnimationActive={animate} />
              <Line yAxisId="total" type="monotone" dataKey="total" name="累计账户" stroke={C_TOTAL} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={animate} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </section>
    </>
  );
}
