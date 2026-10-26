import { useEffect, useState } from 'react';

// 正文栏的实测宽度与字号(issue #218):翻译骨架据此估算每块的行数(手机 / 桌面、窄宽窗口各自成立)。
// ref 指向正文容器(.reader-article-copy),挂载即量一次,之后随容器尺寸变化重量。
export function useCopyMetrics(ref) {
  const [metrics, setMetrics] = useState({ width: 640, font: 17 });
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      const width = el.clientWidth;
      const font = parseFloat(window.getComputedStyle(el).fontSize) || 17;
      if (!width) return;
      setMetrics((current) => (Math.abs(current.width - width) < 4 && current.font === font ? current : { width, font }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return metrics;
}
