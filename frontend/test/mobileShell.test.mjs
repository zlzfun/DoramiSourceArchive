// 手机壳(issue #230 阶段 3 第 2 批「壳与页头」)的纯逻辑:页头标题位转盘、镀层随滚动、「设置在这里」一次性提示、
// 订阅 Tab 合表分组。React 外壳(components/mobile/MobileHeader / MobileReader / MobileSubsPage)由沙箱 Playwright 走查。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { dialCharCount, dialIndex, dialKeyTarget, dialReserve } from '../src/utils/titleDial.js';
import {
  CHROME, SETTINGS_HINT_KEY, markSettingsHintSeen, nextChrome, settingsHintPending,
} from '../src/utils/mobileChrome.js';
import { subscriptionGroups } from '../src/utils/subsGroups.js';

const here = path.dirname(fileURLToPath(import.meta.url));

// ── 页头标题位转盘 ──
test('转盘:当前值落第几项,找不到落第一项', () => {
  const items = [['article', '文章'], ['podcast', '播客'], ['social', '社交']];
  assert.equal(dialIndex(items, 'podcast'), 1);
  assert.equal(dialIndex(items, 'social'), 2);
  assert.equal(dialIndex(items, 'brief'), 0);
  assert.equal(dialIndex([], 'x'), 0);
});

test('转盘:← → 循环,Home / End 到两端,其它键不拦', () => {
  assert.equal(dialKeyTarget('ArrowRight', 0, 3), 1);
  assert.equal(dialKeyTarget('ArrowRight', 2, 3), 0);
  assert.equal(dialKeyTarget('ArrowLeft', 0, 3), 2);
  assert.equal(dialKeyTarget('ArrowLeft', 1, 2), 0);
  assert.equal(dialKeyTarget('Home', 2, 3), 0);
  assert.equal(dialKeyTarget('End', 0, 3), 2);
  assert.equal(dialKeyTarget('Enter', 0, 3), null);
  assert.equal(dialKeyTarget('ArrowDown', 0, 3), null);
  assert.equal(dialKeyTarget('ArrowRight', 0, 0), null);
});

test('转盘:槽宽按最长标签的字数放宽,预留 = 领位 44 + 动作每枚 44(样页 内容 176 · 订阅 88 · 榜单 44)', () => {
  assert.equal(dialCharCount([['article', '文章榜'], ['podcast', '播客榜']]), 3);
  assert.equal(dialCharCount([['subs', '订阅'], ['interest', '兴趣']]), 2);
  assert.equal(dialCharCount([['x', '']]), 1);
  assert.equal(dialReserve(3), 176);
  assert.equal(dialReserve(1), 88);
  assert.equal(dialReserve(0), 44);
  assert.equal(dialReserve(), 44);
});

// ── 镀层随滚动(样页 10 / 11) ──
test('镀层:滚过 4px 页头换镀层底;回到 4 以内撤掉', () => {
  assert.equal(nextChrome({}, { y: 0 }).scrolled, false);
  assert.equal(nextChrome({}, { y: CHROME.scrolledAt }).scrolled, false);
  assert.equal(nextChrome({}, { y: CHROME.scrolledAt + 1 }).scrolled, true);
});

test('镀层:越过 140 且向下 > 6 底栏退场;向上 > 6 或回到 140 以内回来;小幅抖动保持原状', () => {
  let st = { receded: false };
  st = nextChrome(st, { y: 120, dy: 40 });
  assert.equal(st.receded, false, '140 以内不退场');
  st = nextChrome(st, { y: 200, dy: 80 });
  assert.equal(st.receded, true);
  st = nextChrome(st, { y: 203, dy: 3 });
  assert.equal(st.receded, true, '慢速下滚保持退场');
  st = nextChrome(st, { y: 200, dy: -3 });
  assert.equal(st.receded, true, '小幅回弹不叫回');
  st = nextChrome(st, { y: 190, dy: -10 });
  assert.equal(st.receded, false, '往上一划回来');
  st = nextChrome(st, { y: 400, dy: 210 });
  assert.equal(st.receded, true);
  st = nextChrome(st, { y: 100, dy: 0 });
  assert.equal(st.receded, false, '回到 140 以内回来');
});

test('镀层:程序性滚动(恢复位置)不让底栏退场,但往回滚照样叫回来', () => {
  let st = nextChrome({ receded: false }, { y: 900, dy: 900, intent: false });
  assert.equal(st.receded, false);
  assert.equal(st.scrolled, true);
  st = nextChrome({ receded: true }, { y: 300, dy: -40, intent: false });
  assert.equal(st.receded, false);
});

test('镀层:减少动效时底栏常显,不退场', () => {
  const st = nextChrome({ receded: true }, { y: 600, dy: 50, reduced: true });
  assert.equal(st.receded, false);
  assert.equal(st.scrolled, true);
});

// ── 「设置在这里」一次性提示(拍板 30) ──
function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    map,
  };
}

test('迁移提示:没看过就提示,标记后不再提示', () => {
  const store = memoryStorage();
  assert.equal(settingsHintPending(store), true);
  markSettingsHintSeen(store);
  assert.equal(store.map.get(SETTINGS_HINT_KEY), 'seen');
  assert.equal(settingsHintPending(store), false);
});

test('迁移提示:存储不可用(null / 读写抛错)时不提示、写入不抛', () => {
  const broken = {
    getItem() { throw new Error('SecurityError'); },
    setItem() { throw new Error('QuotaExceededError'); },
  };
  assert.equal(settingsHintPending(null), false);
  assert.equal(settingsHintPending(broken), false);
  assert.doesNotThrow(() => markSettingsHintSeen(broken));
  assert.doesNotThrow(() => markSettingsHintSeen(null));
});

// ── 订阅 Tab 合表(拍板 25) ──
test('订阅合表:文章按角色分组(角色表的顺序),播客 / 社交各一组排后,空组不出', () => {
  const roles = [{ key: 'official', label: '官方' }, { key: 'media', label: '媒体' }, { key: 'personal', label: '个人' }];
  const shapes = { a1: 'article', a2: 'article', a3: 'article', p1: 'podcast', s1: 'social', s2: 'social' };
  const role = { a1: 'media', a2: 'official', a3: 'media', s1: 'official', s2: 'personal' };
  const sources = ['a1', 'p1', 'a2', 's1', 'a3', 's2'].map((id) => ({ source_id: id }));
  const groups = subscriptionGroups(sources, {
    shapeOf: (id) => shapes[id],
    roleOf: (s) => role[s.source_id] || 'official',
    roles,
  });
  assert.deepEqual(groups.map((g) => g.label), ['官方', '媒体', '播客', '社交']);
  assert.deepEqual(groups.map((g) => g.list.map((s) => s.source_id)), [['a2'], ['a1', 'a3'], ['p1'], ['s1', 's2']]);
  assert.deepEqual(subscriptionGroups([], { shapeOf: () => 'article', roleOf: () => 'official', roles }), []);
});

// ── 护栏:手机设置去 OPML 与接入集成(拍板 35),桌面设置柜不动;GUEST_LOCKED_SECTIONS 键保留 ──
test('手机设置不列 OPML 与接入集成,桌面设置柜照旧;游客锁的键不删', () => {
  const mobile = readFileSync(path.join(here, '../src/components/mobile/MobileSettings.jsx'), 'utf8');
  for (const id of ['opml', 'feed', 'mcp', 'skill']) {
    assert.ok(!new RegExp(`id: '${id}'`).test(mobile), `手机设置不应再有 ${id} 分区`);
  }
  assert.ok(!/OpmlPreview|opmlPreview/.test(mobile), 'OPML 预览层已撤');
  const desktop = readFileSync(path.join(here, '../src/components/SettingsModal.jsx'), 'utf8');
  for (const id of ['opml', 'feed', 'mcp', 'skill']) {
    assert.ok(new RegExp(`id: '${id}'`).test(desktop), `桌面设置柜应保留 ${id}`);
  }
  const guest = readFileSync(path.join(here, '../src/utils/guestCopy.js'), 'utf8');
  for (const id of ['opml', 'feedback', 'feed', 'mcp', 'skill']) {
    assert.ok(new RegExp(`\\b${id}:`).test(guest), `GUEST_LOCKED_SECTIONS 应保留 ${id}`);
  }
});
