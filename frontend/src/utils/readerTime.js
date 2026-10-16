// 阅读器时间格式化 —— 条目列(ReaderTab)与社交媒体流(SocialFlow)共用。
// 从 ReaderTab 抽出:两处都要按同一时间轴分组并渲染同样的组头,复制一份会漂移。
// 分组轴 = publish_date(原始发布时间,与后端 article_recency_order 的排序主键同轴,
// 组序天然单调);fetched 兜底。曾用 fetched_date 分组,但后端按 publish 排序,
// 二者错位会让组头非单调乱跳(如 7.17→7.15→7.17),故 2026-07 统一到 publish。

export const WEEKDAY_CHARS = ['日', '一', '二', '三', '四', '五', '六'];

export const fmtDayKey = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export const dayKeyOf = (article) => {
  const raw = article?.publish_date || article?.fetched_date;
  if (!raw) return '';
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? '' : fmtDayKey(d);
};

// 组头格式(issue #184):「今天 · 10月13日」「昨天 · 10月12日」「9月15日 周二」;跨年时带年份。
// 中文日期写法,不再用等宽字体的「09-15 · 二」。
export const dayLabelOf = (key) => {
  if (!key) return '更早';
  const now = new Date();
  const d = new Date(`${key}T00:00:00`);
  if (Number.isNaN(d.getTime())) return key;
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  if (key === fmtDayKey(now)) return `今天 · ${md}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === fmtDayKey(yesterday)) return `昨天 · ${md}`;
  const year = d.getFullYear() === now.getFullYear() ? '' : `${d.getFullYear()}年`;
  return `${year}${md} 周${WEEKDAY_CHARS[d.getDay()]}`;
};

// 注:条目列/社交流的相对时刻直接用 utils/datetime.js 的 formatRelativeTime
// (语义已一致:超过一周回落到日期),不在此重复实现。
