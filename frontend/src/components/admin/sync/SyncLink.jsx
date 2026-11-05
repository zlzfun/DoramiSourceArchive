// 连接与定时(样页 A):两栏只读 KV;定时的编辑入口是小图标(→ 定时弹层)。更换远端不做(拍板 #4),连接栏没有编辑入口。
import { Pencil } from 'lucide-react';
import { STREAM_ORDER, cronLabel, fmtDay, nextCronRun } from '../../../utils/syncCatalog';

export default function SyncLink({ con, sched, onSched }) {
  const configured = Boolean(con?.configured);
  const caps = Array.isArray(con?.capabilities) ? con.capabilities : [];
  const order = con?.stream_order || STREAM_ORDER;
  const user = con?.username || sched?.username || '';
  const savedPw = Boolean(sched?.password_set && (!sched?.base_url || sched.base_url === con?.target));
  const fence = con?.consumer_fence;
  const on = Boolean(sched?.enabled);
  const next = on ? nextCronRun(sched?.cron) : null;
  const dash = '—';
  return (
    <>
      <div className="zone-head"><span className="zone-title">连接与定时</span></div>
      <section className="surface-card sync-link">
        <section>
          <div className="sync-card-head"><span className="section-title">连接</span></div>
          <dl className="ledger-kv">
            <dt>远端地址</dt><dd className="sync-kv-mono" title={configured ? con.target : undefined}>{configured ? con.target : dash}</dd>
            <dt>同步账号</dt><dd>{configured && user ? `${user}${savedPw ? ' · 密码已保存' : ''}` : dash}</dd>
            <dt>协议</dt><dd>{configured ? `v2 · ${con.schema_version || '—'}` : dash}</dd>
            <dt>能力</dt><dd title={caps.join('\n') || undefined}>{configured && caps.length ? `${caps.length} 项 · ${order.length} / ${STREAM_ORDER.length} 流` : dash}</dd>
            <dt>authority</dt><dd className="sync-kv-mono" title={con?.authority_id || undefined}>{configured && con.authority_id ? con.authority_id : dash}</dd>
            <dt>接收端围栏</dt>
            <dd>{fence?.active ? [fence.activated_at ? `${fmtDay(fence.activated_at)} 起` : '', fence.reason].filter(Boolean).join(' · ') || '已启用' : '未启用'}</dd>
          </dl>
        </section>
        <section>
          <div className="sync-card-head">
            <span className="section-title">定时</span>
            <span className="flex-1" />
            <button type="button" className="rowact-btn" onClick={onSched} title="编辑定时" aria-label="编辑定时"><Pencil /></button>
          </div>
          <dl className="ledger-kv">
            <dt>状态</dt><dd>{sched?.migration_required ? '已停用 · 需选协议' : sched?.protocol_downgrade_blocked ? '已停用 · v1 不再运行' : on ? '开' : '关'}</dd>
            <dt>频率</dt><dd>{on ? cronLabel(sched.cron) : dash}</dd>
            <dt>cron</dt><dd className="sync-kv-mono">{on ? sched.cron : dash}</dd>
            <dt>下次</dt><dd>{next ? fmtDay(next) : dash}</dd>
            <dt>上次保存</dt><dd>{sched?.updated_at ? fmtDay(sched.updated_at) : dash}</dd>
          </dl>
        </section>
      </section>
    </>
  );
}
