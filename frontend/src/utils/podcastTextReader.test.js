import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  PODCAST_TEXT_LABELS,
  isPodcastTextRequestCurrent,
  mergePodcastTextPage,
  podcastTextPageAction,
  podcastTextView,
} from './podcastTextReader.js';

test('podcast text view prioritizes Chinese digest and Chinese transcript', () => {
  const view = podcastTextView({ items: [
    { kind: 'publisher_transcript', text: 'source' },
    { kind: 'transcript_zh', text: '中文逐字稿' },
    { kind: 'digest_blog_zh', text: '中文精华' },
  ] });
  assert.equal(view.digest.text, '中文精华');
  assert.equal(view.transcript.text, '中文逐字稿');
  assert.equal(view.transcriptLabel.note, 'AI 整理');
  assert.equal(view.hasText, true);
});

test('podcast text view falls back to source transcript and has a true empty state', () => {
  const fallback = podcastTextView({ items: [
    { kind: 'publisher_transcript', text: 'publisher text' },
  ] });
  assert.equal(fallback.transcript.text, 'publisher text');
  assert.equal(fallback.transcriptLabel.note, '来源方提供');
  assert.equal(podcastTextView({ items: [] }).hasText, false);
  assert.equal(PODCAST_TEXT_LABELS.digest_blog_zh.title, '中文精华');
});

test('digest and transcript pages append once and reject stale or duplicate pages', () => {
  const first = {
    artifact_id: 'a1', kind: 'digest_blog_zh', text: '第一段',
    range_start: 0, range_end: 3, next_cursor: 'next-1',
  };
  const second = {
    artifact_id: 'a1', kind: 'digest_blog_zh', text: '第二段',
    range_start: 3, range_end: 6, next_cursor: null,
  };
  assert.equal(mergePodcastTextPage(first, second).text, '第一段第二段');
  assert.equal(mergePodcastTextPage(first, { ...second, range_start: 0 }).text, '第一段');
  assert.equal(mergePodcastTextPage(first, { ...second, artifact_id: 'a2' }).text, '第一段');

  const transcript = { ...first, kind: 'transcript_zh' };
  assert.equal(
    mergePodcastTextPage(transcript, { ...second, kind: 'transcript_zh' }).text,
    '第一段第二段',
  );
});

test('stale cursors and unexpected empty pages refresh the first page', () => {
  const current = {
    artifact_id: 'a1', kind: 'transcript_zh', text: '第一页',
    range_start: 0, range_end: 3, next_cursor: 'signed-cursor',
  };
  const next = {
    ...current, text: '第二页', range_start: 3, range_end: 6, next_cursor: null,
  };
  assert.equal(podcastTextPageAction(current, next), 'merge');
  assert.equal(podcastTextPageAction(current, null), 'refresh');
  assert.equal(podcastTextPageAction(current, null, { status: 400 }), 'refresh');
  assert.equal(podcastTextPageAction(current, null, { name: 'AbortError' }), 'error');
  assert.equal(podcastTextPageAction(current, { ...next, artifact_id: 'a2' }), 'ignore');
});

test('request groups reject late responses after switching episodes', () => {
  const episodeA = { episodeId: 'episode-a' };
  const episodeB = { episodeId: 'episode-b' };
  assert.equal(isPodcastTextRequestCurrent(episodeA, episodeA, 'episode-a'), true);
  assert.equal(isPodcastTextRequestCurrent(episodeA, episodeB, 'episode-a'), false);
  assert.equal(isPodcastTextRequestCurrent(episodeA, episodeA, 'episode-b'), false);
});

test('podcast text surfaces use semantic dark-theme tokens', async () => {
  const css = await readFile(new URL('../index.css', import.meta.url), 'utf8');
  const component = await readFile(
    new URL('../components/PodcastTextPanel.jsx', import.meta.url),
    'utf8',
  );
  assert.match(css, /\[data-theme="dark"\] \.podcast-text-digest/);
  assert.match(css, /\.podcast-text-panel[\s\S]*color: var\(--dorami-ink\)/);
  assert.match(css, /\.podcast-text-loading[\s\S]*color: var\(--dorami-muted\)/);
  assert.doesNotMatch(css, /\.podcast-text-(?:digest|transcript)[^{]*\{[^}]*color:\s*#000/);
  assert.match(component, /podcast-text-initial-error" role="alert"/);
  assert.match(component, /refreshFirstPage\(item\.kind, group\)/);
  assert.match(component, /isPodcastTextRequestCurrent\(group, requestGroup\.current, episodeId\)/);
});
