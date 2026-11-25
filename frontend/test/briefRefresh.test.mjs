// issue #343:今天的早报由系统自动换版,读者面没有重编入口。页面只按 refresh_pending 决定多久再取一次、
// 取到新的一版时是不是「换了一版」;这里再守住「重编入口不回来」。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  CONTENT_POLL_MS, SOON_POLL_MS, briefPollInterval, isBriefPreparing, isBriefSwap,
} from '../src/utils/briefRefresh.js';

const today = (status, edition = {}) => ({ status, edition: { id: 1, refresh_pending: null, ...edition } });

test('多久再取一次:在途或很快有新版 8 秒,等新内容 60 秒,不会再动就不取', () => {
  assert.equal(briefPollInterval(today('pending')), SOON_POLL_MS);
  assert.equal(briefPollInterval(today('generating')), SOON_POLL_MS);
  assert.equal(briefPollInterval(today('ready', { refresh_pending: 'soon' })), SOON_POLL_MS);
  assert.equal(briefPollInterval(today('failed', { refresh_pending: 'soon' })), SOON_POLL_MS);
  assert.equal(briefPollInterval(today('degraded', { refresh_pending: 'content' })), CONTENT_POLL_MS);
  assert.equal(briefPollInterval(today('ready')), 0);
  assert.equal(briefPollInterval(today('failed')), 0);
  assert.equal(briefPollInterval({ status: 'not_started', edition: null }), 0);
  assert.equal(briefPollInterval({ status: 'empty_subscriptions', edition: null }), 0);
  // 空订阅的引导页没有 edition:重新订阅后后端在顶层告知稍后有新版
  assert.equal(briefPollInterval({ status: 'empty_subscriptions', edition: null, refresh_pending: 'soon' }), SOON_POLL_MS);
  assert.equal(briefPollInterval({ status: 'empty_subscriptions', edition: null, refresh_pending: null }), 0);
  assert.equal(briefPollInterval(null), 0);
  assert.ok(SOON_POLL_MS < CONTENT_POLL_MS);
});

test('失败了但系统还会重试:照「编排中」画;重试用尽才是失败', () => {
  assert.equal(isBriefPreparing(today('pending')), true);
  assert.equal(isBriefPreparing(today('generating')), true);
  assert.equal(isBriefPreparing(today('failed', { refresh_pending: 'soon' })), true);
  assert.equal(isBriefPreparing(today('failed')), false);
  assert.equal(isBriefPreparing(today('ready', { refresh_pending: 'soon' })), false);
  assert.equal(isBriefPreparing(null), false);
});

test('「换了一版」只指两份都是编好的版面且不是同一版', () => {
  const shown = today('ready', { id: 7 });
  assert.equal(isBriefSwap(shown, today('degraded', { id: 8 })), true);
  // 同一版的状态变化(refresh_pending 变了)不是换版:直接更新
  assert.equal(isBriefSwap(shown, today('ready', { id: 7, refresh_pending: 'soon' })), false);
  // 眼前还没有编好的版面(编排中 / 失败 / 还没取到):没有可打断的阅读,直接放上去
  assert.equal(isBriefSwap(today('generating', { id: 7 }), today('ready', { id: 7 })), false);
  assert.equal(isBriefSwap(today('failed', { id: 7 }), today('ready', { id: 8 })), false);
  assert.equal(isBriefSwap(null, today('ready', { id: 8 })), false);
  // 取到的不是编好的版面(清成空订阅、又在编):如实显示
  assert.equal(isBriefSwap(shown, { status: 'empty_subscriptions', edition: null }), false);
  assert.equal(isBriefSwap(shown, today('generating', { id: 8 })), false);
});

test('读者面没有重编入口:早报页不再调重编接口,也不再有那几句文案', () => {
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
  const page = read('../src/components/PersonalBriefPage.jsx');
  const code = page.split('\n').filter((line) => !line.trim().startsWith('//')).join('\n');
  for (const gone of ['重新编排', '立即重编', '重试生成', '尚未完成，按当时已有内容编排', '下次编排生效', '新的编排请求已记录']) {
    assert.equal(code.includes(gone), false, `早报页还留着「${gone}」`);
  }
  assert.equal(/rebuildPersonalBrief|today\/rebuild/.test(code), false);
  assert.equal(/today\/rebuild/.test(read('../src/api.js')), false);
});
