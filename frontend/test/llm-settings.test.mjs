import test from 'node:test';
import assert from 'node:assert/strict';
import { llmUpdates, deepseekFlashGuidance } from '../src/utils/llmSettings.js';

const effective = { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash', temperature: 0.3, max_tokens: 4096, thinking_mode: 'disabled', aux_model: '', vision_model: '' };
test('只更新主模型,保留其它字段的有效来源、显式预算与禁用思考', () => {
  assert.deepEqual(llmUpdates({ ...effective, api_key: '', model: 'deepseek-flash' }, effective), { model: 'deepseek-flash' });
  assert.deepEqual(llmUpdates({ ...effective, api_key: '   ' }, effective), {});
  assert.deepEqual(llmUpdates({ ...effective, thinking_mode: '', max_tokens: '8192' }, effective), { max_tokens: 8192, thinking_mode: '' });
});
test('主模型升级保留非空辅助/视觉档,只有显式编辑才提交或清除覆盖', () => {
  const configured = { ...effective, aux_model: 'custom-light', vision_model: 'custom-vision' };
  assert.deepEqual(llmUpdates({ ...configured, model: 'deepseek-flash', api_key: '' }, configured), { model: 'deepseek-flash' });
  assert.deepEqual(llmUpdates({ ...configured, aux_model: 'custom-light-v2' }, configured), { aux_model: 'custom-light-v2' });
  assert.deepEqual(llmUpdates({ ...configured, vision_model: '' }, configured), { vision_model: '' });
  assert.deepEqual(llmUpdates({ ...configured, aux_model: '' }, configured), { aux_model: '' });
});
test('只对官方 Flash 给出规范名提示,不迁移 Pro 或第三方别名', () => {
  assert.equal(deepseekFlashGuidance(effective.base_url, effective.model).legacy, true);
  assert.equal(deepseekFlashGuidance(effective.base_url, 'deepseek-flash').version, 'V4.1-Flash-1017');
  for (const [url, model] of [['https://api.deepseek.com', 'deepseek-v4-pro'], ['https://proxy.example/v1', 'deepseek-v4-flash'], ['https://api.deepseek.com.evil.test', 'deepseek-v4-flash'], ['', 'deepseek-flash']]) {
    assert.equal(deepseekFlashGuidance(url, model), null);
  }
});
