// 「生成流程」页(issue #72)的纯函数:泳道一行上的几句话、倒计时、分页窗口、配置表单的取值与校验。
// 页面组件只管取数与交互;这里的东西不碰 DOM,test/generationFlows.test.mjs 逐个守着。

const pad = (n) => String(n).padStart(2, '0');

// 本地日期 YYYY-MM-DD(「今天」按浏览器所在时区算,与后端按服务器本地日期写的 publish_date 对齐)
export function localDate(now = new Date()) {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// 一个 ISO 时刻里的「时:分」(生成时刻;后端写的是服务器本地时间,不做时区换算)
export function clockOf(iso) {
  const match = /T(\d{2}:\d{2})/.exec(iso || '');
  return match ? match[1] : '';
}

// 「上次结果」是不是今天的事
function endedToday(lastRun, today) {
  return Boolean(lastRun) && ((lastRun.ended_at || '').slice(0, 10) === today || lastRun.report_date === today);
}

// 「产出」一格上面那一句:[语气, 文字]。语气:'' 平常 / run 生成中 / err 失败 / warn 跳过 / none 还没有。
// 顺序:正在跑 > 今天失败或跳过 > 最近一期(今天的写「今天」,不是今天的写月-日)> 还没有生成过。
export function outputLine(flow, today = localDate()) {
  if (flow.running) return ['run', '生成中'];
  const last = flow.last_run;
  if (endedToday(last, today)) {
    if (last.status === 'failed') return ['err', last.reason ? `今天失败 · ${last.reason}` : '今天失败'];
    if (last.status === 'skipped') return ['warn', '今天跳过'];
  }
  const latest = flow.latest;
  if (!latest) return ['none', '还没有生成过'];
  const when = latest.date === today ? '今天' : (latest.date || '').slice(5);
  const clock = clockOf(latest.generated_at);
  return ['', [`${when}${clock ? ` ${clock}` : ''}`, latest.summary].filter(Boolean).join(' · ')];
}

// 行首的结果记号(沿用任务名册 .tt-last 的四种):ok / bad / warn / run;没跑过是空串
export function lastMark(flow) {
  if (flow.running) return 'run';
  const status = flow.last_run?.status;
  if (status === 'failed') return 'bad';
  if (status === 'skipped') return 'warn';
  if (status === 'success' || flow.latest) return 'ok';
  return '';
}

export const LAST_MARK_LABEL = { ok: '成功', bad: '失败', warn: '跳过', run: '生成中', '': '还没有运行过' };

// 「距下次 HH:MM:SS」;超过一天写「N 天 HH:MM」。没有下一次(关着)返回空串。
export function countdown(nextRunAt, now = Date.now()) {
  if (!nextRunAt) return '';
  const target = Date.parse(nextRunAt);
  if (Number.isNaN(target)) return '';
  let seconds = Math.max(0, Math.round((target - now) / 1000));
  const days = Math.floor(seconds / 86400);
  seconds -= days * 86400;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return days > 0 ? `${days} 天 ${pad(h)}:${pad(m)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
}

// 阶段条上第 index 块的状态类:生成中时,前面的已完成、当前的高亮;不在跑时都是平的
export function phaseClass(flow, index) {
  const progress = flow.progress;
  if (!flow.running || !progress || progress.phase_index == null) return '';
  if (index < progress.phase_index) return ' is-done';
  return index === progress.phase_index ? ' is-now' : '';
}

// 生成中阶段条下面的一行:后端给的进度文字;带了完成数 / 总数而文字里没有时补上
export function progressNote(flow) {
  const progress = flow.progress;
  if (!flow.running || !progress) return '';
  const { message, done, total } = progress;
  if (total > 0 && !(message || '').includes(`${done}/${total}`)) return [message, `${done}/${total}`].filter(Boolean).join(' · ');
  return message || '';
}

// 页码窗口:首页、当前±1、末页,间断处省略号(.pager 范式,与知识台账同式)
export function pageWindow(page, totalPages) {
  const wanted = [1, page - 1, page, page + 1, totalPages].filter((p) => p >= 1 && p <= totalPages);
  const out = [];
  let prev = 0;
  for (const p of [...new Set(wanted)].sort((a, b) => a - b)) {
    if (p - prev > 1) out.push({ ellipsis: true, key: `e${p}` });
    out.push({ page: p, key: p });
    prev = p;
  }
  return out;
}

// ── 配置表单(抽屉里的「配置」框)──────────────────────────────────────────────
// 表单的行由后端声明(flow.form):source_scope / source_link / cron / number / toggle / cursor。
// draft = { cron, values: { 字段: 取值 }, scopeMode: 'all' | 'custom', scopeIds: [源 id] }

export function draftOf(flow) {
  const values = {};
  let scopeIds = [];
  let scopeMode = 'all';
  for (const row of flow.form || []) {
    if (row.type === 'number') values[row.field] = String(row.value ?? row.default ?? '');
    if (row.type === 'toggle') values[row.field] = Boolean(row.value);
    if (row.type === 'source_scope') {
      scopeIds = Array.isArray(row.value) ? [...row.value] : [];
      scopeMode = scopeIds.length ? 'custom' : 'all';
    }
  }
  return { cron: flow.cron || '', values, scopeMode, scopeIds };
}

const scopeKey = (draft) => `${draft.scopeMode}\n${[...draft.scopeIds].sort().join('\n')}`;

// 服务器上的取值变了(别的窗口改了配置、保存后重取):把草稿挪到新的起点上。
// baseline = 草稿上次与服务器对齐时的取值;本地没改过的项取服务器的新值,改过的留着。
// 这样「保存配置」提交的整份草稿里,没碰过的项永远是服务器现在的值,不会把别处的改动写回旧值。
export function rebaseDraft(baseline, draft, server) {
  const values = {};
  for (const key of Object.keys(server.values)) {
    const edited = key in draft.values && String(draft.values[key]) !== String(baseline.values[key]);
    values[key] = edited ? draft.values[key] : server.values[key];
  }
  const cronEdited = (draft.cron || '').trim() !== (baseline.cron || '').trim();
  const scopeEdited = scopeKey(draft) !== scopeKey(baseline);
  return {
    cron: cronEdited ? draft.cron : server.cron,
    values,
    scopeMode: scopeEdited ? draft.scopeMode : server.scopeMode,
    scopeIds: scopeEdited ? [...draft.scopeIds] : [...server.scopeIds],
  };
}

// 草稿 → 提交给接口的载荷;填得不对返回 { error }(文案直接进 toast)。
export function payloadOf(flow, draft) {
  const cron = (draft.cron || '').trim();
  if (cron.split(/\s+/).filter(Boolean).length !== 5) return { error: 'cron 表达式必须是 5 段，例如：30 8 * * *' };
  const config = {};
  for (const row of flow.form || []) {
    if (row.type === 'number') {
      const value = Number(draft.values[row.field]);
      const integer = Number.isInteger(row.step ?? 1);
      const bad = draft.values[row.field] === '' || !Number.isFinite(value) || (integer && !Number.isInteger(value))
        || (row.min != null && value < row.min) || (row.max != null && value > row.max);
      if (bad) return { error: `${row.label}需为 ${row.min}–${row.max} 的${integer ? '整数' : '数字'}` };
      config[row.field] = value;
    }
    if (row.type === 'toggle') config[row.field] = Boolean(draft.values[row.field]);
    if (row.type === 'source_scope') {
      if (draft.scopeMode === 'custom' && draft.scopeIds.length === 0) {
        return { error: '自定名单至少勾选一个来源(或切回「全部来源」)' };
      }
      // 全部来源 → 传 [](后端语义:空 = 全部);自定 → 传勾选的集合
      config[row.field] = draft.scopeMode === 'custom' ? [...draft.scopeIds] : [];
    }
  }
  return { payload: { cron, config } };
}

// 草稿与流程现在的取值是否不同(「保存配置」据此决定要不要亮)
export function isDirty(flow, draft) {
  const base = draftOf(flow);
  if ((draft.cron || '').trim() !== (base.cron || '').trim()) return true;
  if (draft.scopeMode !== base.scopeMode) return true;
  if (draft.scopeMode === 'custom' && [...draft.scopeIds].sort().join('\n') !== [...base.scopeIds].sort().join('\n')) return true;
  return Object.keys(base.values).some((key) => String(draft.values[key]) !== String(base.values[key]));
}
