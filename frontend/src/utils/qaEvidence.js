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

/** Keep source numbers stable; uncited retrieved material is a reference, not proof. */
export function qaReferenceGroups(answer, sources = []) {
  const rows = sources.map((source, index) => ({ source, num: index + 1 }));
  const cited = new Set([...String(answer || '').matchAll(/\[(\d{1,2})\](?!\()/g)]
    .map((match) => Number(match[1])).filter((n) => n >= 1 && n <= rows.length));
  return cited.size
    ? { primary: rows.filter(({ num }) => cited.has(num)), more: rows.filter(({ num }) => !cited.has(num)), cited: true }
    : { primary: rows, more: [], cited: false };
}

/** A fallback has no usable model answer to carry into the next question. */
export function qaHistory(turns) {
  return turns.filter((turn) => turn.a && !turn.error && !turn.pending && turn.answerMode !== 'references_only')
    .flatMap((turn) => [{ role: 'user', content: turn.q }, { role: 'assistant', content: turn.a }]);
}

/** Preview only; the original text remains available without another request. */
export function qaExcerptPreview(text, limit = 800) {
  if (text.length <= limit) return { text, truncated: false };
  const end = text.lastIndexOf('\n', limit);
  return { text: text.slice(0, end >= limit / 2 ? end : limit), truncated: true };
}
