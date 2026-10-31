// 视口单位护栏(issue #230 审计 M-30 · conventions §10「视口与安全区」):移动可达的高度一律 var(--vh-full)(= 100dvh,
// 不支持时退 100vh)或 dvh / svh——裸 vh 在手机上按「地址栏收起时」的最大视口算,弹窗 / 灯箱 / 整页会被地址栏裁掉一截。
//   ① mobile.css(只在手机壳生效)不写裸 vh;
//   ② index.css 里没有 dvh 兜底的裸 vh 冻结成计数,只许减不许增(存量是桌面 / 管理面整屏壳与登录页演出,登录页归 #214)。
// 「有兜底」= 同一个声明块里后面又用 dvh / svh / var(--vh-full) 重写了同一个属性(先写 vh 再写 dvh 的写法)。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '../src');

// 冻结于 2026-10-31(issue #230 批 4b):桌面 / 管理面整屏壳(app-top 系 4、台账 1、设置柜 2)、登录页演出 3(归 #214)、
// 桌面早报 .bp-top 1、榜单 / 早报状态块 2(手机档在 mobile.css 以 --vh-full 覆盖)
export const INDEX_BARE_VH_BASELINE = 13;

const BARE_VH = /(?<![a-z-])\d*\.?\d+vh\b/;

// 按声明块解析(codex 检视第 1 轮 P3:旧版逐行看「本行余下 + 下一行」,会把下一条独立规则里的同名 dvh 当兜底,
// 也认不出换行的属性值):逐字符走,注释换成等长空白、跳过字符串,每层块收自己的声明,块结束时判定。
export function bareViewportHeights(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
  const out = [];
  const stack = [[]];   // 每层块的声明 { prop, value, line }
  let buf = '';
  let bufLine = 1;
  let line = 1;
  const flush = () => {
    const decl = /^\s*([a-zA-Z-]+)\s*:\s*([\s\S]*?)\s*$/.exec(buf);
    if (decl) {
      const lead = buf.slice(0, buf.search(/\S/));
      stack[stack.length - 1].push({
        prop: decl[1].toLowerCase(),
        value: decl[2].replace(/\s+/g, ' '),
        line: bufLine + (lead.match(/\n/g) || []).length,
      });
    }
    buf = '';
  };
  const judge = (decls) => decls.forEach((d, i) => {
    if (d.prop.startsWith('--') || !BARE_VH.test(d.value)) return;   // token 定义本身(--vh-full 的 vh 兜底)不算
    const covered = decls.slice(i + 1)
      .some((e) => e.prop === d.prop && /\d+(?:d|s)vh\b|var\(--vh-full\)/.test(e.value));
    if (!covered) out.push({ line: d.line, text: `${d.prop}: ${d.value}` });
  });
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < text.length && text[j] !== ch) { if (text[j] === '\\') j += 1; j += 1; }
      if (buf === '') bufLine = line;
      buf += text.slice(i, j + 1);
      i = j;
      continue;
    }
    if (ch === '{') { buf = ''; stack.push([]); continue; }
    if (ch === '}') { flush(); if (stack.length > 1) judge(stack.pop()); continue; }
    if (ch === ';') { flush(); continue; }
    if (buf === '') bufLine = line;
    buf += ch;
    if (ch === '\n') line += 1;
  }
  return out;
}

test('视口单位:mobile.css 不写裸 vh', () => {
  const hits = bareViewportHeights(readFileSync(path.join(src, 'mobile.css'), 'utf8'));
  assert.deepEqual(hits, []);
});

test('视口单位:index.css 无兜底的裸 vh 只许减不许增', () => {
  const hits = bareViewportHeights(readFileSync(path.join(src, 'index.css'), 'utf8'));
  assert.ok(hits.length <= INDEX_BARE_VH_BASELINE,
    `index.css 无 dvh 兜底的裸 vh ${hits.length} 处,超过冻结的 ${INDEX_BARE_VH_BASELINE} 处:\n${hits.map((h) => `  ${h.line}: ${h.text}`).join('\n')}`);
  if (hits.length < INDEX_BARE_VH_BASELINE) {
    console.log(`[viewportUnits] index.css 现为 ${hits.length} 处,请把 INDEX_BARE_VH_BASELINE 调到 ${hits.length}`);
  }
});

test('视口单位:护栏的反向对照', () => {
  const css = [
    '.a { max-height: 92vh; }',
    '.b {',
    '  min-height: 100vh;',
    '  min-height: 100dvh;',
    '  height: calc(100vh - 8px);',
    '  height: calc(var(--vh-full) - 8px);',
    '  width: 50vw; /* 10vh */',
    '  padding: 4vh 4vw;',
    '}',
    '.c { max-height: 90vh; max-height: 90dvh; }',
    '.d { top: 16vh } .e { width: 100vw; }',
    '.f { height: 80vh; }',
    '.g { height: 80dvh; }',
    '.h {',
    '  max-height:',
    '    70vh;',
    '}',
    '.i { min-height: 100vh; &:hover { color: red; } content: "}"; min-height: 100svh; }',
  ].join('\n');
  // .f 的兜底不能来自下一条独立规则 .g;跨行的属性值按它起头那行记;嵌套规则与字符串里的括号不打断外层的块
  assert.deepEqual(bareViewportHeights(css).map((h) => h.line), [1, 8, 11, 12, 15]);
});
