import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeInterestMatches, interestMatchLabel, interestSelectionLabel,
  interestEvidenceLabel, interestDrilldownAvailable,
} from '../src/utils/interestMatches.js';

const quote = (patch = {}) => ({ text: '工具必须保留用户上下文。', source_kind: 'article_body', availability: 'historical', ...patch });
const typed = (patch = {}) => ({
  ref: { kind: 'custom', id: '9' }, name: '个人 Agent', revision: 2,
  basis: 'edition_snapshot', outcome: 'core', method: 'semantic',
  selection_role: 'selected_by_interest', evidence: { reason: '讨论跨任务保留上下文。', quotes: [quote()] },
  drilldown: { available: true, reason: 'updated' }, ...patch,
});
const normalize = (rows, patch = {}) => normalizeInterestMatches({ selection_lane: 'interest', interest_matches: rows, ...patch });

test('versioned value-lane admission is distinct from interest ranking and legacy also-match', () => {
  const contribution = { version: 1, admission: 'interest_union', ranking: 'none', selected_lane: 'listen',
    contributor_refs: [{ kind: 'custom', id: '9' }, { kind: 'custom', id: '10' }] };
  const matches = normalize([typed({ selection_role: 'also_matches', selection_contribution: contribution }),
    typed({ ref: { kind: 'custom', id: '10' }, selection_role: 'also_matches', selection_contribution: contribution })],
  { selection_lane: 'listen' });
  assert.equal(matches.length, 2);
  assert.ok(matches.every((match) => !match.selected));
  assert.equal(interestSelectionLabel({ selection_lane: 'listen' }, matches), '因兴趣进入候选，按内容价值入选');
  for (const patch of [{ version: true }, { version: 2 }, { selected_lane: 'interest' },
    { ranking: 'interest_lane' }, { contributor_refs: [] }, { admission: 'invented' }]) {
    const rows = normalize([typed({ selection_contribution: { ...contribution, ...patch } })], { selection_lane: 'listen' });
    assert.equal(interestSelectionLabel({ selection_lane: 'listen' }, rows), '');
  }
  const subscribed = normalize([typed({ selection_contribution: { ...contribution, admission: 'none' } })], { selection_lane: 'listen' });
  assert.equal(interestSelectionLabel({ selection_lane: 'listen' }, subscribed), '');
});

test('typed positive projection retains all distinct typed identities, even equal names', () => {
  const rows = normalize([typed(), typed({ ref: { kind: 'custom', id: '10' } }),
    typed({ ref: { kind: 'tag', id: '9' }, method: 'public_assignment' }), typed()]);
  assert.deepEqual(rows.map((row) => row.key), ['custom:9', 'custom:10', 'tag:9']);
  assert.equal(interestMatchLabel(rows), '兴趣 · 3');
  assert.equal(interestSelectionLabel({ selection_lane: 'interest' }, rows), '兴趣入选');
});

test('typed array presence is authoritative and never falls back to legacy positive fields', () => {
  for (const interest_matches of [[], null, {}, [typed({ outcome: 'mention' })]]) {
    assert.deepEqual(normalizeInterestMatches({ interest_matches, matched_interest_codes: ['agent'],
      matched_interest_refs: [{ kind: 'custom', id: '11' }] }), []);
  }
});

test('unknown, mention, no_match, malformed refs, invalid revisions and current basis are not frozen cores', () => {
  assert.deepEqual(normalize(['unknown', 'mention', 'no_match'].map((outcome) => typed({ outcome }))), []);
  assert.deepEqual(normalize([null, typed({ ref: { kind: 'custom', id: 9 } }),
    typed({ ref: { kind: 'invented', id: '9' } }), typed({ revision: 0 }), typed({ revision: '2' }),
    typed({ basis: 'current' })]), []);
  assert.equal(normalizeInterestMatches({ interest_matches: [typed({ basis: 'current' })] }, { basis: 'current' }).length, 1);
});

test('legacy fallback reads all frozen public names and never infers custom name from refs or prose', () => {
  const rows = normalizeInterestMatches({ selection_lane: 'interest', matched_interest_codes: ['agent', 'memory', 'agent'],
    matched_interest_refs: [{ kind: 'custom', id: '9' }], selection_reason: '因「私人秘密」入选',
    snapshot: { tags: [{ code: 'agent', name_zh: '智能体' }], display_tags: [{ code: 'memory', name_en: 'Memory' }] } });
  assert.deepEqual(rows.map((row) => [row.ref.kind, row.name]), [['tag', '智能体'], ['tag', 'Memory']]);
  assert.ok(rows.every((row) => row.evidence.quotes.length === 0));
  assert.deepEqual(normalizeInterestMatches({ matched_interest_refs: [{ kind: 'custom', id: '9' }],
    selection_reason: '因「私人秘密」入选' }), []);
});

test('frozen name does not depend on current catalog and missing frozen name is neutral', () => {
  const [row] = normalize([typed()], { custom_items: [{ id: 9, name: '新名字', revision: 3 }] });
  assert.equal(row.name, '个人 Agent');
  assert.equal(interestSelectionLabel({ selection_lane: 'interest' }, [row]), '因「个人 Agent」入选');
  assert.equal(normalize([typed({ name: null })])[0].name, '自定义兴趣');
});

test('quality, breaking and content lanes never inherit causal selection from incidental matches', () => {
  for (const selection_lane of ['quality', 'breaking', 'read', 'listen']) {
    const rows = normalize([typed()], { selection_lane });
    assert.equal(rows[0].selected, false);
    assert.equal(interestSelectionLabel({ selection_lane }, rows), '');
  }
  const rows = normalize([typed({ selection_role: 'also_matches' })]);
  assert.equal(interestSelectionLabel({ selection_lane: 'interest' }, rows), '兴趣入选');
});

test('redaction discards quote text and free-text reason, including malicious available data', () => {
  const [row] = normalize([typed({ evidence: { redaction: 'evidence_unavailable', reason: 'WITHDRAWN_SECRET',
    quotes: [quote({ text: 'WITHDRAWN_SECRET', availability: 'current' })] } })]);
  assert.equal(JSON.stringify(row).includes('WITHDRAWN_SECRET'), false);
  assert.equal(interestEvidenceLabel(row), '依据暂不可用');
});

test('only allowlisted literal quote projection is copied; withdrawn and arbitrary raw strings disappear', () => {
  const [row] = normalize([typed({ evidence: { quotes: [quote(), quote(), 'RAW_SECRET',
    quote({ text: 'WITHDRAWN_SECRET', availability: 'unavailable' }),
    quote({ text: 'UNKNOWN_SOURCE', source_kind: 'summary' }),
    quote({ text: 'live text', availability: 'current', anchor: 'javascript:bad', policy: 'SECRET' })], policy: 'SECRET' } })]);
  assert.deepEqual(row.evidence.quotes.map((q) => q.text), ['工具必须保留用户上下文。', 'live text']);
  for (const secret of ['RAW_SECRET', 'WITHDRAWN_SECRET', 'UNKNOWN_SOURCE', 'javascript:', 'SECRET']) {
    assert.equal(JSON.stringify(row).includes(secret), false);
  }
});

test('title-only and absent-body evidence cannot be labelled as body-backed proof', () => {
  const [title] = normalize([typed({ method: 'deterministic_title', evidence: { quotes: [quote(), quote({ source_kind: 'title' })] } })]);
  assert.equal(interestEvidenceLabel(title), '仅标题符合条件');
  assert.equal(title.evidence.quotes.length, 1);
  assert.equal(title.evidence.quotes[0].sourceKind, 'title');
  assert.equal(interestEvidenceLabel(normalize([typed({ evidence: {} })])[0]), '正文依据暂不可用');
  assert.equal(interestEvidenceLabel(normalize([typed({ method: 'new_unknown_method' })])[0]), '正文依据暂不可用');
  assert.equal(interestEvidenceLabel(normalize([typed({ evidence: { quotes: [quote({ source_kind: 'podcast_transcript' })] } })])[0]), '正文依据');
});

test('drilldown requires explicit capability, live availability and a real typed navigator', () => {
  const [row] = normalize([typed()]);
  const caps = { interest_match_projection: 1, custom_interest_filter: true, match_contract: 1 };
  assert.equal(interestDrilldownAvailable(row, null, () => {}), false);
  assert.equal(interestDrilldownAvailable(row, { interest_match_projection: 1 }, () => {}), false);
  assert.equal(interestDrilldownAvailable(row, caps, null), false);
  assert.equal(interestDrilldownAvailable({ ...row, drilldown: { available: false } }, caps, () => {}), false);
  assert.equal(interestDrilldownAvailable(row, caps, () => {}), true);
});
