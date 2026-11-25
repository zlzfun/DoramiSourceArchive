// issue #343:早报页换版里不依赖 React 与 DOM 的几件(utils/briefSwap)——请求先后、换版排程、选择跟随、被换下判定。
// 这些原先只有沙箱脚本在测;交叉检视(第二批 R1)指出的五处状态机问题各有一条在这里。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createRequestOrder, createSoftSwap, followToday, isReplacedView, scopeKeyOf,
} from '../src/utils/briefSwap.js';

// 手动计时器 + 动画替身:到点与否由用例说了算
function harness() {
  const timers = new Map();
  let nextId = 1;
  const log = [];
  const swap = createSoftSwap({
    onLand: () => log.push('land'),
    setTimer: (fn, ms) => { const id = nextId; nextId += 1; timers.set(id, { fn, ms }); return id; },
    clearTimer: (id) => { timers.delete(id); },
  });
  const fire = () => {
    const [[id, timer]] = [...timers];
    timers.delete(id);
    timer.fn();
  };
  let leaves = 0;
  const leave = (duration = 150, tag = 'a') => () => {
    leaves += 1;
    return { animations: [`${tag}${leaves}`], duration };
  };
  return { swap, timers, fire, log, leave, leaveCount: () => leaves };
}

test('请求先后:后发的先回来之后,迟到的旧响应(成功或失败)不再采用', () => {
  const order = createRequestOrder();
  const first = order.begin();
  const second = order.begin();
  // 正常顺序:两份都采用
  assert.equal(order.adopt(first), true);
  assert.equal(order.adopt(second), true);
  // 乱序:第三份发得早、回得晚
  const third = order.begin();
  const fourth = order.begin();
  assert.equal(order.adopt(fourth), true);
  assert.equal(order.current(third), false);   // 失败的旧响应:不该再把页面换成错误
  assert.equal(order.adopt(third), false);     // 成功的旧响应:不该把新版盖回去
  assert.equal(order.current(fourth), true);
  // 之后发出的照常
  assert.equal(order.adopt(order.begin()), true);
});

test('换版排程:旧版面淡出到点才放上新的,然后在那次提交里收尾', () => {
  const h = harness();
  const applied = [];
  h.swap.run('today', () => applied.push('B'), h.leave());
  assert.equal(h.swap.leaving(), true);
  assert.deepEqual(applied, []);
  assert.equal(h.swap.settle(), null);          // 还没放上去:没有可收尾的
  assert.equal([...h.timers.values()][0].ms, 150);
  h.fire();
  assert.deepEqual(applied, ['B']);
  assert.deepEqual(h.log, ['land']);
  assert.equal(h.swap.leaving(), false);
  assert.deepEqual(h.swap.settle(), { animations: ['a1'], applied: 1 });
  assert.equal(h.swap.settle(), null);          // 只收尾一次
});

test('换版排程:淡出期间再来的同一类以最后一份为准,不重新淡出;不同类各留一份', () => {
  const h = harness();
  const applied = [];
  h.swap.run('today', () => applied.push('B'), h.leave());
  h.swap.run('today', () => applied.push('C'), h.leave());
  h.swap.run('view', () => applied.push('unpin'), h.leave());
  h.swap.run('today', () => applied.push('D'), h.leave());
  assert.equal(h.leaveCount(), 1);
  assert.equal(h.timers.size, 1);
  h.fire();
  assert.deepEqual(applied, ['D', 'unpin']);
  assert.deepEqual(h.log, ['land']);
  assert.deepEqual(h.swap.settle(), { animations: ['a1'], applied: 2 });
});

test('换版排程:读者自己切了日期,还没放上去的那一类被撤——淡出照常收尾,但不算放了东西', () => {
  const h = harness();
  const applied = [];
  h.swap.run('view', () => applied.push('unpin'), h.leave());
  h.swap.drop('view');
  h.fire();
  assert.deepEqual(applied, []);
  assert.deepEqual(h.log, ['land']);            // 仍要触发那次提交,好把淡出动画撤掉
  assert.deepEqual(h.swap.settle(), { animations: ['a1'], applied: 0 });
  // 只撤被点名的那一类
  h.swap.run('view', () => applied.push('unpin'), h.leave());
  h.swap.run('today', () => applied.push('B'), h.leave());
  h.swap.drop('view');
  h.fire();
  assert.deepEqual(applied, ['B']);
  assert.equal(h.swap.settle().applied, 1);
  // 已经放上去的撤不掉,也不出错
  h.swap.run('view', () => applied.push('late'), h.leave(0));
  h.swap.drop('view');
  assert.deepEqual(applied, ['B', 'late']);
});

test('换版排程:不必演淡出时(减少动效、窗口在后台)当场放上去', () => {
  const h = harness();
  const applied = [];
  h.swap.run('today', () => applied.push('B'), () => ({ animations: [], duration: 0 }));
  assert.deepEqual(applied, ['B']);
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.swap.settle(), { animations: [], applied: 1 });
  // 没给 leave 也一样
  h.swap.run('today', () => applied.push('C'));
  assert.deepEqual(applied, ['B', 'C']);
  assert.deepEqual(h.swap.settle(), { animations: [], applied: 1 });
});

test('换版排程:上一件刚放上去、那次提交还没到时又来一件——旧的淡出动画交给新的一件撤,不留在场上', () => {
  const h = harness();
  const applied = [];
  h.swap.run('today', () => applied.push('B'), h.leave());
  h.fire();                                     // B 放上去了,但页面还没来得及 settle
  h.swap.run('today', () => applied.push('C'), h.leave());
  assert.equal(h.swap.settle(), null);          // 新的一件还在淡出:这次提交什么都不撤(旧动画已转交)
  h.fire();
  assert.deepEqual(applied, ['B', 'C']);
  assert.deepEqual(h.swap.settle(), { animations: ['a1', 'a2'], applied: 1 });
});

test('换版排程:卸载时清掉计时器并交回还没撤的动画;之后还能再用', () => {
  const h = harness();
  const applied = [];
  h.swap.run('today', () => applied.push('B'), h.leave());
  assert.deepEqual(h.swap.dispose(), ['a1']);
  assert.equal(h.timers.size, 0);               // 到点回调不会再跑
  assert.deepEqual(applied, []);
  assert.equal(h.swap.leaving(), false);
  assert.deepEqual(h.swap.dispose(), []);
  h.swap.run('today', () => applied.push('C'), h.leave());
  h.fire();
  assert.deepEqual(applied, ['C']);
});

test('带着版本号看今天:今天这一版换了,选择跟着换到当前版;别的情况不动', () => {
  const today = (id, revision, status = 'ready') => ({ status, edition: { id, revision, report_date: '2026-11-25' } });
  const pinned = { date: '2026-11-25', revision: 6 };
  // 从正文返回时留的是 6;今天从 6 换成 7 → 跟到当前版
  assert.deepEqual(followToday(pinned, today(60, 6), today(70, 7)), { date: null, revision: null });
  // date 为 null(今天)同样
  assert.deepEqual(followToday({ date: null, revision: 6 }, today(60, 6), today(70, 7)), { date: null, revision: null });
  // 清成空订阅引导也算换了
  assert.deepEqual(followToday(pinned, today(60, 6), { status: 'empty_subscriptions', edition: null }), { date: null, revision: null });
  // 同一版的字段更新:不动(返回原对象,不触发重渲染)
  assert.equal(followToday(pinned, today(60, 6), { ...today(60, 6), refresh_pending: 'soon' }), pinned);
  // 没带版本号、看往期、选中的本来就不是换之前那一版:不动
  const latest = { date: null, revision: null };
  assert.equal(followToday(latest, today(60, 6), today(70, 7)), latest);
  const past = { date: '2026-11-24', revision: 6 };
  assert.equal(followToday(past, today(60, 6), today(70, 7)), past);
  const older = { date: '2026-11-25', revision: 5 };
  assert.equal(followToday(older, today(60, 6), today(70, 7)), older);
  // 这次挂载里第一次取到今天(之前没有可比的):不动,交给「被换下」的判定
  assert.equal(followToday(pinned, null, today(70, 7)), pinned);
});

test('眼前是不是今天一个被换下的版次:按今天的事实判,不看那一版自己的状态', () => {
  const base = { isToday: true, usesToday: false, revision: 6 };
  assert.equal(isReplacedView({ ...base, todayStatus: 'ready' }), true);
  assert.equal(isReplacedView({ ...base, todayStatus: 'degraded' }), true);
  assert.equal(isReplacedView({ ...base, todayStatus: 'empty_subscriptions' }), true);
  // 今天还在编 / 失败:选中的可能正是最近一份编好的,不算
  assert.equal(isReplacedView({ ...base, todayStatus: 'generating' }), false);
  assert.equal(isReplacedView({ ...base, todayStatus: 'pending' }), false);
  assert.equal(isReplacedView({ ...base, todayStatus: 'failed' }), false);
  assert.equal(isReplacedView({ ...base, todayStatus: undefined }), false);   // 今天那份还没取到
  // 看的就是当前版、没带版本号、看往期:不算
  assert.equal(isReplacedView({ ...base, usesToday: true, todayStatus: 'ready' }), false);
  assert.equal(isReplacedView({ ...base, revision: null, todayStatus: 'ready' }), false);
  assert.equal(isReplacedView({ ...base, isToday: false, todayStatus: 'ready' }), false);
});

test('订阅范围的指纹:内容变了才变,集合对象换了、顺序不同都不算', () => {
  assert.equal(scopeKeyOf(new Set(['b', 'a'])), scopeKeyOf(new Set(['a', 'b'])));
  assert.notEqual(scopeKeyOf(new Set(['a'])), scopeKeyOf(new Set(['a', 'b'])));
  assert.notEqual(scopeKeyOf(new Set(['a', 'b'])), scopeKeyOf(new Set(['a', 'c'])));
  assert.equal(scopeKeyOf(new Set()), '');
  assert.equal(scopeKeyOf(null), '');
});
