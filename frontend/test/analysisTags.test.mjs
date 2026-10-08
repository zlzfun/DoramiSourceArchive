import { test } from 'node:test';
import assert from 'node:assert/strict';
import { analysisTagSearch, displayAnalysisTags } from '../src/utils/analysis.js';

test('mixed snapshots group canonical tags first without mutating their order or input', () => {
  const primary = { id: 1, name_zh: 'AI智能体', is_primary: true };
  const entity = { type: 'canonical', id: 2, name_en: 'OpenAI', relevance: 0.1 };
  const free = { type: 'extracted', label: '工具编排', score: 0.99 };
  const snapshot = Object.freeze([free, primary, entity]);
  assert.deepEqual(displayAnalysisTags({ display_tags: snapshot }), [primary, entity, free]);
  assert.deepEqual(snapshot, [free, primary, entity]);
  assert.deepEqual(displayAnalysisTags({ tags: [primary, entity] }), [primary, entity]);
  assert.deepEqual(displayAnalysisTags({ display_tags: [], tags: [primary] }), []);
  assert.deepEqual(displayAnalysisTags(null), []);
});

test('the six-tag limit is applied after canonical priority', () => {
  const free = Array.from({ length: 6 }, (_, i) => ({ type: 'extracted', label: `自由 ${i}` }));
  const canonical = { type: 'canonical', id: 8, name_zh: '正式' };
  assert.deepEqual(displayAnalysisTags({ display_tags: [...free, canonical] }), [canonical, ...free.slice(0, 5)]);
});

test('canonical retrieval uses identity, including legacy tags without a type', () => {
  assert.deepEqual(analysisTagSearch({ type: 'canonical', id: 3, name_zh: 'OpenAI', relevance: 0.07 }), {
    label: 'OpenAI', filters: { display_tag_id: '3' },
  });
  assert.deepEqual(analysisTagSearch({ id: '4', name_en: 'Agents' }), {
    label: 'Agents', filters: { display_tag_id: '4' },
  });
  // A missing canonical identity must never silently turn into a free-text query.
  for (const id of [undefined, null, '', 0, -1, 'bad']) {
    assert.equal(analysisTagSearch({ id, name_zh: 'OpenAI' }), null);
  }
});

test('flexible labels use the existing display-tag filter, including punctuation', () => {
  const query = analysisTagSearch({ type: 'extracted', label: ' A/B 案例 & C++ ', candidate_id: 3 });
  assert.deepEqual(query, { label: 'A/B 案例 & C++', filters: { display_tag: 'A/B 案例 & C++' } });
  assert.equal(new URLSearchParams(query.filters).get('display_tag'), 'A/B 案例 & C++');
  assert.equal(analysisTagSearch({ type: 'extracted', label: ' ' }), null);
});
