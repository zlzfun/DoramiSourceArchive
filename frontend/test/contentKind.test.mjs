import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isCompactKind,
  kindChipLabel,
  kindFilterOptions,
  kindKickerLabel,
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
