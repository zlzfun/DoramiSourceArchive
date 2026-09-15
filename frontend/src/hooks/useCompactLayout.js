import { useSyncExternalStore } from 'react';

// 与阅读器四栏布局的最小宽度一致；监听断点变化，不在每次 resize 时更新 React。
const QUERY = '(max-width: 1024px)';

function subscribe(onChange) {
  const media = window.matchMedia(QUERY);
  media.addEventListener('change', onChange);
  return () => media.removeEventListener('change', onChange);
}

export function useCompactLayout() {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
