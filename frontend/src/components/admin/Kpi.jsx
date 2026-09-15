import { Loader2 } from 'lucide-react';

// KPI 总账条单格(issue #76 归并稿 P2 #18:四份私有复制收敛为一份)。
// 被动读数,数字全 ink;tone 只给需要语义色的异常指标(is-warn / is-bad)。
// sub 可以是文本,也可以是带 .kpi-sub-link 的节点(KPI 下钻:点小字即筛)。
export function Kpi({ num, label, sub, tone }) {
  return (
    <div className="kpi">
      <span className={`kpi-num${tone ? ` ${tone}` : ''}`}>{num}</span>
      <span className="kpi-lbl">{label}</span>
      {sub != null && sub !== '' && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

// 加载中 / 失败态占位:与正常格同高同栏,失败给可重试入口(归并稿 P1 #1:
// 一条统计失败只让这一格变脸,不挡住同页其它分区)。
export function KpiState({ label, error, onRetry }) {
  if (error) {
    return (
      <div className="kpi is-error" role="alert">
        <span className="stamp stamp-bad">{label}加载失败</span>
        <span className="tiny-meta">
          {error}
          {onRetry && <> · <button type="button" className="kpi-sub-link" onClick={onRetry}>重试</button></>}
        </span>
      </div>
    );
  }
  return (
    <div className="kpi is-error" aria-busy="true">
      <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" aria-hidden="true" />
      <span className="tiny-meta">正在读取{label}…</span>
    </div>
  );
}
