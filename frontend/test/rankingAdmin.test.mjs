import test from 'node:test';
import assert from 'node:assert/strict';

import { rankingCoverageText, rankingSnapshotStatusMeta } from '../src/utils/rankingAdmin.js';

test('ranking admin status prioritizes an active refresh and exposes empty/degraded/ready states', () => {
  assert.deepEqual(rankingSnapshotStatusMeta({ refresh_running: true }), { label: '刷新中', tone: 'run' });
  assert.deepEqual(rankingSnapshotStatusMeta({ snapshot: null }), { label: '待生成', tone: 'idle' });
  assert.deepEqual(rankingSnapshotStatusMeta({ snapshot: { status: 'degraded' } }), { label: '覆盖不足', tone: 'warn' });
  assert.deepEqual(rankingSnapshotStatusMeta({ snapshot: { status: 'complete' } }), { label: '已就绪', tone: 'ok' });
});

test('ranking admin coverage copy reports tagged and eligible counts for both shapes', () => {
  const text = rankingCoverageText({
    coverage: {
      article: { eligible: 120, analyzed: 110, tagged: 104 },
      podcast: { eligible: 20, analyzed: 16, tagged: 10 },
    },
  });
  assert.match(text, /文章：可入榜 120，已分析 110，已打标签 104/);
  assert.match(text, /播客：可入榜 20，已分析 16，已打标签 10/);
});
