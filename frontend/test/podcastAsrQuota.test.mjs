import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  asrProviderLabel,
  asrUsageText,
  parseAsrQuotaDraft,
} from '../src/utils/podcastAsrQuota.js';

test('ASR 配额默认产品值按整数秒提交', () => {
  assert.deepEqual(parseAsrQuotaDraft('40', '3'), {
    dailyAudioSecondsLimit: 144000,
    maxAudioSecondsPerFile: 10800,
  });
});

test('ASR 配额拒绝负数、日额度小于单集与供应商硬边界', () => {
  assert.match(parseAsrQuotaDraft('-1', '3').error, /大于 0/);
  assert.match(parseAsrQuotaDraft('2', '3').error, /不能小于/);
  assert.match(parseAsrQuotaDraft('40', '12.5').error, /硬边界 12/);
});

test('未知用量不显示为 0，Bailian 与 ISI 名称可辨', () => {
  assert.equal(asrUsageText({ usage_status: 'unknown', usage_reason: '读取失败' }), '用量未知：读取失败');
  assert.match(asrUsageText({
    usage_status: 'available',
    used_audio_seconds: 3600,
    reserved_audio_seconds: 1800,
    remaining_audio_seconds: 7200,
  }), /已用 1\.00h.*预占 0\.50h.*剩余 2\.00h/);
  assert.equal(asrProviderLabel('bailian'), '阿里云百炼');
  assert.equal(asrProviderLabel('aliyun_isi'), '阿里云 ISI');
});

test('额度编辑只在运维 · 播客页，凭据页保留直达回指(issue #114 新路由)', async () => {
  const [zone, credentials, app] = await Promise.all([
    readFile(new URL('../src/components/admin/PodcastZone.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/settings/CredentialsSection.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/App.jsx', import.meta.url), 'utf8'),
  ]);
  assert.match(zone, /id=\{opsBlockId\('podcast', 'asr'\)\}/);
  assert.match(zone, /updatePodcastAsrQuota/);
  assert.doesNotMatch(credentials, /updatePodcastAsrQuota/);
  assert.match(credentials, /前往 运维管理 → 播客/);
  assert.match(app, /jumpWithFocus\('admin', 'podcast\/asr'/);
  assert.match(app, /payload: \{ page: 'podcast', block: 'asr' \}/);
});
