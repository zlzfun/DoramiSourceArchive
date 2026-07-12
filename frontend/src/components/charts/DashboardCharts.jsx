/**
 * 运维看板图表件（基于 recharts）。坐标轴/网格走 dorami 令牌（暗色翻转），
 * 多系列颜色走固定序槽位 + 实体稳定色(colorForEntity);hover 明细由 ThemedTooltip 浮现。
 */
import { useState, useRef, useEffect, useCallback } from 'react';
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
import { C_PRIMARY, AXIS, GRID, colorForEntity, fmtNumLocale as fmt } from './chartUtils';

const AXIS_TICK = { fill: AXIS, fontSize: 11 };

// 统一 hover 浮层：surface-card 风格 + 令牌，暗色安全。
function ThemedTooltip({ active, payload, label, titleKey, colorFor }) {
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
              {/* 面积段描边是 surface(段间缝),payload.color 取 stroke 会发白——优先走实体色 */}
              <span className="h-2 w-2 rounded-sm" style={{ background: colorFor ? colorFor(p.name) : (p.fill || p.color) }} />
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
 * 多系列每日堆叠面积图,含可切换维度的 mini-seg。
 * datasets: { <dimKey>: {data, keys} }(已透视、零填充,keys 超 6 系由 pivotDaily 并入「其它」)。
 * dims: [[dimKey, 标签], …];namespace 供「色随实体」的槽位记忆(如 'ai-purpose'/'ai-user')。
 * 标记规格(dataviz):段间 2px 表面缝(surface 描边)、末端选择性直标(总量 Top2)、
 * hairline 网格、图例常备、tooltip 十字线。
 */
export function MultiSeriesArea({
  title,
  datasets,
  height = 230,
  defaultDim,
  dims = [['purpose', '按用途'], ['user', '按用户']],
  namespace = 'default',
}) {
  const [dim, setDim] = useState(defaultDim ?? dims[0][0]);
  const ds = datasets[dim] || { data: [], keys: [] };
  const colorOf = (k) => colorForEntity(`${namespace}:${dim}`, k);
  // (末端直标已退役:两系列末端相近时文字互叠、右缘裁字,且与常备自绘图例信息重复。)
  return (
    <div className="surface-card card-pad rounded-[var(--r-card)]">
      <div className="card-head">
        <span className="card-title">{title}</span>
        {dims.length > 1 && (
          <span className="mini-seg" style={{ marginLeft: 'auto' }} role="group" aria-label="统计维度">
            {dims.map(([k, lbl]) => (
              <button key={k} type="button" onClick={() => setDim(k)} className={`mini-seg-btn ${dim === k ? 'is-on' : ''}`}>
                {lbl}
              </button>
            ))}
          </span>
        )}
      </div>
      {/* 自绘图例(≥2 系列常备;dark 安全,避免 recharts Legend 默认深色文字) */}
      {ds.keys.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
          {ds.keys.map((k) => (
            <span key={k} className="inline-flex items-center gap-1 tiny-meta text-slate-500">
              <span className="h-2 w-2 rounded-sm" style={{ background: colorOf(k) }} />
              {k}
            </span>
          ))}
        </div>
      )}
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={ds.data} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} tickCount={3} tickFormatter={kFormatter} allowDecimals={false} />
            <Tooltip cursor={{ stroke: GRID }} content={<ThemedTooltip titleKey="day" colorFor={colorOf} />} />
            {ds.keys.map((k) => (
              <Area
                key={k}
                type="monotone"
                dataKey={k}
                name={k}
                stackId="s"
                stroke="var(--dorami-surface)"
                fill={colorOf(k)}
                fillOpacity={0.9}
                strokeWidth={2}
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
 * (运维波:colorByIndex 已退役——逐行取色是「色编码排位」,违反「色随实体」纪律;
 *  单系列排行统一 C_PRIMARY。)
 */
export function RankBars({ rows, labelKey, valueKey, name, color = C_PRIMARY, height = 200, emptyHint = '暂无数据', tickFormatter, labelWidth = 96 }) {
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
          <Bar dataKey={valueKey} name={name} fill={color} radius={[0, 4, 4, 0]} maxBarSize={22} />
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
 * (运维波:colorByIndex 已退役——逐行取色是「色编码排位」;数值改为默认常显
 *  mono 直标,alwaysShowValue=false 可回到 hover 浮现。)列表可滚动且未到底时,
 * 底部内容以 CSS mask 渐隐(.scroll-fade-b)暗示「还可下滑」,到底即撤除、末行完整可见。
 */
export function BarList({ rows, nameKey, metrics, maxHeight = 248, emptyHint = '暂无数据', alwaysShowValue = true }) {
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
      {/* 图例:≥2 指标常备(单指标由标题命名,免图例) */}
      {metrics.length > 1 && (
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
          {rows.map((r) => (
            <div key={r[nameKey]} className="group rounded-[var(--r-sm)] px-1.5 py-1.5 transition-colors hover:bg-[var(--dorami-soft)]">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-bold text-slate-700" title={r[nameKey]}>{r[nameKey]}</span>
                <span className={`shrink-0 tiny-meta tabular-nums text-slate-500 ${alwaysShowValue ? '' : 'opacity-0 transition-opacity group-hover:opacity-100'}`}>
                  {metrics.map((m) => `${m.name} ${Number(r[m.key]) || 0}`).join(' · ')}
                </span>
              </div>
              <div className="mt-1 space-y-0.5">
                {metrics.map((m) => (
                  <div key={m.key} className="h-1.5 overflow-hidden rounded-r-full bg-[var(--dorami-well)]">
                    {/* 数据端圆头、基线端方角(标记规格) */}
                    <div className="h-full rounded-r-full" style={{ width: `${((Number(r[m.key]) || 0) / maxByKey[m.key]) * 100}%`, background: m.color }} />
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
