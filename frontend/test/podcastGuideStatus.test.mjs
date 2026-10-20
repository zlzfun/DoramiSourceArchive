import assert from 'node:assert/strict';
import test from 'node:test';

import { podcastBodyKey, podcastListAvailabilityMeta, podcastProcessingMeta } from '../src/utils/podcast.js';
import {
  PODCAST_TTS_FILTERS,
  PODCAST_TTS_META,
  podcastTaskMeta,
} from '../src/utils/podcastProcessing.js';

const THRESHOLDS = { initial: 6, premium: 7.5 };

test('正文文字面板的键:只在导读稿就绪那一次变化,无关刷新不变(issue #199 R1-01)', () => {
  const episode = (guide = {}, extra = {}) => ({
    id: 'ep-1',
    read_count: 3,
    summary_zh: '简介初评摘要',
    quality_score: 7.1,
    podcast: { show_title: 'Interconnects', premium_guide: { status: 'summarizing', blog_ready: false, mode: '', ...guide } },
    ...extra,
  });
  const before = podcastBodyKey(episode());
  assert.equal(before, 'ep-1:notes');
  // 无导读 → 就绪:键变,面板重挂载、重取文字
  assert.equal(podcastBodyKey(episode({ blog_ready: true })), 'ep-1:guide');
  // 分析结果、阅读量、premium_guide 其它字段变化:键不变,逐字稿翻页位置不被清掉
  assert.equal(podcastBodyKey(episode({}, { read_count: 9, summary_zh: '全文终评摘要', quality_score: 8.4 })), before);
  assert.equal(podcastBodyKey(episode({ status: 'synthesizing', script_ready: true, mode: 'tts_zh', audio_ready: false })), before);
  assert.equal(podcastBodyKey(episode({ blog_ready: true, status: 'ready', audio_ready: true })), 'ep-1:guide');
  // 换篇当然换键;非播客 / 空值不抛
  assert.equal(podcastBodyKey(episode({}, { id: 'ep-2' })), 'ep-2:notes');
  assert.equal(podcastBodyKey({ id: 'a-1' }), 'a-1:notes');
  assert.equal(podcastBodyKey(null), ':notes');
});

test('readable text without audio uses one neutral label', () => {
  const guide = { blog_ready: true, status: 'failed' };
  for (const status of ['queued', 'synthesizing', 'retry_wait', 'failed', '']) {
    assert.deepEqual(
      podcastListAvailabilityMeta({ premium_guide: { ...guide, status } }),
      { label: '中文导读可阅读', tone: 'idle' },
      status,
    );
  }
  assert.equal(
    podcastListAvailabilityMeta({ condensed_audio_url: '/audio', premium_guide: guide }).label,
    '精品导读音频已就绪',
  );
});

test('no guide text distinguishes generating from a quiet original-only row', () => {
  for (const status of ['queued', 'summarizing', 'synthesizing']) {
    assert.deepEqual(
      podcastListAvailabilityMeta({ premium_guide: { blog_ready: false, status } }),
      { label: '精品导读生成中…', tone: 'run' },
      status,
    );
  }
  for (const status of ['failed', 'retry_wait', '']) {
    assert.deepEqual(
      podcastListAvailabilityMeta({ premium_guide: { status } }),
      { label: '仅提供原节目', tone: 'idle' },
      status,
    );
  }
});

test('reader labels never say failure', () => {
  const samples = [
    podcastProcessingMeta('failed'),
    podcastProcessingMeta('error'),
    podcastProcessingMeta('retry_wait'),
    podcastListAvailabilityMeta({ premium_guide: { blog_ready: true, status: 'failed' } }),
    podcastListAvailabilityMeta({ premium_guide: { status: 'failed' } }),
    podcastListAvailabilityMeta({ premium_guide: { status: 'retry_wait' } }),
  ];
  for (const meta of samples) assert.equal(meta.label.includes('失败'), false);
  assert.deepEqual(podcastProcessingMeta('failed'), { label: '仅提供原节目', tone: 'idle' });
});

test('tts retry_wait is a warn stamp with the planned retry, apart from failure', () => {
  assert.equal(PODCAST_TTS_META.retry_wait.label, '等待重试');
  assert.equal(PODCAST_TTS_META.retry_wait.tone, 'warn');
  assert.deepEqual(
    PODCAST_TTS_FILTERS.filter(([, label]) => label === '等待重试' || label === '失败').map(([value]) => value),
    ['retry_wait', 'failed'],
  );
  const waiting = podcastTaskMeta({
    stage_code: 'full_analyzed', verdict: 'premium', is_premium: true, final_score: 8.2,
    tts_status: 'retry_wait',
    tts_error: 'TTS 回执缓存不足，请在播客管理台检查缺口并安全归档已完成回执',
    tts_next_retry_at: '2026-10-15T09:30:00+00:00',
  }, THRESHOLDS);
  assert.equal(waiting.tts.label, '等待重试');
  assert.equal(waiting.tts.tone, 'warn');
  assert.equal(waiting.tts.status, 'retry_wait');
  assert.match(waiting.tts.retryNote, /^计划 .+ 自动重试$/);
  const failed = podcastTaskMeta({
    stage_code: 'full_analyzed', verdict: 'premium', final_score: 8.2,
    tts_status: 'failed', tts_error: 'http_403',
  }, THRESHOLDS);
  assert.equal(failed.tts.label, '失败');
  assert.equal(failed.tts.tone, 'bad');
  assert.equal(failed.tts.retryNote, '');
  const undated = podcastTaskMeta({
    stage_code: 'full_analyzed', final_score: 8, tts_status: 'retry_wait',
  }, THRESHOLDS);
  assert.equal(undated.tts.retryNote, '');
});
