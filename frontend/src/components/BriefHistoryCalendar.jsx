import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useModalA11y } from '../hooks/useModalA11y';
import { formatDateTime } from '../utils/datetime';
import {
  addMonths,
  clampKey,
  monthGrid,
  monthLabel,
  monthOf,
  moveByKey,
  shiftMonthKeepDay,
  weekdayIndex,
} from '../utils/briefCalendar';

// 往期早报日历(issue #184 目检返修,替代按月分组的日期列表):一屏一个月,有早报的日子可点、没有的灰显;
// 正在看的那天实底加粗,今天下方一个点(编排中时点会呼吸),失败的日子数字标红;当天有多版时,版本排在月历下方。
// 非模态浮层:Esc 与层栈复用 useModalA11y(§8)但不困住 Tab,焦点先落在正在看的那天;
// 月历按 WAI-ARIA APG 日期网格走键盘(utils/briefCalendar.moveByKey),焦点走出本月时月历跟着翻,
// 范围夹在「最早一期所在月的 1 日 … 今天」;Tab 出浮层或在外面按下鼠标即关闭,触发钮自身的点击由它切换。
const WEEK_HEAD = ['一', '二', '三', '四', '五', '六', '日'];
const LIVE = new Set(['pending', 'generating']);
const STATUS_LABEL = { degraded: '降级', failed: '失败', pending: '准备中', generating: '编排中' };
const dateText = (key) => `${Number(key.slice(5, 7))}月${Number(key.slice(8, 10))}日`;
const weekdayText = (key) => `周${WEEK_HEAD[weekdayIndex(key)]}`;
const clockOf = (iso) => (iso ? formatDateTime(iso).slice(11, 16) : '');
const focusTarget = (panel) => panel?.querySelector('.bp-cal-day[tabindex="0"]') || null;

export default function BriefHistoryCalendar({
  days,          // [{key, revisions, latest}],日期降序;今天恒在其中(可能没有版次)
  todayKey,
  selDate,
  selRevision,
  live,          // 今日早报正在编排
  onPickDay,
  onPickRevision,
  onClose,
  triggerRef,
}) {
  const panelRef = useRef(null);
  const gridRef = useRef(null);
  const byKey = useMemo(() => new Map(days.map((day) => [day.key, day])), [days]);
  const minKey = useMemo(() => {
    const earliest = days.reduce((min, day) => (day.key < min ? day.key : min), todayKey);
    return `${monthOf(earliest)}-01`;
  }, [days, todayKey]);
  const [focusKey, setFocusKey] = useState(() => clampKey(selDate, minKey, todayKey));
  const [month, setMonth] = useState(() => monthOf(clampKey(selDate, minKey, todayKey)));
  const moveFocusRef = useRef(false);

  useModalA11y(true, onClose, panelRef, { trapFocus: false, initialFocus: focusTarget });
  useEffect(() => {
    const onDown = (event) => {
      if (panelRef.current?.contains(event.target) || triggerRef.current?.contains(event.target)) return;
      onClose();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onClose, triggerRef]);
  // 键盘移动后把焦点落到新的那天(跨月时那颗按钮要等这次渲染才进 DOM)
  useEffect(() => {
    if (!moveFocusRef.current) return;
    moveFocusRef.current = false;
    gridRef.current?.querySelector(`[data-day="${focusKey}"]`)?.focus();
  }, [focusKey, month]);

  const handleBlur = (event) => {
    const next = event.relatedTarget;
    if (next && !panelRef.current?.contains(next) && !triggerRef.current?.contains(next)) onClose();
  };
  // 月份按钮翻月:焦点留在按钮上,网格只挪 roving tabindex 的落点
  const goMonth = (delta) => {
    moveFocusRef.current = false;
    const nextFocus = clampKey(shiftMonthKeepDay(focusKey, delta), minKey, todayKey);
    setFocusKey(nextFocus);
    setMonth(monthOf(nextFocus));
  };
  const onGridKey = (event) => {
    const target = moveByKey(focusKey, event.key);
    if (!target) return;
    event.preventDefault();
    const next = clampKey(target, minKey, todayKey);
    // 夹在范围端点、落点没变:不留聚焦请求——否则它会留到下一次翻月,把焦点从月份按钮拽进网格(Codex E1-P2-01)
    if (next === focusKey) return;
    moveFocusRef.current = true;
    setFocusKey(next);
    setMonth(monthOf(next));
  };

  const canPrev = addMonths(month, -1) >= monthOf(minKey);
  const canNext = month < monthOf(todayKey);
  const selDay = byKey.get(selDate);
  const revisions = selDay?.revisions || [];

  return (
    <div ref={panelRef} className="bp-pop bp-cal" role="dialog" aria-label="往期早报" tabIndex={-1} onBlur={handleBlur}>
      <p className="bp-cal-title">往期早报</p>
      <div className="bp-cal-head">
        <p className="bp-cal-month" aria-live="polite">{monthLabel(month)}</p>
        {/* 到范围边界用 aria-disabled 而不是 disabled:正按着的按钮一旦 disabled 会丢焦点(落到 body),键盘用户就迷路了 */}
        <button type="button" className="bp-cal-nav" aria-label="上个月" aria-disabled={canPrev ? undefined : 'true'} onClick={() => { if (canPrev) goMonth(-1); }}>
          <ChevronLeft aria-hidden="true" />
        </button>
        <button type="button" className="bp-cal-nav" aria-label="下个月" aria-disabled={canNext ? undefined : 'true'} onClick={() => { if (canNext) goMonth(1); }}>
          <ChevronRight aria-hidden="true" />
        </button>
      </div>
      <table className="bp-cal-grid" role="grid" aria-label={monthLabel(month)} ref={gridRef} onKeyDown={onGridKey}>
        <thead>
          <tr>
            {WEEK_HEAD.map((char) => <th key={char} scope="col" abbr={`星期${char}`}>{char}</th>)}
          </tr>
        </thead>
        <tbody>
          {monthGrid(month).map((week) => (
            <tr key={week.find(Boolean)}>
              {week.map((key, index) => {
                if (!key) return <td key={`pad-${index}`} />;
                const day = byKey.get(key);
                const isToday = key === todayKey;
                const has = isToday || (day?.revisions.length || 0) > 0;
                const dayLive = isToday ? live : LIVE.has(day?.latest?.status);
                const failed = !dayLive && day?.latest?.status === 'failed';
                const on = key === selDate;
                const count = day?.revisions.length || 0;
                const notes = [
                  isToday ? '今天' : '',
                  on ? '正在看' : '',
                  !has ? '没有早报' : dayLive ? '编排中' : failed ? '失败' : count > 1 ? `共 ${count} 版` : '',
                ].filter(Boolean);
                return (
                  <td key={key} aria-selected={on || undefined}>
                    <button
                      type="button"
                      data-day={key}
                      tabIndex={key === focusKey ? 0 : -1}
                      className={`bp-cal-day ${has ? 'has-ed' : ''} ${on ? 'is-on' : ''} ${failed ? 'is-bad' : ''}`}
                      aria-disabled={has ? undefined : 'true'}
                      aria-current={isToday ? 'date' : undefined}
                      aria-label={`${dateText(key)} ${weekdayText(key)}${notes.length ? `，${notes.join('，')}` : ''}`}
                      onClick={() => { if (has) onPickDay(key); }}
                      onFocus={() => { if (key !== focusKey) setFocusKey(key); }}
                    >
                      {Number(key.slice(8, 10))}
                      {(isToday || dayLive) && <i className={`bp-cal-dot ${dayLive ? 'is-live' : ''}`} aria-hidden="true" />}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {revisions.length > 1 && (
        <div className="bp-cal-revs" role="group" aria-label={`${dateText(selDate)}的版本`}>
          <p className="bp-cal-revs-label">{dateText(selDate)} · 共 {revisions.length} 版</p>
          <div className="bp-cal-rev-list">
            {revisions.map((rev) => (
              <button
                key={rev.revision}
                type="button"
                className={`bp-cal-rev ${rev.revision === selRevision ? 'is-on' : ''}`}
                aria-pressed={rev.revision === selRevision}
                onClick={() => onPickRevision(rev.revision)}
              >
                {`第 ${rev.revision} 版`}
                <span className="bp-cal-aux">{[clockOf(rev.generated_at), STATUS_LABEL[rev.status]].filter(Boolean).join(' ')}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
