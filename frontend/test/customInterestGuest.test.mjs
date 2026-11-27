import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithOxc } from 'vite';
import { requestLogin, LOGIN_REQUIRED_EVENT } from '../src/utils/loginPrompt.js';
import { customInterestBody, customInterestForm } from '../src/utils/customInterestForm.js';

// Exercise actual handlers with inert hooks, without a DOM or network.
const source = readFileSync(new URL('../src/components/CustomInterestPanel.jsx', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source.replace(/^import .*;\n/gm, ''), 'CustomInterestPanel.jsx', { jsx: { runtime: 'classic' } });
function mount(guest, existing = false) {
  const calls = [], changes = [];
  let index = 0;
  const item = { id: 8, revision: 1, name: '内网 Agent', status: 'active' };
  const values = existing ? [[item], item, { ...customInterestForm(), name: item.name, groups: ['Agent'] }, true] : [];
  const api = (name) => async () => { calls.push(name); return { custom_items: [], item, items: [] }; };
  const bindings = {
    React, Plus: 'i', X: 'i', Modal: 'modal', requestLogin, customInterestBody, customInterestForm,
    useRef: (value) => ({ current: value }),
    useEffect: (effect) => effect(),
    useState: (initial) => {
      const slot = index++;
      return [slot in values ? values[slot] : typeof initial === 'function' ? initial() : initial,
        (value) => changes.push({ slot, value })];
    },
    fetchInterests: api('fetch'), saveCustomInterest: api('save'),
    previewCustomInterest: api('preview'), deleteCustomInterest: api('delete'),
  };
  const component = new Function(...Object.keys(bindings), code.replace('export default ', '') + '\nreturn CustomInterestPanel;')(...Object.values(bindings));
  return { tree: component({ guest }), calls, changes };
}
function nodes(element) {
  if (!element || typeof element !== 'object') return [];
  return [element, ...React.Children.toArray(element.props?.children).flatMap(nodes)];
}

test('guest Add prompts login before private fetch or editor open', () => {
  const previous = globalThis.window;
  globalThis.window = new EventTarget();
  const events = [];
  window.addEventListener(LOGIN_REQUIRED_EVENT, (event) => events.push(event.detail));
  try {
    const { tree, calls, changes } = mount(true);
    nodes(tree).find((node) => node.type === 'button').props.onClick();
    assert.deepEqual(events, [{ action: 'interest' }]);
    assert.deepEqual(calls, []);
    assert.deepEqual(changes, []);
    assert.equal(nodes(tree).find((node) => node.type === 'modal').props.open, false);
  } finally { if (previous === undefined) delete globalThis.window; else globalThis.window = previous; }
});

test('guest transition hides prior private state and blocks preview, save and delete', async () => {
  const { tree, calls, changes } = mount(true, true);
  const all = nodes(tree);
  assert.equal(all.find((node) => node.type === 'modal').props.open, false);
  assert.equal(all.some((node) => node.props?.className === 'interest-chip'), false);
  await all.find((node) => node.type === 'modal').props.panelProps.onSubmit({ preventDefault() {} });
  for (const label of ['预览', '移除']) {
    await all.find((node) => node.type === 'button' && node.props.children === label).props.onClick();
  }
  assert.deepEqual(calls, []);
  assert.deepEqual(changes, []);
});

test('authenticated Add still loads interests and opens editor', () => {
  const { tree, calls, changes } = mount(false);
  nodes(tree).find((node) => node.type === 'button').props.onClick();
  assert.deepEqual(calls, ['fetch']);
  assert.ok(changes.some(({ slot, value }) => slot === 3 && value === true));
});

test('desktop and both mobile interest entries propagate session guest flag', () => {
  for (const file of ['ReaderTab.jsx', 'mobile/MobileReader.jsx']) {
    const content = readFileSync(new URL(`../src/components/${file}`, import.meta.url), 'utf8');
    const entries = [...content.matchAll(/<InterestPage\s+([^>]+)>/g)];
    assert.equal(entries.length, file === 'ReaderTab.jsx' ? 1 : 2);
    for (const entry of entries) assert.match(entry[1], /guest=\{rs\.isGuest\}/);
  }
  const page = readFileSync(new URL('../src/components/InterestPage.jsx', import.meta.url), 'utf8');
  assert.match(page, /<CustomInterestPanel guest=\{guest\}/);
});
