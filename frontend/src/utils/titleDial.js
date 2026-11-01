// 页头标题位转盘(issue #230 阶段 3 第 2 批;样页拍板 31 B「标题即切换」)的纯逻辑。
// 组件在 components/mobile/MobileHeader.jsx 的 TitleDial:各项固定槽位、左锚定,当前项以左沿为原点放大 1.3 倍;
// 选中只认「第几项」(--i / .is-on),不量像素。槽宽 = clamp(44px, (顶栏内容宽 − 预留) / 项数, 字数 × 1.352em + 20px),
// 预留 = 领位 44 + 右侧动作每枚 44(样页 DIAL_RESERVE:内容 176 · 订阅 88 · 榜单 44)。

/** 当前值落在第几项;找不到时落第一项(转盘总有一项选中)。 */
export function dialIndex(items, value) {
  const index = items.findIndex(([v]) => v === value);
  return index < 0 ? 0 : index;
}

/** tablist 的方向键(漫游 tabindex):← → 循环,Home / End 到两端;其它键返回 null(不拦)。 */
export function dialKeyTarget(key, index, count) {
  if (!(count > 0)) return null;
  switch (key) {
    case 'ArrowRight': return (index + 1) % count;
    case 'ArrowLeft': return (index - 1 + count) % count;
    case 'Home': return 0;
    case 'End': return count - 1;
    default: return null;
  }
}

/** 最长标签的字数(按码点数,「文章榜」= 3),槽宽随它放宽,放大后的当前项不被挤。 */
export function dialCharCount(items) {
  return Math.max(1, ...items.map(([, label]) => Array.from(String(label ?? '')).length));
}

/** 转盘之外占掉的宽度:领位 44 + 右侧动作每枚 44。 */
export function dialReserve(actionCount = 0) {
  return 44 + 44 * Math.max(0, actionCount);
}
