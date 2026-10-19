import { test } from 'node:test';
import assert from 'node:assert/strict';

import { formatUnreadCount } from '../src/utils/readerText.js';

// 源行未读数(桌面源栏 + 移动端源抽屉共用)。空值必须回空串:调用方以 `unread > 0`
// 决定是否渲染,函数本身不再造一个「0」——否则 0 未读的源也会多挂一个数字。
test('未读数为 0 / 空 / 非数字时不显示', () => {
  assert.equal(formatUnreadCount(0), '');
  assert.equal(formatUnreadCount(null), '');
  assert.equal(formatUnreadCount(undefined), '');
  assert.equal(formatUnreadCount(''), '');
  assert.equal(formatUnreadCount('abc'), '');
  assert.equal(formatUnreadCount(-3), '');
});

test('未读数按原值显示,超过 999 收成 999+', () => {
  assert.equal(formatUnreadCount(1), '1');
  assert.equal(formatUnreadCount(99), '99');
  assert.equal(formatUnreadCount(999), '999');
  assert.equal(formatUnreadCount(1000), '999+');
  assert.equal(formatUnreadCount(12345), '999+');
});
