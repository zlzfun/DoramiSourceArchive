/**
 * 账户增长（v3.55 issue #31；issue #184 改阶梯线）：把 `/api/admin/account-growth` 的「按创建日新增」序列画成
 * 一条从存量起步的阶梯线——累计账户只在有人加入的那天抬一阶,抬阶处画一个点并标「+N」。两个量仍在一张图里
 * (v3.55 用户验收拍板:两量合一张图,不拆两卡;粒度直接吃运维页头的近 N 天时间窗),但不再柱线双轴:
 * 新增是稀疏事件,画成柱时大多数日子是空的,双轴还让两个量级互相挤压。
 *
 * 累计值从全量序列前缀和推出(视野之前的账户算进起点),视野末端恒等于现存总数。
 * 「今天」取服务端 as_of_day(与 created_at 同一时钟),浏览器与服务端跨日时不漏当天新增。
 * 口径是「现存账户」——已删除账户不在 users 表里,曲线画的是「今天还在的账户何时加入」。
 */
import { useMemo } from 'react';
import { useMotionReduced } from '../../motion';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { C_PRIMARY, AXIS, GRID, fmtNumLocale as fmt } from '../charts/chartUtils';

const AXIS_TICK = { fill: AXIS, fontSize: 12 };
const C_TOTAL = C_PRIMARY;

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
  if (!row || !row.day) return null;   // 起点(窗口之前的存量)不出浮层
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-card-solid)] px-3 py-2 shadow-[var(--sh-2)]">
      <p className="mb-1 text-xs font-semibold text-slate-700">{row.day}</p>
      <div className="space-y-0.5">
        {[['累计账户', row.total], ['当日新增', row.new]].map(([name, value]) => (
          <div key={name} className="flex items-center justify-between gap-4 text-xs">
            <span className="text-slate-500">{name}</span>
            <span className="font-semibold tabular-nums text-slate-700">{fmt(value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const intFormatter = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);

// 抬阶处的点与「+N」标注;其余日子不画点
function JoinDot({ cx, cy, payload }) {
  if (!payload?.new || !Number.isFinite(cx) || !Number.isFinite(cy)) return null;
  return (
    <g>
      <circle cx={cx} cy={cy} r={3.5} fill="var(--dorami-surface)" stroke={C_TOTAL} strokeWidth={1.6} />
      <text x={cx + 7} y={cy - 8} fontSize={12} fill="var(--dorami-ink-2)">+{payload.new}</text>
    </g>
  );
}

export default function AccountGrowth({ growth, days = 30, height = 200 }) {
  // 动效偏好显式传给 recharts(issue #73):不让其 'auto' 档自己读 OS 查询
  const animate = !useMotionReduced();
  const asOf = useMemo(() => (growth?.as_of_day ? parseDay(growth.as_of_day) : new Date()), [growth]);
  const daily = useMemo(() => dailyGrowth(growth?.series, days, asOf), [growth, days, asOf]);
  const windowNew = daily.reduce((acc, r) => acc + r.new, 0);
  // 阶梯从窗口之前的存量起步:首格前补一个起点,窗口第一天就有人加入时也看得见那一阶
  const data = useMemo(() => (daily.length
    ? [{ day: '', label: '', new: 0, total: daily[0].total - daily[0].new }, ...daily]
    : daily), [daily]);
  const totals = data.map((r) => r.total);
  const lo = totals.length ? Math.min(...totals) : 0;
  const hi = totals.length ? Math.max(...totals) : 1;
  const total = totals.length ? totals[totals.length - 1] : 0;
  return (
    <>
      <div className="zone-head">
        <span className="zone-title">账户增长</span>
        <span className="zone-hint">{fmt(total)} 个 · 近 {days} 天新增 {fmt(windowNew)} · 现存账户按加入日回溯,已删除不计</span>
      </div>
      <section className="surface-card card-pad rounded-[var(--r-card)]" aria-label="账户增长">
        <div style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 22, right: 24, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
              <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={44} tickCount={3} tickFormatter={intFormatter} allowDecimals={false} domain={[Math.max(0, lo - 1), hi + 1]} />
              <Tooltip cursor={{ stroke: GRID }} content={<GrowthTooltip />} isAnimationActive={animate} />
              <Line type="stepAfter" dataKey="total" name="累计账户" stroke={C_TOTAL} strokeWidth={1.6} dot={<JoinDot />} activeDot={{ r: 4 }} isAnimationActive={animate} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
    </>
  );
}
