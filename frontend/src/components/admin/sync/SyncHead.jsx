// 节点同步页头(样页 A):左 = 面包屑 + 对端身份一行;右 = 测试连接 · ⋯ 高危 · 立即同步(本页唯一主按钮)+ 定时胶囊。零说明句。
import { ArrowDownToLine, CalendarClock, CircleDashed, Ellipsis, LoaderCircle, PlugZap, RefreshCw } from 'lucide-react';
import {
  STREAM_ORDER, fmtClock, hostOf, runStep, schedChip, schemaShort, shortId, withV,
} from '../../../utils/syncCatalog';

function PeerItem({ k, v, mono = false, title = '' }) {
  return (
    <span className="sync-peer-i" title={title || undefined}>
      <span className="sync-peer-k">{k}</span>
      <span className={`sync-peer-v${mono ? ' sync-mono' : ''}`}>{v}</span>
    </span>
  );
}

export default function SyncHead({
  crumb, con, sched, run, now, pop, starting = false,
  onProbe, onDanger, onSync, onSched, probePop = null, schedPop = null, credPop = null,
}) {
  const configured = Boolean(con?.configured);
  const order = con?.stream_order || STREAM_ORDER;
  const caps = Array.isArray(con?.capabilities) ? con.capabilities : [];
  const version = con?.probe?.version;
  const busy = Boolean(run?.active);
  const step = busy ? runStep(run) : null;
  const chip = schedChip(sched, now);

  return (
    <div className="page-head sync-head">
      <div className="sync-head-l">
        {crumb}
        <div className="sync-peer">
          {configured ? (
            <>
              <span className="sync-role"><ArrowDownToLine aria-hidden="true" />接收端</span>
              <PeerItem k="拉取自" v={hostOf(con.target)} mono title={con.target} />
              {con.authority_id && <PeerItem k="authority" v={shortId(con.authority_id)} mono title={con.authority_id} />}
              {version && <PeerItem k="远端" v={withV(version)} />}
              <PeerItem
                k="协议"
                v={`${schemaShort(con.schema_version) || 'v2'} · ${order.length} / ${STREAM_ORDER.length} 流`}
                title={caps.length ? `协商到的能力:\n${caps.join('\n')}` : ''}
              />
            </>
          ) : (
            <>
              <span className="sync-role is-none"><CircleDashed aria-hidden="true" />未接入</span>
              <PeerItem k="远端" v="—" />
            </>
          )}
        </div>
      </div>
      <div className="sync-head-r">
        <div className="sync-acts">
          {configured && (
            <span className="sync-anchor">
              <button
                type="button"
                className="icon-button signal-refresh"
                title="测试连接"
                aria-label="测试连接"
                aria-haspopup="dialog"
                aria-expanded={pop === 'probe'}
                onClick={onProbe}
              >
                <PlugZap />
              </button>
              {probePop}
            </span>
          )}
          <button
            type="button"
            className="icon-button signal-refresh"
            title="高危操作与兼容导入导出"
            aria-label="高危操作与兼容导入导出"
            onClick={onDanger}
          >
            <Ellipsis />
          </button>
          <span className="sync-anchor">
            <button
              type="button"
              className={`action-button action-button-primary min-h-[32px] px-3 text-xs${busy ? ' is-busy' : ''}`}
              disabled={!configured || busy || starting}
              title={configured ? undefined : '先在下方接入生产端'}
              aria-haspopup={configured && !busy ? 'dialog' : undefined}
              aria-expanded={configured && !busy ? pop === 'cred' : undefined}
              onClick={onSync}
            >
              {busy || starting ? <LoaderCircle className="sync-spin" /> : <RefreshCw />}
              {busy ? (step?.k ? `同步中 ${step.k} / ${step.n}` : '同步中…') : starting ? '启动中…' : '立即同步'}
            </button>
            {credPop}
          </span>
        </div>
        <span className="sync-anchor">
          <button
            type="button"
            className="model-chip sync-sched"
            aria-haspopup="dialog"
            aria-expanded={pop === 'sched'}
            onClick={onSched}
          >
            <i className={chip.on ? '' : 'is-off'} />
            <CalendarClock aria-hidden="true" />
            定时
            <b>{chip.label}</b>
            {chip.on && chip.next && <span className="tabular-nums">· 下次 {fmtClock(chip.next)}</span>}
          </button>
          {schedPop}
        </span>
      </div>
    </div>
  );
}
