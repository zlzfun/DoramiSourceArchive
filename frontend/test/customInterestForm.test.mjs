import test from 'node:test';
import assert from 'node:assert/strict';
import { customInterestBody, customInterestForm } from '../src/utils/customInterestForm.js';

const item = {
  id: 7, revision: 3, name: '仅作显示的名字', description: '原有备注，不升级为匹配约束', status: 'active',
  condition: { all_of: [{ any_of: ['AI', '人工智能'] }], exclude: ['招聘'],
    semantic_intent: { version: 1, requirement: 'AI 是科学研究方法，而非研究对象。',
      negative_examples: ['AI 数据中心的排放研究，未用 AI 帮助科研。', 'Procurement, without a scientific method.'] } },
};

test('editing round-trips nested intent, complete examples and legacy description', () => {
  const form = customInterestForm(item);
  const body = customInterestBody(form, item);
  assert.deepEqual(body, { name: item.name, description: item.description, condition: item.condition,
    status: item.status, expected_revision: 3 });
  form.negativeExamples.push('新增例子');
  assert.equal(item.condition.semantic_intent.negative_examples.length, 2);
  assert.equal(body.condition.exclude.length, 1);
});

test('new and explicitly cleared semantic intent send null; labels do not become requirements', () => {
  const fresh = customInterestForm();
  fresh.mode = 'rules';
  fresh.name = 'AI for Science'; fresh.groups = ['AI，人工智能'];
  assert.equal(customInterestBody(fresh).condition.semantic_intent, null);
  const edited = customInterestForm(item);
  edited.requirement = ' '; edited.negativeExamples = [];
  const body = customInterestBody(edited, item);
  assert.equal(body.condition.semantic_intent, null);
  assert.equal(body.description, item.description);
  assert.deepEqual(body.condition.exclude, ['招聘']);
});

test('new topic interests use an explicit meaning without keyword prerequisites', () => {
  const form = customInterestForm();
  assert.equal(form.mode, 'topic');
  form.name = 'AI 产品与交互';
  assert.throws(() => customInterestBody(form), /话题范围/);
  form.requirement = 'AI 产品的新功能、使用体验和实际工作流程';
  form.groups = ['只作为旧编辑状态保留'];
  const body = customInterestBody(form);
  assert.equal(body.condition.match_mode, 'topic');
  assert.deepEqual(body.condition.all_of, []);
  const saved = { ...body, revision: 1, id: 8 };
  assert.equal(customInterestForm(saved).mode, 'topic');
  const switched = { ...customInterestForm(saved), mode: 'rules', groups: ['AI'] };
  assert.equal(customInterestBody(switched, saved).condition.match_mode, null);
  assert.equal(customInterestForm(item).mode, 'rules');
});

test('semantic prose preserves punctuation, case and internal newlines independently from literal terms', () => {
  const form = customInterestForm(item);
  form.requirement = '  AI 帮助科研。\nAI is a TOOL.  ';
  form.negativeExamples = [' 不采用 AI，研究 AI 本身。 '];
  form.exclude = '招聘,广告，推广';
  const { condition } = customInterestBody(form);
  assert.equal(condition.semantic_intent.requirement, 'AI 帮助科研。\nAI is a TOOL.');
  assert.deepEqual(condition.semantic_intent.negative_examples, ['不采用 AI，研究 AI 本身。']);
  assert.deepEqual(condition.exclude, ['招聘', '广告', '推广']);
});

test('orphan or blank example asks for an explicit edit instead of silently dropping intent', () => {
  const form = customInterestForm(item);
  form.requirement = '';
  assert.throws(() => customInterestBody(form), /填写匹配说明/);
  form.requirement = '匹配说明'; form.negativeExamples = [' '];
  assert.throws(() => customInterestBody(form), /填写排除示例/);
});
