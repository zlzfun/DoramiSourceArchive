// 问答的光里按内容编排的那一部分(issue #301 第五批):回答到达时各段依次由虚到实、引用依次亮;同一轮回答只演一次。
import assert from 'node:assert/strict';
import test from 'node:test';

function el(cls, children = [], text = '') {
  const node = {
    cls, children, textContent: text, played: [],
    animate(keyframes, opts) { node.played.push({ keyframes, opts }); return { finished: Promise.resolve() }; },
    matches(sel) { return sel.split(',').some((s) => s.trim() === cls || s.trim() === `.${cls}`); },
    querySelector(sel) { return node.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const want = sel.replace(':scope > ', '').trim();
      if (sel.startsWith(':scope > :not(')) return children.filter((c) => !['reader-ai-a-id', 'reader-ai-a-body'].includes(c.cls));
      const out = [];
      const walk = (n) => { for (const c of n.children) { if (`.${c.cls}` === want) out.push(c); walk(c); } };
      walk(node);
      return out;
    },
  };
  return node;
}

function answer() {
  const cite = (n) => el('reader-ai-cite', [], String(n));
  const paras = [el('p', [cite(1)]), el('p', [cite(2)]), el('p', [cite(1)])];
  const body = el('reader-ai-a-body', paras);
  const row = (n) => el('reader-ai-ref-row', [el('reader-ai-ref-num', [], String(n))]);
  const refs = el('reader-ai-refs', [row(1), row(2)]);
  const block = el('reader-ai-a', [el('reader-ai-a-id'), body, refs]);
  return { block, paras, refs, rows: refs.children, cites: paras.map((p) => p.children[0]) };
}

function stubDom(reduce = false) {
  globalThis.Element = class { animate() {} };
  globalThis.document = { documentElement: { dataset: reduce ? { motion: 'reduce' } : {}, getAttribute: (k) => (k === 'data-motion' && reduce ? 'reduce' : null) } };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '63, 123, 246' });
}

test('回答到达:各段依次由虚到实,来源条随后淡入,引用依次亮、每个来源行亮一次', async () => {
  stubDom();
  const { revealAnswer } = await import(`../src/utils/qaMotion.js?full=${Date.now()}`);
  const a = answer();
  revealAnswer(a.block);
  assert.deepEqual(a.paras.map((p) => [p.played[0].opts.duration, p.played[0].opts.delay]), [[220, 0], [220, 140], [220, 280]]);
  assert.match(a.paras[0].played[0].keyframes[0].filter, /blur\(6px\)/);
  assert.equal(a.refs.played.length, 1, '来源条淡入一次');
  assert.ok(a.refs.played[0].opts.delay >= 420, '来源条在各段之后');
  const delays = a.cites.map((c) => c.played[0].opts.delay);
  assert.deepEqual([delays[1] - delays[0], delays[2] - delays[1]], [150, 150]);
  assert.deepEqual(a.rows.map((r) => r.played.length), [1, 1], '同一个来源被引用两次,来源行只亮一次');
});

test('减少动效:各段只淡入、不带模糊,引用不亮', async () => {
  stubDom(true);
  const { revealAnswer } = await import(`../src/utils/qaMotion.js?reduce=${Date.now()}`);
  const a = answer();
  revealAnswer(a.block);
  for (const p of a.paras) {
    assert.equal(p.played.length, 1);
    assert.ok(p.played[0].opts.duration <= 200);
    assert.ok(p.played[0].keyframes.every((k) => !('filter' in k)));
  }
  assert.ok(a.cites.every((c) => c.played.length === 0));
  assert.ok(a.rows.every((r) => r.played.length === 0));
});

test('同一轮回答只演一次;没有 fresh 标记的(重新挂载的旧回答)不演', async () => {
  stubDom();
  const { revealOnce } = await import(`../src/utils/qaMotion.js?once=${Date.now()}`);
  const fresh = { fresh: true };
  const a = answer();
  revealOnce(fresh, a.block);
  revealOnce(fresh, a.block);
  assert.equal(a.paras[0].played.length, 1);
  const old = answer();
  revealOnce({ a: '旧回答' }, old.block);
  assert.equal(old.paras[0].played.length, 0);
});

test('提问的那一刻问句由虚到实,同一轮只演一次;减少动效只淡入', async () => {
  stubDom();
  const full = await import(`../src/utils/qaMotion.js?ask=${Date.now()}`);
  const turn = { pending: true };
  const bubble = el('reader-ai-q');
  full.focusQuestionOnce(turn, bubble);
  full.focusQuestionOnce(turn, bubble);
  assert.equal(bubble.played.length, 1);
  assert.equal(bubble.played[0].opts.duration, 200);
  assert.match(bubble.played[0].keyframes[0].filter, /blur\(4px\)/);
  stubDom(true);
  const reduced = await import(`../src/utils/qaMotion.js?ask-reduce=${Date.now()}`);
  const calm = el('reader-ai-q');
  reduced.focusQuestionOnce({ pending: true }, calm);
  assert.equal(calm.played.length, 1);
  assert.ok(calm.played[0].keyframes.every((k) => !('filter' in k)));
});

test('回答先到、正文晚一拍才挂上(渲染器按需加载):等正文挂上再演', async () => {
  stubDom();
  const observers = [];
  globalThis.MutationObserver = class { constructor(cb) { this.cb = cb; observers.push(this); } observe() {} disconnect() { this.off = true; } };
  const { revealOnce } = await import(`../src/utils/qaMotion.js?late=${Date.now()}`);
  const a = answer();
  const body = a.block.children[1];
  const paras = body.children.splice(0);   // 正文还没挂上
  a.block.isConnected = true;
  revealOnce({ fresh: true }, a.block);
  assert.equal(observers.length, 1, '容器是空的:挂观察器等');
  assert.ok(paras.every((p) => p.played.length === 0));
  body.children.push(...paras);
  observers[0].cb();
  assert.ok(observers[0].off, '演过就撤掉观察器');
  assert.deepEqual(paras.map((p) => p.played.length), [1, 1, 1]);
  assert.equal(a.refs.played.length, 0, '来源条那时已经显示着:不藏回去重新淡入');
  assert.ok(a.cites.every((c) => c.played.length === 1), '引用照常依次亮');
  delete globalThis.MutationObserver;
});

test('长回答:至多错开 8 段,第 8 段起同时出;来源条排在其后', async () => {
  stubDom();
  const { revealAnswer } = await import(`../src/utils/qaMotion.js?long=${Date.now()}`);
  const paras = Array.from({ length: 11 }, () => el('p'));
  const refs = el('reader-ai-refs');
  const block = el('reader-ai-a', [el('reader-ai-a-id'), el('reader-ai-a-body', paras), refs]);
  revealAnswer(block);
  assert.deepEqual(paras.map((p) => p.played[0].opts.delay), [0, 140, 280, 420, 560, 700, 840, 980, 980, 980, 980]);
  assert.equal(new Set(paras.map((p) => p.played[0].opts.delay)).size, 8);
  assert.equal(refs.played[0].opts.delay, 8 * 140 + 60);
});

test('取段只看正文容器的直接子元素:单个标题里的行内元素不被拆开', async () => {
  stubDom();
  const { revealAnswer } = await import(`../src/utils/qaMotion.js?h5=${Date.now()}`);
  const strong = el('strong'); const em = el('em');
  const h5 = el('h5', [strong, em]);
  const block = el('reader-ai-a', [el('reader-ai-a-id'), el('reader-ai-a-body', [h5])]);
  revealAnswer(block);
  assert.equal(h5.played.length, 1);
  assert.equal(strong.played.length + em.played.length, 0);
});
