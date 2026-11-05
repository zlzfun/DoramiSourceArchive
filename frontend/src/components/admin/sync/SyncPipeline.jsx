// 流水线卡(样页 A):「本次 / 上次同步」卡头 + 9 条流 ‖ 回传的分组阶段线 + 当前流进度条 / 失败条 + 卡底「不同步」。
// 每站:状态标记 · 产品名 · 流 id · 关键数 · 增删改 · (待同步)· 装了什么 · 用时;整站可点开逐流抽屉。
import { Fragment } from 'react';
import { Check, CircleAlert, LoaderCircle, RefreshCw, Undo2 } from 'lucide-react';
import {
  BINARY_STREAMS, EVIDENCE, GROUPS, LOCAL_NOTE, LOCAL_ONLY, STREAMS,
  curStreamSecs, fmt, fmtBytes, fmtClock, fmtDay, fmtDur, fmtWhen, probePending, remainingOf, runSecs, runTotals,
  stationOf, streamDurations, streamName, streamPercent, trigLabel,
} from '../../../utils/syncCatalog';
import { ResultStamp } from './syncParts';

const idParts = (id) => id.split('_').map((part, i, all) => (
  <Fragment key={i}>{part}{i < all.length - 1 ? <>_<wbr /></> : null}</Fragment>
));

function Station({ s, col, view, last, selected, onOpen, pend, pendTitle }) {
  const k = view.k;
  const seg = k === 'done' ? '' : k === 'run' ? ' seg-run' : k === 'bad' ? ' seg-bad' : ' seg-todo';
  return (
    <button
      type="button"
      className={`pl-st is-${k}${seg}${last ? ' is-last' : ''}${selected ? ' is-sel' : ''}`}
      style={{ gridColumn: col }}
      onClick={() => onOpen(s.id)}
      aria-label={`${s.name} · 详情`}
    >
      <span className={`pl-mk is-${k}`}>{k === 'done' ? <Check aria-hidden="true" /> : null}</span>
      <span className="pl-name">{s.name}</span>
      <span className="pl-id">{idParts(s.id)}</span>
      <span className={`pl-num${view.tone ? ` is-${view.tone}` : ''}`}>{view.num}{view.small ? <small>{view.small}</small> : null}</span>
      <span className="pl-sub">
        {view.sub.map((w, i) => <Fragment key={i}>{i ? ' · ' : ''}<span>{w}</span></Fragment>)}
      </span>
      <span className={`pl-pend${pend === 0 ? ' is-zero' : ''}`} title={pend === null ? undefined : pendTitle}>
        {pend === null ? '' : `待同步 ${fmt(pend)}`}
      </span>
      <span className="pl-what">
        {s.what.map((w, i) => <Fragment key={w}>{i ? <span className="pl-what-sep"> · </span> : null}<span className="pl-what-i">{w}</span></Fragment>)}
      </span>
      <span className="pl-dur">{view.dur}</span>
    </button>
  );
}

function HeadLine({ run, configured, order, now, lastOkAt }) {
  if (!configured) {
    return (
      <div className="sync-card-head">
        <span className="card-title">首次同步</span>
        <span className="stamp stamp-idle is-hollow">待开始</span>
        <span className="sync-meta">{order.length} 流 · 首次整流全量,之后各流增量</span>
      </div>
    );
  }
  if (!run) {
    return (
      <div className="sync-card-head">
        <span className="card-title">上次同步</span>
        <span className="stamp stamp-idle is-hollow">尚未同步</span>
      </div>
    );
  }
  const t = runTotals(run);
  const secs = runSecs(run, now);
  const trig = run.fromCheckpoint ? '' : trigLabel(run.trigger);
  const meta = run.active
    ? [`${fmtClock(run.startAt)} 起`, trig, secs !== null ? `已用 ${fmtDur(secs)}` : ''].filter(Boolean).join(' · ')
    : [fmtDay(run.startAt || run.endAt), trig, secs !== null ? fmtDur(secs) : ''].filter(Boolean).join(' · ');
  let items;
  if (run.active) {
    const left = remainingOf(run);
    items = [['已处理', fmt(t.count)], ...(left !== null ? [['剩余', fmt(left)]] : []), ['下载', fmtBytes(t.downloadedBytes)], ['复用', fmtBytes(t.reusedBytes)]];
  } else if (run.status === 'failed') {
    items = [['已提交', fmt(t.count)], ['新增', fmt(t.inserted)], ['更新', fmt(t.updated)], ['上次成功', lastOkAt ? fmtWhen(lastOkAt, now) : '—']];
  } else {
    items = [['条数', fmt(t.count)], ['新增', fmt(t.inserted)], ['更新', fmt(t.updated)], ['删除', fmt(t.deleted)], ['下载', fmtBytes(t.downloadedBytes)]];
  }
  return (
    <div className="sync-card-head">
      <span className="card-title">{run.active ? '本次同步' : '上次同步'}</span>
      <ResultStamp run={run} />
      {meta && <span className="sync-meta">{meta}</span>}
      <span className="sync-sum">
        {items.map(([k, v]) => <span key={k} className="sync-sum-i">{k} <b>{v}</b></span>)}
      </span>
    </div>
  );
}

function Note({ run, now, onRetry, retryBusy }) {
  if (!run || run.fromCheckpoint) return null;
  if (run.active) {
    if (!run.cur) {
      return (
        <div className="pl-note is-run" role="status">
          <LoaderCircle className="sync-spin" aria-hidden="true" />
          <b>{run.status === 'queued' ? '排队中…' : '正在连接远端…'}</b>
        </div>
      );
    }
    const pct = streamPercent(run);
    const secs = curStreamSecs(run, now);
    return (
      <div className="pl-note is-run" role="status">
        <LoaderCircle className="sync-spin" aria-hidden="true" />
        <b>{streamName(run.cur)}</b><span className="sync-mono">{run.cur}</span><span className="sep" />
        <span>
          {pct !== null
            ? `${fmt(run.streamProcessed)} / ${fmt(run.streamTotal)} · ${pct}%`
            : `${fmt(run.streamProcessed)} 条${run.page ? ` · 第 ${run.page} 页` : ''}`}
        </span>
        {pct !== null && <span className="sync-indet" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>}
        {BINARY_STREAMS.has(run.cur) && (
          <>
            <span className="sep" />
            <span>下载 {fmt(run.downloaded)} 个 · {fmtBytes(run.downloadedBytes)}</span>
            <span>复用 {fmt(run.reused)} 个 · {fmtBytes(run.reusedBytes)}</span>
          </>
        )}
        {secs !== null && <span className="pl-note-end"><span>本流 {fmtDur(secs)}</span></span>}
      </div>
    );
  }
  if (run.status === 'failed') {
    return (
      <div className="pl-note is-bad" role="alert">
        <CircleAlert aria-hidden="true" />
        <b>{run.cur ? `${streamName(run.cur)}${run.page ? ` · 第 ${run.page} 页` : ''}` : '同步失败'}</b>
        {run.error && <span className="sync-mono">{run.error}</span>}
        <span className="pl-note-end">
          {run.cur && run.page > 1 && <span>前 {run.page - 1} 页已提交 · checkpoint 未推进</span>}
          {run.cur && !(run.page > 1) && <span>checkpoint 未推进</span>}
          <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={retryBusy} onClick={onRetry}>
            <RefreshCw aria-hidden="true" />重试同步
          </button>
        </span>
      </div>
    );
  }
  return null;
}

export default function SyncPipeline({ con, run, configured, now, selected = '', onOpen, onRetry, retryBusy = false, lastOkAt = '' }) {
  const order = con?.stream_order || [];
  const n = STREAMS.length;
  const durations = streamDurations(run);
  // 组头:相邻同组合并
  const groups = [];
  STREAMS.forEach((s, i) => {
    const g = groups.at(-1);
    if (g && g.g === s.group) g.span += 1;
    else groups.push({ g: s.group, start: i + 1, span: 1 });
  });
  const showPend = configured && !run?.active;
  const probedAt = con?.probe?.probed_at;
  const pendTitle = `截至测试连接时${probedAt ? ` · ${fmtDay(probedAt)}` : ''};修订晚于本机 checkpoint 的条目(含更新)`;
  const ctx = { configured, now, durations, order: order.length ? order : null };

  return (
    <section className={`surface-card card-pad${configured ? '' : ' sync-dimmed'}`}>
      <HeadLine run={run} configured={configured} order={order.length ? order : STREAMS} now={now} lastOkAt={lastOkAt} />
      <div className="pl" style={{ '--n': n }} role="group" aria-label="逐流状态">
        {groups.map((g) => (
          <span key={g.g + g.start} className="pl-grp" style={{ gridColumn: `${g.start} / span ${g.span}` }}>{GROUPS[g.g]}</span>
        ))}
        <span className="pl-grp" style={{ gridColumn: n + 2 }}><Undo2 aria-hidden="true" />{GROUPS.back}</span>
        <span className="pl-gap" style={{ gridColumn: n + 1 }} aria-hidden="true" />
        {STREAMS.map((s, i) => (
          <Station
            key={s.id}
            s={s}
            col={i + 1}
            view={stationOf(run, s.id, ctx)}
            last={i === n - 1}
            selected={selected === s.id}
            onOpen={onOpen}
            pend={showPend ? probePending(con, s.id) : null}
            pendTitle={pendTitle}
          />
        ))}
        <Station s={EVIDENCE} col={n + 2} view={stationOf(run, EVIDENCE.id, ctx)} last selected={selected === EVIDENCE.id} onOpen={onOpen} pend={null} />
      </div>
      {configured && <Note run={run} now={now} onRetry={onRetry} retryBusy={retryBusy} />}
      <div className="pl-local">
        <span className="pl-local-k">不同步</span>
        <span className="sync-chips">
          {LOCAL_ONLY.map((x) => <span key={x} className="sync-chip" title={LOCAL_NOTE[x] || '本机自有'}>{x}</span>)}
        </span>
      </div>
    </section>
  );
}
