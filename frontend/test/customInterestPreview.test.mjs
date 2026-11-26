import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithOxc } from 'vite';
import { customInterestBody, customInterestForm } from '../src/utils/customInterestForm.js';
import { canFilterCustomInterests } from '../src/utils/readerInterestQuery.js';

const source = readFileSync(new URL('../src/components/CustomInterestPanel.jsx', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source.replace(/^import .*;\n/gm, ''), 'CustomInterestPanel.jsx', { jsx: { runtime: 'classic' } });
const saved = { id: 3, revision: 4, name: 'AI for Science', description: '旧备注', status: 'active',
  condition: { all_of: [{ any_of: ['AI'] }], exclude: [],
    semantic_intent: { version: 1, requirement: 'AI 用于科研', negative_examples: ['研究 AI 自身，未用作工具。'] } } };

function nodes(element) {
  if (!element || typeof element !== 'object') return [];
  return [element, ...React.Children.toArray(element.props?.children).flatMap(nodes)];
}
function text(element) {
  if (element == null || typeof element === 'boolean') return '';
  if (typeof element !== 'object') return String(element);
  return React.Children.toArray(element.props?.children).map(text).join('');
}
function harness(props = {}, item = saved) {
  const states = [[item]], refs = [], pending = [], saves = [], effects = [];
  let stateIndex = 0, refIndex = 0, mounted = false;
  const bindings = { React, Plus: 'i', X: 'i', Modal: 'modal', customInterestBody, customInterestForm, canFilterCustomInterests,
    requestLogin: () => { throw new Error('unexpected guest'); },
    useState: (initial) => {
      const slot = stateIndex++;
      if (!(slot in states)) states[slot] = typeof initial === 'function' ? initial() : initial;
      return [states[slot], (value) => { states[slot] = typeof value === 'function' ? value(states[slot]) : value; }];
    },
    useRef: (initial) => { const slot = refIndex++; return refs[slot] ||= { current: initial }; },
    useEffect: (fn) => { if (!mounted) effects.push(fn); },
    fetchInterests: async () => ({ custom_items: [item] }),
    previewCustomInterest: (body) => new Promise((resolve, reject) => pending.push({ body, resolve, reject })),
    saveCustomInterest: async (body, id) => { saves.push({ body, id }); return { item: { ...saved, ...body, revision: 5 } }; },
    deleteCustomInterest: async () => ({}),
  };
  const Component = new Function(...Object.keys(bindings), code.replace('export default ', '') + '\nreturn CustomInterestPanel;')(...Object.values(bindings));
  const render = () => { stateIndex = 0; refIndex = 0; const tree = Component(props); mounted = true; return tree; };
  const button = (label) => nodes(render()).find((node) => node.type === 'button' && text(node) === label);
  const field = (label) => nodes(render()).find((node) => node.type === 'label' && text(node).startsWith(label)).props.children.find((node) => typeof node === 'object');
  const first = render();
  nodes(first).find((node) => node.props?.className === 'interest-chip').props.onClick();
  return { render, button, field, pending, saves, cleanup: () => effects.map((effect) => effect()).forEach((cleanup) => cleanup?.()) };
}

const semantic = (title) => ({ status: 'semantic_verification_required', items: [], scanned: 12,
  lexical_candidates: title ? [{ article_id: title, title, outcome: 'unknown' }] : [] });

test('discover custom interests keep editing without article navigation buttons', () => {
  assert.equal(harness().button('查看文章'), undefined);
});

test('semantic preview shows candidates awaiting verification, including an empty candidate list', async () => {
  const h = harness();
  const request = h.button('预览').props.onClick();
  assert.deepEqual(h.pending[0].body.condition.semantic_intent, saved.condition.semantic_intent);
  h.pending[0].resolve(semantic('科研文章')); await request;
  assert.match(text(h.render()), /待语义核验/); assert.match(text(h.render()), /词组候选/);
  assert.doesNotMatch(text(h.render()), /核心议题|仅提及|暂无匹配内容/);
  const empty = h.button('预览').props.onClick(); h.pending[1].resolve(semantic()); await empty;
  assert.match(text(h.render()), /待语义核验/); assert.doesNotMatch(text(h.render()), /暂无匹配内容/);
});

test('editing clears preview and ignores an older response without unlocking a newer request', async () => {
  const h = harness();
  const old = h.button('预览').props.onClick();
  h.field('匹配说明').props.onChange({ target: { value: '新的科研条件' } });
  const current = h.button('预览').props.onClick();
  h.pending[0].resolve(semantic('旧结果')); await old;
  assert.doesNotMatch(text(h.render()), /旧结果/);
  assert.equal(h.button('预览中…').props.disabled, true);
  h.pending[1].resolve(semantic('新结果')); await current;
  assert.match(text(h.render()), /新结果/);
  h.field('排除词组').props.onChange({ target: { value: '招聘' } });
  assert.doesNotMatch(text(h.render()), /新结果|待语义核验/);
});

test('closed or reopened editor ignores obsolete preview failures', async () => {
  const h = harness(); const request = h.button('预览').props.onClick();
  nodes(h.render()).find((node) => node.type === 'modal').props.onClose();
  h.button('添加').props.onClick();
  h.pending[0].reject(new Error('obsolete failure')); await request;
  assert.doesNotMatch(text(h.render()), /obsolete failure/);
  assert.equal(h.field('显示名称').props.value, '');
});

test('legacy preview remains core/mention; save keeps old description and revision with new intent', async () => {
  const h = harness(); const request = h.button('预览').props.onClick();
  h.pending[0].resolve({ status: 'completed', scanned: 1, items: [{ article_id: 'a', title: '旧条件命中', outcome: 'core' }], lexical_candidates: [] }); await request;
  assert.match(text(h.render()), /核心议题/);
  h.field('匹配说明').props.onChange({ target: { value: 'AI 作为工具帮助科研' } });
  await nodes(h.render()).find((node) => node.type === 'modal').props.panelProps.onSubmit({ preventDefault() {} });
  assert.equal(h.saves[0].body.description, '旧备注'); assert.equal(h.saves[0].body.expected_revision, 4);
  assert.equal(h.saves[0].body.condition.semantic_intent.requirement, 'AI 作为工具帮助科研');
});

test('editor exposes bounded distinct semantic and literal inputs; complete examples stay separate', () => {
  const h = harness();
  assert.equal(h.field('匹配说明').props.maxLength, 1000);
  assert.equal(h.field('示例 1').props.maxLength, 500);
  for (let index = 1; index < 6; index += 1) h.button('添加排除示例').props.onClick();
  assert.equal(h.button('添加排除示例'), undefined);
  assert.equal(nodes(h.render()).filter((node) => node.type === 'textarea').length, 7);
});
