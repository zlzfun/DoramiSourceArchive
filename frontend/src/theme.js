import { useCallback, useEffect, useState } from 'react';

// 主题偏好：'light' | 'dark' | 'system'（默认跟随系统）。
// 真实生效主题（light/dark）由偏好 + 系统 prefers-color-scheme 解析得出，
// 写到 document.documentElement 的 data-theme（index.html 里已有同源的防闪烁脚本先行设置）。
const STORAGE_KEY = 'dorami-theme';
const MEDIA = '(prefers-color-scheme: dark)';

export function readThemePref() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved;
  } catch {
    /* localStorage 不可用时退回 system */
  }
  return 'system';
}

function systemPrefersDark() {
  return typeof window !== 'undefined' && window.matchMedia(MEDIA).matches;
}

export function resolveEffective(pref) {
  return pref === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : pref;
}

function applyEffective(effective) {
  document.documentElement.setAttribute('data-theme', effective);
}

export function useTheme() {
  const [theme, setThemeState] = useState(readThemePref);

  // 偏好变化 → 写 localStorage + 应用生效主题
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* 忽略写入失败 */
    }
    applyEffective(resolveEffective(theme));
  }, [theme]);

  // 「跟随系统」时实时响应系统外观切换
  useEffect(() => {
    if (theme !== 'system') return undefined;
    const mq = window.matchMedia(MEDIA);
    const onChange = () => applyEffective(resolveEffective('system'));
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [theme]);

  const setTheme = useCallback((next) => setThemeState(next), []);
  // 顶栏快捷：在 亮 ↔ 暗 之间切换（基于当前生效主题，结果落为显式 light/dark）
  // 生效主题当场写到 <html>(不等 effect):读者面的明暗交叉淡化(utils/readerMotion.themeFade)要在同一个同步回调里拿到新界面。
  // 上面那条 effect 随后会再写一次同样的值。
  const toggleTheme = useCallback(() => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyEffective(next);
    setThemeState(next);
  }, []);

  return { theme, setTheme, toggleTheme, effective: resolveEffective(theme) };
}
