// 节点同步控制台(issue #261)的小件:流图标、结果章、逐流小条、抽屉外壳。纯展示,数据推导在 utils/syncCatalog。
import { useRef } from 'react';
import {
  FileText, Flag, Headphones, Image, Layers, Rss, ScrollText, Sparkles, Tags, Undo2, X,
} from 'lucide-react';
import { useModalA11y } from '../../../hooks/useModalA11y';
import { EVIDENCE, STATE_LABEL, streamMeta, streamStateIn } from '../../../utils/syncCatalog';

const STREAM_ICONS = {
  rss: Rss, tags: Tags, 'file-text': FileText, sparkles: Sparkles, layers: Layers, image: Image,
  'scroll-text': ScrollText, headphones: Headphones, flag: Flag, 'undo-2': Undo2,
};

export function StreamIcon({ id, className = '' }) {
  const Icon = STREAM_ICONS[streamMeta(id)?.icon] || FileText;
  return <Icon className={className} aria-hidden="true" />;
}

// 运行结果章(运行记录 / 抽屉头 / 卡头)
export function ResultStamp({ run }) {
  if (!run) return null;
  if (run.status === 'queued') return <span className="stamp stamp-run">排队中</span>;
  if (run.status === 'running') return <span className="stamp stamp-run">运行中</span>;
  if (run.status === 'failed') return <span className="stamp stamp-bad">失败</span>;
  if (run.status === 'cancelled') return <span className="stamp stamp-idle">已取消</span>;
  if (run.status === 'checkpoint') return <span className="stamp stamp-idle is-hollow">各流进度</span>;
  return <span className="stamp stamp-ok">成功</span>;
}

// 单流状态章(逐流抽屉头 / 运行抽屉逐流表)
export function StateStamp({ k, short = false }) {
  if (k === 'done') return <span className="stamp stamp-ok">完成</span>;
  if (k === 'run') return <span className="stamp stamp-run">{short ? '进行' : '进行中'}</span>;
  if (k === 'bad') return <span className="stamp stamp-bad">失败</span>;
  if (k === 'skip') return <span className="stamp stamp-idle is-dashed">未协商</span>;
  return <span className="stamp stamp-idle is-hollow">{short ? '未跑' : '待跑'}</span>;
}

// 逐流小条:协商到的流各一格 + 间隔 + 候选证据回传一格
export function RunStrip({ run, order }) {
  if (run?.v1) return <span className="tiny-meta sync-mono">v1</span>;
  const cells = order.map((sid) => {
    const k = streamStateIn(run, sid, order);
    return <i key={sid} className={k === 'done' ? '' : `is-${k}`} title={`${streamMeta(sid)?.name || sid} · ${STATE_LABEL[k]}`} />;
  });
  const ek = streamStateIn(run, EVIDENCE.id);
  return (
    <span className="rs" aria-label="逐流结果">
      {cells}
      <i className="rs-gap" />
      <i className={ek === 'done' ? '' : `is-${ek}`} title={`${EVIDENCE.name}回传 · ${ek === 'done' ? '完成' : ek === 'bad' ? '失败' : '未跑'}`} />
    </span>
  );
}

// 抽屉外壳(台账抽屉范式):常驻 DOM 以播退场过渡;Esc / 焦点陷阱走 useModalA11y 层栈。
export function SyncDrawerShell({ open, onClose, icon, bad = false, title, sub = '', stamp = null, label, children, foot = null }) {
  const panelRef = useRef(null);
  useModalA11y(open, onClose, panelRef);
  return (
    <>
      <div className={`ledger-scrim ${open ? 'is-open' : ''}`} onClick={onClose} aria-hidden="true" />
      <aside
        ref={panelRef}
        className={`ledger-drawer sync-drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-hidden={!open}
        inert={!open}
        tabIndex={-1}
      >
        <div className="ledger-drawer-head">
          <span className={`sync-drawer-ic${bad ? ' is-bad' : ''}`}>{icon}</span>
          <span className="ledger-drawer-title">{title}{sub ? <small>{sub}</small> : null}</span>
          {stamp}
          <div className="drawer-acts">
            <span className="ai-divider" />
            <button type="button" className="rowact-btn" onClick={onClose} title="关闭" aria-label="关闭"><X /></button>
          </div>
        </div>
        <div className="ledger-drawer-body">{children}</div>
        {foot ? <div className="ledger-drawer-foot">{foot}</div> : null}
      </aside>
    </>
  );
}

// 抽屉统计格:0 / – 退成淡色
export function StatCell({ k, v }) {
  const zero = v === 0 || v === '0' || v === '–' || v === '—';
  return (
    <div>
      <small>{k}</small>
      <b className={zero ? 'is-zero' : ''}>{typeof v === 'number' ? v.toLocaleString('en-US') : v}</b>
    </div>
  );
}
