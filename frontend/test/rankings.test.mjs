import test from 'node:test';
import assert from 'node:assert/strict';

import {
  RANKING_SCOPE_NOTE,
  rankingMovement,
  rankingTrendPath,
  scoreBasisLabel,
} from '../src/utils/rankings.js';

test('ranking scope is explicitly site-wide and independent from personal subscriptions', () => {
  assert.match(RANKING_SCOPE_NOTE, /全站/);
  assert.match(RANKING_SCOPE_NOTE, /与个人订阅无关/);
  assert.doesNotMatch(RANKING_SCOPE_NOTE, /我的订阅/);
});

test('ranking movement is explicit for up, down, flat and new states', () => {
  assert.deepEqual(rankingMovement(3), { label: '↑ 3', direction: 'up' });
  assert.deepEqual(rankingMovement(-2), { label: '↓ 2', direction: 'down' });
  assert.deepEqual(rankingMovement(0), { label: '持平', direction: 'flat' });
  assert.deepEqual(rankingMovement(null), { label: '新', direction: 'new' });
});

test('trend path handles empty, singleton and changing series without NaN', () => {
  assert.equal(rankingTrendPath([]), '');
  assert.match(rankingTrendPath([{ occurrence_count: 4 }]), /^M80\.0,/);
  assert.doesNotMatch(rankingTrendPath([
    { occurrence_count: 2 }, { occurrence_count: 5 }, { occurrence_count: 3 },
  ]), /NaN/);
});

test('podcast score basis never presents show notes as a full score', () => {
  assert.equal(scoreBasisLabel('show_notes'), '简介初评');
  assert.equal(scoreBasisLabel('full_transcript'), '全文终评');
});
