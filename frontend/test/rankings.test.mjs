// 榜单(issue #221 收敛版):热点事件 · 深度阅读 · 播客精选的纯逻辑与结构护栏。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  RANKING_SEGMENTS,
  beijingDayLabel,
  beijingStamp,
  beijingTime,
  eventKicker,
  eventMetaParts,
  eventMovement,
  eventWindowHours,
  firstReportAt,
  latestUpdate,
  logoStack,
  memberTime,
  membersByTime,
  normalizeRankingSegment,
  publisherGroups,
  rankingDataKey,
  rankingEmptyText,
  rankingEntrySub,
  rankingScopeNote,
  representativeOf,
  showRecentFlag,
  timelineDays,
} from '../src/utils/rankings.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

// 「现在」= 2026-11-02 11:00 北京时间(03:00Z)
const NOW = Date.parse('2026-11-02T03:00:00Z');

test('三段命名与顺序:热点事件 · 深度阅读 · 播客精选,默认热点事件', () => {
  assert.deepEqual(RANKING_SEGMENTS.map(([, label]) => label), ['热点事件', '深度阅读', '播客精选']);
  assert.equal(normalizeRankingSegment('nope'), 'events');
  assert.equal(normalizeRankingSegment('listens'), 'listens');
});

test('热点事件:本周 = 168 小时(默认),近 2 天 = 48 小时;两档各一份数据', () => {
  assert.equal(eventWindowHours('week'), 168);
  assert.equal(eventWindowHours(undefined), 168);
  assert.equal(eventWindowHours('recent'), 48);
  assert.equal(rankingDataKey('events', 'week'), 'events:week');
  assert.equal(rankingDataKey('events', 'recent'), 'events:recent');
  assert.equal(rankingDataKey('reads', 'recent'), 'reads');
});

test('范围说明:三段都写明全站、与个人订阅无关;深度阅读写清只收四种内容类型', () => {
  for (const [seg] of RANKING_SEGMENTS) {
    const note = rankingScopeNote(seg, 'week');
    assert.match(note, /^全站/);
    assert.match(note, /与个人订阅无关/);
    assert.doesNotMatch(note, /标签|归档|采集|分发/);
  }
  assert.equal(rankingScopeNote('events', 'week'), '全站近 7 天被两家以上报道的事件，按报道的发布主体数排序，与个人订阅无关');
  assert.match(rankingScopeNote('events', 'recent'), /^全站近 2 天/);
  assert.equal(rankingScopeNote('reads'), '全站近 7 天按内容价值挑选的论文、教程、观点与汇编，同一件事只留一篇，与个人订阅无关');
  assert.equal(rankingScopeNote('listens'), '全站近 7 天按内容价值挑选的单集，与个人订阅无关');
});

test('空榜各一句话', () => {
  assert.equal(rankingEmptyText('events', 'week'), '近 7 天还没有被两家以上报道的事件');
  assert.equal(rankingEmptyText('events', 'recent'), '近 2 天还没有被两家以上报道的事件');
  assert.equal(rankingEmptyText('reads'), '近 7 天还没有达到门槛的深度内容');
  assert.equal(rankingEmptyText('listens'), '近 7 天还没有达到门槛的单集');
});

test('源栏入口副标题随段;没载入过或空榜写段名,不写「0 件」', () => {
  assert.equal(rankingEntrySub('events', 10), '热点事件 10 件');
  assert.equal(rankingEntrySub('reads', 9), '深度阅读 9 篇');
  assert.equal(rankingEntrySub('listens', 10), '播客精选 10 集');
  assert.equal(rankingEntrySub('events', undefined), '热点事件');
  assert.equal(rankingEntrySub('listens', 0), '播客精选');
});

test('名次变化:对比快照 ↑ 绿 / ↓ 红 / 相等留空;没对比名次但有快照 = 进榜;没有快照留空', () => {
  assert.deepEqual(eventMovement({ rank: 2, previous_rank: 5 }, '2026-11-02'), { label: '↑3', direction: 'up', title: '较 11月2日 07:00 上升 3 位' });
  assert.deepEqual(eventMovement({ rank: 6, previous_rank: 1 }, '2026-11-02'), { label: '↓5', direction: 'down', title: '较 11月2日 07:00 下降 5 位' });
  assert.equal(eventMovement({ rank: 4, previous_rank: 4 }, '2026-11-02'), null);
  assert.deepEqual(eventMovement({ rank: 1, previous_rank: null }, '2026-11-02'), { label: '进榜', direction: 'enter', title: '11月2日 07:00 时不在榜上' });
  assert.equal(eventMovement({ rank: 1, previous_rank: null }, null), null);
  // 不画「新」签:is_new 不参与
  assert.equal(eventMovement({ rank: 1, previous_rank: 1, is_new: true }, '2026-11-02'), null);
});

test('「近 2 天」签只在本周视图、is_recent 为真时画', () => {
  assert.equal(showRecentFlag('week', { is_recent: true }), true);
  assert.equal(showRecentFlag('recent', { is_recent: true }), false);
  assert.equal(showRecentFlag('week', { is_recent: false, is_new: true }), false);
});

test('徽标叠放:至多 3 枚,超过时 2 枚 + 「+N」', () => {
  assert.deepEqual(logoStack(['a', 'b']), { shown: ['a', 'b'], rest: 0 });
  assert.deepEqual(logoStack(['a', 'b', 'c']), { shown: ['a', 'b', 'c'], rest: 0 });
  assert.deepEqual(logoStack(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']), { shown: ['a', 'b'], rest: 6 });
  assert.deepEqual(logoStack(null), { shown: [], rest: 0 });
});

const DETAIL = {
  event_id: 560,
  rank: 1,
  publisher_count: 3,
  member_count: 4,
  representative: { article_id: 'g1', title: 'Gemini 4 Carbon', source_id: 'gemini', source_name: 'Gemini 模型公告', source_role: 'official', url: 'https://x', publish_date: '2026-10-28T23:00:00Z' },
  publishers: [
    { key: 'google', name: 'Gemini 模型公告', count: 2, source_id: 'gemini' },
    { key: 'arena', name: 'Arena 排行榜更新', count: 1, source_id: 'arena' },
    { key: 'ls', name: 'Latent Space', count: 1, source_id: 'latent' },
  ],
  members: [
    { article_id: 'l1', title: '[AINews] Carbon tops', title_zh: 'Carbon 登顶', source_id: 'latent', source_name: 'Latent Space', source_role: 'personal', publisher_key: 'ls', report_at: '2026-11-01T17:30:00Z', is_representative: false, summary: '最新一篇' },
    { article_id: 'a1', title: 'Arena update', source_id: 'arena', source_name: 'Arena 排行榜更新', source_role: 'leaderboard', publisher_key: 'arena', report_at: '2026-10-30T02:00:00Z', is_representative: false },
    { article_id: 'd1', title: 'DeepMind 博客', source_id: 'deepmind', source_name: 'Google DeepMind 博客', source_role: 'official', publisher_key: 'google', report_at: '2026-10-29T01:00:00Z', is_representative: false },
    { article_id: 'g1', title: 'Gemini 4 Carbon', title_zh: 'Gemini 4 Carbon 发布', source_id: 'gemini', source_name: 'Gemini 模型公告', source_role: 'official', publisher_key: 'google', report_at: '2026-10-28T23:00:00Z', publish_date: '2026-10-28T23:00:00Z', is_representative: true, summary: '代表报道摘要' },
  ],
};

test('来源分组:按发布主体,同主体多渠道括注;有官方渠道即官方;徽标用主体代表渠道', () => {
  const groups = publisherGroups(DETAIL);
  assert.deepEqual(groups.map((g) => [g.key, g.name, g.count, g.role, g.sourceId]), [
    ['google', 'Gemini 模型公告', 2, 'official', 'gemini'],
    ['arena', 'Arena 排行榜更新', 1, 'leaderboard', 'arena'],
    ['ls', 'Latent Space', 1, 'personal', 'latent'],
  ]);
  assert.deepEqual(groups[0].channels, ['Google DeepMind 博客']);
  assert.deepEqual(groups[1].channels, []);
  // 不在 publishers 里的主体也有自己的一组
  const extra = publisherGroups({ ...DETAIL, members: [...DETAIL.members, { article_id: 'x', source_id: 'it', source_name: 'IT之家', publisher_key: 'ithome', source_role: 'media' }] });
  assert.equal(extra.at(-1).name, 'IT之家');
});

test('代表报道固定取成员里标记的那篇(带中文标题与摘要);首报 = 成员最早发布时间', () => {
  const rep = representativeOf(DETAIL);
  assert.equal(rep.article_id, 'g1');
  assert.equal(rep.title_zh, 'Gemini 4 Carbon 发布');
  assert.equal(rep.url, 'https://x');
  assert.equal(firstReportAt(DETAIL), '2026-10-28T23:00:00Z');
  assert.equal(firstReportAt({ ...DETAIL, first_report_at: '2026-10-27T00:00:00Z' }), '2026-10-27T00:00:00Z');
  assert.equal(representativeOf({ members: [] }), null);
});

test('成员时间:先读有效发布时间 published_at(晚入库的旧稿不按归组用的截断值排),旧响应退回 report_at / publish_date', () => {
  const late = { article_id: 'late', published_at: '2026-10-24T02:00:00Z', report_at: '2026-10-30T04:00:00Z' };
  const fresh = { article_id: 'fresh', published_at: '2026-10-28T02:00:00Z', report_at: '2026-10-29T00:00:00Z' };
  assert.equal(memberTime(late), '2026-10-24T02:00:00Z');
  assert.equal(memberTime({ report_at: 'r', publish_date: 'p' }), 'r');
  assert.equal(memberTime({ publish_date: 'p' }), 'p');
  assert.deepEqual(membersByTime({ members: [late, fresh] }).map((m) => m.article_id), ['fresh', 'late']);
  assert.equal(firstReportAt({ members: [fresh, late] }), '2026-10-24T02:00:00Z');
  assert.deepEqual(timelineDays([fresh, late], NOW).map((day) => day.items[0].article_id), ['fresh', 'late']);
});

test('最新进展:最新一篇不是代表报道时才有', () => {
  assert.equal(latestUpdate(DETAIL).article_id, 'l1');
  assert.equal(latestUpdate({ ...DETAIL, members: DETAIL.members.slice(3) }), null);
  // 乱序也按报道时间取最新
  assert.equal(membersByTime({ members: [...DETAIL.members].reverse() })[0].article_id, 'l1');
});

test('报道时间线按北京时间分日:今天 / 昨天 / M月D日 周X', () => {
  const days = timelineDays(membersByTime(DETAIL), NOW);
  assert.deepEqual(days.map((d) => [d.label, d.items.map((m) => m.article_id)]), [
    ['今天 · 11月2日', ['l1']],          // 11-01T17:30Z = 北京 11-02 01:30
    ['10月30日 周五', ['a1']],
    ['10月29日 周四', ['d1', 'g1']],       // 10-28T23:00Z = 北京 10-29 07:00
  ]);
  assert.equal(beijingDayLabel('2026-11-01T03:00:00Z', NOW), '昨天 · 11月1日');
  assert.equal(beijingTime('2026-11-01T17:30:00Z'), '01:30');
  assert.equal(beijingStamp('2026-10-31T09:32:46Z', NOW), '10月31日 17:32');
  assert.equal(beijingStamp('2025-12-31T09:32:46Z', NOW), '2025年12月31日 17:32');
});

test('没有真实时刻的报道(feed 只给日期,落 00:00)只画日期,不换算出「08:00」', () => {
  assert.equal(beijingTime('2026-10-28T00:00:00+00:00'), '');
  assert.equal(beijingStamp('2026-10-28T00:00:00+00:00', NOW), '10月28日');
  assert.equal(beijingDayLabel('2026-10-28T00:00:00+00:00', NOW), '10月28日 周三');
  assert.equal(beijingDayLabel('2026-11-02', NOW), '今天 · 11月2日');
  const days = timelineDays([{ article_id: 'x', report_at: '2026-10-28T00:00:00+00:00' }], NOW);
  assert.deepEqual(days.map((d) => d.key), ['2026-10-28']);
});

test('详情眉头按打开时所在视图与名次;不在榜上只写「热点事件」;元信息带首报', () => {
  assert.equal(eventKicker('week', 3), '热点事件 · 本周第 3 件');
  assert.equal(eventKicker('recent', 1), '热点事件 · 近 2 天第 1 件');
  assert.equal(eventKicker('week', null), '热点事件');
  assert.deepEqual(eventMetaParts(DETAIL, NOW), ['3 家报道', '4 篇', '首报 10月29日 07:00']);
});

test('榜单页结构:三段分段、无标签三轴 / 30 日趋势 / 全局高分榜 / 必读必听,不展示热度值', () => {
  const page = read('../src/components/RankingsPage.jsx');
  for (const gone of ['RANKING_AXES', '30 日趋势', '全局高分榜', '必读', '必听', 'fetchReaderRanking', 'hot_score', 'heat']) {
    assert.ok(!page.includes(gone), `不应再出现:${gone}`);
  }
  assert.match(page, /fetchReaderEvents\(/);
  assert.match(page, /fetchReaderEvent\(/);
  assert.match(page, /fetchReaderPicks\(/);
  // 详情走层原语:桌面 Drawer、手机 Sheet
  assert.match(page, /<Drawer[^>]*className="rk-drawer"/);
  assert.match(page, /<Sheet[\s\S]*?className="rk-sheet"/);
  const api = read('../src/api.js');
  for (const gone of ['fetchReaderRankings', 'fetchReaderRankingTag', 'fetchReaderRankingHistory']) {
    assert.ok(!api.includes(gone), `api.js 不应再有 ${gone}`);
  }
  assert.match(api, /\/reader\/events\?/);
  assert.match(api, /\/reader\/rankings\/picks\?/);
});

test('标签榜的旧样式随读者面撤出一并删除', () => {
  const css = read('../src/index.css') + read('../src/mobile.css');
  for (const gone of ['.ranking-tag-row', '.ranking-trend', '.ranking-content-row', '.ranking-must', '.ranking-global', '.ranking-layout']) {
    assert.ok(!css.includes(gone), `样式表不应再有 ${gone}`);
  }
});

test('行可悬停:点击由 <li> 接住、内容不再整片关指针;播客分数的 title / 读屏名带评分依据', () => {
  const page = read('../src/components/RankingsPage.jsx');
  assert.match(page, /<li className=\{`rk-row[^`]*`\.trim\(\)\} onClick=\{onOpen\}>/);
  assert.match(page, /<button type="button" className="rk-hit" aria-label=\{label\} \/>/);
  assert.match(page, /podcastReaderBasisText\(/);
  assert.match(page, /title=\{scoreNote\}/);
  const css = read('../src/index.css');
  assert.ok(!/\.rk-side \{[^}]*pointer-events: none/.test(css), '.rk-side 不应再整片关指针(挡住 title)');
});
