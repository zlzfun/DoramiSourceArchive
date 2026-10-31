// 条目列表实例(issue #230 阶段 3 批 3a):主列表与订阅 Tab 推入页是两个实例,查询与窗口互不改写;
// 「对一篇 / 一个源」的改动经实例登记处同步。React 外壳(hooks/useArticleList、MobileSubsFlow)由沙箱 Playwright 走查。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {
  buildListFilters,
  createListRegistry,
  markReadMatcher,
  patchListItems,
  pushListQuery,
  pushMarkReadPlan,
  pushMode,
  pushSpecKey,
  pushViewItems,
  withoutItem,
} from '../src/utils/articleList.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const shapeOf = (sid) => ({ pod: 'podcast', x_karpathy: 'social' }[sid] || 'article');

// 内容 Tab 的主列表查询(订阅聚合 + 只看未读开着)
const MAIN = Object.freeze({
  activeSourceId: null, activeTagId: null, mode: 'article',
  scope: Object.freeze({ axis: 'subscribed', favorite: false }),
  tagSearch: null, searchQuery: '', unreadOnly: true, kindFilter: 'paper',
});

test('拼装:主列表 = 当前容器的订阅聚合 + 读态 + 类型', () => {
  assert.deepEqual(buildListFilters(MAIN), {
    shape: 'article', subscribed_scope: 'only', with_unread: 'true', with_interest: 'true', unread_only: 'true', kind: 'paper',
  });
});

test('推入页查询:全部 / 收藏 / 来源 / 兴趣 / 单个兴趣各自成形,不带搜索与类型筛选', () => {
  const all = buildListFilters(pushListQuery({ kind: 'all' }, { mode: 'podcast' }));
  assert.deepEqual(all, { shape: 'podcast', subscribed_scope: 'only', with_unread: 'true', with_interest: 'true' });
  const fav = buildListFilters(pushListQuery({ kind: 'fav' }, { mode: 'social', unreadOnly: true }));
  assert.equal(fav.favorite_scope, 'only');
  assert.equal(fav.shape, 'social');
  assert.equal(fav.unread_only, 'true');
  const src = buildListFilters(pushListQuery({ kind: 'source', id: 'pod' }, { mode: pushMode({ kind: 'source', id: 'pod' }, 'article', shapeOf) }));
  assert.equal(src.source_id, 'pod');
  assert.equal(src.shape, undefined, '单源不再按容器分流');
  assert.equal(src.subscribed_scope, 'off', '未订阅的预览源也能看');
  const interest = buildListFilters(pushListQuery({ kind: 'interest' }, { mode: 'article' }));
  assert.equal(interest.interest_scope, 'only');
  assert.equal(interest.subscribed_scope, 'off');
  assert.equal(interest.interest_tag_id, undefined);
  const tag = buildListFilters(pushListQuery({ kind: 'tag', id: 42 }, { mode: 'podcast' }));
  assert.equal(tag.interest_tag_id, 42);
  for (const filters of [all, fav, src, interest, tag]) {
    assert.equal(filters.search, undefined);
    assert.equal(filters.kind, undefined);
  }
});

test('两个实例独立:推入页换视图 / 只看未读 / 换来源只换它自己的查询,主列表查询原样', () => {
  const before = JSON.stringify(MAIN);
  const a = pushListQuery({ kind: 'all' }, { mode: 'article' });
  const b = pushListQuery({ kind: 'all' }, { mode: 'social', unreadOnly: true });
  const c = pushListQuery({ kind: 'source', id: 'x_karpathy' }, { mode: pushMode({ kind: 'source', id: 'x_karpathy' }, 'article', shapeOf) });
  assert.notEqual(a, b);
  assert.equal(a.unreadOnly, false, '推入页的只看未读从关着开始,不继承内容 Tab');
  assert.equal(b.mode, 'social');
  assert.equal(c.mode, 'social', '来源页的形态是来源自己的');
  assert.equal(JSON.stringify(MAIN), before, '主列表查询未被改写');
  assert.notEqual(a.scope, MAIN.scope);
});

test('推入页转盘:全部 / 收藏三项,兴趣两项(社交没有标签),来源页没有;越界视图落第一项', () => {
  assert.deepEqual(pushViewItems('all').map(([v]) => v), ['article', 'podcast', 'social']);
  assert.deepEqual(pushViewItems('fav').map(([v]) => v), ['article', 'podcast', 'social']);
  assert.deepEqual(pushViewItems('tag').map(([v]) => v), ['article', 'podcast']);
  assert.equal(pushViewItems('source'), null);
  assert.equal(pushMode({ kind: 'interest' }, 'social', shapeOf), 'article');
  assert.equal(pushMode({ kind: 'fav' }, 'social', shapeOf), 'social');
  assert.equal(pushMode({ kind: 'source', id: 'pod' }, 'social', shapeOf), 'podcast');
});

test('推入页全部标读:来源按源、全部按容器、兴趣逐篇;收藏页没有这枚钮', () => {
  assert.deepEqual(pushMarkReadPlan({ kind: 'source', id: 's1' }, 'article'), { sourceId: 's1', shape: null, interest: false, tagId: null });
  assert.deepEqual(pushMarkReadPlan({ kind: 'all' }, 'podcast'), { sourceId: null, shape: 'podcast', interest: false, tagId: null });
  assert.deepEqual(pushMarkReadPlan({ kind: 'tag', id: 7 }, 'article'), { sourceId: null, shape: 'article', interest: true, tagId: 7 });
  assert.equal(pushMarkReadPlan({ kind: 'interest' }, 'article').interest, true);
  assert.equal(pushMarkReadPlan({ kind: 'fav' }, 'article'), null);
  assert.equal(pushSpecKey({ kind: 'source', id: 's1' }), 'source:s1');
  assert.equal(pushSpecKey({ kind: 'all' }), 'all:');
});

test('窗口就地改动:没命中时原样返回(免一次重渲染),命中只换那一条', () => {
  const items = [{ id: 1, t: 'a' }, { id: 2, t: 'b' }];
  assert.equal(patchListItems(items, 3, (x) => ({ ...x, t: 'z' })), items);
  assert.equal(patchListItems(items, 1, (x) => x), items);
  const next = patchListItems(items, 2, (x) => ({ ...x, t: 'z' }));
  assert.notEqual(next, items);
  assert.equal(next[0], items[0]);
  assert.equal(next[1].t, 'z');
  assert.equal(withoutItem(items, 9), items);
  assert.deepEqual(withoutItem(items, 1).map((x) => x.id), [2]);
});

// 一个假实例:窗口就是一份普通状态,setter 接受 updater(与 React useState 同形)
function fakeList(query, articles) {
  const state = { query, articles, total: articles.length, reloads: 0, refreshes: 0 };
  return {
    state,
    entry: {
      query: () => state.query,
      getArticles: () => state.articles,
      setArticles: (next) => { state.articles = typeof next === 'function' ? next(state.articles) : next; },
      setArticlesTotal: (next) => { state.total = typeof next === 'function' ? next(state.total) : next; },
      reload: () => { state.reloads += 1; },
      refreshReadState: () => { state.refreshes += 1; return Promise.resolve(new Map()); },
    },
  };
}

test('登记处:同一篇的回读在每个实例里就地更新;注销后不再波及', () => {
  const registry = createListRegistry();
  const all = fakeList(pushListQuery({ kind: 'all' }, { mode: 'article' }), [{ id: 'a1', source_id: 's1' }, { id: 'a2', source_id: 's2' }]);
  const src = fakeList(pushListQuery({ kind: 'source', id: 's1' }, { mode: 'article' }), [{ id: 'a1', source_id: 's1' }]);
  const offAll = registry.add(all.entry);
  registry.add(src.entry);
  assert.equal(registry.size(), 2);
  registry.patch('a1', (x) => ({ ...x, title_zh: '中文' }));
  assert.equal(all.state.articles[0].title_zh, '中文');
  assert.equal(src.state.articles[0].title_zh, '中文');
  assert.equal(all.state.articles[1].title_zh, undefined);
  offAll();
  registry.patch('a2', (x) => ({ ...x, title_zh: '二' }));
  assert.equal(all.state.articles[1].title_zh, undefined, '注销的实例不再被改');
  assert.equal(registry.size(), 1);
});

test('登记处:取消收藏只把这一篇移出「收藏」实例(总数 −1),其它实例照旧', () => {
  const registry = createListRegistry();
  const fav = fakeList(pushListQuery({ kind: 'fav' }, { mode: 'article' }), [{ id: 'a1' }, { id: 'a2' }]);
  const all = fakeList(pushListQuery({ kind: 'all' }, { mode: 'article' }), [{ id: 'a1' }, { id: 'a2' }]);
  registry.add(fav.entry);
  registry.add(all.entry);
  registry.unfavorite('a1');
  assert.deepEqual(fav.state.articles.map((x) => x.id), ['a2']);
  assert.equal(fav.state.total, 1);
  assert.deepEqual(all.state.articles.map((x) => x.id), ['a1', 'a2']);
  assert.equal(all.state.total, 2);
  registry.unfavorite('zz');
  assert.equal(fav.state.total, 1, '窗口里没有的那篇不扣总数');
});

test('登记处:按源标读收集各实例里该源的条目,只看未读的实例重拉', () => {
  const registry = createListRegistry();
  const unread = fakeList(pushListQuery({ kind: 'all' }, { mode: 'article', unreadOnly: true }), [{ id: 'a1', source_id: 's1' }, { id: 'a2', source_id: 's2' }]);
  const plain = fakeList(pushListQuery({ kind: 'source', id: 's1' }, { mode: 'article' }), [{ id: 'a3', source_id: 's1' }]);
  registry.add(unread.entry);
  registry.add(plain.entry);
  assert.deepEqual(registry.idsOfSource('s1').sort(), ['a1', 'a3']);
  registry.reloadUnreadOnly();
  assert.equal(unread.state.reloads, 1);
  assert.equal(plain.state.reloads, 0);
});

test('全部标读的范围匹配器(codex 检视第 1 轮 P1-1):来源 = 同源;订阅全集 = 已订阅且形态相符;兴趣轴不做前端匹配', () => {
  const subscribedIds = new Set(['s1', 'pod']);
  const ctx = { subscribedIds, shapeOf };
  const items = [
    { id: 'a1', source_id: 's1' },
    { id: 'a2', source_id: 's2' },          // 未订阅
    { id: 'p1', source_id: 'pod' },         // 播客形态
    { id: 'x1' },                           // 没有来源
  ];
  const pick = (plan) => items.filter(markReadMatcher(plan, ctx)).map((x) => x.id);
  assert.deepEqual(pick(pushMarkReadPlan({ kind: 'source', id: 's2' }, 'article')), ['a2'], '来源:未订阅的预览源也按源推水位');
  assert.deepEqual(pick(pushMarkReadPlan({ kind: 'all' }, 'article')), ['a1']);
  assert.deepEqual(pick(pushMarkReadPlan({ kind: 'all' }, 'podcast')), ['p1']);
  assert.deepEqual(pick({ sourceId: null, shape: null, interest: false }), ['a1', 'p1'], '不带形态 = 全部订阅');
  assert.equal(markReadMatcher(pushMarkReadPlan({ kind: 'tag', id: 't1' }, 'article'), ctx), null);
  assert.equal(markReadMatcher(pushMarkReadPlan({ kind: 'interest' }, 'article'), ctx), null);
  assert.equal(markReadMatcher(null, ctx), null);
});

test('登记处:按匹配器收集各实例条目;读态静默校准只发给不在只看未读的实例', async () => {
  const registry = createListRegistry();
  const unread = fakeList(pushListQuery({ kind: 'all' }, { mode: 'article', unreadOnly: true }), [{ id: 'a1', source_id: 's1' }]);
  const plain = fakeList(pushListQuery({ kind: 'tag', id: 't1' }, { mode: 'article' }), [{ id: 'a2', source_id: 's2' }, { id: 'a3', source_id: 's1' }]);
  registry.add(unread.entry);
  registry.add(plain.entry);
  assert.deepEqual(registry.idsWhere((x) => x.source_id === 's1').sort(), ['a1', 'a3']);
  assert.deepEqual(registry.idsOfSource('s2'), ['a2'], 'idsOfSource 仍可用(按源标读)');
  const jobs = registry.refreshReadState();
  assert.equal(jobs.length, 1);
  await Promise.all(jobs);
  assert.equal(plain.state.refreshes, 1);
  assert.equal(unread.state.refreshes, 0, '只看未读的实例由 reloadUnreadOnly 重拉,不重复校准');
});

test('源码护栏:推入页只用自己的列表实例,不调主列表的导航(否则会改写内容 Tab)', () => {
  const flow = readFileSync(path.join(here, '../src/components/mobile/MobileSubsFlow.jsx'), 'utf8');
  assert.match(flow, /useArticleList\(/);
  for (const nav of ['goSource', 'goTag', 'goAll', 'goFavorites', 'goView', 'setAxis', 'toggleFavoriteScope', 'setUnreadOnly } = rs', 'rs.setUnreadOnly']) {
    assert.ok(!flow.includes(nav), `MobileSubsFlow 不应调用 ${nav}`);
  }
  const reader = readFileSync(path.join(here, '../src/components/mobile/MobileReader.jsx'), 'utf8');
  assert.ok(!/m-scope-chip/.test(reader), '内容 Tab 列表顶不再有筛选胶囊(批 3a)');
  const state = readFileSync(path.join(here, '../src/hooks/useReaderState.js'), 'utf8');
  assert.match(state, /useListLoader\(listWindow/, '主列表与推入页走同一个列表 hook');
});
