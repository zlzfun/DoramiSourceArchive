// 往期早报日历(issue #184 目检返修)的纯日期运算。
// 日期键一律 'YYYY-MM-DD'、月键 'YYYY-MM'(与早报 report_date 同形,字符串可直接比大小);
// 加减按 UTC 日历做纯日期算术,与本机时区、夏令时无关;一周从周一开始(中文日历习惯)。

const DAY_MS = 86400000;
const pad = (n) => String(n).padStart(2, '0');
const toUTC = (key) => {
  const [y, m, d] = key.split('-').map(Number);
  return Date.UTC(y, m - 1, d);
};
const fromUTC = (ms) => {
  const date = new Date(ms);
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
};

export const monthOf = (key) => key.slice(0, 7);

export function addDays(key, delta) {
  return fromUTC(toUTC(key) + delta * DAY_MS);
}

export function addMonths(monthKey, delta) {
  const [y, m] = monthKey.split('-').map(Number);
  const index = y * 12 + (m - 1) + delta;
  return `${Math.floor(index / 12)}-${pad((index % 12) + 1)}`;
}

export function daysInMonth(monthKey) {
  const [y, m] = monthKey.split('-').map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

// 同日挪月:目标月没有这一天时落到月末(1 月 31 日的上一月是 12 月 31 日,下一月是 2 月 28 / 29 日)
export function shiftMonthKeepDay(key, delta) {
  const target = addMonths(monthOf(key), delta);
  return `${target}-${pad(Math.min(Number(key.slice(8, 10)), daysInMonth(target)))}`;
}

// 周一 = 0 … 周日 = 6
export function weekdayIndex(key) {
  return (new Date(toUTC(key)).getUTCDay() + 6) % 7;
}

// 月历网格:按周分行,每行 7 格(周一起),月外的格为 null;共 4–6 行
export function monthGrid(monthKey) {
  const lead = weekdayIndex(`${monthKey}-01`);
  const total = daysInMonth(monthKey);
  const cells = [
    ...Array(lead).fill(null),
    ...Array.from({ length: total }, (_, i) => `${monthKey}-${pad(i + 1)}`),
  ];
  while (cells.length % 7) cells.push(null);
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

// 键位到目标日期(WAI-ARIA APG 日期网格):←→ 一天、↑↓ 一周、Home / End 本周首尾、
// PageUp / PageDown 上 / 下个月同一天;不认识的键返回 null
export function moveByKey(key, eventKey) {
  switch (eventKey) {
    case 'ArrowLeft': return addDays(key, -1);
    case 'ArrowRight': return addDays(key, 1);
    case 'ArrowUp': return addDays(key, -7);
    case 'ArrowDown': return addDays(key, 7);
    case 'Home': return addDays(key, -weekdayIndex(key));
    case 'End': return addDays(key, 6 - weekdayIndex(key));
    case 'PageUp': return shiftMonthKeepDay(key, -1);
    case 'PageDown': return shiftMonthKeepDay(key, 1);
    default: return null;
  }
}

export function clampKey(key, min, max) {
  if (key < min) return min;
  if (key > max) return max;
  return key;
}

// 月份标签:同年也写全「2026 年 10 月」——日历头只此一处,不省年份
export function monthLabel(monthKey) {
  return `${Number(monthKey.slice(0, 4))} 年 ${Number(monthKey.slice(5, 7))} 月`;
}
