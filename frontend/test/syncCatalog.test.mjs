import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  EVIDENCE_ID, LEGACY_STREAM_ORDER, OPTIONAL_CAPABILITIES, REQUIRED_CAPABILITIES, SCHEMA_VERSION, STREAMS, STREAM_ORDER,
  consoleOf, cronFromFreq, cronLabel, fmtBytes, fmtDur, fmtSnapshot, freqFromCron, inventoryCells, negotiatedOrder, nextCronRun,
  probePending, remainingOf, runErrorHead, runFromCheckpoints, runOf, runStep, runTotals, schedChip, stationOf, streamDurations,
  shortId, streamPercent, streamStateIn, syncPendingItem, syncTileView, toDate, trigLabel,
} from '../src/utils/syncCatalog.js';
import { pendingItems } from '../src/utils/opsHome.js';

const py = (path) => readFileSync(new URL(`../../src/services/${path}`, import.meta.url), 'utf8');
const tuple = (src, name) => {
  const m = new RegExp(`^${name} = \\(([\\s\\S]*?)^\\)`, 'm').exec(src);
  assert.ok(m, name);
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
};
const constant = (src, name) => new RegExp(`^${name} = "([^"]+)"`, 'm').exec(src)?.[1];

test('流顺序与能力常量与后端协议同源(读 remote_sync.py / archive_sync_v2.py 比对)', () => {
  const rs = py('remote_sync.py');
  const v2 = py('archive_sync_v2.py');
  assert.deepEqual(STREAM_ORDER, tuple(rs, 'V2_STREAM_ORDER'));
  assert.deepEqual(LEGACY_STREAM_ORDER, tuple(rs, 'V2_LEGACY_STREAM_ORDER'));
  assert.equal(SCHEMA_VERSION, constant(v2, 'SCHEMA_VERSION'));
  assert.deepEqual(REQUIRED_CAPABILITIES, [constant(v2, 'TRANSACTION_REVISION_CAPABILITY'), constant(v2, 'AUTHORITATIVE_PRESENCE_CAPABILITY')]);
  assert.equal(OPTIONAL_CAPABILITIES.events, constant(v2, 'EVENTS_CAPABILITY'));
  assert.equal(OPTIONAL_CAPABILITIES.podcast_texts, constant(v2, 'PODCAST_TEXT_PUBLICATIONS_CAPABILITY'));
  assert.equal(OPTIONAL_CAPABILITIES.podcast_audio, constant(v2, 'PODCAST_AUDIO_PUBLICATIONS_CAPABILITY'));
  // 目录按协议顺序登记,每条流都有产品名 / 分组 / 装了什么
  assert.deepEqual(STREAMS.map((s) => s.id), STREAM_ORDER);
  for (const s of STREAMS) assert.ok(s.name && s.group && s.what.length && s.items.length, s.id);
});

test('协商顺序:legacy 六流 + 按能力插入 events / 播客文本 / 精简音频;采集就绪永远最后', () => {
  assert.deepEqual(negotiatedOrder(REQUIRED_CAPABILITIES), LEGACY_STREAM_ORDER);
  assert.deepEqual(negotiatedOrder([...REQUIRED_CAPABILITIES, ...Object.values(OPTIONAL_CAPABILITIES)]), STREAM_ORDER);
  const ev = negotiatedOrder([OPTIONAL_CAPABILITIES.events]);
  assert.equal(ev[ev.indexOf('analyses') + 1], 'events');
  assert.equal(negotiatedOrder(null), null);
});

const order = STREAM_ORDER;
const doneStats = (count, extra = {}) => ({ pages: 1, count, inserted: count, updated: 0, deleted: 0, ...extra });

test('成功任务:逐流取 result.streams,统计归一(媒体下载 / 复用合并,字段缺省当 0)', () => {
  const run = runOf({
    job_id: 'j1', status: 'succeeded', created_at: 1762315200, started_at: 1762315202, ended_at: 1762315421, created_by: 'system',
    result: {
      capabilities: [...REQUIRED_CAPABILITIES, ...Object.values(OPTIONAL_CAPABILITIES)],
      streams: Object.fromEntries(order.map((sid) => [sid, doneStats(sid === 'media' ? 10 : 2, sid === 'media' ? { media_downloaded: 7, media_reused: 3, downloaded_bytes: 2048 } : {})])),
      candidate_evidence: { status: 'success', pages: 1, inserted: 18, skipped: 196 },
    },
  }, order);
  assert.equal(run.status, 'succeeded');
  assert.equal(run.streams.media.downloaded, 7);
  assert.equal(run.streams.media.reused, 3);
  for (const sid of order) assert.equal(streamStateIn(run, sid), 'done');
  assert.equal(streamStateIn(run, EVIDENCE_ID), 'done');
  const t = runTotals(run);
  assert.equal(t.count, 2 * 8 + 10);
  assert.equal(t.downloadedBytes, 2048);
  assert.equal(trigLabel(run.trigger), '定时');
  assert.equal(trigLabel('admin'), 'admin');
  // jobs 表时间是 epoch 秒
  assert.equal(toDate(1762315200).getTime(), 1762315200 * 1000);
});

test('失败任务:result 为空,逐流读 streams_done,失败流 = progress.stream,页 = progress.page;其后各流未跑', () => {
  const run = runOf({
    job_id: 'j2', status: 'failed', created_at: 1, ended_at: 300, error: 'checksum mismatch', result: null,
    progress: {
      order, stream: 'analyses', page: 37, stream_processed: 36000, stream_total: 41000, processed: 36415,
      stream_started_at: '2026-11-05T04:01:00+00:00',
      streams_done: { sources: doneStats(3), taxonomy: doneStats(0), articles: doneStats(412, { started_at: '2026-11-05T04:00:04+00:00', completed_at: '2026-11-05T04:00:56+00:00' }) },
    },
  });
  assert.deepEqual(order.map((sid) => streamStateIn(run, sid)), ['done', 'done', 'done', 'bad', 'todo', 'todo', 'todo', 'todo', 'todo']);
  assert.equal(streamStateIn(run, EVIDENCE_ID), 'todo');
  assert.equal(runErrorHead(run), 'AI 分析 · 第 37 页');
  const st = stationOf(run, 'analyses');
  assert.equal(st.num, '第 37 页');
  assert.deepEqual(st.sub, ['前 36 页已提交']);
  assert.equal(stationOf(run, 'events').sub[0], '未跑');
  assert.equal(runTotals(run).count, 3 + 0 + 412 + 36000);
  assert.equal(streamDurations(run).articles, 52);
  assert.equal(runErrorHead(runOf({ status: 'failed', progress: null })), '同步失败');
});

test('运行中:当前流进度 / 百分比(分母未知不给)/ 第几条流 / 剩余;老后端没有 streams_done 时当前流之前视为完成', () => {
  const base = {
    job_id: 'j3', status: 'running', created_at: 1,
    progress: {
      order, stream: 'media', page: 1, stream_processed: 412, stream_total: 640, downloaded: 360, reused: 52,
      streams_done: { sources: doneStats(0), taxonomy: doneStats(0), articles: doneStats(216), analyses: doneStats(190), events: doneStats(5) },
      run_pending: { articles: 216, analyses: 190, events: 5, podcast_texts: 2, podcast_audio: 0, source_states: 7, sources: 0 },
    },
  };
  const run = runOf(base);
  assert.equal(streamStateIn(run, 'media'), 'run');
  assert.equal(streamPercent(run), 64);
  assert.deepEqual(runStep(run), { k: 6, n: 9 });
  assert.equal(remainingOf(run), (640 - 412) + 2 + 0 + 7);
  assert.deepEqual(stationOf(run, 'podcast_texts').sub, ['待同步 2']);
  assert.equal(stationOf(run, 'media').small, '/ 640');
  // 老生产端:stream_total 为 null → 不显示百分比、剩余未知
  const old = runOf({ ...base, progress: { ...base.progress, stream_total: null } });
  assert.equal(streamPercent(old), null);
  assert.equal(remainingOf(old), null);
  assert.equal(stationOf(old, 'media').small, '条');
  // 老后端:没有 streams_done / order,当前流之前按顺序视为已完成(无统计)
  const legacy = runOf({ job_id: 'j4', status: 'running', progress: { stream: 'analyses', stream_processed: 9 } }, order);
  assert.deepEqual(order.slice(0, 4).map((sid) => streamStateIn(legacy, sid)), ['done', 'done', 'done', 'run']);
  assert.deepEqual(stationOf(legacy, 'articles').sub, ['已完成']);
  // 排队中:还没有进度
  const queued = runOf({ job_id: 'j5', status: 'queued' }, order);
  assert.equal(queued.active, true);
  assert.equal(streamStateIn(queued, 'sources'), 'todo');
});

test('未协商的流显示「未协商」(旧版对端只广播必需能力)', () => {
  const old = negotiatedOrder(REQUIRED_CAPABILITIES);
  const run = runOf({ status: 'succeeded', result: { capabilities: REQUIRED_CAPABILITIES, streams: Object.fromEntries(old.map((s) => [s, doneStats(1)])) } });
  assert.equal(streamStateIn(run, 'events'), 'skip');
  assert.equal(stationOf(run, 'podcast_audio').num, '未协商');
  assert.equal(streamStateIn(run, 'media'), 'done');
});

test('未配置:各站一律待跑 · 首次整流', () => {
  const st = stationOf(null, 'articles', { configured: false });
  assert.equal(st.k, 'todo');
  assert.deepEqual(st.sub, ['首次整流']);
});

test('没有任务记录时用 KV checkpoint 拼「上次同步」', () => {
  const run = runFromCheckpoints({ sources: { snapshot: '12', count: 3, completed_at: '2026-11-05T12:00:03' }, taxonomy: { snapshot: '12', count: 0 } }, order);
  // 只展示各流已知进度,不冒充整次成功(检视 R1 #4):没有 checkpoint 的流仍是未跑
  assert.equal(run.status, 'checkpoint');
  assert.equal(streamStateIn(run, 'sources'), 'done');
  assert.equal(streamStateIn(run, 'articles'), 'todo');
  assert.equal(streamStateIn(run, 'candidate_evidence'), 'todo');
  assert.equal(stationOf(run, 'taxonomy').num, '未变');
  assert.deepEqual(stationOf(run, 'taxonomy').sub, ['v12']);
  assert.equal(runFromCheckpoints({}, order), null);
});

test('「待同步 N」只在 probe.pending 有该流、且该流 checkpoint.snapshot == probe.pending_since 时成立', () => {
  const con = { checkpoints: { articles: { snapshot: '2418520' }, analyses: { snapshot: '2418000' } }, probe: { pending: { articles: 216, analyses: 190 }, pending_since: '2418520' } };
  assert.equal(probePending(con, 'articles'), 216);
  assert.equal(probePending(con, 'analyses'), null);
  assert.equal(probePending(con, 'media'), null);
  assert.equal(probePending({ probe: { pending: null } }, 'articles'), null);
});

test('本机盘点格:缺键不显示,整条流都缺就整节隐藏;tombstone 取 streams[流]', () => {
  const inv = { streams: { articles: { upsert: 46218, tombstone: 324 } }, items: { articles: 46218, daily_briefs: 74 } };
  assert.deepEqual(inventoryCells('articles', inv), [['文章', 46218], ['公共日报', 74], ['tombstone', 324]]);
  assert.deepEqual(inventoryCells('events', inv), []);
  assert.deepEqual(inventoryCells('media', { items: { media: 9 } }), [['图片', 9], ['删除', '不传播']]);
  assert.deepEqual(inventoryCells('articles', null), []);
});

test('定时:频率 ⇄ cron、下次时间(APScheduler 语义:day_of_week 0 = 周一,day 与 dow 取交集)', () => {
  assert.equal(cronFromFreq('six'), '0 */6 * * *');
  assert.equal(cronFromFreq('daily', '04:30'), '30 4 * * *');
  assert.deepEqual(freqFromCron('30 4 * * *'), { freq: 'daily', dailyTime: '04:30', customCron: '' });
  assert.equal(freqFromCron('*/15 * * * *').freq, 'custom');
  assert.equal(cronLabel('0 */6 * * *'), '每 6 小时');
  const from = new Date(2026, 10, 5, 14, 26, 30); // 周四
  assert.deepEqual(nextCronRun('0 */6 * * *', from), new Date(2026, 10, 5, 18, 0));
  assert.deepEqual(nextCronRun('0 3 * * *', from), new Date(2026, 10, 6, 3, 0));
  assert.deepEqual(nextCronRun('0 9 * * 0', from), new Date(2026, 10, 9, 9, 0)); // 0 = 周一
  assert.deepEqual(nextCronRun('0 9 * * sun', from), new Date(2026, 10, 8, 9, 0));
  assert.equal(nextCronRun('bad cron', from), null);
  assert.equal(nextCronRun('61 * * * *', from), null);
  assert.equal(schedChip({ enabled: false }).label, '关');
  assert.equal(schedChip({ enabled: true, migration_required: true }).label, '需选协议');
  assert.equal(schedChip({ enabled: true, cron: '0 */6 * * *' }, from).label, '每 6 小时');
});

test('格式:字节 / 用时 / 修订号', () => {
  assert.equal(fmtBytes(0), '0 B');
  assert.equal(fmtBytes(80.4 * 1024 * 1024), '80.4 MB');
  assert.equal(fmtBytes(246 * 1024 * 1024), '246 MB');
  assert.equal(fmtDur(219), '3 分 39 秒');
  assert.equal(fmtDur(1), '1 秒');
  assert.equal(fmtDur(null), '');
  assert.equal(fmtSnapshot('articles', '2418520'), 'r2,418,520');
  assert.equal(fmtSnapshot('taxonomy', '12'), 'v12');
});

test('控制台块:有 console 用 console;老后端从 state.targets 兜底', () => {
  const c = consoleOf({ console: { target: 'https://a', configured: true, capabilities: REQUIRED_CAPABILITIES } });
  assert.deepEqual(c.stream_order, LEGACY_STREAM_ORDER);
  const legacy = consoleOf({ state: { targets: { 'https://b': { last_synced_at: '2026-11-05T12:00', v2_authority_id: 'auth', v2_streams: { sources: { snapshot: '1' } } } } } });
  assert.equal(legacy.configured, true);
  assert.equal(legacy.target, 'https://b');
  assert.equal(legacy.authority_id, 'auth');
  assert.equal(consoleOf({ state: { targets: {} } }).configured, false);
});

test('首页磁贴与状态磁贴:运行中 k / n、失败只在状态磁贴告警一次(排最前)、未接入灰字', () => {
  const now = new Date(2026, 10, 5, 14, 26);
  assert.equal(syncTileView({ configured: true, status: 'running', running: { done: 5, total: 9, stream: 'media' } }, now).num, '6 / 9');
  assert.equal(syncTileView({ configured: false, status: 'none' }, now).num, '未接入');
  const failed = { configured: true, status: 'failed', last_success_at: new Date(2026, 10, 5, 6, 3).toISOString(), failed: { stream: 'analyses', page: 37 } };
  const v = syncTileView(failed, now);
  assert.equal(v.num, '06:03');
  assert.equal(v.mark, 'bad');
  assert.deepEqual(syncPendingItem(failed), { tone: 'bad', key: 'sync', name: '节点同步失败', value: 'AI 分析', page: 'sync', block: 'runs' });
  assert.equal(syncPendingItem({ status: 'idle' }), null);
  const items = pendingItems({ home: { sync: failed }, feedback: { counts: { open: 2 } } });
  assert.deepEqual(items.map((i) => i.key), ['sync', 'feedback']);
});

test('authority 短码:去掉 dorami- 前缀取 uuid 前 8 位', () => {
  assert.equal(shortId('dorami-b39cc556-8dd3-4ada-aaf9-5366d2a7cc15'), 'b39cc556');
  assert.equal(shortId('7c1e9a2b-4f3d'), '7c1e9a2b');
  assert.equal(shortId(''), '');
});
