// 令牌交付计量(issue #312)的展示口径:接口组、接口 / 令牌的叫法、抽屉标题与令牌卡第二行、逐日透视。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  DELIVERY_GROUPS,
  channelLabel,
  deliveryGroupColor,
  deliverySectionTitle,
  pivotDeliveryDaily,
  tokenKindLabel,
  tokenMetaLine,
} from '../src/utils/deliveryChannels.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const iso = (daysAgo) => {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

test('四个接口组各有固定颜色,且与后端的分组一一对上', () => {
  assert.deepEqual(DELIVERY_GROUPS.map((g) => g.key), ['articles', 'events', 'subscription', 'mcp']);
  assert.equal(new Set(DELIVERY_GROUPS.map((g) => g.color)).size, 4);
  // 组名在后端 services/token_usage.py 里是常量:这里少认一个,那一组的调用就会被画成第一组的颜色。
  const backend = readFileSync(path.resolve(here, '../../src/services/token_usage.py'), 'utf8');
  const keys = [...backend.matchAll(/^GROUP_[A-Z]+ = "([a-z]+)"$/gm)].map((m) => m[1]);
  assert.deepEqual(keys.sort(), DELIVERY_GROUPS.map((g) => g.key).sort());
});

test('颜色按 key 也按显示名取;不认识的组不抛错', () => {
  const mcp = DELIVERY_GROUPS.find((g) => g.key === 'mcp');
  assert.equal(deliveryGroupColor('mcp'), mcp.color);
  assert.equal(deliveryGroupColor('MCP'), mcp.color);
  assert.equal(deliveryGroupColor('热点事件'), DELIVERY_GROUPS[1].color);
  assert.equal(deliveryGroupColor('nope'), DELIVERY_GROUPS[0].color);
});

test('接口叫它的真实路径,MCP 工具写成 mcp · 工具名', () => {
  assert.equal(channelLabel('feed/articles.md'), 'feed/articles.md');
  assert.equal(channelLabel('subscriptions/{id}/articles'), 'subscriptions/{id}/articles');
  assert.equal(channelLabel('mcp:search_articles'), 'mcp · search_articles');
  assert.equal(channelLabel(undefined), '');
});

test('令牌的类型名', () => {
  assert.equal(tokenKindLabel({ kind: 'feed' }), '聚合令牌');
  assert.equal(tokenKindLabel({ kind: 'subscription', subscription: { id: 3, name: 'OpenAI 博客' } }), '订阅令牌 · OpenAI 博客');
  assert.equal(tokenKindLabel({ kind: 'subscription', subscription: { id: 3, name: '' } }), '订阅令牌'); // 订阅已删
});

test('令牌卡第二行:最近调用、作废后仍被调、未调用', () => {
  const stamp = (v) => `<${v}>`;
  assert.equal(tokenMetaLine({ status: 'current', calls: 5, last_at: 'T1' }, stamp), '最近调用 <T1>');
  assert.equal(tokenMetaLine({ status: 'current', calls: 0, last_at: null }, stamp), '未调用');
  assert.equal(tokenMetaLine({ status: 'retired', calls: 5, last_at: 'T1', rejected: 12 }, stamp), '最近调用 <T1> · 作废后仍被调 12 次');
  // 窗口内只有被拒、没有成功调用的旧令牌:不写「未调用」
  assert.equal(tokenMetaLine({ status: 'retired', calls: 0, last_at: null, rejected: 3 }, stamp), '作废后仍被调 3 次');
  // 现用的令牌不会有「作废后」;rejected 即便有数也不显示
  assert.equal(tokenMetaLine({ status: 'current', calls: 1, last_at: 'T1', rejected: 2 }, stamp), '最近调用 <T1>');
});

test('抽屉这一区的标题', () => {
  assert.equal(deliverySectionTitle({ window_days: 30, totals: { calls: 119 }, tokens: [{}, {}] }), '接口调用 · 近 30 天 119 次 · 2 枚令牌');
  assert.equal(deliverySectionTitle({ window_days: 7, totals: { calls: 56 }, tokens: [{}] }), '接口调用 · 近 7 天 56 次');
  assert.equal(deliverySectionTitle({ window_days: 30, totals: { calls: 0 }, tokens: [{}] }), '接口调用');
});

test('逐日透视:系列按固定次序,只出有数的组,缺失日补 0', () => {
  const rows = [
    { day: iso(0), group: 'mcp', calls: 4, items: 30 },
    { day: iso(0), group: 'articles', calls: 2, items: 200 },
    { day: iso(2), group: 'articles', calls: 1, items: 100 },
    { day: iso(1), group: 'events', calls: 0, items: 0 },   // 窗口内合计为 0 的组不出系列
    { day: iso(1), group: 'mystery', calls: 9, items: 9 },  // 不认识的组不画
  ];
  const calls = pivotDeliveryDaily(rows, 3, 'calls');
  assert.deepEqual(calls.keys, ['文章', 'MCP']);            // 调得多的是 MCP,但次序恒按组的固定次序
  assert.deepEqual(calls.data.map((d) => d.day), [iso(2), iso(1), iso(0)]);
  assert.deepEqual(calls.data.map((d) => [d['文章'], d.MCP]), [[1, 0], [0, 0], [2, 4]]);
  const items = pivotDeliveryDaily(rows, 3, 'items');
  assert.deepEqual(items.data.map((d) => [d['文章'], d.MCP]), [[100, 0], [0, 0], [200, 30]]);
  assert.deepEqual(pivotDeliveryDaily([], 3), { data: calls.data.map(({ day, label }) => ({ day, label })), keys: [] });
});
