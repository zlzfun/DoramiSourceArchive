// 往期早报日历的日期运算 + 西文 lang 标记(issue #184 目检返修)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  addDays,
  addMonths,
  clampKey,
  daysInMonth,
  monthGrid,
  monthLabel,
  moveByKey,
  shiftMonthKeepDay,
  weekdayIndex,
} from '../src/utils/briefCalendar.js';
import { latinLang } from '../src/utils/readerText.js';

test('日期加减跨月、跨年、闰年,与本机时区无关', () => {
  assert.equal(addDays('2026-10-31', 1), '2026-11-01');
  assert.equal(addDays('2026-01-01', -1), '2025-12-31');
  assert.equal(addDays('2024-02-28', 1), '2024-02-29');
  assert.equal(addDays('2026-03-08', 7), '2026-03-15'); // 美国夏令时切换那周,纯日期算术不受影响
  assert.equal(addMonths('2026-01', -1), '2025-12');
  assert.equal(addMonths('2025-12', 1), '2026-01');
  assert.equal(addMonths('2026-10', -14), '2025-08');
  assert.deepEqual(['2024-02', '2026-02', '2026-10', '2026-11'].map(daysInMonth), [29, 28, 31, 30]);
});

test('同日挪月落不到这一天时取月末', () => {
  assert.equal(shiftMonthKeepDay('2026-03-31', -1), '2026-02-28');
  assert.equal(shiftMonthKeepDay('2024-01-31', 1), '2024-02-29');
  assert.equal(shiftMonthKeepDay('2026-01-15', -1), '2025-12-15');
});

test('一周从周一开始;月历按周分行、月外留空', () => {
  assert.equal(weekdayIndex('2026-10-12'), 0); // 周一
  assert.equal(weekdayIndex('2026-10-14'), 2); // 周三
  assert.equal(weekdayIndex('2026-10-18'), 6); // 周日
  const october = monthGrid('2026-10'); // 10 月 1 日是周四
  assert.equal(october.length, 5);
  assert.deepEqual(october[0], [null, null, null, '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']);
  assert.deepEqual(october[4], ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31', null]);
  assert.ok(october.every((week) => week.length === 7));
  assert.equal(monthGrid('2021-02').length, 4); // 2 月 1 日周一、28 天:正好四行
  assert.equal(monthGrid('2026-08').length, 6); // 8 月 1 日周六、31 天:六行
});

test('键位:←→ 一天、↑↓ 一周、Home / End 本周首尾、翻页键挪月,其它键不处理', () => {
  const wed = '2026-10-14';
  assert.equal(moveByKey(wed, 'ArrowLeft'), '2026-10-13');
  assert.equal(moveByKey(wed, 'ArrowRight'), '2026-10-15');
  assert.equal(moveByKey(wed, 'ArrowUp'), '2026-10-07');
  assert.equal(moveByKey(wed, 'ArrowDown'), '2026-10-21');
  assert.equal(moveByKey(wed, 'Home'), '2026-10-12');
  assert.equal(moveByKey(wed, 'End'), '2026-10-18');
  assert.equal(moveByKey('2026-10-31', 'PageDown'), '2026-11-30');
  assert.equal(moveByKey('2026-10-14', 'PageUp'), '2026-09-14');
  assert.equal(moveByKey(wed, 'Enter'), null);
  assert.equal(moveByKey(wed, 'a'), null);
});

test('范围夹取与月份标签', () => {
  assert.equal(clampKey('2026-10-20', '2026-09-01', '2026-10-15'), '2026-10-15');
  assert.equal(clampKey('2026-08-31', '2026-09-01', '2026-10-15'), '2026-09-01');
  assert.equal(clampKey('2026-10-01', '2026-09-01', '2026-10-15'), '2026-10-01');
  assert.equal(monthLabel('2026-10'), '2026 年 10 月');
  assert.equal(monthLabel('2027-01'), '2027 年 1 月');
});

test('西文标记:英文标 en,中文与无字母文本沿用页面语言', () => {
  assert.equal(latinLang('When AI Solves Your Life’s Work'), 'en');
  assert.equal(latinLang('Dyna-2: General Purpose Physical Agents'), 'en');
  assert.equal(latinLang('一个 Personal Agent 创业者的思考｜对谈 Ambi 创始人子皓'), undefined);
  assert.equal(latinLang('合成超级智能：从半导体到超导体'), undefined);
  assert.equal(latinLang('2026 · 03'), undefined);
  assert.equal(latinLang(''), undefined);
  assert.equal(latinLang(null), undefined);
});
