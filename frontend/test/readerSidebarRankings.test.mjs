import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const desktop = readFileSync(new URL('../src/components/ReaderTab.jsx', import.meta.url), 'utf8');
const mobile = readFileSync(new URL('../src/components/mobile/MobileReader.jsx', import.meta.url), 'utf8');
const state = readFileSync(new URL('../src/hooks/useReaderState.js', import.meta.url), 'utf8');

function between(source, start, end) {
  const from = source.indexOf(start);
  assert.ok(from >= 0, `missing start: ${start}`);
  const to = source.indexOf(end, from + start.length);
  assert.ok(to > from, `missing end: ${end}`);
  return source.slice(from, to);
}

const sidebar = between(
  desktop,
  '{/* ── 源栏 · 我的订阅 ── */}',
  '{/* ── 我的早报',
);

test('rankings keep the desktop subscription list mounted and the ranking entry on', () => {
  assert.doesNotMatch(sidebar, /!rankingsOpen\s*&&/);
  assert.match(sidebar, /<div className="reader-source-scroll">/);
  assert.match(sidebar, /aria-pressed=\{rankingsOpen\}/);
  assert.match(sidebar, /reader-ranking-entry \$\{rankingsOpen \? 'is-on' : ''\}/);
});

test('a sidebar source click closes rankings, then clears the brief trail and opens that source', () => {
  const click = 'onClick={() => { setRankingsOpen(false); leaveBriefTrail(); goSource(source.source_id); }}';
  const key = "onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setRankingsOpen(false); leaveBriefTrail(); goSource(source.source_id); } }}";
  assert.ok(sidebar.includes(click), 'source click');
  assert.ok(sidebar.includes(key), 'source keydown');
});

test('sidebar tag and discover clicks close rankings before they navigate', () => {
  assert.ok(
    sidebar.includes('onPick={(id) => { setRankingsOpen(false); leaveBriefTrail(); goTag(id); }}'),
    'tag pick',
  );
  assert.ok(
    sidebar.includes('onClick={() => { setRankingsOpen(false); openDiscoverSources(); }}'),
    'discover more',
  );
});

test('view rail and discover rail already close rankings on the way out', () => {
  assert.match(desktop, /setBriefOpen\(false\); setRankingsOpen\(false\); leaveBriefTrail\(\); goView\(view\)/);
  assert.match(desktop, /setBriefOpen\(false\); setRankingsOpen\(false\); leaveBriefTrail\(\); openDiscover\(\)/);
});

test('opening a source or tag drops discover and any in-flight article open', () => {
  const goSource = between(state, 'const goSource = (sourceId) => {', 'const goTag =');
  const goTag = between(state, 'const goTag = (tagId) => {', 'const deepLinkDoneRef');
  for (const fn of [goSource, goTag]) {
    assert.match(fn, /supersedePendingOpen\(\)/);
    assert.match(fn, /setDiscover\(false\)/);
    assert.ok(fn.indexOf('supersedePendingOpen()') < fn.indexOf('setActiveSourceId'));
  }
  const openDiscover = between(state, 'const openDiscover = useCallback((opts) => {', 'const closeDiscover');
  assert.match(openDiscover, /supersedePendingOpen\(\)/);
});

test('mobile rankings are a full-screen tab, so the desktop source-list gate is not copied there', () => {
  assert.doesNotMatch(mobile, /rankingsOpen/);
  assert.doesNotMatch(mobile, /reader-source-scroll/);
  assert.match(mobile, /tab === 'rankings'/);
  assert.match(mobile, /<RankingsPage/);
});
