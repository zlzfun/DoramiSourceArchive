// 第七批(对拍补差)的三样:滑到新位置的几处(FLIP_ZONES)、换页签时新内容进场的几处(SWAP_ZONES)、「依据」的行高折叠(foldRows)。
// 行为在浏览器里核对;这里守配置表的形状与折叠的取值。
import assert from 'node:assert/strict';
import test from 'node:test';

// 最小的桩顶替浏览器环境(这几个模块在载入时会碰 <html> 的属性与 window 事件)
const attrs = new Map();
globalThis.Element ??= class { animate() {} };
globalThis.window ??= { addEventListener() {}, removeEventListener() {} };
globalThis.document ??= { documentElement: { dataset: {}, setAttribute: (k, v) => attrs.set(k, v), getAttribute: (k) => attrs.get(k) ?? null, removeAttribute: (k) => attrs.delete(k) } };

test('滑到新位置的三处:条目列、榜单换范围、发现页形态筛选;每处该有的都有', async () => {
  const { FLIP_ZONES } = await import('../src/utils/listFlip.js');
  assert.deepEqual(FLIP_ZONES.map((zone) => zone.name), ['list', 'rank', 'discover']);
  for (const zone of FLIP_ZONES) {
    for (const key of ['triggers', 'host', 'row']) assert.equal(typeof zone[key], 'string', `${zone.name}.${key}`);
    for (const key of ['idOf', 'atRest']) assert.equal(typeof zone[key], 'function', `${zone.name}.${key}`);
    assert.equal(typeof zone.cover, 'boolean');
  }
  const [list, rank, discover] = FLIP_ZONES;
  // 要回服务端重拉的垫底色(挡住骨架 /「加载中」),当场就换的不垫
  assert.deepEqual([list.cover, rank.cover, discover.cover], [true, true, false]);
  // 条目列与发现页只在停在顶上时做;榜单不限
  assert.deepEqual([list.atRest({ scrollTop: 0 }), list.atRest({ scrollTop: 40 }), discover.atRest({ scrollTop: 0 }), discover.atRest({ scrollTop: 40 }), rank.atRest({ scrollTop: 400 })], [true, false, true, false, true]);
  // 认「同一条」的属性
  assert.equal(list.idOf({ dataset: { flipId: 'a' } }), 'a');
  assert.equal(rank.idOf({ dataset: { row: 'event-7' } }), 'event-7');
  assert.equal(rank.area, '.rk-list', '榜单的替身层只盖那张榜,不盖页头与页签');
  assert.equal(list.label, '.reader-date-label', '日期组头同名的跟着滑');
});

test('换页签时新内容进场的几处:榜单、发现页(页签与滚下去之后的形态筛选)、播客正文', async () => {
  const { SWAP_ZONES } = await import('../src/utils/swapMotion.js');
  assert.deepEqual(SWAP_ZONES.map((zone) => [zone.name, zone.dist]), [['rank-tab', 12], ['discover-tab', 12], ['discover-shape', 12], ['podcast-tab', 8]]);
  const byName = Object.fromEntries(SWAP_ZONES.map((zone) => [zone.name, zone]));
  // 榜单要回服务端取:等新榜真的挂上;发现页是本地筛选:内容一变就演
  assert.deepEqual([Boolean(byName['rank-tab'].sync), byName['discover-tab'].sync, byName['discover-shape'].sync], [false, true, true]);
  // 发现页的形态筛选:停在页顶时归 FLIP 那一层管,这里只管滚下去之后
  const shellAt = (top) => ({ querySelector: (sel) => (sel === ':scope > .reader-disc' ? { scrollTop: top } : null) });
  assert.deepEqual([byName['discover-shape'].when(shellAt(0)), byName['discover-shape'].when(shellAt(120))], [false, true]);
});

test('「依据」的行高折叠:展开 220ms 行高 + 220ms 淡入;收起 120ms 淡出 + 160ms 行高、末帧保持;连点先撤上一段', async () => {
  const { foldRows } = await import('../src/utils/readerMotion.js');
  const stub = () => {
    const el = { played: [], running: [], firstElementChild: null };
    el.animate = (keyframes, opts) => { const anim = { keyframes, opts, cancelled: false, cancel() { this.cancelled = true; } }; el.played.push(anim); el.running.push(anim); return anim; };
    el.getAnimations = () => el.running;
    return el;
  };
  const fold = stub(); const inner = stub(); fold.firstElementChild = inner;
  const opening = foldRows(fold, true);
  assert.deepEqual([opening.keyframes.map((k) => k.gridTemplateRows), opening.opts.duration], [['0fr', '1fr'], 220]);
  assert.deepEqual([inner.played[0].keyframes.map((k) => k.opacity), inner.played[0].opts.duration], [[0, 1], 220]);
  const closing = foldRows(fold, false);
  assert.ok(opening.cancelled && inner.played[0].cancelled, '收起时先撤掉还在跑的展开');
  assert.deepEqual([closing.keyframes.map((k) => k.gridTemplateRows), closing.opts.duration, closing.opts.fill], [['1fr', '0fr'], 160, 'forwards']);
  assert.deepEqual([inner.played[1].keyframes.map((k) => k.opacity), inner.played[1].opts.duration, inner.played[1].opts.fill], [[1, 0], 120, 'forwards']);
  assert.equal(foldRows(null, true), null);
});
