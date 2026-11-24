import test from 'node:test';
import assert from 'node:assert/strict';
import { canFilterCustomInterests, resolveInterestNavigation, strictInterestQuery, blockedInterestQuery, loadInterestPage } from '../src/utils/readerInterestQuery.js';
import { buildListFilters, pushListQuery, pushMarkReadPlan, pushViewItems } from '../src/utils/articleList.js';
const caps = { interest_match_projection: 1, custom_interest_filter: true, match_contract: 1 };
const spec = { kind: 'custom', id: '7', name: '内网智能体' };
const query = (opts = {}) => pushListQuery(spec, { mode: 'article', interestCapabilities: caps, interestRevision: 3, sessionKey: 'alice:4', ...opts });

test('private navigation requires all advertised capabilities and resolves only owner catalog identities', () => {
  const catalog = { capabilities: caps, custom: [{ id: 7, name: '内网智能体', status: 'active' }], tags: [{ id: 29, code: 'topic:agent', name: '智能体', kind: 'topic' }] };
  assert.deepEqual(resolveInterestNavigation({ kind: 'custom', id: '7' }, catalog), spec);
  assert.equal(resolveInterestNavigation({ kind: 'custom', id: '29' }, catalog), null);
  assert.equal(resolveInterestNavigation({ kind: 'tag', id: '29' }, catalog), null);
  assert.equal(resolveInterestNavigation({ kind: 'tag', id: 'topic:agent' }, catalog).id, 29);
  for (const key of Object.keys(caps)) {
    const partial = { ...caps }; delete partial[key];
    assert.equal(canFilterCustomInterests(partial), false);
    assert.equal(resolveInterestNavigation({ kind: 'custom', id: '7' }, { ...catalog, capabilities: partial }), null);
  }
  assert.equal(resolveInterestNavigation({ kind: 'custom', id: '7' }, { ...catalog, custom: [{ id: 7, status: 'paused' }] }), null);
});

test('custom search is ANDed with explicit owner filter; unrelated navigation/query identity never leaks to API', () => {
  assert.deepEqual(buildListFilters(query({ searchQuery: '权限', unreadOnly: true })), {
    shape: 'article', subscribed_scope: 'off', interest_scope: 'only', interest_kinds: 'custom', custom_interest_id: '7',
    search: '权限', with_unread: 'true', with_interest: 'true', unread_only: 'true',
  });
  for (const opts of [{ mode: 'podcast' }, { searchQuery: '新词' }, { interestRevision: 4 }, { sessionKey: 'bob:5' }]) {
    assert.notEqual(JSON.stringify(query(opts)), JSON.stringify(query()));
  }
  assert.equal(strictInterestQuery(query()), true);
  assert.deepEqual(pushViewItems('custom'), [['article', '文章'], ['podcast', '播客']]);
});

test('custom and previously opted union fail closed after capability loss; legacy public path remains compatible', () => {
  const unavailable = query({ interestCapabilities: null });
  assert.equal(blockedInterestQuery(unavailable), true);
  assert.throws(() => buildListFilters(unavailable));
  assert.throws(() => buildListFilters(query({ customInterestUnavailable: true })));
  const union = pushListQuery({ kind: 'interest', union: true }, { mode: 'article', interestCapabilities: caps });
  assert.equal(buildListFilters(union).interest_kinds, 'tag,custom');
  assert.throws(() => buildListFilters({ ...union, interestCapabilities: null }));
  assert.equal(buildListFilters(pushListQuery({ kind: 'interest' }, { mode: 'article' })).interest_kinds, undefined);
  const publicQuery = pushListQuery({ kind: 'tag', id: 29 }, { mode: 'article', interestCapabilities: caps });
  assert.equal(strictInterestQuery(publicQuery), false);
  assert.equal(buildListFilters(publicQuery).interest_tag_id, 29);
  assert.equal(buildListFilters(publicQuery).interest_kinds, undefined);
  assert.equal(pushMarkReadPlan(spec, 'article', caps), null);
  assert.equal(pushMarkReadPlan({ kind: 'interest', union: true }, 'article', null), null);
  assert.equal(pushMarkReadPlan({ kind: 'tag', id: 29 }, 'article', caps).tagId, 29);
});

test('append binds prior token; exact stale code retries once at zero and replaces window', async () => {
  const calls = [];
  const result = await loadInterestPage(async (skip, token) => {
    calls.push([skip, token]);
    if (skip) throw Object.assign(new Error('stale'), { status: 409, code: 'interest_pagination_stale' });
    return { items: [{ id: 'fresh' }], total: 1, pagination_token: 'new' };
  }, { skip: 30, append: true, strict: true, token: 'old' });
  assert.deepEqual(calls, [[30, 'old'], [0, '']]);
  assert.equal(result.replace, true);
  assert.equal(result.token, 'new');
  const normal = await loadInterestPage(async (skip, token) => {
    assert.equal(skip, 30); assert.equal(token, 'old');
    return { items: [{ id: 'second' }], pagination_token: 'old' };
  }, { skip: 30, append: true, strict: true, token: 'old' });
  assert.equal(normal.replace, false);
});

test('missing token, repeated stale, unrelated conflicts and abort never recurse or fall back', async () => {
  let calls = 0;
  await assert.rejects(loadInterestPage(async () => { calls++; }, { append: true, strict: true }), /重新加载/);
  assert.equal(calls, 0);
  await assert.rejects(loadInterestPage(async () => ({ items: [], total: 0 }), { strict: true }), /暂不可用/);
  await assert.rejects(loadInterestPage(async () => { calls++; throw Object.assign(new Error('stale'), { status: 409, code: 'interest_pagination_stale' }); }, { strict: true }), /stale/);
  assert.equal(calls, 2);
  for (const error of [Object.assign(new Error('other'), { status: 409, code: 'other' }), new DOMException('cancelled', 'AbortError')]) {
    calls = 0;
    await assert.rejects(loadInterestPage(async () => { calls++; throw error; }, { strict: true }), (actual) => actual === error);
    assert.equal(calls, 1);
  }
});
