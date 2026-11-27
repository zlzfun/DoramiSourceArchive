// 同一个令牌上的修改与整表重取的先后(issue #344 第二个 PR,Codex 检视 R1 #4):
// 两个都带回整个对象的回包乱序到达时,旧状态不能盖掉新状态。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createMutationGate } from '../src/utils/mutationGate.js';

const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};

test('修改串行:有一个在途时别的不放行', () => {
  const gate = createMutationGate();
  assert.equal(gate.begin(), true);
  assert.equal(gate.busy, true);
  assert.equal(gate.begin(), false); // 第二个修改不发请求
  assert.equal(gate.end(), false);
  assert.equal(gate.busy, false);
  assert.equal(gate.begin(), true);
});

test('重取期间发生过修改:这份结果作废,修改结束后再取一次', () => {
  const gate = createMutationGate();
  const before = gate.snapshot();
  assert.equal(gate.begin(), true);         // 重取在途时开始了一次修改
  assert.equal(gate.accept(before), false); // 重取回来:不能用
  assert.equal(gate.end(), true);           // 修改结束:要再取一次
  const after = gate.snapshot();
  assert.equal(gate.accept(after), true);
  assert.equal(gate.end(), false);          // 没有被作废的重取,不用再取
});

test('修改在途时发出的重取,修改结束后才回来:同样作废', () => {
  const gate = createMutationGate();
  gate.begin();
  const during = gate.snapshot();
  gate.end();
  assert.equal(gate.accept(during), false);
});

test('没有修改时重取照常生效', () => {
  const gate = createMutationGate();
  const first = gate.snapshot();
  const second = gate.snapshot();
  assert.equal(gate.accept(first), true);
  assert.equal(gate.accept(second), true);
});

// 检视里的复现:先解绑(回包晚到)、再关权限(回包先到)。按约定第二个修改根本发不出去,状态只会被一个回包写。
test('乱序回包的复现:解绑在途时权限开关不放行,最后的状态来自唯一那次修改', async () => {
  const gate = createMutationGate();
  let token = { id: 1, can_register_sources: true, bindings: ['a', 'b'] };
  const apply = (next) => { token = next; };
  const mutate = async (request) => {
    if (!gate.begin()) return false;
    try { apply(await request()); return true; } finally { gate.end(); }
  };
  const unbind = deferred();
  const first = mutate(() => unbind.promise);
  const second = await mutate(async () => ({ id: 1, can_register_sources: false, bindings: ['a', 'b'] }));
  assert.equal(second, false);
  unbind.resolve({ id: 1, can_register_sources: true, bindings: ['a'] });
  assert.equal(await first, true);
  assert.deepEqual(token, { id: 1, can_register_sources: true, bindings: ['a'] });
  // 解绑结束后再关权限:回包基于解绑之后的状态
  assert.equal(await mutate(async () => ({ id: 1, can_register_sources: false, bindings: ['a'] })), true);
  assert.deepEqual(token, { id: 1, can_register_sources: false, bindings: ['a'] });
});
