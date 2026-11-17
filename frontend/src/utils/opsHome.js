// 运维首页(issue #114)的纯数据推导:窗口切片、读者 / 系统用途拆分、播客阶段构成、待处理清单、全站开关点。
// 输入是各接口的原样响应(未加载 = null / undefined),输出给 OpsHome 渲染;无 React 依赖(frontend/test/opsHome.test.mjs)。

import { syncPendingItem } from './syncCatalog.js';

// 读者面 AI 日预算只计这几类用途,与 src/services/accounts.READER_AI_BUDGET_PURPOSES 同步(测试读后端源码比对)。
export const READER_AI_PURPOSES = ['translate', 'ask', 'summarize', 'podcast_ondemand', 'article_ondemand'];

const pad = (n) => String(n).padStart(2, '0');
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// 窗口内的本地日期(旧 → 新,含末日)。asOf = 'YYYY-MM-DD'(服务端今天)优先,缺省取浏览器今天。
export function windowDays(days, asOf = '') {
  const n = Math.max(1, Math.floor(Number(days) || 1));
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(asOf || '');
  const end = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date();
  const out = [];
  for (let i = n - 1; i >= 0; i -= 1) {
    const d = new Date(end.getFullYear(), end.getMonth(), end.getDate() - i);
    out.push(isoDay(d));
  }
  return out;
}

// 账户增长窗口:逐日新增 + 窗口起点存量(阶梯线起点)+ 窗口新增合计。口径 = 现存账户(account-growth)。
export function growthWindow(growth, days) {
  if (!growth) return null;
  const dayList = windowDays(days, growth.as_of_day);
  const byDay = new Map((growth.series || []).map((r) => [r.day, Number(r.new) || 0]));
  const news = dayList.map((d) => byDay.get(d) || 0);
  const added = news.reduce((a, b) => a + b, 0);
  const total = Number(growth.totals?.accounts) || 0;
  return { days: dayList, news, added, start: Math.max(0, total - added), total };
}

// AI 用量拆成「读者用途 / 系统用途」两组逐日调用(补零),并给窗口合计与读者占比。
export function aiReaderSplit(usage, days) {
  if (!usage) return null;
  const dayList = windowDays(days);
  const idx = new Map(dayList.map((d, i) => [d, i]));
  const rows = dayList.map(() => [0, 0]);
  const reader = new Set(READER_AI_PURPOSES);
  for (const r of usage.by_day_purpose || []) {
    const i = idx.get(r.day);
    if (i === undefined) continue;
    rows[i][reader.has(r.purpose) ? 0 : 1] += Number(r.calls) || 0;
  }
  const readerCalls = (usage.by_purpose || []).filter((p) => reader.has(p.purpose)).reduce((a, p) => a + (Number(p.calls) || 0), 0);
  const totalCalls = Number(usage.totals?.calls) || 0;
  return {
    days: dayList,
    rows,
    totalCalls,
    totalTokens: Number(usage.totals?.total_tokens) || 0,
    readerShare: totalCalls ? readerCalls / totalCalls : 0,
  };
}

// 播客单集阶段构成(podcast-premium-tasks 的 breakdown.stage):五份,背景量(未入选 / 未初评)排在后面、色最淡。
export function stageParts(stage) {
  if (!stage) return [];
  const n = (k) => Number(stage[k]) || 0;
  return [
    { key: 'done', name: '全文完成', value: n('full_analyzed'), tone: 'ok' },
    {
      key: 'run', name: '进行中', value: n('awaiting_transcript') + n('processing') + n('retry_wait') + n('reconciliation'), tone: 'run',
      detail: [['待全文', n('awaiting_transcript')], ['处理中', n('processing')], ['等待重试', n('retry_wait')], ['待对账', n('reconciliation')]],
    },
    { key: 'idle', name: '未入选', value: n('not_selected'), tone: 'idle' },
    { key: 'new', name: '未初评', value: n('not_processed'), tone: 'new' },
    { key: 'failed', name: '失败', value: n('failed'), tone: 'bad' },
  ];
}

// 预算占比(0 = 不限 → null)。
export function budgetRatio(aiGlobal) {
  const budget = Number(aiGlobal?.daily_token_budget) || 0;
  if (!budget) return null;
  return (Number(aiGlobal?.tokens_used_today) || 0) / budget;
}

const pct = (x) => `${Math.round(x * 100)}%`;

// 待处理清单:按严重度排序(bad 在前,warn 在后);每项落到所在页的板块。未加载的接口不产生项。
// snap 的键:storage / podTasks / podStats / home / aiGlobal / usrc / feedback / taxonomy / xQuota / asr。
export function pendingItems(snap = {}) {
  const items = [];
  const push = (tone, key, name, value, page, block) => items.push({ tone, key, name, value, page, block });
  // 节点同步失败(issue #261):告警只在「状态」磁贴出现一次,排在最前,点了落到节点同步 · 运行记录
  const sync = syncPendingItem(snap.home?.sync);
  if (sync) items.push(sync);
  if (snap.storage?.backup?.status === 'failed') push('bad', 'backup', '备份未完成', '', 'external', 'storage');
  const podFailed = Number(snap.podTasks?.breakdown?.stage?.failed) || 0;
  if (podFailed) push('bad', 'podcast-failed', '播客失败', podFailed, 'podcast', 'tasks');
  const backfillFailed = Number(snap.home?.backfills?.unresolved_failed) || 0;
  if (backfillFailed) push('bad', 'backfill', '回填失败', backfillFailed, 'analysis', 'backfill');
  if (snap.podStats?.storage_pressure) push('bad', 'podcast-storage', '播客存储保护', '', 'podcast', 'overview');
  const ratio = budgetRatio(snap.aiGlobal);
  if (ratio !== null && ratio >= 0.8) push('warn', 'budget', '读者 AI 预算', pct(ratio), 'reader', 'switches');
  const xBudget = Number(snap.xQuota?.monthly_budget_usd) || 0;
  const xRatio = xBudget ? (Number(snap.xQuota?.estimated_cost_usd) || 0) / xBudget : 0;
  if (snap.xQuota?.blocked || xRatio >= 0.8) push('warn', 'xapi', 'X API 预算', pct(Math.min(1, xRatio)), 'external', 'xapi');
  if (snap.asr?.usage_status === 'frozen') push('warn', 'asr', 'ASR 配额冻结', '', 'podcast', 'asr');
  const usrcFailing = Number(snap.usrc?.kpi?.failing_count) || 0;
  if (usrcFailing) push('warn', 'usrc', '自定源失败', usrcFailing, 'user-sources', 'sources');
  const fbOpen = Number(snap.feedback?.counts?.open) || 0;
  if (fbOpen) push('warn', 'feedback', '反馈待处理', fbOpen, 'inbox', 'feedback');
  const candidates = Number(snap.taxonomy?.unresolved_candidate_count) || 0;
  if (candidates) push('warn', 'candidates', '标签候选', candidates, 'analysis', 'catalog');
  return items;
}

// 磁贴状态点:某页有待处理时取最严重的一档。
export function pageMarks(items) {
  const marks = {};
  for (const it of items) {
    if (marks[it.page] !== 'bad') marks[it.page] = it.tone;
  }
  return marks;
}

// 全站 8 个总闸(首页只读点,点击跳所在板块)。on = null 表示还没读到。
export function switchDots(snap = {}) {
  const flag = (v) => (v === undefined || v === null ? null : Boolean(v));
  const flags = snap.analysis?.feature_flags;
  return [
    { key: 'ai', name: '用户 AI', on: flag(snap.aiGlobal?.enabled), page: 'reader', block: 'switches' },
    { key: 'ondemand', name: '读者点播', on: flag(snap.ondemand?.enabled), page: 'reader', block: 'switches' },
    { key: 'share', name: '公开分享', on: flag(snap.share?.enabled), page: 'reader', block: 'switches' },
    { key: 'usrc', name: '自定源', on: flag(snap.usrc?.config?.enabled), page: 'user-sources', block: 'sources' },
    { key: 'guest', name: '游客访问', on: flag(snap.guest?.settings?.enabled), page: 'accounts', block: 'guest' },
    { key: 'guestAi', name: '游客 AI', on: flag(snap.guest?.settings?.ai_enabled), page: 'accounts', block: 'guest' },
    { key: 'analysis', name: '文章分析', on: flag(flags?.article_analysis_enabled), page: 'analysis', block: 'metrics' },
    { key: 'digest', name: '个人早报', on: flag(flags?.personal_digest_enabled), page: 'analysis', block: 'metrics' },
  ];
}

// 媒体库占用空间可读化(去重后落盘字节):返回 [数, 单位],首页把单位排成小字。
export function bytesParts(n) {
  if (!n) return ['0', 'B'];
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(n);
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return [value >= 100 ? String(Math.round(value)) : value.toFixed(1), units[i]];
}

// 各源阅读 Top N:有聚合端点给窗口内排行;否则退回 /admin/content 的累计值(整表返回,前端排序)。
export function readTop(home, content, k) {
  if (home?.reads?.by_source) {
    return { windowed: true, rows: home.reads.by_source.slice(0, k).map((r) => ({ name: r.name || r.source_id, value: Number(r.reads) || 0 })) };
  }
  const rows = [...(content?.sources || [])]
    .filter((s) => (s.read_count || 0) > 0)
    .sort((a, b) => (b.read_count - a.read_count) || String(a.name).localeCompare(String(b.name)))
    .slice(0, k)
    .map((s) => ({ name: s.name || s.source_id, value: Number(s.read_count) || 0 }));
  return { windowed: false, rows };
}
