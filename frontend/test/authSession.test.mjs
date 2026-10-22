// 认证请求串行与 401 处置(issue #213 检视 F4)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GUEST_RESUME_COOLDOWN_MS, createSerialQueue, decideAuthExpired } from '../src/utils/authSession.js';

const base = { currentGen: 3, now: 1_000_000, lastResumeAt: 0 };

test('旧身份发出的请求晚到的 401 一律忽略(游客与账号都是)', () => {
  assert.equal(decideAuthExpired({ ...base, eventGen: 2, isGuest: true }), 'ignore');
  assert.equal(decideAuthExpired({ ...base, eventGen: 2, isGuest: false }), 'ignore');
});

test('当前身份的 401:游客且过了冷却 → 续期;冷却内或正式账号 → 回登录页', () => {
  assert.equal(decideAuthExpired({ ...base, eventGen: 3, isGuest: true }), 'resume');
  assert.equal(
    decideAuthExpired({ ...base, eventGen: 3, isGuest: true, lastResumeAt: base.now - GUEST_RESUME_COOLDOWN_MS + 1 }),
    'reset',
  );
  assert.equal(decideAuthExpired({ ...base, eventGen: 3, isGuest: false }), 'reset');
});

test('事件没带代次(非 apiFetch 派发)按当前代次处理', () => {
  assert.equal(decideAuthExpired({ ...base, eventGen: undefined, isGuest: true }), 'resume');
  assert.equal(decideAuthExpired({ ...base, eventGen: undefined, isGuest: false }), 'reset');
});

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

test('串行队列:后一个任务等前一个结束才开始,失败不卡住后面,各自拿到自己的结果', async () => {
  const queue = createSerialQueue();
  const log = [];
  const gate = deferred();
  const first = queue.run(async () => { log.push('first:start'); await gate.promise; log.push('first:end'); return 1; });
  const second = queue.run(async () => { log.push('second:start'); throw new Error('boom'); });
  const third = queue.run(async () => { log.push('third:start'); return 3; });
  await Promise.resolve();
  assert.deepEqual(log, ['first:start']);
  gate.resolve();
  assert.equal(await first, 1);
  await assert.rejects(second, /boom/);
  assert.equal(await third, 3);
  assert.deepEqual(log, ['first:start', 'first:end', 'second:start', 'third:start']);
});

test('同批并发的 401:第一个续期成功推进代次,其余排队任务判为旧身份而忽略', async () => {
  const queue = createSerialQueue();
  let gen = 5;
  let resumes = 0;
  let resets = 0;
  const handle = (eventGen) => queue.run(async () => {
    const decision = decideAuthExpired({ eventGen, currentGen: gen, isGuest: true, now: 1_000_000, lastResumeAt: 0 });
    if (decision === 'resume') { resumes += 1; await Promise.resolve(); gen += 1; return; }
    if (decision === 'reset') resets += 1;
  });
  await Promise.all([handle(5), handle(5), handle(5)]);
  assert.equal(resumes, 1);
  assert.equal(resets, 0);
  // 续期成功后,续期前发出、之后才回来的 401(代次 5)同样忽略
  await handle(5);
  assert.equal(resets, 0);
});

test('登录进行中到来的旧 401:排在登录之后,轮到时代次已变 → 不会回登录页', async () => {
  const queue = createSerialQueue();
  let gen = 7;
  let resets = 0;
  const loginGate = deferred();
  const login = queue.run(async () => { await loginGate.promise; gen += 1; });
  const stale = queue.run(async () => {
    if (decideAuthExpired({ eventGen: 7, currentGen: gen, isGuest: true, now: 1_000_000, lastResumeAt: 999_999 }) === 'reset') resets += 1;
  });
  loginGate.resolve();
  await Promise.all([login, stale]);
  assert.equal(resets, 0);
});
