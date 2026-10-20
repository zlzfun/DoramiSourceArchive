import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react';
import { PlayerContext } from './playerContext';
import {
  initialPlayerState,
  isPlayingIntent,
  nextRate,
  playerReducer,
  playerTrackLabel,
  readPlayerSession,
  resolveStartPosition,
  writePlayerSession,
} from '../../utils/playerCore';
import { readPodcastPosition, writePodcastPosition } from '../../utils/podcastPlayback';
import { mediaProxyUrl } from '../../api';

// 全局播放器(issue #199):全站唯一的 <audio> 与播放状态。挂在阅读器工作区外壳(ReaderWorkspace),
// 桌面阅读器 ⇄ 移动壳互切、换文章、换容器、进出早报 / 发现 / 榜单都不卸载;不放进按文章重挂载的阅读窗。
// 进度以 audio.currentTime 为准,经 subscribeProgress 推给播放条直接写进度元素,不进 React state。

const SAVE_EVERY_MS = 3000;
const POSITION_STATE_EVERY_MS = 1000;
const sessionStore = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

/** 显式位置只对记下它的那一条音轨有效。 */
function explicitFor(seek, item) {
  return seek && item && seek.key === item.key ? seek.t : null;
}

function initPlayer(username) {
  const saved = readPlayerSession(sessionStore(), username);
  return saved ? playerReducer(initialPlayerState, { type: 'restore', ...saved }) : initialPlayerState;
}

export default function PlayerProvider({ username = '', active = true, children }) {
  const [state, dispatch] = useReducer(playerReducer, username, initPlayer);
  const [announcement, setAnnouncement] = useState('');
  const audioRef = useRef(null);
  const stateRef = useRef(state);
  const loadedRef = useRef(null);        // 已装进 <audio> 的条目(恢复的会话在首次播放时才装)
  const pendingSeekRef = useRef(null);   // 元数据到了再跳的续播位置
  // 元数据到达之前读者明确拖到的位置 { key, t }(恢复的会话还没装进 <audio>,或已装、仍在加载):
  // 按音轨 key 隔离,换台 / 关闭作废,元数据到达并应用之后才清;不套「末 3 秒视为听完」(0 也是有效值)
  const explicitSeekRef = useRef(null);
  const lastSaveRef = useRef(0);
  const lastPositionStateRef = useRef(0);
  const progressSubs = useRef(new Set());
  const dockRef = useRef(null);          // PlayerDock 注册 { beforeChange, exit, cancelExit }
  const closingRef = useRef(false);
  const inputRef = useRef('pointer');    // 最近一次输入方式:只有键盘触发时才程序化移焦点

  useLayoutEffect(() => {
    stateRef.current = state;
  });

  useEffect(() => {
    const onKey = (event) => {
      if (!event.metaKey && !event.ctrlKey && !event.altKey) inputRef.current = 'key';
    };
    const onPointer = () => { inputRef.current = 'pointer'; };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('pointerdown', onPointer, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pointerdown', onPointer, true);
    };
  }, []);

  const durationOf = useCallback(() => {
    const audio = audioRef.current;
    const loaded = loadedRef.current;
    const item = stateRef.current.item;
    if (audio && loaded && item && loaded.key === item.key && Number.isFinite(audio.duration) && audio.duration > 0) {
      return audio.duration;
    }
    return item?.duration || 0;
  }, []);

  const positionOf = useCallback(() => {
    const audio = audioRef.current;
    const loaded = loadedRef.current;
    const item = stateRef.current.item;
    if (!item) return 0;
    const explicit = explicitFor(explicitSeekRef.current, item);
    if (audio && loaded && loaded.key === item.key && pendingSeekRef.current === null && explicit === null) {
      return audio.currentTime || 0;
    }
    const saved = pendingSeekRef.current ?? readPodcastPosition(item.articleId, item.track);
    return resolveStartPosition({ saved, explicit, duration: durationOf() });
  }, [durationOf]);

  const emitProgress = useCallback((force = false) => {
    const t = positionOf();
    const d = durationOf();
    progressSubs.current.forEach((fn) => fn(t, d, force));
  }, [durationOf, positionOf]);

  const updatePositionState = useCallback((force = false) => {
    const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
    if (!ms?.setPositionState) return;
    const now = Date.now();
    if (!force && now - lastPositionStateRef.current < POSITION_STATE_EVERY_MS) return;
    const duration = durationOf();
    if (!(duration > 0)) return;
    lastPositionStateRef.current = now;
    try {
      ms.setPositionState({
        duration,
        playbackRate: audioRef.current?.playbackRate || 1,
        position: Math.min(duration, Math.max(0, positionOf())),
      });
    } catch {
      /* 部分浏览器在元数据未齐时拒绝,下一次再写 */
    }
  }, [durationOf, positionOf]);

  const persist = useCallback((force = false) => {
    const audio = audioRef.current;
    const item = loadedRef.current;
    if (!audio || !item || pendingSeekRef.current !== null) return;
    const now = Date.now();
    if (!force && now - lastSaveRef.current < SAVE_EVERY_MS) return;
    const t = audio.currentTime;
    if (!Number.isFinite(t)) return;
    if (writePodcastPosition(item.articleId, item.track, t)) lastSaveRef.current = now;
  }, []);

  const onPlayRejected = useCallback((error) => {
    // 换台时上一条的 play() 被新的加载打断(AbortError)不是失败;被浏览器拦下自动播放则回到暂停
    if (error?.name === 'AbortError') return;
    if (error?.name === 'NotAllowedError') dispatch({ type: 'paused' });
  }, []);

  const loadInto = useCallback((item, { play = true } = {}) => {
    const audio = audioRef.current;
    if (!audio || !item) return;
    persist(true);
    loadedRef.current = item;
    lastSaveRef.current = 0;
    // 换到别的音轨:上一条的显式位置作废;同一条(恢复的会话首次播放 / 重试)保留到元数据到达
    if (explicitSeekRef.current && explicitSeekRef.current.key !== item.key) explicitSeekRef.current = null;
    pendingSeekRef.current = explicitFor(explicitSeekRef.current, item) ?? readPodcastPosition(item.articleId, item.track);
    const rate = stateRef.current.rate || 1;
    audio.defaultPlaybackRate = rate;
    audio.playbackRate = rate;
    audio.src = item.src;
    // play() 必须在点击这一拍里同步调用(iOS Safari 只认手势内的 play)
    if (play) audio.play()?.catch(onPlayRejected);
  }, [onPlayRejected, persist]);

  const announce = useCallback((text) => {
    setAnnouncement('');
    window.setTimeout(() => setAnnouncement(text), 40);
  }, []);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    const cur = stateRef.current;
    if (!audio || !cur.item) return;
    if (cur.status === 'error' || audio.paused) {
      if (cur.status === 'error' || loadedRef.current?.key !== cur.item.key) {
        loadInto(cur.item, { play: true });
      } else {
        audio.play()?.catch(onPlayRejected);
      }
      dispatch({ type: 'intent-play' });
    } else {
      audio.pause();
    }
  }, [loadInto, onPlayRejected]);

  const play = useCallback((item, { originEl = null } = {}) => {
    if (!item?.src) return;
    const cur = stateRef.current;
    if (closingRef.current) {
      // 收起动画还没走完又点了收听:撤掉收起,按换台处理(条不消失)
      closingRef.current = false;
      dockRef.current?.cancelExit?.();
    }
    if (cur.item?.key === item.key && cur.form !== 'none' && !closingRef.current) {
      toggle();
      return;
    }
    const kind = cur.form === 'none' ? 'open' : 'swap';
    dockRef.current?.beforeChange?.(kind, { originEl, input: inputRef.current });
    loadInto(item, { play: true });
    dispatch({ type: 'load', item });
    announce(`正在播放：${item.title}${item.ai ? '，AI 生成' : ''}`);
  }, [announce, loadInto, toggle]);

  const pause = useCallback(() => { audioRef.current?.pause(); }, []);

  const seekTo = useCallback((seconds) => {
    const audio = audioRef.current;
    const cur = stateRef.current;
    if (!cur.item) return;
    const d = durationOf();
    const t = Math.max(0, d > 0 ? Math.min(d, seconds) : seconds);
    if (audio && loadedRef.current?.key === cur.item.key && pendingSeekRef.current === null) {
      audio.currentTime = t;
    } else {
      // 元数据还没到(恢复的会话没装进 <audio>,或已装、仍在加载):记成显式位置,首次播放 / 元数据到了从这里起
      explicitSeekRef.current = { key: cur.item.key, t };
      writePodcastPosition(cur.item.articleId, cur.item.track, t);
      if (loadedRef.current?.key === cur.item.key) pendingSeekRef.current = t;
    }
    emitProgress(true);
    updatePositionState(true);
  }, [durationOf, emitProgress, updatePositionState]);

  const seekBy = useCallback((delta) => { seekTo(positionOf() + delta); }, [positionOf, seekTo]);

  const cycleRate = useCallback(() => {
    const rate = nextRate(stateRef.current.rate);
    const audio = audioRef.current;
    if (audio) {
      audio.defaultPlaybackRate = rate;
      audio.playbackRate = rate;
    }
    dispatch({ type: 'rate', rate });
  }, []);

  const minimize = useCallback(() => {
    if (stateRef.current.form !== 'bar') return;
    dockRef.current?.beforeChange?.('mini', { input: inputRef.current });
    dispatch({ type: 'minimize' });
  }, []);

  const expand = useCallback(() => {
    if (stateRef.current.form !== 'mini') return;
    dockRef.current?.beforeChange?.('bar', { input: inputRef.current });
    dispatch({ type: 'expand' });
  }, []);

  const close = useCallback(async () => {
    const cur = stateRef.current;
    if (!cur.item || cur.form === 'none' || closingRef.current) return;
    const audio = audioRef.current;
    persist(true);
    audio?.pause();
    closingRef.current = true;
    const input = inputRef.current;
    try {
      await dockRef.current?.exit?.();
    } catch {
      /* 动画被打断:照常关闭 */
    }
    if (!closingRef.current) return;   // 收起途中又点了收听
    closingRef.current = false;
    if (audio) {
      audio.removeAttribute('src');
      audio.load();
    }
    loadedRef.current = null;
    pendingSeekRef.current = null;
    explicitSeekRef.current = null;
    dockRef.current?.beforeChange?.('close', { input, key: cur.item.key });
    dispatch({ type: 'close' });
  }, [persist]);

  const retry = useCallback(() => {
    const cur = stateRef.current;
    if (!cur.item) return;
    loadInto(cur.item, { play: true });
    dispatch({ type: 'intent-play' });
  }, [loadInto]);

  const subscribeProgress = useCallback((fn) => {
    progressSubs.current.add(fn);
    return () => progressSubs.current.delete(fn);
  }, []);

  // 离开阅读器界面(管理员切回管理台,阅读器被 is-off 隐藏而不卸载):暂停,条还在,回来接着点播放
  useEffect(() => {
    if (!active) audioRef.current?.pause();
  }, [active]);

  // 会话恢复:同一标签页刷新后恢复为暂停态的条与位置(写在 sessionStorage,换号 / 新会话不恢复)
  const itemKey = state.item?.key || '';
  useEffect(() => {
    writePlayerSession(sessionStore(), username, stateRef.current);
  }, [username, itemKey, state.form, state.rate]);

  // 卸载(退出登录 / 账号失效):停止播放、记下位置,释放媒体会话
  useEffect(() => {
    const audio = audioRef.current;
    const onPageHide = () => persist(true);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      persist(true);
      audio?.pause();
      const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
      if (ms) {
        ms.metadata = null;
        ms.playbackState = 'none';
      }
    };
  }, [persist]);

  // ── Media Session:系统级媒体控件(锁屏 / 耳机 / 键盘媒体键)──
  const playing = isPlayingIntent(state);
  useEffect(() => {
    const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
    if (!ms || typeof window.MediaMetadata !== 'function') return;
    const item = stateRef.current.item;
    if (!item || stateRef.current.form === 'none') {
      ms.metadata = null;
      ms.playbackState = 'none';
      return;
    }
    ms.metadata = new window.MediaMetadata({
      title: item.title,
      artist: item.show || '',
      album: playerTrackLabel(item),
      artwork: item.cover ? [{ src: mediaProxyUrl(item.cover), sizes: '512x512' }] : [],
    });
  }, [itemKey]);
  useEffect(() => {
    const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
    if (!ms || !itemKey) return;
    ms.playbackState = playing ? 'playing' : 'paused';
  }, [playing, itemKey]);
  useEffect(() => {
    const ms = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
    if (!ms?.setActionHandler) return undefined;
    const handlers = {
      play: () => { if (audioRef.current?.paused) toggle(); },
      pause: () => audioRef.current?.pause(),
      seekbackward: (details) => seekBy(-(details?.seekOffset || 15)),
      seekforward: (details) => seekBy(details?.seekOffset || 15),
      seekto: (details) => { if (Number.isFinite(details?.seekTime)) seekTo(details.seekTime); },
      stop: () => close(),
    };
    for (const [action, handler] of Object.entries(handlers)) {
      try { ms.setActionHandler(action, handler); } catch { /* 浏览器不支持该动作 */ }
    }
    return () => {
      for (const action of Object.keys(handlers)) {
        try { ms.setActionHandler(action, null); } catch { /* 同上 */ }
      }
    };
  }, [close, seekBy, seekTo, toggle]);

  // ── <audio> 事件 → 状态机 ──
  const audioHandlers = {
    onPlay: () => dispatch({ type: 'intent-play' }),
    onPlaying: () => { dispatch({ type: 'playing' }); updatePositionState(true); },
    onPause: () => { persist(true); dispatch({ type: 'paused' }); updatePositionState(true); },
    onWaiting: () => dispatch({ type: 'waiting' }),
    onCanPlay: () => dispatch({ type: 'canplay' }),
    onLoadedMetadata: (event) => {
      const audio = event.currentTarget;
      const pos = resolveStartPosition({
        saved: pendingSeekRef.current,
        explicit: explicitFor(explicitSeekRef.current, loadedRef.current),
        duration: audio.duration,
      });
      pendingSeekRef.current = null;
      if (pos > 0) {
        try {
          audio.currentTime = pos;
        } catch {
          /* 个别浏览器在可寻址范围到齐前拒绝 seek:从头播放也能用 */
        }
      }
      // 显式位置到这里才算用掉(赋 src、调 play() 时都不清,否则会落回自动续播规则)
      explicitSeekRef.current = null;
      emitProgress(true);
      updatePositionState(true);
    },
    onDurationChange: () => emitProgress(true),
    onTimeUpdate: () => { persist(false); emitProgress(false); updatePositionState(false); },
    onSeeked: () => { emitProgress(true); updatePositionState(true); },
    onRateChange: () => updatePositionState(true),
    onEnded: () => { persist(true); dispatch({ type: 'ended' }); emitProgress(true); },
    onError: (event) => {
      // src 被移除(关闭)时也会触发 error:没有条目就不是读者能看到的失败
      if (!loadedRef.current || !event.currentTarget.getAttribute('src')) return;
      dispatch({ type: 'error' });
      announce('无法播放');
    },
  };

  const api = useMemo(() => ({
    play, toggle, pause, seekTo, seekBy, cycleRate, minimize, expand, close, retry,
    subscribeProgress,
    progress: () => ({ t: positionOf(), d: durationOf() }),
    registerDock: (dock) => {
      dockRef.current = dock;
      return () => { if (dockRef.current === dock) dockRef.current = null; };
    },
    lastInput: () => inputRef.current,
  }), [close, cycleRate, durationOf, expand, minimize, pause, play, positionOf, retry, seekBy, seekTo, subscribeProgress, toggle]);

  const value = useMemo(() => ({ state, api }), [state, api]);

  return (
    <PlayerContext.Provider value={value}>
      {children}
      {/* 全站唯一的音频元素;src 由播放器在点击这一拍里赋值(见 loadInto) */}
      <audio ref={audioRef} preload="metadata" hidden {...audioHandlers} />
      <div className="sr-only" aria-live="polite">{announcement}</div>
    </PlayerContext.Provider>
  );
}
