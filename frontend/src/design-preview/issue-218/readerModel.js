import { ARTICLES, CONTAINER_ITEMS, ROLE_GROUPS, SOURCES, SOURCE_NAME_MAP } from './fixtures';

// 桌面与手机共用的派生数据(样页内的「接口响应」替身)

export function sourceGroupsFor(mode) {
  return ROLE_GROUPS
    .map((role) => ({ ...role, list: SOURCES.filter((source) => source.shape === mode && source.role === role.key) }))
    .filter((group) => group.list.length > 0);
}

export function unreadBySource(mode, unread) {
  const counts = {};
  CONTAINER_ITEMS[mode].forEach((item) => {
    if (unread.has(item.id)) counts[item.source_id] = (counts[item.source_id] || 0) + 1;
  });
  return counts;
}

// 内容类型筛选带(只在文章容器):选项来自当前范围里实际出现的类型,资讯不单列
const KIND_ORDER = ['paper', 'tutorial', 'release'];
export function kindOptionsFor(mode, sourceFilter) {
  if (mode !== 'article') return [];
  const scoped = ARTICLES.filter((item) => !sourceFilter || item.source_id === sourceFilter);
  return KIND_ORDER
    .map((kind) => scoped.find((item) => item.kind === kind))
    .filter(Boolean)
    .map((item) => ({ kind: item.kind, label: item.kind_label }));
}

export function listTitleOf(sourceFilter) {
  return sourceFilter ? (SOURCE_NAME_MAP[sourceFilter] || sourceFilter) : '订阅';
}

export function emptyListText({ mode, favOnly, unreadOnly, sourceFilter, kindFilter, searching }) {
  if (searching) return '没有匹配的动态';
  if (favOnly) return '当前范围还没有收藏，阅读时点星标即可收藏';
  if (unreadOnly) return '没有未读内容，都看完啦';
  if (sourceFilter) return '该来源暂无内容';
  if (kindFilter) return '当前范围没有这一类内容';
  return mode === 'podcast' ? '暂无播客' : mode === 'social' ? '暂无动态' : '暂无文章';
}

export function neighborsOf(items, id) {
  const index = items.findIndex((item) => item.id === id);
  return {
    index,
    prev: index > 0 ? items[index - 1] : null,
    next: index >= 0 && index < items.length - 1 ? items[index + 1] : null,
  };
}
