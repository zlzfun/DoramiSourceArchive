import { useEffect, useState } from 'react';

// 防抖值:输入停止变化 ms 后才更新返回值(搜索框 → 服务端过滤的标准间置)。
// 替代各 Tab 手写的「setTimeout 300ms + 清理」样板。
export function useDebouncedValue(value, ms = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}
