import test from 'node:test';
import assert from 'node:assert/strict';

import {
  podcastPlaybackKey,
  readPodcastPosition,
  resumablePodcastPosition,
  writePodcastPosition,
} from './podcastPlayback.js';

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test('podcast playback keys isolate episode and audio variant', () => {
  assert.notEqual(podcastPlaybackKey('episode/1', 'original'), podcastPlaybackKey('episode/1', 'digest'));
  assert.notEqual(podcastPlaybackKey('episode/1', 'original'), podcastPlaybackKey('episode/2', 'original'));
  assert.match(podcastPlaybackKey('episode/1', 'original'), /episode%2F1:original$/);
});

test('podcast playback progress round-trips and rejects invalid positions', () => {
  const storage = memoryStorage();
  assert.equal(readPodcastPosition('episode-1', 'original', storage), 0);
  assert.equal(writePodcastPosition('episode-1', 'original', 42.5, storage), true);
  assert.equal(readPodcastPosition('episode-1', 'original', storage), 42.5);
  assert.equal(readPodcastPosition('episode-1', 'digest', storage), 0);
  assert.equal(writePodcastPosition('episode-1', 'digest', -1, storage), false);
});

test('podcast playback storage failures are non-fatal', () => {
  const brokenStorage = {
    getItem: () => { throw new Error('blocked'); },
    setItem: () => { throw new Error('blocked'); },
  };
  assert.equal(readPodcastPosition('episode-1', 'original', brokenStorage), 0);
  assert.equal(writePodcastPosition('episode-1', 'original', 12, brokenStorage), false);
  assert.equal(resumablePodcastPosition(90, 60), 60);
  assert.equal(resumablePodcastPosition('bad', 60), 0);
});
