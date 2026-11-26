import { useLayoutEffect, useRef, useState } from 'react';
import { clampCuts } from '../utils/socialClamp';

// 限高的盒子是否真的截掉了内容(issue #346):返回 [ref, cut],ref 挂到那只限高的盒子上。
//
// 只能量:挂载、回到限高态、内容换了各量一次(画出来之前就量完,按钮不会闪一下再消失),之后盒子尺寸变了再量。
// active 为假时(展开了,或这条根本不限高)不量也不清,留着上一次的结果——展开期间「收起」还得在。
// 盒子没排版时(卡片滚出视口后跳过排版、所在面板收起)高度读出来是 0,这不是结论:同样留着上一次的结果,
// 等它重新排出来、尺寸观察器报出真实尺寸时再量。
//
// 尺寸变了不在观察器回调里当场量,推到下一帧:回调里读高度会逼浏览器当场重排,正在滚进视口的其它卡片跟着排出来、
// 又产生一批新的尺寸变化,WebKit 报「ResizeObserver loop completed with undelivered notifications」。
export function useClampCut(active, content) {
  const ref = useRef(null);
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!active || !el) return undefined;
    const measure = () => {
      if (el.clientHeight > 0) setCut(clampCuts(el));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    });
    observer.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [active, content]);
  return [ref, cut];
}
