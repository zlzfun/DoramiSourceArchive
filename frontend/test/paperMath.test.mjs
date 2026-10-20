import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import gfm from 'remark-gfm';
import math from 'remark-math';
import katex from 'rehype-katex';

test('论文表格的单竖线与范数经过 GFM 和 KaTeX 后仍为不同数学符号', () => {
  // Matches the converter contract tested by test_table_math_preserves_single_and_double_bars.
  const formula = String.raw`p(y\vert x)+\Vert x\Vert +\left\vert x\right\vert `;
  const html = renderToStaticMarkup(React.createElement(Markdown, {
    remarkPlugins: [gfm, math], rehypePlugins: [katex],
  }, `| Formula |\n| --- |\n| $${formula}$ |`));
  assert.ok(!html.includes('katex-error'));
  assert.match(html, /<table>/);
  assert.match(html, /<mo[^>]*>∣<\/mo>/);
  assert.match(html, /<mi[^>]*>∥<\/mi>/);
  assert.ok(html.includes(formula));
});
