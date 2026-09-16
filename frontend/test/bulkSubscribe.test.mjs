import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BULK_SUBSCRIBE_TIMEOUT_MS,
  bulkSubscribeModel,
  createBulkSubscribeDeadline,
  waitForBulkSubscribeSettlement,
} from '../src/utils/bulkSubscribe.js';

const sources = [
  { source_id: 'article_a', shape: 'article' },
  { source_id: 'article_b', shape: 'article' },
  { source_id: 'article_hidden', shape: 'article', hidden: true },
  { source_id: 'podcast_a', shape: 'podcast' },
  { source_id: 'social_a', shape: 'social' },
];

test('文章筛选只统计可见且尚未订阅的文章源', () => {
  const model = bulkSubscribeModel('article', sources, new Set(['article_a']));
  assert.equal(model.remainingCount, 1);
  assert.equal(model.text, '订阅全部文章源（剩余 1 个）');
  assert.equal(model.disabled, false);
});

test('播客筛选使用播客源文案，完成后禁用', () => {
  const pending = bulkSubscribeModel('podcast', sources, new Set());
  assert.equal(pending.text, '订阅全部播客源（剩余 1 个）');
  assert.equal(pending.disabled, false);

  const complete = bulkSubscribeModel('podcast', sources, new Set(['podcast_a']));
  assert.equal(complete.text, '已全部订阅');
  assert.equal(complete.disabled, true);
});

test('非文章/播客筛选不显示批量入口，提交中锁定入口', () => {
  assert.equal(bulkSubscribeModel('all', sources, new Set()), null);
  const busy = bulkSubscribeModel('article', sources, new Set(), 'article');
  assert.equal(busy.text, '订阅中…');
  assert.equal(busy.disabled, true);

  const otherShape = bulkSubscribeModel('podcast', sources, new Set(), 'article');
  assert.equal(otherShape.busy, false);
  assert.equal(otherShape.disabled, true);
  assert.equal(otherShape.text, '订阅全部播客源（剩余 1 个）');
});

test('加载中或该形态没有候选源时不误报已全部订阅', () => {
  assert.equal(bulkSubscribeModel('article', sources, new Set(), null, true), null);
  assert.equal(bulkSubscribeModel('article', [], new Set()), null);
  assert.equal(bulkSubscribeModel('podcast', sources.filter((source) => source.hidden), new Set()), null);
});

test('批量订阅默认十秒超时，截止器到时中止请求', async () => {
  assert.equal(BULK_SUBSCRIBE_TIMEOUT_MS, 10_000);
  const deadline = createBulkSubscribeDeadline(5);
  await new Promise((resolve) => deadline.signal.addEventListener('abort', resolve, { once: true }));
  assert.equal(deadline.signal.aborted, true);
  assert.equal(deadline.didTimeout(), true);
  deadline.clear();
});

test('超时后轮询服务端状态，确认事务结束才刷新', async () => {
  const states = [true, true, false];
  let waits = 0;
  const settled = await waitForBulkSubscribeSettlement(
    async () => ({ processing: states.shift() }),
    { attempts: 5, intervalMs: 1, wait: async () => { waits += 1; } },
  );
  assert.equal(settled, true);
  assert.equal(waits, 2);

  const stillRunning = await waitForBulkSubscribeSettlement(
    async () => ({ processing: true }),
    { attempts: 2, intervalMs: 1, wait: async () => {} },
  );
  assert.equal(stillRunning, false);
});
