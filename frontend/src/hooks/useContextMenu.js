import { useCallback, useEffect, useRef, useState } from 'react';

// 右键上下文菜单的状态半边(v3.28,设计样页 docs/design/dorami-context-menu-quiet.html)。
// openMenu 先做「让位判定」——不满足才拦截,保住浏览器默认菜单该在的场合:
//   ① 事件源自输入域(拼写检查/粘贴等原生菜单项不可抢);
//   ② 卡内有**既有**文字选区(用户先拖选再右键,通常想复制/搜索/翻译)。
// 「既有」的判定不能在 contextmenu 时刻做:macOS 浏览器在右键 mousedown 的默认行为里
// 会自动选中光标下的单词,到 contextmenu 时选区已非空,与用户的主动拖选无法区分
// (实测:右键卡片极易被自动选词顶掉自绘菜单)。监听器先于默认行为执行,故在
// mousedown(button=2) 捕获阶段快照「右键前是否已有选区」;自动选词不算让位理由,
// 且在拦截成功后顺带清除——菜单开着时残留一个单词高亮是纯噪声。
// 通过判定才 preventDefault,并记录视口坐标快照。items 由调用方在 open 时闭包构建,
// 天然携带最新对象与状态,不需要 payload 二次分发;anchorKey 标记作用对象,
// 供列表行渲染 .is-ctx-anchor 锚定态(菜单存续期的 wash 淡底)。
// 单例:菜单开着时再次右键直接覆盖 state(换坐标与条目),不经关-开两帧。
export function useContextMenu() {
  const [menu, setMenu] = useState(null); // { x, y, items, anchorKey } | null
  const hadSelectionBeforeRightDownRef = useRef(false);

  useEffect(() => {
    const onRightDown = (e) => {
      if (e.button !== 2) return;
      const sel = window.getSelection?.();
      hadSelectionBeforeRightDownRef.current = !!(sel && !sel.isCollapsed);
    };
    document.addEventListener('mousedown', onRightDown, true);
    return () => document.removeEventListener('mousedown', onRightDown, true);
  }, []);

  const openMenu = useCallback((event, items, anchorKey = null) => {
    const target = event.target;
    if (target.closest?.('input, textarea') || target.isContentEditable) return;
    const sel = window.getSelection?.();
    const hasSelection = !!(sel && !sel.isCollapsed);
    if (hadSelectionBeforeRightDownRef.current && hasSelection && event.currentTarget.contains(sel.anchorNode)) return;
    if (!items?.length) return;
    event.preventDefault();
    // 仅清除右键顺手自动选中的单词(右键前无选区 → 现在的选区必是自动选词);
    // 用户先前的拖选绝不动——卡内的已在上面让位,卡外的与本菜单无关。
    if (!hadSelectionBeforeRightDownRef.current && hasSelection) sel.removeAllRanges();
    setMenu({ x: event.clientX, y: event.clientY, items, anchorKey });
  }, []);

  const closeMenu = useCallback(() => setMenu(null), []);

  return { menu, openMenu, closeMenu };
}
