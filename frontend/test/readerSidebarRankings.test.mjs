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

// 源栏内容是一个常量(issue #279 第二批):文章 / 播客容器里挂在左栏,社交页挂在页边
const sidebar = between(
  desktop,
  '/* ── 源栏 · 我的订阅 ──',
  '/* ── 源栏结束 ── */',
);

// issue #279 第二批(2026-11-13):整幅页面(早报 / 榜单 / 发现 / 社交)不挂来源栏——它是文章与播客两个容器的东西,与这些页面无关。
// 取代 2026-11-04(#221 目检)「榜单打开时源列表保持在场」。社交页把同一份账号列表挂到页边
test('wide pages (brief / rankings / discover / social) drop the desktop source column; social hangs the same list in its margin', () => {
  assert.match(desktop, /const widePage = pageOpen \|\| discover \|\| socialView;/);
  // 第三批:来源栏可收起(收起后同一份列表从列表栏头的范围名下拉出来)
  assert.match(desktop, /\{!widePage && !srcFolded && <aside className="reader-col reader-col-sources">\{sourcesPanel\}<\/aside>\}/);
  assert.match(desktop, /\{srcFolded && srcPop && \(\s*<div\s+className="reader-srcpop"/);
  assert.match(desktop, /<SocialFlow\s+side=\{sourcesPanel\}/);
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
  // issue #279 第三批:返回不再是阅读窗顶上的一条色带,并入吸顶栏左端(占掉来源名的位置)
  const trail = between(desktop, "const paneBack = !briefReturn ? null : briefReturn.from === 'rankings' ? (", '</nav>');
  assert.match(trail, /setRankingRestore\(briefReturn\)/);
  assert.match(trail, /setBriefReturn\(null\)/);
  assert.match(trail, /setRankingsOpen\(true\)/);
  assert.match(trail, /返回热点事件/);
  // 「下一条」只给早报那一程:榜单的 ctx 没有序列
  assert.match(desktop, /const briefNext = briefReturn && briefReturn\.from !== 'rankings' \?/);
  assert.match(desktop, /\{paneBack \|\| \(\s*<div className="reader-crumb">/);
  assert.doesNotMatch(desktop, /reader-brief-trail/);
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

// issue #279 第二批补(2026-11-16,用户指出发现页右上角违和):页头右侧的控件分两组各自贴右——
// 早先六个控件摊在同一张五列网格里,上一行多出「订阅全部」时会把下一行的形态筛选和排序撑开。
test('discover header: the two right-hand groups are separate flex boxes, each flush right; mobile keeps the flat order', () => {
  const discover = readFileSync(new URL('../src/components/DiscoverPage.jsx', import.meta.url), 'utf8');
  const tools = between(discover, '<div className="reader-disc-tools">', '<div className="reader-disc-scroll">');
  const acts = between(tools, '<div className="reader-disc-acts">', '<div className="reader-disc-filters">');
  const filters = tools.slice(tools.indexOf('<div className="reader-disc-filters">'));
  // 页名那一行:搜索 + 添加;页签行:整组订阅 + 形态筛选 + 排序(整组订阅挨着它作用的筛选)
  assert.ok(acts.includes('className="reader-disc-search"') && acts.includes('className="reader-disc-add"'));
  assert.ok(!acts.includes('reader-disc-bulk-sub') && !acts.includes('reader-disc-seg') && !acts.includes('reader-disc-sort'));
  const order = ['reader-disc-bulk-sub', 'reader-seg reader-disc-seg', 'reader-disc-sort '].map((name) => filters.indexOf(name));
  assert.ok(order.every((at) => at >= 0) && order[0] < order[1] && order[1] < order[2], `页签行右侧的顺序 ${order}`);
  // 拍平后的顺序与改前一致(手机壳里两层分组不成盒):页签、搜索、添加、整组订阅、形态筛选、排序
  const flat = ['reader-disc-viewseg', 'className="reader-disc-search"', 'className="reader-disc-add"', 'reader-disc-bulk-sub', 'reader-seg reader-disc-seg', 'reader-disc-sort '].map((name) => tools.indexOf(name));
  assert.deepEqual([...flat].sort((a, b) => a - b), flat);

  const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
  assert.match(css, /\n  \.reader-disc-acts, \.reader-disc-filters \{ display: contents; \}/, '默认不成盒');
  const head = between(css, '.reader-shell > .reader-disc .reader-disc-head-inner {', '.reader-shell > .reader-disc .reader-disc-coll-dethead');
  assert.match(head, /grid-template-columns: auto minmax\(0, 1fr\); grid-template-rows: 124px 41px;/);
  assert.match(head, /\.reader-disc-acts \{ grid-column: 2; grid-row: 1; justify-self: end; [^}]*display: flex;/);
  assert.match(head, /\.reader-disc-filters \{ grid-column: 2; grid-row: 2; justify-self: end; display: flex;/);
  // 单个控件不再各占一列
  for (const name of ['search', 'add', 'bulk-sub', 'seg', 'sort']) {
    assert.doesNotMatch(head, new RegExp(`\\.reader-disc-${name} \\{[^}]*grid-column`), `.reader-disc-${name} 不该再指定网格列`);
  }
  const mobileCss = readFileSync(new URL('../src/mobile.css', import.meta.url), 'utf8');
  assert.ok(!mobileCss.includes('reader-disc-acts') && !mobileCss.includes('reader-disc-filters'));
});
