import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import gfm from 'remark-gfm';
import math from 'remark-math';
import katex from 'rehype-katex';
import paperLayout from '../src/utils/remarkPaperLayout.js';
import { excerptOf } from '../src/utils/readerText.js';

test('列表摘录不显示完整或截断的排版元信息', () => {
  assert.equal(excerptOf('[zqpl]: #zq1.zq4\n\n###### Abstract\n\nBody.'), 'Abstract Body.');
  assert.equal(excerptOf('Body.\n\n[zqpl]: #zq1.zq'), 'Body.');
});

function renderPaper(markdown, layout = true) {
  return renderToStaticMarkup(React.createElement(Markdown, {
    remarkPlugins: [gfm, math, ...(layout ? [paperLayout] : [])], rehypePlugins: [katex],
  }, markdown));
}

test('论文排版标记保留摘要标题、图注、对齐和强调，且只作用于紧邻块', () => {
  const markdown = [
    '[zqpl]: #zq1.zq4', '###### Abstract',
    'Normal abstract with **bold**, *italic* and ***both***.',
    '[zqpl]: #zq2.zq4', '**Figure 1:** Caption.',
    '[zqpl]: #zq5', 'Right paragraph.',
    'Normal paragraph.', '| Model | Score |', '| :--- | ---: |', '| Ours | $x^2$ |',
  ].join('\n\n').replace('| Model | Score |\n\n| :--- | ---: |\n\n', '| Model | Score |\n| :--- | ---: |\n');
  const html = renderPaper(markdown);
  assert.match(html, /<h6 class="paper-abstract-title paper-align-center">Abstract<\/h6>/);
  assert.match(html, /<p class="paper-caption paper-align-center"><strong>Figure 1:<\/strong> Caption.<\/p>/);
  assert.match(html, /<p class="paper-align-right">Right paragraph.<\/p>\n<p>Normal paragraph.<\/p>/);
  assert.match(html, /<strong>bold<\/strong>, <em>italic<\/em> and <em><strong>both<\/strong><\/em>/);
  assert.match(html, /<td style="text-align:right"><span class="katex">/);
  assert.ok(!html.includes('zqpl') && !html.includes('katex-error'));
  // Older/generic renderers still show all content without visible markers.
  assert.ok(!renderPaper(markdown, false).includes('zqpl'));
});

test('未知排版值不能注入任意属性，代码块内标记不被解释', () => {
  const html = renderPaper(
    '[zqpl]: #zq4.onclick\n\nParagraph.\n\n'
    + '[zqpl]: javascript:alert(1)\n\nAnother paragraph.\n\n'
    + '```text\n[zqpl]: #zq4\n```\n\nFinal paragraph.',
  );
  assert.ok(!html.includes('class="paper-') && !html.includes('onclick='));
  assert.match(html, /<p>Paragraph.<\/p>/);
  assert.match(html, /<code class="language-text">\[zqpl\]: #zq4/);
  assert.match(html, /<p>Final paragraph.<\/p>/);
});

test('代码块前的布局定义不覆盖语言，也不影响后续段落', () => {
  const html = renderPaper('[zqpl]: #zq4\n\n```python\nprint(1)\n```\n\nNormal.');
  assert.match(html, /<pre><code class="language-python">print\(1\)/);
  assert.ok(!html.includes('paper-align-'));
  assert.match(html, /<p>Normal.<\/p>/);
});

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
