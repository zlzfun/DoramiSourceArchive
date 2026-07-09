/**
 * 运维看板图表件（基于 recharts）。坐标轴/网格走 dorami 令牌（暗色翻转），
 * 多系列颜色用分类调色板（CATEGORICAL，便于区分用户/用途）。数字默认隐藏，
 * hover 时由 ThemedTooltip 浮现。
 */
import { useState, useRef, useEffect, useCallback } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { C_PRIMARY, AXIS, GRID, seriesColor, fmtNumLocale as fmt } from './chartUtils';

const AXIS_TICK = { fill: AXIS, fontSize: 11 };

// 统一 hover 浮层：surface-card 风格 + 令牌，暗色安全。
function ThemedTooltip({ active, payload, label, titleKey }) {
  if (!active || !payload || payload.length === 0) return null;
  const head = payload[0]?.payload?.[titleKey] ?? label;
  // 多系列时按值倒序、过滤掉 0，浮层更聚焦。
  const rows = payload
    .filter((p) => Number(p.value) > 0)
    .sort((a, b) => Number(b.value) - Number(a.value));
  if (rows.length === 0) return null;
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-card-solid)] px-3 py-2 shadow-[var(--sh-2)]">
      <p className="mb-1 text-xs font-bold text-slate-700">{head}</p>
      <div className="space-y-0.5">
        {rows.map((p) => (
          <div key={p.dataKey} className="flex items-center justify-between gap-4 text-xs">
            <span className="inline-flex items-center gap-1.5 text-slate-500">
              <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
              {p.name}
            </span>
            <span className="font-bold tabular-nums text-slate-700">{fmt(p.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const kFormatter = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);

/**
 * 多系列每日面积图，含可切换维度的分段控制。
 * datasets: { <dimKey>: {data, keys} }（已透视、零填充）。
 * dims: [[dimKey, 标签], …]，默认「按用途 / 按用户」；单用户详情可传
 *       [['calls','调用'],['tokens','tokens']] 复用同一组件。
 */
export function MultiSeriesArea({
  title,
  datasets,
  height = 230,
  defaultDim,
  dims = [['purpose', '按用途'], ['user', '按用户']],
}) {
  const [dim, setDim] = useState(defaultDim ?? dims[0][0]);
  const ds = datasets[dim] || { data: [], keys: [] };
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-surface)] p-4">
      <div className="mb-2 flex items-center gap-3">
        <p className="micro-label text-slate-500">{title}</p>
        <div className="ml-auto inline-flex rounded-[var(--r-control)] border border-[var(--dorami-border)] p-0.5">
          {dims.map(([k, lbl]) => (
            <button
              key={k}
              onClick={() => setDim(k)}
              className={`rounded-[var(--r-sm)] px-2 py-0.5 micro-label transition-colors ${
                dim === k ? 'bg-[var(--dorami-wash)] text-[var(--dorami-accent-ink)]' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>
      </div>
      {/* 自绘图例（dark 安全，避免 recharts Legend 默认深色文字） */}
      <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
        {ds.keys.map((k, i) => (
          <span key={k} className="inline-flex items-center gap-1 tiny-meta text-slate-500">
            <span className="h-2 w-2 rounded-sm" style={{ background: seriesColor(i) }} />
            {k}
          </span>
        ))}
      </div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={ds.data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} tickCount={3} tickFormatter={kFormatter} allowDecimals={false} />
            <Tooltip cursor={{ stroke: GRID }} content={<ThemedTooltip titleKey="day" />} />
            {ds.keys.map((k, i) => (
              <Area
                key={k}
                type="monotone"
                dataKey={k}
                name={k}
                stackId="s"
                stroke={seriesColor(i)}
                fill={seriesColor(i)}
                fillOpacity={0.55}
                strokeWidth={1.5}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/**
 * 横向排行柱（单系列）。
 * rows: 对象数组；labelKey=类目字段，valueKey=数值字段；name=数值中文名（tooltip 用）。
 * colorByIndex=true 时每条柱按分类调色板取色（少量类目排行更易区分），否则统一 color。
 */
export function RankBars({ rows, labelKey, valueKey, name, color = C_PRIMARY, height = 200, emptyHint = '暂无数据', tickFormatter, labelWidth = 96, colorByIndex = false }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] tiny-meta" style={{ height }}>
        {emptyHint}
      </div>
    );
  }
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
          <CartesianGrid stroke={GRID} horizontal={false} />
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis type="category" dataKey={labelKey} tick={AXIS_TICK} axisLine={false} tickLine={false} width={labelWidth} interval={0} tickFormatter={tickFormatter} />
          <Tooltip cursor={{ fill: 'var(--dorami-wash)' }} content={<ThemedTooltip titleKey={labelKey} />} />
          <Bar dataKey={valueKey} name={name} fill={color} radius={[0, 4, 4, 0]} maxBarSize={22}>
            {colorByIndex && rows.map((row, i) => <Cell key={row[labelKey] ?? i} fill={seriesColor(i)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * 多指标条形列表（自绘，非 recharts）。承载「类目多、名字长」的排行——
 * 每行一类目占整行宽（名字尽量完整），列表限高内部滚动；每个 metric 一根细条，
 * 按该指标自身最大值独立归一化（不同量级也都可见，绝对值看右侧数字）。
 * rows: 对象数组；nameKey=类目名字段；metrics=[{key, name, color}]。
 * colorByIndex=true 时（仅适合单指标）每行的条按分类调色板逐行取色——排行也有色彩层次。
 * 数值默认隐藏、hover 整行才浮现（与 RankBars 的 tooltip 一致，更清爽）；alwaysShowValue=true
 * 时行尾常显数值（排行榜场景——看榜本就是看数）。列表可滚动且未到底时，底部内容以
 * CSS mask 渐隐（.scroll-fade-b）暗示「还可下滑」，到底即撤除、末行完整可见。
 */
export function BarList({ rows, nameKey, metrics, maxHeight = 248, emptyHint = '暂无数据', colorByIndex = false, alwaysShowValue = false }) {
  const scrollRef = useRef(null);
  const [scroll, setScroll] = useState({ overflowing: false, atEnd: true });

  const updateScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const overflowing = el.scrollHeight - el.clientHeight > 4;
    const atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    setScroll((s) => (s.overflowing === overflowing && s.atEnd === atEnd ? s : { overflowing, atEnd }));
  }, []);

  useEffect(() => { updateScroll(); }, [rows, updateScroll]);

  if (!rows || rows.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] tiny-meta" style={{ minHeight: 120 }}>
        {emptyHint}
      </div>
    );
  }
  const maxByKey = {};
  for (const m of metrics) maxByKey[m.key] = Math.max(1, ...rows.map((r) => Number(r[m.key]) || 0));
  const showHint = scroll.overflowing && !scroll.atEnd;
  return (
    <div>
      {/* 逐行配色时颜色编码「排名」而非「指标」，故不渲染指标色例（单指标无歧义） */}
      {!colorByIndex && (
        <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
          {metrics.map((m) => (
            <span key={m.key} className="inline-flex items-center gap-1 tiny-meta text-slate-500">
              <span className="h-2 w-2 rounded-sm" style={{ background: m.color }} />
              {m.name}
            </span>
          ))}
        </div>
      )}
      <div className="relative">
        <div ref={scrollRef} onScroll={updateScroll} className={`overflow-y-auto pr-1 ${showHint ? 'scroll-fade-b' : ''}`} style={{ maxHeight }}>
          {rows.map((r, i) => (
            <div key={r[nameKey]} className="group rounded-[var(--r-sm)] px-1.5 py-1.5 transition-colors hover:bg-[var(--dorami-soft)]">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-bold text-slate-700" title={r[nameKey]}>{r[nameKey]}</span>
                <span className={`shrink-0 tiny-meta tabular-nums text-slate-500 ${alwaysShowValue ? '' : 'opacity-0 transition-opacity group-hover:opacity-100'}`}>
                  {metrics.map((m) => `${m.name} ${Number(r[m.key]) || 0}`).join(' · ')}
                </span>
              </div>
              <div className="mt-1 space-y-0.5">
                {metrics.map((m) => (
                  <div key={m.key} className="h-1.5 overflow-hidden rounded-full bg-[var(--dorami-well)]">
                    <div className="h-full rounded-full" style={{ width: `${((Number(r[m.key]) || 0) / maxByKey[m.key]) * 100}%`, background: colorByIndex ? seriesColor(i) : m.color }} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
