// 「生成流程」页的纯函数(issue #72):泳道一行上的几句话、倒计时、分页窗口、配置表单。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  LAST_MARK_LABEL, clockOf, countdown, draftOf, isDirty, lastMark, localDate, outputLine, pageWindow, payloadOf,
  phaseClass, progressNote,
} from '../src/utils/generationFlows.js';

const TODAY = '2026-11-27';
const latest = { article_id: 'daily_brief_2026-11-27', date: TODAY, generated_at: '2026-11-27T08:31:00', summary: '收录 33 条' };
const flow = (over = {}) => ({
  id: 'daily_brief', name: '哆啦美·AI资讯日报', running: false, last_run: null, latest: null, progress: { state: 'idle', phase_index: null },
  cron: '30 8 * * *', form: [], ...over,
});

test('本地日期与时刻', () => {
  assert.equal(localDate(new Date(2026, 10, 7, 23, 59)), '2026-11-07');
  assert.equal(clockOf('2026-11-27T08:31:00'), '08:31');
  assert.equal(clockOf('2026-11-27'), '');
  assert.equal(clockOf(undefined), '');
});

test('产出一格的那一句:生成中 > 今天失败或跳过 > 最近一期 > 还没有', () => {
  assert.deepEqual(outputLine(flow(), TODAY), ['none', '还没有生成过']);
  assert.deepEqual(outputLine(flow({ latest }), TODAY), ['', '今天 08:31 · 收录 33 条']);
  // 最近一期不是今天的:写月-日(不是每天一期的流程也读得通)
  assert.deepEqual(
    outputLine(flow({ latest: { ...latest, date: '2026-11-23', generated_at: '2026-11-23T09:02:00', summary: '8 个事件' } }), TODAY),
    ['', '11-23 09:02 · 8 个事件'],
  );
  assert.deepEqual(outputLine(flow({ latest: { ...latest, summary: '' } }), TODAY), ['', '今天 08:31']);
  assert.deepEqual(outputLine(flow({ latest, running: true }), TODAY), ['run', '生成中']);
  const failed = { status: 'failed', reason: 'LLM 请求超时', ended_at: '2026-11-27T08:30:40' };
  assert.deepEqual(outputLine(flow({ latest: { ...latest, date: '2026-11-26' }, last_run: failed }), TODAY), ['err', '今天失败 · LLM 请求超时']);
  assert.deepEqual(outputLine(flow({ last_run: { ...failed, reason: '' } }), TODAY), ['err', '今天失败']);
  assert.deepEqual(outputLine(flow({ latest, last_run: { status: 'skipped', reason: '没有新榜单', ended_at: '2026-11-27T09:30:01' } }), TODAY), ['warn', '今天跳过']);
  // 上次结果只有 report_date(日报的旧记录没有 ended_at)也认得出是今天
  assert.deepEqual(outputLine(flow({ last_run: { status: 'skipped', reason: '暂无新增内容', report_date: TODAY } }), TODAY), ['warn', '今天跳过']);
  // 昨天的失败不盖住最近一期
  assert.deepEqual(
    outputLine(flow({ latest: { ...latest, date: '2026-11-26', generated_at: '2026-11-26T08:31:00' }, last_run: { ...failed, ended_at: '2026-11-26T08:30:40' } }), TODAY),
    ['', '11-26 08:31 · 收录 33 条'],
  );
  // 今天成功:就是最近一期那一句
  assert.deepEqual(outputLine(flow({ latest, last_run: { status: 'success', ended_at: '2026-11-27T08:31:02' } }), TODAY), ['', '今天 08:31 · 收录 33 条']);
});

test('行首的结果记号', () => {
  assert.equal(lastMark(flow()), '');
  assert.equal(lastMark(flow({ latest })), 'ok');
  assert.equal(lastMark(flow({ last_run: { status: 'success' } })), 'ok');
  assert.equal(lastMark(flow({ last_run: { status: 'failed' }, latest })), 'bad');
  assert.equal(lastMark(flow({ last_run: { status: 'skipped' }, latest })), 'warn');
  assert.equal(lastMark(flow({ last_run: { status: 'failed' }, running: true })), 'run');
  assert.deepEqual(Object.keys(LAST_MARK_LABEL).sort(), ['', 'bad', 'ok', 'run', 'warn']);
});

test('距下次', () => {
  const now = Date.parse('2026-11-27T16:11:41+08:00');
  assert.equal(countdown('2026-11-28T08:30:00+08:00', now), '16:18:19');
  assert.equal(countdown('2026-12-01T09:00:00+08:00', now), '3 天 16:48');
  assert.equal(countdown('2026-11-27T16:11:40+08:00', now), '00:00:00');      // 已经过了:不出负数
  assert.equal(countdown(null, now), '');
  assert.equal(countdown('不是时间', now), '');
});

test('阶段条与进度文字', () => {
  const running = flow({ running: true, progress: { state: 'running', phase_index: 1, message: '评分 12/33（已有分析 5 篇）', done: 12, total: 33 } });
  assert.deepEqual([0, 1, 2].map((i) => phaseClass(running, i)), [' is-done', ' is-now', '']);
  assert.deepEqual([0, 1].map((i) => phaseClass(flow(), i)), ['', '']);
  // 刚点了生成、后端的进度还没取到:不点亮任何一块
  assert.equal(phaseClass(flow({ running: true }), 0), '');
  assert.equal(progressNote(running), '评分 12/33（已有分析 5 篇）');
  assert.equal(progressNote(flow({ running: true, progress: { message: '撰写点评', done: 3, total: 12 } })), '撰写点评 · 3/12');
  assert.equal(progressNote(flow({ running: true, progress: { message: '从 25 个仓库里挑 6 个', done: 0, total: 0 } })), '从 25 个仓库里挑 6 个');
  assert.equal(progressNote(flow()), '');
});

test('页码窗口', () => {
  const pages = (page, total) => pageWindow(page, total).map((item) => (item.ellipsis ? '…' : item.page));
  assert.deepEqual(pages(1, 1), [1]);
  assert.deepEqual(pages(1, 5), [1, 2, '…', 5]);
  assert.deepEqual(pages(3, 5), [1, 2, 3, 4, 5]);
  assert.deepEqual(pages(5, 9), [1, '…', 4, 5, 6, '…', 9]);
});

const BRIEF_FORM = [
  { type: 'source_scope', field: 'source_ids', label: '取材', value: null },
  { type: 'cron', label: '定时' },
  { type: 'number', field: 'top_n', label: '每期条数', min: 1, max: 50, step: 1, value: 12, default: 12 },
  { type: 'number', field: 'min_score', label: '入选门槛', min: 0, max: 10, step: 0.5, value: 6, default: 6 },
  { type: 'cursor', field: 'cursor', label: '增量游标', value: '2026-06-06T21:51:37' },
];
const PICKS_FORM = [
  { type: 'source_link', label: '取材', source_id: 'github_trending_daily', text: 'GitHub Trending 日榜' },
  { type: 'cron', label: '定时' },
  { type: 'number', field: 'pick_count', label: '挑选个数', min: 1, max: 10, step: 1, value: 6, default: 6 },
  { type: 'toggle', field: 'ai_only', label: '只看 AI 相关', value: true, default: true },
];

test('草稿:从流程现在的取值来', () => {
  const brief = flow({ form: BRIEF_FORM });
  assert.deepEqual(draftOf(brief), { cron: '30 8 * * *', values: { top_n: '12', min_score: '6' }, scopeMode: 'all', scopeIds: [] });
  const scoped = flow({ form: [{ ...BRIEF_FORM[0], value: ['rss_a', 'rss_b'] }, ...BRIEF_FORM.slice(1)] });
  assert.deepEqual([draftOf(scoped).scopeMode, draftOf(scoped).scopeIds], ['custom', ['rss_a', 'rss_b']]);
  assert.deepEqual(draftOf(flow({ cron: '30 9 * * *', form: PICKS_FORM })).values, { pick_count: '6', ai_only: true });
  assert.equal(isDirty(brief, draftOf(brief)), false);
});

test('提交的载荷:游标与取材入口不随保存提交;全部来源传空名单', () => {
  const brief = flow({ form: BRIEF_FORM });
  assert.deepEqual(payloadOf(brief, draftOf(brief)), { payload: { cron: '30 8 * * *', config: { source_ids: [], top_n: 12, min_score: 6 } } });
  const custom = { ...draftOf(brief), cron: ' 15 7 * * * ', scopeMode: 'custom', scopeIds: ['rss_a'], values: { top_n: '20', min_score: '6.5' } };
  assert.deepEqual(payloadOf(brief, custom), { payload: { cron: '15 7 * * *', config: { source_ids: ['rss_a'], top_n: 20, min_score: 6.5 } } });
  const picks = flow({ cron: '30 9 * * *', form: PICKS_FORM });
  assert.deepEqual(payloadOf(picks, { ...draftOf(picks), values: { pick_count: '4', ai_only: false } }),
    { payload: { cron: '30 9 * * *', config: { pick_count: 4, ai_only: false } } });
});

test('填得不对:说清是哪一项', () => {
  const brief = flow({ form: BRIEF_FORM });
  const draft = draftOf(brief);
  const errorOf = (changes) => payloadOf(brief, { ...draft, ...changes }).error;
  assert.match(errorOf({ cron: '30 8 * *' }), /cron 表达式必须是 5 段/);
  assert.match(errorOf({ cron: '' }), /cron 表达式必须是 5 段/);
  assert.equal(errorOf({ values: { ...draft.values, top_n: '0' } }), '每期条数需为 1–50 的整数');
  assert.equal(errorOf({ values: { ...draft.values, top_n: '2.5' } }), '每期条数需为 1–50 的整数');
  assert.equal(errorOf({ values: { ...draft.values, top_n: '' } }), '每期条数需为 1–50 的整数');
  assert.equal(errorOf({ values: { ...draft.values, min_score: '11' } }), '入选门槛需为 0–10 的数字');
  assert.equal(errorOf({ values: { ...draft.values, min_score: 'abc' } }), '入选门槛需为 0–10 的数字');
  assert.match(errorOf({ scopeMode: 'custom', scopeIds: [] }), /自定名单至少勾选一个来源/);
});

test('改过才亮「保存配置」', () => {
  const brief = flow({ form: BRIEF_FORM });
  const draft = draftOf(brief);
  assert.equal(isDirty(brief, { ...draft, cron: '30 8 * * * ' }), false);       // 首尾空白不算改动
  assert.equal(isDirty(brief, { ...draft, cron: '0 9 * * *' }), true);
  assert.equal(isDirty(brief, { ...draft, values: { ...draft.values, top_n: '13' } }), true);
  assert.equal(isDirty(brief, { ...draft, scopeMode: 'custom', scopeIds: ['rss_a'] }), true);
  const scoped = flow({ form: [{ ...BRIEF_FORM[0], value: ['rss_a', 'rss_b'] }, ...BRIEF_FORM.slice(1)] });
  assert.equal(isDirty(scoped, { ...draftOf(scoped), scopeIds: ['rss_b', 'rss_a'] }), false);     // 顺序不同不算
  assert.equal(isDirty(scoped, { ...draftOf(scoped), scopeIds: ['rss_a'] }), true);
  // 切回全部来源:勾选还留在草稿里,但算改动(保存后名单会清空)
  assert.equal(isDirty(scoped, { ...draftOf(scoped), scopeMode: 'all' }), true);
  const picks = flow({ form: PICKS_FORM });
  assert.equal(isDirty(picks, { ...draftOf(picks), values: { pick_count: '6', ai_only: false } }), true);
});
