import assert from 'node:assert/strict';
import test from 'node:test';

import { podcastListAvailabilityMeta } from './podcast.js';

test('podcast list availability never exposes background processing state', () => {
  assert.deepEqual(podcastListAvailabilityMeta(false), {
    label: '仅提供原节目',
    tone: 'idle',
  });
  assert.deepEqual(podcastListAvailabilityMeta(true), {
    label: '单人速览已就绪',
    tone: 'ok',
  });
});
