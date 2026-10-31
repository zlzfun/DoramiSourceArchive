import { useSyncExternalStore } from 'react';
import { MEDIA } from '../utils/breakpoints';

// 手机壳 ⇄ 桌面四栏的总开关 = 命名断点 bp-shell(≤ 1024,utils/breakpoints.js;CSS 侧 `@variant bp-shell`)。
// 监听断点变化，不在每次 resize 时更新 React。
const QUERY = MEDIA['bp-shell'];

function subscribe(onChange) {
  const media = window.matchMedia(QUERY);
  media.addEventListener('change', onChange);
  return () => media.removeEventListener('change', onChange);
}

export function compactLayoutMatches() {
  return window.matchMedia(QUERY).matches;
}

export function useCompactLayout() {
  return useSyncExternalStore(
    subscribe,
    compactLayoutMatches,
    () => false,
  );
}
