import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithOxc } from 'vite';
import * as helpers from '../src/utils/interestMatches.js';

// Execute the actual component and handlers with inert layer/hook adapters.
// These checks exercise rendering/dispatch; they do not claim browser focus QA.
const source = readFileSync(new URL('../src/components/InterestMatchDisclosure.jsx', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source.replace(/^import .*;\n/gm, ''), 'InterestMatchDisclosure.jsx', { jsx: { runtime: 'classic' } });
const match = (id, patch = {}) => ({ ref: { kind: 'custom', id }, name: '同名兴趣', revision: 1,
  outcome: 'core', basis: 'edition_snapshot', method: 'semantic', selection_role: 'also_matches',
  evidence: { reason: '原文讨论持久状态。', quotes: [{ text: `正文证据${id}`, source_kind: 'article_body', availability: 'historical' }] },
  drilldown: { available: true }, ...patch });
const rows = helpers.normalizeInterestMatches({ interest_matches: [match('1'), match('2')] });
function mount(props = {}, open = true) {
  const changes = [];
  const layer = ({ children, head, open: isOpen }) => isOpen ? React.createElement('section', { role: 'dialog' }, head, children) : null;
  const bindings = { React, X: 'i', Modal: layer, Sheet: layer, ...helpers,
    useState: () => [open, (value) => changes.push(value)] };
  const Component = new Function(...Object.keys(bindings), code.replace('export default ', '') + '\nreturn InterestMatchDisclosure;')(...Object.values(bindings));
  return { tree: Component({ matches: rows, ...props }), changes };
}
function nodes(element) {
  if (!element || typeof element !== 'object') return [];
  return [element, ...React.Children.toArray(element.props?.children).flatMap(nodes)];
}
function nestedButton(element, inside = false) {
  if (!element || typeof element !== 'object') return false;
  if (element.type === 'button' && inside) return true;
  return React.Children.toArray(element.props?.children).some((child) => nestedButton(child, inside || element.type === 'button'));
}

test('desktop and mobile disclose every equal-name match with literal historical quotes', () => {
  for (const mobile of [false, true]) {
    const { tree } = mount({ mobile, selectionLabel: '新闻价值入选' });
    const html = renderToStaticMarkup(tree);
    assert.match(html, /正文证据1/); assert.match(html, /正文证据2/);
    assert.equal((html.match(/当时依据/g) || []).length, 2);
    assert.match(html, /新闻价值入选/);
    assert.doesNotMatch(html, /入选兴趣/);
    assert.equal(nestedButton(tree), false);
  }
});

test('missing capability disables actual handler and never invokes a broad article-list fallback', () => {
  const calls = [];
  const { tree } = mount({ onOpenInterest: (ref) => calls.push(ref) });
  const buttons = nodes(tree).filter((node) => node.type === 'button' && node.props.className.includes('interest-match-drilldown'));
  assert.equal(buttons.length, 2);
  for (const button of buttons) { assert.equal(button.props.disabled, true); button.props.onClick(); }
  assert.deepEqual(calls, []);
});

test('capable drilldown dispatches exactly selected typed identity and closes the layer', () => {
  const calls = [];
  const { tree, changes } = mount({ capabilities: { interest_match_projection: 1, custom_interest_filter: true, match_contract: 1 },
    onOpenInterest: (ref) => calls.push(ref) });
  nodes(tree).filter((node) => node.type === 'button' && node.props.className.includes('interest-match-drilldown'))[1].props.onClick();
  assert.deepEqual(calls, [{ kind: 'custom', id: '2' }]);
  assert.deepEqual(changes, [false]);
});

test('trigger is a real button; click/touch do not bubble into article open or long press', () => {
  const { tree, changes } = mount({}, false);
  const trigger = nodes(tree).find((node) => node.type === 'button');
  assert.equal(trigger.props['aria-haspopup'], 'dialog');
  assert.equal(trigger.props['aria-expanded'], false);
  let stopped = 0;
  trigger.props.onTouchStart({ stopPropagation: () => stopped++ });
  trigger.props.onContextMenu({ stopPropagation: () => stopped++ });
  trigger.props.onClick({ stopPropagation: () => stopped++ });
  assert.equal(stopped, 3);
  assert.deepEqual(changes, [true]);
});

test('brief names every custom interest even for multiple matches; title proof stays title-only', () => {
  const matches = helpers.normalizeInterestMatches({ interest_matches: [
    match('1', { name: '编程智能体', method: 'deterministic_title', evidence: { quotes: [] } }),
    match('2', { name: '推理效率' }),
  ] });
  for (const mobile of [false, true]) {
    const { tree, changes } = mount({ matches, expandedNames: true, mobile }, false);
    const html = renderToStaticMarkup(tree);
    assert.match(html, /自定义兴趣 · 编程智能体 · 仅标题符合条件/);
    assert.match(html, /自定义兴趣 · 推理效率 · 正文依据/);
    assert.doesNotMatch(html, /兴趣 · 2/);
    const triggers = nodes(tree).filter((node) => node.type === 'button' && node.props.className === 'interest-match-trigger');
    assert.equal(triggers.length, 2);
    let stopped = 0;
    triggers[1].props.onClick({ stopPropagation: () => stopped++ });
    assert.equal(stopped, 1); assert.deepEqual(changes, [true]);
    assert.equal(nestedButton(tree), false);
  }
});

test('redacted and title-only data cannot render as original-body evidence', () => {
  const matches = helpers.normalizeInterestMatches({ interest_matches: [
    match('1', { evidence: { redaction: 'evidence_unavailable', reason: 'PRIVATE_SECRET', quotes: [{ text: 'PRIVATE_SECRET' }] } }),
    match('2', { method: 'deterministic_title', evidence: { quotes: [] } }),
  ] });
  const html = renderToStaticMarkup(mount({ matches }).tree);
  assert.doesNotMatch(html, /PRIVATE_SECRET/);
  assert.match(html, /依据暂不可用/);
  assert.match(html, /仅标题符合条件/);
});

test('layer primitives retain keyboard, close and portal contracts', () => {
  const desktop = nodes(mount().tree).find((node) => node.props.ariaLabel === '兴趣依据');
  assert.equal(desktop.props.closeOnOverlay, true);
  assert.equal(desktop.props.portal, true);
  const mobile = nodes(mount({ mobile: true }).tree).find((node) => node.props.label === '兴趣依据');
  assert.equal(mobile.props.grabLabel, '关闭兴趣依据');
});

test('both layer panels stop chrome/content long-press bubbling without intercepting defaults or drag', () => {
  for (const mobile of [false, true]) {
    const layer = nodes(mount({ mobile }).tree).find((node) => node.props.ariaLabel === '兴趣依据' || node.props.label === '兴趣依据');
    const panel = layer.props.panelProps;
    let stops = 0, defaults = 0;
    const event = { stopPropagation: () => stops++, preventDefault: () => defaults++ };
    panel.onTouchStart(event);
    panel.onContextMenu(event);
    assert.equal(stops, 2);
    assert.equal(defaults, 0);
    assert.equal(panel.onClick, undefined);
    assert.equal(panel.onPointerDown, undefined);
    const body = nodes(mount({ mobile }).tree).find((node) => node.props.className === 'interest-match-body');
    assert.equal(body.props.onTouchStart, undefined);
  }
});

test('named detail label opens only its own reason and quotes', () => {
  const { tree, changes } = mount({ matches: [rows[1]], triggerLabel: '兴趣：同名兴趣' }, false);
  const trigger = nodes(tree).find((node) => node.type === 'button');
  assert.equal(trigger.props.children, '兴趣：同名兴趣');
  trigger.props.onClick({ stopPropagation() {} });
  assert.deepEqual(changes, [true]);
  const html = renderToStaticMarkup(mount({ matches: [rows[1]], triggerLabel: '兴趣：同名兴趣' }).tree);
  assert.match(html, /正文证据2/);
  assert.doesNotMatch(html, /正文证据1/);
});
