// 自 frontend/src/utils/readerTime.js 复制(DOM 无关纯函数);改动须两侧同步。
export const WEEKDAY_CHARS = ['日', '一', '二', '三', '四', '五', '六'];

export const fmtDayKey = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// iOS 的 JS 引擎不认 "YYYY-MM-DD HH:mm:ss"(空格分隔)——归一到 ISO 的 T 分隔再解析
export function parseDate(raw) {
  if (!raw) return null;
  if (raw instanceof Date) return Number.isNaN(raw.getTime()) ? null : raw;
  let s = String(raw).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(s)) s = s.replace(' ', 'T');
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

export const dayKeyOf = (article) => {
  const d = parseDate(article?.publish_date || article?.fetched_date);
  return d ? fmtDayKey(d) : '';
};

// 组头格式:「今天 · 07-18」「昨天 · 07-17」「07-16 · 四」
export const dayLabelOf = (key) => {
  if (!key) return '更早';
  const now = new Date();
  const mmdd = `${key.slice(5, 7)}-${key.slice(8, 10)}`;
  if (key === fmtDayKey(now)) return `今天 · ${mmdd}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === fmtDayKey(yesterday)) return `昨天 · ${mmdd}`;
  const d = parseDate(`${key}T00:00:00`);
  return d ? `${mmdd} · ${WEEKDAY_CHARS[d.getDay()]}` : mmdd;
};

// 相对时刻:分钟/小时内相对,超过一周回落到日期(与 utils/datetime.formatRelativeTime 语义一致)
export function formatRelativeTime(raw, fallback = '') {
  const d = parseDate(raw);
  if (!d) return fallback;
  const diff = Date.now() - d.getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} 天前`;
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function formatDateTime(raw) {
  const d = parseDate(raw);
  if (!d) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
