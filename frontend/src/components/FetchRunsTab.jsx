import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ChevronRight,
  Plus,
  RefreshCw,
  RotateCw,
  Save,
  Search,
  X,
} from 'lucide-react';
import {
  createCollectionJob,
  deleteCollectionJob,
  fetchCollectionJobRuns,
  fetchCollectionJobs,
  fetchDailyStats,
  fetchFetchRuns,
  runCollectionJob,
  triggerFetch,
  updateCollectionJob,
} from '../api';
import LogoMark from './LogoMark';
import Modal from './Modal';
import { groupBySection, resolveCompany } from '../sourceTaxonomy';
import { runAction } from '../utils/runAction';
import { useConfirm } from '../hooks/useConfirm';
import { TEST_RUN_LIMIT, normalizeIds, collectionRunMessage } from '../utils/collection';

const PAGE_SIZE = 50;
const POLL_SECONDS = 30; // 静默轮询间隔:页面激活时后台拉取,无 UI 开关(自动刷新按钮已退役)
const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

// 状态 → 状态章(.stamp)映射:success/partial_failed/failed/running。
const STAMP = {
  success: ['stamp-ok', '成功'],
  partial_failed: ['stamp-warn', '部分失败'],
  failed: ['stamp-bad', '失败'],
  running: ['stamp-run', '运行中'],
};
function stampFor(status) {
  return STAMP[status] || ['stamp-idle', status || '未知'];
}
// 状态 → 形状点类(时刻表微章 / 子节点 tick)。
function tickFor(status) {
  if (status === 'success') return 'ok';
  if (status === 'partial_failed') return 'warn';
  if (status === 'failed') return 'bad';
  if (status === 'running') return 'run';
  return '';
}
// 7 日点阵严重度:失败 > 部分失败 > 成功。
const SEVERITY = { failed: 3, partial_failed: 2, success: 1, running: 1 };
const SEV_CLASS = { 3: 'bad', 2: 'warn', 1: 'ok', 0: '' };

const pad = n => String(n).padStart(2, '0');

function dayKey(iso) {
  return iso ? String(iso).substring(0, 10) : '';
}
function localDayStr(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
function formatClock(ms) {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function formatRunTime(iso) {
  if (!iso) return '--:--';
  return String(iso).substring(11, 16);
}
function formatCountdown(targetIso, nowMs) {
  const target = Date.parse(targetIso);
  if (Number.isNaN(target)) return null;
  let s = Math.max(0, Math.round((target - nowMs) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  s %= 60;
  return `${h}:${pad(m)}:${pad(s)}`;
}
function formatDuration(durationMs) {
  if (durationMs === null || durationMs === undefined) return '—';
  if (durationMs < 1000) return `${durationMs} ms`;
  const sec = durationMs / 1000;
  if (sec < 60) return `${sec.toFixed(1)} s`;
  return `${(sec / 60).toFixed(1)} min`;
}
function parseParams(paramsJson) {
  if (!paramsJson) return {};
  try {
    const value = typeof paramsJson === 'string' ? JSON.parse(paramsJson) : paramsJson;
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

// stats 端点行(day×计数分列)→ 7 日点阵(旧→新 7 格,精确口径;A 每日聚合端点波)。
// worst:failed>0→bad、partial>0→warn、当日有运行→ok、无→空格。
function sevenDayDotsFromStats(rows) {
  const days = [];
  const base = new Date();
  base.setHours(0, 0, 0, 0);
  for (let i = 6; i >= 0; i -= 1) {
    const d = new Date(base);
    d.setDate(d.getDate() - i);
    days.push(localDayStr(d));
  }
  const byDay = {};
  rows.forEach(r => {
    let sev = 0;
    if (r.failed > 0) sev = 3;
    else if (r.partial > 0) sev = 2;
    else if (r.runs > 0) sev = 1;
    byDay[r.day] = Math.max(byDay[r.day] || 0, sev);
  });
  return days.map(k => SEV_CLASS[byDay[k] || 0]);
}

// 已加载运行按日聚合最差状态,得出 7 日点阵(旧→新 7 格)与最近一次结果。
// (stats 拉取失败时的窗口口径回退。)
function sevenDayDots(runs) {
  const days = [];
  const base = new Date();
  base.setHours(0, 0, 0, 0);
  for (let i = 6; i >= 0; i -= 1) {
    const d = new Date(base);
    d.setDate(d.getDate() - i);
    days.push(localDayStr(d));
  }
  const byDay = {};
  runs.forEach(run => {
    const k = dayKey(run.started_at);
    if (!k) return;
    byDay[k] = Math.max(byDay[k] || 0, SEVERITY[run.status] || 0);
  });
  return days.map(k => SEV_CLASS[byDay[k] || 0]);
}
function latestStatus(runs) {
  if (!runs.length) return null;
  return runs.reduce((best, run) =>
    String(run.started_at || '') > String(best.started_at || '') ? run : best
  ).status;
}

function scopeLabel(scope) {
  if (scope === 'saved_job') return '采集任务';
  if (scope === 'legacy_task') return '旧版计划';
  return '临时抓取';
}
function normalizeCollectorDisplayName(value) {
  return String(value || '').replaceAll('节点组', '采集范围');
}

function blankJob() {
  return {
    name: '',
    description: '',
    fetcher_ids: [],
    params: {},
    per_fetcher_params: {},
    cron_expr: '',
    is_active: true,
    downstream_policy: {},
  };
}
function defaultParamsFor(fetcher) {
  const params = {};
  (fetcher?.parameters || []).forEach(param => {
    params[param.field] = param.default ?? '';
  });
  return params;
}
// ── cron 粗解析(仅编辑器回显 + 距下次预览,不做校验) ──
// 支持常见五段式:全 *、数字、*/N、逗号列表、a-b 范围(可带 /step)、星期数字/范围。
// 任一字段不认识 → 诚实降级(不显示人话/距下次)。
function parseCronField(raw, min, max) {
  const out = new Set();
  for (const token of String(raw).split(',')) {
    const t = token.trim();
    if (t === '') return null;
    let m;
    if (t === '*') {
      for (let v = min; v <= max; v += 1) out.add(v);
    } else if ((m = t.match(/^\*\/(\d+)$/))) {
      const step = Number(m[1]);
      if (!step) return null;
      for (let v = min; v <= max; v += step) out.add(v);
    } else if ((m = t.match(/^(\d+)-(\d+)(?:\/(\d+))?$/))) {
      const a = Number(m[1]);
      const b = Number(m[2]);
      const step = m[3] ? Number(m[3]) : 1;
      if (!step || a > b || a < min || b > max) return null;
      for (let v = a; v <= b; v += step) out.add(v);
    } else if ((m = t.match(/^(\d+)$/))) {
      const v = Number(m[1]);
      if (v < min || v > max) return null;
      out.add(v);
    } else {
      return null;
    }
  }
  return [...out].sort((a, b) => a - b);
}

function parseCron(expr) {
  const trimmed = String(expr || '').trim();
  if (!trimmed) return { kind: 'manual' };
  const parts = trimmed.split(/\s+/);
  if (parts.length !== 5) return { kind: 'unparsed' };
  const minute = parseCronField(parts[0], 0, 59);
  const hour = parseCronField(parts[1], 0, 23);
  const dom = parseCronField(parts[2], 1, 31);
  const month = parseCronField(parts[3], 1, 12);
  let dow = parseCronField(parts[4], 0, 7);
  if (!minute || !hour || !dom || !month || !dow) return { kind: 'unparsed' };
  // cron 星期 7 归一为 0(周日)
  dow = [...new Set(dow.map(d => (d === 7 ? 0 : d)))].sort((a, b) => a - b);
  const star = {
    minute: parts[0].trim() === '*',
    hour: parts[1].trim() === '*',
    dom: parts[2].trim() === '*',
    month: parts[3].trim() === '*',
    dow: parts[4].trim() === '*',
  };
  return { kind: 'cron', fields: { minute, hour, dom, month, dow }, star, raw: parts };
}

function cronDayMatches(info, date) {
  const { fields, star } = info;
  if (!fields.month.includes(date.getMonth() + 1)) return false;
  const domOk = fields.dom.includes(date.getDate());
  const dowOk = fields.dow.includes(date.getDay());
  if (!star.dom && !star.dow) return domOk || dowOk; // 两者都限定 → 任一命中(cron 语义)
  if (!star.dom) return domOk;
  if (!star.dow) return dowOk;
  return true;
}

function nextCronTime(info, fromMs) {
  const cursor = new Date(fromMs);
  cursor.setSeconds(0, 0);
  cursor.setMinutes(cursor.getMinutes() + 1);
  const cursorMs = cursor.getTime();
  for (let day = 0; day < 367; day += 1) {
    const probe = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + day);
    if (!cronDayMatches(info, probe)) continue;
    for (const h of info.fields.hour) {
      for (const mm of info.fields.minute) {
        const cand = new Date(probe.getFullYear(), probe.getMonth(), probe.getDate(), h, mm);
        if (cand.getTime() >= cursorMs) return cand.getTime();
      }
    }
  }
  return null;
}

function cronHuman(info) {
  const { fields, star } = info;
  const single = (arr) => (arr.length === 1 ? arr[0] : null);
  const M = single(fields.minute);
  const H = single(fields.hour);
  const hhmm = H != null && M != null ? `${pad(H)}:${pad(M)}` : null;
  const minStep = info.raw[0].trim().match(/^\*\/(\d+)$/);
  const hourStep = info.raw[1].trim().match(/^\*\/(\d+)$/);
  const dateFree = star.dom && star.month && star.dow;
  if (star.minute && star.hour && dateFree) return '每分钟';
  if (minStep && star.hour && dateFree) return `每 ${minStep[1]} 分钟`;
  if (M != null && hourStep && dateFree) return M === 0 ? `每 ${hourStep[1]} 小时` : `每 ${hourStep[1]} 小时(第 ${M} 分)`;
  if (M != null && star.hour && dateFree) return `每小时第 ${pad(M)} 分`;
  if (hhmm && dateFree) return `每天 ${hhmm}`;
  if (hhmm && star.dom && star.month && !star.dow) {
    const key = fields.dow.join(',');
    if (key === '1,2,3,4,5') return `工作日 ${hhmm}`;
    if (key === '0,6') return `周末 ${hhmm}`;
    return `${fields.dow.map(d => `周${WEEKDAYS[d]}`).join('、')} ${hhmm}`;
  }
  if (hhmm && !star.dom && star.month && star.dow) return `每月 ${fields.dom.join('、')} 日 ${hhmm}`;
  return null;
}

// 编辑器读数:人话 + 下次触发毫秒(拿不准 → 诚实降级文案 + 无距下次)。
function readCron(expr) {
  const info = parseCron(expr);
  if (info.kind === 'manual') return { human: '手动触发,无定时', nextMs: null };
  if (info.kind === 'unparsed') return { human: '保存后按调度器口径生效', nextMs: null };
  return { human: cronHuman(info) || '已按 cron 定时', nextMs: nextCronTime(info, Date.now()) };
}

export default function FetchRunsTab({
  availableFetchers,
  showToast,
  onArticlesChanged,
  onRunsChanged,
  isActive = true,
  runsDirty = false,
  onRunsRefreshed,
  pendingFilter,
  onPendingFilterApplied,
  pendingJobDraft,
  onPendingJobDraftApplied,
}) {
  const confirm = useConfirm();
  const loadRequestRef = useRef(0);
  const [collectionJobs, setCollectionJobs] = useState([]);
  const [collectionRuns, setCollectionRuns] = useState([]);
  const [fetchRuns, setFetchRuns] = useState([]);
  const [daily, setDaily] = useState(null); // GET /api/stats/daily(精确聚合;null=回退窗口口径)
  const [windowDays, setWindowDays] = useState(30); // 时间窗(近 N 天,总账条+流水行集统一口径)
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  // 服务端参数:仅 fetcher_id(来自节点页跳转);状态/触发/对象一律本地过滤。
  const [serverFetcherId, setServerFetcherId] = useState('');
  const [statusFilter, setStatusFilter] = useState(''); // '' | running | success | partial_failed | failed
  const [triggerFilter, setTriggerFilter] = useState(''); // '' | manual | scheduled
  const [selectedJobId, setSelectedJobId] = useState(null); // number | 'adhoc' | null
  const [expandedKeys, setExpandedKeys] = useState(() => new Set());
  const [page, setPage] = useState(0);

  // 台面时钟 + 静默轮询:每秒走字(非动画),同一 interval 每 POLL_SECONDS 静默拉取。
  const [nowMs, setNowMs] = useState(() => Date.now());
  const pollTickRef = useRef(POLL_SECONDS);

  const [jobModalOpen, setJobModalOpen] = useState(false);
  const [editingJobId, setEditingJobId] = useState(null);
  const [jobDraft, setJobDraft] = useState(blankJob());
  const [jobSearch, setJobSearch] = useState('');

  const fetchersById = useMemo(
    () => Object.fromEntries(availableFetchers.map(fetcher => [fetcher.id, fetcher])),
    [availableFetchers]
  );
  const jobsById = useMemo(
    () => Object.fromEntries(collectionJobs.map(job => [job.id, job])),
    [collectionJobs]
  );
  const getFetcherName = useCallback((id) => fetchersById[id]?.name || id, [fetchersById]);
  const companyForId = useCallback(
    (id) => resolveCompany(fetchersById[id] || { source_owner: '', base_url: '' }),
    [fetchersById]
  );

  const draftFetcherIds = useMemo(() => jobDraft.fetcher_ids || [], [jobDraft.fetcher_ids]);
  const filteredModalFetchers = useMemo(() => {
    const query = jobSearch.trim().toLowerCase();
    if (!query) return availableFetchers;
    return availableFetchers.filter(fetcher =>
      [fetcher.name, fetcher.id, fetcher.desc].filter(Boolean).join(' ').toLowerCase().includes(query)
    );
  }, [availableFetchers, jobSearch]);

  // 目录同款分组(节点页 groupBySection):板块 → 公司 → 节点。
  const groupedCatalog = useMemo(() => groupBySection(filteredModalFetchers), [filteredModalFetchers]);

  // 点名册组全选/清空:整组编入(补默认参数)或整组移出;已改参数保留(重勾恢复,防误触丢配置)。
  const setGroupChecked = (fetchers, add) => {
    setJobDraft(prev => {
      const cur = new Set(prev.fetcher_ids || []);
      const perParams = { ...(prev.per_fetcher_params || {}) };
      fetchers.forEach(f => {
        if (add) {
          if (!cur.has(f.id)) {
            cur.add(f.id);
            perParams[f.id] = perParams[f.id] || defaultParamsFor(f);
          }
        } else {
          cur.delete(f.id);
        }
      });
      return { ...prev, fetcher_ids: normalizeIds([...cur]), per_fetcher_params: perParams };
    });
  };

  // 编辑器读数:整体 cron 人话 + 距下次(距下次每秒随台面时钟走字)。
  const cronInfo = useMemo(() => readCron(jobDraft.cron_expr), [jobDraft.cron_expr]);
  const cronCountdown = cronInfo.nextMs != null
    ? formatCountdown(new Date(cronInfo.nextMs).toISOString(), nowMs)
    : null;

  // 编排单计数:参数改动数(与 schema default 不同的字段)。
  const paramChangeCount = useMemo(() => {
    let n = 0;
    draftFetcherIds.forEach(id => {
      const params = (jobDraft.per_fetcher_params || {})[id] || {};
      (fetchersById[id]?.parameters || []).forEach(param => {
        const def = param.default ?? '';
        const value = params[param.field] ?? def;
        if (String(value) !== String(def)) n += 1;
      });
    });
    return n;
  }, [draftFetcherIds, jobDraft.per_fetcher_params, fetchersById]);

  const loadAll = useCallback(async () => {
    const reqId = ++loadRequestRef.current;
    setLoading(true);
    try {
      // 拉取不带 status/trigger 服务端参数(总账条计数不随本地筛选塌缩);仅 fetcher_id 保留。
      // stats 为精确聚合口径(点阵/总账条),拉取失败降级 null → 各消费点回退窗口口径。
      const [jobs, jobRuns, nodeRuns, stats] = await Promise.all([
        fetchCollectionJobs(),
        fetchCollectionJobRuns({}, 100),
        fetchFetchRuns({ fetcher_id: serverFetcherId }, 200),
        fetchDailyStats(windowDays).catch(() => null),
      ]);
      if (reqId !== loadRequestRef.current) return;
      setCollectionJobs(jobs);
      setCollectionRuns(jobRuns);
      setFetchRuns(nodeRuns);
      setDaily(stats);
      setLoadError('');
    } catch (e) {
      if (reqId !== loadRequestRef.current) return;
      setLoadError(e.message || '任务与运行数据加载失败,请确认后端服务已启动后重试');
      showToast(e.message || '任务与运行数据加载失败,请确认后端服务已启动后重试', 'error');
    } finally {
      if (reqId === loadRequestRef.current) setLoading(false);
    }
  }, [serverFetcherId, windowDays, showToast]);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => {
    if (isActive && runsDirty) {
      loadAll();
      onRunsRefreshed?.();
    }
  }, [isActive, runsDirty, loadAll, onRunsRefreshed]);

  // 台面时钟 + 静默轮询:单一 1s interval。仅在本页激活时走字(隐藏页不空转重渲染);
  // 数据自动更新对用户无感——没有开关、没有倒计时,和寻常网页一样默默保持最新。
  useEffect(() => {
    if (!isActive) return undefined;
    pollTickRef.current = POLL_SECONDS;
    const timer = setInterval(() => {
      setNowMs(Date.now());
      pollTickRef.current -= 1;
      if (pollTickRef.current <= 0) {
        pollTickRef.current = POLL_SECONDS;
        loadAll();
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [isActive, loadAll]);

  // 节点页跳转:落 fetcher_id(服务端)+ status(本地);清空任务选择。
  useEffect(() => {
    if (!pendingFilter) return;
    setServerFetcherId(pendingFilter.fetcher_id || '');
    setStatusFilter(pendingFilter.status || '');
    setSelectedJobId(null);
    onPendingFilterApplied?.();
  }, [pendingFilter, onPendingFilterApplied]);

  // 节点页「保存为采集任务」草稿:本地打开新建编辑器预填。
  useEffect(() => {
    if (!pendingJobDraft) return;
    setEditingJobId(null);
    setJobDraft({ ...blankJob(), ...pendingJobDraft });
    setJobSearch('');
    setJobModalOpen(true);
    onPendingJobDraftApplied?.();
  }, [pendingJobDraft, onPendingJobDraftApplied]);

  // 筛选变化重置分页与折叠。
  useEffect(() => {
    setPage(0);
    setExpandedKeys(new Set());
  }, [serverFetcherId, statusFilter, triggerFilter, selectedJobId]);

  // ── 数据推导 ──

  // 子节点运行按父运行 ID 分组(窗口内 fetchRuns)。
  const childrenByParent = useMemo(() => {
    const map = new Map();
    fetchRuns.forEach(run => {
      if (!run.job_run_id) return;
      if (!map.has(run.job_run_id)) map.set(run.job_run_id, []);
      map.get(run.job_run_id).push(run);
    });
    return map;
  }, [fetchRuns]);

  const nodeRunsWithoutParent = useMemo(
    () => fetchRuns.filter(run => !run.job_run_id),
    [fetchRuns]
  );

  // 任务级运行 → 各任务的运行集(用于时刻表最近结果 / 7 日点阵)。
  const runsByJobId = useMemo(() => {
    const map = new Map();
    collectionRuns.forEach(run => {
      if (run.job_id == null) return;
      if (!map.has(run.job_id)) map.set(run.job_id, []);
      map.get(run.job_id).push(run);
    });
    return map;
  }, [collectionRuns]);

  // 临时运行集(时刻表汇总行 + 「对象:临时」):非 saved_job 的任务级 + 全部无父节点级。
  const adhocRuns = useMemo(
    () => [
      ...collectionRuns.filter(run => run.run_scope !== 'saved_job'),
      ...nodeRunsWithoutParent,
    ],
    [collectionRuns, nodeRunsWithoutParent]
  );

  // 时刻表分组:活跃有 next 按升序 → 活跃手动按名称 → 临时汇总 → 已停用。
  // 点阵/今日次数优先吃 stats 精确口径(A 波),端点不可用时回退已加载窗口;
  // 上次结果点保持窗口口径(要的是「最近一条」的即时状态,含 running)。
  const timetable = useMemo(() => {
    const statsByJob = new Map();
    const statsAdhoc = [];
    if (daily) {
      daily.runs.forEach(r => {
        if (r.scope !== 'saved_job') statsAdhoc.push(r);
        if (r.job_id == null) return;
        if (!statsByJob.has(r.job_id)) statsByJob.set(r.job_id, []);
        statsByJob.get(r.job_id).push(r);
      });
      daily.solo.forEach(r => statsAdhoc.push(r));
    }
    const withNext = [];
    const manual = [];
    const disabled = [];
    collectionJobs.forEach(job => {
      const runs = runsByJobId.get(job.id) || [];
      const entry = {
        kind: 'job',
        id: job.id,
        job,
        name: job.name,
        nodeCount: (job.fetcher_ids || []).length,
        cron: job.cron_expr || '',
        nextRunAt: job.next_run_at || null,
        isActive: job.is_active !== false,
        lastStatus: latestStatus(runs),
        dots: daily ? sevenDayDotsFromStats(statsByJob.get(job.id) || []) : sevenDayDots(runs),
      };
      if (!entry.isActive) disabled.push(entry);
      else if (entry.nextRunAt) withNext.push(entry);
      else manual.push(entry);
    });
    withNext.sort((a, b) => String(a.nextRunAt).localeCompare(String(b.nextRunAt)));
    manual.sort((a, b) => String(a.name).localeCompare(String(b.name)));

    const todayKey = localDayStr(new Date());
    const adhocEntry = {
      kind: 'adhoc',
      id: 'adhoc',
      name: '临时抓取',
      todayCount: daily
        ? statsAdhoc.filter(r => r.day === todayKey).reduce((acc, r) => acc + (r.runs || 0), 0)
        : adhocRuns.filter(run => dayKey(run.started_at) === todayKey).length,
      lastStatus: latestStatus(adhocRuns),
      dots: daily ? sevenDayDotsFromStats(statsAdhoc) : sevenDayDots(adhocRuns),
    };
    return { active: [...withNext, ...manual], adhoc: adhocEntry, disabled };
  }, [collectionJobs, runsByJobId, adhocRuns, daily]);

  // 统一运行流水:任务级 + 无 job_run_id 的节点级,按 started_at 降序。
  // 口径统一:行集与总账条同为「近 windowDays 天」——否则统计(stats 精确)与
  // 列表(加载窗口可含更早行)数字对不上;时间窗页头可调(7/14/30/90,与运维页同档)。
  const unifiedRuns = useMemo(() => {
    const cutoff = new Date();
    cutoff.setHours(0, 0, 0, 0);
    cutoff.setDate(cutoff.getDate() - (windowDays - 1));
    const cutoffKey = localDayStr(cutoff);
    const inWindow = run => (dayKey(run.started_at) || '') >= cutoffKey;
    const rows = [];
    collectionRuns.filter(inWindow).forEach(run => {
      const job = run.job_id ? jobsById[run.job_id] : null;
      const rawName = normalizeCollectorDisplayName(run.name || job?.name || `采集运行 #${run.id}`);
      // 单节点临时运行的后端拼名「临时抓取: {fetcher_id}」→ 节点友好名(scope 已在副行表达,
      // 主行不再重复「临时抓取」+ 原始 id)。
      const adhocSingle = rawName.match(/^临时抓取: (\S+)$/);
      const title = adhocSingle && fetchersById[adhocSingle[1]] ? getFetcherName(adhocSingle[1]) : rawName;
      rows.push({
        key: `c-${run.id}`,
        rowType: 'collection',
        title,
        subtitle: run.job_id ? `任务 #${run.job_id} · ${run.node_count || 0} 节点` : `${scopeLabel(run.run_scope)} · ${run.node_count || 0} 节点`,
        ...run,
      });
    });
    nodeRunsWithoutParent.filter(inWindow).forEach(run => {
      rows.push({
        key: `f-${run.id}`,
        rowType: 'fetch',
        title: getFetcherName(run.fetcher_id),
        subtitle: `${run.fetcher_id} · 单节点`,
        ...run,
      });
    });
    return rows.sort((a, b) => String(b.started_at || '').localeCompare(String(a.started_at || '')));
  }, [collectionRuns, nodeRunsWithoutParent, jobsById, fetchersById, getFetcherName, windowDays]);

  // 服务端 fetcher 硬作用域(总账条计数以此为窗口,不随本地筛选塌缩)。
  const scopedRuns = useMemo(() => {
    if (!serverFetcherId) return unifiedRuns;
    const parentIds = new Set(fetchRuns.map(run => run.job_run_id).filter(Boolean));
    return unifiedRuns.filter(run => (run.rowType === 'fetch' ? true : parentIds.has(run.id)));
  }, [unifiedRuns, fetchRuns, serverFetcherId]);

  // 总账条计数:stats 精确口径(近 30 天,A 波);来源过滤生效或端点不可用时
  // 回退窗口口径(stats 不分来源,精确数与来源作用域会口径打架)。
  const statsCounts = !serverFetcherId && daily;
  const counts = useMemo(() => {
    const acc = { total: 0, running: 0, success: 0, partial: 0, failed: 0, saved: 0, fetched: 0, skipped: 0, today: 0 };
    const todayKey = localDayStr(new Date());
    if (statsCounts) {
      [...daily.runs, ...daily.solo].forEach(r => {
        acc.total += r.runs || 0;
        acc.running += r.running || 0;
        acc.success += r.success || 0;
        acc.partial += r.partial || 0;
        acc.failed += r.failed || 0;
        acc.saved += r.saved || 0;
        acc.fetched += r.fetched || 0;
        acc.skipped += r.skipped || 0;
        if (r.day === todayKey) acc.today += r.runs || 0;
      });
      return acc;
    }
    scopedRuns.forEach(run => {
      acc.total += 1;
      acc.saved += run.saved_count || 0;
      acc.fetched += run.fetched_count || 0;
      acc.skipped += run.skipped_count || 0;
      if (dayKey(run.started_at) === todayKey) acc.today += 1;
      if (run.status === 'running') acc.running += 1;
      else if (run.status === 'success') acc.success += 1;
      else if (run.status === 'partial_failed') acc.partial += 1;
      else if (run.status === 'failed') acc.failed += 1;
    });
    return acc;
  }, [scopedRuns, statsCounts, daily]);

  const pct = (n) => (counts.total ? `${((n / counts.total) * 100).toFixed(1)}%` : '0%');

  // 表格集:窗口 + 任务/状态/触发 本地筛选。
  // (「对象:任务/临时」筛选已退役——时刻表点选任务行/「临时抓取」行即是对象维度,mini-seg 与之重复。)
  const tableRuns = useMemo(() => {
    return scopedRuns.filter(run => {
      if (selectedJobId != null) {
        if (selectedJobId === 'adhoc') {
          const isAdhoc = run.rowType === 'fetch' || run.run_scope !== 'saved_job';
          if (!isAdhoc) return false;
        } else if (run.rowType !== 'collection' || run.job_id !== selectedJobId) {
          return false;
        }
      }
      if (statusFilter && run.status !== statusFilter) return false;
      if (triggerFilter && run.trigger_type !== triggerFilter) return false;
      return true;
    });
  }, [scopedRuns, selectedJobId, statusFilter, triggerFilter]);

  const pageCount = Math.max(1, Math.ceil(tableRuns.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRuns = useMemo(
    () => tableRuns.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [tableRuns, safePage]
  );

  // 当前页按日分组(带当日摘要)。
  const dayGroups = useMemo(() => {
    const todayKey = localDayStr(new Date());
    const groups = [];
    let current = null;
    pageRuns.forEach(run => {
      const key = dayKey(run.started_at);
      if (!current || current.key !== key) {
        current = { key, runs: [], saved: 0, failed: 0 };
        groups.push(current);
      }
      current.runs.push(run);
      current.saved += run.saved_count || 0;
      if (run.status === 'failed' || run.status === 'partial_failed') current.failed += 1;
    });
    return groups.map(group => {
      const parts = (group.key || '').split('-');
      const d = parts.length === 3 ? new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])) : null;
      const label = parts.length === 3 ? `${parts[1]}-${parts[2]}` : group.key;
      return {
        ...group,
        label: group.key === todayKey ? `今天 · ${label}` : label,
        weekday: d ? `周${WEEKDAYS[d.getDay()]}` : '',
      };
    });
  }, [pageRuns]);

  // ── 动作 ──

  const openCreateJob = () => {
    setEditingJobId(null);
    setJobDraft(blankJob());
    setJobSearch('');
    setJobModalOpen(true);
  };
  const openEditJob = (job) => {
    setEditingJobId(job.id);
    setJobDraft({
      name: job.name || '',
      description: job.description || '',
      fetcher_ids: job.fetcher_ids || [],
      params: job.params || {},
      per_fetcher_params: job.per_fetcher_params || {},
      cron_expr: job.cron_expr || '',
      is_active: job.is_active !== false,
      downstream_policy: job.downstream_policy || {},
    });
    setJobSearch('');
    setJobModalOpen(true);
  };

  const updateDraftParam = (fetcherId, field, value) => {
    setJobDraft(prev => ({
      ...prev,
      per_fetcher_params: {
        ...(prev.per_fetcher_params || {}),
        [fetcherId]: { ...((prev.per_fetcher_params || {})[fetcherId] || {}), [field]: value },
      },
    }));
  };
  const toggleDraftFetcher = (fetcher) => {
    setJobDraft(prev => {
      const checked = (prev.fetcher_ids || []).includes(fetcher.id);
      const nextIds = checked
        ? prev.fetcher_ids.filter(id => id !== fetcher.id)
        : [...(prev.fetcher_ids || []), fetcher.id];
      return {
        ...prev,
        fetcher_ids: normalizeIds(nextIds),
        per_fetcher_params: checked
          ? prev.per_fetcher_params
          : { ...(prev.per_fetcher_params || {}), [fetcher.id]: defaultParamsFor(fetcher) },
      };
    });
  };

  const handleSaveJob = async () => {
    const name = jobDraft.name.trim();
    const fetcherIds = normalizeIds(jobDraft.fetcher_ids);
    if (!name) { showToast('采集任务名称不能为空', 'error'); return; }
    if (fetcherIds.length === 0) { showToast('采集任务至少需要选择一个节点', 'error'); return; }
    const payload = {
      ...jobDraft,
      name,
      fetcher_ids: fetcherIds,
      cron_expr: jobDraft.cron_expr.trim(),
    };
    await runAction(() => (editingJobId ? updateCollectionJob(editingJobId, payload) : createCollectionJob(payload)), {
      showToast,
      success: editingJobId ? `已保存 ${name}` : `已新建 ${name}`,
      error: '保存采集任务失败,请检查名称与节点后重试',
      onSuccess: () => { setJobModalOpen(false); loadAll(); },
    });
  };

  const handleRunJob = async (id, options = {}) => {
    onRunsChanged?.();
    try {
      const result = await runCollectionJob(id, options);
      const prefix = options.testLimit ? `测试运行完成(每源 ${options.testLimit} 条)` : '采集任务运行完成';
      showToast(collectionRunMessage(prefix, result), result.failed_count ? 'error' : 'success');
      await loadAll();
      onArticlesChanged?.();
      onRunsChanged?.();
    } catch (e) {
      showToast(e.message || '采集任务运行失败,请稍后重试', 'error');
    }
  };

  const handleToggleJob = (job) => {
    const next = !(job.is_active !== false);
    runAction(() => updateCollectionJob(job.id, { is_active: next }), {
      showToast,
      success: `已${next ? '启用' : '停用'} ${job.name}`,
      error: '更新采集任务失败,请稍后重试',
      onSuccess: loadAll,
    });
  };

  const handleDeleteJob = async (job) => {
    if (!(await confirm(`确定删除采集任务「${job.name}」？`))) return;
    await runAction(() => deleteCollectionJob(job.id), {
      showToast,
      success: `已删除 ${job.name}`,
      error: '删除采集任务失败,请稍后重试',
      onSuccess: () => {
        if (selectedJobId === job.id) setSelectedJobId(null);
        loadAll();
      },
    });
  };

  // 从一次运行拼装采集任务草稿并本地打开新建编辑器(与 pendingJobDraft 同路径)。
  const draftFromRun = (run) => {
    if (run.rowType === 'fetch') {
      return {
        fetcher_ids: [run.fetcher_id],
        per_fetcher_params: { [run.fetcher_id]: parseParams(run.params_json) },
      };
    }
    const children = childrenByParent.get(run.id) || [];
    if (!children.length) return null;
    const perParams = {};
    children.forEach(child => { perParams[child.fetcher_id] = parseParams(child.params_json); });
    return { fetcher_ids: normalizeIds(children.map(child => child.fetcher_id)), per_fetcher_params: perParams };
  };
  const saveRunAsJob = (run) => {
    const draft = draftFromRun(run);
    if (!draft) return;
    setEditingJobId(null);
    setJobDraft({ ...blankJob(), ...draft });
    setJobSearch('');
    setJobModalOpen(true);
  };

  // 失败行重跑:单节点走 triggerFetch,任务级走 runCollectionJob。
  // 刷新走 runsDirty 通道(onRunsChanged → App 标脏 → 本页 effect 重新 loadAll),
  // 不在此直接调 loadAll——既是既有跨页刷新机制,也避开 ref 读取在渲染期被误判。
  const rerunRow = async (run) => {
    onRunsChanged?.();
    try {
      if (run.rowType === 'fetch') {
        await triggerFetch(run.fetcher_id, parseParams(run.params_json));
      } else if (run.job_id) {
        await runCollectionJob(run.job_id);
      } else {
        return;
      }
      showToast(`已重跑 ${run.title}`, 'success');
      onArticlesChanged?.();
      onRunsChanged?.();
    } catch (e) {
      showToast(e.message || `重跑 ${run.title} 失败,请稍后重试`, 'error');
    }
  };

  // 临时运行(可升格)判定:单节点,或非 saved_job 的任务级。
  const canSaveAsJob = (run) =>
    run.rowType === 'fetch' || run.run_scope !== 'saved_job';
  const isFailed = (run) => run.status === 'failed' || run.status === 'partial_failed';
  const canRerun = (run) =>
    isFailed(run) && (run.rowType === 'fetch' || Boolean(run.job_id));

  const toggleExpand = (key) => {
    setExpandedKeys(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const selectedJob = typeof selectedJobId === 'number' ? jobsById[selectedJobId] : null;

  const timetableCount = timetable.active.length + timetable.disabled.length;
  const timetableEmpty = timetableCount === 0 && timetable.adhoc.todayCount === 0 && adhocRuns.length === 0;

  // 时刻表行:tt-item 包裹(选中态背景/竖条落在包裹层),选中任务行下方就地展开动作区
  // ——任务的动作跟着任务列表走(原右栏 jobbar 上下文条已退役,右栏回归纯流水)。
  const renderTtRow = (entry) => {
    const isSel = entry.kind === 'adhoc' ? selectedJobId === 'adhoc' : selectedJobId === entry.id;
    const countdown = entry.nextRunAt ? formatCountdown(entry.nextRunAt, nowMs) : null;
    return (
      <div key={entry.id} className={`tt-item ${isSel ? 'is-sel' : ''}`}>
        <button
          onClick={() => setSelectedJobId(isSel ? null : entry.id)}
          className={`tt-row ${entry.kind === 'job' && !entry.isActive ? 'is-off' : ''}`}
        >
          <div className="tt-row-top">
            <span className="tt-name">{entry.name}</span>
            {entry.lastStatus && <span className={`tt-last ${tickFor(entry.lastStatus)}`} title={`上次结果:${stampFor(entry.lastStatus)[1]}`} />}
          </div>
          <div className="tt-row-mid">
            {entry.kind === 'adhoc'
              ? <>由节点管理直接发起 <span className="tt-cron">不定时</span></>
              : <>{entry.nodeCount} 节点 <span className="tt-cron">{entry.cron || '手动触发'}</span></>}
          </div>
          <div className="tt-row-bot">
            <span className="tt-dots" title="近 7 日结果">
              {entry.dots.map((cls, i) => <i key={i} className={cls} />)}
            </span>
            {entry.kind === 'adhoc' ? (
              <span className="tt-next is-manual">{entry.todayCount ? `今日 ${entry.todayCount} 次` : '暂无'}</span>
            ) : !entry.isActive ? (
              <span className="tt-next is-manual">已停用</span>
            ) : countdown ? (
              <span className="tt-next"><span className="u">距下次</span> {countdown}</span>
            ) : (
              <span className="tt-next is-manual">手动</span>
            )}
          </div>
        </button>
        {isSel && entry.kind === 'job' && (
          <div className="tt-acts">
            {entry.isActive && (
              <>
                <button className="tt-act-btn" title="立即运行" onClick={() => handleRunJob(entry.id)}>运行</button>
                <button className="tt-act-btn" title={`测试运行:每源 ${TEST_RUN_LIMIT} 条`} onClick={() => handleRunJob(entry.id, { testLimit: TEST_RUN_LIMIT })}>测试</button>
              </>
            )}
            <button className="tt-act-btn" title="编辑配置" onClick={() => openEditJob(entry.job)}>编辑</button>
            <button className="tt-act-btn" title={entry.isActive ? '停用任务' : '启用任务'} onClick={() => handleToggleJob(entry.job)}>{entry.isActive ? '停用' : '启用'}</button>
            <button className="tt-act-btn is-danger" title="删除任务" onClick={() => handleDeleteJob(entry.job)}>删除</button>
          </div>
        )}
      </div>
    );
  };

  const statPct = { success: pct(counts.success), partial: pct(counts.partial), failed: pct(counts.failed) };

  return (
    <div className="runs-shell">
      <header className="page-head">
        <h1 className="page-title">任务与运行</h1>
        <div className="page-head-actions">
          <span className="win-label">时间窗</span>
          <div className="mini-seg" role="group" aria-label="时间窗">
            {[7, 14, 30, 90].map(d => (
              <button
                key={d}
                type="button"
                onClick={() => setWindowDays(d)}
                className={`mini-seg-btn ${windowDays === d ? 'is-on' : ''}`}
              >
                {d} 天
              </button>
            ))}
          </div>
          <button onClick={loadAll} disabled={loading} title="刷新" aria-label="刷新" className="icon-button signal-refresh">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button onClick={openCreateJob} className="action-button action-button-primary min-h-[36px] px-3 text-xs">
            <Plus /> 新建采集任务
          </button>
        </div>
      </header>

      <div className="runs-work">
        <div className="runs-paper">

          {/* ════ 左栏:时刻表 ════ */}
          <aside className="timetable" aria-label="采集任务时刻表">
            <div className="tt-head">
              <span className="tt-head-title">采集任务</span>
              <span className="tt-head-n">{timetable.active.length}{timetable.disabled.length ? ` + ${timetable.disabled.length} 停用` : ''}</span>
              <span className="tt-clock">{formatClock(nowMs)}</span>
            </div>
            <div className="tt-list">
              {timetableEmpty ? (
                <div className="tt-empty">还没有采集任务<br />点右上角「新建采集任务」创建第一个</div>
              ) : (
                <>
                  {timetable.active.map(renderTtRow)}
                  <div className="tt-off-head">未存为任务</div>
                  {renderTtRow(timetable.adhoc)}
                  {timetable.disabled.length > 0 && (
                    <>
                      <div className="tt-off-head">已停用</div>
                      {timetable.disabled.map(renderTtRow)}
                    </>
                  )}
                </>
              )}
            </div>
            <div className="tt-foot">按下次触发时间排序;点任务过滤右侧流水</div>
          </aside>

          {/* ════ 右区:运行流水 ════ */}
          <main className="flow">
            {/* 总账条:前四格 = 状态筛选,末格被动读数 */}
            <div className="flow-strip">
              <div className="flow-strip-stats">
                <button className={`flow-stat ${statusFilter === '' ? 'is-on' : ''}`} onClick={() => setStatusFilter('')}>
                  <span className="flow-stat-num">{counts.total}</span>
                  <span className="flow-stat-lbl">全部运行</span>
                  <span className="flow-stat-sub">{statsCounts ? `近 ${windowDays} 天` : '窗口内'} · 今日 {counts.today}</span>
                </button>
                <button className={`flow-stat ${statusFilter === 'running' ? 'is-on' : ''}`} onClick={() => setStatusFilter(statusFilter === 'running' ? '' : 'running')}>
                  <span className="flow-stat-num is-run">{counts.running}</span>
                  <span className="flow-stat-lbl">运行中</span>
                  <span className="flow-stat-sub">进行中</span>
                </button>
                <button className={`flow-stat ${statusFilter === 'success' ? 'is-on' : ''}`} onClick={() => setStatusFilter(statusFilter === 'success' ? '' : 'success')}>
                  <span className="flow-stat-num is-ok">{counts.success}</span>
                  <span className="flow-stat-lbl">成功</span>
                  <span className="flow-stat-sub">{statPct.success}</span>
                </button>
                <button className={`flow-stat ${statusFilter === 'partial_failed' ? 'is-on' : ''}`} onClick={() => setStatusFilter(statusFilter === 'partial_failed' ? '' : 'partial_failed')}>
                  <span className="flow-stat-num is-warn">{counts.partial}</span>
                  <span className="flow-stat-lbl">部分失败</span>
                  <span className="flow-stat-sub">{statPct.partial}</span>
                </button>
                <button className={`flow-stat ${statusFilter === 'failed' ? 'is-on' : ''}`} onClick={() => setStatusFilter(statusFilter === 'failed' ? '' : 'failed')}>
                  <span className="flow-stat-num is-bad">{counts.failed}</span>
                  <span className="flow-stat-lbl">失败</span>
                  <span className="flow-stat-sub">{statPct.failed}</span>
                </button>
                <div className="flow-stat is-passive">
                  <span className="flow-stat-num">{counts.saved}</span>
                  <span className="flow-stat-lbl">新增入库</span>
                  <span className="flow-stat-sub">抓取 {counts.fetched} · 跳过 {counts.skipped}</span>
                </div>
              </div>
              <div className="flow-strip-tools">
                <div className="flow-tools-row">
                  <span className="flow-tools-lbl">触发</span>
                  <div className="mini-seg" role="group" aria-label="触发方式">
                    <button className={`mini-seg-btn ${triggerFilter === '' ? 'is-on' : ''}`} onClick={() => setTriggerFilter('')}>全部</button>
                    <button className={`mini-seg-btn ${triggerFilter === 'manual' ? 'is-on' : ''}`} onClick={() => setTriggerFilter('manual')}>手动</button>
                    <button className={`mini-seg-btn ${triggerFilter === 'scheduled' ? 'is-on' : ''}`} onClick={() => setTriggerFilter('scheduled')}>定时</button>
                  </div>
                </div>
              </div>
            </div>

            {loadError && (
              <div className="flow-err-bar"><AlertTriangle /> {loadError}</div>
            )}

            <div className="flow-scroll">
              <table className="flow-table">
                <thead>
                  <tr>
                    <th className="flow-th" style={{ width: 64 }}>时间</th>
                    <th className="flow-th" style={{ width: 96 }}>状态</th>
                    <th className="flow-th">对象</th>
                    <th className="flow-th" style={{ width: 56 }}>触发</th>
                    <th className="flow-th is-num" style={{ width: 64 }}>抓取</th>
                    <th className="flow-th is-num" style={{ width: 64 }}>新增</th>
                    <th className="flow-th is-num" style={{ width: 64 }}>跳过</th>
                    <th className="flow-th is-num" style={{ width: 96 }}>耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && unifiedRuns.length === 0 ? (
                    Array.from({ length: 6 }).map((_, i) => (
                      <tr key={`skel-${i}`} className="flow-skel">
                        <td><div className="skeleton h-3 w-10" /></td>
                        <td><div className="skeleton h-5 w-16 rounded-full" /></td>
                        <td><div className="skeleton h-4 w-40" /></td>
                        <td><div className="skeleton h-3 w-8" /></td>
                        <td><div className="skeleton h-3 w-8 ml-auto" /></td>
                        <td><div className="skeleton h-3 w-8 ml-auto" /></td>
                        <td><div className="skeleton h-3 w-8 ml-auto" /></td>
                        <td><div className="skeleton h-3 w-10 ml-auto" /></td>
                        <td />
                      </tr>
                    ))
                  ) : pageRuns.length === 0 ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="flow-empty">
                          当前筛选下暂无运行记录<br />
                          去左侧时刻表选一个任务立即运行,或在节点管理发起一次临时抓取
                        </div>
                      </td>
                    </tr>
                  ) : (
                    dayGroups.map(group => (
                      <Fragment key={`day-${group.key}`}>
                        <tr className="day-row">
                          <td colSpan={8}>
                            <span className="d">{group.label}<em>{group.weekday}</em></span>
                            <span className="sum">{group.runs.length} 次 · 新增 <b>{group.saved}</b> · 失败 <b>{group.failed}</b></span>
                          </td>
                        </tr>
                        {group.runs.map(run => {
                          const [stampCls, stampLbl] = stampFor(run.status);
                          const children = run.rowType === 'collection' ? (childrenByParent.get(run.id) || []) : [];
                          const hasChildren = children.length > 0;
                          // 单节点聚合运行的展开只会重复主行数据,不给 caret(存为任务仍可从子行拼装)
                          const expandable = hasChildren && (run.node_count || 0) > 1;
                          const expanded = expandedKeys.has(run.key);
                          const running = run.status === 'running';
                          const nodeCount = run.node_count || 0;
                          const terminalChildren = children.filter(c => c.status !== 'running').length;
                          const showProgress = running && nodeCount > 0 && hasChildren;
                          const saveable = canSaveAsJob(run) && (run.rowType === 'fetch' || hasChildren);
                          const rerunnable = canRerun(run);
                          const showErr = isFailed(run) && run.error_message && !expanded;
                          return (
                            <Fragment key={run.key}>
                              <tr
                                className={`run-row ${expandable ? 'is-expandable' : ''} ${expanded ? 'is-open' : ''} ${showErr ? 'has-err' : ''}`}
                                onClick={expandable ? () => toggleExpand(run.key) : undefined}
                              >
                                <td><span className="run-time">{formatRunTime(run.started_at)}</span></td>
                                <td><span className={`stamp ${stampCls}`}>{stampLbl}</span></td>
                                <td>
                                  <div className="run-obj">
                                    {expandable
                                      ? <ChevronRight className="run-caret" />
                                      : <span className="run-caret-ph" />}
                                    <div className="run-obj-txt">
                                      <div className="run-obj-name">{run.title}</div>
                                      {showProgress ? (
                                        <div className="run-progress">
                                          <span className="run-progress-track">
                                            <span className="run-progress-fill" style={{ width: `${Math.round((terminalChildren / nodeCount) * 100)}%` }} />
                                          </span>
                                          <span className="run-progress-n">{terminalChildren}/{nodeCount} 节点</span>
                                        </div>
                                      ) : (
                                        <div className="run-obj-sub">{run.subtitle}</div>
                                      )}
                                    </div>
                                  </div>
                                </td>
                                <td><span className={`run-trigger ${run.trigger_type === 'scheduled' ? 'is-sched' : ''}`}>{run.trigger_type === 'scheduled' ? '定时' : '手动'}</span></td>
                                <td><span className={`run-n ${!run.fetched_count ? 'is-zero' : ''}`}>{run.fetched_count || '–'}</span></td>
                                <td><span className={`run-n is-main ${!run.saved_count ? 'is-zero' : ''}`}>{run.saved_count || '–'}</span></td>
                                <td><span className={`run-n ${!run.skipped_count ? 'is-zero' : ''}`}>{run.skipped_count || '–'}</span></td>
                                <td className="run-durcell">
                                  <span className={`run-dur ${(saveable || rerunnable) ? 'has-acts' : ''}`}>{formatDuration(run.duration_ms)}</span>
                                  {(saveable || rerunnable) && (
                                    <div className="rowacts">
                                      {rerunnable && (
                                        <button className="rowact-btn" title="重跑" onClick={(e) => { e.stopPropagation(); rerunRow(run); }}><RotateCw /></button>
                                      )}
                                      {saveable && (
                                        <button className="rowact-btn" title="存为采集任务" onClick={(e) => { e.stopPropagation(); saveRunAsJob(run); }}><Save /></button>
                                      )}
                                    </div>
                                  )}
                                </td>
                              </tr>
                              {showErr && (
                                <tr className="err-row">
                                  <td colSpan={8}>
                                    <div className="err-msg"><AlertTriangle /> <span>{run.error_message}</span></div>
                                  </td>
                                </tr>
                              )}
                              {expanded && children.map(child => {
                                const childErr = isFailed(child) && child.error_message;
                                return (
                                  <Fragment key={child.id}>
                                    <tr className="child-row">
                                      <td /><td />
                                      <td>
                                        <div className="child-obj">
                                          <span className={`child-tick ${tickFor(child.status)}`} />
                                          <span className="child-name">{getFetcherName(child.fetcher_id)}</span>
                                          <span className="child-id">{child.fetcher_id}</span>
                                        </div>
                                      </td>
                                      <td />
                                      <td><span className={`run-n ${!child.fetched_count ? 'is-zero' : ''}`}>{child.fetched_count || '–'}</span></td>
                                      <td><span className={`run-n is-main ${!child.saved_count ? 'is-zero' : ''}`}>{child.saved_count || '–'}</span></td>
                                      <td><span className={`run-n ${!child.skipped_count ? 'is-zero' : ''}`}>{child.skipped_count || '–'}</span></td>
                                      <td><span className="run-dur">{formatDuration(child.duration_ms)}</span></td>
                                    </tr>
                                    {childErr && (
                                      <tr className="child-err">
                                        <td colSpan={8}>
                                          <div className="err-msg"><AlertTriangle /> <span>{child.error_message}</span></div>
                                        </td>
                                      </tr>
                                    )}
                                  </Fragment>
                                );
                              })}
                            </Fragment>
                          );
                        })}
                      </Fragment>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="flow-foot">
              <span className="flow-foot-info">
                {tableRuns.length} 次运行 · 近 {windowDays} 天
                {selectedJob && (
                  <>
                    {` · 已按「${selectedJob.name}」过滤`}
                    <button className="foot-clear" aria-label="取消任务过滤" onClick={() => setSelectedJobId(null)}><X /></button>
                  </>
                )}
                {selectedJobId === 'adhoc' && (
                  <>
                    {' · 仅临时抓取'}
                    <button className="foot-clear" aria-label="取消临时抓取过滤" onClick={() => setSelectedJobId(null)}><X /></button>
                  </>
                )}
                {serverFetcherId && (
                  <>
                    {` · 来源:${getFetcherName(serverFetcherId)}`}
                    <button className="foot-clear" aria-label="取消来源过滤" onClick={() => setServerFetcherId('')}><X /></button>
                  </>
                )}
              </span>
              {pageCount > 1 && (
                <div className="pager">
                  <button className="pager-btn" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>‹</button>
                  {Array.from({ length: pageCount }).map((_, i) => (
                    <button key={i} className={`pager-btn ${i === safePage ? 'is-on' : ''}`} onClick={() => setPage(i)}>{i + 1}</button>
                  ))}
                  <button className="pager-btn" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>›</button>
                </div>
              )}
            </div>
          </main>
        </div>
      </div>

      <Modal
        open={jobModalOpen}
        onClose={() => setJobModalOpen(false)}
        size="2xl"
        centered
        overlayClassName="py-6"
        ariaLabel={editingJobId ? '编辑采集任务' : '新建采集任务'}
      >
        <div className="jr-sheet">
          {/* ── 头:时刻区(kicker/启用 · 名称 · cron+说明 · 人话回显+距下次) ── */}
          <header className="jr-head">
            <div className="jr-head-top">
              <span className="jr-kicker">{editingJobId ? '编辑采集任务' : '新建采集任务'}</span>
              <span className="jr-switch-row">
                <button
                  type="button"
                  role="switch"
                  aria-checked={jobDraft.is_active}
                  aria-label="启用任务"
                  onClick={() => setJobDraft(prev => ({ ...prev, is_active: !prev.is_active }))}
                  className={`ledger-switch ${jobDraft.is_active ? 'is-on' : ''}`}
                />
                启用
              </span>
              <button onClick={() => setJobModalOpen(false)} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
            </div>
            <input
              value={jobDraft.name}
              onChange={event => setJobDraft(prev => ({ ...prev, name: event.target.value }))}
              placeholder="任务名称"
              aria-label="任务名称"
              className="form-input jr-name-input"
            />
            <div className="jr-head-row2">
              <input
                value={jobDraft.cron_expr}
                onChange={event => setJobDraft(prev => ({ ...prev, cron_expr: event.target.value }))}
                placeholder="0 9 * * *"
                aria-label="整体 cron(5 段)"
                className="form-input font-mono"
              />
              <input
                value={jobDraft.description}
                onChange={event => setJobDraft(prev => ({ ...prev, description: event.target.value }))}
                placeholder="说明(可选)"
                aria-label="说明"
                className="form-input"
              />
            </div>
            {jobDraft.cron_expr.trim() !== '' && (
              <div className="jr-echo">
                {cronInfo.nextMs != null
                  ? <span>= <b>{cronInfo.human}</b> 触发</span>
                  : <span><b>{cronInfo.human}</b></span>}
                {cronCountdown && <span className="jr-echo-next">距下次 {cronCountdown}</span>}
              </div>
            )}
          </header>

          {/* ── 工具行:搜索 + 编入/改动计数 ── */}
          <div className="jr-tools">
            <div className="jr-searchbox">
              <Search />
              <input
                value={jobSearch}
                onChange={event => setJobSearch(event.target.value)}
                placeholder="搜索节点名或 ID"
                aria-label="搜索节点"
              />
            </div>
            <span className="jr-count">
              已编入 <b>{draftFetcherIds.length}</b> / {availableFetchers.length} · 改动 <b>{paramChangeCount}</b>
            </span>
          </div>

          {/* ── 点名册:勾选与定额同一行(勾中行尾浮现上限;默认值 muted、改动 ink 加粗) ── */}
          <div className="jr-list">
            {groupedCatalog.length === 0 ? (
              <div className="jr-empty">没有匹配「{jobSearch.trim()}」的节点,换个关键词试试</div>
            ) : groupedCatalog.map(section => {
              const sectionFetchers = section.companies.flatMap(bucket => bucket.fetchers);
              const allIn = sectionFetchers.every(f => draftFetcherIds.includes(f.id));
              return (
                <Fragment key={section.id}>
                  <div className="jr-group-head">
                    {section.label}
                    <button
                      type="button"
                      className="jr-group-all"
                      onClick={() => setGroupChecked(sectionFetchers, !allIn)}
                    >
                      {allIn ? '清空' : '全选'}
                    </button>
                  </div>
                  {sectionFetchers.map(fetcher => {
                    const checked = draftFetcherIds.includes(fetcher.id);
                    const params = (jobDraft.per_fetcher_params || {})[fetcher.id] || {};
                    return (
                      <div
                        key={fetcher.id}
                        role="checkbox"
                        aria-checked={checked}
                        tabIndex={0}
                        onClick={() => toggleDraftFetcher(fetcher)}
                        onKeyDown={event => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            toggleDraftFetcher(fetcher);
                          }
                        }}
                        className={`jr-row ${checked ? 'is-in' : ''}`}
                      >
                        <span className="jr-check" />
                        <LogoMark company={companyForId(fetcher.id)} size="xs" />
                        <span className="jr-id">
                          <span className="jr-name">{fetcher.name}</span>
                          <span className="jr-sid">{fetcher.id}</span>
                        </span>
                        {/* 行尾定额:当前非模板节点契约 = 仅 limit;若未来长出新参数,同款紧凑输入顺排
                            (形态可增长,见样页注记,不回退双面板) */}
                        <span className="jr-quota">
                          {(fetcher.parameters || []).map(param => {
                            const value = params[param.field] ?? param.default ?? '';
                            const changed = String(value) !== String(param.default ?? '');
                            return (
                              <Fragment key={param.field}>
                                <span className="jr-quota-lbl">{param.field === 'limit' ? '上限' : param.label}</span>
                                <input
                                  type={param.type === 'number' ? 'number' : 'text'}
                                  value={value}
                                  aria-label={`${fetcher.name} ${param.label}`}
                                  onClick={event => event.stopPropagation()}
                                  onKeyDown={event => event.stopPropagation()}
                                  onChange={event => updateDraftParam(
                                    fetcher.id,
                                    param.field,
                                    param.type === 'number' ? Number(event.target.value) : event.target.value
                                  )}
                                  className={changed ? 'is-changed' : ''}
                                />
                              </Fragment>
                            );
                          })}
                        </span>
                      </div>
                    );
                  })}
                </Fragment>
              );
            })}
          </div>

          {/* 编外兜底:已编入但不在目录的节点(改名/退役的化石引用)——可见、可移除,
              否则只出现在计数里没法清理 */}
          {draftFetcherIds.some(id => !fetchersById[id]) && (
            <div className="jr-list" style={{ flex: 'none', overflow: 'visible' }}>
              <div className="jr-group-head">不在目录(已改名或退役,建议移出)</div>
              {draftFetcherIds.filter(id => !fetchersById[id]).map(id => (
                <div
                  key={id}
                  role="checkbox"
                  aria-checked
                  tabIndex={0}
                  onClick={() => toggleDraftFetcher({ id })}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      toggleDraftFetcher({ id });
                    }
                  }}
                  className="jr-row is-in"
                >
                  <span className="jr-check" />
                  <span className="jr-id">
                    <span className="jr-name font-mono">{id}</span>
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* ── 脚:注记 + 唯一 primary CTA ── */}
          <footer className="jr-foot">
            <span className="jr-foot-note">保存后时刻表立即更新;运行中的批次不受影响</span>
            <button onClick={() => setJobModalOpen(false)} className="action-button action-button-quiet ml-auto">取消</button>
            <button onClick={handleSaveJob} className="action-button action-button-primary"><Save /> 保存任务</button>
          </footer>
        </div>
      </Modal>
    </div>
  );
}
