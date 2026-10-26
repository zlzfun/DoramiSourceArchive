import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import articleImages from '../src/utils/remarkArticleImages.js';

const sourceUrl = 'https://openai.com/index/decisions-api';
const render = (text, url = sourceUrl) => renderToStaticMarkup(React.createElement(Markdown, {
  remarkPlugins: [[articleImages, { sourceUrl: url }]],
}, text));

test('archived relative images keep their content and resolve against the article origin', () => {
  const body = '## 体验演示\n\n麦克风关闭\n\n![](/_next/static/immutable/media/neutral.hash.webp)\n\n'
    + '![diagram](./diagram.png)\n\n![cdn](//images.example.com/pic.webp)';
  const html = render(body);
  assert.match(html, /src="https:\/\/openai.com\/_next\/static\/immutable\/media\/neutral.hash.webp"/);
  assert.match(html, /src="https:\/\/openai.com\/index\/diagram.png"/);
  assert.match(html, /src="https:\/\/images.example.com\/pic.webp"/);
  assert.ok(html.includes('体验演示') && html.includes('麦克风关闭'));
});

test('reference images resolve while ordinary links, citations and code remain untouched', () => {
  const html = render('![face][f]\n\n[f]: /face.webp\n\n[link](/docs) [1](#dorami-cite-1)\n\n```md\n![](/example.png)\n```');
  assert.match(html, /src="https:\/\/openai.com\/face.webp"/);
  assert.match(html, /href="\/docs"/);
  assert.match(html, /href="#dorami-cite-1"/);
  assert.ok(html.includes('![](/example.png)'));
});

test('missing or invalid article URLs do not invent a base, unsafe schemes remain blocked', () => {
  for (const url of ['', 'bad-url', 'file:///tmp/article']) {
    assert.match(render('![](/image.png)', url), /src="\/image.png"/);
  }
  assert.ok(!render('![](javascript:alert%281%29)').includes('src="javascript:'));
  assert.match(render('![](https://cdn.example.com/image.webp)'), /src="https:\/\/cdn.example.com\/image.webp"/);
});
