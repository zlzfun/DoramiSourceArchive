// 运行记录(样页 A):最近 10 次;列头即操作(开始 / 条数 / 用时排序,触发 / 结果轮换筛选);
// 逐流小条;失败行下贴一行错误(流 · 第 N 页 · 原文)。整行开运行抽屉。
import { Fragment, useMemo, useState } from 'react';
import { CircleAlert } from 'lucide-react';
import { ThFilter, ThSort } from '../TableTh';
import {
  fmt, fmtBytes, fmtDay, fmtDur, isScheduled, runErrorHead, runSecs, runTotals, toDate, trigLabel,
} from '../../../utils/syncCatalog';
import { ResultStamp, RunStrip } from './syncParts';

const RES_OPTS = [['', '结果'], ['ok', '成功'], ['bad', '失败']];
const TRIG_OPTS = [['', '触发'], ['system', '定时'], ['admin', '手动']];

export default function SyncRuns({ runs, order, now, onOpen }) {
  const [sort, setSort] = useState('start');
  const [dir, setDir] = useState('desc');
  const [res, setRes] = useState('');
  const [trig, setTrig] = useState('');
  const onSort = (k) => {
    if (k === sort) setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSort(k); setDir('desc'); }
  };
  const rows = useMemo(() => {
    let list = runs.slice();
    if (res) list = list.filter((r) => (res === 'bad' ? r.status === 'failed' : r.status !== 'failed'));
    if (trig) list = list.filter((r) => (trig === 'system' ? isScheduled(r.trigger) : !isScheduled(r.trigger)));
    const key = (r) => (sort === 'count' ? runTotals(r).count : sort === 'secs' ? (runSecs(r, now) ?? -1) : (toDate(r.startAt)?.getTime() ?? 0));
    list.sort((a, b) => { const x = key(a); const y = key(b); const c = x < y ? -1 : x > y ? 1 : 0; return dir === 'asc' ? c : -c; });
    return list;
  }, [runs, res, trig, sort, dir, now]);

  const n = (v, main = false) => <span className={`run-n${main ? ' is-main' : ''}${v ? '' : ' is-zero'}`}>{v ? fmt(v) : '–'}</span>;
  const filtered = Boolean(res || trig);

  return (
    <>
      <div className="zone-head"><span className="zone-title">运行记录</span></div>
      <section className="surface-card rounded-[var(--r-card)] overflow-hidden sync-runs">
        <div className="acct-scroll">
          <table className="acct-table is-fixed">
            <thead>
              <tr>
                <ThSort label="开始" k="start" sort={sort} order={dir} onSort={onSort} width={132} />
                <ThFilter label="触发" value={trig} onChange={setTrig} options={TRIG_OPTS} width={96} />
                <ThFilter label="结果" value={res} onChange={setRes} options={RES_OPTS} width={92} />
                <th className="acct-th" style={{ width: 150 }}>逐流</th>
                <ThSort label="条数" k="count" sort={sort} order={dir} onSort={onSort} num width={96} />
                <th className="acct-th is-num" style={{ width: 84 }}>新增</th>
                <th className="acct-th is-num" style={{ width: 84 }}>更新</th>
                <th className="acct-th is-num" style={{ width: 84 }}>删除</th>
                <th className="acct-th is-num" style={{ width: 96 }}>下载</th>
                <ThSort label="用时" k="secs" sort={sort} order={dir} onSort={onSort} num width={110} />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={10} className="acct-empty tiny-meta">
                    {filtered
                      ? <>没有匹配当前筛选的运行 · <button type="button" className="kpi-sub-link" onClick={() => { setRes(''); setTrig(''); }}>清除筛选</button></>
                      : '还没有同步记录,点右上角「立即同步」开始第一次'}
                  </td>
                </tr>
              )}
              {rows.map((r) => {
                const t = runTotals(r);
                const secs = runSecs(r, now);
                const failed = r.status === 'failed';
                return (
                  <Fragment key={r.id}>
                    <tr
                      className={`acct-row${failed ? ' has-err' : ''}`}
                      tabIndex={0}
                      onClick={() => onOpen(r.id)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(r.id); } }}
                    >
                      <td><span className="acct-mono">{fmtDay(r.startAt)}</span></td>
                      <td><span className={`run-trigger${isScheduled(r.trigger) ? ' is-sched' : ''}`}>{trigLabel(r.trigger)}</span></td>
                      <td><ResultStamp run={r} /></td>
                      <td><RunStrip run={r} order={r.order || order} /></td>
                      <td>{n(t.count, true)}</td>
                      <td>{n(t.inserted)}</td>
                      <td>{n(t.updated)}</td>
                      <td>{n(t.deleted)}</td>
                      <td><span className={`run-n${t.downloadedBytes ? '' : ' is-zero'}`}>{t.downloadedBytes ? fmtBytes(t.downloadedBytes) : '–'}</span></td>
                      <td><span className="run-dur">{secs === null ? '–' : r.active ? `${fmtDur(secs)} …` : fmtDur(secs)}</span></td>
                    </tr>
                    {failed && (
                      <tr className="err-row">
                        <td colSpan={10}>
                          <span className="err-msg">
                            <CircleAlert aria-hidden="true" />
                            <span><b>{runErrorHead(r)}</b>{r.error ? <> · <span className="sync-mono">{r.error}</span></> : null}</span>
                          </span>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
