import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatPodcastArtifactBytes,
  formatPodcastArtifactTime,
  podcastArtifactKindLabel,
  podcastArtifactStatusMeta,
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
  assert.deepEqual(podcastArtifactStatusMeta('ready', 'source_audio'), { label: '可处理', tone: 'ok' });
  assert.deepEqual(podcastArtifactStatusMeta('future_status'), { label: 'future_status', tone: 'idle' });
});

test('formatPodcastArtifactTime falls back for empty and invalid values', () => {
  assert.equal(formatPodcastArtifactTime(''), '—');
  assert.equal(formatPodcastArtifactTime('not-a-date'), '—');
  assert.match(formatPodcastArtifactTime('2026-09-05T01:02:00Z', 'zh-CN'), /2026/);
});
