import test from 'node:test';
import assert from 'node:assert/strict';

import { collectionRunMessage } from './collection.js';

test('collection feedback reports queued analysis work', () => {
  assert.equal(
    collectionRunMessage('批量抓取完成', {
      saved_count: 5,
      analysis_queued_count: 3,
      failed_count: 0,
    }, 2),
    '批量抓取完成：完成 2 个节点，新增 5 条，已入队分析 3 篇',
  );
});

test('collection feedback omits analysis text when nothing was queued', () => {
  assert.equal(
    collectionRunMessage('采集完成', { saved_count: 0, failed_count: 0 }),
    '采集完成：新增 0 条',
  );
});
