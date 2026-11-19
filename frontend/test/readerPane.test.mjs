// 桌面阅读窗(issue #279 第三批)的结构护栏:吸顶栏收动作、标题区三行、速读卡桌面版式与页边、来源栏可收起。
// 手机壳共用 AiReadingCard / ListenEntries / ArticleRow / DiscoverPage,新行为都经参数只在桌面打开。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, rel), 'utf8');
const desktop = read('../src/components/ReaderTab.jsx');
const card = read('../src/components/AiReadingCard.jsx');
const css = read('../src/index.css');
const mobileDir = path.join(here, '../src/components/mobile');
const mobile = readdirSync(mobileDir).filter((name) => name.endsWith('.jsx')).map((name) => readFileSync(path.join(mobileDir, name), 'utf8')).join('\n');

test('吸顶栏:语言二段与查看原文排在收藏之前;标题区不再有尾行', () => {
  // 截取吸顶栏那一段:到阅读窗的 <article> 为止(issue #301 第二批起它不再带 reader-enter 类;收藏钮的处理函数改名 onPaneFavorite)
  const barStart = desktop.indexOf('<div className="reader-pane-bar">');
  const barEnd = desktop.indexOf('<article className="reader-pane"', barStart);
  assert.ok(barStart > 0 && barEnd > barStart, '找得到吸顶栏与阅读窗的起止');
  const bar = desktop.slice(barStart, barEnd);
  assert.ok(bar.indexOf('{paneTools}') > 0 && bar.indexOf('{paneTools}') < bar.indexOf('onClick={onPaneFavorite}'), '正文动作在收藏钮之前');
  const head = desktop.slice(desktop.indexOf('<header className={`reader-pane-head'), desktop.indexOf('</header>', desktop.indexOf('<header className={`reader-pane-head')));
  for (const gone of ['reader-pane-foot', 'reader-pane-byline', 'paneActions', 'reader-pane-tags']) {
    assert.ok(!head.includes(gone), `标题区不应再有 ${gone}`);
  }
  assert.ok(!desktop.includes('paneActions'), '尾行动作常量已拆掉');
  // 来源名一屏只写一次:吸顶栏被「返回」占用时眉题才带来源
  assert.match(desktop, /\[briefReturn \? \(sourceNameMap\[activeArticle\.source_id\] \|\| ''\) : '', kindKickerLabel\(activeArticle\) \|\| ''\]/);
});

test('速读卡:桌面版式经 page 打开,手机壳不传;页边与卡脚用同一份标签', () => {
  assert.match(card, /return props\.page \? <PageCard \{\.\.\.props\} \/> : <ClassicCard \{\.\.\.props\} \/>;/);
  assert.match(desktop, /<AiReadingCard\s+page\s+tags=\{paneTagsEl\}/);
  assert.match(desktop, /<ScoreMargin article=\{activeArticle\} podcast=\{podcastView\} tags=\{paneTagsEl\} \/>/);
  assert.ok(!/<AiReadingCard[^>]*\bpage\b/.test(mobile), '手机壳的速读卡不走桌面版式');
  assert.ok(!/<ListenEntries[^>]*\bquiet\b/.test(mobile), '手机壳的收听入口保留原说明');
  // 依据是点开的,不是悬停:卡头的分数钮带 aria-expanded
  const page = card.slice(card.indexOf('function PageCard'), card.indexOf('export function ScoreMargin'));
  assert.match(page, /className="reader-ai-dims"\s+aria-expanded=/);
  assert.ok(!page.includes('reader-ai-score-tip'), '桌面版式不再有悬停浮层');
  assert.match(page, /splitSentences\(primaryText\)/);
});

test('页边只在阅读窗够宽时出现;出了以后卡里的分数、依据、卡脚收起', () => {
  assert.match(css, /\.reader-col-read \{ container: reader-read \/ inline-size; \}/);
  assert.match(css, /\n\.reader-pane-side \{ display: none; \}/);
  const wide = css.slice(css.indexOf('@container reader-read (min-width: 992px) {'));
  const block = wide.slice(0, wide.indexOf('\n}\n'));
  for (const part of ['.reader-doc > .reader-pane-side { display: block;', '.reader-ai-summary.is-page .reader-ai-dims', '.reader-ai-summary.is-page .reader-ai-why', '.reader-ai-summary.is-page .reader-ai-foot']) {
    assert.ok(block.includes(part), part);
  }
});

test('来源栏可收起:记住选择;首行「全部订阅 / 全部兴趣」;预览订阅是栏头下的一枚小钮', () => {
  assert.match(desktop, /const SRC_FOLD_KEY = 'dorami-reader-src-fold';/);
  assert.match(desktop, /className=\{`reader-shell \$\{srcFolded && !widePage \? 'is-src-folded' : ''\}`\}/);
  assert.match(css, /\.reader-shell\.is-src-folded \{ grid-template-columns: var\(--vrail\) var\(--col-m\) minmax\(0, 1fr\); \}/);
  assert.match(desktop, /\{showSourceRows \? '全部订阅' : '全部兴趣'\}/);
  assert.ok(!desktop.includes('reader-sub-banner'), '桌面不再用整条订阅色带');
  assert.match(desktop, /<div className="reader-list-sub">/);
});

test('日期:桌面条目行与署名行不写连字符日期;手机卡片保持原写法', () => {
  assert.match(desktop, /formatRelativeTime\(article\.publish_date \|\| article\.fetched_date, '', \{ cn: !card \}\)/);
  assert.match(desktop, /\{formatReaderDate\(activeArticle\.publish_date\)\}/);
  assert.ok(!desktop.includes('formatPublishDate('), '署名行不再用连字符写法');
});
