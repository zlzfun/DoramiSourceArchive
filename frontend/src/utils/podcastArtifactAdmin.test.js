import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatPodcastArtifactBytes,
  formatPodcastArtifactTime,
  podcastArtifactKindLabel,
  podcastArtifactStatusMeta,
  podcastArtifactTotalStorageMeta,
} from './podcastArtifactAdmin.js';

test('formatPodcastArtifactBytes formats storage sizes without invalid output', () => {
  assert.equal(formatPodcastArtifactBytes(0), '0 B');
  assert.equal(formatPodcastArtifactBytes(1024), '1.0 KB');
  assert.equal(formatPodcastArtifactBytes(5.5 * 1024 * 1024), '5.5 MB');
  assert.equal(formatPodcastArtifactBytes(Number.NaN), '0 B');
});

test('artifact labels preserve unknown backend values for honest display', () => {
  assert.equal(podcastArtifactKindLabel('digest_audio_zh'), '中文精简版');
  assert.equal(podcastArtifactKindLabel('future_kind'), 'future_kind');
  assert.deepEqual(podcastArtifactStatusMeta('published'), { label: '已发布', tone: 'ok' });
  assert.deepEqual(podcastArtifactStatusMeta('ready'), { label: '待发布', tone: 'warn' });
  assert.deepEqual(podcastArtifactStatusMeta('future_status'), { label: 'future_status', tone: 'idle' });
});

test('formatPodcastArtifactTime falls back for empty and invalid values', () => {
  assert.equal(formatPodcastArtifactTime(''), '—');
  assert.equal(formatPodcastArtifactTime('not-a-date'), '—');
  assert.match(formatPodcastArtifactTime('2026-09-05T01:02:00Z', 'zh-CN'), /2026/);
});

test('zero podcast total quota shows actual usage without implying a zero-byte limit', () => {
  const stats = {
    disk_bytes: 12 * 1024 * 1024,
    quota_bytes: 0,
    quota_remaining_bytes: 0,
  };

  const total = podcastArtifactTotalStorageMeta(stats);
  assert.deepEqual(total, {
    value: '12.0 MB',
    label: '总存储占用',
    sub: '不设固定上限 · 按磁盘余量保护',
  });

  assert.doesNotMatch(`${total.value} ${total.sub}`, /0 B/);
});

test('positive podcast quotas keep remaining and total quota details', () => {
  const stats = {
    disk_bytes: 12 * 1024 * 1024,
    quota_bytes: 20 * 1024 * 1024,
    quota_remaining_bytes: 8 * 1024 * 1024,
  };

  assert.deepEqual(podcastArtifactTotalStorageMeta(stats), {
    value: '8.0 MB',
    label: '配额余量',
    sub: '总额 20.0 MB',
  });
});
