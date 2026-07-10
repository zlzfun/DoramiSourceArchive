import { useEffect, useRef, useState } from 'react';
import { RefreshCw, ChevronRight } from 'lucide-react';

const AUTO_COLLAPSE_MS = 4000; // 出现/新增任务后先完整展示,随后缩为迷你脉搏
const HOVER_LEAVE_MS = 200;    // 离开缓冲,防边缘抖动

// 「N 个节点正在抓取」浮窗(App 级,全页常驻):
// 出现时展开完整任务列表,短暂延迟后缩小为仅表示运转状态的迷你件(旋转图标+计数),
// hover/键盘聚焦时展开详情;运行中任务数增加时重新展开提示一次。
// 点击(任一形态)跳转运行历史。embedded 变体保持原状(选择条内嵌场景)。
export default function RunningWidget({ runningIds, fetchProgress, fetchersById, onViewRunning, variant = 'floating', hidden = false }) {
  const ids = Array.from(runningIds);
  const [pinned, setPinned] = useState(true);
  const [hover, setHover] = useState(false);
  const leaveTimer = useRef(null);
  const prevCount = useRef(runningIds.size);

  // 新任务加入 → 重新展开提示一次
  useEffect(() => {
    if (runningIds.size > prevCount.current) setPinned(true);
    prevCount.current = runningIds.size;
  }, [runningIds.size]);

  // 展开态自动回收
  useEffect(() => {
    if (!pinned) return undefined;
    const t = setTimeout(() => setPinned(false), AUTO_COLLAPSE_MS);
    return () => clearTimeout(t);
  }, [pinned, runningIds.size]);

  useEffect(() => () => { if (leaveTimer.current) clearTimeout(leaveTimer.current); }, []);

  const enter = () => { if (leaveTimer.current) clearTimeout(leaveTimer.current); setHover(true); };
  const leave = () => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
    leaveTimer.current = setTimeout(() => setHover(false), HOVER_LEAVE_MS);
  };

  const detail = (
    <>
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
    </>
  );

  if (variant === 'embedded') {
    return (
      <button type="button" onClick={() => onViewRunning?.()} className="running-widget running-widget-embedded" title="查看运行历史">
        {detail}
      </button>
    );
  }

  const open = pinned || hover;
  return (
    <div
      className={`running-dock ${hidden ? 'running-widget-hidden' : ''}`}
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocus={enter}
      onBlur={leave}
    >
      <button
        type="button"
        onClick={() => onViewRunning?.()}
        className={`running-widget running-widget-docked ${open ? '' : 'dock-hidden'}`}
        title="查看运行历史"
        tabIndex={open ? 0 : -1}
        aria-hidden={!open}
      >
        {detail}
      </button>
      <button
        type="button"
        onClick={() => onViewRunning?.()}
        className={`running-mini ${open ? 'dock-hidden' : ''}`}
        title={`${runningIds.size} 个节点正在抓取 · 查看运行历史`}
        tabIndex={open ? -1 : 0}
        aria-hidden={open}
        aria-label={`${runningIds.size} 个节点正在抓取`}
      >
        <RefreshCw className="running-mini-icon animate-spin" />
        <span className="running-mini-n">{runningIds.size}</span>
      </button>
    </div>
  );
}
