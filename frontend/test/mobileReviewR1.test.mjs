// issue #230 交叉检视第 1 轮(Codex)修复的接线护栏。行为由沙箱 Playwright 走查(推入页标读回内容 Tab、
// 推入页正文跨到桌面 / 发起标签检索、桌面单源进手机、早报跨版式、播放卡收起、分享页 chunk 失败)与 e2e guards(动作单 Tab 回绕);
// 纯逻辑(标读匹配器、登记处、inert 计数、两项 CSS 棘轮)各在 articleList / layerPrimitives / hoverGuard / viewportUnits 里测。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.resolve(here, '../src', rel), 'utf8');

test('P1-1:推入页标读不再自带 reload / unreadOnly,由数据层对齐所有实例', () => {
  const flow = read('components/mobile/MobileSubsFlow.jsx');
  assert.match(flow, /markListAllRead\(plan, \{ listArticles: articles \}\)/);
  const state = read('hooks/useReaderState.js');
  const body = state.slice(state.indexOf('const markListAllRead'), state.indexOf('const handleMarkAllRead'));
  assert.match(body, /markReadMatcher\(plan/);
  assert.match(body, /listRegistry\.idsWhere\(match\)/);
  assert.match(body, /listRegistry\.reloadUnreadOnly\(\)/);
  assert.match(body, /if \(!match\) calibrateReadState\(\)/);
  assert.match(body, /partial \? \[\] : listArticles/, '兴趣轴没写满时发起窗口不乐观清点');
  // 复检 P2:校准在途期间的手动标读 / 标未读比响应新,那一轮不撤覆盖
  const calib = state.slice(state.indexOf('const calibrateReadState'), state.indexOf('const handleMarkAllRead'));
  assert.match(calib, /const seq = readToggleSeqRef\.current;/);
  assert.match(calib, /if \(readToggleSeqRef\.current !== seq\) return;/);
  assert.match(state, /readToggleSeqRef\.current \+= 1;\s*readOverridesRef\.current\.set\(id, !toUnread\);/);
});

test('P1-2 / P2-3:正文那篇的容器在标签检索与换版式时对齐;进手机撤掉没开正文时的下钻', () => {
  const reader = read('components/mobile/MobileReader.jsx');
  const leave = reader.slice(reader.indexOf('const leaveArticleForSearch'), reader.indexOf('const onboardingRequired'));
  assert.match(leave, /alignModeToArticle\(activeArticle\)/);
  const ws = read('components/ReaderWorkspace.jsx');
  assert.match(ws, /useLayoutEffect\(\(\) => \{\s*if \(handoffLayoutRef\.current === mobile\) return;/);
  assert.match(ws, /rs\.alignModeToArticle\(rs\.activeArticle, \{ keepArticle: true \}\)/);
  assert.match(ws, /!rs\.activeArticle && \(rs\.activeSourceId \|\| rs\.activeTagId\)\) \{\s*rs\.dropNarrowing\(\)/);
});

test('P2-4:焦点陷阱只认 Tab 真能走到的元素', () => {
  const a11y = read('hooks/useModalA11y.js');
  assert.match(a11y, /el\.tabIndex >= 0 && !el\.closest\('\[inert\]'\)/);
  assert.match(a11y, /document\.activeElement === first \|\| document\.activeElement === panel/);
});

test('P2-6 / P2-7 / P2-8:分享页入口有错误边界;早报跨版式快照优先;播放卡收起时 inert', () => {
  const share = read('components/SharedArticleEntry.jsx');
  assert.match(share, /<TabErrorBoundary>\s*<Suspense/);
  const brief = read('components/PersonalBriefPage.jsx');
  assert.match(brief, /const place = placeKeeper\?\.take\?\.\(\);\s*if \(place\) restoreRef\.current = place;/);
  const dock = read('components/player/PlayerDock.jsx');
  assert.match(dock, /inert=\{cardState === 'closing' \|\| undefined\}/);
});
