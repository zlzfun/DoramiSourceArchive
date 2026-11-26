import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithOxc } from 'vite';
import { normalizeInterestMatches } from '../src/utils/interestMatches.js';
const source = readFileSync(new URL('../src/components/CurrentInterestMatches.jsx', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source.replace(/^import .*;\n/gm, ''), 'CurrentInterestMatches.jsx', { jsx: { runtime: 'classic' } });
const Disclosure = () => null;
const CurrentInterestMatches = new Function('React', 'InterestMatchDisclosure', 'normalizeInterestMatches', code.replace('export default ', '') + '\nreturn CurrentInterestMatches;')(React, Disclosure, normalizeInterestMatches);
const row = (id, patch = {}) => ({ ref: { kind: 'custom', id }, name: `兴趣${id}`, revision: 1, basis: 'current', outcome: 'core', method: 'semantic', evidence: { quotes: [] }, drilldown: { available: true }, ...patch });

test('current projection shows every core with article navigation context and no frozen selection cause', () => {
  const article = { id: 'article', source_id: 'podcast', selection_lane: 'interest', interest_matches: [row('1'), row('2'), row('3', { outcome: 'mention' })] };
  const calls = []; const caps = { interest_match_projection: 1 };
  const tree = CurrentInterestMatches({ article, mobile: true, capabilities: caps, onOpenInterest: (...args) => calls.push(args) });
  assert.equal(tree.type, React.Fragment);
  const disclosures = tree.props.children;
  assert.equal(disclosures.length, 2);
  const disclosure = disclosures[0];
  assert.equal(disclosure.props.triggerLabel, "兴趣1");
  assert.equal(disclosure.props.triggerClassName, "reader-tag-chip is-actionable");
  assert.equal(disclosures[1].props.triggerLabel, "兴趣2");
  assert.equal(disclosure.type, Disclosure); assert.equal(disclosure.props.mobile, true); assert.equal(disclosure.props.capabilities, caps);
  assert.deepEqual(disclosure.props.matches.map((match) => match.ref.id), ['1']);
  assert.deepEqual(disclosures[1].props.matches.map((match) => match.ref.id), ['2']);
  assert.ok(disclosure.props.matches.every((match) => match.selected === false));
  disclosure.props.onOpenInterest({ kind: 'custom', id: '2' });
  assert.deepEqual(calls, [[{ kind: 'custom', id: '2' }, article]]);
});

test('guest, frozen-only and legacy prose never expose current positive private matches', () => {
  for (const props of [
    { guest: true, article: { interest_matches: [row('1')] } },
    { article: { interest_matches: [row('1', { basis: 'edition_snapshot' })] } },
    { article: { matched_interest_codes: ['agent'], interest_hits: ['legacy'], matched_interest_refs: [{ kind: 'custom', id: '1' }] } },
    { article: { interest_matches: null, interest_hits: ['legacy'] } },
  ]) assert.equal(CurrentInterestMatches(props), null);
});

 test('list indicator omits interest names and counts and is not a separate action', () => {
  const tree = CurrentInterestMatches({ article: { interest_matches: [row('1'), row('2')] }, compact: true });
  assert.equal(tree.type, 'span');
  assert.equal(tree.props.children, '有你感兴趣的内容');
  assert.equal(tree.props.onClick, undefined);
});
