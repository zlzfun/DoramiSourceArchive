// 层原语(issue #230 阶段 3 第 1 批)的纯逻辑:返回键层栈、遮罩判定(长按松手,M-01)、抓手点按与下拖、
// 菜单键位(M-35)、焦点落点、下层 inert、灯箱双指缩放(M-40)、遮罩护栏。
// 项目没有 DOM 测试框架,React 外壳(components/layer)由沙箱 Playwright 走查;这里只测它们依赖的纯模块。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { Linter } from 'eslint';
import { createLayerHistory } from '../src/utils/layerHistory.js';
import { createOverlayCloseGuard } from '../src/utils/overlayClose.js';
import { createSheetDrag, SHEET_DRAG } from '../src/utils/sheetDrag.js';
import { nextMenuIndex } from '../src/utils/menuNav.js';
import { LAYER_KINDS, INERT_KEEP_SELECTOR, initialFocusTarget } from '../src/utils/layerKinds.js';
import { inertOthers, resetInertState } from '../src/utils/inertOthers.js';
import { pinchTransform, settleTransform, clampScale, PINCH } from '../src/utils/pinchZoom.js';
import eslintConfig from '../eslint.config.js';

// ── ① 返回键层栈 ──
// 假 window:history 是同 URL 条目的数组 + 当前下标;back / go 排进队列,flush() 才真正走并派发 popstate
// (浏览器里 back 是异步的);pushState 同步截断前进栈。
function fakeWindow() {
  const listeners = [];
  const queue = [];
  const win = {
    location: { href: 'https://dorami.test/#/reader' },
    entries: [{ base: true }],
    index: 0,
    history: {
      get state() { return win.entries[win.index]; },
      get length() { return win.entries.length; },
      pushState(state) {
        win.entries = win.entries.slice(0, win.index + 1).concat([state]);
        win.index += 1;
      },
      back() { queue.push(-1); },
      go(delta) { queue.push(delta); },
    },
    addEventListener(type, fn) { if (type === 'popstate') listeners.push(fn); },
    flush() {
      while (queue.length) {
        const delta = queue.shift();
        win.index = Math.max(0, Math.min(win.entries.length - 1, win.index + delta));
        listeners.forEach((fn) => fn({ state: win.entries[win.index] }));
      }
    },
    // 用户按系统返回键
    userBack() { win.history.back(); win.flush(); },
  };
  return win;
}

test('层栈:两层叠开,系统返回只关最上层,再按一次关下一层,不退出站点', () => {
  const win = fakeWindow();
  const layers = createLayerHistory(win);
  const closed = [];
  layers.open(() => closed.push('page'));
  layers.open(() => closed.push('sheet'));
  assert.equal(win.index, 2);
  win.userBack();
  assert.deepEqual(closed, ['sheet']);
  assert.equal(win.index, 1);
  win.userBack();
  assert.deepEqual(closed, ['sheet', 'page']);
  assert.equal(win.index, 0);
  assert.equal(layers.depth(), 0);
});

test('层栈:程序性关层(遮罩 / Esc / 抓手)消费自己那条历史,自触发的 popstate 不误关下层', () => {
  const win = fakeWindow();
  const layers = createLayerHistory(win);
  const closed = [];
  layers.open(() => closed.push('page'));
  const sheet = layers.open(() => closed.push('sheet'));
  assert.equal(layers.close(sheet), true);
  win.flush();
  assert.deepEqual(closed, [], '自触发的 popstate 被计数吞掉');
  assert.equal(win.index, 1);
  win.userBack();
  assert.deepEqual(closed, ['page']);
  assert.equal(win.index, 0);
});

test('层栈:已被返回键弹栈的层再走程序性关闭是空操作,不多退一步', () => {
  const win = fakeWindow();
  const layers = createLayerHistory(win);
  const entry = layers.open(() => {});
  win.userBack();
  assert.equal(layers.close(entry), false);
  win.flush();
  assert.equal(win.index, 0);
});

test('层栈:同一次提交里「关一层、开一层」(动作单项打开确认框)净深度不变', () => {
  const win = fakeWindow();
  const layers = createLayerHistory(win);
  const closed = [];
  const sheet = layers.open(() => closed.push('sheet'));
  layers.open(() => closed.push('confirm'));
  layers.close(sheet);
  win.flush();
  assert.equal(win.index, 1);
  assert.deepEqual(closed, []);
  win.userBack();
  assert.deepEqual(closed, ['confirm']);
});

test('层栈:版式切换一次卸载几层,合并成一次回退;StrictMode 重挂载(mounted 又为真)不动历史', () => {
  const win = fakeWindow();
  const layers = createLayerHistory(win);
  const a = layers.open(() => {});
  const b = layers.open(() => {});
  const c = layers.open(() => {});
  const tasks = [];
  const schedule = (fn) => tasks.push(fn);
  layers.retireOnUnmount(a, { current: false }, schedule);
  layers.retireOnUnmount(b, { current: false }, schedule);
  layers.retireOnUnmount(c, { current: true }, schedule); // 仍挂着
  assert.equal(tasks.length, 1, '同一批只排一次');
  tasks[0]();
  win.flush();
  assert.equal(win.index, 1);
  assert.equal(layers.depth(), 1);
});

// ── ② 遮罩判定(M-01) ──
test('M-01:长按弹出动作单,松手的合成 click 落到新盖上的遮罩——按下不在遮罩,不关', () => {
  const overlay = { id: 'overlay' };
  const row = { id: 'row' };
  const guard = createOverlayCloseGuard();
  // 按下发生在遮罩出现之前(条目上),遮罩只收到松手后的 click;触屏隐式捕获,pointerup 也落在条目上
  guard.onPointerUp({ target: row, currentTarget: overlay, button: 0 });
  assert.equal(guard.shouldClose({ target: overlay, currentTarget: overlay, button: 0 }), false);
  // 随后在遮罩上真点一下,照常关
  guard.onPointerDown({ target: overlay, currentTarget: overlay, button: 0 });
  guard.onPointerUp({ target: overlay, currentTarget: overlay, button: 0 });
  assert.equal(guard.shouldClose({ target: overlay, currentTarget: overlay, button: 0 }), true);
});

// ── ③ 抓手:点按与下拖 ──
test('抓手:原地点按不算拖,click 不被吞(点按即关)', () => {
  const drag = createSheetDrag();
  drag.start(500, 0);
  assert.deepEqual(drag.move(502, 16), { dragging: false, offset: 0 });
  assert.deepEqual(drag.end(502, 60), { dragged: false, close: false });
  assert.equal(drag.consumeClick(), false);
});

test('抓手:4px 防抖,越过才跟手;往上拖不动面板', () => {
  const drag = createSheetDrag();
  drag.start(500, 0);
  assert.equal(drag.move(500 + SHEET_DRAG.slop, 10).dragging, false);
  const moved = drag.move(530, 40);
  assert.equal(moved.dragging, true);
  assert.equal(moved.offset, 30);
  assert.equal(drag.move(480, 80).offset, 0, '越过起点往上只停在 0');
});

test('抓手:拖不够远、不够快 → 回弹,随后的 click 吞掉一次', () => {
  const drag = createSheetDrag();
  drag.start(500, 0);
  for (let t = 1; t <= 10; t += 1) drag.move(500 + t * 6, t * 50); // 0.12px/ms,共 60px
  const result = drag.end(560, 520);
  assert.deepEqual(result, { dragged: true, close: false });
  assert.equal(drag.consumeClick(), true);
  assert.equal(drag.consumeClick(), false, '只吞一次');
});

test('抓手:拖过 96px 松手即关;快甩(末段 > 0.6px/ms)短距离也关', () => {
  const far = createSheetDrag();
  far.start(400, 0);
  for (let t = 1; t <= 12; t += 1) far.move(400 + t * 10, t * 60);
  assert.equal(far.end(520, 760).close, true);

  const flick = createSheetDrag();
  flick.start(400, 0);
  flick.move(410, 16);
  flick.move(440, 40);
  assert.equal(flick.end(450, 50).close, true);
});

test('抓手:上一次拖完没跟出 click,下一次真点按不被误吞', () => {
  const drag = createSheetDrag();
  drag.start(500, 0);
  drag.move(540, 50);
  drag.end(540, 400); // 回弹;松手在按钮外,浏览器没派 click
  drag.start(500, 1000);
  drag.end(500, 1050);
  assert.equal(drag.consumeClick(), false);
});

// ── ④ 菜单键位(M-35) ──
test('菜单:↑ ↓ 两端回绕,Home / End 跳首尾,焦点在容器上时 ↓ 落首项、↑ 落末项', () => {
  assert.equal(nextMenuIndex(5, 0, 'ArrowDown'), 1);
  assert.equal(nextMenuIndex(5, 4, 'ArrowDown'), 0);
  assert.equal(nextMenuIndex(5, 0, 'ArrowUp'), 4);
  assert.equal(nextMenuIndex(5, 2, 'Home'), 0);
  assert.equal(nextMenuIndex(5, 2, 'End'), 4);
  assert.equal(nextMenuIndex(5, -1, 'ArrowDown'), 0);
  assert.equal(nextMenuIndex(5, -1, 'ArrowUp'), 4);
  assert.equal(nextMenuIndex(5, 1, 'Tab'), null);
  assert.equal(nextMenuIndex(0, -1, 'ArrowDown'), null);
});

// ── ⑤ 焦点落点 ──
test('焦点:模态 / 底页 / 页面 / 浮层各自的缺省,调用方指定的目标优先', () => {
  const panel = { id: 'panel' };
  const item = { id: 'item' };
  assert.equal(initialFocusTarget(LAYER_KINDS.sheet.focus, { keyboard: true, panel }), null, '键盘打开底页:首个控件');
  assert.equal(initialFocusTarget(LAYER_KINDS.sheet.focus, { keyboard: false, panel }), panel, '指针打开底页:面板容器');
  assert.equal(initialFocusTarget(LAYER_KINDS.drawer.focus, { keyboard: false, panel }), panel);
  assert.equal(initialFocusTarget(LAYER_KINDS.page.focus, { keyboard: true, panel }), panel, '整屏页恒落面板');
  assert.equal(initialFocusTarget(LAYER_KINDS.modal.focus, { keyboard: false, panel }), null, 'Modal 维持原行为:首个控件');
  assert.equal(initialFocusTarget(LAYER_KINDS.sheet.focus, { keyboard: false, chosen: item, panel }), item);
  assert.equal(LAYER_KINDS.page.trap, false, '整屏页不困 Tab(下层已 inert,页面层之上的播放条要可达)');
  assert.equal(LAYER_KINDS.popover.inert, false, '浮层非模态,不 inert');
  assert.equal(LAYER_KINDS.sheet.inert, 'all');
  assert.equal(INERT_KEEP_SELECTOR.all, '[data-layer-keep="always"]');
});

// ── ⑥ 下层 inert ──
function node(name, children = []) {
  const n = {
    name,
    tagName: 'DIV',
    children,
    attrs: new Map(),
    hasAttribute: (k) => n.attrs.has(k),
    setAttribute: (k, v) => n.attrs.set(k, v),
    removeAttribute: (k) => n.attrs.delete(k),
    contains: (other) => other === n || n.children.some((c) => c.contains(other)),
  };
  return n;
}
const inertNames = (nodes) => nodes.filter((n) => n.hasAttribute('inert')).map((n) => n.name);

test('inert:层以外整片 inert,保留层自身 / Toast / 播放条及其祖先;还原后干净', () => {
  resetInertState();
  const list = node('list');
  const page = node('page');
  const dock = node('dock');
  const shell = node('shell', [list, page, dock]);
  const toast = node('toast');
  const header = node('header');
  const root = node('root', [header, shell, toast]);
  const portal = node('portal');
  const body = node('body', [root, portal]);
  const all = [list, page, dock, shell, toast, header, root, portal];
  const release = inertOthers([page, dock, toast], body);
  assert.deepEqual(inertNames(all).sort(), ['header', 'list', 'portal']);
  release();
  release(); // 幂等
  assert.deepEqual(inertNames(all), []);
});

test('inert:两层叠放按引用计数,上层关了下层的 inert 还在;原本就 inert 的节点不碰', () => {
  resetInertState();
  const list = node('list');
  const page = node('page');
  const shell = node('shell', [list, page]);
  const preset = node('preset');
  preset.setAttribute('inert', '');
  const root = node('root', [shell, preset]);
  const body = node('body', [root]);
  const releasePage = inertOthers([page], body);
  const sheet = node('sheet');
  body.children.push(sheet); // 上层(portal)是之后才挂上的,不在正文页那次计算里
  const releaseSheet = inertOthers([sheet], body);
  assert.deepEqual(inertNames([list, root, preset]).sort(), ['list', 'preset', 'root']);
  releaseSheet();
  assert.deepEqual(inertNames([list, root, preset]).sort(), ['list', 'preset'], '上层还原后正文页的 inert 仍在');
  releasePage();
  assert.deepEqual(inertNames([list, root, preset]), ['preset'], '原本就 inert 的节点保持原样');
});

// ── ⑦ 灯箱双指缩放(M-40) ──
test('双指缩放:两指之间那一点跟着手指走,缩放夹在 [1, 4]', () => {
  const center = { x: 200, y: 400 };
  const start = { s0: 1, t0: { x: 0, y: 0 }, m0: { x: 250, y: 380 }, d0: 100 };
  const out = pinchTransform(start, center, { x: 250, y: 380 }, 200);
  assert.equal(out.s, 2);
  // 起手中点对应的图片局部点 p = (m0 − c − t0) / s0 = (50, −20);缩放后它仍在屏幕 (250, 380)
  assert.equal(center.x + out.tx + out.s * 50, 250);
  assert.equal(center.y + out.ty + out.s * -20, 380);
  assert.equal(pinchTransform(start, center, start.m0, 1000).s, PINCH.max);
  assert.equal(pinchTransform(start, center, start.m0, 10).s, PINCH.min);
  assert.equal(clampScale(0.5), 1);
});

test('双指缩放:松手接近 1 倍即归位;放大后平移夹在图边不越过原图边的范围', () => {
  assert.deepEqual(settleTransform({ s: 1.02, tx: 30, ty: -12 }, { width: 300, height: 200 }), { s: 1, tx: 0, ty: 0 });
  assert.deepEqual(settleTransform({ s: 2, tx: 400, ty: -400 }, { width: 300, height: 200 }), { s: 2, tx: 150, ty: -100 });
  assert.deepEqual(settleTransform({ s: 2, tx: 20, ty: 10 }, { width: 300, height: 200 }), { s: 2, tx: 20, ty: 10 });
});

// ── ⑧ 遮罩护栏(lint) ──
const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const linter = new Linter({ cwd: frontendDir });
const scrimHits = (code, file) => linter
  .verify(code, eslintConfig, { filename: path.join(frontendDir, file) })
  .filter((m) => m.ruleId === 'dorami/no-raw-modal-overlay').length;

test('护栏:业务代码自写 .m-dim 遮罩被拦,层原语目录放行', () => {
  const raw = 'export const A = ({ close }) => <div className="m-dim" onClick={close} />;';
  assert.equal(scrimHits(raw, 'src/components/mobile/Foo.jsx'), 1);
  assert.equal(scrimHits('export const A = () => <div className={`m-dim ${"x"}`} />;', 'src/components/Foo.jsx'), 1);
  assert.equal(scrimHits(raw, 'src/components/layer/LayerScrim.jsx'), 0);
  assert.equal(scrimHits('export const A = () => <div className="m-dimmer m-drawer" />;', 'src/components/Foo.jsx'), 0);
});
