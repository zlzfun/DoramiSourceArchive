import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { withFreshAnalysis } from '../src/utils/analysisProjection.js';
const reader = readFileSync(new URL('../src/hooks/useReaderState.js', import.meta.url), 'utf8');
const mobile = readFileSync(new URL('../src/components/mobile/MobileSubsFlow.jsx', import.meta.url), 'utf8');

test('old-server missing typed removes stale private projection without fabricating typed presence', () => {
  const incoming = { interest_hits: ['公共兴趣'] };
  const next = withFreshAnalysis({ interest_matches: [{ name: '旧私有命中' }] }, incoming);
  assert.equal(Object.hasOwn(next, 'interest_matches'), false);
  assert.deepEqual(next.interest_hits, ['公共兴趣']);
  for (const interest_matches of [[], null]) {
    const authoritative = withFreshAnalysis(next, { interest_matches });
    assert.equal(Object.hasOwn(authoritative, 'interest_matches'), true);
    assert.equal(authoritative.interest_matches, interest_matches);
  }
});

test('late old-id denial clears its cached body and all instance evidence without closing new pane', () => {
  const block = reader.slice(reader.indexOf('  const revokeArticleAccess ='), reader.indexOf('  // ── 源目录'));
  for (const activeId of ['old', 'new']) {
    const caches = { bodyCacheRef: { current: new Map([['old', 'secret body']]) }, summaryCacheRef: { current: new Map([['old', 'secret summary']]) } };
    const activeIdRef = { current: activeId }; const panes = [];
    let rows = [{ id: 'old' }, { id: 'new' }]; let projection = { id: 'old', content: 'secret', interest_matches: ['positive'] };
    const bindings = { ...caches, activeIdRef, useCallback: (fn) => fn,
      setArticles: (fn) => { rows = fn(rows); }, listRegistry: { patch: (id, fn) => { assert.equal(id, 'old'); projection = fn(projection); } },
      setActiveArticle: (value) => panes.push(value), setActiveBody: () => {}, setActiveSummary: () => {}, setActiveBodyLoading: () => {} };
    const revoke = new Function(...Object.keys(bindings), block + '\nreturn revokeArticleAccess;')(...Object.values(bindings));
    revoke('old');
    assert.equal(caches.bodyCacheRef.current.has('old'), false); assert.equal(caches.summaryCacheRef.current.has('old'), false);
    assert.deepEqual(projection.interest_matches, []); assert.equal(projection.content, null);
    assert.deepEqual(rows, [{ id: 'new' }]); assert.deepEqual(panes, activeId === 'old' ? [null] : []);
  }
});

test('phone search uses the shared debounce and stable searchQuery for identity and empty states', () => {
  assert.match(mobile, /const \[searchInput, setSearchInput\] = useState\(''\)/);
  assert.match(mobile, /const searchQuery = useDebouncedValue\(searchInput, 300\)/);
  assert.match(mobile, /value=\{searchInput\}/); assert.match(mobile, /setSearchInput\(event.target.value\)/);
  assert.match(mobile, /pushListQuery\([\s\S]*?searchQuery, interestCapabilities/);
  const source = readFileSync(new URL('../src/hooks/useDebouncedValue.js', import.meta.url), 'utf8').replace(/^import .*;\n/gm, '').replace('export ', '');
  let stable = ''; let cleanup; let timer; let scheduled = 0;
  const useDebouncedValue = new Function('useState', 'useEffect', 'setTimeout', 'clearTimeout', source + '\nreturn useDebouncedValue;')(
    () => [stable, (value) => { stable = value; }], (fn) => { cleanup?.(); cleanup = fn(); },
    (fn, ms) => { assert.equal(ms, 300); timer = fn; return ++scheduled; }, () => { timer = null; });
  for (const value of ['智', '智能', '智能体']) assert.equal(useDebouncedValue(value, 300), '');
  timer(); assert.equal(stable, '智能体');
});

test('blocked windows expose catalog retry while stale replacement uses narrow detail refresh', () => {
  assert.match(reader, /retryInterestList = \(\) => interestListUnavailable \? refreshInterests\(\) : loadArticles\(0, false\)/);
  assert.match(mobile, /blockedInterestQuery\(query\) \? rs.refreshInterests\(\) : loadArticles\(0, false\)/);
  const replacement = reader.slice(reader.indexOf('  const onListReplaced ='), reader.indexOf('  const interestListUnavailable'));
  assert.match(replacement, /refreshActiveArticle\(activeIdRef.current\)/);
  assert.doesNotMatch(replacement, /setActiveArticle|setActiveBody/);
});
