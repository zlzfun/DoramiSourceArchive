import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PODCAST_FORCE_TTS_REASON,
  podcastPremiumTtsCommand,
} from './podcastPremiumGuide.js';

test('forced podcast TTS command uses the dedicated audited endpoint', () => {
  const command = podcastPremiumTtsCommand('episode/42', 'force-tts-once');
  assert.equal(command.path, '/admin/podcast-premium-guides/episode%2F42/force');
  assert.deepEqual(command.body, {
    reason: PODCAST_FORCE_TTS_REASON,
    idempotency_key: 'force-tts-once',
  });
});

test('forced podcast TTS command creates a fresh idempotency key', () => {
  const first = podcastPremiumTtsCommand('episode-42');
  const second = podcastPremiumTtsCommand('episode-42');
  assert.match(first.body.idempotency_key, /^podcast-tts-.{8,}$/);
  assert.match(second.body.idempotency_key, /^podcast-tts-.{8,}$/);
  assert.notEqual(first.body.idempotency_key, second.body.idempotency_key);
});
