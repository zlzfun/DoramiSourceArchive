import { canFilterCustomInterests } from './readerInterestQuery.js';

// Reader presentation only. Positive matches come from the server's owner-scoped
// projection; neither refs, names nor snippets can manufacture a semantic match.
const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value) => typeof value === 'string' ? value.trim() : '';
const METHODS = new Set(['semantic', 'deterministic_title', 'public_assignment', 'legacy_unknown']);
const SOURCE_KINDS = new Set(['article_body', 'podcast_transcript', 'title']);

function contributionOf(raw, ref, item, basis) {
  if (basis !== 'edition_snapshot' || !record(raw) || raw.version !== 1
    || raw.selected_lane !== item.selection_lane
    || !['interest_union', 'none'].includes(raw.admission)
    || !['interest_lane', 'none'].includes(raw.ranking)
    || !Array.isArray(raw.contributor_refs)
    || !raw.contributor_refs.some((r) => record(r) && r.kind === ref.kind && r.id === ref.id)
    || (raw.ranking === 'interest_lane' && item.selection_lane !== 'interest')) return null;
  return { admission: raw.admission, ranking: raw.ranking, lane: raw.selected_lane };
}

function evidenceOf(raw, method) {
  const evidence = record(raw) ? raw : {};
  // A redaction status never coexists with reader-visible quote text or reason.
  if (evidence.redaction) return { reason: '', quotes: [], redacted: true };
  const quotes = [];
  const seen = new Set();
  for (const quote of Array.isArray(evidence.quotes) ? evidence.quotes : []) {
    if (!record(quote) || !['current', 'historical'].includes(quote.availability)
      || !SOURCE_KINDS.has(quote.source_kind) || !text(quote.text)) continue;
    if (method === 'deterministic_title' && quote.source_kind !== 'title') continue;
    if (method !== 'semantic' && method !== 'deterministic_title') continue;
    const key = `${quote.source_kind}:${quote.availability}:${quote.text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    // Deliberately do not copy anchors, URLs, policy or raw verifier diagnostics.
    quotes.push({ text: quote.text, sourceKind: quote.source_kind, availability: quote.availability });
  }
  return { reason: text(evidence.reason), quotes, redacted: false };
}

/** Typed arrays are authoritative, including empty/malformed arrays. A brief
 * requires edition_snapshot; a current projection cannot explain an old edition. */
export function normalizeInterestMatches(item, { basis = 'edition_snapshot' } = {}) {
  if (!record(item)) return [];
  const matches = [];
  const seen = new Set();
  if (Object.hasOwn(item, 'interest_matches')) {
    for (const raw of Array.isArray(item.interest_matches) ? item.interest_matches : []) {
      if (!record(raw) || !record(raw.ref) || !['tag', 'custom'].includes(raw.ref.kind)
        || !text(raw.ref.id) || raw.outcome !== 'core' || raw.basis !== basis) continue;
      if (raw.ref.kind === 'custom' && (!Number.isInteger(raw.revision) || raw.revision < 1)) continue;
      const key = `${raw.ref.kind}:${raw.ref.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const method = METHODS.has(raw.method) ? raw.method : 'legacy_unknown';
      matches.push({
        key, ref: { kind: raw.ref.kind, id: raw.ref.id },
        name: text(raw.name) || (raw.ref.kind === 'custom' ? '自定义兴趣' : '兴趣标签'),
        revision: raw.revision ?? null, basis, method,
        contribution: contributionOf(raw.selection_contribution, raw.ref, item, basis),
        selected: basis === 'edition_snapshot' && item.selection_lane === 'interest'
          && raw.selection_role === 'selected_by_interest',
        evidence: evidenceOf(raw.evidence, method),
        drilldown: {
          available: raw.drilldown?.available === true,
          reason: ['updated', 'paused', 'deleted', 'unavailable'].includes(raw.drilldown?.reason)
            ? raw.drilldown.reason : null,
        },
      });
    }
    return matches;
  }
  // Legacy public codes only. Never infer a custom name from custom refs or the
  // free-text selection reason, and never borrow the current interest catalog.
  if (basis !== 'edition_snapshot') return [];
  const snapshot = record(item.snapshot) ? item.snapshot : {};
  const tags = [...(Array.isArray(snapshot.tags) ? snapshot.tags : []),
    ...(Array.isArray(snapshot.display_tags) ? snapshot.display_tags : [])];
  for (const code of Array.isArray(item.matched_interest_codes) ? item.matched_interest_codes : []) {
    if (!text(code) || seen.has(`tag:${code}`)) continue;
    seen.add(`tag:${code}`);
    const tag = tags.find((row) => record(row) && row.code === code);
    matches.push({
      key: `tag:${code}`, ref: { kind: 'tag', id: code },
      name: text(tag?.name_zh) || text(tag?.name_en) || text(tag?.label) || code,
      revision: null, basis, method: 'public_assignment',
      selected: item.selection_lane === 'interest',
      evidence: { reason: '', quotes: [], redacted: false },
      drilldown: { available: false, reason: null },
    });
  }
  return matches;
}

export function interestMatchLabel(matches) {
  if (!matches.length) return '';
  return matches.length === 1 ? `兴趣 · ${matches[0].name}` : `兴趣 · ${matches.length}`;
}

export function interestSelectionLabel(item, matches) {
  if (['read', 'listen'].includes(item?.selection_lane)
    && matches.some((match) => match.contribution?.admission === 'interest_union')) {
    return '因兴趣进入候选，按内容价值入选';
  }
  if (item?.selection_lane !== 'interest') return '';
  const selected = matches.filter((match) => match.selected);
  return selected.length === 1 ? `因「${selected[0].name}」入选` : '兴趣入选';
}

export function interestEvidenceLabel(match) {
  if (match.evidence.redacted) return '依据暂不可用';
  if (match.method === 'deterministic_title') return '仅标题符合条件';
  if (match.method === 'public_assignment') return '标签匹配';
  if (match.method === 'semantic' && match.evidence.quotes.some((quote) => quote.sourceKind !== 'title')) return '正文依据';
  return '正文依据暂不可用';
}

export function interestDrilldownAvailable(match, capabilities, onOpenInterest) {
  return typeof onOpenInterest === 'function' && match.drilldown.available
    && capabilities?.interest_match_projection === 1
    && (match.ref.kind !== 'custom' || canFilterCustomInterests(capabilities));
}
