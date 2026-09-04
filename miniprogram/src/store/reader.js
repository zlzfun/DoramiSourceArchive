import { useEffect, useState } from 'react';
import {
  fetchReaderSources, fetchUnreadCounts, fetchFavorites,
  subscribeSource as apiSubscribe, unsubscribeSource as apiUnsubscribe,
  addFavorite as apiAddFavorite, removeFavorite as apiRemoveFavorite,
} from '../api';

/**
 * 阅读器共享状态(精简版数据层,方案 §3.1:不复用 DOM 耦合的 useReaderState,
 * 按其职责清单重写——源目录/订阅、未读、收藏、各容器过滤器)。
 * 模块级单例 + 订阅回调;页面经 useReaderStore() 取快照并随变更重渲染。
 */
const SHAPES = ['article', 'podcast', 'bulletin', 'social'];

const state = {
  sources: [],
  sourceMap: {},
  sourcesLoaded: false,
  sourcesLoading: false,
  unreadBySource: {},
  unreadTotal: 0,
  favoriteIds: new Set(),
  favoritesLoaded: false,
  // 本会话逐篇已读覆盖:id → true(已读)/false(未读);无覆盖以服务端 article.unread 为准
  readOverrides: new Map(),
  // 各容器过滤器:{ sourceId: string|null, favOnly: bool, unreadOnly: bool, search: '' }
  filters: Object.fromEntries(SHAPES.map((s) => [s, { sourceId: null, favOnly: false, unreadOnly: false, search: '' }])),
  // 列表需要重拉的标记(过滤器变更、订阅变更后由 feed 页消费)
  listVersion: 0,
};
const listeners = new Set();
function emit() { listeners.forEach((fn) => fn()); }

export function useReaderStore() {
  const [, tick] = useState(0);
  useEffect(() => {
    const fn = () => tick((n) => n + 1);
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  return state;
}
export function getReaderState() { return state; }

export function resetReaderStore() {
  state.sources = []; state.sourceMap = {}; state.sourcesLoaded = false;
  state.unreadBySource = {}; state.unreadTotal = 0;
  state.favoriteIds = new Set(); state.favoritesLoaded = false;
  state.readOverrides = new Map();
  SHAPES.forEach((s) => { state.filters[s] = { sourceId: null, favOnly: false, unreadOnly: false, search: '' }; });
  emit();
}

// ---- 源目录 ----
export async function loadSources({ force = false } = {}) {
  if (state.sourcesLoading || (state.sourcesLoaded && !force)) return state.sources;
  state.sourcesLoading = true; emit();
  try {
    const data = await fetchReaderSources();
    const items = Array.isArray(data) ? data : (data.items || []);
    state.sources = items;
    state.sourceMap = Object.fromEntries(items.map((s) => [s.source_id, s]));
    state.sourcesLoaded = true;
  } finally {
    state.sourcesLoading = false; emit();
  }
  return state.sources;
}
export function subscribedSources() { return state.sources.filter((s) => s.subscribed); }
export function sourceName(id) { const s = state.sourceMap[id]; return (s && s.name) || id || ''; }

export async function subscribe(sourceId) {
  await apiSubscribe(sourceId);
  const s = state.sourceMap[sourceId];
  if (s) s.subscribed = true;
  state.listVersion += 1; emit();
  refreshUnread().catch(() => {});
}
export async function unsubscribe(sourceId) {
  await apiUnsubscribe(sourceId);
  const s = state.sourceMap[sourceId];
  if (s) s.subscribed = false;
  SHAPES.forEach((shape) => { if (state.filters[shape].sourceId === sourceId) state.filters[shape].sourceId = null; });
  delete state.unreadBySource[sourceId];
  state.listVersion += 1; emit();
}

// ---- 未读 ----
export function applyUnreadCounts(data) {
  state.unreadBySource = (data && data.by_source) || {};
  state.unreadTotal = (data && data.total) || 0;
  emit();
}
export async function refreshUnread() { applyUnreadCounts(await fetchUnreadCounts()); }
export function isArticleUnread(article) {
  if (state.readOverrides.has(article.id)) return !state.readOverrides.get(article.id);
  return Boolean(article.unread);
}
export function overrideRead(id, read) { state.readOverrides.set(id, read); emit(); }
export function unreadForShape(shape) {
  return state.sources
    .filter((s) => s.subscribed && s.shape === shape)
    .reduce((sum, s) => sum + (state.unreadBySource[s.source_id] || 0), 0);
}

// ---- 收藏 ----
export async function loadFavoriteIds() {
  const data = await fetchFavorites({}, 1, 0);
  state.favoriteIds = new Set(data.favorite_ids || []);
  state.favoritesLoaded = true; emit();
}
export async function toggleFavorite(id) {
  const on = state.favoriteIds.has(id);
  if (on) { await apiRemoveFavorite(id); state.favoriteIds.delete(id); }
  else { await apiAddFavorite(id); state.favoriteIds.add(id); }
  state.favoriteIds = new Set(state.favoriteIds);
  emit();
  return !on;
}

// ---- 过滤器 ----
export function setFilter(shape, patch) {
  state.filters[shape] = { ...state.filters[shape], ...patch };
  state.listVersion += 1; emit();
}
export function bumpList() { state.listVersion += 1; emit(); }
