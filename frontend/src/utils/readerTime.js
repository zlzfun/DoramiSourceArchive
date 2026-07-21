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

// 样页组头格式:「今天 · 07-18」「昨天 · 07-17」「07-16 · 四」
export const dayLabelOf = (key) => {
  if (!key) return '更早';
  const now = new Date();
  const mmdd = `${key.slice(5, 7)}-${key.slice(8, 10)}`;
  if (key === fmtDayKey(now)) return `今天 · ${mmdd}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === fmtDayKey(yesterday)) return `昨天 · ${mmdd}`;
  const d = new Date(`${key}T00:00:00`);
  return Number.isNaN(d.getTime()) ? mmdd : `${mmdd} · ${WEEKDAY_CHARS[d.getDay()]}`;
};

// 注:条目列/社交流的相对时刻直接用 utils/datetime.js 的 formatRelativeTime
// (语义已一致:超过一周回落到日期),不在此重复实现。
