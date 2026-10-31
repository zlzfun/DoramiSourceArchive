import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, RotateCcw, RotateCw, X } from 'lucide-react';
import { usePlayer } from './playerContext';
import PodcastCover from '../PodcastCover';
import LayerScrim from '../layer/LayerScrim';
import { PlayGlyph, PauseGlyph, SkipGlyph, Spark, StopGlyph } from './PlayerGlyphs';
import { usePlayerCard } from './usePlayerCard';
import {
  createFx,
  expandMorph,
  liteEnter,
  measureOrigin,
  miniMorph,
  openMorph,
  reducedEnter,
  snapshotBar,
  snapshotMini,
  snapshotSwap,
  swapMorph,
} from './playerFx';
import {
  decideFxTier,
  fabOffset,
  formatClock,
  formatRate,
  framesSayLite,
  isPlayingIntent,
  mobileForm,
  playerItemTitle,
  playerOpenLabel,
  playerTrackLabel,
  playerTrackSpeech,
  rateSpeech,
  speakClock,
} from '../../utils/playerCore';
import { useTitlePreference } from '../../hooks/useTitlePreference';
import { PLAYER_MOTION, playerEasings } from '../../utils/playerSpring';
import { loadCoverTone, resolveVarColor, veilAlphaFor } from '../../utils/coverTone';
import { mediaProxyUrl } from '../../api';
import { motionReduced } from '../../motion';

// 播放条(issue #199,样页 global.html?layout=float&tone=blur):桌面是内容区浮层——视图轨右缘到窗口右缘、
// 四周留 12px 的浮层卡,横跨源栏 / 条目列 / 阅读窗;手机列表页在底部 Tab 栏之上,正文页在安全区之上。
// 形态 none(只有标题区入口)→ bar ⇄ mini;关闭回到 none。形变见 playerFx.js。
// 手机壳(issue #230 批 3d,样页 docs/design/issue-230-mobile 拍板 32 / 33 / 34):bar 画成一行 60px 的坞
// (封面进度环 · 标题 / 副行 · 一枚播放钮),点坞身或往上拖展开成悬浮卡(usePlayerCard);手机退役迷你
// (mini 在手机上按 bar 画,桌面照旧);桌面播放条本波不动。

const SCROLLERS = [
  '.reader-pane', '.reader-list-scroll', '.reader-source-scroll', '.reader-social-scroll', '.reader-disc-scroll',
  '.rankings-page', '.bp-sheet', '.m-list', '.m-read-scroll', '.m-page-scroll', '.m-subs-scroll',
].join(', ');
const FX_OVERRIDE_KEY = 'dorami-player-fx';
const FX_LITE_KEY = 'dorami-player-lite';
const FX_CHECKED_KEY = 'dorami-player-fx-checked';
const FX_RENDERER_KEY = 'dorami-player-renderer';

const session = {
  get(key) { try { return window.sessionStorage.getItem(key) || ''; } catch { return ''; } },
  set(key, value) { try { window.sessionStorage.setItem(key, value); } catch { /* 隐私模式:本会话不记 */ } },
};

function probeRenderer() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl');
    if (!gl) return 'none';
    let renderer = String(gl.getParameter(gl.RENDERER) || '');
    if (!renderer || /^webkit webgl$/i.test(renderer)) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (ext) renderer = String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || renderer);
    }
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return renderer;
  } catch {
    return '';
  }
}

/** 'reduce' | 'lite' | 'full'。设置里的「减少动效」优先;否则看会话覆盖 / 首次形变录到的掉帧 / 软件光栅器。 */
function fxTier() {
  if (motionReduced()) return 'reduce';
  return decideFxTier({
    override: session.get(FX_OVERRIDE_KEY),
    lite: session.get(FX_LITE_KEY) === '1',
    renderer: session.get(FX_RENDERER_KEY),
  });
}

/** 手机列表页:底栏顶边(按布局位置,不算它自己的 transform)到视口底的距离;坞与卡浮在它之上 --m-float。 */
function tabbarLift(host, layer) {
  const tabbar = host.querySelector('.m-tabbar');
  if (!tabbar || !tabbar.getClientRects().length) return null;
  const r = tabbar.getBoundingClientRect();
  let ty = 0;
  try {
    const t = getComputedStyle(tabbar).transform;
    if (t && t !== 'none') ty = new DOMMatrixReadOnly(t).m42;
  } catch { /* 不认的 transform:按当前位置量 */ }
  const floor = layer.getBoundingClientRect().bottom;
  return Math.max(0, Math.round(floor - (r.top - ty)));
}

/** 浮钮的静止矩形(没有避让位移时):它在 .reader-shell 里右 22 / 下 22 绝对定位。
 *  不读浮钮当前的矩形——避让动画进行中读到的是半路的位置,累加起来会越让越远。 */
function hostFabRect(host) {
  const shell = host.querySelector('.reader-shell');
  if (!shell) return null;
  const s = shell.getBoundingClientRect();
  const fab = host.querySelector('.reader-ai-fab');
  const width = fab?.offsetWidth || 42;
  const height = fab?.offsetHeight || 40;
  return { left: s.right - 22 - width, right: s.right - 22, top: s.bottom - 22 - height, bottom: s.bottom - 22, width, height };
}

export default function PlayerDock({ mobile = false, hostRef, mobilePage = 'tabs', onOpenItem, routeKey = '', showToast = null }) {
  const { state, api } = usePlayer();
  const { item, status, buffering, rate } = state;
  const form = mobile ? mobileForm(state.form) : state.form;   // 手机退役迷你:mini 按坞画
  const playing = isPlayingIntent(state);
  const itemKey = item?.key || '';
  // 标题随「默认显示中文标题」(issue #218):条目带中文译名且偏好开时显示译名
  const { preferChinese } = useTitlePreference();
  const shownTitle = playerItemTitle(item, preferChinese);

  const layerRef = useRef(null);
  const dockRef = useRef(null);
  const underRef = useRef(null);
  const overRef = useRef(null);
  const barRef = useRef(null);
  const miniRef = useRef(null);
  const seekRef = useRef(null);
  const ringRef = useRef(null);
  const curRef = useRef(null);
  const durRef = useRef(null);
  const sliderRef = useRef(null);
  const clockRef = useRef(null);      // 手机坞副行的「已播 / 总长」
  const cardRef = useRef(null);       // 手机悬浮卡(外层:影子与抓手)
  const clipRef = useRef(null);       // 悬浮卡裁切层(开合的 clip-path 形变)
  const cardInRef = useRef(null);
  const grabRef = useRef(null);
  const scrimRef = useRef(null);
  const fxRef = useRef(null);
  const pendingRef = useRef(null);
  const prevRef = useRef({ form, key: itemKey });
  const formRef = useRef(form);
  const exitRef = useRef(null);
  const paintRef = useRef(() => {});
  const pointerRef = useRef({ x: -1, y: -1 });
  const fabAnimRef = useRef(null);
  const easings = useMemo(() => playerEasings(), []);
  const [themeKey, setThemeKey] = useState(0);
  const [tone, setTone] = useState(null);        // { src, blurUrl, veil }
  const [oldTones, setOldTones] = useState([]);  // 换台时冻结的旧底,只做 opacity 淡出
  const toneRef = useRef(null);

  useLayoutEffect(() => { formRef.current = form; });

  // ── 轻量档:软件光栅器判定放到空闲时做一次(本会话记住) ──
  useEffect(() => {
    if (session.get(FX_RENDERER_KEY)) return undefined;
    const run = () => session.set(FX_RENDERER_KEY, probeRenderer() || 'unknown');
    if (typeof window.requestIdleCallback === 'function') {
      const id = window.requestIdleCallback(run, { timeout: 4000 });
      return () => window.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(run, 1500);
    return () => window.clearTimeout(id);
  }, []);

  useEffect(() => {
    const onMove = (event) => { pointerRef.current = { x: event.clientX, y: event.clientY }; };
    window.addEventListener('pointermove', onMove, { passive: true, capture: true });
    window.addEventListener('pointerdown', onMove, { passive: true, capture: true });
    return () => {
      window.removeEventListener('pointermove', onMove, { capture: true });
      window.removeEventListener('pointerdown', onMove, { capture: true });
    };
  }, []);

  // 主题 / 中性色切换:遮罩浓度要按新的表面色重算
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setThemeKey((v) => v + 1));
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme', 'data-palette'] });
    return () => observer.disconnect();
  }, []);

  const finishFx = useCallback(() => {
    const fx = fxRef.current;
    if (!fx) return;
    fxRef.current = null;
    fx.finish();
    fx.cleanup();
    delete layerRef.current?.dataset.assembling;
  }, []);

  // ── 让位:各滚动区底部留出条的位置;收回时把「滚到底被推一截」的栏变成跟手的下滑 ──
  const applyClearance = useCallback((nextForm) => {
    const host = hostRef?.current;
    if (!host) return;
    const before = host.getAttribute('data-player') || '';
    const next = nextForm === 'none' ? '' : nextForm;
    if (before === next) return;
    const shrinking = (before === 'bar' && next !== 'bar') || (before === 'mini' && next === '');
    const scrollers = shrinking
      ? [...host.querySelectorAll(SCROLLERS)].filter((el) => el.offsetParent && el.scrollHeight > el.clientHeight)
      : [];
    const tops = scrollers.map((el) => el.scrollTop);
    if (next) host.setAttribute('data-player', next); else host.removeAttribute('data-player');
    if (!shrinking || motionReduced()) return;
    scrollers.forEach((el, i) => {
      const d = tops[i] - el.scrollTop;
      if (d < 0.5) return;
      [...el.children].slice(0, 60).forEach((child) => {
        child.animate([{ transform: `translateY(${-d}px)` }, { transform: 'none' }], { duration: 300, easing: easings.settle });
      });
    });
  }, [easings, hostRef]);

  // ── 问哆啦美浮钮 / 面板避让、条高、Toast 抬升(条与迷你的实际矩形算) ──
  const syncChrome = useCallback(({ animateFab = false, prevForm = null } = {}) => {
    const host = hostRef?.current;
    const layer = layerRef.current;
    if (!host) return;
    const f = formRef.current;
    const bar = barRef.current;
    const mini = miniRef.current;
    const barRect = f === 'bar' && bar && !bar.hidden ? bar.getBoundingClientRect() : null;
    const miniRect = f === 'mini' && mini && !mini.hidden ? mini.getBoundingClientRect() : null;
    if (barRect) host.style.setProperty('--player-bar-h', `${Math.round(barRect.height)}px`);
    if (layer && mobile) {
      const lift = tabbarLift(host, layer);
      if (lift !== null) layer.style.setProperty('--player-tabbar', `${lift}px`);
    }
    // Toast 浮在条之上(桌面的迷你在右下角,与居中的 Toast 不重叠);手机悬浮卡开着时浮在卡之上
    const card = mobile && cardRef.current && !cardRef.current.hidden ? cardRef.current.getBoundingClientRect() : null;
    const top = card || barRect || (mobile ? miniRect : null);
    document.documentElement.style.setProperty('--player-top', top ? `${Math.round(window.innerHeight - top.top)}px` : '0px');
    if (mobile) return;
    const fabStatic = hostFabRect(host);
    const shell = host.querySelector('.reader-shell')?.getBoundingClientRect();
    const target = fabOffset(f, fabStatic, barRect, miniRect);
    const prev = {
      x: parseFloat(host.style.getPropertyValue('--player-fab-x')) || 0,
      y: parseFloat(host.style.getPropertyValue('--player-fab-y')) || 0,
    };
    host.style.setProperty('--player-fab-x', `${target.x}px`);
    host.style.setProperty('--player-fab-y', `${target.y}px`);
    const lifted = barRect || miniRect;
    const lift = lifted && shell ? Math.max(0, Math.round(shell.bottom - lifted.top + 12 - 22)) : 0;
    host.style.setProperty('--player-panel-lift', `${lift}px`);
    // 落点盖住静止的指针:先不接指针事件(不让浮钮「被悬停」展开成带字胶囊),等指针真的动了再恢复
    if (fabStatic) {
      const p = pointerRef.current;
      const r = { left: fabStatic.left + target.x, right: fabStatic.right + target.x, top: fabStatic.top + target.y, bottom: fabStatic.bottom + target.y };
      if (p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom) {
        host.setAttribute('data-fab-settling', '');
        const at = { ...p };
        const off = (event) => {
          if (Math.abs(event.clientX - at.x) + Math.abs(event.clientY - at.y) < 2) return;
          host.removeAttribute('data-fab-settling');
          window.removeEventListener('pointermove', off, true);
        };
        window.addEventListener('pointermove', off, true);
      }
    }
    const fab = host.querySelector('.reader-ai-fab');
    if (!animateFab || !fab || motionReduced() || (prev.x === target.x && prev.y === target.y)) return;
    fabAnimRef.current?.cancel();
    const tf = (p) => `${p.x}px ${p.y}px`;
    if ((prevForm === 'bar' && f === 'mini') || (prevForm === 'mini' && f === 'bar')) {
      // 条 ⇄ 迷你:走直角路径(先横后竖 / 先竖后横),不斜穿正在成形的迷你
      const corner = prevForm === 'bar' ? { x: target.x, y: prev.y } : { x: prev.x, y: target.y };
      fabAnimRef.current = fab.animate([{ translate: tf(prev) }, { translate: tf(corner), offset: 0.5 }, { translate: tf(target) }], { duration: 520, easing: 'cubic-bezier(.3, .1, .2, 1)' });
    } else {
      fabAnimRef.current = fab.animate([{ translate: tf(prev) }, { translate: tf(target) }], {
        duration: PLAYER_MOTION.settleDur, easing: easings.settle, delay: f === 'none' ? 80 : 120, fill: 'backwards',
      });
    }
  }, [easings, hostRef, mobile]);

  // ── 向 Provider 注册:状态变化之前先量(入口马上要重建、条马上要隐藏) ──
  useEffect(() => api.registerDock({
    beforeChange(kind, ctx = {}) {
      finishFx();
      const input = ctx.input || 'pointer';
      if (kind === 'open') pendingRef.current = { kind, input, origin: ctx.originEl ? measureOrigin(ctx.originEl) : null };
      else if (kind === 'swap') {
        const host = formRef.current === 'mini' ? miniRef.current : barRef.current;
        pendingRef.current = { kind, input, snap: snapshotSwap(host, ctx.originEl) };
      } else if (kind === 'mini') pendingRef.current = { kind, input, snap: snapshotBar(barRef.current, dockRef.current) };
      else if (kind === 'bar') pendingRef.current = { kind, input, snap: snapshotMini(miniRef.current) };
      else if (kind === 'close') pendingRef.current = { kind, input, key: ctx.key };
    },
    exit() {
      finishFx();
      const layer = layerRef.current;
      const el = formRef.current === 'mini' ? miniRef.current : barRef.current;
      if (!el || !layer || motionReduced()) return Promise.resolve();
      layer.dataset.leaving = '';
      // 浮钮先回位(条在下沉淡出,浮钮不必等)
      const host = hostRef?.current;
      if (host && !mobile) {
        const fab = host.querySelector('.reader-ai-fab');
        const prev = { x: parseFloat(host.style.getPropertyValue('--player-fab-x')) || 0, y: parseFloat(host.style.getPropertyValue('--player-fab-y')) || 0 };
        host.style.setProperty('--player-fab-x', '0px');
        host.style.setProperty('--player-fab-y', '0px');
        host.style.setProperty('--player-panel-lift', '0px');
        if (fab && (prev.x || prev.y)) {
          fabAnimRef.current?.cancel();
          fabAnimRef.current = fab.animate([{ translate: `${prev.x}px ${prev.y}px` }, { translate: '0px 0px' }], {
            duration: PLAYER_MOTION.settleDur, easing: easings.settle, delay: 80, fill: 'backwards',
          });
        }
      }
      const a = el.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(18px) scale(.98)' }], {
        duration: PLAYER_MOTION.closeDur, easing: easings.out, fill: 'forwards',
      });
      exitRef.current = a;
      return a.finished.then(() => undefined, () => undefined);
    },
    cancelExit() {
      exitRef.current?.cancel();
      exitRef.current = null;
      delete layerRef.current?.dataset.leaving;
      syncChrome();
    },
  }), [api, easings, finishFx, hostRef, mobile, syncChrome]);

  const focusFor = useCallback((input, selector) => {
    if (input !== 'key') return;
    const el = typeof selector === 'string' ? layerRef.current?.querySelector(selector) : selector;
    el?.focus({ preventScroll: true });
  }, []);

  // ── 形态 / 条目变化:在同一帧里量终态、起动画(useLayoutEffect 在绘制之前) ──
  useLayoutEffect(() => {
    const prev = prevRef.current;
    prevRef.current = { form, key: itemKey };
    const pending = pendingRef.current;
    pendingRef.current = null;
    const layer = layerRef.current;
    const input = pending?.input || 'pointer';

    if (form === 'none') {
      exitRef.current = null;
      if (layer) delete layer.dataset.leaving;
      applyClearance('none');
      syncChrome();
      if (prev.form !== 'none' && input === 'key') {
        // 关闭回到这一期的入口(在场的话),焦点不掉进 body
        const back = document.querySelector(`.listen-entry[data-listen-key="${CSS.escape(pending?.key || prev.key)}"]`)
          || document.querySelector('.listen-entry');
        back?.focus({ preventScroll: true });
      }
      return;
    }
    if (prev.form === form && prev.key === itemKey) {
      // 首次挂载(同一标签页恢复的会话):直接就位,只补让位
      applyClearance(form);
      return;
    }
    const bar = barRef.current;
    const mini = miniRef.current;
    const tier = fxTier();
    const E = easings;
    const M = PLAYER_MOTION;
    const under = underRef.current;
    const over = overRef.current;
    if (!layer || !bar || (!mobile && !mini) || !under || !over) return;   // 手机没有迷你

    const run = (fx, { onLand, measureFrames = false } = {}) => {
      fxRef.current = fx;
      const deltas = [];
      let raf = 0;
      if (measureFrames) {
        let last = 0;
        const step = (now) => {
          if (last) deltas.push(now - last);
          last = now;
          raf = requestAnimationFrame(step);
        };
        raf = requestAnimationFrame(step);
      }
      fx.done().then(() => {
        if (raf) cancelAnimationFrame(raf);
        if (measureFrames) {
          session.set(FX_CHECKED_KEY, '1');
          if (framesSayLite(deltas)) session.set(FX_LITE_KEY, '1');
        }
        if (fxRef.current !== fx) return;
        fxRef.current = null;
        delete layer.dataset.assembling;
        fx.cleanup();
        onLand?.();
      });
    };

    if (prev.form === 'none') {
      // 打开:none → bar
      applyClearance('bar');
      syncChrome({ animateFab: true, prevForm: 'none' });
      const fx = createFx(under, over);
      const origin = pending?.origin;
      if (tier === 'reduce') {
        reducedEnter(fx, bar);
        run(fx);
        focusFor(input, '.player-bar .player-play');
        return;
      }
      if (tier === 'lite' || !origin) {
        // 起点不可见(滚出视口 / 恢复的会话)或软件合成:淡入
        liteEnter(fx, bar, E);
        run(fx, { onLand: () => focusFor(input, '.player-bar .player-play') });
        return;
      }
      layer.dataset.assembling = 'open';
      // 播放钮是墨色(实心 accent 留给标题区主钮):accent 内核与墨色内核交叉淡变
      openMorph(fx, { origin, bar, wide: !mobile, toneDiffers: true, E, M });
      run(fx, {
        // 首次完整形变录一次帧间隔:p95 超预算则本会话改走轻量档
        measureFrames: !session.get(FX_CHECKED_KEY) && !session.get(FX_OVERRIDE_KEY),
        onLand: () => focusFor(input, '.player-bar .player-play'),
      });
      return;
    }

    if (prev.form === 'bar' && form === 'mini') {
      applyClearance('mini');
      syncChrome({ animateFab: true, prevForm: 'bar' });
      const fx = createFx(under, over);
      if (tier === 'full' && pending?.snap) {
        layer.dataset.assembling = 'mini';
        miniMorph(fx, { snap: pending.snap, mini, playing, E, M });
      } else if (tier === 'lite') {
        liteEnter(fx, mini, E);
      } else {
        reducedEnter(fx, mini);
      }
      run(fx, { onLand: () => focusFor(input, '.player-mini .player-mini-art') });
      if (tier === 'reduce') focusFor(input, '.player-mini .player-mini-art');
      return;
    }

    if (prev.form === 'mini' && form === 'bar') {
      applyClearance('bar');
      syncChrome({ animateFab: true, prevForm: 'mini' });
      const fx = createFx(under, over);
      if (tier === 'full' && pending?.snap) {
        layer.dataset.assembling = 'bar';
        expandMorph(fx, { snap: pending.snap, bar, playing, E, M });
      } else if (tier === 'lite') {
        liteEnter(fx, bar, E);
      } else {
        reducedEnter(fx, bar);
      }
      run(fx, { onLand: () => focusFor(input, '.player-bar .player-play') });
      if (tier === 'reduce') focusFor(input, '.player-bar .player-play');
      return;
    }

    // 换台:条不消失
    syncChrome();
    if (tier === 'reduce' || !pending?.snap) return;
    const fx = createFx(under, over);
    swapMorph(fx, { snap: pending.snap, host: form === 'mini' ? mini : bar, E, M });
    run(fx);
  }, [form, itemKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // 卸载:撤掉宿主上的让位 / 避让变量与 Toast 抬升。必须是 layout 清理:桌面 ⇄ 手机互切时旧条与新条共用同一个宿主,
  // React 的顺序是「旧实例 layout 清理 → 新实例 layout 设置 → 旧实例 passive 清理」,写成 passive 会把新条刚写好的
  // data-player 一并删掉(开发构建的 StrictMode 二次挂载会把它补回来,所以只在生产构建里露出来)
  useLayoutEffect(() => {
    const host = hostRef?.current;
    return () => {
      if (host) {
        host.removeAttribute('data-player');
        host.removeAttribute('data-fab-settling');
        ['--player-bar-h', '--player-fab-x', '--player-fab-y', '--player-panel-lift'].forEach((v) => host.style.removeProperty(v));
      }
      document.documentElement.style.removeProperty('--player-top');
      fxRef.current?.cleanup();
    };
  }, [hostRef]);

  // 窗口尺寸 / 条高变化(容器查询切一行 ⇄ 两行)/ 桌面 ⇄ 手机 / 手机推页:重算避让与 Toast 抬升
  useEffect(() => {
    if (form === 'none') return undefined;
    const onResize = () => syncChrome();
    window.addEventListener('resize', onResize);
    const bar = barRef.current;
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => syncChrome()) : null;
    if (bar && ro) ro.observe(bar);
    // 手机:底栏高度变了(字号 / 横竖屏),坞与卡跟着换底边
    const tabbar = mobile ? hostRef?.current?.querySelector('.m-tabbar') : null;
    if (tabbar && ro) ro.observe(tabbar);
    return () => {
      window.removeEventListener('resize', onResize);
      ro?.disconnect();
    };
  }, [form, hostRef, mobile, syncChrome]);
  useEffect(() => {
    if (form === 'none') return undefined;
    // 推页 / 返回时条随页面换底边(CSS 过渡 420ms),落定后再量一次
    syncChrome();
    const id = window.setTimeout(() => syncChrome(), PLAYER_MOTION.settleDur + 40);
    return () => window.clearTimeout(id);
  }, [form, mobile, mobilePage, syncChrome]);

  // ── 进度:每秒 4 次写在进度轨 / 进度环上(不写在条根上——写在根上会连带重光栅模糊位图) ──
  useEffect(() => {
    if (!itemKey) return undefined;
    let last = 0;
    const paint = (t, d, force) => {
      const now = performance.now();
      if (!force && now - last < 240) return;
      last = now;
      const p = d > 0 ? Math.min(1, Math.max(0, t / d)) : 0;
      const v = p.toFixed(4);
      seekRef.current?.style.setProperty('--player-p', v);
      ringRef.current?.style.setProperty('--player-p', v);
      if (curRef.current) curRef.current.textContent = formatClock(t);
      if (durRef.current) durRef.current.textContent = d > 0 ? formatClock(d) : '--:--';
      if (clockRef.current) clockRef.current.textContent = `${formatClock(t)} / ${d > 0 ? formatClock(d) : '--:--'}`;
      const slider = sliderRef.current;
      if (slider) {
        slider.setAttribute('aria-valuemax', String(Math.round(d || 0)));
        slider.setAttribute('aria-valuenow', String(Math.round(t)));
        slider.setAttribute('aria-valuetext', d > 0 ? `${speakClock(t)}，共 ${speakClock(d)}` : speakClock(t));
      }
    };
    paintRef.current = paint;
    const { t, d } = api.progress();
    paint(t, d, true);
    return api.subscribeProgress(paint);
  }, [api, itemKey, form, status]);

  // ── 封面模糊配色:封面缩成 64×64 的静态模糊位图,遮罩浓度逐张算;读不到像素回中性底 ──
  const cover = item?.cover ? mediaProxyUrl(item.cover) : '';
  const applyTone = useCallback((next) => {
    const prev = toneRef.current;
    toneRef.current = next;
    // 换台:旧底先冻成一层,只做 opacity 淡出(不逐帧插值背景);同一张封面只更新遮罩浓度
    if (prev && (!next || prev.src !== next.src) && !motionReduced()) {
      setOldTones((list) => [...list.slice(-2), { ...prev, id: `${prev.src}#${Date.now()}` }]);
    }
    setTone(next);
  }, []);
  useEffect(() => {
    if (!cover) {
      if (toneRef.current) applyTone(null);
      return undefined;
    }
    let alive = true;
    loadCoverTone(cover).then((result) => {
      if (!alive) return;
      const layer = layerRef.current;
      if (!result?.blurUrl || !layer) {
        if (toneRef.current) applyTone(null);
        return;
      }
      // 最差像素叠上遮罩后,标题 / 副行与时间 / 「AI 生成」都要过 4.5:1(按当前主题的计算色算)
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      const lo = dark ? 0.64 : 0.7;
      const base = resolveVarColor(layer, '--player-base');
      const inks = ['--player-fg', '--player-fg-2', '--player-ai-ink'].map((v) => resolveVarColor(layer, v)).filter(Boolean);
      const veil = base && inks.length === 3 ? veilAlphaFor(result.pixels, base, inks, { lo }) : null;
      applyTone({ src: cover, blurUrl: result.blurUrl, veil });
    });
    return () => { alive = false; };
  }, [applyTone, cover, themeKey]);

  // ── 进度条:点击 / 拖动定位;← → 各 5 秒,Home / End,空格 / 回车播放暂停 ──
  const dragRef = useRef(null);
  const seekFromX = useCallback((clientX) => {
    const el = sliderRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const { d } = api.progress();
    if (!(d > 0) || !r.width) return null;
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * d;
  }, [api]);
  const sliderHandlers = {
    onPointerDown: (event) => {
      if (event.button !== 0 && event.pointerType === 'mouse') return;
      const t = seekFromX(event.clientX);
      if (t === null) return;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      event.currentTarget.dataset.dragging = '';
      dragRef.current = { id: event.pointerId, t, lastSeek: performance.now() };
      paintRef.current(t, api.progress().d, true);
      api.seekTo(t);
    },
    onPointerMove: (event) => {
      const drag = dragRef.current;
      if (!drag || drag.id !== event.pointerId) return;
      const t = seekFromX(event.clientX);
      if (t === null) return;
      drag.t = t;
      paintRef.current(t, api.progress().d, true);
      const now = performance.now();
      if (now - drag.lastSeek > 120) {
        drag.lastSeek = now;
        api.seekTo(t);
      }
    },
    onPointerUp: (event) => {
      const drag = dragRef.current;
      if (!drag || drag.id !== event.pointerId) return;
      dragRef.current = null;
      delete event.currentTarget.dataset.dragging;
      api.seekTo(drag.t);
    },
    onPointerCancel: (event) => {
      dragRef.current = null;
      delete event.currentTarget.dataset.dragging;
    },
    onKeyDown: (event) => {
      const { t, d } = api.progress();
      if (event.key === 'ArrowRight' || event.key === 'ArrowUp') { event.preventDefault(); api.seekTo(t + 5); }
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') { event.preventDefault(); api.seekTo(t - 5); }
      else if (event.key === 'PageUp') { event.preventDefault(); api.seekTo(t + 30); }
      else if (event.key === 'PageDown') { event.preventDefault(); api.seekTo(t - 30); }
      else if (event.key === 'Home') { event.preventDefault(); api.seekTo(0); }
      else if (event.key === 'End') { event.preventDefault(); if (d > 0) api.seekTo(d); }
      else if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); api.toggle(); }
    },
  };

  // ── 手机:坞 ⇄ 悬浮卡(开合、手势、层、停止 + 撤销都在 usePlayerCard) ──
  const pc = usePlayerCard({
    enabled: mobile && Boolean(item) && form === 'bar',
    mobilePage,
    routeKey,
    api,
    showToast,
    onOpenItem,
    tier: fxTier,
    easings,
    refs: { layerRef, barRef, cardRef, clipRef, innerRef: cardInRef, grabRef, scrimRef },
  });
  const cardState = pc.card;
  // 卡开合:Toast 抬升改按卡 / 坞重算
  useEffect(() => {
    if (mobile && form !== 'none') syncChrome();
  }, [cardState, form, mobile, syncChrome]);
  const barPointer = mobile ? pc.dockHandlers : {};

  const toneStyle = (t) => {
    if (!t) return undefined;
    const style = { '--player-blur': `url("${t.blurUrl}")` };
    if (typeof t.veil === 'number') style['--player-veil-p'] = `${Math.round(t.veil * 100)}%`;
    return style;
  };
  const dropOld = (id) => setOldTones((list) => list.filter((t) => t.id !== id));
  const bgLayers = (
    <>
      <span className="player-bg" aria-hidden="true" />
      {oldTones.map((t) => (
        <span
          key={t.id}
          className="player-bg is-old"
          aria-hidden="true"
          style={toneStyle(t)}
          onAnimationEnd={() => dropOld(t.id)}
        />
      ))}
    </>
  );

  const speech = playerTrackSpeech(item);
  const playLabel = status === 'error' ? `重试${speech}` : playing ? `暂停${speech}` : `播放${speech}`;
  // ✦ 占位只给 AI 音轨(文章导读没有封面);没有封面的原节目用普通播客图标,不冒充 AI 触点
  const art = item ? (item.ai && !item.cover
    ? <span className="player-cover is-ai" aria-hidden="true"><Spark /></span>
    : <PodcastCover src={item.cover} className="player-cover" loading="eager" />) : null;
  const playGlyph = (
    <span className="player-play-ico">
      <span className="player-g-play"><PlayGlyph /></span>
      <span className="player-g-pause"><PauseGlyph /></span>
    </span>
  );

  // ── 手机:坞(一行)与悬浮卡(B1:这一期 · 进度 · 倍速 / −15 / 播放 / +15 / 停止 五枚 48 圆钮) ──
  const trackSub = (withShow) => (item?.ai ? (
    <>
      {withShow && item.show ? <><span className="player-sub-text">{item.show}</span><span className="player-dot" aria-hidden="true">·</span></> : null}
      <span className={withShow ? 'player-sub-track' : 'player-sub-text'}><Spark />{playerTrackLabel(item)}</span>
      <span className="player-dot" aria-hidden="true">·</span>
      <span className="player-ai">AI 生成</span>
    </>
  ) : withShow ? (
    <>
      {item?.show ? <><span className="player-sub-text">{item.show}</span><span className="player-dot" aria-hidden="true">·</span></> : null}
      <span className="player-sub-track">{playerTrackLabel(item)}</span>
    </>
  ) : (
    <span className="player-sub-text">{item?.show || playerTrackLabel(item)}</span>
  ));
  const dockRow = mobile && item ? (
    <div className="player-dock-row">
      <button
        type="button"
        className="player-info"
        aria-label={`展开播放器：${shownTitle.title}`}
        aria-haspopup="dialog"
        aria-expanded={cardState === 'open'}
        onClick={pc.open}
      >
        <span className="player-art">
          <svg ref={ringRef} className="player-ring" viewBox="0 0 44 44" aria-hidden="true">
            <circle className="player-ring-track" cx="22" cy="22" r="20.75" />
            <circle className="player-ring-fill" cx="22" cy="22" r="20.75" pathLength="100" />
          </svg>
          <span className="player-art-slot">{art}</span>
        </span>
        <span className="player-info-slot">
          <span className="player-title" lang={shownTitle.lang || undefined}>{shownTitle.title}</span>
          <span className="player-sub">
            {rate !== 1 ? <span className="player-rate-tag">{formatRate(rate)}</span> : null}
            {trackSub(false)}
            <span className="player-dot" aria-hidden="true">·</span>
            {status === 'error'
              ? <span className="player-sub-err">无法播放</span>
              : <span ref={clockRef} className="player-clock">00:00 / --:--</span>}
          </span>
        </span>
      </button>
      <button type="button" className="player-play" aria-label={playLabel} onClick={api.toggle}>
        {playGlyph}
      </button>
    </div>
  ) : null;
  const cardEl = mobile && item ? (
    <div
      ref={cardRef}
      className="player-card"
      role="dialog"
      aria-modal="true"
      aria-label="播放器"
      tabIndex={-1}
      data-layer-panel=""
      hidden={cardState === 'closed'}
      {...pc.cardHandlers}
    >
      {/* 抓手(拍板 33):真按钮,命中区 120 × 44、上沿伸出卡外 16px(不进裁切层);点按收起,往下拖整卡也收起 */}
      <button ref={grabRef} type="button" className="player-grab" aria-label="收起播放器" onClick={() => pc.close()}>
        <span className="player-grab-bar" aria-hidden="true" />
      </button>
      <div ref={clipRef} className="player-card-clip">
        {bgLayers}
        <div ref={cardInRef} className="player-card-in">
          <button type="button" className="player-ident" aria-label={playerOpenLabel(item, shownTitle.title)} onClick={() => pc.openItem(item)}>
            <span className="player-ident-art">{art}</span>
            <span className="player-ident-text">
              <span className="player-ident-title" lang={shownTitle.lang || undefined}>{shownTitle.title}</span>
              <span className="player-ident-sub">{trackSub(true)}<ChevronRight className="player-ident-go" aria-hidden="true" /></span>
            </span>
          </button>
          <div ref={seekRef} className="player-card-seek">
            {status === 'error' ? (
              <>
                <span className="player-error" role="status">无法播放</span>
                <button type="button" className="player-retry" onClick={api.retry}>重试</button>
              </>
            ) : (
              <>
                <span ref={curRef} className="player-time player-cur">00:00</span>
                <div className="player-track">
                  <div
                    ref={sliderRef}
                    className="player-track-in"
                    role="slider"
                    tabIndex={0}
                    aria-label="播放进度"
                    aria-valuemin={0}
                    {...sliderHandlers}
                  >
                    <span className="player-rail"><span className="player-fill" /></span>
                    <span className="player-thumb" />
                  </div>
                </div>
                <span ref={durRef} className="player-time player-dur">--:--</span>
              </>
            )}
          </div>
          <div className="player-card-row">
            <button type="button" className={`player-cbtn is-ring player-crate${rate !== 1 ? ' is-on' : ''}`} aria-label={rateSpeech(rate)} onClick={api.cycleRate}>
              <span>{formatRate(rate)}</span>
            </button>
            <span className="player-cdiv" aria-hidden="true" />
            <div className="player-ctrio">
              <button type="button" className="player-cbtn is-well" aria-label="后退 15 秒" onClick={() => api.seekBy(-15)}>
                <SkipGlyph back />
              </button>
              <button type="button" className="player-play" aria-label={playLabel} onClick={api.toggle}>
                {playGlyph}
              </button>
              <button type="button" className="player-cbtn is-well" aria-label="前进 15 秒" onClick={() => api.seekBy(15)}>
                <SkipGlyph />
              </button>
            </div>
            <span className="player-cdiv" aria-hidden="true" />
            <button type="button" className="player-cbtn is-ring player-cstop" aria-label="停止播放（播放条一并收起）" onClick={pc.stop}>
              <StopGlyph />
            </button>
          </div>
        </div>
      </div>
    </div>
  ) : null;

  // data-layer-keep(层原语,issue #230):整屏推入页(正文 / 发现,z 低于本条)把下层 inert 时保留播放条;盖住它的层照常 inert
  const layer = (
    <div
      ref={layerRef}
      className={`player-layer ${mobile ? 'is-mobile' : 'is-desktop'}`}
      data-form={form}
      data-page={mobile ? mobilePage : undefined}
      data-card={mobile ? cardState : undefined}
      data-layer-keep="dock"
      data-tone={tone ? 'blur' : 'neutral'}
      data-playing={playing ? '' : undefined}
      data-buffering={playing && buffering ? '' : undefined}
      data-error={status === 'error' ? '' : undefined}
      style={{ ...toneStyle(tone), '--player-settle-ease': easings.settle }}
    >
      <div ref={underRef} className="player-fx is-under" aria-hidden="true" />
      <div ref={dockRef} className="player-dock">
        {item && form !== 'none' && (
          <>
            <div
              ref={barRef}
              className="player-bar"
              role="region"
              aria-label={`正在播放：${shownTitle.title}${item.ai ? '（AI 生成）' : ''}`}
              hidden={form !== 'bar'}
              onDragStart={(event) => event.preventDefault()}
              {...barPointer}
            >
              {bgLayers}
              {mobile ? dockRow : (
              <div className="player-bar-in">
                <button
                  type="button"
                  className="player-info"
                  aria-label={`打开：${shownTitle.title}`}
                  onClick={() => onOpenItem?.(item)}
                >
                  <span className="player-art"><span className="player-art-slot">{art}</span></span>
                  <span className="player-info-slot">
                    <span className="player-title" lang={shownTitle.lang || undefined}>{shownTitle.title}</span>
                    <span className="player-sub">
                      {item.ai ? (
                        <>
                          <Spark />
                          <span className="player-sub-text">{playerTrackLabel(item)}</span>
                          <span className="player-dot" aria-hidden="true">·</span>
                          <span className="player-ai">AI 生成</span>
                        </>
                      ) : (
                        <span className="player-sub-text">{item.show || playerTrackLabel(item)}</span>
                      )}
                    </span>
                  </span>
                </button>
                <div className="player-ctrl">
                  <button type="button" className="player-btn player-skip" aria-label="后退 15 秒" onClick={() => api.seekBy(-15)}>
                    <RotateCcw aria-hidden="true" /><span>15</span>
                  </button>
                  <button type="button" className="player-play" aria-label={playLabel} onClick={api.toggle}>
                    {playGlyph}
                  </button>
                  <button type="button" className="player-btn player-skip" aria-label="前进 15 秒" onClick={() => api.seekBy(15)}>
                    <span>15</span><RotateCw aria-hidden="true" />
                  </button>
                </div>
                <div className="player-row2">
                  <div ref={seekRef} className="player-seek">
                    {status === 'error' ? (
                      <>
                        <span className="player-error" role="status">无法播放</span>
                        <button type="button" className="player-retry" onClick={api.retry}>重试</button>
                      </>
                    ) : (
                      <>
                        <span ref={curRef} className="player-time player-cur">00:00</span>
                        <div className="player-track">
                          <div
                            ref={sliderRef}
                            className="player-track-in"
                            role="slider"
                            tabIndex={0}
                            aria-label="播放进度"
                            aria-valuemin={0}
                            {...sliderHandlers}
                          >
                            <span className="player-rail"><span className="player-fill" /></span>
                            <span className="player-thumb" />
                          </div>
                        </div>
                        <span ref={durRef} className="player-time player-dur">--:--</span>
                      </>
                    )}
                  </div>
                  <div className="player-tail">
                    <button
                      type="button"
                      className={`player-btn player-rate ${rate !== 1 ? 'is-on' : ''}`}
                      aria-label={`播放速度 ${rate} 倍`}
                      onClick={api.cycleRate}
                    >
                      {formatRate(rate)}
                    </button>
                    <button type="button" className="player-btn player-min" aria-label="缩小播放条" title="缩小" onClick={api.minimize}>
                      <ChevronDown aria-hidden="true" />
                    </button>
                    <button type="button" className="player-btn player-close" aria-label="关闭播放条（停止播放）" title="关闭" onClick={api.close}>
                      <X aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </div>
              )}
            </div>
            {mobile ? cardEl : (
            <div
              ref={miniRef}
              className="player-mini"
              role="region"
              aria-label={`正在播放（已缩小）：${shownTitle.title}`}
              hidden={form !== 'mini'}
            >
              {bgLayers}
              <button type="button" className="player-mini-art" aria-label="展开播放条" onClick={api.expand}>
                <svg ref={ringRef} className="player-ring" viewBox="0 0 40 40" aria-hidden="true">
                  <circle className="player-ring-track" cx="20" cy="20" r="18.75" />
                  <circle className="player-ring-fill" cx="20" cy="20" r="18.75" pathLength="100" />
                </svg>
                <span className="player-art-slot">{art}</span>
              </button>
              <button type="button" className="player-play player-mini-play" aria-label={playLabel} onClick={api.toggle}>
                {playGlyph}
              </button>
            </div>
            )}
          </>
        )}
      </div>
      <div ref={overRef} className="player-fx is-over" aria-hidden="true" />
    </div>
  );
  if (!mobile) return layer;
  // 卡外的淡压暗(拍板 32):列表页压在列表之上、底栏之下(底栏照常能点,切 Tab 时卡一并收起);
  // 正文页 / 推入页没有底栏,整屏压在页之上、卡之下。点了只收卡、不穿透(LayerScrim:按下与松开同在遮罩才算)
  return (
    <>
      {cardState !== 'closed' ? (
        <div ref={scrimRef} className={`player-scrim${cardState === 'open' ? ' is-on' : ''}`} data-page={mobilePage}>
          <LayerScrim onClose={() => pc.close()} />
        </div>
      ) : null}
      {layer}
    </>
  );
}
