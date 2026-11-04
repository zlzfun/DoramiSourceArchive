// issue #230 批 3b:早报「本期编排 · 入选」按正在看的那一期取数;早报卡长按动作单的取材
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { briefSelectionCounts } from '../src/utils/briefStats.js';
import { briefMenuArticle, briefMenuItems } from '../src/utils/briefMenu.js';

const item = (lane, extra = {}) => ({ selection_lane: lane, matched_interest_codes: [], snapshot: {}, ...extra });

test('入选数字只取这一期自己的快照,看往期不混今天的数字', () => {
  const today = {
    expected_source_ids: ['a', 'b', 'c'],
    selection_stats: { candidate_count: 29, followed_count: 2, same_event_merged: 1 },
    items: [item('news'), item('news', { matched_interest_codes: ['x'] }), item('read'), item('listen'), item('breaking')],
  };
  const past = {
    expected_source_ids: ['a', 'b'],
    selection_stats: { candidate_count: 115, followed_count: 4 },
    items: [item('news'), item('news'), item('news', { matched_interest_codes: ['y'], snapshot: { subscribed: false } })],
  };
  const t = briefSelectionCounts(today);
  const p = briefSelectionCounts(past);
  assert.deepEqual([t.scopeCount, t.candidateCount, t.ownItems.length, t.followedCount, t.mergedCount], [3, 29, 2, 2, 1]);
  assert.deepEqual([t.readCount, t.listenCount, t.valueCount, t.interestCount], [1, 1, 2, 1]);
  assert.deepEqual([p.scopeCount, p.candidateCount, p.ownItems.length, p.followedCount, p.mergedCount], [2, 115, 3, 4, 0]);
  assert.deepEqual([p.interestCount, p.externalHits, p.valueCount], [1, 1, 0]);
});

test('历史版本没有选篇统计时对应半句省略;订阅为空按兴趣编排', () => {
  const legacy = briefSelectionCounts({ expected_source_ids: ['a'], items: [item('news')] });
  assert.equal(legacy.stats, null);
  assert.equal(legacy.candidateCount, null);
  assert.equal(legacy.followedCount, null);
  assert.equal(legacy.interestOnly, false);
  assert.equal(briefSelectionCounts({ expected_source_ids: [], items: [item('news')] }).interestOnly, true);
  assert.equal(briefSelectionCounts({ expected_source_ids: [], selection_stats: { interest_only: false }, items: [] }).interestOnly, false);
  assert.equal(briefSelectionCounts({ degraded_reason: 'insufficient_non_interest_content' }).ratioUnfillable, true);
  assert.equal(briefSelectionCounts(null).items.length, 0);
});

test('早报条目折成动作单的文章形状;没有 article_id 不出', () => {
  const row = { article_id: 'rss_x', snapshot: { title: 'T', title_zh: '标题', source_id: 's', source_url: 'https://e.x/1' } };
  assert.deepEqual(briefMenuArticle(row), { id: 'rss_x', title: 'T', title_zh: '标题', source_id: 's', source_url: 'https://e.x/1' });
  assert.equal(briefMenuArticle({ snapshot: {} }), null);
  assert.ok(briefMenuArticle(row, { shape: 'podcast' }).podcast);
  assert.ok(briefMenuArticle({ ...row, snapshot: { ...row.snapshot, content_type: 'podcast_episode' } }).podcast);
});

test('早报动作单去掉读态一项,不留孤立的分隔线', () => {
  const menu = [
    { key: 'read' }, { key: 'fav' }, { type: 'sep' },
    { key: 'copy' }, { key: 'open' }, { type: 'sep' },
    { key: 'markall' },
  ];
  assert.deepEqual(briefMenuItems(menu).map((it) => it.key || it.type), ['fav', 'sep', 'copy', 'open', 'sep', 'markall']);
  assert.deepEqual(briefMenuItems([{ key: 'read' }, { type: 'sep' }, { key: 'copy' }]).map((it) => it.key || it.type), ['copy']);
});
