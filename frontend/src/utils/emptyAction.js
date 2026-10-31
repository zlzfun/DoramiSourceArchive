// 空态指向第一个动作(审计 M-41;conventions §1「空状态要告诉读者下一步」;issue #230 批 4a):
// 手机内容 Tab 的列表 / 社交流为空时,按「为什么是空的」挑一个就地能做的动作——
// 搜索没结果 → 清除搜索;只看未读 → 显示全部;按类型筛 → 显示全部类型;这个视图本来就没内容 → 去发现页找来源。
export const EMPTY_ACTION_LABELS = Object.freeze({
  clearSearch: '清除搜索',
  showAll: '显示全部',
  allKinds: '显示全部类型',
  discover: '发现更多来源',
  discoverSocial: '发现社交账号',
});

export function emptyActionKind({ searchQuery = '', unreadOnly = false, kindFilter = '', noSources = false } = {}) {
  if (searchQuery) return 'clearSearch';
  if (unreadOnly) return 'showAll';
  if (kindFilter) return 'allKinds';
  return noSources ? 'discoverSocial' : 'discover';
}
