import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchRunningProgress } from '../api';

const POLL_MS = 2500;
const IDLE_AFTER_EMPTY = 2; // 连续空结果 N 次后休眠,等下一次 kick / 窗口聚焦

// 全局运行进度(App 级):为跨页运行浮窗供数。
// 生命周期按需:平时休眠零请求;被 kick(任一页面发起运行 → onRunsChanged 通道)或
// 窗口重获焦点时唤醒轮询,跑空两次自动休眠——不给空闲会话加任何轮询负担。
// 实现为 effect 内闭包状态机:awake/empty/timer 全部活在 effect 作用域,
// 外部只拿到稳定的 kick 句柄,规避 render 期写 ref 的违规。
export function useRunningProgress(enabled) {
  const [progress, setProgress] = useState({});
  const machineRef = useRef(null);

  const kick = useCallback(() => { machineRef.current?.kick(); }, []);

  useEffect(() => {
    if (!enabled) { setProgress({}); return undefined; }
    let awake = false;
    let empty = 0;
    let timer = null;
    let disposed = false;

    const sleep = () => {
      awake = false;
      if (timer) { clearTimeout(timer); timer = null; }
    };

    const tick = async () => {
      let data;
      try { data = (await fetchRunningProgress()) || {}; } catch { data = {}; }
      if (disposed || !awake) return;
      setProgress(data);
      const active = Object.values(data).filter(p => p && p.status !== 'completed').length;
      if (active === 0) {
        empty += 1;
        if (empty >= IDLE_AFTER_EMPTY) { sleep(); setProgress({}); return; }
      } else {
        empty = 0;
      }
      timer = setTimeout(tick, POLL_MS);
    };

    const wake = () => {
      empty = 0;
      if (awake) return;
      awake = true;
      tick();
    };

    machineRef.current = { kick: wake };
    wake(); // 挂载/能力就绪时探一次,捕捉定时任务等外部发起的运行
    const onFocus = () => wake();
    window.addEventListener('focus', onFocus);
    return () => {
      disposed = true;
      sleep();
      machineRef.current = null;
      window.removeEventListener('focus', onFocus);
      setProgress({});
    };
  }, [enabled]);

  const runningIds = useMemo(() => new Set(
    Object.entries(progress)
      .filter(([, p]) => p && p.status !== 'completed')
      .map(([id]) => id),
  ), [progress]);

  return { progress, runningIds, kick };
}
