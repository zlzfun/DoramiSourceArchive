import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildListFilters, LIST_PAGE_SIZE, pushListQuery } from '../src/utils/articleList.js';
import { blockedInterestQuery, invalidInterestWindow, loadInterestPage, strictInterestQuery } from '../src/utils/readerInterestQuery.js';
const source = readFileSync(new URL('../src/hooks/useArticleList.js', import.meta.url), 'utf8').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const caps = { interest_match_projection: 1, custom_interest_filter: true, match_contract: 1 };
const query = (id, searchQuery = '') => pushListQuery({ kind: 'custom', id }, { mode: 'article', searchQuery, interestCapabilities: caps, sessionKey: 'alice:1' });
const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
const tick = () => new Promise((resolve) => setImmediate(resolve));

// Execute the real loader with a minimal deterministic hook scheduler. Transport
// deliberately ignores abort to prove the additional sequence guard. No DOM/browser.
function harness(fetchArticles) {
  const slots = []; let cursor = 0; let effects = []; let polling;
  const useRef = (initial) => { const i = cursor++; return slots[i] ??= { current: initial }; };
  const useState = (initial) => { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], (v) => { slots[i] = typeof v === 'function' ? v(slots[i]) : v; }]; };
  const useCallback = (fn, deps) => { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = { fn, deps }; return slots[i].fn; };
  const effect = (fn, deps) => { const i = cursor++; if (!same(slots[i]?.deps, deps)) { const prior = slots[i]; slots[i] = { deps }; effects.push(() => { prior?.cleanup?.(); slots[i].cleanup = fn(); }); } };
  const run = async (fn) => fn(new AbortController().signal);
  const bindings = { useRef, useState, useCallback, useEffect: effect, useLayoutEffect: effect,
    useAbortableLoad: () => run, usePolling: (_fn, _ms, options) => { polling = options; }, fetchArticles,
    fetchArticle: async () => null, blockedInterestQuery, invalidInterestWindow, loadInterestPage, strictInterestQuery, buildListFilters, LIST_PAGE_SIZE,
    analysisNeedsPolling: () => true };
  const loader = new Function(...Object.keys(bindings), `${source}\nreturn useListLoader;`)(...Object.values(bindings));
  const win = { articles: [], articlesTotal: 0, articlesLoading: false, loadingMore: false,
    listRef: { current: { scrollTop: 120 } }, sentinel: null, listVersionRef: { current: 0 }, windowRevision: 0 };
  for (const key of ['articles', 'articlesTotal', 'articlesLoading', 'loadingMore', 'windowRevision']) {
    win[`set${key[0].toUpperCase()}${key.slice(1)}`] = (value) => { win[key] = typeof value === 'function' ? value(win[key]) : value; };
  }
  const errors = []; let resets = 0; let replacements = 0; let facets = { gen: 0 };
  const showToast = (message) => errors.push(message);
  const setFacets = (update) => { facets = update(facets); };
  return { win, errors, get facets() { return facets; }, get resets() { return resets; }, get replacements() { return replacements; }, get polling() { return polling; },
    render(q) {
      cursor = 0; effects = [];
      const result = loader(win, { query: q, showToast, kindFacets: { enabled: true, key: q.activeCustomInterestId, state: facets, setState: setFacets }, onScopeReset: () => { resets++; }, onWindowReplaced: () => { replacements++; } });
      effects.forEach((fn) => fn()); return result;
    },
  };
}

test('query change discards a late prior response even if transport ignores abort', async () => {
  const pending = [];
  const h = harness((filters) => new Promise((resolve) => pending.push({ filters, resolve })));
  h.render(query('7')); h.render(query('8', '权限'));
  assert.equal(pending.length, 2);
  pending[1].resolve({ items: [{ id: 'new' }], total: 1, pagination_token: 'new-token' }); await tick();
  pending[0].resolve({ items: [{ id: 'old-owner-query' }], total: 50, pagination_token: 'old-token' }); await tick();
  assert.deepEqual(h.win.articles, [{ id: 'new' }]); assert.equal(h.win.articlesTotal, 1);
  assert.equal(h.polling.enabled, false);
});

test('strict stale append reuses exact filters, replaces all rows/facets and resets scroll/restore epoch', async () => {
  const calls = []; let count = 0;
  const h = harness(async (filters, limit, skip, includeTotal, options) => {
    calls.push({ filters, limit, skip, includeTotal, signal: options.signal }); count++;
    if (count === 2) throw Object.assign(new Error('stale'), { status: 409, code: 'interest_pagination_stale' });
    return { items: [{ id: count === 1 ? 'old' : count === 3 ? 'fresh' : 'next' }], total: 40, pagination_token: count === 1 ? 't1' : 't2', kind_facets: [{ key: count }] };
  });
  const q = query('7', '权限'); h.render(q); await tick();
  const loader = h.render(q); h.win.listRef.current.scrollTop = 120;
  await loader.loadArticles(1, true);
  assert.deepEqual(calls.map((call) => [call.skip, call.filters.pagination_token]), [[0, undefined], [1, 't1'], [0, undefined]]);
  const { pagination_token: ignored, ...appendFilters } = calls[1].filters;
  assert.equal(ignored, 't1'); assert.deepEqual(appendFilters, calls[0].filters); assert.deepEqual(calls[2].filters, calls[0].filters);
  assert.equal(calls[1].signal, calls[2].signal); assert.ok(calls.every((call) => call.includeTotal));
  assert.deepEqual(h.win.articles, [{ id: 'fresh' }]); assert.deepEqual(h.facets.items, [{ key: 3 }]);
  assert.equal(h.win.listRef.current.scrollTop, 0); assert.equal(h.win.windowRevision, 1); assert.equal(h.resets, 1); assert.equal(h.replacements, 1);
  assert.equal(h.win.loadingMore, false);
  assert.equal(h.win.articlesLoading, false);
  assert.equal(h.render(q).hasMore, true);
  await h.render(q).loadArticles(1, true);
  assert.equal(calls[3].filters.pagination_token, 't2');
  assert.deepEqual(h.win.articles, [{ id: 'fresh' }, { id: 'next' }]);
  assert.equal(h.win.loadingMore, false);
  assert.equal(h.replacements, 1);
});

test('missing strict token clears positive window and stops automatic pagination until manual reload', async () => {
  let valid = false;
  const h = harness(async () => ({ items: [{ id: 'positive' }], total: 40, ...(valid ? { pagination_token: 'ok' } : {}) }));
  const q = query('7'); h.render(q); await tick();
  assert.deepEqual(h.win.articles, []); assert.equal(h.render(q).hasMore, false); assert.equal(h.errors.length, 1);
  valid = true; await h.render(q).loadArticles(0, false);
  assert.deepEqual(h.win.articles, [{ id: 'positive' }]); assert.equal(h.render(q).hasMore, true);
});

test('transient append failure keeps prior window but halts paging; a failed stale retry clears known-invalid data', async () => {
  for (const staleFirst of [false, true]) {
    let count = 0;
    const h = harness(async () => {
      count++;
      if (count === 1) return { items: [{ id: 'prior' }], total: 90, pagination_token: 'old' };
      if (staleFirst && count === 2) throw Object.assign(new Error('stale'), { status: 409, code: 'interest_pagination_stale' });
      throw Object.assign(new Error('temporary network failure'), { status: 503 });
    });
    const q = query('7'); h.render(q); await tick();
    await h.render(q).loadArticles(1, true);
    assert.deepEqual(h.win.articles, staleFirst ? [] : [{ id: 'prior' }]);
    assert.equal(h.render(q).hasMore, false); assert.equal(h.render(q).pageFailed, true);
    assert.equal(count, staleFirst ? 3 : 2);
  }
});

test('explicit denial on append clears positive rows even without stale token response', async () => {
  let count = 0;
  const h = harness(async () => {
    if (++count === 1) return { items: [{ id: 'prior' }], total: 90, pagination_token: 'old' };
    throw Object.assign(new Error('denied'), { status: 403 });
  });
  const q = query('7'); h.render(q); await tick(); await h.render(q).loadArticles(1, true);
  assert.deepEqual(h.win.articles, []); assert.equal(h.render(q).hasMore, false);
});
