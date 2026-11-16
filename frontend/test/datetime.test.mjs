// 读者面发布时间按本地时区显示(issue #184)。测试固定在北京时间。
process.env.TZ = 'Asia/Shanghai';

const { default: test } = await import('node:test');
const { default: assert } = await import('node:assert/strict');
const { formatPublishDate, formatPublishDateTime, formatDateTime, formatReaderDate, formatRelativeTime } = await import('../src/utils/datetime.js');

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

test('读者面桌面的日期写法:几月几日 时:分,今年不带年份,跨年与悬停提示带年份', () => {
  const year = new Date().getFullYear();
  assert.equal(formatReaderDate(`${year}-09-15T07:01:51+00:00`), '9月15日 15:01');
  assert.equal(formatReaderDate(`${year}-09-15T07:01:51+00:00`, { withYear: true }), `${year}年9月15日 15:01`);
  // 换算后落在今年:不带年份
  assert.equal(formatReaderDate(`${year - 1}-12-31T23:25:00+00:00`), '1月1日 07:25');
  assert.equal(formatReaderDate(`${year - 1}-03-02T04:00:00+00:00`), `${year - 1}年3月2日 12:00`);
  // 没有真实时刻:只写到日,不因时区换算跨日
  assert.equal(formatReaderDate(`${year}-09-15T00:00:00+00:00`), '9月15日');
  assert.equal(formatReaderDate(`${year}-09-15`), '9月15日');
  assert.equal(formatReaderDate(''), '');
});

test('相对时间:七天以外,读者面桌面写几月几日,缺省仍是连字符', () => {
  const old = new Date(Date.now() - 9 * 86400 * 1000);
  const iso = old.toISOString();
  const sameYear = old.getFullYear() === new Date().getFullYear();
  const cn = `${sameYear ? '' : `${old.getFullYear()}年`}${old.getMonth() + 1}月${old.getDate()}日`;
  assert.equal(formatRelativeTime(iso, '', { cn: true }), cn);
  assert.match(formatRelativeTime(iso, ''), /^(\d{4}-)?\d{2}-\d{2}$/);
  // 七天以内两种写法一致
  const recent = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
  assert.equal(formatRelativeTime(recent, '', { cn: true }), formatRelativeTime(recent, ''));
});
