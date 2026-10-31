import { useEffect, useRef } from 'react';
import { useModalA11y } from '../../hooks/useModalA11y';
import { useLayerHistory } from '../../hooks/useLayerHistory';
import { useCompactLayout } from '../../hooks/useCompactLayout';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { inertOthers } from '../../utils/inertOthers';
import { lastInputWasKeyboard } from '../../utils/inputModality';
import { INERT_KEEP_SELECTOR, LAYER_KINDS, initialFocusTarget } from '../../utils/layerKinds';

// ── 层原语(issue #230 阶段 3 第 1 批;conventions「移动端」§层)──
// 一个 hook 统一接管「层」的五件事,各种层只描述自己的样子:
//   ① 返回键握手(useLayerHistory):手机壳里打开即入层栈,系统返回先关最上层、不离站;桌面不挂。
//   ② Esc / 焦点(useModalA11y 层栈):Esc 只由最上层消费;打开移焦、关闭还焦;模态层困住 Tab。
//   ③ 下层 inert(utils/inertOthers):模态层以外整片 inert,读屏与 Tab 都进不去;关闭时先还原再还焦。
//   ④ 遮罩判定:遮罩元素用 LayerScrim / Modal 外壳,「按下与松开同在遮罩」才关(hooks/useOverlayClose)。
//   ⑤ 动效:全部走 CSS(进出场与拖拽回弹都受 <html data-motion="reduce"> 全局兜底压到 1ms)。
//
// 四种移动层 + Modal 外壳(modal / sheet / drawer / page / popover),各自的缺省见 utils/layerKinds.js。
//
// 出口(样页拍板 33):每层都要有一个不靠系统返回、也不靠拖动的出口——卡外能点即算(遮罩 / 点外面),
// 整屏推入的页面要有返回钮,底页的抓手是真按钮(SheetGrab)。
//
// 选项:
//   open / onClose        必填;onClose 是返回键、遮罩、点外面的出口
//   panelRef              焦点范围(面板元素,需 tabIndex={-1})
//   rootRef               inert 计算时保留的整层根(含遮罩),缺省 = panelRef
//   kind                  modal | sheet | drawer | page | popover(utils/layerKinds.js)
//   history               'compact'(缺省,只在手机壳里握手)| true | false(子页自己管历史时关掉)
//   onEscape              Esc 的去处,缺省 = onClose(设置栈等多级页传「退一级」)
//   initialFocus(panel, { keyboard })  返回要聚焦的元素;返回空则按 kind 的缺省
//   inert                 覆盖 kind 缺省:'all' | 'page' | false
//   trapFocus / scrollLock 覆盖 kind 缺省
//   outsideIgnore         popover:这些 ref 里的按下不算「外面」(触发钮自己切换开合)
export function useLayer({
  open,
  onClose,
  panelRef,
  rootRef = null,
  kind = 'modal',
  history = 'compact',
  onEscape = null,
  initialFocus = null,
  inert,
  trapFocus,
  scrollLock,
  outsideIgnore = null,
}) {
  const spec = LAYER_KINDS[kind] || LAYER_KINDS.modal;
  const compact = useCompactLayout();
  const historyOn = history === 'compact' ? compact : Boolean(history);
  const inertMode = inert === undefined ? spec.inert : inert;
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  const ignoreRef = useRef(outsideIgnore);
  useEffect(() => { ignoreRef.current = outsideIgnore; });

  // ① 返回键
  useLayerHistory(Boolean(open) && historyOn, onClose);

  // ③ 下层 inert —— 必须声明在 ② 之前:关闭时 React 按声明序跑清理,先还原 inert,再由 ② 还焦点
  // (焦点还给 inert 子树里的元素会静默失败)
  useEffect(() => {
    if (!open || !inertMode || typeof document === 'undefined') return undefined;
    const root = (rootRef || panelRef)?.current;
    if (!root) return undefined;
    const keep = [root, ...document.querySelectorAll(INERT_KEEP_SELECTOR[inertMode] || INERT_KEEP_SELECTOR.all)];
    return inertOthers(keep);
  }, [open, inertMode, rootRef, panelRef]);

  // ② Esc / 焦点
  const focusMode = spec.focus;
  useModalA11y(Boolean(open), onEscape || onClose, panelRef, {
    trapFocus: trapFocus === undefined ? spec.trap : trapFocus,
    initialFocus: (panel) => {
      const keyboard = lastInputWasKeyboard();
      const chosen = initialFocus ? initialFocus(panel, { keyboard }) : null;
      return initialFocusTarget(focusMode, { keyboard, chosen, panel });
    },
  });

  useBodyScrollLock(Boolean(open) && (scrollLock === undefined ? spec.scrollLock : scrollLock));

  // popover:点外面即关(mousedown,与全站浮层同约定;触屏是松手后合成的 mousedown,滚动不会触发)
  useEffect(() => {
    if (!open || !spec.outside || typeof document === 'undefined') return undefined;
    const onDown = (event) => {
      const panel = panelRef.current;
      if (panel && panel.contains(event.target)) return;
      if ((ignoreRef.current || []).some((ref) => ref?.current?.contains(event.target))) return;
      closeRef.current?.();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open, spec.outside, panelRef]);
}
