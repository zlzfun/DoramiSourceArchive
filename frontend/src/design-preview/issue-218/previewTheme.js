import { useCallback, useEffect, useState } from 'react';

// 样页自己的主题偏好:写独立键,不改现网的 dorami-theme;首次打开时只读现网键,沿用读者习惯的亮暗
export const PREVIEW_THEME_KEY = 'dorami-r2-preview:theme';
const MEDIA = '(prefers-color-scheme: dark)';
const PREFS = ['light', 'dark', 'system'];

function readPref() {
  try {
    const saved = localStorage.getItem(PREVIEW_THEME_KEY) || localStorage.getItem('dorami-theme');
    return PREFS.includes(saved) ? saved : 'system';
  } catch {
    return 'system';
  }
}

export function usePreviewTheme() {
  const [pref, setPrefState] = useState(readPref);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia(MEDIA).matches);

  useEffect(() => {
    const media = window.matchMedia(MEDIA);
    const onChange = () => setSystemDark(media.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  const effective = pref === 'system' ? (systemDark ? 'dark' : 'light') : pref;
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', effective);
  }, [effective]);

  const setPref = useCallback((next) => {
    setPrefState(next);
    try {
      localStorage.setItem(PREVIEW_THEME_KEY, next);
    } catch {
      /* 存储不可用:本页内仍生效 */
    }
  }, []);
  const toggle = useCallback(() => setPref(effective === 'dark' ? 'light' : 'dark'), [effective, setPref]);

  return { pref, effective, setPref, toggle };
}

// 减少动效:只改本页 <html data-motion>,不写现网 dorami-motion 键
export function useMotionAttr() {
  const [reduced, setReducedState] = useState(
    () => document.documentElement.getAttribute('data-motion') === 'reduce',
  );
  const setReduced = useCallback((next) => {
    setReducedState(next);
    if (next) document.documentElement.setAttribute('data-motion', 'reduce');
    else document.documentElement.removeAttribute('data-motion');
  }, []);
  return { reduced, setReduced };
}
