import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  READER_AI_PURPOSES, aiReaderSplit, budgetRatio, bytesParts, growthWindow, pageMarks, pendingItems, readTop, stageParts, switchDots, windowDays,
} from '../src/utils/opsHome.js';

test('窗口日期:旧 → 新、含末日;asOf 优先于浏览器今天', () => {
  assert.deepEqual(windowDays(3, '2026-03-01'), ['2026-02-27', '2026-02-28', '2026-03-01']);
  assert.equal(windowDays(30).length, 30);
});

test('账户增长窗口:逐日新增补零、窗口起点存量 = 现存 − 窗口新增', () => {
  const g = growthWindow({ totals: { accounts: 20 }, as_of_day: '2026-03-10', series: [{ day: '2026-03-09', new: 2 }, { day: '2026-03-01', new: 5 }] }, 7);
  assert.deepEqual(g.news, [0, 0, 0, 0, 0, 2, 0]);
  assert.equal(g.added, 2);
  assert.equal(g.start, 18);
  assert.equal(growthWindow(null, 7), null);
});

test('AI 用量拆读者 / 系统两组(读者用途与后端预算白名单同步)', () => {
  const py = readFileSync(new URL('../../src/services/accounts.py', import.meta.url), 'utf8');
  const start = py.indexOf('READER_AI_BUDGET_PURPOSES = (');
  const block = py.slice(start, py.indexOf('\n)\n', start));
  assert.deepEqual([...block.matchAll(/^\s+"([a-z_]+)",/gm)].map((m) => m[1]), READER_AI_PURPOSES);
  const [d1, d2] = windowDays(2);
  const split = aiReaderSplit({
    totals: { calls: 10, total_tokens: 900 },
    by_purpose: [{ purpose: 'translate', calls: 4 }, { purpose: 'article_analysis', calls: 6 }],
    by_day_purpose: [{ day: d1, purpose: 'translate', calls: 4 }, { day: d2, purpose: 'article_analysis', calls: 6 }, { day: '1999-01-01', purpose: 'ask', calls: 9 }],
  }, 2);
  assert.deepEqual(split.rows, [[4, 0], [0, 6]]);
  assert.equal(split.readerShare, 0.4);
});

test('播客阶段构成:进行中合并四态并带明细,背景量排在后面', () => {
  const parts = stageParts({ full_analyzed: 10, awaiting_transcript: 1, processing: 2, retry_wait: 3, reconciliation: 4, not_selected: 50, not_processed: 5, failed: 1 });
  assert.deepEqual(parts.map((p) => [p.key, p.value]), [['done', 10], ['run', 10], ['idle', 50], ['new', 5], ['failed', 1]]);
  assert.equal(parts[1].detail.length, 4);
  assert.deepEqual(stageParts(null), []);
});

test('待处理:未加载不出项;按严重度排序;各项落到所在页板块', () => {
  assert.deepEqual(pendingItems({}), []);
  const items = pendingItems({
    storage: { backup: { status: 'failed' } },
    podTasks: { breakdown: { stage: { failed: 3 } } },
    home: { backfills: { unresolved_failed: 1 } },
    aiGlobal: { daily_token_budget: 100, tokens_used_today: 85 },
    usrc: { kpi: { failing_count: 2 } },
    feedback: { counts: { open: 5 } },
    taxonomy: { unresolved_candidate_count: 12 },
    xQuota: { estimated_cost_usd: 1, monthly_budget_usd: 50 },
    asr: { usage_status: 'available' },
  });
  assert.deepEqual(items.map((i) => i.key), ['backup', 'podcast-failed', 'backfill', 'budget', 'usrc', 'feedback', 'candidates']);
  assert.ok(items.slice(0, 3).every((i) => i.tone === 'bad') && items.slice(3).every((i) => i.tone === 'warn'));
  assert.deepEqual(items.find((i) => i.key === 'budget'), { tone: 'warn', key: 'budget', name: '读者 AI 预算', value: '85%', page: 'reader', block: 'switches' });
  assert.deepEqual(pageMarks(items), { external: 'bad', podcast: 'bad', analysis: 'bad', reader: 'warn', 'user-sources': 'warn', inbox: 'warn' });
  // 预算 0 = 不限,不报;X API 超 80% 或已封顶才报
  assert.equal(budgetRatio({ daily_token_budget: 0 }), null);
  assert.deepEqual(pendingItems({ xQuota: { blocked: true, estimated_cost_usd: 50, monthly_budget_usd: 50 } }).map((i) => i.value), ['100%']);
});

test('8 个总闸点:读到前为 null(灰点),读到后按开关;都落到所在板块', () => {
  const dots = switchDots({});
  assert.equal(dots.length, 8);
  assert.ok(dots.every((d) => d.on === null && d.page && d.block));
  const on = switchDots({ aiGlobal: { enabled: true }, guest: { settings: { enabled: true, ai_enabled: false } }, analysis: { feature_flags: { article_analysis_enabled: true, personal_digest_enabled: false } } });
  const byKey = Object.fromEntries(on.map((d) => [d.key, d.on]));
  assert.equal(byKey.ai, true);
  assert.equal(byKey.guestAi, false);
  assert.equal(byKey.digest, false);
  assert.equal(byKey.share, null);
});

test('各源 Top N:有聚合端点用窗口内排行,否则退回累计并排除零阅读', () => {
  assert.deepEqual(readTop({ reads: { by_source: [{ source_id: 'a', name: 'A', reads: 3 }] } }, null, 5), { windowed: true, rows: [{ name: 'A', value: 3 }] });
  const fallback = readTop(null, { sources: [{ source_id: 'b', name: 'B', read_count: 2 }, { source_id: 'c', name: 'C', read_count: 9 }, { source_id: 'z', name: 'Z', read_count: 0 }] }, 5);
  assert.deepEqual(fallback, { windowed: false, rows: [{ name: 'C', value: 9 }, { name: 'B', value: 2 }] });
});

test('媒体库容量分成数与单位', () => {
  assert.deepEqual(bytesParts(0), ['0', 'B']);
  assert.deepEqual(bytesParts(2.4 * 1024 ** 3), ['2.4', 'GB']);
  assert.deepEqual(bytesParts(512 * 1024 ** 2), ['512', 'MB']);
});
