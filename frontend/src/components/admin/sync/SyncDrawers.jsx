// 节点同步的三种抽屉(样页 A):逐流 / 运行 / 高危。高危动作一律过确认框(由 SyncPage 统一弹出)。
import { useRef, useState } from 'react';
import { Download, History, LoaderCircle, RefreshCw, RotateCcw, Sparkles, TriangleAlert, Upload } from 'lucide-react';
import { exportArchiveArticles, importArchiveArticlesJsonl } from '../../../api';
import {
  BINARY_STREAMS, EVIDENCE_ID, STREAMS, curStreamSecs, fmt, fmtBytes, fmtDay, fmtDur, fmtFull, fmtSnapshot, hostOf,
  inventoryCells, normStats, runErrorHead, runSecs, runTotals, shortId, streamDurations, streamMeta, streamName, streamStateIn, trigLabel,
} from '../../../utils/syncCatalog';
import { ResultStamp, StatCell, StateStamp, StreamIcon, SyncDrawerShell } from './syncParts';

const dash = '–';
const cellsOf = (y) => {
  const cells = [['页', y.pages], ['条数', y.count], ['新增', y.inserted], ['更新', y.updated], ['删除', y.deleted], ['清理', y.pruned], ['跳过', y.skipped], ['延后', y.deferred]];
  return cells;
};

/* ── 逐流:装了什么 · 本机条数 · checkpoint · 本次 / 上次统计 · 历次;脚部「重置本流」跳到高危抽屉预选 ── */
export function StreamDrawer({ open, onClose, sid, con, inventory, run, runs, now, onReset }) {
  const meta = streamMeta(sid) || STREAMS[0];
  const configured = Boolean(con?.configured);
  const order = con?.stream_order || null;
  const isEv = sid === EVIDENCE_ID;
  const k = configured ? streamStateIn(run, sid, order) : 'todo';
  const local = inventoryCells(sid, inventory);
  const cp = normStats(con?.checkpoints?.[sid]);
  const y = run?.streams?.[sid] || null;
  const notAdvanced = (k === 'bad' || k === 'todo') && run && (run.status === 'failed' || run.active);
  const reset = con?.resets?.[sid];
  const gen = con?.analysis_generation;

  let progress = null;
  if (k === 'run') {
    const bin = BINARY_STREAMS.has(sid);
    progress = (
      <section>
        <div className="drawer-sec-title">本次进度</div>
        <div className="sync-stats">
          <StatCell k="已处理" v={run.streamProcessed} />
          <StatCell k="分母" v={run.streamTotal === null ? dash : run.streamTotal} />
          {bin ? (
            <>
              <StatCell k="下载" v={`${fmt(run.downloaded)} · ${fmtBytes(run.downloadedBytes)}`} />
              <StatCell k="复用" v={`${fmt(run.reused)} · ${fmtBytes(run.reusedBytes)}`} />
            </>
          ) : (
            <>
              <StatCell k="页" v={run.page || dash} />
              <StatCell k="用时" v={fmtDur(curStreamSecs(run, now)) || dash} />
            </>
          )}
        </div>
      </section>
    );
  } else if (k === 'bad') {
    progress = (
      <section>
        <div className="drawer-sec-title">本次失败</div>
        <div className="sync-stats">
          <StatCell k="失败页" v={run.page || dash} />
          <StatCell k="已提交页" v={run.page ? run.page - 1 : dash} />
          <StatCell k="已处理" v={run.streamProcessed} />
          <StatCell k="用时" v={fmtDur(curStreamSecs(run, now)) || dash} />
        </div>
        {run.error && <pre className="sync-err">{run.error}</pre>}
      </section>
    );
  }

  const stats = y || (k !== 'run' ? cp : null);
  let statSec = null;
  if (k === 'skip') {
    statSec = (
      <section>
        <div className="drawer-sec-title">上次统计</div>
        <p className="tiny-meta">对端未广播 <span className="sync-mono">{meta.opt}</span>,本流不拉、不记 checkpoint</p>
      </section>
    );
  } else if (isEv) {
    const last = runs.find((r) => r.status === 'succeeded' && r.evidence);
    const e = last?.evidence;
    if (e) {
      statSec = (
        <section>
          <div className="drawer-sec-title">上次回传{last.startAt ? ` · ${fmtDay(last.startAt)}` : ''}</div>
          {e.status === 'success' ? (
            <div className="sync-stats">
              <StatCell k="页" v={Number(e.pages) || 0} />
              <StatCell k="新增" v={Number(e.inserted) || 0} />
              <StatCell k="跳过" v={Number(e.skipped) || 0} />
            </div>
          ) : <pre className="sync-err">{e.error || '回传失败'}</pre>}
        </section>
      );
    }
  } else if (stats) {
    const cells = cellsOf(stats);
    if (BINARY_STREAMS.has(sid)) cells.push(['下载', stats.downloaded], ['复用', stats.reused], ['下载字节', fmtBytes(stats.downloadedBytes)], ['复用字节', fmtBytes(stats.reusedBytes)]);
    const bl = stats.backlog;
    statSec = (
      <section>
        <div className="drawer-sec-title">{y ? '本次统计' : `上次统计${stats.completedAt ? ` · ${fmtDay(stats.completedAt)}` : ''}`}</div>
        <div className="sync-stats">{cells.map(([a, b]) => <StatCell key={a} k={a} v={b} />)}</div>
        {bl && (
          <div className="sync-stats">
            <StatCell k="补回成员" v={Number(bl.installed) || 0} />
            <StatCell k="仍等待" v={Number(bl.pending) || 0} />
            <StatCell k="过期" v={Number(bl.expired) || 0} />
            <StatCell k="被取代" v={Number(bl.superseded) || 0} />
          </div>
        )}
      </section>
    );
  }

  const hist = !isEv && runs.length > 0 && (
    <section>
      <div className="drawer-sec-title">历次</div>
      <table className="sync-mini">
        <thead>
          <tr><th style={{ width: 96 }}>开始</th><th className="is-l" style={{ width: 60 }}>结果</th><th>条数</th><th>新增</th><th>更新</th><th>删除</th></tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const kk = streamStateIn(r, sid, order);
            const z = r.streams[sid];
            const label = { done: '完成', run: '进行中', bad: '失败', todo: '未跑', skip: '未协商' }[kk];
            const c = (v) => <td className={v ? '' : 'is-zero'}>{v ? fmt(v) : dash}</td>;
            return (
              <tr key={r.id} className={kk === 'bad' ? 'is-bad' : kk === 'todo' || kk === 'skip' ? 'is-todo' : ''}>
                <td>{fmtDay(r.startAt)}</td>
                <td className="is-l">{label}</td>
                {z ? <>{c(z.count)}{c(z.inserted)}{c(z.updated)}{c(z.deleted)}</>
                  : kk === 'run' ? <><td>{fmt(r.streamProcessed)}</td><td colSpan={3} /></>
                    : <td colSpan={4} />}
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );

  const foot = !isEv && configured ? (
    <>
      <button type="button" className="rowact-btn is-danger" onClick={() => onReset(sid)} title="重置本流 checkpoint(高危,需确认)" aria-label="重置本流 checkpoint"><RotateCcw /></button>
      <span className="flex-1" />
      <span className="tiny-meta">{meta.opt ? <>能力 <span className="sync-mono">{meta.opt}</span></> : '必需流'}</span>
    </>
  ) : null;

  return (
    <SyncDrawerShell
      open={open}
      onClose={onClose}
      icon={<StreamIcon id={sid} />}
      bad={k === 'bad'}
      title={meta.name}
      sub={sid}
      stamp={<StateStamp k={k} />}
      label={`${meta.name} · 详情`}
      foot={foot}
    >
      <section>
        <div className="drawer-sec-title">装了什么</div>
        <div className="sync-what">{meta.items.map(([a, b]) => <div key={a}><b>{a}</b><span>{b}</span></div>)}</div>
      </section>
      {local.length > 0 && (
        <section>
          <div className="drawer-sec-title">本机</div>
          <div className="sync-stats">{local.map(([a, b]) => <StatCell key={a} k={a} v={b} />)}</div>
        </section>
      )}
      {configured && !isEv && k !== 'skip' && (
        <section>
          <div className="drawer-sec-title">checkpoint</div>
          <dl className="ledger-kv">
            <dt>authority</dt><dd className="sync-mono" title={cp?.authority || con?.authority_id || undefined}>{shortId(cp?.authority || con?.authority_id) || '—'}</dd>
            <dt>snapshot</dt><dd className="sync-mono">{cp?.snapshot ? fmtSnapshot(sid, cp.snapshot) : '—'}</dd>
            <dt>完成于</dt><dd>{cp?.completedAt ? fmtFull(cp.completedAt) : '—'}{notAdvanced ? ' · 本次未推进' : ''}</dd>
            {sid === 'analyses' && gen && (<><dt>字段代次</dt><dd>{gen.recorded ?? gen.current ?? '—'}{gen.current !== undefined && gen.recorded !== undefined && gen.recorded !== null && gen.recorded !== gen.current ? ` → ${gen.current}` : ''}</dd></>)}
            {reset?.at && (<><dt>上次重置</dt><dd>{fmtDay(reset.at)}{reset.by ? ` · ${reset.by}` : ''}</dd></>)}
          </dl>
        </section>
      )}
      {progress}
      {statSec}
      {hist}
    </SyncDrawerShell>
  );
}

/* ── 运行:概要 · 逐流表 · 错误原文 · 重试同步 ── */
export function RunDrawer({ open, onClose, run, order, now, onRetry, retryBusy = false }) {
  if (!run) return <SyncDrawerShell open={false} onClose={onClose} icon={<History />} title="" label="运行详情" />;
  const ord = run.order || order;
  const t = runTotals(run);
  const secs = runSecs(run, now);
  const durs = streamDurations(run);
  const failed = run.status === 'failed';
  const c = (v) => <td className={v ? '' : 'is-zero'}>{v ? fmt(v) : dash}</td>;
  const rows = [...ord, EVIDENCE_ID].map((sid) => {
    const k = streamStateIn(run, sid, ord);
    const z = run.streams[sid];
    let cells;
    if (sid === EVIDENCE_ID) {
      const e = run.evidence;
      cells = k === 'done'
        ? <>{c((Number(e.inserted) || 0) + (Number(e.skipped) || 0))}{c(Number(e.inserted) || 0)}<td className="is-zero">{dash}</td><td className="is-zero">{dash}</td><td /></>
        : k === 'bad' ? <td colSpan={5} className="is-l" title={e?.error || undefined}>回传失败</td> : <td colSpan={5} />;
    } else if (z) {
      cells = <>{c(z.count)}{c(z.inserted)}{c(z.updated)}{c(z.deleted)}<td>{durs[sid] === null || durs[sid] === undefined ? dash : fmtDur(durs[sid])}</td></>;
    } else if (k === 'run') {
      cells = <><td colSpan={4} className="is-l">{fmt(run.streamProcessed)}{run.streamTotal !== null ? ` / ${fmt(run.streamTotal)}` : ' 条'}</td><td>{fmtDur(curStreamSecs(run, now)) || dash}</td></>;
    } else if (k === 'bad') {
      cells = <><td colSpan={4} className="is-l">{run.page ? `第 ${run.page} 页 · ` : ''}已处理 {fmt(run.streamProcessed)}</td><td>{fmtDur(curStreamSecs(run, now)) || ''}</td></>;
    } else cells = <td colSpan={5} />;
    return (
      <tr key={sid} className={k === 'bad' ? 'is-bad' : k === 'todo' || k === 'skip' ? 'is-todo' : ''}>
        <td><span className="sync-mini-name">{streamName(sid)}</span></td>
        <td className="is-l"><StateStamp k={k} short /></td>
        {cells}
      </tr>
    );
  });
  const foot = (
    <>
      {failed && (
        <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={retryBusy} onClick={onRetry}>
          <RefreshCw />重试同步
        </button>
      )}
      <span className="flex-1" />
      {run.id && <span className="tiny-meta sync-mono">job {shortId(run.id)}</span>}
    </>
  );
  return (
    <SyncDrawerShell
      open={open}
      onClose={onClose}
      icon={<History />}
      bad={failed}
      title={`${fmtDay(run.startAt)} 的同步`}
      sub={`${trigLabel(run.trigger)} · ${run.active ? '进行中' : run.endAt ? `至 ${fmtDay(run.endAt).slice(6)}` : '—'}`}
      stamp={<ResultStamp run={run} />}
      label="运行详情"
      foot={foot}
    >
      <section>
        <div className="drawer-sec-title">概要</div>
        <div className="sync-stats">
          <StatCell k="条数" v={t.count} />
          <StatCell k="新增" v={t.inserted} />
          <StatCell k="更新" v={t.updated} />
          <StatCell k="删除" v={t.deleted} />
          <StatCell k="下载" v={t.downloadedBytes ? fmtBytes(t.downloadedBytes) : dash} />
          <StatCell k="复用" v={t.reusedBytes ? fmtBytes(t.reusedBytes) : dash} />
          <StatCell k="用时" v={secs === null ? dash : fmtDur(secs)} />
          <StatCell k="触发" v={trigLabel(run.trigger)} />
        </div>
      </section>
      {!run.v1 && (
        <section>
          <div className="drawer-sec-title">逐流</div>
          <table className="sync-mini">
            <thead>
              <tr>
                <th style={{ width: 84 }}>流</th><th className="is-l" style={{ width: 62 }}>结果</th>
                <th>条数</th><th>新增</th><th>更新</th><th>删除</th><th style={{ width: 78 }}>用时</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
      )}
      {failed && run.error && (
        <section>
          <div className="drawer-sec-title">错误 · {runErrorHead(run)}</div>
          <pre className="sync-err">{run.error}</pre>
        </section>
      )}
    </SyncDrawerShell>
  );
}

/* ── v1 文章归档(兼容):导出常可用;本机做过 v2 同步(consumer 模式)后导入禁用(后端会 409) ── */
function todayRange() {
  const now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const day = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
  return { start: `${day}T00:00`, end: `${day}T23:59` };
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
async function gzipText(text) {
  if (typeof CompressionStream === 'undefined') return null;
  const stream = new Blob([text], { type: 'application/x-ndjson; charset=utf-8' }).stream().pipeThrough(new CompressionStream('gzip'));
  return new Response(stream).blob();
}
async function readArchiveFile(file) {
  const isGzip = file.name.toLowerCase().endsWith('.gz') || file.type === 'application/gzip';
  if (!isGzip) return file.text();
  if (typeof DecompressionStream === 'undefined') throw new Error('当前浏览器不支持直接解压 .gz,请先解压为 .jsonl 后再导入');
  return new Response(file.stream().pipeThrough(new DecompressionStream('gzip'))).text();
}
const namePart = (v, fb) => (v || fb).replace(/[^0-9A-Za-z_-]+/g, '-').replace(/^-+|-+$/g, '') || fb;

function V1Section({ importLocked, showToast, onArticlesChanged }) {
  const [range, setRange] = useState(todayRange);
  const [busy, setBusy] = useState('');
  const fileRef = useRef(null);
  const doExport = async () => {
    setBusy('export');
    try {
      const text = await exportArchiveArticles({ fetched_date_start: range.start, fetched_date_end: range.end, source_ids: '', limit: 5000, skip: 0 });
      let count = 0;
      try { count = JSON.parse(text.split('\n').find(Boolean))?.count ?? 0; } catch { count = 0; }
      const base = `dorami-archive-${namePart(range.start, 'begin')}_${namePart(range.end, 'now')}`;
      const gz = await gzipText(text);
      if (gz) downloadBlob(gz, `${base}.jsonl.gz`);
      else downloadBlob(new Blob([text], { type: 'application/x-ndjson; charset=utf-8' }), `${base}.jsonl`);
      showToast(`已导出归档包 ${count} 篇`, 'success');
    } catch (e) {
      showToast(e.message || '导出归档包失败', 'error');
    } finally {
      setBusy('');
    }
  };
  const doImport = async (file) => {
    if (!file) return;
    setBusy('import');
    try {
      const r = await importArchiveArticlesJsonl(await readArchiveFile(file));
      onArticlesChanged?.();
      showToast(`已导入归档包:新增 ${r.imported_count} · 更新 ${r.updated_count} · 跳过 ${r.skipped_count}`, r.error_count ? 'info' : 'success');
    } catch (e) {
      showToast(e.message || '导入归档包失败', 'error');
    } finally {
      setBusy('');
      if (fileRef.current) fileRef.current.value = '';
    }
  };
  return (
    <section className="sync-danger">
      <div className="drawer-sec-title">v1 文章归档(兼容)</div>
      <div className="sync-two">
        <label><span className="form-label">收录起点</span><input className="form-input form-input-inline" type="datetime-local" value={range.start} onChange={(e) => setRange((r) => ({ ...r, start: e.target.value }))} aria-label="收录起点" /></label>
        <label><span className="form-label">收录终点</span><input className="form-input form-input-inline" type="datetime-local" value={range.end} onChange={(e) => setRange((r) => ({ ...r, end: e.target.value }))} aria-label="收录终点" /></label>
      </div>
      <div className="sync-danger-acts">
        <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={Boolean(busy)} onClick={doExport}>
          {busy === 'export' ? <LoaderCircle className="sync-spin" /> : <Download />}导出 .jsonl
        </button>
        <button
          type="button"
          className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
          disabled={importLocked || Boolean(busy)}
          title={importLocked ? '本机已做过 v2 同步,v1 导入会被拒绝' : undefined}
          onClick={() => fileRef.current?.click()}
        >
          {busy === 'import' ? <LoaderCircle className="sync-spin" /> : <Upload />}导入 .jsonl
        </button>
        <input ref={fileRef} type="file" className="sync-file" accept=".jsonl,.gz,.jsonl.gz,application/x-ndjson,application/gzip" onChange={(e) => doImport(e.target.files?.[0])} />
        <span className="flex-1" />
        {importLocked && <span className="tiny-meta">已 v2 同步 · 不可导入</span>}
      </div>
    </section>
  );
}

/* ── 高危:重置 checkpoint(九宫格选流)· 重放分析流 · v1 兼容导出 / 导入(更换远端不做,拍板 #4) ── */
export function DangerDrawer({ open, onClose, con, pick, setPick, locked = false, onAsk, showToast, onArticlesChanged }) {
  const configured = Boolean(con?.configured);
  const order = con?.stream_order || [];
  const gen = con?.analysis_generation;
  const replay = gen?.refetch_at ? { at: gen.refetch_at, by: gen.refetch_by } : con?.resets?.analyses;
  const lockTitle = locked ? '同步运行中,结束后再操作' : undefined;
  return (
    <SyncDrawerShell
      open={open}
      onClose={onClose}
      icon={<TriangleAlert />}
      bad
      title="高危操作"
      sub={configured ? `${hostOf(con.target)}${con.authority_id ? ` · ${shortId(con.authority_id)}` : ''}` : '未接入'}
      label="高危操作"
    >
      {configured && (
        <>
          <section className="sync-danger">
            <div className="drawer-sec-title">重置 checkpoint</div>
            <div className="sync-pick" role="group" aria-label="选择要重置的流">
              {order.map((sid) => {
                const cp = con.checkpoints?.[sid];
                return (
                  <button key={sid} type="button" aria-pressed={pick === sid} onClick={() => setPick(sid)}>
                    <b>{streamName(sid)}</b>
                    <small>{cp?.snapshot ? fmtSnapshot(sid, cp.snapshot) : '—'}</small>
                  </button>
                );
              })}
            </div>
            <div className="sync-danger-acts">
              <span className="tiny-meta">下次同步整流重拉「{streamName(pick)}」,已应用的修订跳过</span>
              <span className="flex-1" />
              <button type="button" className="action-button action-button-danger min-h-[32px] px-3 text-xs" disabled={locked || !order.includes(pick)} title={lockTitle} onClick={() => onAsk('reset', pick)}>
                <RotateCcw />重置所选流
              </button>
            </div>
          </section>
          <section className="sync-danger">
            <div className="drawer-sec-title">重放分析流</div>
            <dl className="ledger-kv">
              <dt>字段代次</dt><dd>{gen?.current ?? gen?.recorded ?? '—'}</dd>
              <dt>上次重放</dt><dd>{replay?.at ? `${fmtDay(replay.at)}${replay.by ? ` · ${replay.by}` : ''}` : '—'}</dd>
            </dl>
            <div className="sync-danger-acts">
              <span className="flex-1" />
              <button type="button" className="action-button action-button-danger min-h-[32px] px-3 text-xs" disabled={locked} title={lockTitle} onClick={() => onAsk('replay', 'analyses')}>
                <Sparkles />重放分析流
              </button>
            </div>
          </section>
        </>
      )}
      <V1Section importLocked={Boolean(con?.consumer_fence?.active)} showToast={showToast} onArticlesChanged={onArticlesChanged} />
    </SyncDrawerShell>
  );
}
