import { useLayoutEffect } from 'react';
import { enterPop, exitGhost } from '../utils/readerMotion';

/**
 * 桌面读者面的浮层开合(issue #301 第二批,conventions §7):入场对准触发钮 0.96 → 1(150ms),离场 100ms 原路退。
 *
 *   usePopMotion(panelRef, { trigger: triggerRef });          // 往期日历、来源下拉、分享
 *   usePopMotion(panelRef, { origin: 'keep' });               // 右键菜单:原点沿用样式表里的光标位置
 *
 * scope 给了的话,面板不在这个祖先之下就不动(分享浮层手机壳也在用,那边不经过这里)。
 * 离场用替身:React 卸载面板之前克隆一份留在原位淡出,状态当场就切回(与 utils/layerExit 同一种做法)。
 * 布局副作用的清理在节点移出文档之前跑,所以拿得到;StrictMode 开发态会多跑一次清理,替身那边自己识别。
 */
export function usePopMotion(ref, { trigger = null, origin = null, scope = null } = {}) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || (scope && !el.closest(scope))) return undefined;
    const at = enterPop(el, origin === 'keep' ? 'keep' : (typeof trigger === 'function' ? trigger() : trigger?.current) || null);
    return () => exitGhost(el, at);
    // 只在挂载 / 卸载各跑一次:面板开着的时候触发钮不会换
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
