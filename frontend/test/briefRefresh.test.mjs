// issue #343:今天的早报由系统自动换版,读者面没有重编入口。页面只按 refresh_pending 决定多久再取一次、
// 取到新的一版时是不是「换了一版」;这里再守住「重编入口不回来」。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  CONTENT_POLL_MS, SOON_POLL_MS, briefFace, briefPollInterval, isBriefPreparing, isBriefRefreshing, isBriefSwap,
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

test('页头说「正在编排」:眼前是编好的一版、新的一版几十秒内就到;等新内容落地与编排中的状态块都不算', () => {
  assert.equal(isBriefRefreshing(today('ready', { refresh_pending: 'soon' })), true);
  assert.equal(isBriefRefreshing(today('degraded', { refresh_pending: 'soon' })), true);
  // 等新内容落地可能是几个小时:不说「正在编排」
  assert.equal(isBriefRefreshing(today('ready', { refresh_pending: 'content' })), false);
  assert.equal(isBriefRefreshing(today('ready')), false);
  // 眼前没有编好的版面:海报位自己已经写着「早报编排中…」
  assert.equal(isBriefRefreshing(today('generating')), false);
  assert.equal(isBriefRefreshing(today('failed', { refresh_pending: 'soon' })), false);
  assert.equal(isBriefRefreshing({ status: 'empty_subscriptions', edition: null, refresh_pending: 'soon' }), false);
  assert.equal(isBriefRefreshing(null), false);
});

test('版面换没换:换了一版、编排完成、清成空订阅都算;同一版的字段更新与在途的状态流转不算', () => {
  const shown = today('ready', { id: 7 });
  assert.notEqual(briefFace(shown), briefFace(today('degraded', { id: 8 })));
  assert.notEqual(briefFace(today('generating', { id: 8 })), briefFace(today('ready', { id: 8 })));
  assert.notEqual(briefFace(shown), briefFace({ status: 'empty_subscriptions', edition: null }));
  assert.notEqual(briefFace(today('failed', { id: 7 })), briefFace(today('ready', { id: 8 })));
  // 同一版:只是「接下来还会换」变了
  assert.equal(briefFace(shown), briefFace(today('ready', { id: 7, refresh_pending: 'soon' })));
  // 在途的三种画法是同一块「早报编排中…」
  assert.equal(briefFace(today('pending', { id: 8 })), briefFace(today('generating', { id: 8 })));
  assert.equal(briefFace(today('pending', { id: 8 })), briefFace(today('failed', { id: 7, refresh_pending: 'soon' })));
  // 重试用尽的失败与还会重试的失败不是一个画法
  assert.notEqual(briefFace(today('failed', { id: 7 })), briefFace(today('failed', { id: 7, refresh_pending: 'soon' })));
  assert.equal(briefFace(null), '');
});

test('换版不硬切:后台换上来的版面走淡出 → 换上来,页头在新版快到时写「正在编排」', () => {
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
  const page = read('../src/components/PersonalBriefPage.jsx');
  const has = (text, piece) => assert.equal(text.includes(piece), true, `没找到:${piece}`);
  // 后台再取(quiet)与放下留着的那一版都按 soft 走;首次载入不带
  has(page, 'showToday(data, { soft: quiet })');
  has(page, 'showToday(heldTodayRef.current, { soft: true })');
  has(page, 'leaveBrief(sheetRef.current)');
  has(page, 'swapInBrief(sheetRef.current)');
  // 桌面页头的编排时刻位、手机的日期钮,各说一次
  has(page, 'const refreshing = usesToday && showEdition && isBriefRefreshing(today);');
  has(page, '<Loader2 className="animate-spin" aria-hidden="true" />正在编排</> : topNote}');
  has(page, "{refreshing ? '正在编排' : dayText}");
  has(page, "aria-label={`往期早报，正在看 ${dayText}${refreshing ? '，正在编排' : ''}`}");
  const motion = read('../src/utils/spatialMotion.js');
  // 取值只用 token:淡出用 fast 档,换上来用 move 弹簧;减少动效不演淡出、只淡入
  has(motion, "{ duration: MOTION.fast, easing: 'linear', fill: 'forwards' }");
  has(motion, "{ opacity: 1, transform: 'none' }], 'move');");
  has(motion, 'if (!sheet || !canAnimate() || motionReduced()) return still;');
  has(motion, 'if (reduced) fadeIn(el, 160);');
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
