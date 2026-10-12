import test from 'node:test';
import assert from 'node:assert/strict';
import {
  EMPTY_KIND_CHOICE,
  applyKindFacetDelta,
  applyPolledFacetDelta,
  isCompactKind,
  kindChipLabel,
  kindFilterFor,
  kindFilterOptions,
  kindKickerLabel,
  reconcileKindChoice,
  reconcilePolledKinds,
} from '../src/utils/contentKind.js';
import { readerShapeOf } from '../src/utils/readerShape.js';

test('资讯与未判定的条目不挂类型签', () => {
  assert.equal(kindChipLabel({ kind: 'news', kind_label: '资讯' }), '');
  assert.equal(kindChipLabel({ kind: null }), '');
  assert.equal(kindChipLabel(null), '');
  assert.equal(kindChipLabel({ kind: 'paper', kind_label: '论文' }), '论文');
});

test('只有版本更新用紧凑行', () => {
  assert.equal(isCompactKind({ kind: 'release' }), true);
  assert.equal(isCompactKind({ kind: 'project' }), false);
  assert.equal(isCompactKind({ kind: 'news' }), false);
  assert.equal(isCompactKind(undefined), false);
});

test('眉头:非资讯写类型,资讯写事件,都没有留空', () => {
  assert.equal(kindKickerLabel({ kind: 'paper', kind_label: '论文', event_label: null }), '论文');
  assert.equal(kindKickerLabel({ kind: 'release', kind_label: '版本更新', event_label: '产品更新' }), '版本更新');
  assert.equal(kindKickerLabel({ kind: 'news', kind_label: '资讯', event_label: '模型发布' }), '模型发布');
  assert.equal(kindKickerLabel({ kind: 'news', kind_label: '资讯', event_label: null }), '');
});

test('筛选条只列计数大于 0 的类型,保持分面顺序', () => {
  const facets = [
    { kind: 'paper', label: '论文', count: 3 },
    { kind: 'tutorial', label: '教程', count: 0 },
    { kind: 'release', label: '版本更新', count: '2' },
    { kind: 'news', label: '资讯', count: 9 },
  ];
  assert.deepEqual(kindFilterOptions(facets), [
    { kind: 'paper', label: '论文', count: 3 },
    { kind: 'release', label: '版本更新', count: 2 },
  ]);
  assert.deepEqual(kindFilterOptions(undefined), []);
  // 选中项计数归零时仍保留,读者能看见并取消它
  assert.deepEqual(
    kindFilterOptions(facets, 'tutorial').map((o) => o.kind),
    ['paper', 'tutorial', 'release'],
  );
});

test('旧的「动态」形态折叠进文章容器', () => {
  assert.equal(readerShapeOf({ shape: 'bulletin' }), 'article');
  assert.equal(readerShapeOf('podcast'), 'podcast');
  assert.equal(readerShapeOf({ shape: 'social' }), 'social');
  assert.equal(readerShapeOf({}), 'article');
});

// 模拟 hook 每次渲染做的两步:先按当前作用域收口选中值,再派生生效的筛选
function render(choice, scopeKey) {
  const next = reconcileKindChoice(choice, scopeKey);
  return { choice: next, filter: kindFilterFor(choice, scopeKey) };
}

test('类型筛选离开作用域即清除,返回原作用域不复活(codex R1 #3)', () => {
  const A = '["article",null,null,"subscribed",false]';
  const B = '["article","src-b",null,"subscribed",false]';
  const favA = '["article",null,null,"subscribed",true]';
  let state = { key: A, kind: 'paper' };
  assert.equal(render(state, A).filter, 'paper');
  assert.equal(render(state, A).choice, state, '作用域未变:原样返回,不触发 setState');

  // 切到来源 B:同一帧派生为空(不会先带旧类型请求 B),并清除选中值
  let frame = render(state, B);
  assert.equal(frame.filter, '');
  state = frame.choice;
  assert.equal(state, EMPTY_KIND_CHOICE);
  // 返回聚合 A:旧筛选不复活
  assert.equal(render(state, A).filter, '');

  // 开收藏再关收藏同理
  state = { key: A, kind: 'tutorial' };
  state = render(state, favA).choice;
  assert.equal(render(state, A).filter, '');
});

const FACETS = [
  { kind: 'paper', label: '论文', count: 2 },
  { kind: 'tutorial', label: '教程', count: 31 },
  { kind: 'opinion', label: '观点', count: 0 },
];

test('轮询补位:响应满额时改判的在途项仍被移出,分面同步加减(codex R1 #4)', () => {
  // 教程筛选,同范围 31 篇,首屏 30 篇含在途的 A;A 改判观点后第 31 篇补位,响应仍是 30 篇
  const loaded = Array.from({ length: 30 }, (_, i) => ({ id: `t${i}`, kind: 'tutorial' }));
  loaded[5] = { id: 'A', kind: 'tutorial', analysis_status: 'pending' };
  const fresh = new Map(loaded.filter((a) => a.id !== 'A').map((a) => [a.id, a]));
  fresh.set('t30', { id: 't30', kind: 'tutorial' }); // 补位进来的第 31 篇
  // 缺失的在途项 A 经补取拿到真实分类
  fresh.set('A', { id: 'A', kind: 'opinion' });

  const { changes, removed } = reconcilePolledKinds(loaded, fresh, 'tutorial');
  assert.deepEqual([...removed], ['A']);
  assert.deepEqual(changes, [{ from: 'tutorial', to: 'opinion' }]);
  const counts = Object.fromEntries(applyKindFacetDelta(FACETS, changes).map((f) => [f.kind, f.count]));
  assert.deepEqual(counts, { paper: 2, tutorial: 30, opinion: 1 });
});

test('轮询:仍匹配、只是被挤出窗口的在途项不移出', () => {
  const loaded = [{ id: 'A', kind: 'tutorial', analysis_status: 'pending' }];
  const { changes, removed } = reconcilePolledKinds(loaded, new Map([['A', { id: 'A', kind: 'tutorial' }]]), 'tutorial');
  assert.equal(removed.size, 0);
  assert.deepEqual(changes, []);
});

test('首次分析完成:未判定 → 观点,观点计数从 0 变 1,筛选选项随之出现', () => {
  const loaded = [{ id: 'B', kind: null, analysis_status: 'pending' }];
  const { changes, removed } = reconcilePolledKinds(loaded, new Map([['B', { id: 'B', kind: 'opinion' }]]), '');
  assert.equal(removed.size, 0, '未筛类型时不移出');
  const next = applyKindFacetDelta(FACETS, changes);
  assert.deepEqual(kindFilterOptions(next).map((o) => o.kind), ['paper', 'tutorial', 'opinion']);
  assert.equal(applyKindFacetDelta(FACETS, []), FACETS, '无变化原样返回');
});

test('分面增量只应用到轮询开始时那一代:同作用域刷新后晚到的旧轮询不再加减', () => {
  const changes = [{ from: 'tutorial', to: 'opinion' }];
  const gen1 = { key: 'K', gen: 1, items: FACETS };
  const applied = applyPolledFacetDelta(gen1, { key: 'K', gen: 1 }, changes);
  assert.equal(applied.items.find((f) => f.kind === 'opinion').count, 1);
  // 手动刷新拿到服务端新计数(已含这次改判),代次 +1;旧轮询的增量被丢弃
  const refreshed = { key: 'K', gen: 2, items: applyKindFacetDelta(FACETS, changes) };
  assert.equal(applyPolledFacetDelta(refreshed, { key: 'K', gen: 1 }, changes), refreshed);
  // 切了作用域同样丢弃
  assert.equal(applyPolledFacetDelta({ ...gen1, key: 'K2' }, { key: 'K', gen: 1 }, changes).items, FACETS);
});
