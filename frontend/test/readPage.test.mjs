// 手机正文页(issue #230 批 3c「正文页」)的纯逻辑:「⋯」菜单按文章能力过滤、右上镀层钮随滚动退场、
// 入栈动效的来处(卡片矩形 → clip-path inset)。React 外壳(components/mobile/MobileArticlePage)由沙箱 Playwright 走查。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { READ_HEAD_HIDE_AT, readHeadHidden, readMenuSpec } from '../src/utils/readPage.js';
import {
  READ_ORIGIN_MAX_AGE,
  READ_ORIGIN_SELECTOR,
  clipInset,
  insetPath,
  rememberReadOrigin,
  takeReadOrigin,
} from '../src/utils/readTransition.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, rel), 'utf8');
const keys = (items) => items.map((item) => item.type || item.key);
const labels = (items) => items.filter((item) => !item.type).map((item) => item.label);

// ── 「⋯」菜单(样页 read-menu,第 4 轮)──
test('⋯ 菜单:中文文章不出翻译项;顺序 = 查看原文 | 复制站内链接 | 标为未读 · 标记该源全部已读', () => {
  const items = readMenuSpec({});
  assert.deepEqual(keys(items), ['open', 'sep', 'copy', 'sep', 'read', 'markall']);
  assert.deepEqual(labels(items), ['查看原文', '复制站内链接', '标为未读', '标记该源全部已读']);
});

test('⋯ 菜单:有英文原文且 AI 可用时翻译排第一,文案随视图 / 缓存 / 在途变化', () => {
  assert.deepEqual(keys(readMenuSpec({ translatable: true })), ['translate', 'open', 'sep', 'copy', 'sep', 'read', 'markall']);
  const label = (opts) => readMenuSpec({ translatable: true, ...opts })[0].label;
  assert.equal(label({}), '译为中文');
  assert.equal(label({ translationOn: true, hasTranslation: true }), '显示原文');
  assert.equal(label({ hasTranslation: true }), '显示中文');
  assert.equal(label({ podcast: true }), '将节目简介译为中文');
  assert.equal(label({ translating: true }), '译为中文（翻译中）');
  assert.equal(label({ translationOn: true, translating: true }), '显示原文（翻译中）');
});

test('⋯ 菜单:正文没到时翻译项置灰不隐藏;没有原文链接时「查看原文」置灰', () => {
  const items = readMenuSpec({ translatable: true, bodyReady: false, hasSource: false });
  assert.equal(items.find((i) => i.key === 'translate').disabled, true);
  assert.equal(items.find((i) => i.key === 'open').disabled, true);
  const ok = readMenuSpec({ translatable: true });
  assert.equal(ok.find((i) => i.key === 'translate').disabled, false);
  assert.equal(ok.find((i) => i.key === 'open').disabled, false);
});

test('⋯ 菜单:支持系统分享才画「分享给…」,紧跟复制站内链接;播客的原文是节目页面;未读时是「标为已读」', () => {
  assert.deepEqual(keys(readMenuSpec({ canShare: true })), ['open', 'sep', 'copy', 'sysshare', 'sep', 'read', 'markall']);
  assert.equal(readMenuSpec({ canShare: true }).find((i) => i.key === 'sysshare').label, '分享给…');
  assert.equal(readMenuSpec({ podcast: true }).find((i) => i.key === 'open').label, '打开节目页面');
  assert.equal(readMenuSpec({ unread: true }).find((i) => i.key === 'read').label, '标为已读');
  // 收藏与分享浮层在右上角,菜单里不重复
  assert.ok(!keys(readMenuSpec({ translatable: true, canShare: true })).some((k) => k === 'fav' || k === 'share'));
});

// ── 右上镀层钮随滚动退场 ──
test('镀层钮:越过 80px 往下读藏起,往上一划回来,回到顶部 80px 内常显', () => {
  assert.equal(readHeadHidden(false, { y: 200, dy: 10 }), true);
  assert.equal(readHeadHidden(true, { y: 200, dy: -10 }), false);
  assert.equal(readHeadHidden(true, { y: 200, dy: 3 }), true);     // 小抖动不改状态
  assert.equal(readHeadHidden(false, { y: 200, dy: -3 }), false);
  assert.equal(readHeadHidden(true, { y: READ_HEAD_HIDE_AT - 1, dy: 20 }), false);
});

test('镀层钮:减少动效时常显;不是读者自己滚的(位置恢复 / 译文对位)不改状态', () => {
  assert.equal(readHeadHidden(true, { y: 400, dy: 30, reduced: true }), false);
  assert.equal(readHeadHidden(false, { y: 400, dy: 30, intent: false }), false);
  assert.equal(readHeadHidden(true, { y: 400, dy: -30, intent: false }), true);
});

// ── 入栈动效的来处 ──
const frame = { left: 0, top: 0, width: 390, height: 844 };

test('来处矩形 → clip-path inset:屏内卡片按四边距算;部分出屏夹到屏边', () => {
  assert.deepEqual(clipInset({ top: 200, left: 12, right: 378, bottom: 330 }, frame), { top: 200, right: 12, bottom: 514, left: 12 });
  assert.deepEqual(clipInset({ top: -40, left: 12, right: 378, bottom: 90 }, frame), { top: 0, right: 12, bottom: 754, left: 12 });
  assert.deepEqual(clipInset({ top: 800, left: 12, right: 378, bottom: 900 }, frame), { top: 800, right: 12, bottom: 0, left: 12 });
});

test('来处矩形:完全在屏外或露出不到 12px 时没有来处(退回普通推入)', () => {
  assert.equal(clipInset({ top: 900, left: 0, right: 390, bottom: 1000 }, frame), null);
  assert.equal(clipInset({ top: -200, left: 0, right: 390, bottom: -10 }, frame), null);
  assert.equal(clipInset({ top: 838, left: 0, right: 390, bottom: 940 }, frame), null);
  assert.equal(clipInset(null, frame), null);
});

test('inset 串带圆角', () => {
  assert.equal(insetPath({ top: 200, right: 12, bottom: 514, left: 12 }, 14), 'inset(200px 12px 514px 12px round 14px)');
});

const fakeCard = (rect) => ({ getBoundingClientRect: () => rect });
const fakeTarget = (card) => ({ closest: (selector) => (selector === READ_ORIGIN_SELECTOR ? card : null) });

test('来处:点到卡片记矩形,取走一次即清空;过期当没有', () => {
  const card = fakeCard({ top: 100, left: 12, right: 378, bottom: 220 });
  rememberReadOrigin(fakeTarget(card), 1000);
  const origin = takeReadOrigin(1500);
  assert.equal(origin.el, card);
  assert.deepEqual(origin.rect, { top: 100, left: 12, right: 378, bottom: 220 });
  assert.equal(takeReadOrigin(1600), null);
  rememberReadOrigin(fakeTarget(card), 1000);
  assert.equal(takeReadOrigin(1000 + READ_ORIGIN_MAX_AGE + 1), null);
});

test('来处:点到卡片以外不覆盖上一次的记录', () => {
  const card = fakeCard({ top: 10, left: 0, right: 100, bottom: 60 });
  rememberReadOrigin(fakeTarget(card), 2000);
  assert.equal(rememberReadOrigin(fakeTarget(null), 2100), null);
  assert.equal(rememberReadOrigin(null, 2100), null);
  assert.equal(takeReadOrigin(2200).el, card);
});

test('来处:条目卡 / 早报条目与头条 / 榜单行都算,另留 data-read-origin 给推入页', () => {
  for (const part of ['[data-read-origin]', '.reader-entry', '.bp-item', '.bp-head', '.ranking-content-row']) {
    assert.ok(READ_ORIGIN_SELECTOR.includes(part), part);
  }
});

// ── 结构护栏(拍板 3 / 7 / 26,审计 M-45)──
test('正文页:顶栏不再有「译」与「查看来源」钮,文末没有上一篇 / 下一篇;「⋯」是只有菜单的底页', () => {
  const src = read('../src/components/mobile/MobileArticlePage.jsx');
  assert.ok(!src.includes('reader-tr-glyph'), '译钮已撤出顶栏');
  assert.ok(!src.includes('m-read-pager') && !src.includes('prevArticle'), '文末无上一篇 / 下一篇');
  assert.ok(!src.includes('查看来源'), '查看原文进「⋯」与文末');
  assert.match(src, /label="更多操作"/);
  assert.match(src, /className="is-float"/);
  const css = read('../src/mobile.css');
  assert.ok(!css.includes('.m-read-pager') && !css.includes('.m-pager-btn'), '翻页样式已删');
});
