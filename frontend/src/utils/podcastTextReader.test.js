import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  PODCAST_TEXT_LABELS,
  isPodcastTextRequestCurrent,
  mergePodcastTextPage,
  podcastTextPageAction,
  podcastTranscriptForLanguage,
  podcastTextView,
} from './podcastTextReader.js';

test('podcast text view prioritizes Chinese digest and Chinese transcript', () => {
  const view = podcastTextView({ items: [
    { kind: 'publisher_transcript', text: 'source' },
    { kind: 'transcript_zh', text: '中文逐字稿' },
    { kind: 'digest_blog_zh', text: '中文精华' },
  ] });
  assert.equal(view.digest.text, '中文精华');
  assert.equal(view.transcripts[0].item.text, '中文逐字稿');
  assert.equal(view.transcripts[0].label.note, 'AI 翻译整理');
});

test('podcast text view falls back to source transcript and has a true empty state', () => {
  const fallback = podcastTextView({ items: [
    { kind: 'publisher_transcript', text: 'publisher text' },
  ] });
  assert.equal(fallback.transcripts[0].item.text, 'publisher text');
  assert.equal(fallback.transcripts[0].label.title, '节目方逐字稿');
  assert.equal(fallback.transcripts[0].label.note, '节目方提供');
  assert.deepEqual(podcastTextView({ items: [] }).transcripts, []);
  assert.equal(PODCAST_TEXT_LABELS.digest_blog_zh.title, '精品导读');
});

test('normalized ASR transcript is visible and remains distinct from publisher text', () => {
  const view = podcastTextView({ items: [
    { kind: 'normalized_transcript', text: '识别内容' },
    { kind: 'publisher_transcript', text: '节目方内容' },
  ] });
  assert.deepEqual(
    view.transcripts.map(({ item, label }) => [item.kind, label.title, label.note]),
    [
      ['publisher_transcript', '节目方逐字稿', '节目方提供'],
      ['normalized_transcript', 'ASR 逐字稿', '语音识别稿'],
    ],
  );
  assert.equal(view.transcripts[0].item.kind, 'publisher_transcript');
});

test('language mode keeps source transcript original and switches to cached Chinese transcript', () => {
  const view = podcastTextView({ items: [
    { kind: 'normalized_transcript', text: 'asr source' },
    { kind: 'publisher_transcript', text: 'publisher source' },
    { kind: 'transcript_zh', text: '中文逐字稿' },
  ] });
  const original = podcastTranscriptForLanguage({
    view,
    preferredKind: 'publisher_transcript',
    selectedKind: 'normalized_transcript',
  });
  assert.equal(original.transcript.item.kind, 'normalized_transcript');
  assert.deepEqual(
    original.sourceTranscripts.map(({ item }) => item.kind),
    ['publisher_transcript', 'normalized_transcript'],
  );

  const chinese = podcastTranscriptForLanguage({
    view,
    translated: true,
    preferredKind: 'publisher_transcript',
  });
  assert.equal(chinese.transcript.item.kind, 'transcript_zh');
  assert.equal(chinese.source.item.kind, 'publisher_transcript');
});

test('translated mode exposes its source while Chinese transcript is not cached yet', () => {
  const view = podcastTextView({ items: [
    { kind: 'publisher_transcript', text: 'publisher source' },
  ] });
  const result = podcastTranscriptForLanguage({
    view,
    translated: true,
    preferredKind: 'publisher_transcript',
  });
  assert.equal(result.chinese, null);
  assert.equal(result.source.item.kind, 'publisher_transcript');
  assert.equal(result.transcript.item.kind, 'publisher_transcript');
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
  assert.match(component, /aria-expanded=\{transcriptOpen\}/);
  assert.match(component, /aria-label="逐字稿来源"/);
  assert.match(component, /tabIndex=\{0\}/);
  assert.match(component, /refreshFirstPage\(item\.kind, group\)/);
  assert.match(component, /isPodcastTextRequestCurrent\(group, requestGroup\.current, episodeId\)/);
  assert.match(component, /translatePodcastTranscript\(episodeId, translationSourceKind/);
  assert.match(component, /正在翻译逐字稿，完成后会自动显示/);
  assert.match(component, /重新翻译/);
});

test('publisher transcript follows show notes on desktop and mobile reader surfaces', async () => {
  const desktop = await readFile(
    new URL('../components/ReaderTab.jsx', import.meta.url),
    'utf8',
  );
  const mobile = await readFile(
    new URL('../components/mobile/MobileArticlePage.jsx', import.meta.url),
    'utf8',
  );
  const experience = await readFile(
    new URL('../components/PodcastExperiencePanel.jsx', import.meta.url),
    'utf8',
  );

  for (const reader of [desktop, mobile]) {
    const introduction = reader.indexOf('<h2 className="section-title">节目简介</h2>');
    const translationScope = reader.indexOf('data-ai-translation-scope="article-body"');
    const transcriptScope = reader.indexOf('data-ai-translation-excluded="true"');
    const transcript = reader.indexOf('preferredTranscriptKind="publisher_transcript"');
    const origin = reader.indexOf('className="reader-pane-origin"', transcript);
    assert.ok(introduction >= 0);
    assert.ok(translationScope > introduction);
    assert.ok(transcriptScope > translationScope);
    assert.ok(transcript > introduction);
    assert.ok(transcript > transcriptScope);
    assert.ok(origin > transcript);
    assert.match(reader, /showTranslation=\{showTranslation\}/);
  }
  assert.match(experience, /hiddenTranscriptKinds=\{\['publisher_transcript'\]\}/);
});

test('podcast lists reuse taxonomy tag styling and hide pipeline status', async () => {
  const reader = await readFile(
    new URL('../components/ReaderTab.jsx', import.meta.url),
    'utf8',
  );
  const podcastBranch = reader.slice(
    reader.indexOf('{entryPodcast ? ('),
    reader.indexOf(') : (', reader.indexOf('{entryPodcast ? (')),
  );

  assert.match(podcastBranch, /\{analysisTag\}/);
  assert.match(reader, /const analysisTag =[\s\S]*className="reader-entry-tag"/);
  assert.match(reader, /podcastListAvailabilityMeta/);
  assert.doesNotMatch(podcastBranch, /podcastFullProcessingMeta|analysisStatus\.label/);
  assert.equal(reader.match(/className="reader-entry-tag">\{analysisLabel\}/g)?.length, 1);
});

test('blog-only guide without condensed audio is visible in experience panel and reader surfaces', async () => {
  const desktop = await readFile(
    new URL('../components/ReaderTab.jsx', import.meta.url),
    'utf8',
  );
  const mobile = await readFile(
    new URL('../components/mobile/MobileArticlePage.jsx', import.meta.url),
    'utf8',
  );
  const experience = await readFile(
    new URL('../components/PodcastExperiencePanel.jsx', import.meta.url),
    'utf8',
  );

  for (const reader of [desktop, mobile]) {
    assert.match(reader, /hasGuideBlog = Boolean\(activePodcast\?\.premium_guide\?\.blog_ready/);
    assert.match(reader, /isBlogOnlyGuide = hasGuideBlog && !hasGuideAudio/);
    assert.match(reader, /podcastGuideActive = podcastView\s*&&\s*\(isBlogOnlyGuide \|\| \(podcastVariant === 'digest' && hasGuideAudio\)\)/);
  }
  assert.match(experience, /isBlogOnlyGuide = hasGuideBlog && !hasGuideAudio/);
  assert.match(experience, /guideVisible = isBlogOnlyGuide \|\| \(variant === 'digest' && hasGuideAudio\)/);
});

