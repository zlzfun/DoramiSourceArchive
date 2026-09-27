import assert from 'node:assert/strict';
import test from 'node:test';

import { podcastListAvailabilityMeta } from './podcast.js';

test('podcast list availability never exposes background processing state', () => {
  assert.deepEqual(podcastListAvailabilityMeta(false), {
    label: '仅提供原节目',
    tone: 'idle',
  });
  assert.deepEqual(podcastListAvailabilityMeta(true), {
    label: '精品导读已就绪',
    tone: 'ok',
  });
});

test('list labels separate text, running synthesis, failed audio and playable audio', () => {
  const guide = { blog_ready: true, status: 'failed' };
  assert.deepEqual(podcastListAvailabilityMeta({ premium_guide: guide }), {
    label: '导读文字已生成，音频失败', tone: 'bad',
  });
  assert.equal(podcastListAvailabilityMeta({ premium_guide: { ...guide, status: 'synthesizing' } }).tone, 'run');
  assert.equal(podcastListAvailabilityMeta({ premium_guide: { ...guide, status: '' } }).label, '导读文字已生成，音频待生成');
  assert.equal(podcastListAvailabilityMeta({ condensed_audio_url: '/audio', premium_guide: guide }).label, '精品导读音频已就绪');
});

test('non-Chinese text-only guide never promises pending audio', () => {
  assert.deepEqual(podcastListAvailabilityMeta({
    premium_guide: { blog_ready: true, status: 'ready', mode: 'brief_zh' },
  }), {
    label: '中文导读已生成', tone: 'ok',
  });
});
