// 榜单(issue #221 收敛版,2026-11-03 拍板;设计稿 docs/design/issue-221-rankings/converge.html):
// 热点事件 · 深度阅读 · 播客精选三段的纯逻辑——范围说明、空态、入口副标题、名次变化、徽标叠放、
// 事件详情的发布主体分组与报道时间线(按北京时间分日)。组件只取用,node:test 直接核对。

export const RANKING_SEGMENTS = [
  ['events', '热点事件'],
  ['reads', '深度阅读'],
  ['listens', '播客精选'],
];
export const DEFAULT_RANKING_SEGMENT = 'events';

// 热点事件段内的时间范围:本周 = 近 7 天(168 小时,默认)/ 近 2 天(48 小时)
export const EVENT_RANGES = [
  ['week', '本周'],
  ['recent', '近 2 天'],
];
export const DEFAULT_EVENT_RANGE = 'week';

export function normalizeRankingSegment(value) {
  return RANKING_SEGMENTS.some(([key]) => key === value) ? value : DEFAULT_RANKING_SEGMENT;
}

export function normalizeEventRange(value) {
  return value === 'recent' ? 'recent' : DEFAULT_EVENT_RANGE;
}

export function eventWindowHours(range) {
  return normalizeEventRange(range) === 'recent' ? 48 : 168;
}

export function picksShapeOf(segment) {
  return segment === 'listens' ? 'podcast' : 'article';
}

// 一个视图一份数据:热点事件按时间范围各算一份,另两段各一份(入口副标题的计数也按它记)
export function rankingDataKey(segment, range) {
  const seg = normalizeRankingSegment(segment);
  return seg === 'events' ? `events:${normalizeEventRange(range)}` : seg;
}

// 页头下的范围说明一句(只出现一次;都写明「全站」「与个人订阅无关」——深度阅读 / 播客精选与早报分区同名同尺子)
export function rankingScopeNote(segment, range) {
  const seg = normalizeRankingSegment(segment);
  if (seg === 'reads') return '全站近 7 天按内容价值挑选的论文、教程、观点与汇编，同一件事只留一篇，与个人订阅无关';
  if (seg === 'listens') return '全站近 7 天按内容价值挑选的单集，与个人订阅无关';
  const days = normalizeEventRange(range) === 'recent' ? '2' : '7';
  return `全站近 ${days} 天被两家以上报道的事件，按报道的发布主体数排序，与个人订阅无关`;
}

export function rankingEmptyText(segment, range) {
  const seg = normalizeRankingSegment(segment);
  if (seg === 'reads') return '近 7 天还没有达到门槛的深度内容';
  if (seg === 'listens') return '近 7 天还没有达到门槛的单集';
  return `近 ${normalizeEventRange(range) === 'recent' ? '2' : '7'} 天还没有被两家以上报道的事件`;
}

// 桌面源栏「榜单」入口的副标题:随当前段;没加载过(或空榜)写段名,不写「0 件」
export function rankingEntrySub(segment, count) {
  const seg = normalizeRankingSegment(segment);
  const name = RANKING_SEGMENTS.find(([key]) => key === seg)[1];
  const n = Number(count);
  if (count == null || !Number.isInteger(n) || n <= 0) return name;
  if (seg === 'reads') return `${name} ${n} 篇`;
  if (seg === 'listens') return `${name} ${n} 集`;
  return `${name} ${n} 件`;
}

// ── 时间:详情里的日期时刻与分日一律按北京时间(读者在国内;样页同口径)──
// 没有真实时刻的值(feed 只给日期,后端落 00:00:00)只按原始日期画、不画时刻,也不因换算时区把日期推到别的日子
// (与 utils/datetime.formatPublishDate 同一判定:时刻位缺失或为 00:00)
const BEIJING_OFFSET_MS = 8 * 3600 * 1000;
const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];
const pad2 = (n) => String(n).padStart(2, '0');

function lacksClock(raw) {
  const time = raw.replace('T', ' ').substring(11, 16);
  return !time || time === '00:00';
}

// → { y, m, d, hh, mm, wd, clock };解析失败返回 null。数字时间戳一律按北京墙钟
function beijingParts(value) {
  if (value == null || value === '') return null;
  if (typeof value === 'string' && lacksClock(value)) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
    if (!match) return null;
    const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
    return { y, m, d, hh: 0, mm: 0, wd: new Date(Date.UTC(y, m - 1, d)).getUTCDay(), clock: false };
  }
  const ts = typeof value === 'number' ? value : Date.parse(value);
  if (Number.isNaN(ts)) return null;
  const b = new Date(ts + BEIJING_OFFSET_MS);   // 读 getUTC* 即北京墙钟
  return {
    y: b.getUTCFullYear(), m: b.getUTCMonth() + 1, d: b.getUTCDate(),
    hh: b.getUTCHours(), mm: b.getUTCMinutes(), wd: b.getUTCDay(), clock: true,
  };
}

const keyOf = (p) => `${p.y}-${pad2(p.m)}-${pad2(p.d)}`;
const yearPrefix = (p, now) => (p.y === beijingParts(now).y ? '' : `${p.y}年`);

export function beijingDayKey(value) {
  const p = beijingParts(value);
  return p ? keyOf(p) : '';
}

// 「17:32」;没有真实时刻时为空
export function beijingTime(value) {
  const p = beijingParts(value);
  return p && p.clock ? `${pad2(p.hh)}:${pad2(p.mm)}` : '';
}

// 「10月31日 17:32」;跨年带年份;没有真实时刻只写日期
export function beijingStamp(value, now = Date.now()) {
  const p = beijingParts(value);
  if (!p) return '';
  const date = `${yearPrefix(p, now)}${p.m}月${p.d}日`;
  return p.clock ? `${date} ${pad2(p.hh)}:${pad2(p.mm)}` : date;
}

// 日组头:「今天 · 11月2日」「昨天 · 11月1日」「10月31日 周六」(与条目列的组头同写法,utils/readerTime.dayLabelOf)
export function beijingDayLabel(value, now = Date.now()) {
  const p = beijingParts(value);
  if (!p) return '';
  const md = `${p.m}月${p.d}日`;
  const key = keyOf(p);
  if (key === beijingDayKey(now)) return `今天 · ${md}`;
  if (key === beijingDayKey(now - 86400 * 1000)) return `昨天 · ${md}`;
  return `${yearPrefix(p, now)}${md} 周${WEEKDAYS[p.wd]}`;
}

// 「11月1日」:快照日期(YYYY-MM-DD,本身就是北京日期)
function snapshotDayText(snapshotDate) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(snapshotDate || ''));
  return m ? `${Number(m[2])}月${Number(m[3])}日` : '';
}

// ── 名次变化(拍板 7:对比当天 07:00 快照)──
// previous_rank 有值 → ↑n 绿 / ↓n 红 / 相等留空;previous_rank 为空且有快照 → 「进榜」;没有快照 → 留空。不画「新」签。
export function eventMovement(item, snapshotDate) {
  const rank = Number(item?.rank);
  const prev = item?.previous_rank;
  const day = snapshotDayText(snapshotDate);
  const at = day ? `${day} 07:00` : '上次快照';
  if (prev != null && prev !== '' && Number.isFinite(Number(prev)) && Number.isFinite(rank)) {
    const delta = Number(prev) - rank;
    if (delta > 0) return { label: `↑${delta}`, direction: 'up', title: `较 ${at} 上升 ${delta} 位` };
    if (delta < 0) return { label: `↓${-delta}`, direction: 'down', title: `较 ${at} 下降 ${-delta} 位` };
    return null;
  }
  if (snapshotDate) return { label: '进榜', direction: 'enter', title: `${at} 时不在榜上` };
  return null;
}

// 「近 2 天」签:只在「本周」视图、首见在 48 小时内的事件上画
export function showRecentFlag(range, item) {
  return normalizeEventRange(range) === 'week' && item?.is_recent === true;
}

// 来源徽标叠放:至多 3 枚;超过 3 个时画 2 枚 + 「+N」(N = 余下的个数,与样页 CARDS.stack 同)
export function logoStack(list, max = 3) {
  const items = Array.isArray(list) ? list.filter(Boolean) : [];
  if (items.length <= max) return { shown: items, rest: 0 };
  const shown = items.slice(0, Math.max(1, max - 1));
  return { shown, rest: items.length - shown.length };
}

// ── 事件详情 ──
const timeOf = (member) => member?.report_at || member?.publish_date || '';
const msOf = (member) => {
  const ts = Date.parse(timeOf(member));
  return Number.isNaN(ts) ? -Infinity : ts;
};

// 成员按报道时间倒序(接口已排好;稳定排序兜底)
export function membersByTime(detail) {
  return (detail?.members || [])
    .map((member, index) => ({ member, index }))
    .sort((a, b) => (msOf(b.member) - msOf(a.member)) || (a.index - b.index))
    .map(({ member }) => member);
}

// 代表报道:成员里标了 is_representative 的那篇(带 title_zh / 摘要 / 报道时间),缺了用事件上的 representative 兜底
export function representativeOf(detail) {
  const rep = detail?.representative || {};
  const members = detail?.members || [];
  const member = members.find((m) => m?.is_representative)
    || members.find((m) => rep.article_id && m?.article_id === rep.article_id);
  if (!member && !rep.article_id) return null;
  return { ...rep, ...(member || {}), article_id: member?.article_id || rep.article_id };
}

// 首报 = 成员最早发布时间(接口给 first_report_at;旧数据按成员推)
export function firstReportAt(detail) {
  if (detail?.first_report_at) return detail.first_report_at;
  let best = '';
  let bestMs = Infinity;
  for (const member of detail?.members || []) {
    const raw = member?.publish_date || member?.report_at || '';
    const ts = Date.parse(raw);
    if (!Number.isNaN(ts) && ts < bestMs) { bestMs = ts; best = raw; }
  }
  return best;
}

// 来源 · N 家:按 publisher_key 分组,只写发布主体;同主体多渠道括注其余渠道名;
// 角色有一篇来自官方渠道即「官方」;徽标用主体的代表渠道(publishers[].source_id)
export function publisherGroups(detail) {
  const groups = new Map();
  for (const p of detail?.publishers || []) {
    if (!p?.key || groups.has(p.key)) continue;
    groups.set(p.key, { key: p.key, name: p.name || p.key, sourceId: p.source_id || '', listed: Number(p.count) || 0, items: [] });
  }
  for (const member of detail?.members || []) {
    const key = member?.publisher_key || member?.source_id || '';
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, { key, name: member.source_name || key, sourceId: member.source_id || '', listed: 0, items: [] });
    groups.get(key).items.push(member);
  }
  return [...groups.values()].map((g) => {
    const channels = [...new Set(g.items.map((m) => m.source_name).filter(Boolean))].filter((name) => name !== g.name);
    const role = g.items.some((m) => m.source_role === 'official') ? 'official' : (g.items[0]?.source_role || '');
    return {
      key: g.key,
      name: g.name,
      sourceId: g.sourceId || g.items[0]?.source_id || '',
      channels,
      role,
      count: g.items.length || g.listed,
    };
  });
}

// 报道时间线 · M 篇:按北京时间分日(报道时间倒序),每日一组
export function timelineDays(members, now = Date.now()) {
  const days = [];
  for (const member of members || []) {
    const key = beijingDayKey(timeOf(member)) || 'unknown';
    const last = days[days.length - 1];
    if (last && last.key === key) {
      last.items.push(member);
    } else {
      days.push({ key, label: key === 'unknown' ? '时间未知' : beijingDayLabel(timeOf(member), now), items: [member] });
    }
  }
  return days;
}

// 最新进展:最新一篇不是代表报道时才有
export function latestUpdate(detail) {
  const latest = membersByTime(detail)[0];
  return latest && !latest.is_representative ? latest : null;
}

// 详情眉头:按打开时所在视图与该视图里的名次;不在榜上只写「热点事件」
export function eventKicker(range, rank) {
  const n = Number(rank);
  if (rank == null || rank === '' || !Number.isInteger(n) || n < 1) return '热点事件';
  return `热点事件 · ${normalizeEventRange(range) === 'recent' ? '近 2 天' : '本周'}第 ${n} 件`;
}

// 详情元信息「N 家报道 · M 篇 · 首报 10月31日 17:32」
export function eventMetaParts(detail, now = Date.now()) {
  const parts = [];
  if (Number(detail?.publisher_count) > 0) parts.push(`${detail.publisher_count} 家报道`);
  if (Number(detail?.member_count) > 0) parts.push(`${detail.member_count} 篇`);
  const first = beijingStamp(firstReportAt(detail), now);
  if (first) parts.push(`首报 ${first}`);
  return parts;
}
