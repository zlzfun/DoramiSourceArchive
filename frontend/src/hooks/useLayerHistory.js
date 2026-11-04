import { useEffect, useRef } from 'react';
import { sharedLayerHistory } from '../utils/layerHistory';

// ── 移动层 × 浏览器返回键握手(移动波 Wave3;issue #230 起核心在 utils/layerHistory.js) ──
// 层开着时按返回 = 关最上层,不是退出站点;程序性关层消费掉自己那条历史。
// 新层优先走层原语(components/layer/useLayer,它在手机壳里自动接本 hook);
// 这里仍单独导出,给「同一层里的子页」用(设置栈的分区页 / 导入预览、发现页的合集详情)。
// 桌面不挂本 hook(层原语按 useCompactLayout 判定),零波及。
export function useLayerHistory(open, onClose) {
  const entryRef = useRef(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });

  useEffect(() => {
    const history = sharedLayerHistory();
    if (!history) return;
    if (open && !entryRef.current) {
      entryRef.current = history.open(() => closeRef.current?.());
    } else if (!open && entryRef.current) {
      const entry = entryRef.current;
      entryRef.current = null;
      history.close(entry); // 已由返回键弹栈的,核心里是空操作
    }
  }, [open]);

  // 版式切换会卸载多个层;合并回退一次,避免留下空的返回步骤。
  // 延迟到当前 commit 后,StrictMode 的清理/重挂载不应删除仍在使用的历史。
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const entry = entryRef.current;
      if (!entry) return;
      sharedLayerHistory()?.retireOnUnmount(entry, mountedRef);
    };
  }, []);
}
