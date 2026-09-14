import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  PODCAST_FORCE_TTS_REASON,
  podcastPremiumTtsCommand,
  podcastTtsStatusMeta,
} from './podcastPremiumGuide.js';

test('forced podcast TTS command uses the dedicated audited endpoint', () => {
  const command = podcastPremiumTtsCommand('episode/42', 'force-tts-once');
  assert.equal(command.path, '/admin/podcast-premium-guides/episode%2F42/force');
  assert.deepEqual(command.body, {
    reason: PODCAST_FORCE_TTS_REASON,
    idempotency_key: 'force-tts-once',
  });
});

test('forced podcast TTS command creates a fresh idempotency key', () => {
  const first = podcastPremiumTtsCommand('episode-42');
  const second = podcastPremiumTtsCommand('episode-42');
  assert.match(first.body.idempotency_key, /^podcast-tts-.{8,}$/);
  assert.match(second.body.idempotency_key, /^podcast-tts-.{8,}$/);
  assert.notEqual(first.body.idempotency_key, second.body.idempotency_key);
});

test('premium task list explains that forced TTS skips automatic selection', async () => {
  const component = await readFile(
    new URL('../components/admin/PodcastPremiumGuidesPanel.jsx', import.meta.url),
    'utf8',
  );
  assert.match(component, /item\.can_force_tts/);
  assert.match(component, /title="跳过自动优质筛选，使用已完成的全文分析生成 TTS 音频"/);
  assert.match(component, /TTS \{tts\.label\}/);
  assert.match(component, /setTimeout\(\(\) => load\(page, filter\), 3000\)/);
});

test('TTS status metadata distinguishes live progress, success, and failure', () => {
  assert.deepEqual(
    podcastTtsStatusMeta({ tts_status: 'synthesizing', tts_forced: true }),
    {
      status: 'synthesizing', label: '正在合成音频', tone: 'run', active: true,
      error: '', forced: true,
    },
  );
  assert.equal(podcastTtsStatusMeta({ tts_status: 'ready' }).tone, 'ok');
  assert.equal(podcastTtsStatusMeta({ tts_status: 'failed', tts_error: '供应商错误' }).error, '供应商错误');
  assert.equal(podcastTtsStatusMeta({ audio_ready: true }).status, 'ready');
});
