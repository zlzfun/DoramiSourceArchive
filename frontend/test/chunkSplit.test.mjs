// 首屏分包护栏(issue #230 审计 M-14):登录页与读者首屏不得静态依赖图表库(recharts)与 Markdown / KaTeX 渲染器。
// 真跑一次生产构建(write: false,不落盘),从入口 chunk 沿静态 import 走完首屏会 modulepreload 的闭包:
//   ① 闭包里不能有 vendor-charts / vendor-markdown,也不能含 recharts / react-markdown / katex / remark / rehype 的模块;
//   ② 阅读器(ReaderWorkspace)与分享页的静态闭包同样不含 Markdown / 图表(Markdown 只经 ReaderMarkdownView 动态拉);
//   ③ 业务代码只经 components/ReaderMarkdown.jsx(→ utils/readerMarkdownLoader.js)拿渲染器,不直接 import ReaderMarkdownView。
// 反向对照(文件末尾):退回修复前的同级 manualChunks,react 会被 recharts 拖进 vendor-charts、入口随之静态依赖它,
// 第一条断言即红——护栏确实咬人。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from 'vite';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const HEAVY = /[\\/]node_modules[\\/](recharts|react-markdown|katex|remark-[^\\/]+|rehype-[^\\/]+|d3-[^\\/]+|victory-vendor)[\\/]/;

async function bundle(mutate) {
  const result = await build({
    root,
    configFile: path.join(root, 'vite.config.js'),
    logLevel: 'silent',
    build: { write: false, minify: false, reportCompressedSize: false },
    plugins: mutate ? [{ name: 'chunk-split-mutation', config: mutate }] : [],
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((r) => r.output);
  return outputs.filter((item) => item.type === 'chunk');
}

function closure(chunks, start) {
  const byFile = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
  const seen = new Set();
  const stack = [start.fileName];
  while (stack.length) {
    const file = stack.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    for (const next of byFile.get(file)?.imports || []) stack.push(next);
  }
  return [...seen].map((file) => byFile.get(file)).filter(Boolean);
}

function heavyIn(chunks) {
  return chunks.flatMap((chunk) => [
    ...(/vendor-(charts|markdown)/.test(chunk.fileName) ? [chunk.fileName] : []),
    ...chunk.moduleIds.filter((id) => HEAVY.test(id)).slice(0, 3),
  ]);
}

const byModule = (chunks, rel) => chunks.find((chunk) => chunk.facadeModuleId?.endsWith(rel));

let built;
const chunksOnce = () => (built ||= bundle());

test('分包:入口(登录页 / 读者首屏)的静态闭包不含图表与 Markdown / KaTeX', async () => {
  const chunks = await chunksOnce();
  const entry = chunks.find((chunk) => chunk.isEntry);
  assert.ok(entry, '找不到入口 chunk');
  assert.deepEqual(heavyIn(closure(chunks, entry)), []);
});

test('分包:阅读器与分享页的静态闭包不含 Markdown / 图表;图表只随运维台、Markdown 只随 ReaderMarkdownView', async () => {
  const chunks = await chunksOnce();
  for (const rel of ['components/ReaderWorkspace.jsx', 'components/SharedArticlePage.jsx']) {
    const chunk = byModule(chunks, rel);
    assert.ok(chunk, `${rel} 应是独立的懒加载 chunk`);
    assert.ok(!chunk.isEntry);
    assert.deepEqual(heavyIn(closure(chunks, chunk)), [], rel);
  }
  const view = byModule(chunks, 'components/ReaderMarkdownView.jsx');
  assert.ok(view && closure(chunks, view).some((chunk) => /vendor-markdown/.test(chunk.fileName)), 'ReaderMarkdownView 才带 vendor-markdown');
  const ops = byModule(chunks, 'components/AdminOpsTab.jsx');
  assert.ok(ops && closure(chunks, ops).some((chunk) => /vendor-charts/.test(chunk.fileName)), '运维台才带 vendor-charts');
});

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

test('分包:业务代码不直接 import ReaderMarkdownView(只经 readerMarkdownLoader 动态拉)', () => {
  const src = path.join(root, 'src');
  const offenders = walk(src)
    .filter((file) => /\.(jsx?|mjs)$/.test(file))
    .filter((file) => /from\s+['"][^'"]*ReaderMarkdownView(\.jsx)?['"]/.test(readFileSync(file, 'utf8')))
    .map((file) => path.relative(src, file));
  assert.deepEqual(offenders, []);
  const loader = readFileSync(path.join(src, 'utils/readerMarkdownLoader.js'), 'utf8');
  assert.match(loader, /import\(\s*['"]\.\.\/components\/ReaderMarkdownView\.jsx['"]\s*\)/);
});

test('分包:护栏的反向对照——退回旧的同级分组(react 被 recharts 拖进 vendor-charts),入口闭包会带上图表库', async () => {
  // 原样复刻修复前的 manualChunks:三组同级、按发现顺序抢,react 被 recharts 的依赖递归并走
  const chunks = await bundle((config) => {
    const output = config.build.rolldownOptions.output;
    delete output.codeSplitting;
    output.manualChunks = (id) => {
      if (!id.includes('node_modules')) return undefined;
      if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react';
      if (/[\\/]node_modules[\\/](recharts|d3-[^\\/]+|victory-vendor)[\\/]/.test(id)) return 'vendor-charts';
      return undefined;
    };
  });
  const entry = chunks.find((chunk) => chunk.isEntry);
  assert.ok(heavyIn(closure(chunks, entry)).length > 0, '退回同级分组后,入口应静态依赖 vendor-charts');
});
