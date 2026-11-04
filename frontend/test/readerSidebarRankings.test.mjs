import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const desktop = readFileSync(new URL('../src/components/ReaderTab.jsx', import.meta.url), 'utf8');
const mobile = readFileSync(new URL('../src/components/mobile/MobileReader.jsx', import.meta.url), 'utf8');
const state = readFileSync(new URL('../src/hooks/useReaderState.js', import.meta.url), 'utf8');
const workspace = readFileSync(new URL('../src/components/ReaderWorkspace.jsx', import.meta.url), 'utf8');

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

test('rankings keep the desktop subscription list mounted', () => {
  assert.doesNotMatch(sidebar, /!rankingsOpen\s*&&/);
  assert.match(sidebar, /<div className="reader-source-scroll">/);
});

// issue #221 目检(2026-11-04):榜单收敛后不分文章 / 播客,入口从源栏顶挪到视图轨,与发现同组(看全站)、排在发现之前;
// 源栏里不再有榜单入口,也不随视图写文章榜 / 播客榜
test('the 榜单 entry sits on the view rail right before 发现, not in the source sidebar', () => {
  assert.doesNotMatch(sidebar, /榜单<\/strong>|reader-ranking-entry|openRankings/);
  const rail = between(desktop, '{/* 榜单 + 发现:', 'aria-label={onboardingRequired');
  assert.match(rail, /aria-label="榜单"\s+aria-pressed=\{rankingsOpen\}\s+onClick=\{openRankings\}/);
  assert.match(rail, /<span className="reader-vrail-label">榜单<\/span>/);
  assert.ok(rail.indexOf('aria-label="榜单"') < rail.lastIndexOf('<button'), '榜单在发现之前');
  assert.doesNotMatch(desktop, /文章榜|播客榜|标签趋势|rankingEntrySub|rankingCounts/);
  // 与发现并列,不再嵌在发现页里:发现页的「榜单」跳转页签撤掉(桌面与手机)
  const discover = readFileSync(new URL('../src/components/DiscoverPage.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(discover, /onOpenRankings|'rankings', '榜单'/);
  assert.doesNotMatch(desktop + mobile, /onOpenRankings/);
});

// issue #221:段与时间范围提到 ReaderWorkspace(关掉再打开回到上次的段,桌面 ⇄ 手机互切不丢),默认热点事件;
// 切段不再联动视图轨(去掉 onShapeChange={goView}),也不按当前视图选初始段
test('ranking segment lives in the workspace, defaults to 热点事件 and no longer drives the view rail', () => {
  assert.match(workspace, /useState\(DEFAULT_RANKING_SEGMENT\)/);
  assert.match(workspace, /useState\(DEFAULT_EVENT_RANGE\)/);
  const page = between(desktop, '{rankingsOpen && (', '/>');
  assert.match(page, /segment=\{rankingSegment\}/);
  assert.match(page, /onSegmentChange=\{setRankingSegment\}/);
  assert.doesNotMatch(page, /onShapeChange|initialShape|goView/);
  assert.doesNotMatch(mobile, /RANK_ITEMS|rankShape|文章榜|播客榜/);
  assert.match(mobile, /<TitleDial items=\{RANKING_SEGMENTS\} value=\{rankingSegment\} onChange=\{setRankingSegment\}/);
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

// issue #221 目检:从榜单点进站内原文后能回去——与早报同一条外出返回带(ctx.from === 'rankings'),
// 桌面阅读窗顶「返回热点事件 / 返回榜单」、手机正文页返回都把 ctx 作为 restore 交回榜单页;点入口 / 点 Tab 是新开
test('opening an article from rankings leaves a return trail that reopens rankings where the reader was', () => {
  assert.match(workspace, /const \[rankingRestore, setRankingRestore\] = useState\(null\)/);
  const page = between(desktop, '{rankingsOpen && (', '/>');
  assert.match(page, /restore=\{rankingRestore\}/);
  assert.match(page, /onOpenArticle=\{async \(articleId, ctx\) =>/);
  assert.match(page, /setBriefReturn\(ctx \|\| null\)/);
  const trail = between(desktop, "{briefReturn?.from === 'rankings' && (", '</nav>');
  assert.match(trail, /setRankingRestore\(briefReturn\)/);
  assert.match(trail, /setBriefReturn\(null\)/);
  assert.match(trail, /setRankingsOpen\(true\)/);
  assert.match(trail, /返回热点事件/);
  // 早报那条带不给榜单的 ctx 画「早报下一条」
  assert.match(desktop, /\{briefReturn && briefReturn\.from !== 'rankings' && \(\(\) => \{/);
  const openRankings = between(desktop, 'const openRankings = useCallback(() => {', '}, [');
  assert.match(openRankings, /setRankingRestore\(null\)/);

  const close = between(mobile, 'const closeArticle = useCallback(() => {', '}, [');
  assert.match(close, /briefReturn\?\.from === 'rankings'/);
  assert.match(close, /setRankingRestore\(briefReturn\)/);
  assert.match(close, /setTab\('rankings'\)/);
  const mobilePage = between(mobile, '<RankingsPage', '/>');
  assert.match(mobilePage, /restore=\{rankingRestore\}/);
  assert.match(mobilePage, /setBriefReturn\(ctx \|\| null\)/);
  assert.match(mobile, /if \(t === 'rankings'\) setRankingRestore\(null\)/);
});
