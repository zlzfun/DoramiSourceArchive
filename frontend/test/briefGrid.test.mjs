// 早报网格规划器的回归用例(issue #74,codex 检视 P3):项目没有前端测试框架,用 Node 内建 node:test 直跑,
// `npm test` 接入 CI。固定样页「同一张数换分数」画板里的场景 + 边界,再穷举一遍不变量:
// 输出长度 = 输入长度、span 只取 2/3/4/6、逐行 span 和恒为 6(没有残行,也没有溢出)。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GRID_UNITS, gridColsFor, planSectionSpans, scoreOf, skeletonSpans } from '../src/utils/briefGrid.js';

const rowsOf = (spans) => {
  const rows = [];
  let row = [];
  let acc = 0;
  for (const span of spans) {
    row.push(span);
    acc += span;
    assert.ok(acc <= GRID_UNITS, `row overflow: ${JSON.stringify(spans)}`);
    if (acc === GRID_UNITS) { rows.push(row); row = []; acc = 0; }
  }
  assert.equal(row.length, 0, `ragged tail: ${JSON.stringify(spans)}`);
  return rows;
};

test('三列:样页场景与阈值边界', () => {
  const cases = [
    [[], []],
    [[7], [6]],
    [[null], [6]],
    [[9.2, 8.8], [6, 6]],
    [[8.4, 7.1], [4, 2]],
    [[8.0, 7.8], [3, 3]],
    [[8.0, 7.0], [4, 2]],       // 分差恰为 Δ = 1.0 → ⅔ + ⅓
    [[8.0, 7.01], [3, 3]],      // 差一点点 → 各半
    [[9.0, 7.0], [6, 6]],       // 恰到 T = 9.0 → 通栏,剩下单卡也通栏
    [[8.9, 7.0], [4, 2]],
    [[9.1, 7.4, 7.2, 6.8], [6, 2, 2, 2]],   // 头卡过绝对线
    [[8.4, 7.1, 6.9, 6.2], [6, 2, 2, 2]],   // 头卡相对够高
    [[8.2, 8.0, 7.4, 6.6], [3, 3, 3, 3]],   // 咬得紧 → 2 + 2 各半
    [[8.0, 7.6, 7.5, 6.3], [3, 3, 4, 2]],   // 2 + 2,第二行拉开
    [[8.6, 7.2, 7.0, 6.8, 6.5], [4, 2, 2, 2, 2]],   // 余 2,头对拉开
    [[7.9, 7.6, 7.2, 6.9, 6.4], [3, 3, 2, 2, 2]],   // 余 2,头对咬紧
    [[7.0, 6.0, 5.0], [2, 2, 2]],
    [[7.0, 6.0, 5.0, 4.0, 3.0, 2.0], [2, 2, 2, 2, 2, 2]],
    [[8.9, 7.7, 7.5, 7.1, 6.8, 6.6, 6.2], [6, 2, 2, 2, 2, 2, 2]],
    [[9.5, 9.2, 9.0, 7], [6, 6, 6, 6]],     // 连续 9+ 逐张通栏
    [[null, null, 7, null], [3, 3, 4, 2]],  // 缺分永远是矮的一方
    [['', Number.NaN, '7.5', 6], [3, 3, 4, 2]], // 空串 / NaN 按缺分,数字串可解析
  ];
  for (const [scores, expected] of cases) {
    assert.deepEqual(planSectionSpans(scores, 3), expected, JSON.stringify(scores));
  }
});

test('两列只有通栏与各半,单列全通栏', () => {
  assert.deepEqual(planSectionSpans([9.1, 7.4, 7.2, 6.8], 2), [6, 6, 3, 3]);
  assert.deepEqual(planSectionSpans([8.4, 7.1, 6.9], 2), [6, 3, 3]);
  assert.deepEqual(planSectionSpans([8.4, 7.1], 2), [3, 3]);   // 两列不用 ⅔ + ⅓
  assert.deepEqual(planSectionSpans([8.4, 7.1, 6.9], 1), [6, 6, 6]);
  assert.deepEqual(planSectionSpans([], 1), []);
});

test('阈值可注入', () => {
  // gap 压到 0.1:余 1 时头卡 8.2 − 8.0 = 0.2 已够「相对通栏」;余 2 时头对拉开成 ⅔ + ⅓
  assert.deepEqual(planSectionSpans([8.2, 8.0, 7.4, 6.6], 3, { gap: 0.1 }), [6, 2, 2, 2]);
  assert.deepEqual(planSectionSpans([8.2, 8.0, 7.4, 6.6, 6.0], 3, { gap: 0.1 }), [4, 2, 2, 2, 2]);
  assert.deepEqual(planSectionSpans([8.2, 8.0, 7.4, 6.6], 3, { solo: 8.0 }), [6, 6, 3, 3]);
});

test('不变量穷举:长度守恒、span 取值、逐行和恒为 6', () => {
  const pool = [10, 9, 8.9, 8, 7, null];
  const allowed = { 1: [6], 2: [3, 6], 3: [2, 3, 4, 6] };
  let checked = 0;
  for (const cols of [1, 2, 3]) {
    for (let n = 0; n <= 6; n += 1) {
      const total = pool.length ** n;
      for (let code = 0; code < total; code += 1) {
        const scores = [];
        let rest = code;
        for (let i = 0; i < n; i += 1) { scores.push(pool[rest % pool.length]); rest = Math.floor(rest / pool.length); }
        const spans = planSectionSpans(scores, cols);
        assert.equal(spans.length, n);
        for (const span of spans) assert.ok(allowed[cols].includes(span), `${cols} cols got span ${span}`);
        rowsOf(spans);
        checked += 1;
      }
    }
  }
  assert.ok(checked > 100000);
});

test('列数断点与骨架屏', () => {
  assert.deepEqual([988, 987, 654, 653, 0, Number.NaN, undefined].map(gridColsFor), [3, 2, 2, 1, 1, 1, 1]);
  assert.deepEqual([3, 2, 1].map(skeletonSpans), [[6, 2, 2, 2], [6, 3, 3], [6, 6]]);
});

test('scoreOf:缺分是缺席不是 0 分', () => {
  assert.equal(scoreOf({ quality_score: 7.5 }), 7.5);
  assert.equal(scoreOf({ snapshot: { quality_score: '6' } }), 6);
  assert.equal(scoreOf({ quality_score: null, snapshot: { quality_score: 8 } }), 8);
  assert.equal(scoreOf({ quality_score: '' }), null);
  assert.equal(scoreOf({ quality_score: 'abc' }), null);
  assert.equal(scoreOf({}), null);
  assert.equal(scoreOf(null), null);
});
