import { createContext, useContext } from 'react';
import { initialPlayerState } from '../../utils/playerCore';

// 全局播放器的 context(issue #199)。state 只在条目 / 形态 / 播放态 / 倍速变化时更新——
// 进度不进 React state(每秒 4 次写在进度元素上,见 PlayerDock),所以订阅它的阅读器不会随进度重渲。
export const PlayerContext = createContext(null);

const NOOP_PLAYER = Object.freeze({
  state: initialPlayerState,
  api: Object.freeze({
    play() {}, toggle() {}, pause() {}, seekBy() {}, seekTo() {}, cycleRate() {},
    minimize() {}, expand() {}, close() {}, retry() {},
  }),
});

/** 没有 Provider 时(单独渲染的组件 / 测试)返回一个空播放器,调用方不必判空。 */
export function usePlayer() {
  return useContext(PlayerContext) || NOOP_PLAYER;
}
