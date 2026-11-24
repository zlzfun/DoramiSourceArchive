// 动效的「触发方式」判定(issue #301,utils/readerMotion.js):键盘触发的不动;触发方式按触发那一刻算,
// 请求回来才换内容的路径在入口处挂起、带到播放处;显式传入的优先于挂起的,挂起的优先于此刻的最近输入。
// 这里用最小的桩顶替浏览器环境(Element.animate、window 事件、<html> 属性),只测纯逻辑。
import { test, before } from 'node:test';
import assert from 'node:assert/strict';

const listeners = { keydown: [], pointerdown: [] };
const attrs = new Map();
let clock = 1000;

before(() => {
  globalThis.Element = class { animate() {} };
  globalThis.window = { addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); } };
  globalThis.document = { documentElement: { setAttribute: (k, v) => attrs.set(k, v), getAttribute: (k) => attrs.get(k) ?? null, removeAttribute: (k) => attrs.delete(k) } };
  Object.defineProperty(globalThis, 'performance', { value: { now: () => clock }, configurable: true });
});

const key = () => listeners.keydown.forEach((fn) => fn({ metaKey: false, ctrlKey: false, altKey: false }));
const pointer = () => listeners.pointerdown.forEach((fn) => fn({}));

test('最近一次输入:键盘 → none,指针 → full;同时写到 <html data-input>', async () => {
  const m = await import('../src/utils/readerMotion.js');
  pointer();
  assert.equal(m.motionIntent(), 'pointer');
  assert.equal(m.pointerMotion(), 'full');
  key();
  assert.equal(attrs.get('data-input'), 'key');
  assert.equal(m.motionIntent(), 'key');
  assert.equal(m.pointerMotion(), 'none');
  pointer();
  assert.equal(attrs.get('data-input'), 'pointer');
});

test('挂起:请求在途时按当时的触发方式判,不看此刻的最近输入;放下之后回到此刻', async () => {
  const m = await import('../src/utils/readerMotion.js');
  key();
  const token = m.holdIntent();           // 回车发起
  pointer();                              // 等待时点了一下空白
  assert.equal(m.pointerMotion(), 'none', '仍按键盘判');
  assert.equal(m.currentIntent(), 'key');
  m.releaseIntent(token);
  assert.equal(m.pointerMotion(), 'full');
  pointer();
  m.holdIntent();                         // 指针发起
  key();                                  // 等待时按了一下键
  assert.equal(m.pointerMotion(), 'full', '仍按指针判');
  m.releaseIntent();
  assert.equal(m.pointerMotion(), 'none');
});

test('显式传入的触发方式优先于挂起的:别的请求挂着指针意图时,这一下是键盘就不动', async () => {
  const m = await import('../src/utils/readerMotion.js');
  pointer();
  m.holdIntent();                         // 指针点了「下一条」,还没回来
  key();                                  // 用键盘切筛选
  assert.equal(m.pointerMotion(), 'full', '不带参数会继承挂起的那一份');
  assert.equal(m.pointerMotion(m.motionIntent()), 'none', '入口处用这一下自己的触发方式判');
  m.releaseIntent();
});

test('挂起的凭据:被更晚的一次盖过后,旧凭据放不掉新的;10 秒过期', async () => {
  const m = await import('../src/utils/readerMotion.js');
  key();
  const old = m.holdIntent();
  pointer();
  const next = m.holdIntent();
  m.releaseIntent(old);
  key();
  assert.equal(m.currentIntent(), 'pointer', '新的那一份还在');
  m.releaseIntent(next);
  assert.equal(m.currentIntent(), 'key');
  m.holdIntent('pointer');
  clock += 9000;
  assert.equal(m.currentIntent(), 'pointer');
  clock += 2000;
  assert.equal(m.currentIntent(), 'key', '过期后回到此刻的最近输入');
  m.releaseIntent();
});

test('减少动效:按播放那一刻的偏好算,触发方式是指针也只留淡入', async () => {
  const m = await import('../src/utils/readerMotion.js');
  pointer();
  attrs.set('data-motion', 'reduce');
  assert.equal(m.pointerMotion(), 'fade');
  assert.equal(m.pointerMotion('pointer'), 'fade');
  assert.equal(m.pointerMotion('key'), 'none');
  attrs.delete('data-motion');
  assert.equal(m.pointerMotion(), 'full');
});

test('viewTransit:键盘触发直接 update,不调 fallback;指针触发但起不了转场时同步提交再调 fallback', async () => {
  const m = await import('../src/utils/readerMotion.js');
  const calls = [];
  // 没有 startViewTransition 的环境
  key();
  assert.equal(m.viewTransit('t', () => calls.push('update'), { fallback: (mode) => calls.push(`fallback:${mode}`) }), false);
  assert.deepEqual(calls.splice(0), ['update'], '键盘:与没有这层时相同');
  pointer();
  assert.equal(m.viewTransit('t', () => calls.push('update'), { fallback: (mode) => calls.push(`fallback:${mode}`) }), false);
  assert.deepEqual(calls.splice(0), ['update', 'fallback:full'], '指针 + 不支持:新界面提交之后再走退路');
  attrs.set('data-motion', 'reduce');
  m.viewTransit('t', () => calls.push('update'), { fallback: (mode) => calls.push(`fallback:${mode}`) });
  assert.deepEqual(calls.splice(0), ['update', 'fallback:fade'], '减少动效:退路里只许淡入');
  attrs.delete('data-motion');
});

test('viewTransit:同步入口用这一下自己的触发方式,不继承别的请求挂起的指针意图', async () => {
  const m = await import('../src/utils/readerMotion.js');
  const calls = [];
  pointer();
  m.holdIntent();              // 指针打开了一篇报道,请求还没回来
  key();                       // 按 Esc 返回
  m.viewTransit('t', () => calls.push('update'), { fallback: () => calls.push('fallback') });
  assert.deepEqual(calls, ['update'], '按键盘判:不起转场、不走退路');
  m.releaseIntent();
});

test('viewTransit:支持时起一场转场,过程中带 data-vt,结束后摘掉名字与类型;上一场没完时不叠', async () => {
  const m = await import('../src/utils/readerMotion.js');
  pointer();
  let finish;
  const root = globalThis.document.documentElement;
  root.dataset = {};
  globalThis.document.startViewTransition = (cb) => { cb(); return { finished: new Promise((resolve) => { finish = resolve; }), ready: Promise.resolve() }; };
  const el = { style: {} };
  const order = [];
  const t = m.viewTransit('rk-open', () => order.push('update'), { before: (name) => { name(el, 'rk-card'); order.push('before'); }, after: () => order.push(`after:${el.style.viewTransitionName}`) });
  assert.ok(t);
  assert.deepEqual(order, ['before', 'update', 'after:'], 'update 之后、after 之前旧名字已摘掉');
  assert.equal(root.dataset.vt, 'rk-open');
  const again = [];
  assert.equal(m.viewTransit('rk-back', () => again.push('update'), { fallback: (mode) => again.push(mode) }), false);
  assert.deepEqual(again, ['update', 'full'], '上一场没完:直接换并走退路');
  finish();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(root.dataset.vt, undefined);
  delete globalThis.document.startViewTransition;
});

test('shareTitle:每一场先清掉上一场的尺寸;旧端没取到时 taken 为假、give 不写', async () => {
  const m = await import('../src/utils/readerMotion.js');
  const props = new Map();
  globalThis.document.documentElement.style = { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k) };
  globalThis.getComputedStyle = () => ({ fontSize: '20px' });
  const el = { getBoundingClientRect: () => ({ width: 300 }) };
  const first = m.shareTitle();
  first.take(el); first.give(el);
  assert.equal(first.taken, true);
  assert.equal(props.get('--vt-title-ratio'), '1.0000');
  const second = m.shareTitle();
  assert.equal(props.size, 0, '新的一场从干净的开始');
  assert.equal(second.taken, false);
  second.give(el);
  assert.equal(props.size, 0);
});
