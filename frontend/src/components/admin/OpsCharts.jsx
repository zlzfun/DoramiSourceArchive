/**
 * 运维首页图表件(issue #114,样页 v4「A 精修」):手写 SVG,不走 recharts——首页磁贴要逐柱强调、
 * 右侧两条刻度、X 轴只标首尾、名字压在淡色条上、环图中心用真实像素字号,recharts 难以精确控制。
 * 画风规则(Carbon / dataviz):逐日计数 = 离散柱、账户增长 = 阶梯线;单系列柱强调末柱(今天),其余退一档;
 * 网格是发丝实线;所有图共用一个浮层(OpsTipProvider),日期 → 系列(固定顺序)→ 合计。
 * 颜色一律走 index.css 的 .ops-* 类(token),这里只给类名。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motionReduced } from '../../motion';
import { TipCtx, axisFmt, colPath, dayHead, fmt, md, niceCeil, useBox, useOpsTip } from './opsChartKit';

/* ── 共用浮层 ── */
export function OpsTipProvider({ children }) {
  const [tip, setTip] = useState(null); // { x, y, anchor?: DOMRect, head, unit, rows, total }
  const elRef = useRef(null);
  const [pos, setPos] = useState({ left: -9999, top: -9999 });
  useLayoutEffect(() => {
    if (!tip || !elRef.current) return;
    const el = elRef.current; const w = el.offsetWidth; const h = el.offsetHeight;
    const vw = window.innerWidth; const vh = window.innerHeight;
    let left; let top;
    if (tip.anchor) {
      left = Math.max(8, Math.min(tip.anchor.left, vw - 8 - w));
      top = tip.anchor.bottom + 6;
      if (top + h > vh - 8) top = tip.anchor.top - 6 - h;
    } else {
      left = tip.x + 14; if (left + w > vw - 8) left = tip.x - 14 - w;
      left = Math.max(8, left);
      top = Math.max(8, Math.min(tip.y - h / 2, vh - h - 8));
    }
    setPos({ left, top: Math.max(8, top) });
  }, [tip]);
  useEffect(() => {
    if (!tip) return undefined;
    const off = () => setTip(null);
    window.addEventListener('scroll', off, { passive: true, capture: true });
    return () => window.removeEventListener('scroll', off, { capture: true });
  }, [tip]);
  const api = useMemo(() => ({ show: (t) => setTip(t), hide: () => setTip(null) }), []);
  return (
    <TipCtx.Provider value={api}>
      {children}
      {tip && createPortal(
        <div ref={elRef} className="ops-tip" role="tooltip" style={{ left: pos.left, top: pos.top }}>
          <div className="ops-tip-head"><span>{tip.head}</span>{tip.unit ? <span className="ops-tip-unit">{tip.unit}</span> : null}</div>
          {tip.rows.map((r) => (
            <div key={r.name} className={`ops-tip-row${r.value === 0 ? ' is-zero' : ''}${r.tone ? ` is-${r.tone}` : ''}`}>
              <span className="ops-tip-name"><i className={r.swatch ? `ops-sw ${r.swatch}` : 'ops-sw is-none'} />{r.name}</span>
              <span className="ops-tip-val">{typeof r.value === 'number' ? fmt(r.value) : r.value}</span>
            </div>
          ))}
          {tip.total != null && (
            <div className="ops-tip-row is-total"><span className="ops-tip-name"><i className="ops-sw is-none" />合计</span><span className="ops-tip-val">{fmt(tip.total)}</span></div>
          )}
        </div>,
        document.body,
      )}
    </TipCtx.Provider>
  );
}

/* ── 切时间窗时数字滚到新值(≈ 400ms;减少动效直落) ── */
export function RollNumber({ value, format = fmt }) {
  const [shown, setShown] = useState(value);
  const prev = useRef(value);
  useEffect(() => {
    const from = prev.current; prev.current = value;
    if (from === value || typeof from !== 'number' || typeof value !== 'number' || motionReduced()) { setShown(value); return undefined; }
    let raf = 0; const t0 = performance.now();
    const step = (t) => {
      const k = Math.min(1, (t - t0) / 400); const e = 1 - (1 - k) ** 3;
      setShown(from + (value - from) * e);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{typeof shown === 'number' ? format(Number.isInteger(value) ? Math.round(shown) : shown) : shown}</>;
}

/* ── 逐日柱:单系列(强调末柱)或两档堆叠(mini);axis = 右侧两条刻度 + X 首尾日期 ── */
export function DailyBars({ id, days, values, names, tones, unit, mini = false, todayIso, label, onOpen }) {
  const [ref, { w, h }] = useBox();
  const tip = useOpsTip();
  const [hot, setHot] = useState(-1);
  const n = days.length;
  const L = 0; const R = mini ? 0 : 34; const T = mini ? 3 : 8; const B = mini ? 1 : 20;
  const iw = Math.max(1, w - L - R); const ih = Math.max(1, h - T - B);
  const sums = values.map((row) => row.reduce((a, b) => a + b, 0));
  // 计数轴:顶格取整齐值且至少 2,半格只在是整数时画(不出 0.5 这类小数刻度)
  const max = mini ? Math.max(1, ...sums) : Math.max(2, niceCeil(Math.max(1, ...sums)));
  const yTicks = Number.isInteger(max / 2) ? [0, max / 2, max] : [0, max];
  const Y = (v) => T + ih - (v / max) * ih;
  const slot = iw / Math.max(1, n);
  const bw = Math.max(1, Math.min(mini ? 6 : 10, slot * 0.6));
  const rad = mini ? 1.5 : 3;
  const single = names.length === 1;
  const show = (i, e) => {
    setHot(i);
    tip.show({
      x: e.clientX, y: e.clientY, head: dayHead(days[i], todayIso), unit,
      rows: names.map((nm, j) => ({ name: nm, swatch: single ? 'is-accent' : tones[j], value: values[i][j] })),
      total: single ? null : sums[i],
    });
  };
  const hide = () => { setHot(-1); tip.hide(); };
  return (
    <div ref={ref} className={`ops-chart-box${onOpen ? '' : ' is-static'}`} data-chart={id} onClick={onOpen} title={onOpen ? '打开完整图表' : undefined}>
      {w > 0 && h > 0 && (
        <svg className="ops-chart" width={w} height={h} role="img" aria-label={label}>
          {mini
            ? <line className="ops-gl" x1={L} x2={w - R} y1={T + ih} y2={T + ih} />
            : yTicks.map((v) => (
              <g key={v}>
                <line className={`ops-gl${v === 0 ? ' is-base' : ''}`} x1={L} x2={w - R} y1={Y(v)} y2={Y(v)} />
                {v > 0 && <text x={w - R + 8} y={Y(v) + 4}>{axisFmt(v)}</text>}
              </g>
            ))}
          {hot >= 0 && <rect className="ops-cur" x={L + hot * slot} y={T} width={slot} height={ih} />}
          {days.map((d, i) => {
            const x = L + i * slot + (slot - bw) / 2;
            if (!sums[i]) return <rect key={d} className="ops-empty-dot" x={x + bw / 2 - 1} y={T + ih - 2} width={2} height={2} rx={1} />;
            let acc = 0;
            const top = values[i].reduce((t, v, j) => (v ? j : t), -1);
            return (
              <g key={d}>
                {values[i].map((v, j) => {
                  if (!v) return null;
                  const y0 = Y(acc); const y1 = Y(acc + v);
                  const hh = Math.max(0.8, y0 - y1 - (acc > 0 ? 1 : 0));
                  acc += v;
                  const cls = single ? `ops-bar${i === n - 1 ? ' is-now' : ''}` : `ops-bar ${tones[j]}`;
                  return j === top
                    ? <path key={j} className={cls} d={colPath(x, y1, bw, hh, rad)} />
                    : <rect key={j} className={cls} x={x} y={y1} width={bw} height={hh} />;
                })}
              </g>
            );
          })}
          {!mini && n > 1 && (
            <>
              <text x={L} y={h - 4} textAnchor="start">{md(days[0])}</text>
              <text x={w - R} y={h - 4} textAnchor="end">{md(days[n - 1])}</text>
            </>
          )}
          {days.map((d, i) => (
            <rect key={`hit-${d}`} className="ops-hit" x={L + i * slot} y={T} width={slot} height={ih} onMouseMove={(e) => show(i, e)} onMouseLeave={hide} />
          ))}
        </svg>
      )}
    </div>
  );
}

/* ── 账户增长阶梯线(无聚合端点时的账户主图):只在有人加入的那天抬一阶 ── */
export function StepLine({ id, days, news, start, todayIso, onOpen }) {
  const [ref, { w, h }] = useBox();
  const tip = useOpsTip();
  const [hot, setHot] = useState(-1);
  const n = days.length;
  const L = 0; const R = 34; const T = 10; const B = 20;
  const iw = Math.max(1, w - L - R); const ih = Math.max(1, h - T - B);
  const tot = news.reduce((acc, v, i) => { acc.push((i ? acc[i - 1] : start) + v); return acc; }, []);
  const lo = Math.max(0, start - 1); const hi = (tot[n - 1] ?? start) + 1;
  const Y = (v) => T + ih - ((v - lo) / Math.max(1, hi - lo)) * ih;
  const slot = iw / Math.max(1, n); const X = (i) => L + (i + 0.5) * slot;
  let d = `M${L},${Y(start).toFixed(1)}`;
  tot.forEach((v, i) => { d += `H${X(i).toFixed(1)}V${Y(v).toFixed(1)}`; });
  d += `H${(L + iw).toFixed(1)}`;
  const ticks = [...new Set([lo, Math.round((lo + hi) / 2), hi])];
  const show = (i, e) => {
    setHot(i);
    tip.show({ x: e.clientX, y: e.clientY, head: dayHead(days[i], todayIso), unit: '个', rows: [{ name: '累计账户', swatch: 'is-accent', value: tot[i] }, { name: '当日新增', value: news[i] }] });
  };
  const hide = () => { setHot(-1); tip.hide(); };
  return (
    <div ref={ref} className="ops-chart-box" data-chart={id} onClick={onOpen} title="打开完整图表">
      {w > 0 && h > 0 && (
        <svg className="ops-chart" width={w} height={h} role="img" aria-label="账户增长">
          {ticks.map((v) => (
            <g key={v}>
              <line className="ops-gl" x1={L} x2={w - R} y1={Y(v)} y2={Y(v)} />
              <text x={w - R + 8} y={Y(v) + 4}>{v}</text>
            </g>
          ))}
          {hot >= 0 && <line className="ops-xhair" x1={X(hot)} x2={X(hot)} y1={T} y2={T + ih} />}
          <path className="ops-step" d={d} />
          {news.map((v, i) => (v > 0 ? <circle key={days[i]} className="ops-step-dot" cx={X(i)} cy={Y(tot[i])} r={3.5} /> : null))}
          {hot >= 0 && <circle className="ops-xdot" cx={X(hot)} cy={Y(tot[hot])} r={4} />}
          {n > 1 && (
            <>
              <text x={L} y={h - 4} textAnchor="start">{md(days[0])}</text>
              <text x={w - R} y={h - 4} textAnchor="end">{md(days[n - 1])}</text>
            </>
          )}
          {days.map((dd, i) => <rect key={`hit-${dd}`} className="ops-hit" x={L + i * slot} y={T} width={slot} height={ih} onMouseMove={(e) => show(i, e)} onMouseLeave={hide} />)}
        </svg>
      )}
    </div>
  );
}

/* ── 排行条(Tremor BarList / Plausible 做法):名字压在淡色条上,值单独右列 ── */
const BLIST_ROW = 34; const BLIST_GAP = 8;
export function BarList({ id, rows, unit, metric, total, onOpen }) {
  const tip = useOpsTip();
  const [ref, { h }] = useBox();
  // 按容器高度放得下几条就画几条(至少 3 条),不截半行
  const fit = h ? Math.max(3, Math.floor((h + BLIST_GAP) / (BLIST_ROW + BLIST_GAP))) : rows.length;
  const shown = rows.slice(0, fit);
  const max = Math.max(1, ...shown.map((r) => r.value));
  if (!rows.length) return <div className="ops-chart-box is-static ops-empty" data-chart={id}>暂无阅读</div>;
  return (
    <div ref={ref} className="ops-chart-box" data-chart={id} onClick={onOpen} title="打开完整排行">
      <div className="ops-blist" role="list">
        {shown.map((r) => (
          <div
            key={r.name}
            className="ops-blist-row"
            role="listitem"
            onMouseMove={(e) => tip.show({ x: e.clientX, y: e.clientY, head: r.name, unit, rows: [{ name: metric, swatch: 'is-accent', value: r.value }, ...(total ? [{ name: '占比', value: `${((r.value / total) * 100).toFixed(1)}%` }] : [])] })}
            onMouseLeave={tip.hide}
          >
            <span className="ops-blist-track"><span className="ops-blist-bar" style={{ width: `${Math.max(2, (r.value / max) * 100).toFixed(1)}%` }} /></span>
            <span className="ops-blist-name" title={r.name}>{r.name}</span>
            <span className="ops-blist-val">{fmt(r.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── 环图(播客阶段构成):环厚为半径 25%,段间 2.2° 缝,极小段按 2.4° 下限画(数值以浮层为准);中心 = 总数 + 标签 ── */
const TONE_CLASS = { ok: 'is-ok', run: 'is-run', idle: 'is-idle', new: 'is-new', bad: 'is-bad' };
export function Donut({ id, parts, total, label, unit, onOpen }) {
  const [ref, { w, h }] = useBox();
  const tip = useOpsTip();
  const [hot, setHot] = useState('');
  const size = Math.max(72, Math.min(w, h, 168));
  const sum = parts.reduce((a, p) => a + p.value, 0) || 1;
  const SW = 12.5; const Rr = 50 - SW / 2 - 1; const C = 2 * Math.PI * Rr; const GAP = 2.2; const MIN = 2.4;
  const shown = parts.filter((p) => p.value > 0);
  const angs = shown.map((p) => Math.max(MIN, (p.value / sum) * 360));
  const over = angs.reduce((a, b) => a + b, 0) - 360;
  if (over > 0) angs[angs.indexOf(Math.max(...angs))] -= over;
  const starts = angs.reduce((acc, a, i) => { acc.push(i ? acc[i - 1] + angs[i - 1] : 0); return acc; }, []);
  const segs = shown.map((p, i) => {
    const a = angs[i]; const len = Math.max(0.6, ((a - GAP) / 360) * C); const off = -((starts[i] + GAP / 2) / 360) * C;
    return { p, len, off };
  });
  const show = (p, e) => {
    setHot(p.key);
    tip.show({ x: e.clientX, y: e.clientY, head: p.name, unit, rows: [{ name: '数量', swatch: TONE_CLASS[p.tone], value: p.value }, { name: '占比', value: `${((p.value / sum) * 100).toFixed(1)}%` }, ...(p.detail || []).map(([nm, v]) => ({ name: nm, value: v }))] });
  };
  const hide = () => { setHot(''); tip.hide(); };
  return (
    <div ref={ref} className="ops-chart-box ops-donut-box" data-chart={id} onClick={onOpen} title="打开完整构成">
      {w > 0 && h > 0 && (
        <div className="ops-donut-wrap" style={{ width: size, height: size }}>
          <svg className={`ops-donut${hot ? ' has-hot' : ''}`} viewBox="0 0 100 100" role="img" aria-label={parts.map((p) => `${p.name} ${p.value}`).join(',')}>
            <g transform="rotate(-90 50 50)">
              {segs.map(({ p, len, off }) => (
                <circle
                  key={p.key}
                  className={`ops-donut-p ${TONE_CLASS[p.tone]}${hot === p.key ? ' is-hot' : ''}`}
                  cx="50" cy="50" r={Rr} fill="none" strokeWidth={SW}
                  strokeDasharray={`${len.toFixed(2)} ${C.toFixed(2)}`} strokeDashoffset={off.toFixed(2)}
                  onMouseMove={(e) => show(p, e)} onMouseLeave={hide}
                />
              ))}
            </g>
          </svg>
          <div className="ops-donut-c"><span className="ops-donut-n">{fmt(total)}</span><span className="ops-donut-l">{label}</span></div>
        </div>
      )}
    </div>
  );
}

/* ── 新闻价值直方图(1–10 整数档):靛色序数阶(分越高越深)+ P50 标线 ── */
const HIST_STEP = ['is-h1', 'is-h1', 'is-h2', 'is-h3', 'is-h4', 'is-h5', 'is-h6', 'is-h7', 'is-h8', 'is-h9'];
export function ScoreHistogram({ id, counts, p50, onOpen }) {
  const [ref, { w, h }] = useBox();
  const tip = useOpsTip();
  const [hot, setHot] = useState(-1);
  const L = 2; const R = 2; const T = w >= 150 ? 16 : 6; const B = 1;
  const iw = Math.max(1, w - L - R); const ih = Math.max(1, h - T - B);
  const total = counts.reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...counts);
  const Y = (v) => T + ih - (v / max) * ih;
  const slot = iw / 10; const bw = Math.min(14, slot * 0.6);
  const px = L + (Math.min(10, Math.max(1, p50 || 1)) - 1) * slot;
  const show = (b, e) => {
    setHot(b);
    tip.show({ x: e.clientX, y: e.clientY, head: `${b + 1} 分档`, unit: '篇', rows: [{ name: '文章', swatch: HIST_STEP[b], value: counts[b] }, { name: '占比', value: total ? `${((counts[b] / total) * 100).toFixed(1)}%` : '0%' }] });
  };
  const hide = () => { setHot(-1); tip.hide(); };
  return (
    <div ref={ref} className="ops-chart-box" data-chart={id} onClick={onOpen} title="打开完整分布">
      {w > 0 && h > 0 && (
        <svg className="ops-chart" width={w} height={h} role="img" aria-label="新闻价值分布">
          <line className="ops-gl" x1={L} x2={w - R} y1={T + ih} y2={T + ih} />
          {hot >= 0 && <rect className="ops-cur" x={L + hot * slot} y={T} width={slot} height={ih} />}
          {counts.map((v, b) => {
            const x = L + b * slot + (slot - bw) / 2; const y = Y(v); const hh = Math.max(v ? 1.5 : 0, T + ih - y);
            return hh > 0 ? <path key={b} className={`ops-hbar ${HIST_STEP[b]}`} d={colPath(x, T + ih - hh, bw, hh, 3)} /> : null;
          })}
          {p50 ? (
            <>
              <line className="ops-mark-line" x1={px} x2={px} y1={T - 3} y2={T + ih} />
              {w >= 150 && <text className="is-ink" x={px - 4} y={T - 5} textAnchor="end">P50</text>}
            </>
          ) : null}
          {counts.map((_, b) => <rect key={`hit-${b}`} className="ops-hit" x={L + b * slot} y={T} width={slot} height={ih + B} onMouseMove={(e) => show(b, e)} onMouseLeave={hide} />)}
        </svg>
      )}
    </div>
  );
}
