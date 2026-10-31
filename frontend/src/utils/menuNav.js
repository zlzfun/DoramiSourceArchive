// 菜单键位(issue #230 M-35,WAI-ARIA APG Menu):↑ ↓ 在菜单项之间走(两端回绕),Home / End 跳首尾;
// 禁用项照样能走到(APG:aria-disabled 的项可聚焦、不可激活),分隔线与组标签不是菜单项、不在序列里。
// current 为 -1(焦点在菜单容器上)时 ↓ 落首项、↑ 落末项。返回 null 表示这个键不归菜单管。
export function nextMenuIndex(count, current, key) {
  if (count <= 0) return null;
  switch (key) {
    case 'ArrowDown':
      return current < 0 ? 0 : (current + 1) % count;
    case 'ArrowUp':
      return current < 0 ? count - 1 : (current - 1 + count) % count;
    case 'Home':
      return 0;
    case 'End':
      return count - 1;
    default:
      return null;
  }
}
