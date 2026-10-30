import assert from 'node:assert/strict';
import test from 'node:test';

import { podcastListAvailabilityMeta } from './podcast.js';

test('podcast list availability never exposes background processing state', () => {
  assert.equal(podcastListAvailabilityMeta(false), null);
  assert.deepEqual(podcastListAvailabilityMeta(true), {
    label: '有声导读',
    kind: 'audio',
  });
});

test('list labels keep playable audio and a readable text guide distinct', () => {
  const guide = { blog_ready: true, status: 'failed' };
  assert.deepEqual(podcastListAvailabilityMeta({ premium_guide: guide }), {
    label: '中文导读', kind: 'text',
  });
  assert.equal(
    podcastListAvailabilityMeta({ condensed_audio_url: '/audio', premium_guide: guide }).label,
    '有声导读',
  );
});

test('non-Chinese text-only guide never promises pending audio', () => {
  assert.deepEqual(podcastListAvailabilityMeta({
    premium_guide: { blog_ready: true, status: 'ready', mode: 'brief_zh' },
  }), {
    label: '中文导读', kind: 'text',
  });
});
