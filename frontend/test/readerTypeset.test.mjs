// 排印(issue #279 第五批):大字标题的标点挤压只拆不改;中西文间隙只开在桌面壳上。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { punctParts } from '../src/utils/readerText.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, rel), 'utf8');

test('标点挤压:拆成文字 / 收尾标点 / 起首标点,拼回去与原文逐字相等', () => {
  const title = 'Fable 5.2深夜掀桌！从0造操作系统，性价比反超「老对手」';
  const parts = punctParts(title);
  assert.equal(parts.map((p) => p.text).join(''), title);
  assert.deepEqual(parts.filter((p) => p.kind).map((p) => [p.text, p.kind]), [['！', 'close'], ['，', 'close'], ['「', 'open']]);
});

test('标点挤压:句末的收尾标点不收;句首的起首标点照收;连续标点各自成段', () => {
  assert.deepEqual(punctParts('全球解禁！'), [{ text: '全球解禁！', kind: '' }]);
  assert.deepEqual(punctParts('「碳」全球解禁').slice(0, 3), [{ text: '「', kind: 'open' }, { text: '碳', kind: '' }, { text: '」', kind: 'close' }]);
  assert.deepEqual(punctParts('他说：「好」').filter((p) => p.kind).map((p) => p.kind), ['close', 'open']);
  // 成对标点两侧对称:句中的收尾一侧也收;落在句末的那个按 keepTail 不收
  assert.deepEqual(punctParts('宣布「开源」模型').filter((p) => p.kind).map((p) => [p.text, p.kind]), [['「', 'open'], ['」', 'close']]);
  assert.deepEqual(punctParts('《新规》（试行）发布').filter((p) => p.kind).map((p) => p.text), ['《', '》', '（', '）']);
});

test('标点挤压:西文标题、空值原样', () => {
  assert.deepEqual(punctParts("Life's open weights, what's next?"), [{ text: "Life's open weights, what's next?", kind: '' }]);
  assert.deepEqual(punctParts(''), []);
  assert.deepEqual(punctParts(null), []);
});

test('DisplayTitle 只用在桌面的标题上;中西文间隙只开在桌面壳上', () => {
  // 播客标题 24px:只按词断行,不挤压
  assert.match(read('../src/components/ReaderTab.jsx'), /className="reader-pane-title"[^>]*><DisplayTitle text=\{paneTitle\.title\} squeeze=\{!podcastView\} \/>/);
  // 西文标题不分块也不挤压
  assert.match(read('../src/components/DisplayTitle.jsx'), /if \(latinLang\(text\)\) return text;/);
  assert.match(read('../src/components/RankingsPage.jsx'), /\{rankOnly \? <DisplayTitle text=\{shown\.title\} \/> : shown\.title\}/);
  const brief = read('../src/components/PersonalBriefPage.jsx');
  // 早报:桌面头条(挤压 + 按词断行)与桌面条目标题(只按词断行);手机分支仍是纯文本
  assert.equal((brief.match(/<DisplayTitle /g) || []).length, 2);
  assert.match(brief, /\{mobile \? view\.title : <DisplayTitle text=\{view\.title\} squeeze=\{false\} \/>\}/);
  const mobileDir = path.join(here, '../src/components/mobile');
  const mobile = readdirSync(mobileDir).filter((n) => n.endsWith('.jsx')).map((n) => readFileSync(path.join(mobileDir, n), 'utf8')).join('\n');
  assert.ok(!mobile.includes('DisplayTitle'));
  const css = read('../src/index.css') + read('../src/mobile.css');
  assert.match(css, /\n\.reader-shell \{ text-autospace: normal; \}/);
  assert.equal((css.match(/\{ text-autospace: normal; \}/g) || []).length, 1, '只有桌面壳一处开启');
});
