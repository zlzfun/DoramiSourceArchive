// 默认字母头像:无自定义头像时显示用户名首字符(单个大写字母),
// 并按该字符稳定派生一个色相——同名恒同色,不同字母彼此易区分。
// 颜色本体在 CSS 的 .avatar-letter(读 --avatar-h,亮暗主题各自取浅底深字/深底浅字)。

export function avatarInitial(name) {
  const ch = (name || '').trim().charAt(0);
  return ch ? ch.toUpperCase() : '?';
}

// 黄金角散列:相邻码点(如 A/B)也能拉开色相差;对中文等非拉丁字符同样适用。
export function avatarHue(name) {
  const ch = (name || '').trim().charAt(0);
  if (!ch) return 243; // 空名兜底:品牌靛色相
  return Math.round((ch.toUpperCase().charCodeAt(0) * 137.508) % 360);
}
