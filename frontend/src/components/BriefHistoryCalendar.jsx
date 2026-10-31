import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useLayer } from './layer/useLayer';
import Sheet from './layer/Sheet';
import { formatDateTime } from '../utils/datetime';
import {
  addMonths,
  calendarDayState,
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
// issue #230:层的行为走层原语 useLayer(kind='popover'):Esc 层栈、点外面关闭(触发钮除外)、
// 手机壳里入返回键层栈(系统返回先关月历、不离开页面,审计 M-05)。
// 手机档(批 3b,样页拍板 12「往期月历(定稿)」)是底页 BriefHistorySheet:同一套月历运算与键盘,日期只分有 / 没有两态
// (失败日按没有),单版的日子点了即关底页载入,多版的日子版次排在月历下方再选;没有图例与引导文字。
const WEEK_HEAD = ['一', '二', '三', '四', '五', '六', '日'];
const STATUS_LABEL = { degraded: '降级', failed: '失败', pending: '准备中', generating: '编排中' };
const dateText = (key) => `${Number(key.slice(5, 7))}月${Number(key.slice(8, 10))}日`;
const weekdayText = (key) => `周${WEEK_HEAD[weekdayIndex(key)]}`;
const clockOf = (iso) => (iso ? formatDateTime(iso).slice(11, 16) : '');
const focusTarget = (panel) => panel?.querySelector('.bp-cal-day[tabindex="0"]') || null;

// 月份与漫游焦点(桌面浮层与手机底页共用):范围夹在「最早一期所在月的 1 日 … 今天」。
// gridRef 由调用方持有、另传给 CalendarTable(返回值里不带 ref,渲染期读 nav 不算读 ref)
function useCalendarNav({ days, todayKey, selDate, gridRef }) {
  const byKey = useMemo(() => new Map(days.map((day) => [day.key, day])), [days]);
  const minKey = useMemo(() => {
    const earliest = days.reduce((min, day) => (day.key < min ? day.key : min), todayKey);
    return `${monthOf(earliest)}-01`;
  }, [days, todayKey]);
  const [focusKey, setFocusKey] = useState(() => clampKey(selDate, minKey, todayKey));
  const [month, setMonth] = useState(() => monthOf(clampKey(selDate, minKey, todayKey)));
  const moveFocusRef = useRef(false);

  // 键盘移动后把焦点落到新的那天(跨月时那颗按钮要等这次渲染才进 DOM)
  useEffect(() => {
    if (!moveFocusRef.current) return;
    moveFocusRef.current = false;
    gridRef.current?.querySelector(`[data-day="${focusKey}"]`)?.focus();
  }, [focusKey, month, gridRef]);

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
  const onDayFocus = (key) => { if (key !== focusKey) setFocusKey(key); };

  return {
    byKey,
    focusKey,
    month,
    goMonth,
    onGridKey,
    onDayFocus,
    canPrev: addMonths(month, -1) >= monthOf(minKey),
    canNext: month < monthOf(todayKey),
  };
}

// 翻月钮:到范围边界用 aria-disabled 而不是 disabled——正按着的按钮一旦 disabled 会丢焦点(落到 body),键盘用户就迷路了
// children 排在两钮之间(手机底页:‹ 2026 年 9 月 ›)
function MonthButtons({ nav, className, renderIcon = (Icon) => <Icon aria-hidden="true" />, children = null }) {
  return (
    <>
      <button type="button" className={className} aria-label="上个月" aria-disabled={nav.canPrev ? undefined : 'true'} onClick={() => { if (nav.canPrev) nav.goMonth(-1); }}>
        {renderIcon(ChevronLeft)}
      </button>
      {children}
      <button type="button" className={className} aria-label="下个月" aria-disabled={nav.canNext ? undefined : 'true'} onClick={() => { if (nav.canNext) nav.goMonth(1); }}>
        {renderIcon(ChevronRight)}
      </button>
    </>
  );
}

// 月历表格(role=grid):日格的有 / 没有 / 失败 / 编排中由 calendarDayState 判定;onDay(key, state) 只在「有」时调用
function CalendarTable({ nav, gridRef, todayKey, live, isOn, twoState, onDay }) {
  return (
    <table className="bp-cal-grid" role="grid" aria-label={monthLabel(nav.month)} ref={gridRef} onKeyDown={nav.onGridKey}>
      <thead>
        <tr>
          {WEEK_HEAD.map((char) => <th key={char} scope="col" abbr={`星期${char}`}>{char}</th>)}
        </tr>
      </thead>
      <tbody>
        {monthGrid(nav.month).map((week) => (
          <tr key={week.find(Boolean)}>
            {week.map((key, index) => {
              if (!key) return <td key={`pad-${index}`} />;
              const isToday = key === todayKey;
              const state = calendarDayState(nav.byKey.get(key), { isToday, todayLive: live, twoState });
              const on = isOn(key);
              const notes = [
                isToday ? '今天' : '',
                on ? '正在看' : '',
                !state.has ? '没有早报' : state.live ? '编排中' : state.failed ? '失败' : state.count > 1 ? `共 ${state.count} 版` : '',
              ].filter(Boolean);
              return (
                <td key={key} aria-selected={on || undefined}>
                  <button
                    type="button"
                    data-day={key}
                    tabIndex={key === nav.focusKey ? 0 : -1}
                    className={`bp-cal-day ${state.has ? 'has-ed' : ''} ${on ? 'is-on' : ''} ${state.failed ? 'is-bad' : ''}`}
                    aria-disabled={state.has ? undefined : 'true'}
                    aria-current={isToday ? 'date' : undefined}
                    aria-label={`${dateText(key)} ${weekdayText(key)}${notes.length ? `，${notes.join('，')}` : ''}`}
                    onClick={() => { if (state.has) onDay(key, state); }}
                    onFocus={() => nav.onDayFocus(key)}
                  >
                    {Number(key.slice(8, 10))}
                    {(isToday || state.live) && <i className={`bp-cal-dot ${state.live ? 'is-live' : ''}`} aria-hidden="true" />}
                  </button>
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// 某一天的版次(多版时排在月历下方);current = 正在看的那一版(不是正在看的这天时为 null)
function RevisionList({ dayKey, revisions, current, onPick }) {
  return (
    <div className="bp-cal-revs" role="group" aria-label={`${dateText(dayKey)}的版本`}>
      <p className="bp-cal-revs-label">{dateText(dayKey)} · 共 {revisions.length} 版</p>
      <div className="bp-cal-rev-list">
        {revisions.map((rev) => (
          <button
            key={rev.revision}
            type="button"
            className={`bp-cal-rev ${rev.revision === current ? 'is-on' : ''}`}
            aria-pressed={rev.revision === current}
            onClick={() => onPick(rev.revision)}
          >
            {`第 ${rev.revision} 版`}
            <span className="bp-cal-aux">{[clockOf(rev.generated_at), STATUS_LABEL[rev.status]].filter(Boolean).join(' ')}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

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
  const nav = useCalendarNav({ days, todayKey, selDate, gridRef });

  useLayer({
    open: true,
    onClose,
    kind: 'popover',
    panelRef,
    initialFocus: focusTarget,
    outsideIgnore: [triggerRef],
  });

  const handleBlur = (event) => {
    const next = event.relatedTarget;
    if (next && !panelRef.current?.contains(next) && !triggerRef.current?.contains(next)) onClose();
  };

  const revisions = nav.byKey.get(selDate)?.revisions || [];

  return (
    <div ref={panelRef} className="bp-pop bp-cal" role="dialog" aria-label="往期早报" tabIndex={-1} onBlur={handleBlur}>
      <p className="bp-cal-title">往期早报</p>
      <div className="bp-cal-head">
        <p className="bp-cal-month" aria-live="polite">{monthLabel(nav.month)}</p>
        <MonthButtons nav={nav} className="bp-cal-nav" />
      </div>
      <CalendarTable
        nav={nav}
        gridRef={gridRef}
        todayKey={todayKey}
        live={live}
        isOn={(key) => key === selDate}
        twoState={false}
        onDay={(key) => onPickDay(key)}
      />
      {revisions.length > 1 && (
        <RevisionList dayKey={selDate} revisions={revisions} current={selRevision} onPick={onPickRevision} />
      )}
    </div>
  );
}

// 手机档的往期底页(批 3b,样页拍板 12 / 33):层原语 Sheet——抓手真按钮「关闭往期早报」,点外面 / 下拖 / 系统返回 / Esc 都能关,
// 底页自己让出安全区(Sheet 的下内边距含 safe-area);日期只分有 / 没有两态(失败日按没有,与桌面不同),今天一个蓝点;
// 实底圆 = 正在看的那天;点多版的日子把实底圆移过去、版次排在月历下方再选;单版的日子点了即关底页载入。
// onPickEdition(dayKey, revision | null):revision 为 null 时载入那天的最新一版。
export function BriefHistorySheet({
  days,
  todayKey,
  selDate,
  selRevision,
  live,
  onPickEdition,
  onClose,
}) {
  const gridRef = useRef(null);
  const nav = useCalendarNav({ days, todayKey, selDate, gridRef });
  const [pick, setPick] = useState(selDate);
  const pickRevisions = nav.byKey.get(pick)?.revisions || [];

  const onDay = (key, state) => {
    if (state.count > 1) { setPick(key); return; }
    onPickEdition(key, null);
  };

  return (
    <Sheet
      open
      onClose={onClose}
      label="往期早报"
      grabLabel="关闭往期早报"
      className="m-hist-sheet"
      initialFocus={(panel, { keyboard }) => (keyboard ? focusTarget(panel) : panel)}
      head={(
        <div className="m-hist-head">
          <h2 className="m-hist-title">往期早报</h2>
          <div className="m-hist-nav">
            <MonthButtons
              nav={nav}
              className="m-gbtn m-hist-navbtn"
              renderIcon={(Icon) => <span className="m-gbtn-in"><Icon aria-hidden="true" /></span>}
            >
              <p className="m-hist-month" aria-live="polite">{monthLabel(nav.month)}</p>
            </MonthButtons>
          </div>
        </div>
      )}
    >
      <div className="m-hist-body">
        <CalendarTable
          nav={nav}
          gridRef={gridRef}
          todayKey={todayKey}
          live={live}
          isOn={(key) => key === pick}
          twoState
          onDay={onDay}
        />
        {pickRevisions.length > 1 && (
          <RevisionList
            dayKey={pick}
            revisions={pickRevisions}
            current={pick === selDate ? selRevision : null}
            onPick={(revision) => onPickEdition(pick, revision)}
          />
        )}
      </div>
    </Sheet>
  );
}
