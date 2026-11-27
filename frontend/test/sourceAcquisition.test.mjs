// 源的获取方式(issue #344):抓取与推送并列,源管理的行与检视器按这里的口径写。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ACQUISITION_LABELS, acquisitionOf, bindableSources, bindingPillOf, hasFetcher, lastPushedAt, pushBindingsOf,
  registerPayload, registerQuotaOf,
} from '../src/utils/sourceAcquisition.js';

const push = (actor, at = null) => ({ actor, token_preview: '...abc', is_active: true, last_pushed_at: at });

test('抓取 / 推送 / 两样都有 / 都没有', () => {
  assert.equal(acquisitionOf({ id: 'rss_a', bound: true }), 'fetch');
  assert.equal(acquisitionOf({ id: 'rss_a' }), 'fetch'); // 首屏兜底目录没有 bound 字段:按有抓取器
  assert.equal(acquisitionOf({ id: 'x_openai', bound: true, push_bindings: [push('grok-bot')] }), 'both');
  assert.equal(acquisitionOf({ id: 'push_grok', bound: false, push_bindings: [push('grok-bot')] }), 'push');
  assert.equal(acquisitionOf({ id: 'x_temp', bound: false, push_bindings: [] }), 'none');
  assert.equal(acquisitionOf({ id: 'x_temp', bound: false }), 'none');
  assert.deepEqual(Object.keys(ACQUISITION_LABELS).sort(), ['both', 'fetch', 'none', 'push']);
});

test('读者自定源走专用抓取通道,算有抓取器', () => {
  assert.equal(hasFetcher({ id: 'user_rss_1', user_source: true, bound: false }), true);
  assert.equal(acquisitionOf({ id: 'user_rss_1', user_source: true }), 'fetch');
});

test('最近一批取各令牌里最晚的', () => {
  const source = { push_bindings: [push('a', '2026-11-25T08:00:00+00:00'), push('b'), push('c', '2026-11-25T09:30:00+00:00')] };
  assert.equal(lastPushedAt(source), '2026-11-25T09:30:00+00:00');
  assert.equal(lastPushedAt({ push_bindings: [push('a')] }), '');
  assert.equal(lastPushedAt({}), '');
  assert.deepEqual(pushBindingsOf({ push_bindings: null }), []);
});

test('下拉里只列绑得了的源', () => {
  const catalog = [
    { id: 'x_openai' }, { id: 'generic_rss', is_template: true }, { id: 'gone', retired: true },
    { id: 'user_rss_1', user_source: true }, { id: 'push_grok' }, null,
  ];
  assert.deepEqual(bindableSources(catalog, ['push_grok']).map((s) => s.id), ['x_openai']);
  assert.deepEqual(bindableSources(null), []);
});

test('登记表单:定向源给出处,合集给 push_ 后缀,填得不够不出载荷', () => {
  const base = { kind: 'social_post', scope: 'open', name: ' Grok 推荐 ', identity: '', suffix: 'grok_picks', avatar: '', tokenId: '3', listed: true };
  assert.deepEqual(registerPayload(base), {
    kind: 'social_post', name: 'Grok 推荐', listed: true, token_id: 3, source_id_suffix: 'grok_picks',
  });
  assert.equal(registerPayload({ ...base, suffix: 'No' }), null);
  assert.equal(registerPayload({ ...base, name: '  ' }), null);
  assert.deepEqual(
    registerPayload({ ...base, scope: 'one', identity: '@New_Lab', tokenId: '', avatar: ' https://img.test/a.jpg ', listed: false }),
    { kind: 'social_post', name: 'Grok 推荐', listed: false, avatar_url: 'https://img.test/a.jpg', handle: 'New_Lab' },
  );
  assert.equal(registerPayload({ ...base, scope: 'one', identity: '' }), null);
  // 文章:看得出是 feed 的按 feed 给,其余按站点
  const article = { ...base, kind: 'article', scope: 'one', tokenId: '' };
  assert.equal(registerPayload({ ...article, identity: 'https://blog.example.com/' }).url, 'https://blog.example.com/');
  assert.equal(registerPayload({ ...article, identity: 'https://blog.example.com/feed.xml' }).feed_url, 'https://blog.example.com/feed.xml');
  assert.equal(registerPayload({ ...article, identity: 'https://blog.example.com/rss' }).feed_url, 'https://blog.example.com/rss');
  // 播客没有「不限」:scope 留着 open 也按定向,必须给 feed
  assert.equal(registerPayload({ ...base, kind: 'podcast_episode', identity: '' }), null);
  assert.deepEqual(
    registerPayload({ ...base, kind: 'podcast_episode', identity: 'https://pod.test/feed', tokenId: '' }),
    { kind: 'podcast_episode', name: 'Grok 推荐', listed: true, feed_url: 'https://pod.test/feed' },
  );
});

test('今日登记:有权限的令牌写「已登记 / 上限」,没有权限的不写', () => {
  assert.equal(registerQuotaOf({ can_register_sources: true, registered_today: 3, register_daily_limit: 20 }), '3 / 20');
  assert.equal(registerQuotaOf({ can_register_sources: true, register_daily_limit: 20 }), '0 / 20');
  // 没有权限:即使今天早些时候登记过(之后被关掉),也不写数字
  assert.equal(registerQuotaOf({ can_register_sources: false, registered_today: 3, register_daily_limit: 20 }), '');
  assert.equal(registerQuotaOf(null), '');
});

test('绑定行的徽标:只有待列入的来源标「临时」', () => {
  assert.equal(bindingPillOf({ source_id: 'x_new_lab', admission_status: 'provisional' }), '临时');
  assert.equal(bindingPillOf({ source_id: 'x_openai', admission_status: 'incubating' }), '');
  assert.equal(bindingPillOf({ source_id: 'x_openai' }), '');
  assert.equal(bindingPillOf(null), '');
});
