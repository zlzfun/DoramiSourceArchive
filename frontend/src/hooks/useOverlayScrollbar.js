import { useCallback, useEffect, useRef } from 'react';

/* 浮层滚动条(Folo 式):隐藏原生条(scrollbar-width:none),用一个绝对定位的半透明
   滑块压在内容之上——这样内容可左右满宽铺开、右侧不再被原生条的 gutter 占位。
   纯 CSS 在 macOS「始终显示滚动条」下无法让原生条 overlay(Chrome 124+ 移除了
   overflow:overlay),故自绘一个轻量滑块:随滚动/尺寸变化同步高度与位置,支持拖拽,
   滚动/悬停时浮现、静止 ~900ms 后淡出。

   用法:给滚动元素与滑块元素各一个 ref,滑块是滚动元素的兄弟节点、其定位祖先要 relative。
   返回 sync():内容高度变化(如无限滚动追加)时由调用方主动重算一次。 */
export function useOverlayScrollbar(scrollRef, thumbRef) {
  const idleTimer = useRef(null);
  const dragRef = useRef(null);

  const sync = useCallback(() => {
    const el = scrollRef.current;
    const thumb = thumbRef.current;
    if (!el || !thumb) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    if (scrollHeight <= clientHeight + 1) {
      thumb.style.height = '0';
      thumb.classList.remove('is-visible');
      return;
    }
    const trackH = clientHeight;
    const h = Math.max(28, (clientHeight / scrollHeight) * trackH);
    const maxTop = trackH - h;
    const top = maxTop * (scrollTop / (scrollHeight - clientHeight));
    thumb.style.height = `${h}px`;
    thumb.style.transform = `translateY(${top}px)`;
  }, [scrollRef, thumbRef]);

  const flash = useCallback(() => {
    const thumb = thumbRef.current;
    const el = scrollRef.current;
    if (!thumb || !el || el.scrollHeight <= el.clientHeight + 1) return;
    thumb.classList.add('is-visible');
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => {
      if (!dragRef.current) thumb.classList.remove('is-visible');
    }, 900);
  }, [scrollRef, thumbRef]);

  useEffect(() => {
    const el = scrollRef.current;
    const thumb = thumbRef.current;
    if (!el || !thumb) return undefined;

    sync();

    const onScroll = () => { sync(); flash(); };
    el.addEventListener('scroll', onScroll, { passive: true });

    const ro = new ResizeObserver(() => sync());
    ro.observe(el);

    const onDown = (e) => {
      const { scrollHeight, clientHeight } = el;
      if (scrollHeight <= clientHeight + 1) return;
      dragRef.current = { startY: e.clientY, startTop: el.scrollTop };
      thumb.classList.add('is-visible', 'is-dragging');
      try { thumb.setPointerCapture(e.pointerId); } catch { /* noop */ }
      e.preventDefault();
    };
    const onMove = (e) => {
      const drag = dragRef.current;
      if (!drag) return;
      const { scrollHeight, clientHeight } = el;
      const trackH = clientHeight;
      const h = Math.max(28, (clientHeight / scrollHeight) * trackH);
      const maxTop = trackH - h;
      const dy = e.clientY - drag.startY;
      el.scrollTop = drag.startTop + (maxTop > 0 ? (dy / maxTop) * (scrollHeight - clientHeight) : 0);
    };
    const onUp = () => {
      if (dragRef.current) {
        dragRef.current = null;
        thumb.classList.remove('is-dragging');
        flash();
      }
    };
    thumb.addEventListener('pointerdown', onDown);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);

    return () => {
      el.removeEventListener('scroll', onScroll);
      ro.disconnect();
      thumb.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [scrollRef, thumbRef, sync, flash]);

  return sync;
}
