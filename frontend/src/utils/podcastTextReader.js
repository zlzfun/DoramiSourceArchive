export const PODCAST_TEXT_LABELS = Object.freeze({
  digest_blog_zh: { title: '中文精华', note: 'AI 整理' },
  transcript_zh: { title: '中文逐字稿', note: 'AI 翻译整理' },
  publisher_transcript: { title: '节目方逐字稿', note: '节目方提供' },
  normalized_transcript: { title: 'ASR 逐字稿', note: '语音识别稿' },
});

const TRANSCRIPT_PRIORITY = Object.freeze([
  'transcript_zh',
  'publisher_transcript',
  'normalized_transcript',
]);

export function podcastTextView(response) {
  const items = Array.isArray(response?.items) ? response.items : [];
  const byKind = Object.fromEntries(items.map((item) => [item.kind, item]));
  const transcripts = TRANSCRIPT_PRIORITY
    .filter((kind) => byKind[kind])
    .map((kind) => ({
      item: byKind[kind],
      label: PODCAST_TEXT_LABELS[kind],
    }));
  return {
    digest: byKind.digest_blog_zh || null,
    transcripts,
  };
}

export function mergePodcastTextPage(current, next) {
  if (!current) return next || null;
  if (!next) return current;
  if (
    current.artifact_id !== next.artifact_id
    || current.kind !== next.kind
    || next.range_start !== current.range_end
  ) return current;
  return {
    ...next,
    range_start: current.range_start,
    text: `${current.text}${next.text}`,
  };
}

export function podcastTextPageAction(current, next, error = null) {
  // A signed cursor becomes stale after republish. An unexpected empty page is
  // treated the same way so the panel cannot remain stranded behind old state.
  if (error) return error.status === 400 ? 'refresh' : 'error';
  if (!next) return 'refresh';
  if (
    !current
    || current.artifact_id !== next.artifact_id
    || current.kind !== next.kind
    || next.range_start !== current.range_end
  ) return 'ignore';
  return 'merge';
}

export function isPodcastTextRequestCurrent(group, currentGroup, episodeId) {
  return Boolean(
    group
    && group === currentGroup
    && group.episodeId === episodeId,
  );
}
