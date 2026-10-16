// 读者面发布时间按本地时区显示(issue #184)。测试固定在北京时间。
process.env.TZ = 'Asia/Shanghai';

const { default: test } = await import('node:test');
const { default: assert } = await import('node:assert/strict');
const { formatPublishDate, formatPublishDateTime, formatDateTime } = await import('../src/utils/datetime.js');

test('带时区的 UTC 时间换算成本地时间:07:01Z 显示成北京时间 15:01', () => {
  assert.equal(formatPublishDate('2026-09-15T07:01:51.092183+00:00'), '2026-09-15 15:01');
  assert.equal(formatPublishDateTime('2026-09-15T07:01:51.092183+00:00'), '2026-09-15 15:01:51');
});

test('换算可以跨日:UTC 晚上是北京时间第二天', () => {
  assert.equal(formatPublishDate('2026-09-16T23:25:00+00:00'), '2026-09-17 07:25');
});

test('没有时刻的 00:00 占位只画原始日期,不因时区换算跨日', () => {
  assert.equal(formatPublishDate('2026-09-15T00:00:00+00:00'), '2026-09-15');
  assert.equal(formatPublishDate('2026-09-15T00:00:00'), '2026-09-15');
  assert.equal(formatPublishDate('2026-09-15'), '2026-09-15');
  // 悬停提示同一规则:不补出换算出来的时刻(codex R1-P2-04)
  assert.equal(formatPublishDateTime('2026-09-15T00:00:00+00:00'), '2026-09-15');
  assert.equal(formatPublishDateTime('2026-09-15'), '2026-09-15');
});

test('不带时区的值按本地时间解析,与原来按字符截断一致', () => {
  assert.equal(formatPublishDate('2026-09-15T17:01:54'), '2026-09-15 17:01');
});

test('空值与无法解析的值', () => {
  assert.equal(formatPublishDate(''), '');
  assert.equal(formatPublishDate(null, '—'), '—');
  assert.equal(formatPublishDate('昨天 15:00'), '昨天 15:00');
});

test('管理面的 formatDateTime 不变:仍按字符截断(审计语境保留原值)', () => {
  assert.equal(formatDateTime('2026-09-15T07:01:51+00:00'), '2026-09-15 07:01:51');
});
