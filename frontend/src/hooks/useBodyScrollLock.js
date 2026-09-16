import { useEffect } from 'react';

// body 滚动锁——全站共用一把、按引用计数(issue #104 迁弹窗上外壳时暴露):
// 多层浮层(抽屉 + 确认框、抽屉 + 表单弹窗)各自「存旧值 → 还旧值」时,只要关闭顺序与打开
// 顺序不对称(Esc 一次关两层、外层先卸载),后关的那层会把它记住的 'hidden' 写回 body,
// 浮层全没了页面还锁着。这里改成:第一把锁记下 body 原值并置 hidden,最后一把释放时还回,
// 中间任何顺序都不碰 body。释放函数幂等。
let depth = 0;
let savedOverflow = '';

export function lockBodyScroll() {
  if (depth === 0) {
    savedOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  depth += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    depth -= 1;
    if (depth === 0) document.body.style.overflow = savedOverflow;
  };
}

export function useBodyScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    return lockBodyScroll();
  }, [active]);
}
