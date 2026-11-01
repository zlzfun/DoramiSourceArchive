import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useLayer } from '../layer/useLayer';
import { inertOthers } from '../../utils/inertOthers';
import { INERT_KEEP_SELECTOR } from '../../utils/layerKinds';
import { createSheetDrag } from '../../utils/sheetDrag';
import { PLAYER_CARD_DRAG, dockSwipeOpens } from '../../utils/playerCore';
import { PLAYER_MOTION } from '../../utils/playerSpring';

// 手机播放器的悬浮卡(issue #230 阶段 3 批 3d;样页 docs/design/issue-230-mobile 拍板 32 / 33 / 34,细节页 player-card B1)。
// 坞(一行 60px 胶囊)原地长成卡:卡浮在底栏之上 8px(正文页 / 推入页在安全区之上 8px),卡底 = 坞底。
//   card 状态:closed(只有坞)→ open(卡在上、坞藏起)→ closing(收回坞的动画)→ closed。
//   层的行为走层原语 useLayer(kind 'sheet'):返回键握手(进层栈)、Esc、焦点(缺省按 layerKinds:
//   键盘打开落首个控件,指针打开落卡本身)、困住 Tab;下层 inert 在这里自己算——列表页要留着底栏可点
//   (切 Tab 时卡一并收起、一次就切过去),useLayer 的 'all' / 'page' 两档都不留底栏。
//   出口(拍板 33):卡顶抓手(真按钮「收起播放器」)/ 点卡外(遮罩只收卡、不穿透)/ 整卡往下拖(进度条除外)/
//   系统返回 / Esc / 切底栏 Tab。收起 = 回坞、继续放;「停止」另是一枚钮(停播、坞收掉、提示条可撤销)。
//   形变只动 clip-path / transform / opacity(从坞的矩形长到卡的矩形,阻尼与现网 expand / settle 同参);
//   减少动效 = 120ms 淡入、收起直接落定;轻量档 = 淡入 + 12px 位移。

const TABBAR_SELECTOR = '.m-tabbar';

const eatNextClick = () => {
  const eat = (event) => { event.stopPropagation(); event.preventDefault(); };
  window.addEventListener('click', eat, { capture: true, once: true });
  window.setTimeout(() => window.removeEventListener('click', eat, { capture: true }), 350);
};

export function usePlayerCard({
  enabled,
  mobilePage,
  routeKey,
  api,
  showToast,
  onOpenItem,
  tier,
  easings,
  refs,
}) {
  const { layerRef, barRef, cardRef, clipRef, innerRef, grabRef, scrimRef } = refs;
  const [card, setCard] = useState('closed');
  // 坞不在(停止 / 关闭 / 没有条目):卡跟着收掉(渲染期按前值调整,不留一帧开着的卡)
  if (!enabled && card !== 'closed') setCard('closed');

  const stateRef = useRef(card);
  const optsRef = useRef({ from: 0, refocus: true });
  const fxRef = useRef([]);
  const timerRef = useRef(0);
  useLayoutEffect(() => { stateRef.current = card; });

  const open = useCallback(() => {
    if (!enabled) return;
    if (stateRef.current === 'open') return;
    setCard('open');
  }, [enabled]);

  // 收起:from = 拖到的位移(从那里收回);refocus = 收起后焦点回坞(切 Tab / 推入这一期时不抢焦点)
  const close = useCallback(({ from = 0, refocus = true, instant = false } = {}) => {
    if (stateRef.current !== 'open') return;
    optsRef.current = { from, refocus };
    stateRef.current = instant ? 'closed' : 'closing';
    setCard(instant ? 'closed' : 'closing');
  }, []);
  const closeFromLayer = useCallback(() => close(), [close]);   // 返回键 / Esc / 遮罩:不带参数

  const isOpen = enabled && card === 'open';
  useLayer({ open: isOpen, onClose: closeFromLayer, kind: 'sheet', panelRef: cardRef, rootRef: layerRef, inert: false });

  // 下层 inert:留播放器自身、卡外遮罩、Toast 等「永远可达」的,列表页另留底栏。
  // 声明在 useLayer 之后:关闭时它的清理晚于 useLayer 的还焦——焦点还给坞(在播放器里,从不 inert)
  useEffect(() => {
    if (!isOpen || typeof document === 'undefined') return undefined;
    const keep = [layerRef.current, scrimRef.current, ...document.querySelectorAll(INERT_KEEP_SELECTOR.all)];
    if (mobilePage === 'tabs') keep.push(...document.querySelectorAll(TABBAR_SELECTOR));
    return inertOthers(keep.filter(Boolean));
  }, [isOpen, mobilePage, layerRef, scrimRef]);

  // 收起后焦点回坞(useModalA11y 只还给打开时的焦点元素;触屏点开时那是 body)
  const prevOpenRef = useRef(false);
  useEffect(() => {
    const was = prevOpenRef.current;
    prevOpenRef.current = isOpen;
    if (!was || isOpen || !optsRef.current.refocus) return;
    const active = document.activeElement;
    if (active && active !== document.body && !cardRef.current?.contains(active)) return;
    barRef.current?.querySelector('.player-info')?.focus({ preventScroll: true });
  }, [isOpen, barRef, cardRef]);

  // 切底栏 Tab / 推页:卡一并收起(不抢焦点)
  const routeRef = useRef({ routeKey, mobilePage });
  useEffect(() => {
    const prev = routeRef.current;
    routeRef.current = { routeKey, mobilePage };
    if (prev.routeKey === routeKey && prev.mobilePage === mobilePage) return;
    close({ refocus: false });
  }, [routeKey, mobilePage, close]);

  // ── 开合形变 ──
  const cancelFx = () => {
    fxRef.current.forEach((a) => { try { a.cancel(); } catch { /* 已结束 */ } });
    fxRef.current = [];
    window.clearTimeout(timerRef.current);
  };
  const prevCardRef = useRef(card);
  useLayoutEffect(() => {
    const prev = prevCardRef.current;
    prevCardRef.current = card;
    if (prev === card) return;
    const el = cardRef.current;
    const clip = clipRef.current;
    const inner = innerRef.current;
    const grab = grabRef.current;
    if (card === 'closed') {
      cancelFx();
      if (el) { el.style.transform = ''; delete el.dataset.settled; }
      return;
    }
    if (!el || !clip || !inner || !grab) return;
    const run = (target, frames, options) => {
      const a = target.animate(frames, options);
      fxRef.current.push(a);
      return a;
    };
    const mode = tier();
    if (card === 'open') {
      cancelFx();
      el.style.transform = '';
      delete el.dataset.settled;
      // 卡太高(大字号 / 窄屏横屏)时卡内滚动:那时竖向手势让给滚动,下拖收起只认抓手
      el.toggleAttribute('data-scrolls', clip.scrollHeight > clip.clientHeight + 1);
      if (mode === 'reduce') {
        el.dataset.settled = '';
        run(el, [{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: 'ease-out' });
        return;
      }
      if (mode === 'lite') {
        run(el, [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], { duration: 240, easing: easings.fade });
        timerRef.current = window.setTimeout(() => { el.dataset.settled = ''; }, 240);
        return;
      }
      const H = clip.offsetHeight;
      const dh = barRef.current?.offsetHeight || 60;
      const R = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 24;
      run(clip, [
        { clipPath: `inset(${Math.max(0, H - dh)}px 0px 0px 0px round ${dh / 2}px)` },
        { clipPath: `inset(0px 0px 0px 0px round ${R}px)` },
      ], { duration: PLAYER_MOTION.expandDur, easing: easings.expand });
      run(inner, [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }],
        { duration: 380, delay: 80, easing: easings.fade, fill: 'backwards' });
      run(grab, [{ opacity: 0 }, { opacity: 1 }], { duration: 220, delay: 240, easing: 'ease-out', fill: 'backwards' });
      timerRef.current = window.setTimeout(() => { el.dataset.settled = ''; }, 480);
      return;
    }
    // closing
    cancelFx();
    delete el.dataset.settled;
    const done = () => { if (stateRef.current === 'closing') setCard('closed'); };
    const { from } = optsRef.current;
    if (mode === 'reduce') { done(); return; }
    if (mode === 'lite') {
      run(el, [{ opacity: 1, transform: `translateY(${from}px)` }, { opacity: 0, transform: `translateY(${from + 12}px)` }],
        { duration: 180, easing: easings.out, fill: 'forwards' }).finished.then(done, () => {});
      return;
    }
    const H = clip.offsetHeight;
    const dh = barRef.current?.offsetHeight || 60;
    const R = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 24;
    if (from) run(el, [{ transform: `translateY(${from}px)` }, { transform: 'none' }], { duration: 300, easing: easings.settle });
    run(inner, [{ opacity: 1 }, { opacity: 0 }], { duration: 120, easing: 'ease-in', fill: 'forwards' });
    run(grab, [{ opacity: 1 }, { opacity: 0 }], { duration: 100, easing: 'ease-in', fill: 'forwards' });
    run(clip, [
      { clipPath: `inset(0px 0px 0px 0px round ${R}px)` },
      { clipPath: `inset(${Math.max(0, H - dh)}px 0px 0px 0px round ${dh / 2}px)` },
    ], { duration: PLAYER_MOTION.settleDur, easing: easings.settle, fill: 'forwards' }).finished.then(done, () => {});
  }, [card]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    fxRef.current.forEach((a) => { try { a.cancel(); } catch { /* 已结束 */ } });
    window.clearTimeout(timerRef.current);
  }, []);

  // ── 停止 + 撤销:卡先落定收起(不播收回动画),坞下沉淡出;提示条带「撤销」 ──
  const stop = useCallback(() => {
    if (stateRef.current === 'open' || stateRef.current === 'closing') {
      flushSync(() => {
        optsRef.current = { from: 0, refocus: false };
        stateRef.current = 'closed';
        setCard('closed');
      });
    }
    const snapshot = api.stop?.();
    if (snapshot && showToast) {
      showToast('已停止播放', 'info', { action: { label: '撤销', onClick: () => api.resume?.(snapshot) } });
    }
  }, [api, showToast]);

  // 「这一期」:收起卡,推入这一期 / 这篇文章(播放不中断)
  const openItem = useCallback((item) => {
    close({ refocus: false });
    onOpenItem?.(item);
  }, [close, onOpenItem]);

  // ── 坞:点坞身展开(按钮 onClick);往上拖过 24px 松手也展开(播放钮除外;动过 4px 才算拖、拖起来才捕获) ──
  const swipeRef = useRef(null);
  const dockHandlers = {
    onPointerDown: (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      if (event.target.closest('.player-play')) return;
      swipeRef.current = { id: event.pointerId, y0: event.clientY, dy: 0, dragging: false };
    },
    onPointerMove: (event) => {
      const g = swipeRef.current;
      if (!g || g.id !== event.pointerId) return;
      g.dy = event.clientY - g.y0;
      if (g.dragging || Math.abs(g.dy) <= PLAYER_CARD_DRAG.slop) return;
      g.dragging = true;
      try { barRef.current?.setPointerCapture(event.pointerId); } catch { /* 指针已结束 */ }
    },
    onPointerUp: (event) => {
      const g = swipeRef.current;
      if (!g || g.id !== event.pointerId) return;
      swipeRef.current = null;
      if (!g.dragging) return;
      eatNextClick();
      if (dockSwipeOpens(g.dy)) open();
    },
    onPointerCancel: () => { swipeRef.current = null; },
  };

  // ── 卡:整卡往下拖收起(进度条除外);判定用层原语的下拖状态机,跟手 0.6、遮罩随之变淡 ──
  const [drag] = useState(() => createSheetDrag(PLAYER_CARD_DRAG));
  const capturedRef = useRef(null);
  const offsetRef = useRef(0);
  const resetDrag = () => {
    const el = cardRef.current;
    if (el) { el.style.transform = ''; el.style.transition = ''; }
    const scrim = scrimRef.current;
    if (scrim) { scrim.style.opacity = ''; scrim.style.transition = ''; }
  };
  const finishDrag = (event, cancelled) => {
    const result = cancelled ? drag.cancel() : drag.end(event.clientY, event.timeStamp);
    if (capturedRef.current != null) {
      try { cardRef.current?.releasePointerCapture(capturedRef.current); } catch { /* 已释放 */ }
      capturedRef.current = null;
    }
    const off = offsetRef.current;
    offsetRef.current = 0;
    resetDrag();
    if (!result.dragged) return;
    if (result.close) { close({ from: off }); return; }
    if (off > 0.5 && tier() !== 'reduce') {
      const el = cardRef.current;
      el?.animate([{ transform: `translateY(${off}px)` }, { transform: 'none' }], { duration: 320, easing: easings.settle });
    }
  };
  const cardHandlers = {
    onPointerDown: (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      if (stateRef.current !== 'open') return;
      if (event.target.closest('.player-track-in')) return;   // 进度条:拖的是进度
      const el = cardRef.current;
      if (el?.hasAttribute('data-scrolls') && !event.target.closest('.player-grab')) return;
      drag.start(event.clientY, event.timeStamp);
    },
    onPointerMove: (event) => {
      if (!drag.isActive()) return;
      const { dragging, offset } = drag.move(event.clientY, event.timeStamp);
      if (!dragging) return;
      const el = cardRef.current;
      if (!el) return;
      if (capturedRef.current == null) {
        // 拖起来才捕获:按下就捕获的话,Chromium 把轻点的 click 派给卡,抓手 / 钮收不到
        try { el.setPointerCapture(event.pointerId); capturedRef.current = event.pointerId; } catch { /* 不支持 */ }
        el.style.transition = 'none';
        if (scrimRef.current) scrimRef.current.style.transition = 'none';
      }
      const off = Math.round(offset * PLAYER_CARD_DRAG.follow * 10) / 10;
      offsetRef.current = off;
      el.style.transform = `translateY(${off}px)`;
      if (scrimRef.current) scrimRef.current.style.opacity = String(Math.max(0.2, 1 - offset / 260));
    },
    onPointerUp: (event) => finishDrag(event, false),
    onPointerCancel: (event) => finishDrag(event, true),
    // 拖过之后,松手合成的那次 click 不算点按(抓手 / 五枚钮 / 这一期都不触发)
    onClickCapture: (event) => {
      if (drag.consumeClick()) { event.stopPropagation(); event.preventDefault(); }
    },
    onDragStart: (event) => event.preventDefault(),
  };

  return { card: enabled ? card : 'closed', open, close, stop, openItem, dockHandlers, cardHandlers };
}
