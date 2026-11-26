import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithOxc } from 'vite';
import { normalizeInterestMatches, interestSelectionLabel } from '../src/utils/interestMatches.js';
import { VALUE_LANES } from '../src/utils/briefStats.js';

// Compile the actual brief file and exercise its pure presentation boundary.
const source = readFileSync(new URL('../src/components/PersonalBriefPage.jsx', import.meta.url), 'utf8');
const stripped = source.replace(/^import[\s\S]*?from ['"][^'"]+['"];\n/gm, '');
const { code } = await transformWithOxc(stripped, 'PersonalBriefPage.jsx', { jsx: { runtime: 'classic' } });
const bindings = { React, VALUE_LANES, normalizeInterestMatches, interestSelectionLabel,
  scoreDimensionOf: () => 'news', scoreOf: () => null, qualityScoreText: () => '', scoreTierClass: () => '',
  SCORE_DIMENSION_LABELS: { news: '新闻价值' }, titleView: (snapshot) => ({ title: snapshot.title, orig: '' }),
  resolveCompany: () => null, Zap: 'i' };
const { viewOf, whyOf, WhyMark, BriefInterestMark } = new Function(...Object.keys(bindings),
  code.replace('export default ', '').replaceAll('export ', '') + '\nreturn {viewOf, whyOf, WhyMark, BriefInterestMark};')(...Object.values(bindings));
const item = { selection_lane: 'interest', selection_reason: '冻结时选篇理由', snapshot: { title: '一篇文章' },
  interest_matches: [{ ref: { kind: 'custom', id: '7' }, revision: 2, name: '冻结兴趣名称', basis: 'edition_snapshot',
    outcome: 'core', method: 'semantic', selection_role: 'selected_by_interest', evidence: {} }] };

test('brief indicates interest without exposing names or matching diagnostics', () => {
  const view = viewOf(item, null, true);
  assert.equal(view.interests.length, 1);
  assert.equal(view.why.text, '因「冻结兴趣名称」入选');
  assert.equal(view.why.title, '冻结时选篇理由');
  const html = renderToStaticMarkup(BriefInterestMark({ view }));
  assert.match(html, /有你感兴趣的内容/);
  assert.doesNotMatch(html, /冻结兴趣名称|命中|仅标题|正文依据|本期收录|冻结时选篇理由|button/);
  assert.doesNotMatch(source, /BriefCustomInterests|InterestMatchDisclosure|custom_interest_summary/);
});

test('actual quality/breaking/value lane remains independent of match projection', () => {
  for (const [lane, label] of [['quality', '新闻价值入选'], ['read', '内容价值入选'], ['listen', '内容价值入选'], ['breaking', '重大事件']]) {
    const view = viewOf({ ...item, selection_lane: lane }, null, true);
    assert.equal(view.why.text, label);
    assert.equal(view.interests[0].selected, false);
  }
});

test('guest view drops typed private projection and has no private disclosure', () => {
  const view = viewOf(item, null, true, true);
  assert.deepEqual(view.interests, []);
  assert.equal(view.why.text, '兴趣入选');
  assert.equal(BriefInterestMark({ view, guest: true }), null);
});

test('legacy custom refs cannot create a personal chip or invent frozen name', () => {
  const { interest_matches: ignored, ...legacy } = item;
  void ignored;
  const view = viewOf({ ...legacy, matched_interest_refs: [{ kind: 'custom', id: '7' }] }, null, true);
  assert.deepEqual(view.interests, []);
  assert.equal(view.why.text, '兴趣入选');
  assert.equal(whyOf({ selection_lane: 'quality' }, {}).text, '新闻价值入选');
});

test('WhyMark preserves prior visible lanes: only interest/breaking, never neutral/latest chips', () => {
  for (const why of [{ cls: '', text: '新闻价值入选' }, { cls: '', text: '内容价值入选' }, { cls: 'is-fallback', text: '最新更新' }]) {
    assert.equal(WhyMark({ why }), null);
  }
  for (const why of [{ cls: 'is-interest', text: '兴趣入选' }, { cls: 'is-breaking', text: '重大事件', icon: true }]) {
    const mark = WhyMark({ why });
    assert.ok(mark);
    assert.ok(React.Children.toArray(mark.props.children).includes(why.text));
  }
});

test('guest legacy codes and refs do not expose personal interest marks', () => {
  const legacy = { selection_lane: 'interest', matched_interest_codes: ['topic:ai'],
    matched_interest_refs: [{ kind: 'custom', id: '7' }],
    snapshot: { tags: [{ code: 'topic:ai', name_zh: '旧公共兴趣' }] } };
  const view = viewOf(legacy, null, true, true);
  assert.deepEqual(view.interests, []);
  assert.equal(BriefInterestMark({ view, guest: true, showBreaking: true }), null);
});

test('topic indicator hides names and hides when no frozen matches exist', () => {
  const first = { ...item.interest_matches[0], name: 'AI 科研与医疗', method: 'deterministic_title' };
  const second = { ...first, ref: { kind: 'custom', id: '8' }, name: '开源模型与生态' };
  const view = viewOf({ ...item, interest_matches: [first, second, { ...first, ref: { kind: 'tag', id: 'medical' } }] }, null, true);
  const html = renderToStaticMarkup(BriefInterestMark({ view }));
  assert.equal(html, '<p class="bp-interest-topics text-sm">有你感兴趣的内容</p>');
  assert.equal(BriefInterestMark({ view: { interests: [] } }), null);
  assert.equal(BriefInterestMark({ view, guest: true }), null);
});
