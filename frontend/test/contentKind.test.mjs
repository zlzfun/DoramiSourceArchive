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
  polledKindChanges,
  polledMembershipChanged,
  reconcileKindChoice,
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

test('轮询补位:筛选下在途项不在满额响应里即判为成员变化,交给整窗重拉(codex R1 #4 / R2-2)', () => {
  // 教程筛选,同范围 31 篇,首屏 30 篇含在途的 A;A 改判观点后第 31 篇补位,响应仍是满额 30 篇
  const loaded = Array.from({ length: 30 }, (_, i) => ({ id: `t${i}`, kind: 'tutorial' }));
  loaded[5] = { id: 'A', kind: 'tutorial', analysis_status: 'pending' };
  const inFlight = new Set(['A']);
  const polled = new Set([...loaded.map((a) => a.id).filter((id) => id !== 'A'), 't30']);
  assert.equal(polledMembershipChanged(loaded, inFlight, polled, { kindFilter: 'tutorial' }), true);
  // 先翻页拿到了服务端新总数、再轮询,判定不变——总数不在本地扣,由快照给出
  assert.equal(polledMembershipChanged(loaded, inFlight, polled, { tagSearch: true }), true);
  // 不带筛选时分析结果不影响成员
  assert.equal(polledMembershipChanged(loaded, inFlight, polled, {}), false);
  // 在途项都在响应里:无成员变化
  assert.equal(polledMembershipChanged(loaded, inFlight, new Set(loaded.map((a) => a.id)), { kindFilter: 'tutorial' }), false);
});

test('首次分析完成:未判定 → 观点,观点计数从 0 变 1,筛选选项随之出现', () => {
  const loaded = [{ id: 'B', kind: null, analysis_status: 'pending' }, { id: 'C', kind: 'paper' }];
  const changes = polledKindChanges(loaded, new Map([['B', { id: 'B', kind: 'opinion' }], ['C', { id: 'C', kind: 'paper' }]]));
  assert.deepEqual(changes, [{ from: '', to: 'opinion' }]);
  const next = applyKindFacetDelta(FACETS, changes);
  assert.deepEqual(kindFilterOptions(next).map((o) => o.kind), ['paper', 'tutorial', 'opinion']);
  assert.equal(applyKindFacetDelta(FACETS, []), FACETS, '无变化原样返回');
  // 改判:教程 → 观点,两边各加减一
  const moved = applyKindFacetDelta(FACETS, [{ from: 'tutorial', to: 'opinion' }]);
  assert.deepEqual(moved.map((f) => f.count), [2, 30, 1]);
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
