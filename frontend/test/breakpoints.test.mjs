// 命名断点护栏(issue #230 移动规范):CSS 的 @custom-variant bp-* 与 JS 的 utils/breakpoints.js 逐字一致;
// index.css / mobile.css 不再出现裸宽度 @media(私有例外须在同一行带 bp-exempt 注记并写明归属);
// src 里的 JS 不再手写宽度媒体查询字符串(一律从 utils/breakpoints.js 取)。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { BREAKPOINTS, MEDIA } from '../src/utils/breakpoints.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '../src');
const read = (rel) => readFileSync(path.join(src, rel), 'utf8');
const SHEETS = { 'index.css': read('index.css'), 'mobile.css': read('mobile.css') };

export function customVariants(css) {
  const out = {};
  for (const m of css.matchAll(/@custom-variant\s+(bp-[a-z-]+)\s+\(@media\s+(.+?)\);/g)) out[m[1]] = m[2];
  return out;
}

export function rawWidthMedia(css) {
  return css.split('\n')
    .map((line, i) => ({ line, n: i + 1 }))
    .filter(({ line }) => /@media[^{]*\((?:max|min)-width\s*:/.test(line)
      && !/^\s*@custom-variant\s+bp-/.test(line)   // 命名断点的定义本身
      && !/bp-exempt:/.test(line));
}

test('断点:index.css 的 @custom-variant bp-* 与 utils/breakpoints.js 的 MEDIA 一一对应、逐字一致', () => {
  const css = customVariants(SHEETS['index.css']);
  assert.deepEqual(Object.keys(css).sort(), Object.keys(MEDIA).sort());
  for (const [name, query] of Object.entries(MEDIA)) assert.equal(css[name], query, name);
  assert.equal(MEDIA['bp-shell'], `(max-width: ${BREAKPOINTS.shell}px)`);
  assert.equal(MEDIA['bp-desktop'], `(min-width: ${BREAKPOINTS.shell + 1}px)`, '桌面壳恰是手机壳的反面,1024 那一档不再两边都成立');
});

test('断点:样式表里没有裸宽度 @media(私有例外带 bp-exempt 注记)', () => {
  for (const [name, css] of Object.entries(SHEETS)) {
    const hits = rawWidthMedia(css);
    assert.deepEqual(hits, [], `${name}: ${hits.map((h) => `${h.n}: ${h.line.trim()}`).join(' | ')}`);
  }
  const exempt = Object.values(SHEETS).join('\n').split('\n').filter((line) => /bp-exempt:/.test(line));
  assert.ok(exempt.length <= 2, `私有例外只有登录页 880(#214)与榜单 1120(#221):${exempt.join(' | ')}`);
});

test('断点:护栏的反向对照——裸 @media 与漂移的 variant 都能被抓到', () => {
  assert.equal(rawWidthMedia('.a{}\n@media (max-width: 1023px) {\n').length, 1);
  assert.equal(rawWidthMedia('@media (hover: hover) {\n@media (max-width: 900px) { /* bp-exempt: x */\n').length, 0);
  assert.equal(customVariants('@custom-variant bp-shell (@media (max-width: 1023px));')['bp-shell'], '(max-width: 1023px)');
  assert.notEqual(customVariants('@custom-variant bp-shell (@media (max-width: 1023px));')['bp-shell'], MEDIA['bp-shell']);
});

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

test('断点:JS 不手写宽度媒体查询,一律取 utils/breakpoints.js', () => {
  const offenders = walk(src)
    .filter((file) => /\.(jsx?|mjs)$/.test(file) && !file.endsWith(path.join('utils', 'breakpoints.js')))
    .filter((file) => /['"`]\((?:max|min)-width:\s*\d/.test(readFileSync(file, 'utf8')))
    .map((file) => path.relative(src, file));
  assert.deepEqual(offenders, []);
});
