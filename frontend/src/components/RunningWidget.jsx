import { RefreshCw, ChevronRight } from 'lucide-react';

// 「N 个节点正在抓取」浮窗：FetchTab 曾在选择条内嵌版与右下浮动版各写一份完全相同的
// 结构，此处收敛为单一组件。variant 决定外观（embedded=嵌在选择条 / floating=右下浮动）；
// floating 版在选择条出现时用 hidden 隐藏（避免与内嵌版重叠）。
export default function RunningWidget({ runningIds, fetchProgress, fetchersById, onViewRunning, variant = 'floating', hidden = false }) {
  const ids = Array.from(runningIds);
  const className = variant === 'embedded'
    ? 'running-widget running-widget-embedded'
    : `running-widget ${hidden ? 'running-widget-hidden' : ''}`;
  return (
    <button
      type="button"
      onClick={() => onViewRunning?.()}
      className={className}
      title="查看运行历史"
      aria-hidden={variant === 'floating' ? hidden : undefined}
    >
      <RefreshCw className="running-widget-icon animate-spin" />
      <div className="running-widget-body">
        <div className="running-widget-headline">
          <span>{runningIds.size} 个节点正在抓取</span>
          <ChevronRight className="running-widget-chevron" />
        </div>
        <div className="running-widget-list">
          {ids.slice(0, 4).map(id => {
            const p = fetchProgress[id];
            const name = fetchersById[id]?.name || id;
            const isQueued = !p;
            const progressText = isQueued ? '排队中' : (p.total ? `${p.current}/${p.total}` : `${p.current}`);
            return (
              <div key={id} className="running-widget-row">
                <span className="running-widget-name">{name}</span>
                <span className={`running-widget-progress ${isQueued ? 'running-widget-progress-queued' : ''}`}>{progressText}</span>
              </div>
            );
          })}
          {runningIds.size > 4 && <div className="running-widget-more">+{runningIds.size - 4} 个</div>}
        </div>
      </div>
    </button>
  );
}
