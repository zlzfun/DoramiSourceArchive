// 列表与早报(issue #279 第四批)的护栏:条目行按分值收放、早报目录与两栏、导轨标签常驻、问答入口进导轨。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { rowTierOf } from '../src/utils/analysis.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, rel), 'utf8');
const desktop = read('../src/components/ReaderTab.jsx');
const brief = read('../src/components/PersonalBriefPage.jsx');
const panel = read('../src/components/ReaderAiPanel.jsx');
const css = read('../src/index.css');
const mobileDir = path.join(here, '../src/components/mobile');
const mobile = readdirSync(mobileDir).filter((name) => name.endsWith('.jsx')).map((name) => readFileSync(path.join(mobileDir, name), 'utf8')).join('\n');

test('篇幅档位:8 分及以上两行、6–8 分一行、6 分以下只留标题;没有分数不算低分', () => {
  assert.equal(rowTierOf(9.2), 'full');
  assert.equal(rowTierOf(8), 'full');
  assert.equal(rowTierOf(7.99), 'mid');
  assert.equal(rowTierOf(6), 'mid');
  assert.equal(rowTierOf(5.9), 'low');
  assert.equal(rowTierOf(0), 'low');
  for (const none of [null, undefined, '', 'abc', NaN]) assert.equal(rowTierOf(none), 'full');
  assert.equal(rowTierOf('7.2'), 'mid');
});

test('条目行:手机卡片、播客、紧凑行、搜索、等高偏好都不参与收放;低档不渲染摘要', () => {
  assert.match(desktop, /const rowTier = card \|\| entryPodcast \|\| compact \|\| evenRows \|\| searchQuery \? 'full' : rowTierOf\(primaryScore\?\.value\);/);
  assert.match(desktop, /\{excerpt && rowTier !== 'low' && <span className="reader-entry-excerpt">/);
  // 设置开关只在桌面设置柜出现
  assert.match(read('../src/components/SettingsModal.jsx'), /<ReadingSection desktop \/>/);
  assert.ok(!/<ReadingSection[^>]*desktop/.test(mobile), '移动设置栈不出「一律等高」');
  // 新样式锚在桌面壳上
  assert.match(css, /\n\.reader-shell \.reader-entry\.is-mid \.reader-entry-excerpt \{/);
  assert.ok(!/\n\s*\.reader-entry\.is-(mid|low)/.test(css), '收放样式不应有不带桌面锚点的写法');
});

test('早报:目录只在桌面、多于一个板块时出现;条数只写在目录里', () => {
  assert.match(brief, /\{!mobile && showEdition && acts\.length > 1 && \(\s*<nav className="bp-toc" aria-label="本期板块">/);
  assert.match(brief, /\{\(mobile \|\| acts\.length <= 1\) && <p className="bp-act-count">/);
  assert.match(brief, /onScroll=\{mobile\s+\? \(event\) => \{ recordProgress\(event\); noteScroll\(event\); \}\s+: \(event\) => \{ recordProgress\(event\); noteScroll\(event\); syncToc\(\); \}\}/);
  assert.match(css, /\n\.reader-shell \.bp-items \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
});

test('问答:入口在导轨,右下浮钮退役;面板在整幅页面上也能开', () => {
  assert.ok(!panel.includes('reader-ai-fab'), '浮钮标记已删');
  assert.match(desktop, /className=\{`reader-vrail-btn reader-vrail-qa \$\{qaOpen \? 'is-on' : ''\}`\}/);
  assert.match(desktop, /activeArticle=\{widePage \? null : activeArticle\}/);
  assert.ok(!/\{!pageOpen && !discover && \(\s*<ReaderAiPanel/.test(desktop), '面板不再只挂在文章 / 播客 / 社交');
  // 点导轨钮收起时,面板的「点外面收起」不能抢先把它关掉再被 click 打开
  assert.match(panel, /\.reader-vrail-qa'\)\) return;/);
  // 空态不复述面板标题,也不写「在…里检索相关资料后作答」
  assert.ok(!panel.includes('reader-ai-empty-title') && !panel.includes('reader-ai-empty-sub'));
});

test('导轨标签常驻:只作用读者面的视图轨,管理面的应用导轨仍是悬停揭示', () => {
  assert.match(css, /\n\.reader-vrail \.reader-vrail-label \{ opacity: 1; transform: none; transition: none;/);
  assert.ok(!/\n\.app-rail \.reader-vrail-label \{ opacity: 1/.test(css));
});

test('检视返修:月历开着时海报抬到目录之上;问答收起动画中再点是重开;目录点了就亮、到底亮最后一个、就绪时先同步', () => {
  assert.match(css, /\n\.reader-shell \.bp-poster:has\(\.bp-pop\) \{ z-index: 5; \}/);
  assert.match(panel, /if \(panelShownRef\.current\) closeAiPanel\(\); else openAiPanel\(\);/);
  assert.match(panel, /window\.clearTimeout\(closeTimerRef\.current\); closeTimerRef\.current = 0; \}\s*setAiPanelClosing\(false\);/);
  assert.match(brief, /sheet\.scrollTop \+ sheet\.clientHeight >= sheet\.scrollHeight - 2\) current = acts\[acts\.length - 1\]/);
  assert.match(brief, /setActiveAct\(key\);\s*window\.clearTimeout\(tocPinRef\.current\);\s*tocPinRef\.current = window\.setTimeout\(/);
  assert.match(brief, /const id = requestAnimationFrame\(syncToc\);/);
  // 首次同步要等板块真的画出来,换期也要重来
  assert.match(brief, /if \(mobile \|\| !showEdition \|\| !actKeys\) return undefined;/);
  assert.match(brief, /\}, \[mobile, showEdition, viewKey, actKeys, syncToc\]\);/);
});

