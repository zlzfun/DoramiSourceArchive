// 转推卡片顶上那一行(issue #344):写转推者本人,不写源名。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { reposterLabel } from '../src/utils/socialPost.js';

test('显示名优先,只有账号名的写 @账号名', () => {
  assert.equal(reposterLabel({ author_name: 'Field Reporter', author_handle: 'field_reporter' }, 'Grok 推荐', 'push_grok'), 'Field Reporter');
  // 载荷里 author 只给 handle 是合法的:归一化后显示名是空串
  assert.equal(reposterLabel({ author_name: '', author_handle: 'alice' }, 'Grok 推荐', 'push_grok'), '@alice');
  assert.equal(reposterLabel({ author_name: '  ', author_handle: '@alice' }, 'Grok 推荐'), '@alice');
});

test('两样都没有的旧条目退回源名,再退回源 id', () => {
  assert.equal(reposterLabel({}, 'X · Sam Altman', 'x_sama'), 'X · Sam Altman');
  assert.equal(reposterLabel({ author_name: null, author_handle: null }, '', 'x_sama'), 'x_sama');
  assert.equal(reposterLabel(undefined, '', 'x_sama'), 'x_sama');
});
