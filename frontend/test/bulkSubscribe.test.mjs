import test from 'node:test';
import assert from 'node:assert/strict';
import { bulkSubscribeModel } from '../src/utils/bulkSubscribe.js';

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
});
