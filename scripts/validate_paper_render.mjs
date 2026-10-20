#!/usr/bin/env node
// Render converted samples through the same Markdown/KaTeX libraries as Reader.
// Usage: node scripts/validate_paper_render.mjs /tmp/paper-samples
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import paperLayout from '../frontend/src/utils/remarkPaperLayout.js';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { default: Markdown } = await import(require.resolve('react-markdown'));
const { default: gfm } = await import(require.resolve('remark-gfm'));
const { default: breaks } = await import(require.resolve('remark-breaks'));
const { default: math } = await import(require.resolve('remark-math'));
const { default: katex } = await import(require.resolve('rehype-katex'));
const directory = process.argv[2];
if (!directory) throw new Error('Provide the sample output directory');
const rows = [];
for (const file of fs.readdirSync(directory).filter((name) => name.endsWith('.md')).sort()) {
  const html = renderToStaticMarkup(React.createElement(Markdown, {
    remarkPlugins: [gfm, breaks, math, paperLayout], rehypePlugins: [katex],
  }, fs.readFileSync(path.join(directory, file), 'utf8')));
  const errors = [...html.matchAll(/class="katex-error"[^>]*title="([^"]*)"/g)].map((m) => m[1]);
  rows.push({
    arxiv_id: file.slice(0, -3), rendered_math: (html.match(/class="katex"/g) || []).length,
    errors,
  });
}
fs.writeFileSync(path.join(directory, 'render-report.json'), JSON.stringify(rows, null, 2));
console.log(JSON.stringify({
  papers: rows.length, rendered_math: rows.reduce((n, row) => n + row.rendered_math, 0),
  errors: rows.reduce((n, row) => n + row.errors.length, 0),
}));
if (rows.some((row) => row.errors.length)) process.exitCode = 1;
