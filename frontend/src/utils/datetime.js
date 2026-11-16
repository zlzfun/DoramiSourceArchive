// 共享时间格式化工具。
// 注意：DateRangePicker 内部的 formatDate(Date→字符串) 是日历私有逻辑，语义不同，不在此处。

// ISO 字符串 → 'YYYY-MM-DD HH:mm:ss'（按字符截断，不做时区转换，与后端返回保持一致）。
export function formatDateTime(value, fallback = '-') {
  if (!value) return fallback;
  return value.replace('T', ' ').substring(0, 19);
}

const pad2 = (n) => String(n).padStart(2, '0');

// 带时区的 ISO 字符串 → 浏览器所在时区的 'YYYY-MM-DD HH:mm(:ss)'。
// 读者面专用:发布时间入库是 UTC(+00:00),按字符截断会把北京时间 15:01 显示成 07:01(issue #184)。
// 不带时区的值按本地时间解析,结果与按字符截断一致;解析失败时退回按字符截断。
function formatLocal(value, withSeconds) {
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return value.replace('T', ' ').substring(0, withSeconds ? 19 : 16);
  const d = new Date(ts);
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}${withSeconds ? `:${pad2(d.getSeconds())}` : ''}`;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${hm}`;
}

// feed 没给时刻时后端落的是 00:00:00 假值;只有日期的值同理——两者都「没有真实时刻」
function lacksRealTime(raw) {
  const time = raw.replace('T', ' ').substring(11, 16);
  return !time || time === '00:00';
}

// 发布时间的完整写法(阅读窗署名行的悬停提示),按本地时区;没有真实时刻时同样只给原始日期
// (不补出一个换算出来的「17:00:00」,也不因时区把日期推到前一天,codex R1-P2-04)。
export function formatPublishDateTime(value, fallback = '') {
  if (!value) return fallback;
  const raw = String(value);
  if (lacksRealTime(raw)) return raw.substring(0, 10);
  return formatLocal(raw, true);
}

// 发布日期(阅读窗署名行):没有真实时刻的只画原始日期(画出来像「凌晨发布」,换算时区还会跨日);
// 有真实时刻才按本地时区补 HH:MM。
export function formatPublishDate(value, fallback = '') {
  if (!value) return fallback;
  const raw = String(value);
  if (lacksRealTime(raw)) return raw.substring(0, 10);
  return formatLocal(raw, false);
}

// 读者面桌面的日期写法(issue #279 第三批):「11月10日 07:14」,跨年才带年份;没有真实时刻的只写到日。
// 连字符写法是给台账看的;读者面桌面逐处往这个写法收(社交流 / 早报 / 榜单还没收)。withYear 用于悬停提示(总是带年份)。
export function formatReaderDate(value, { withYear = false, fallback = '' } = {}) {
  if (!value) return fallback;
  const raw = String(value);
  const bare = lacksRealTime(raw);
  // 没有真实时刻的值按字面日期取,不过时区(同 formatPublishDate:换算会把日期推到前一天)
  const d = bare ? new Date(`${raw.substring(0, 10)}T00:00:00`) : new Date(Date.parse(raw));
  if (Number.isNaN(d.getTime())) return raw.substring(0, 10) || fallback;
  const year = withYear || d.getFullYear() !== new Date().getFullYear() ? `${d.getFullYear()}年` : '';
  const day = `${year}${d.getMonth() + 1}月${d.getDate()}日`;
  return bare ? day : `${day} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// ISO 字符串 → 相对时间（刚刚 / N 分钟前 / N 小时前 / N 天前 / MM-DD / YYYY-MM-DD）。
// cn:七天以外写「11月3日」/「2025年11月3日」(读者面桌面,issue #279 第三批);缺省仍是连字符写法(管理面、手机壳)。
export function formatRelativeTime(value, fallback = '从未运行', { cn = false } = {}) {
  if (!value) return fallback;
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return formatDateTime(value, fallback);
  const diffSec = Math.floor((Date.now() - ts) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} 天前`;
  const d = new Date(ts);
  if (cn) return `${d.getFullYear() === new Date().getFullYear() ? '' : `${d.getFullYear()}年`}${d.getMonth() + 1}月${d.getDate()}日`;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  if (d.getFullYear() === new Date().getFullYear()) return `${mm}-${dd}`;
  return `${d.getFullYear()}-${mm}-${dd}`;
}
