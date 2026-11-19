// 沉浸阅读的状态机(issue #301 第六批):往下读过一屏进入;回滚、回到顶上、指针 / 焦点进了三栏、按键(滚动键除外)、换篇退出。
import assert from 'node:assert/strict';
import test from 'node:test';

function harness() {
  const listeners = { shell: {}, window: {} };
  const attrs = new Map();
  const observers = [];
  globalThis.window = {
    addEventListener: (type, fn) => { listeners.window[type] = fn; },
    removeEventListener: (type) => { delete listeners.window[type]; },
  };
  globalThis.MutationObserver = class { constructor(cb) { this.cb = cb; observers.push(this); } observe() {} disconnect() { this.off = true; } };
  const shell = {
    addEventListener: (type, fn) => { listeners.shell[type] = fn; },
    removeEventListener: (type) => { delete listeners.shell[type]; },
    hasAttribute: (name) => attrs.has(name),
    setAttribute: (name, value) => attrs.set(name, value),
    removeAttribute: (name) => attrs.delete(name),
  };
  const pane = (marked = true) => ({ scrollTop: 0, isConnected: true, matches: (sel) => sel === '.reader-pane' || (marked && sel === '.reader-pane[data-immersive]') });
  const inside = { closest: () => ({}) };     // 在三栏里的元素
  const outside = { closest: () => null };    // 在阅读窗里的元素
  const scrollTo = (el, top) => { el.scrollTop = top; listeners.shell.scroll({ target: el }); };
  return { shell, listeners, observers, pane, inside, outside, scrollTo, reading: () => attrs.has('data-reading') };
}

test('往下读过一屏才进入:位置与连续下滚量两个条件都要到', async () => {
  const h = harness();
  const { mountImmersiveRead, IMMERSIVE } = await import(`../src/utils/immersiveRead.js?a=${Date.now()}`);
  mountImmersiveRead(h.shell);
  const p = h.pane();
  h.scrollTo(p, 0);                       // 第一次见到这个阅读窗:只记位置
  // 顶上那一段(wakeTop 以内)不攒下滚量:从顶上一路往下,要离开顶上之后再连续滚够 enterDown 才进入
  h.scrollTo(p, 100); assert.equal(h.reading(), false, '还在顶上附近');
  h.scrollTo(p, IMMERSIVE.wakeTop + 10); assert.equal(h.reading(), false);
  h.scrollTo(p, IMMERSIVE.enterTop + 10); assert.equal(h.reading(), false, '位置到了,连续下滚量还差一点');
  h.scrollTo(p, IMMERSIVE.wakeTop + 10 + IMMERSIVE.enterDown + 1); assert.equal(h.reading(), true);
  // 从深处回滚超过阈值:退出;位置仍然够,但连续下滚量要重新攒
  const deep = p.scrollTop;
  h.scrollTo(p, deep - IMMERSIVE.wakeUp - 1); assert.equal(h.reading(), false, '回滚超过阈值:退出');
  h.scrollTo(p, deep + 100); assert.equal(h.reading(), false, '才往下滚了 125');
  h.scrollTo(p, deep + 200); assert.equal(h.reading(), true, '又连续往下滚够了');
});

test('退出:回滚、回到顶上附近、指针与焦点进了三栏', async () => {
  const h = harness();
  const { mountImmersiveRead } = await import(`../src/utils/immersiveRead.js?b=${Date.now()}`);
  mountImmersiveRead(h.shell);
  const p = h.pane();
  const enter = () => { h.scrollTo(p, 0); h.scrollTo(p, 300); h.scrollTo(p, 600); assert.equal(h.reading(), true); };
  enter(); h.scrollTo(p, 590); assert.equal(h.reading(), true, '回滚 10px 不算');
  h.scrollTo(p, 570); assert.equal(h.reading(), false, '累计回滚 30px:退出');
  enter(); h.listeners.shell.pointermove({ target: h.outside }); assert.equal(h.reading(), true, '指针在阅读窗里动:不退出');
  h.listeners.shell.pointermove({ target: h.inside }); assert.equal(h.reading(), false);
  enter(); h.listeners.shell.pointerdown({ target: h.inside }); assert.equal(h.reading(), false);
  enter(); h.listeners.shell.focusin({ target: h.inside }); assert.equal(h.reading(), false);
  enter(); p.scrollTop = 60; h.listeners.shell.scroll({ target: p }); assert.equal(h.reading(), false, '回到顶上附近');
});

test('按键:修饰键与「在阅读窗里按滚动键」不唤回,别的键、在三栏里按方向键都唤回', async () => {
  const h = harness();
  const { mountImmersiveRead } = await import(`../src/utils/immersiveRead.js?c=${Date.now()}`);
  mountImmersiveRead(h.shell);
  const p = h.pane();
  const enter = () => { h.scrollTo(p, 0); h.scrollTo(p, 300); h.scrollTo(p, 600); assert.equal(h.reading(), true); };
  enter();
  for (const key of ['Shift', 'Meta', ' ', 'PageDown', 'ArrowDown', 'End']) {
    h.listeners.window.keydown({ key, target: h.outside });
    assert.equal(h.reading(), true, `${key} 不唤回`);
  }
  h.listeners.window.keydown({ key: 'ArrowDown', target: h.inside }); assert.equal(h.reading(), false, '焦点在条目列里按方向键是在换篇');
  enter(); h.listeners.window.keydown({ key: 'Tab', target: h.outside }); assert.equal(h.reading(), false);
  enter(); h.listeners.window.keydown({ key: 'a', target: h.outside }); assert.equal(h.reading(), false);
});

test('换了一篇:旧阅读窗不在了就退出;新阅读窗从头算', async () => {
  const h = harness();
  const { mountImmersiveRead } = await import(`../src/utils/immersiveRead.js?d=${Date.now()}`);
  const unmount = mountImmersiveRead(h.shell);
  const first = h.pane();
  h.scrollTo(first, 0); h.scrollTo(first, 300); h.scrollTo(first, 600);
  assert.equal(h.reading(), true);
  first.isConnected = false; h.observers[0].cb();
  assert.equal(h.reading(), false, '阅读窗重挂载、没有新的滚动事件');
  const second = h.pane();
  h.scrollTo(second, 500); assert.equal(h.reading(), false, '新阅读窗的第一个滚动事件只记位置');
  h.scrollTo(second, 700); assert.equal(h.reading(), true);
  unmount();
  assert.equal(h.reading(), false, '卸载时清掉状态');
  assert.deepEqual([Object.keys(h.listeners.shell).length, Object.keys(h.listeners.window).length, h.observers[0].off], [0, 0, true]);
});

test('只认带标记的阅读窗:播客详情共用同一个容器但没有标记,滚多远都不进入', async () => {
  const h = harness();
  const { mountImmersiveRead } = await import(`../src/utils/immersiveRead.js?e=${Date.now()}`);
  mountImmersiveRead(h.shell);
  const podcast = h.pane(false);
  h.scrollTo(podcast, 0); h.scrollTo(podcast, 400); h.scrollTo(podcast, 900);
  assert.equal(h.reading(), false);
  // 从文章(沉浸中)切到播客:文章的阅读窗不在了 → 退出;播客的滚动不再进入
  const article = h.pane();
  h.scrollTo(article, 0); h.scrollTo(article, 300); h.scrollTo(article, 600);
  assert.equal(h.reading(), true);
  article.isConnected = false; h.observers[0].cb();
  h.scrollTo(podcast, 1200); h.scrollTo(podcast, 1600);
  assert.equal(h.reading(), false);
});
