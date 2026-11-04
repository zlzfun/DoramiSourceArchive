// issue #230 阶段 3 批 4c「内容列表改成夜航卡」的护栏:卡片版式只在手机壳生效(桌面零变化),手机三处列表与长按预览卡
// 都走 ArticleRow 的 card 版式,日期组头不在长按包装里;长按松手的溢出点击不触发菜单项(卡片变高后矮屏手指正落在菜单项上)。
// 版式本身由沙箱 Playwright 截图与几何断言走查。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createSpillClickGuard } from '../src/utils/overlayClose.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.resolve(here, '../src', rel), 'utf8');
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

function section4c() {
  const css = read('mobile.css');
  const start = css.indexOf('批 4c:内容列表换成夜航卡');
  const end = css.indexOf('批 4c 结束');
  assert.ok(start > 0 && end > start, 'mobile.css 里找不到批 4c 段');
  return stripComments(css.slice(css.lastIndexOf('/*', start), end));
}

// 段里每条规则的选择器(逗号拆开)
function selectorsOf(css) {
  const out = [];
  for (const m of css.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
    const head = m[1].trim();
    if (!head || head.startsWith('@')) continue;
    // :is(a, b) 里的逗号不拆
    let depth = 0;
    let cur = '';
    for (const ch of head) {
      if (ch === '(') depth += 1;
      if (ch === ')') depth -= 1;
      if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    out.push(cur.trim());
  }
  return out.filter(Boolean);
}

test('批 4c 段的每个选择器都锚在手机壳上(.is-card / .m-*):桌面的条目行、社交流、骨架不受影响', () => {
  const sels = selectorsOf(section4c());
  assert.ok(sels.length > 30, sels.length);
  const loose = sels.filter((s) => !/\.is-card\b|\.m-[a-z]/.test(s));
  assert.deepEqual(loose, []);
});

test('护栏的反向对照:没锚在手机壳上的选择器会被抓到', () => {
  const sels = selectorsOf('.reader-entry { padding: 0; } .m-list .reader-entry, .social-post { margin: 0; }');
  assert.deepEqual(sels.filter((s) => !/\.is-card\b|\.m-[a-z]/.test(s)), ['.reader-entry', '.social-post']);
});

test('手机三处列表 + 长按预览卡用卡片版式;日期组头在长按包装之外;桌面列表不传 card', () => {
  for (const file of ['components/mobile/MobileReader.jsx', 'components/mobile/MobileSubsFlow.jsx']) {
    const src = read(file);
    const row = src.slice(src.indexOf('<ArticleRow'), src.indexOf('/>', src.indexOf('<ArticleRow')));
    assert.match(row, /\n\s*card\n/, `${file}: ArticleRow 带 card`);
    assert.match(row, /showLabel=\{false\}/, `${file}: 组头不由 ArticleRow 画`);
    assert.match(src, /\{entry\.showLabel && <div className="m-day">\{dayLabelOf\(entry\.dayKey\)\}<\/div>\}\s*<div className="m-press"/, `${file}: .m-day 在 .m-press 之前`);
    assert.equal((src.match(/<ArticleCardsSkeleton\b[^>]*card=/g) || []).length, 2, `${file}: 首屏与翻页骨架都是卡形`);
  }
  const peek = read('components/mobile/EntryPeek.jsx');
  assert.match(peek, /<ArticleRow[\s\S]*?\n\s*card\n[\s\S]*?\/>/);
  const reader = read('components/ReaderTab.jsx');
  const desk = reader.slice(reader.indexOf('export default function ReaderTab'));
  for (const m of desk.matchAll(/<(ArticleRow|ArticleCardsSkeleton)\b[\s\S]*?\/>/g)) assert.doesNotMatch(m[0], /\bcard\b/, m[0].slice(0, 60));
});

test('长按松手的溢出点击(M-01 余项):打开时那次按下还没松,松手补发的指针 click 吞掉;真点与键盘照常', () => {
  const click = (detail = 1) => ({ detail });
  // 长按:菜单在按住时弹出 → 松手(没有对应的按下)→ 补发的 click 落在菜单项上
  let g = createSpillClickGuard();
  g.onPointerUp();
  assert.equal(g.shouldSwallow(click()), true);
  // 只吞那一下:之后的真点击(先按下再松手)照常
  g.onPointerDown(); g.onPointerUp();
  assert.equal(g.shouldSwallow(click()), false);
  // 点按打开的菜单(⋯):打开前那次点击已经结束,第一下真点照常
  g = createSpillClickGuard();
  g.onPointerDown(); g.onPointerUp();
  assert.equal(g.shouldSwallow(click()), false);
  // 键盘激活(Enter / 空格合成的 click,detail 0)永远照常,哪怕长按还没松完
  g = createSpillClickGuard();
  g.onPointerUp();
  assert.equal(g.shouldSwallow(click(0)), false);
});

test('底页(层原语 Sheet)挂着溢出点击护栏:窗口捕获 pointerdown / pointerup,层根捕获阶段吞掉那一下', () => {
  const sheet = read('components/layer/Sheet.jsx');
  assert.match(sheet, /createSpillClickGuard\(\)/);
  assert.match(sheet, /addEventListener\('pointerdown', guard\.onPointerDown, true\)/);
  assert.match(sheet, /addEventListener\('pointerup', guard\.onPointerUp, true\)/);
  assert.match(sheet, /className=\{`m-sheet-layer[^`]*`\}[^>]*onClickCapture=\{onClickCapture\}/);
});
