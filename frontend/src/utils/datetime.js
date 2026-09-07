// 共享时间格式化工具。
// 注意：DateRangePicker 内部的 formatDate(Date→字符串) 是日历私有逻辑，语义不同，不在此处。

// ISO 字符串 → 'YYYY-MM-DD HH:mm:ss'（按字符截断，不做时区转换，与后端返回保持一致）。
export function formatDateTime(value, fallback = '-') {
  if (!value) return fallback;
  return value.replace('T', ' ').substring(0, 19);
}

// 发布日期(阅读窗署名行):只画日期;feed 没给时刻时后端落的是 00:00:00 假值,
// 画出来像"凌晨发布",有真实时刻才补 HH:MM。
export function formatPublishDate(value, fallback = '') {
  if (!value) return fallback;
  const text = value.replace('T', ' ');
  const date = text.substring(0, 10);
  const time = text.substring(11, 16);
  return time && time !== '00:00' ? `${date} ${time}` : date;
}

// ISO 字符串 → 相对时间（刚刚 / N 分钟前 / N 小时前 / N 天前 / MM-DD / YYYY-MM-DD）。
export function formatRelativeTime(value, fallback = '从未运行') {
  if (!value) return fallback;
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return formatDateTime(value, fallback);
  const diffSec = Math.floor((Date.now() - ts) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} 天前`;
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  if (d.getFullYear() === new Date().getFullYear()) return `${mm}-${dd}`;
  return `${d.getFullYear()}-${mm}-${dd}`;
}
