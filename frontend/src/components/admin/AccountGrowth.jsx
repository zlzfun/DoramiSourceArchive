/**
 * 账户增长（v3.55 issue #31）：把 `/api/admin/account-growth` 的「按创建日新增」序列画成
 * 两张单系列图——累计账户(面积)与新增账户(柱)。两个量级差一个数量级,合画一张要么双轴
 * (dataviz 禁)要么柱被压平,故拆成并列两卡、共用一套时间桶。
 *
 * 粒度 seg 决定桶与视野:日 = 近 30 天 / 周 = 近 26 周(周一起) / 月 = 自首个账户起。
 * 累计值从全量序列前缀和推出(视野之前的账户算进起点),视野末端恒等于现存总数。
 * 口径是「现存账户」——已删除账户不在 users 表里,曲线画的是「今天还在的账户何时加入」。
 */
import { useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { C_PRIMARY, AXIS, GRID, fmtNumLocale as fmt } from '../charts/chartUtils';

const AXIS_TICK = { fill: AXIS, fontSize: 11 };
const GRAINS = [['day', '日'], ['week', '周'], ['month', '月']];
const DAY_SPAN = 30;
const WEEK_SPAN = 26;

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function parseDay(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}
// 桶起点:日 = 当天;周 = 所在周的周一;月 = 当月 1 日。
function bucketStart(date, grain) {
  const d = new Date(date);
  if (grain === 'week') {
    const dow = (d.getDay() + 6) % 7; // 周一=0
    d.setDate(d.getDate() - dow);
  } else if (grain === 'month') {
    d.setDate(1);
  }
  return d;
}
function stepBucket(date, grain) {
  const d = new Date(date);
  if (grain === 'day') d.setDate(d.getDate() + 1);
  else if (grain === 'week') d.setDate(d.getDate() + 7);
  else d.setMonth(d.getMonth() + 1);
  return d;
}
function bucketLabel(date, grain) {
  if (grain === 'month') return `${date.getFullYear()}-${date.getMonth() + 1}`;
  return `${date.getMonth() + 1}-${date.getDate()}`;
}
function bucketTitle(date, grain) {
  const start = isoDate(date);
  if (grain === 'day') return start;
  if (grain === 'week') {
    const end = new Date(date);
    end.setDate(end.getDate() + 6);
    return `${start} ～ ${isoDate(end)}`;
  }
  return `${date.getFullYear()} 年 ${date.getMonth() + 1} 月`;
}

/**
 * 把逐日新增序列折成指定粒度的连续桶(含空桶),并附前缀累计。
 * 返回 [{key, label, title, new, total}],视野内升序。
 */
function bucketGrowth(series, grain, today = new Date()) {
  const rows = (series || []).filter((r) => /^\d{4}-\d{2}-\d{2}$/.test(r.day));
  if (rows.length === 0) return [];
  const sorted = [...rows].sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
  const firstDay = parseDay(sorted[0].day);
  // 视野起点
  let start;
  if (grain === 'day') {
    start = new Date(today); start.setDate(start.getDate() - (DAY_SPAN - 1));
  } else if (grain === 'week') {
    start = bucketStart(today, 'week'); start.setDate(start.getDate() - 7 * (WEEK_SPAN - 1));
  } else {
    start = bucketStart(firstDay, 'month');
  }
  start = bucketStart(start, grain);
  const startKey = isoDate(start);
  // 视野之前的账户 → 起点累计
  let carried = 0;
  const byBucket = new Map();
  for (const r of sorted) {
    const bs = bucketStart(parseDay(r.day), grain);
    const key = isoDate(bs);
    if (key < startKey) { carried += r.new || 0; continue; }
    byBucket.set(key, (byBucket.get(key) || 0) + (r.new || 0));
  }
  const out = [];
  let total = carried;
  const endKey = isoDate(bucketStart(today, grain));
  for (let cur = new Date(start); isoDate(cur) <= endKey; cur = stepBucket(cur, grain)) {
    const key = isoDate(cur);
    const added = byBucket.get(key) || 0;
    total += added;
    out.push({ key, label: bucketLabel(cur, grain), title: bucketTitle(cur, grain), new: added, total });
  }
  return out;
}

function GrowthTooltip({ active, payload, name }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-card-solid)] px-3 py-2 shadow-[var(--sh-2)]">
      <p className="mb-1 text-xs font-bold text-slate-700">{row.title}</p>
      <div className="flex items-center justify-between gap-4 text-xs">
        <span className="inline-flex items-center gap-1.5 text-slate-500">
          <span className="h-2 w-2 rounded-sm" style={{ background: C_PRIMARY }} />
          {name}
        </span>
        <span className="font-bold tabular-nums text-slate-700">{fmt(payload[0].value)}</span>
      </div>
    </div>
  );
}

const intFormatter = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);

export default function AccountGrowth({ growth, height = 200 }) {
  const [grain, setGrain] = useState('day');
  // 「今天」取服务端 as_of_day(与 created_at 同一时钟),浏览器与服务端跨日时不漏当天新增。
  const asOf = useMemo(() => (growth?.as_of_day ? parseDay(growth.as_of_day) : new Date()), [growth]);
  const data = useMemo(() => bucketGrowth(growth?.series, grain, asOf), [growth, grain, asOf]);
  const windowNew = data.reduce((acc, r) => acc + r.new, 0);
  const grainNoun = grain === 'day' ? `近 ${DAY_SPAN} 天` : grain === 'week' ? `近 ${WEEK_SPAN} 周` : '全部';
  const seg = (
    <span className="mini-seg" role="group" aria-label="时间粒度">
      {GRAINS.map(([k, lbl]) => (
        <button key={k} type="button" onClick={() => setGrain(k)} className={`mini-seg-btn ${grain === k ? 'is-on' : ''}`}>{lbl}</button>
      ))}
    </span>
  );
  return (
    <>
      <div className="zone-head">
        <span className="zone-title">账户增长</span>
        <span className="zone-hint">{grainNoun} · 新增 {fmt(windowNew)} · 现存账户按加入日回溯,已删除不计</span>
        <span className="zone-acts">{seg}</span>
      </div>
      <div className="admin-grid">
        <div className="surface-card card-pad rounded-[var(--r-card)]">
          <div className="card-head"><span className="card-title">累计账户</span></div>
          <div style={{ height }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
                <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} tickCount={3} tickFormatter={intFormatter} allowDecimals={false} />
                <Tooltip cursor={{ stroke: GRID }} content={<GrowthTooltip name="累计账户" />} />
                <Area type="monotone" dataKey="total" name="累计账户" stroke={C_PRIMARY} strokeWidth={2} fill={C_PRIMARY} fillOpacity={0.14} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="surface-card card-pad rounded-[var(--r-card)]">
          <div className="card-head"><span className="card-title">新增账户</span></div>
          <div style={{ height }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 4, right: 16, bottom: 0, left: 0 }} barCategoryGap="30%">
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
                <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} tickCount={3} tickFormatter={intFormatter} allowDecimals={false} />
                <Tooltip cursor={{ fill: 'var(--dorami-wash)' }} content={<GrowthTooltip name="新增账户" />} />
                <Bar dataKey="new" name="新增账户" fill={C_PRIMARY} radius={[4, 4, 0, 0]} maxBarSize={14} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </>
  );
}
