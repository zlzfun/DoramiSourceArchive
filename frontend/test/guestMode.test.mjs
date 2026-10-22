// 游客模式(issue #213):登录提示文案查表、后端错误码映射、事件派发,以及游客早报措辞改写
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  LOGIN_PROMPTS,
  LOGIN_REQUIRED_EVENT,
  actionForErrorCode,
  loginPromptCopy,
  requestLogin,
} from '../src/utils/loginPrompt.js';
import { GUEST_LOCKED_SECTIONS, guestEdition, guestSelectionReason } from '../src/utils/guestCopy.js';

const INTERNAL_WORDS = /归档|采集|分发|节点|层/;

test('登录提示:已知动作取对应文案,来源名填进标题,缺名回落「这个来源」', () => {
  assert.equal(loginPromptCopy('favorite').title, '登录后即可收藏');
  assert.equal(loginPromptCopy('subscribe', { name: '量子位' }).title, '登录后即可订阅「量子位」');
  assert.equal(loginPromptCopy('subscribe').title, '登录后即可订阅「这个来源」');
  assert.equal(loginPromptCopy('ai').icon, 'sparkles');
});

test('登录提示:未知动作回落通用文案', () => {
  assert.deepEqual(loginPromptCopy('nope'), loginPromptCopy('generic'));
  assert.equal(loginPromptCopy(undefined).title, '登录后可用');
});

test('登录提示文案:读者面不漏内部词,说明句以全角句号收尾', () => {
  for (const [action, copy] of Object.entries(LOGIN_PROMPTS)) {
    assert.doesNotMatch(`${copy.title}${copy.message}`, INTERNAL_WORDS, action);
    assert.ok(copy.message.endsWith('。'), `${action} 的说明句应以「。」收尾`);
  }
  for (const [section, sentence] of Object.entries(GUEST_LOCKED_SECTIONS)) {
    assert.doesNotMatch(sentence, INTERNAL_WORDS, section);
    assert.ok(sentence.endsWith('。'), `${section} 的说明句应以「。」收尾`);
  }
});

test('后端错误码 → 提示动作:只认两个游客码,其余不弹', () => {
  assert.equal(actionForErrorCode('login_required'), 'generic');
  assert.equal(actionForErrorCode('guest_quota_exhausted'), 'ai');
  assert.equal(actionForErrorCode('guest_disabled'), null);
  assert.equal(actionForErrorCode(''), null);
  assert.equal(actionForErrorCode(undefined), null);
});

test('requestLogin 派发 dorami-login-required,detail 带动作与来源名', () => {
  const previous = globalThis.window;
  const target = new EventTarget();
  globalThis.window = target;
  try {
    let detail = null;
    target.addEventListener(LOGIN_REQUIRED_EVENT, (event) => { detail = event.detail; });
    requestLogin('subscribe', { name: 'IT之家 AI' });
    assert.deepEqual(detail, { action: 'subscribe', name: 'IT之家 AI' });
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
});

test('游客早报措辞:「你订阅的」类说法换成「默认订阅」,其余原样', () => {
  assert.equal(
    guestSelectionReason('来自你订阅的「量子位」，是今日订阅中的高质量内容。'),
    '来自默认订阅的「量子位」，是今日默认订阅中的高质量内容。',
  );
  assert.equal(guestSelectionReason('来自你订阅的「量子位」，按内容价值入选。'), '来自默认订阅的「量子位」，按内容价值入选。');
  assert.equal(
    guestSelectionReason('今日重大事件 · 3 家来源同时报道，代表来源「X」，不在你的订阅内也为你保留。'),
    '今日重大事件 · 3 家来源同时报道，代表来源「X」，不在默认订阅内也为你保留。',
  );
  assert.equal(guestSelectionReason('订阅源最新更新，不计入正式精选。'), '订阅源最新更新，不计入正式精选。');
  assert.equal(guestSelectionReason(''), '');
  assert.equal(guestSelectionReason(null), null);
});

test('guestEdition 返回新对象:条目与快照里的理由都改写,原 edition 不动', () => {
  const edition = {
    revision: 2,
    items: [
      { id: 1, selection_reason: '来自你订阅的「A」，按内容价值入选。', snapshot: { selection_reason: '来自你订阅的「A」，按内容价值入选。', title: 't' } },
      { id: 2, selection_reason: '订阅源最新更新，不计入正式精选。', snapshot: null },
    ],
  };
  const shown = guestEdition(edition);
  assert.notEqual(shown, edition);
  assert.equal(shown.revision, 2);
  assert.equal(shown.items[0].selection_reason, '来自默认订阅的「A」，按内容价值入选。');
  assert.equal(shown.items[0].snapshot.selection_reason, '来自默认订阅的「A」，按内容价值入选。');
  assert.equal(shown.items[0].snapshot.title, 't');
  assert.equal(shown.items[1].snapshot, null);
  assert.equal(edition.items[0].selection_reason, '来自你订阅的「A」，按内容价值入选。');
  assert.equal(guestEdition(null), null);
  assert.deepEqual(guestEdition({ status: 'pending' }), { status: 'pending' });
});
