// 命名断点(issue #230 移动规范,conventions「移动端」节):CSS 与 JS 共用一份口径。
//
// CSS 侧是 index.css 顶部的 `@custom-variant bp-*`(Tailwind v4 在开发与构建两种模式下都会展开;
// `@custom-media` 只在生产构建的 Lightning CSS 里展开、开发服务器原样输出,故不用),
// 样式里写 `@variant bp-phone { … }`,JSX 里写 `bp-phone:` 前缀;JS 侧用这里的 MEDIA。
// frontend/test/breakpoints.test.mjs 核对两边逐字一致,并拦 index.css / mobile.css 里新增的裸宽度 @media。
//
// 档位:
//   bp-phone-s   ≤ 480        小屏手机微调(SE 级)
//   bp-phone     ≤ 767        手机竖屏主档
//   bp-tablet    768–1024     平板竖屏 / 大屏手机横屏(仍是手机壳)
//   bp-shell     ≤ 1024       手机壳 ⇄ 桌面四栏的开关(useCompactLayout)
//   bp-desktop   ≥ 1025       桌面壳(bp-shell 的反面;导轨几何等)
//   bp-desktop-m ≤ 1280       桌面内部收窄
// 容器查询(早报 960 / 680 / 420、播放条 640 / 900)按容器宽排版,不属于本表。
export const BREAKPOINTS = Object.freeze({
  phoneS: 480,
  phone: 767,
  shell: 1024,
  desktopM: 1280,
});

export const MEDIA = Object.freeze({
  'bp-phone-s': `(max-width: ${BREAKPOINTS.phoneS}px)`,
  'bp-phone': `(max-width: ${BREAKPOINTS.phone}px)`,
  'bp-tablet': `(min-width: ${BREAKPOINTS.phone + 1}px) and (max-width: ${BREAKPOINTS.shell}px)`,
  'bp-shell': `(max-width: ${BREAKPOINTS.shell}px)`,
  'bp-desktop': `(min-width: ${BREAKPOINTS.shell + 1}px)`,
  'bp-desktop-m': `(max-width: ${BREAKPOINTS.desktopM}px)`,
});

export function mediaMatches(name) {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(MEDIA[name]).matches;
}
