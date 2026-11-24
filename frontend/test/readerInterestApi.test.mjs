import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../src/api.js', import.meta.url), 'utf8').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
function api(fetch) {
  return new Function('fetch', 'API_BASE_URL', 'actionForErrorCode', `${source}\nreturn { fetchArticles, fetchArticle, fetchInterests, bumpAuthGeneration };`)(fetch, '/api', () => null);
}
const response = (data, status = 200) => ({ ok: status < 400, status, headers: { get: () => null }, json: async () => data });

test('detail opts into current projection; strict pagination sends token and nested stale error parses precisely', async () => {
  const calls = [];
  const client = api(async (url, options) => { calls.push([url, options]); return response({}); });
  await client.fetchArticle('a/b', { withInterest: true });
  assert.equal(calls[0][0], '/api/articles/a%2Fb?with_interest=true');
  assert.equal(Object.hasOwn(calls[0][1], 'withInterest'), false);
  await client.fetchArticle('a/b');
  assert.equal(calls[1][0], '/api/articles/a%2Fb');
  await client.fetchArticles({ interest_scope: 'only', interest_kinds: 'custom', custom_interest_id: '7', pagination_token: 't', kind_facets: 'true' }, 30, 30, true);
  const params = new URL(calls[2][0], 'https://test.invalid').searchParams;
  assert.equal(params.get('pagination_token'), 't'); assert.equal(params.get('include_total'), 'true');
  const failing = api(async () => response({ detail: { code: 'interest_pagination_stale', message: '兴趣或可见内容已变化，请重新加载' } }, 409));
  await assert.rejects(failing.fetchArticles(), (error) => error.status === 409 && error.code === 'interest_pagination_stale' && error.message === '兴趣或可见内容已变化，请重新加载');
});

test('list, detail and catalog late successes and errors cannot cross account generations', async () => {
  for (const method of ['fetchArticles', 'fetchArticle', 'fetchInterests']) {
    for (const status of [200, 403]) {
      let finish;
      const client = api(() => new Promise((resolve) => { finish = resolve; }));
      const promise = method === 'fetchArticle' ? client[method]('one', { withInterest: true }) : client[method]();
      client.bumpAuthGeneration(); finish(response({ private: 'old owner' }, status));
      await assert.rejects(promise, { name: 'AbortError' });
    }
  }
});

test('aborted reader response stays cancelled even when transport ignores AbortSignal', async () => {
  let finish;
  const client = api(() => new Promise((resolve) => { finish = resolve; }));
  const controller = new AbortController();
  const promise = client.fetchInterests({ signal: controller.signal });
  controller.abort(); finish(response({ custom_items: [{ name: 'secret' }] }));
  await assert.rejects(promise, { name: 'AbortError' });
});
