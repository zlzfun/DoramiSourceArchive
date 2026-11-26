import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithOxc } from 'vite';
import { canFilterCustomInterests } from '../src/utils/readerInterestQuery.js';
const source = readFileSync(new URL('../src/components/CustomInterestFilter.jsx', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source.replace(/^import .*;\n/gm, ''), 'CustomInterestFilter.jsx', { jsx: { runtime: 'classic' } });
const Filter = new Function('React', 'canFilterCustomInterests', code.replace('export default ', '') + '\nreturn CustomInterestFilter;')(React, canFilterCustomInterests);
const capabilities = { interest_match_projection: 1, custom_interest_filter: true, match_contract: 1 };
const items = [{ id: 39, name: 'RSI 自进化', status: 'active' }, { id: 40, name: '主动性 Agent', status: 'paused' }];
test('article filter selects actual ID, clears and disables paused interests', () => {
  const changes = [];
  const tree = Filter({ items, capabilities, value: '39', onChange: (id) => changes.push(id) });
  assert.equal(tree.props.value, '39');
  assert.equal(tree.props.disabled, false);
  tree.props.onChange({ target: { value: '39' } });
  tree.props.onChange({ target: { value: '' } });
  assert.deepEqual(changes, ['39', '']);
  const options = React.Children.toArray(tree.props.children);
  assert.equal(options[1].props.value, '39');
  assert.equal(options[2].props.disabled, true);
});
test('missing capabilities never downgrade filtering; no interests hides selector', () => {
  for (const key of Object.keys(capabilities)) {
    const partial = { ...capabilities }; delete partial[key];
    assert.equal(Filter({ items, capabilities: partial }).props.disabled, true);
  }
  assert.equal(Filter({ items: [] }), null);
});
