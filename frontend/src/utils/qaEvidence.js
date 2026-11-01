const BASIS_LABELS = {
  show_notes: '节目简介',
  publisher_transcript: '来源逐字稿',
  normalized_transcript: '语音识别逐字稿',
  transcript_zh: '中文逐字稿',
  digest_blog_zh: '中文导读',
};

/** Only show evidence actually returned for this answer; never synthesize transcript text. */
export function qaEvidence(source) {
  if (!Array.isArray(source?.evidence)) return [];
  return source.evidence
    .filter((item) => typeof item?.text === 'string' && item.text.trim())
    .map((item) => ({
      ...item,
      label: BASIS_LABELS[item.kind] || (typeof source.basis_label === 'string' && source.basis_label.trim()) || '参考原文',
    }));
}

export function qaBasisLabel(source) {
  if (typeof source?.basis_label === 'string' && source.basis_label.trim()) return source.basis_label;
  return [...new Set(qaEvidence(source).map((item) => item.label))].join('、');
}
