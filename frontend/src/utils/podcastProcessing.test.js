import assert from 'node:assert/strict';
import test from 'node:test';

import {
  podcastForceTtsResult,
  podcastProcessingReason,
  podcastProcessingSuccessMessage,
  podcastRetryKind,
  podcastRetryLabel,
  podcastScoreCell,
  podcastTaskMeta,
} from './podcastProcessing.js';

const THRESHOLDS = { initial: 6, premium: 7.5 };

test('retry kind is derived from processing status and stage', () => {
  assert.equal(podcastRetryKind({ processing_status: 'reconciliation_required', processing_stage: 'asr' }), 'reconcile');
  assert.equal(podcastRetryKind({ processing_status: 'failed', stage: 'fetch' }), 'asr');
  assert.equal(podcastRetryKind({ processing_status: 'retry_wait', stage: 'analyze' }), '');
  assert.equal(podcastRetryKind({ processing_status: 'failed' }), 'full');
  assert.equal(podcastRetryKind({ processing_status: 'running', stage: 'asr' }), '');
  assert.equal(podcastRetryKind({ status: 'not_required' }), '');
});

test('audit reason, button label and success toast come from the same predicate', () => {
  const reconcile = { processing_status: 'reconciliation_required', processing_stage: 'asr' };
  assert.equal(podcastProcessingReason(reconcile), '管理员核对并重试 ASR 转录');
  assert.equal(podcastRetryLabel(reconcile), '对账恢复 ASR');
  assert.equal(podcastProcessingSuccessMessage(reconcile), '已启动 ASR 对账恢复');
  const analyze = { processing_status: 'failed', processing_stage: 'analyze' };
  assert.equal(podcastProcessingReason(analyze), '管理员重试全文分析');
  assert.equal(podcastProcessingSuccessMessage(analyze), '已重试全文分析');
  // /retry 路径对 not_required 也按阶段措辞
  assert.equal(podcastProcessingReason({ processing_status: 'not_required' }, 'retry'), '管理员重试全文处理');
  assert.equal(podcastProcessingReason({}), '管理员强制全文处理');
  assert.equal(podcastProcessingSuccessMessage({}), '已启动全文处理');
});

test('task meta normalizes stage / verdict / tts and keeps reconciliation apart from failure', () => {
  const waiting = podcastTaskMeta({
    processing_status: 'retry_wait', processing_stage: 'asr', initial_score: 6,
    processing_error: 'provider usage capacity was unavailable before submission',
    next_retry_at: '2026-09-23T00:00:00+08:00', can_retry: false, can_force: false,
  }, THRESHOLDS);
  assert.equal(waiting.stage.code, 'retry_wait');
  assert.equal(waiting.stage.tone, 'warn');
  assert.match(waiting.reason, /等待 ASR 配额.*自动重试.*provider usage capacity/);
  assert.equal(waiting.actions.retry, '');
  assert.equal(podcastTaskMeta({ processing_status: 'retry_wait', processing_stage: 'asr' }, THRESHOLDS).stage.code, 'retry_wait');
  const reconcile = podcastTaskMeta({
    stage_code: 'reconciliation', verdict: 'unscored', processing_status: 'reconciliation_required',
    processing_stage: 'asr', initial_score: 6.5, can_retry: true,
  }, THRESHOLDS);
  assert.equal(reconcile.stage.label, '待对账');
  assert.equal(reconcile.stage.tone, 'warn');
  assert.equal(reconcile.reason, 'ASR 结果待对账 · 对账后重试 ASR');
  assert.equal(reconcile.verdict.label, '未终评');
  assert.equal(reconcile.actions.retry, 'reconcile');
  assert.equal(reconcile.tts.shown, false);

  const legacy = podcastTaskMeta({ processing_status: 'failed', processing_stage: 'asr', processing_error: '音频地址 403', initial_score: 6 }, THRESHOLDS);
  assert.equal(legacy.stage.code, 'failed');
  assert.equal(legacy.reason, 'ASR 转录失败 · 音频地址 403');

  const premium = podcastTaskMeta({
    stage_code: 'full_analyzed', verdict: 'premium', is_premium: true, initial_score: 7, final_score: 8.7,
    analysis_basis: 'asr_transcript', tts_status: 'synthesizing', can_force_tts: false,
  }, THRESHOLDS);
  assert.equal(premium.reason, 'ASR 逐字稿 · 8.7 ≥ 门槛 7.5');
  assert.equal(premium.verdict.label, '优质');
  assert.equal(premium.tts.label, '合成音频中');
  assert.equal(premium.active, true);

  const rejected = podcastTaskMeta({ initial_score: 4.5 }, THRESHOLDS);
  assert.equal(rejected.stage.code, 'not_selected');
  assert.equal(rejected.reason, '简介初评 4.5 < 付费 ASR 线 6.0');
  assert.deepEqual(podcastScoreCell({ initial_score: 4.5 }), { main: '4.5', sub: '简介' });
  assert.deepEqual(podcastScoreCell({ initial_score: 7, final_score: 8.7 }), { main: '8.7', sub: '全文 · 简介 7.0' });
});

test('forced TTS result distinguishes ready / idempotent hit / started', () => {
  assert.deepEqual(podcastForceTtsResult({ status: 'ready' }), { message: '已生成 TTS 音频', tone: 'success' });
  assert.equal(podcastForceTtsResult({ status: 'synthesizing', started: false }).tone, 'info');
  assert.equal(podcastForceTtsResult({ status: 'queued', started: true }).tone, 'success');
});
