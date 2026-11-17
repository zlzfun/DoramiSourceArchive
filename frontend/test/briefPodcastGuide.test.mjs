import { test } from 'node:test';
import assert from 'node:assert/strict';
import { briefGuideAction, briefGuideActive, isBriefPodcast } from '../src/utils/briefPodcastGuide.js';

test('podcasts are recognized outside the listen lane and in historical source-only snapshots', () => {
  assert.ok(isBriefPodcast({ article_id: 'p', selection_lane: 'quality', snapshot: { content_type: 'podcast_episode' } }));
  assert.ok(isBriefPodcast({ article_id: 'p', snapshot: {} }, { shape: 'podcast' }));
  assert.ok(isBriefPodcast({ article_id: 'p', selection_lane: 'listen' }));
  assert.equal(isBriefPodcast({ article_id: 'a', snapshot: { content_type: 'article' } }), false);
  assert.equal(isBriefPodcast({ article_id: null, selection_lane: 'listen' }), false);
});

test('text and ready flags cannot substitute for published playable audio', () => {
  for (const status of ['not_started', 'failed', 'ready']) {
    for (const url of ['', 'javascript:alert(1)', '//untrusted.invalid/audio']) {
      const article = { podcast: { premium_guide: { status, blog_ready: true, audio_ready: true }, condensed_audio_url: url } };
      assert.equal(briefGuideAction(article).kind, 'request');
    }
  }
  const article = { podcast: { condensed_audio_url: '/api/reader/podcast-artifacts/a/audio', premium_guide: { status: 'failed' } } };
  assert.equal(briefGuideAction(article).kind, 'play');
  assert.equal(briefGuideActive(article), false);
});

test('queued/retry states and accepted POST prevent a second generation request', () => {
  for (const status of ['queued', 'summarizing', 'synthesizing', 'retry_wait']) {
    const article = { podcast: { premium_guide: { status } } };
    assert.equal(briefGuideAction(article).kind, 'busy');
    assert.equal(briefGuideActive(article), true);
  }
  assert.equal(briefGuideAction({ podcast: {} }, { accepted: true }).kind, 'busy');
  assert.equal(briefGuideAction({ podcast: {} }, { submitting: true }).kind, 'busy');
});

test('unreadable state offers read-only recovery, never a speculative paid request', () => {
  assert.equal(briefGuideAction(null).kind, 'busy');
  assert.equal(briefGuideAction(null, { error: '404' }).kind, 'refresh');
  assert.equal(briefGuideAction({ podcast: { condensed_audio_url: '/old.wav' } }, { error: 'offline' }).kind, 'refresh');
  assert.equal(briefGuideAction({ id: 'no-longer-a-podcast' }).kind, 'unavailable');
});
