// issue #230 阶段 3 批 4a「交互与版式收尾」的纯逻辑与样式护栏:空态动作、theme-color 取色、
// 平板版心 / 左右让位 token、手机样式表不再手写 hex 与像素圆角(审计 M-37 的棘轮)、长按动作单一律浮动卡。
// React 外壳(公告占位、长按预览卡、推入页退场、跨版式早报日期)由沙箱 Playwright 走查。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { EMPTY_ACTION_LABELS, emptyActionKind } from '../src/utils/emptyAction.js';
import { hexOfColor } from '../src/utils/themeColor.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.resolve(here, '../src', rel), 'utf8');

test('空态动作(M-41):为什么空就给哪一步——搜索 > 只看未读 > 类型筛选 > 去发现', () => {
  assert.equal(emptyActionKind({ searchQuery: 'x', unreadOnly: true, kindFilter: 'paper' }), 'clearSearch');
  assert.equal(emptyActionKind({ unreadOnly: true, kindFilter: 'paper' }), 'showAll');
  assert.equal(emptyActionKind({ kindFilter: 'paper' }), 'allKinds');
  assert.equal(emptyActionKind({}), 'discover');
  assert.equal(emptyActionKind({ noSources: true }), 'discoverSocial');
  for (const kind of ['clearSearch', 'showAll', 'allKinds', 'discover', 'discoverSocial']) assert.ok(EMPTY_ACTION_LABELS[kind], kind);
});

// 假 document:canvas 的 2D 上下文按 fillStyle 回一个像素(认不出的颜色保持上一个值,同浏览器)
function fakeDoc(table) {
  return {
    createElement: () => {
      let style = 'transparent';
      return {
        getContext: () => ({
          set fillStyle(v) { if (v in table) style = v; },
          get fillStyle() { return style; },
          fillRect() {},
          getImageData: () => ({ data: table[style] }),
        }),
      };
    },
  };
}

test('theme-color(M-32):任意 CSS 颜色经 canvas 规整成 #rrggbb;认不出 / 全透明不改', () => {
  const doc = fakeDoc({ transparent: [0, 0, 0, 0], 'rgb(248, 249, 251)': [248, 249, 251, 255], 'color(srgb 0.06 0.07 0.08)': [16, 17, 21, 255] });
  assert.equal(hexOfColor('rgb(248, 249, 251)', doc), '#f8f9fb');
  assert.equal(hexOfColor('color(srgb 0.06 0.07 0.08)', doc), '#101115');
  assert.equal(hexOfColor('oklch(bogus)', doc), '');
  assert.equal(hexOfColor('', doc), '');
  assert.equal(hexOfColor('rgb(1, 2, 3)', null), '');
});

test('版心与左右让位(M-08 / M-12):--m-side-l / -r = max(安全区, 版心留白),平板档才有版心留白', () => {
  const css = read('index.css');
  assert.match(css, /--m-col: 680px;/);
  assert.match(css, /--m-col-pad: 0px;/);
  assert.match(css, /--m-side-l: max\(var\(--sal\), var\(--m-col-pad\)\);/);
  assert.match(css, /--m-side-r: max\(var\(--sar\), var\(--m-col-pad\)\);/);
  assert.match(css, /@variant bp-tablet \{\s*:root \{ --m-col-pad: max\(0px, calc\(\(100vw - var\(--m-col\)\) \/ 2\)\); \}/);
  // 手机壳里贴左右的几何不再直接用安全区(批 4a 段统一换成 --m-side-*):页头、底栏、浮动底页、坞
  const mobile = read('mobile.css');
  for (const rule of ['.m-head { left: var(--m-side-l); right: var(--m-side-r); }', '.m-tabbar { left: calc(var(--m-gutter) + var(--m-side-l))']) {
    assert.ok(mobile.includes(rule), rule);
  }
});

// 去掉注释后再扫(注释里的「issue #199」之类不算颜色)
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

test('手机样式表护栏(M-37):mobile.css 不手写 hex 颜色、不手写像素圆角(0 与 50% 除外)', () => {
  const css = stripComments(read('mobile.css'));
  const hex = css.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepEqual(hex, []);
  const radii = [...css.matchAll(/border-radius:\s*([^;]+);/g)].map((m) => m[1].trim())
    .filter((v) => !/^(0|50%|inherit)$/.test(v) && !/^var\(--r-/.test(v) && !/^0 0 var\(--r-|^var\(--r-[a-z]+\) var\(--r-/.test(v));
  assert.deepEqual(radii, []);
});

test('长按动作单一律是浮动圆角卡(样页 sheet 屏;批 3c 的 is-float),预览卡走 Sheet 的 aside 槽', () => {
  const reader = read('components/mobile/MobileReader.jsx');
  assert.match(reader, /<ActionSheet[\s\S]*?className="is-float"[\s\S]*?\/>/);
  const sheet = read('components/mobile/ActionSheet.jsx');
  assert.match(sheet, /aside=\{preview \? <div className="m-sheet-peek" aria-hidden="true" inert>/);
});

test('收藏星(M-02 / M-35):条目行里不再有嵌在 <button> 里的 span[role=button](星与「+ 订阅」)', () => {
  const row = read('components/ReaderTab.jsx');
  const start = row.indexOf('export const ArticleRow');
  const end = row.indexOf('export default function ReaderTab');
  const body = row.slice(start, end);
  assert.ok(start > 0 && end > start);
  // 收藏星与「+ 订阅」都不再是 role=button / tabIndex(点到它们由整行的点击分流)
  assert.doesNotMatch(body, /role="button"/);
  assert.doesNotMatch(body, /tabIndex=\{0\}/);
});
