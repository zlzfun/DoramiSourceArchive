// 悬停守卫棘轮(issue #230 审计 M-29 · conventions §10「悬停三分法」):装饰性 :hover 一律包在 @media (hover: hover) 里——
// 触屏上点按会留下「粘滞悬停」(iOS 点进正文再返回,那一行还留着悬停底色;悬停才改文案的钮点一下先翻文案)。
// 存量冻结成每个样式表的计数(BASELINE),只许减不许增:新增的 :hover 规则必须带守卫;
// 把存量包进守卫后请把这里的数字一起调低(测试会提示新的计数)。
// 守卫 = 祖先 at-rule 里有 (hover: hover) / (any-hover: hover) / (pointer: fine),或 Tailwind 的 @variant hover。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '../src');

// 冻结于 2026-10-31(issue #230 批 4b;原 248 + 7):存量多在桌面四栏 / 管理面;手机页面实际命中的装饰性悬停已包进守卫,
// 余下命中手机页面的只有收藏星(M-02)、发现页「已订阅」翻退订(M-03)、公告关闭钮(M-11)与 is-on / disabled 的「悬停复原」规则
export const BASELINE = { 'index.css': 217, 'mobile.css': 6 };

const GUARD = /^@media\b[^{]*\((?:any-)?hover\s*:\s*hover\)|^@media\b[^{]*\(pointer\s*:\s*fine\)|^@variant\s+hover\b/;

// 返回未守卫的 :hover 规则:[{ line, selector }]。按块解析(跳过注释与字符串),一条规则(选择器列表)记一次。
export function unguardedHover(css) {
  const out = [];
  const stack = []; // { prelude, guarded }
  let prelude = '';
  let preludeLine = 1;
  let line = 1;
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i];
    if (ch === '\n') line += 1;
    if (ch === '/' && css[i + 1] === '*') {
      const end = css.indexOf('*/', i + 2);
      const stop = end === -1 ? css.length : end + 2;
      for (let j = i; j < stop; j += 1) if (css[j] === '\n') line += 1;
      i = stop - 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < css.length && css[j] !== ch) { if (css[j] === '\\') j += 1; j += 1; }
      prelude += css.slice(i, j + 1);
      i = j;
      continue;
    }
    if (ch === '{') {
      const head = prelude.trim().replace(/\s+/g, ' ');
      const parentGuarded = stack.length ? stack[stack.length - 1].guarded : false;
      const guarded = parentGuarded || GUARD.test(head);
      if (!guarded && !head.startsWith('@') && head.includes(':hover')) out.push({ line: preludeLine, selector: head });
      stack.push({ prelude: head, guarded });
      prelude = '';
      continue;
    }
    if (ch === '}') { stack.pop(); prelude = ''; continue; }
    if (ch === ';') { prelude = ''; continue; }
    if (!prelude.trim() && !/\s/.test(ch)) preludeLine = line;
    prelude += ch;
  }
  return out;
}

const SHEETS = ['index.css', 'mobile.css'];

test('悬停守卫:未守卫的 :hover 规则只许减不许增(存量棘轮)', () => {
  for (const name of SHEETS) {
    const hits = unguardedHover(readFileSync(path.join(src, name), 'utf8'));
    assert.ok(
      hits.length <= BASELINE[name],
      `${name}: 未守卫的 :hover 规则 ${hits.length} 条,超过冻结的 ${BASELINE[name]} 条——新增悬停样式请包进 @media (hover: hover)`
        + `(功能性悬停入口在触屏常显,见 conventions §10)。新增的可能是:\n${hits.slice(-8).map((h) => `  ${name}:${h.line} ${h.selector}`).join('\n')}`,
    );
    if (hits.length < BASELINE[name]) {
      console.log(`[hoverGuard] ${name} 现为 ${hits.length} 条,低于冻结的 ${BASELINE[name]} 条:请把 test/hoverGuard.test.mjs 的 BASELINE 调到 ${hits.length}`);
    }
  }
});

test('悬停守卫:解析器的反向对照——裸 :hover 记一条,守卫内、注释与字符串里的不记', () => {
  const css = [
    '.a:hover { color: red; }',
    '@media (hover: hover) { .b:hover { color: red; } }',
    '@layer components { .c:hover, .c:focus { color: red; } @media (any-hover: hover) { .d:hover {} } }',
    '/* .e:hover { } */ .f::after { content: ".g:hover {"; }',
    '.h { &:hover { color: red; } }',
    '@media (hover: none) { .i:hover { color: red; } }',
    '@variant hover { .j:hover {} }',
    '@media (max-width: 767px) and (hover: hover) { .k:hover {} }',
  ].join('\n');
  assert.deepEqual(unguardedHover(css).map((h) => h.selector), ['.a:hover', '.c:hover, .c:focus', '&:hover', '.i:hover']);
  assert.deepEqual(unguardedHover(css).map((h) => h.line), [1, 3, 5, 6]);
});
