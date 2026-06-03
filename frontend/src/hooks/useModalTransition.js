import { useEffect, useRef, useState } from 'react';

// 让 prop 受控的模态在关闭时先播退出动画再卸载。
// 用法：
//   const { mounted, closing } = useModalTransition(open);
//   if (!mounted) return null;
//   <div className={`modal-overlay ${closing ? 'is-closing' : ''}`}>…</div>
// open 由 true→false 时，组件保持挂载 duration 毫秒（播退出动画），随后卸载。
export function useModalTransition(open, duration = 320) {
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    if (open) {
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
      setMounted(true);
      setClosing(false);
    } else if (mounted) {
      setClosing(true);
      timerRef.current = setTimeout(() => {
        setMounted(false);
        setClosing(false);
        timerRef.current = null;
      }, duration);
    }
    return () => {
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    };
  }, [open, mounted, duration]);

  return { mounted, closing };
}
