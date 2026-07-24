import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { fetchMediaHeatmap, fetchMediaDay, prefetchArticleMedia } from '../../api';
import { useModalA11y } from '../../hooks/useModalA11y';
import { fmtNum } from './adminUtils';

// 媒体热点图（图床波，刻度 1:1 取自样页 media-heatmap.html）：
// 一格一天（按文章入库日归集），深浅 = 当日图片的本地缓存覆盖率，右上角三角 = 当日有下载失败。
// 点格开抽屉看当日逐篇明细与失败原因，可对单篇定点重抓（取代已撤的全量回填）。

const WEEKS = 53;
const DOW_LABELS = ['', '一', '', '三', '', '五', ''];

const iso = (dt) => {
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, '0');
  const d = String(dt.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

// 覆盖率四档 + 无图档；null = 当日无文章入库（空格，不可点）。
function levelOf(day) {
  if (!day || !day.articles) return null;
  if (!day.images_total) return 0;
  const rate = day.cached / day.images_total;
  if (rate === 0) return 1;
  if (rate < 0.5) return 2;
  if (rate < 0.9) return 3;
  return 4;
}

function statusOf(day) {
  if (!day || !day.articles) return null;
  if (!day.images_total) return 'noimg';
  if (!day.cached) return 'none';
  if (day.failed > 0 || day.pending > 0) return 'partial';
  return 'ok';
}

export default function MediaHeatmap({ showToast }) {
  const [data, setData] = useState(null);
  const [mode, setMode] = useState('coverage'); // coverage | status
  // 视图:'rolling'(近一年滚动窗,默认) | 具体年份数字(自然年,GitHub 式年份轨切换)
  const [view, setView] = useState('rolling');
  const [years, setYears] = useState([]); // 归档覆盖的年份(降序),随响应更新
  const [activeDate, setActiveDate] = useState(null);
  const [dayDetail, setDayDetail] = useState(null);
  const [retrying, setRetrying] = useState(null); // 正在重抓的 article_id
  const [tip, setTip] = useState(null); // { text, x, y }

  const drawerRef = useRef(null);
  const tipRef = useRef(null);
  const closeDrawer = useCallback(() => setActiveDate(null), []);
  useModalA11y(Boolean(activeDate), closeDrawer, drawerRef);

  const loadHeatmap = useCallback(() => {
    fetchMediaHeatmap(365, view === 'rolling' ? null : view)
      .then((d) => {
        setData(d);
        if (Array.isArray(d?.years)) setYears(d.years);
      })
      .catch(() => {});
  }, [view]);

  useEffect(() => { loadHeatmap(); }, [loadHeatmap]);

  // 打开某天 → 拉当日明细（切换日期时先清空，避免看到上一天的残影）
  useEffect(() => {
    if (!activeDate) { setDayDetail(null); return; }
    let alive = true;
    setDayDetail(null);
    fetchMediaDay(activeDate)
      .then((d) => { if (alive) setDayDetail(d); })
      .catch((error) => { if (alive) showToast?.(error.message || '获取当日媒体明细失败', 'error'); });
    return () => { alive = false; };
  }, [activeDate, showToast]);

  const byDate = useMemo(() => {
    const map = new Map();
    for (const day of data?.days || []) map.set(day.date, day);
    return map;
  }, [data]);

  // 格阵几何:滚动视图 = 53 周自今天回溯;年份视图 = 该自然年(列首对齐周日,
  // 年首前的对齐格与年尾后的格隐藏)。周数随视图变化,列模板以内联样式下发。
  const { columns, monthLabels, weekCount } = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let gridStart;
    let rangeStart;
    let rangeEnd;
    let weeks;
    if (view === 'rolling') {
      gridStart = new Date(today);
      gridStart.setDate(gridStart.getDate() - 7 * (WEEKS - 1) - today.getDay());
      rangeStart = gridStart;
      rangeEnd = today;
      weeks = WEEKS;
    } else {
      rangeStart = new Date(view, 0, 1);
      rangeEnd = new Date(view, 11, 31);
      gridStart = new Date(rangeStart);
      gridStart.setDate(gridStart.getDate() - rangeStart.getDay());
      weeks = Math.ceil((rangeStart.getDay() + (view % 4 === 0 && (view % 100 !== 0 || view % 400 === 0) ? 366 : 365)) / 7);
    }
    const cols = [];
    const labels = [];
    let lastMonth = -1;
    for (let w = 0; w < weeks; w += 1) {
      const cells = [];
      let weekMonth = null;
      for (let dow = 0; dow < 7; dow += 1) {
        const dt = new Date(gridStart);
        dt.setDate(gridStart.getDate() + w * 7 + dow);
        const future = dt > today || dt < rangeStart || dt > rangeEnd;
        // 月份标签取本周第一个落在范围内的日子(年份视图的年首周含上一年对齐格)
        if (weekMonth === null && !future) weekMonth = dt.getMonth();
        cells.push({ date: iso(dt), future });
      }
      cols.push(cells);
      labels.push(weekMonth !== null && weekMonth !== lastMonth ? `${weekMonth + 1}月` : '');
      if (weekMonth !== null && weekMonth !== lastMonth) lastMonth = weekMonth;
    }
    return { columns: cols, monthLabels: labels, weekCount: weeks };
  }, [view]);

  const totals = useMemo(() => {
    const acc = { images: 0, cached: 0, failed: 0, pending: 0 };
    for (const day of data?.days || []) {
      acc.images += day.images_total;
      acc.cached += day.cached;
      acc.failed += day.failed;
      acc.pending += day.pending;
    }
    return acc;
  }, [data]);

  const showTip = useCallback((event, day) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const text = !day.images_total
      ? `${day.date} · ${day.articles} 篇文章｜正文无图片`
      : `${day.date} · ${day.articles} 篇文章｜图片 ${day.images_total} 张 · 已缓存 ${day.cached}`
        + (day.failed ? ` · 失败 ${day.failed}` : '')
        + (day.pending ? ` · 未抓取 ${day.pending}` : '');
    setTip({ text, x: rect.left + rect.width / 2, y: rect.top });
  }, []);

  // 浮标定位：渲染后按实测宽高居中并夹在视口内（先渲染再量，避免闪跳）
  useEffect(() => {
    const el = tipRef.current;
    if (!el || !tip) return;
    const rect = el.getBoundingClientRect();
    const left = Math.min(Math.max(8, tip.x - rect.width / 2), window.innerWidth - rect.width - 8);
    el.style.left = `${left}px`;
    el.style.top = `${Math.max(8, tip.y - rect.height - 8)}px`;
  }, [tip]);

  const handleRetry = useCallback(async (articleId) => {
    setRetrying(articleId);
    try {
      const result = await prefetchArticleMedia(articleId);
      // 局部更新该篇的状态，并同步刷新热点图（当日格子深浅随之变化）
      setDayDetail((prev) => prev && {
        ...prev,
        articles: prev.articles.map((a) => (a.id !== articleId ? a : {
          ...a,
          images: result.images,
          cached: result.images.filter((i) => i.status === 'cached').length,
          failed: result.images.filter((i) => i.status === 'failed').length,
          pending: result.images.filter((i) => i.status === 'pending').length,
        })),
      });
      loadHeatmap();
      showToast?.(
        result.failed ? `重抓完成：成功 ${result.cached} 张，仍失败 ${result.failed} 张` : `重抓完成：${result.cached} 张图片已缓存`,
        result.failed ? 'info' : 'success',
      );
    } catch (error) {
      showToast?.(error.message || '重抓图片失败', 'error');
    } finally {
      setRetrying(null);
    }
  }, [showToast, loadHeatmap]);

  if (!data) {
    return (
      <section className="surface-card card-pad rounded-[var(--r-card)]" style={{ marginTop: 12 }}>
        <p className="tiny-meta text-center">
          <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载媒体热点图…
        </p>
      </section>
    );
  }

  const coverage = totals.images ? Math.round((totals.cached / totals.images) * 100) : 0;
  const rangeLabel = view === 'rolling' ? '近一年' : `${view} 年`;

  return (
    <>
      <section className="surface-card card-pad rounded-[var(--r-card)]" style={{ marginTop: 12 }}>
        <div className="card-head">
          <span className="card-title">
            {rangeLabel} · 逐日缓存覆盖{totals.images ? ` · 覆盖率 ${coverage}%` : ''}
          </span>
          <div className="mini-seg" role="group" aria-label="配色语义">
            {[['coverage', '覆盖率深浅'], ['status', '状态分色']].map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setMode(key)}
                className={`mini-seg-btn ${mode === key ? 'is-on' : ''}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* 格阵 + 右侧年份轨(GitHub 式):滚动窗与自然年切换,填掉宽屏下右缘的空旷 */}
        <div className="heat-body">
        <div className="heat-scroll">
          <div className={`heat-wrap ${mode === 'status' ? 'mode-status' : ''}`}>
            <div className="heat-months" style={{ gridTemplateColumns: `repeat(${weekCount}, 16px)` }}>
              {monthLabels.map((label, i) => (
                <span key={`m${i}`} className="heat-month" style={{ gridColumn: i + 1 }}>{label}</span>
              ))}
            </div>
            <div className="heat-dows">
              {DOW_LABELS.map((label, i) => <span key={`d${i}`} className="heat-dow">{label}</span>)}
            </div>
            <div
              className="heat-grid"
              style={{ gridTemplateColumns: `repeat(${weekCount}, 13px)` }}
              onMouseLeave={() => setTip(null)}
            >
              {columns.map((week, w) => week.map((cell, dow) => {
                const day = byDate.get(cell.date);
                const level = levelOf(day);
                const empty = level === null;
                return (
                  <button
                    key={cell.date}
                    type="button"
                    className={`heat-cell ${day?.failed ? 'has-fail' : ''} ${activeDate === cell.date ? 'is-on' : ''}`}
                    style={{ gridColumn: w + 1, gridRow: dow + 1, visibility: cell.future ? 'hidden' : undefined }}
                    disabled={empty || cell.future}
                    data-lvl={empty ? undefined : level}
                    data-st={empty ? undefined : statusOf(day)}
                    onClick={() => setActiveDate(cell.date)}
                    onMouseEnter={(e) => !empty && showTip(e, day)}
                    onFocus={(e) => !empty && showTip(e, day)}
                    onBlur={() => setTip(null)}
                    aria-label={empty
                      ? `${cell.date} 无入库文章`
                      : `${cell.date}：${day.articles} 篇文章，图片 ${day.images_total} 张，已缓存 ${day.cached}，失败 ${day.failed}，未抓取 ${day.pending}`}
                  />
                );
              }))}
            </div>
          </div>
        </div>

        {(years.length > 0) && (
          <div className="heat-years" role="group" aria-label="时间范围">
            <button
              type="button"
              className={`heat-year-btn ${view === 'rolling' ? 'is-on' : ''}`}
              aria-pressed={view === 'rolling'}
              onClick={() => setView('rolling')}
            >
              近一年
            </button>
            {years.map((y) => (
              <button
                key={y}
                type="button"
                className={`heat-year-btn ${view === y ? 'is-on' : ''}`}
                aria-pressed={view === y}
                onClick={() => setView(y)}
              >
                {y}
              </button>
            ))}
          </div>
        )}
        </div>

        <div className="heat-foot">
          {mode === 'coverage' ? (
            <span className="heat-note">
              <span className="heat-swatch is-notch" aria-hidden="true" />
              当日有图片下载失败
            </span>
          ) : <span />}
          <span className="heat-legend">
            {mode === 'coverage' ? (
              <>
                <span>低</span>
                <span className="heat-legend-cells">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <span key={i} className="heat-swatch" style={{ background: `var(--heat-${i})` }} />
                  ))}
                </span>
                <span>高</span>
                <span style={{ marginLeft: 6 }}>缓存覆盖率</span>
              </>
            ) : (
              [['var(--state-ok)', '全部已缓存'], ['var(--state-warn)', '部分缺失'],
                ['var(--state-idle)', '全未抓取'], ['var(--heat-0)', '无图']].map(([color, label]) => (
                  <span key={label} className="heat-legend-item">
                    <span className="heat-swatch" style={{ background: color }} />{label}
                  </span>
                ))
            )}
          </span>
        </div>
      </section>

      {/* ── 当日明细抽屉（ledger-drawer 语法，同用户活动抽屉）── */}
      <div className={`ledger-scrim ${activeDate ? 'is-open' : ''}`} onClick={closeDrawer} aria-hidden="true" />
      <aside
        ref={drawerRef}
        className={`ledger-drawer ${activeDate ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={activeDate ? `${activeDate} · 媒体明细` : '当日媒体明细'}
        aria-hidden={!activeDate}
      >
        <div className="ledger-drawer-head">
          <span className="ledger-drawer-title" style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>
            {activeDate || ''}
          </span>
          <button type="button" className="icon-button shrink-0" onClick={closeDrawer} aria-label="关闭详情">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="ledger-drawer-body">
          {!dayDetail ? (
            <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-6 text-center tiny-meta">
              <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载当日明细…
            </p>
          ) : (
            <MediaDayDetail
              detail={dayDetail}
              retrying={retrying}
              onRetry={handleRetry}
            />
          )}
        </div>
      </aside>

      {tip && <div ref={tipRef} className="heat-tip" role="status">{tip.text}</div>}
    </>
  );
}

// 抽屉内容：当日三态读数 + 逐篇文章（图片状态胶囊 / 失败原因 / 定点重抓）。
function MediaDayDetail({ detail, retrying, onRetry }) {
  const sum = detail.articles.reduce((acc, a) => ({
    cached: acc.cached + a.cached,
    failed: acc.failed + a.failed,
    pending: acc.pending + a.pending,
  }), { cached: 0, failed: 0, pending: 0 });
  const withImages = detail.articles.filter((a) => a.images_total > 0);

  return (
    <>
      <div className="heat-dstats">
        <div className="heat-dstat"><span className="heat-dstat-num is-ok">{fmtNum(sum.cached)}</span><span className="heat-dstat-lbl">已缓存</span></div>
        <div className="heat-dstat"><span className="heat-dstat-num is-warn">{fmtNum(sum.failed)}</span><span className="heat-dstat-lbl">失败</span></div>
        <div className="heat-dstat"><span className="heat-dstat-num is-idle">{fmtNum(sum.pending)}</span><span className="heat-dstat-lbl">未抓取</span></div>
        <div className="heat-dstat"><span className="heat-dstat-num">{fmtNum(detail.articles.length)}</span><span className="heat-dstat-lbl">当日文章</span></div>
      </div>

      <div>
        <div className="drawer-sec-title">
          带图文章{withImages.length ? ` · ${withImages.length} 篇` : ''}
        </div>
        {withImages.length === 0 ? (
          <p className="heat-empty-hint">
            当日入库的 {detail.articles.length} 篇文章正文中没有图片，无需缓存。
          </p>
        ) : withImages.map((article) => {
          const canRetry = article.failed > 0 || article.pending > 0;
          const errors = article.images.filter((img) => img.status === 'failed').slice(0, 3);
          return (
            <div key={article.id} className="heat-arow">
              <div className="heat-arow-top">
                <span className="heat-arow-title">{article.title}</span>
                <span className="heat-arow-src">{article.source_id}</span>
              </div>
              <div className="heat-arow-meta">
                {article.cached > 0 && <span className="heat-pill is-ok">已缓存 {article.cached}</span>}
                {article.failed > 0 && <span className="heat-pill is-warn">失败 {article.failed}</span>}
                {article.pending > 0 && <span className="heat-pill is-idle">未抓取 {article.pending}</span>}
                {canRetry && (
                  <button
                    type="button"
                    className="heat-retry"
                    onClick={() => onRetry(article.id)}
                    disabled={retrying === article.id}
                  >
                    {retrying === article.id ? '抓取中…' : '重抓'}
                  </button>
                )}
              </div>
              {errors.length > 0 && (
                <div className="heat-errs">
                  {errors.map((img) => (
                    <div key={img.url} className="heat-err" title={`${img.url} → ${img.error || '下载失败'}`}>
                      {img.url} → <b>{img.error || '下载失败'}</b>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
