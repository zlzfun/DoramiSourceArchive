// 条目列表实例的纯逻辑(issue #230 阶段 3 批 3a):一条列表 = 一份查询 + 一扇窗口(已载入的条目与总数)。
// 阅读器的主列表(内容 Tab / 桌面条目列)与手机订阅 Tab 的推入页(来源 / 全部 / 收藏 / 兴趣)各是一个实例,
// 走同一个 hooks/useArticleList;本模块放两边共用、可在 node 里单测的部分:
//   · buildListFilters —— 查询 → GET /api/articles 参数的单一拼装点(首拉、翻页、分析轮询共用);
//   · pushListQuery    —— 推入页的查询(只由推入页自己的 spec / 视图 / 只看未读决定,不读主列表);
//   · createListRegistry —— 主列表之外的实例登记处:正文回读、收藏、按源标读这类「对一篇 / 一个源」的改动
//     由数据层一次广播到所有实例,各实例的查询与窗口互不改写。

export const LIST_PAGE_SIZE = 30;

const SUBSCRIBED = Object.freeze({ axis: 'subscribed', favorite: false });
const FAVORITES = Object.freeze({ axis: 'subscribed', favorite: true });
const INTEREST = Object.freeze({ axis: 'interest', favorite: false });

// 列表请求参数的单一拼装点:轴 + 下钻项 + 收藏 + 容器形态 + 读态 + 搜索(列表首拉与分析轮询共用)
export function buildListFilters({ activeSourceId, activeTagId, mode, scope, tagSearch, searchQuery, unreadOnly, kindFilter }) {
  const filters = {};
  if (activeSourceId) filters.source_id = activeSourceId;
  else filters.shape = mode; // 容器分流(文章/播客/社交各取自己那类)
  if (scope.axis === 'interest') {
    // 兴趣轴 = 全站可见源 ∩ 命中关注标签(下钻时只认那一枚)
    filters.subscribed_scope = 'off';
    filters.interest_scope = 'only';
    if (activeTagId) filters.interest_tag_id = activeTagId;
  } else {
    // 订阅轴:全集 = 订阅源;单源 = 该源(未订阅的预览源也要能看,故 off)
    filters.subscribed_scope = activeSourceId ? 'off' : 'only';
  }
  if (scope.favorite) filters.favorite_scope = 'only';
  if (tagSearch) Object.assign(filters, tagSearch.filters);
  else if (searchQuery) filters.search = searchQuery;
  filters.with_unread = 'true';    // 条目附页级未读标记(水位由 unread-counts 校准)
  filters.with_interest = 'true';  // 条目附命中的兴趣标签名(命中胶囊)
  if (unreadOnly) filters.unread_only = 'true';
  if (kindFilter) filters.kind = kindFilter; // 内容类型筛选(issue #178),单选
  return filters;
}

// ── 推入页(手机订阅 Tab;样页「来源流」)──
// spec.kind:source(单个来源)· all(订阅全集,拍板 24)· fav(收藏)· interest(全部兴趣内容,全站)· tag(单个兴趣)
const VIEW_ITEMS = [['article', '文章'], ['podcast', '播客'], ['social', '社交']];
// 兴趣轴只在文章 / 播客容器上有(社交没有标签),兴趣页的转盘只放这两项
const INTEREST_VIEW_ITEMS = VIEW_ITEMS.slice(0, 2);

export function pushViewItems(kind) {
  if (kind === 'all' || kind === 'fav') return VIEW_ITEMS;
  if (kind === 'interest' || kind === 'tag') return INTEREST_VIEW_ITEMS;
  return null;   // 来源页的形态就是来源自己的,不切
}

// 推入页的容器形态:来源页 = 来源所属形态;其余 = 转盘当前视图
export function pushMode(spec, view, shapeOf) {
  if (spec?.kind === 'source') return shapeOf ? shapeOf(spec.id) : 'article';
  const items = pushViewItems(spec?.kind) || VIEW_ITEMS;
  return items.some(([value]) => value === view) ? view : items[0][0];
}

// 推入页的查询:只由 spec / 形态 / 只看未读决定;不带搜索与类型筛选(样页推入页没有这两样)
export function pushListQuery(spec, { mode, unreadOnly = false }) {
  const kind = spec?.kind;
  return {
    activeSourceId: kind === 'source' ? spec.id : null,
    activeTagId: kind === 'tag' ? spec.id : null,
    mode,
    scope: kind === 'fav' ? FAVORITES : kind === 'interest' || kind === 'tag' ? INTEREST : SUBSCRIBED,
    tagSearch: null,
    searchQuery: '',
    unreadOnly: Boolean(unreadOnly),
    kindFilter: '',
  };
}

// 推入页「全部标读」的写路(与主列表同口径:订阅轴按源 / 容器推水位,兴趣轴逐篇写行);
// 收藏页返回 null —— 后端没有「收藏范围」的标读,推水位会把没收藏的一并标掉,越出读者看见的范围,故不出这枚钮。
export function pushMarkReadPlan(spec, mode) {
  switch (spec?.kind) {
    case 'source': return { sourceId: spec.id, shape: null, interest: false, tagId: null };
    case 'all': return { sourceId: null, shape: mode, interest: false, tagId: null };
    case 'interest': return { sourceId: null, shape: mode, interest: true, tagId: null };
    case 'tag': return { sourceId: null, shape: mode, interest: true, tagId: spec.id };
    default: return null;
  }
}

// 「全部标读」的范围在各列表实例里认哪些条目(codex 检视第 1 轮 P1):与后端同口径——来源 plan 推该源水位;订阅全集 plan
// 推「已订阅且形态相符」各源的水位(mark_all_read 的源集合)。兴趣 plan 返回 null:后端逐篇写行、批次封顶(has_more),
// 前端不知道实际写了哪些,不做乐观清点,交给各实例按服务端读态静默校准。
export function markReadMatcher(plan, { subscribedIds, shapeOf }) {
  if (!plan || plan.interest) return null;
  if (plan.sourceId) return (item) => item?.source_id === plan.sourceId;
  return (item) => Boolean(item?.source_id) && subscribedIds.has(item.source_id)
    && (!plan.shape || shapeOf(item.source_id) === plan.shape);
}

// 推入页实例的身份:换一个来源 / 兴趣就是新实例(状态从头来,与样页「每次推入都是新的一页」同)
export const pushSpecKey = (spec) => (spec ? `${spec.kind}:${spec.id ?? ''}` : '');

// ── 窗口的就地改动(不改顺序、不改总数;没有命中的条目时原样返回,调用方据此免一次重渲染)──
export function patchListItems(items, id, apply) {
  let changed = false;
  const next = items.map((item) => {
    if (item?.id !== id) return item;
    const patched = apply(item);
    if (patched !== item) changed = true;
    return patched;
  });
  return changed ? next : items;
}

export function withoutItem(items, id) {
  return items.some((item) => item?.id === id) ? items.filter((item) => item?.id !== id) : items;
}

// ── 实例登记处 ──
// 每个实例登记一份入口:{ query(), getArticles(), setArticles(updater), setArticlesTotal(updater), reload(), refreshReadState() }。
// 数据层只经它做「对一篇 / 一个源」的改动,从不改写实例的查询,也不碰实例的滚动位置。
export function createListRegistry() {
  const lists = new Set();
  return {
    add(entry) {
      lists.add(entry);
      return () => { lists.delete(entry); };
    },
    size: () => lists.size,
    // 正文回读 / 摘要 / 中文标题回填:同一篇在每个实例里就地更新
    patch(id, apply) {
      if (!id) return;
      lists.forEach((list) => list.setArticles((prev) => patchListItems(prev, id, apply)));
    },
    // 取消收藏:只有「收藏」实例把这一篇移出窗口(总数随之 −1);其余实例照旧显示
    unfavorite(id) {
      if (!id) return;
      lists.forEach((list) => {
        if (!list.query()?.scope?.favorite) return;
        if (!list.getArticles().some((item) => item?.id === id)) return;
        list.setArticles((prev) => withoutItem(prev, id));
        list.setArticlesTotal((total) => Math.max(0, total - 1));
      });
    },
    // 全部标读:各实例里命中范围的条目 id(调用方据此写本会话读态覆盖);只看未读的实例随后重拉
    idsWhere(match) {
      const ids = [];
      lists.forEach((list) => {
        for (const item of list.getArticles()) if (match(item)) ids.push(item.id);
      });
      return ids;
    },
    idsOfSource(sourceId) {
      return this.idsWhere((item) => item?.source_id === sourceId);
    },
    reloadUnreadOnly() {
      lists.forEach((list) => { if (list.query()?.unreadOnly) list.reload(); });
    },
    // 兴趣轴标读后:不在只看未读的实例按原窗口静默重取读态(只看未读的由 reloadUnreadOnly 重拉);
    // 返回各实例的 Promise<Map<id, unread>>
    refreshReadState() {
      const jobs = [];
      lists.forEach((list) => { if (!list.query()?.unreadOnly) jobs.push(list.refreshReadState()); });
      return jobs;
    },
  };
}
