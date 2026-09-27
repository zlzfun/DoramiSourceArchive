import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hasStorageOperations, storageBackupMeta, storageCacheMeta, storageHealthMeta } from '../src/utils/storageStatus.js';

test('local and old deployments keep storage operations hidden', () => {
  for (const status of [null, {}, { media: { storage_backend: 'local' }, podcast: { storage_backend: 'local' }, backup: { enabled: false } }]) {
    assert.equal(hasStorageOperations(status), false);
  }
  assert.equal(hasStorageOperations({ media: { storage_backend: 'oss' } }), true);
  assert.equal(hasStorageOperations({ podcast: { storage_backend: 'oss' } }), true);
  assert.equal(hasStorageOperations({ backup: { enabled: true } }), true);
});

test('unobserved storage never appears healthy and new failures override earlier success', () => {
  assert.equal(storageHealthMeta({}).tone, 'idle');
  assert.equal(storageHealthMeta({ last_success_at: '2026-09-16' }).tone, 'ok');
  const failure = storageHealthMeta({ last_success_at: '2026-09-16', last_error: 'private diagnostic detail' });
  assert.equal(failure.tone, 'bad');
  assert.doesNotMatch(JSON.stringify(failure), /private diagnostic/);
});

test('automatic maintenance distinguishes disabled, pending, completed, and failed checks', () => {
  assert.equal(storageCacheMeta({ enabled: false, last_run_at: '2026-09-16' }).tone, 'idle');
  assert.equal(storageCacheMeta({ enabled: true }).label, '等待首次回收');
  assert.equal(storageCacheMeta({ enabled: true, last_run_at: '2026-09-16' }).tone, 'ok');
  assert.equal(storageCacheMeta({ enabled: true, last_run_at: '2026-09-16', last_error: 'failed' }).tone, 'warn');
});

test('backup failures remain visible despite a previous successful snapshot', () => {
  assert.equal(storageBackupMeta({ enabled: true }).label, '等待首次备份');
  assert.equal(storageBackupMeta({ enabled: true, last_success_at: '2026-09-16' }).tone, 'ok');
  assert.equal(storageBackupMeta({ enabled: true, last_success_at: '2026-09-16', last_error: 'failed' }).tone, 'bad');
  assert.equal(storageBackupMeta({ enabled: true, running: true, last_error: 'failed' }).tone, 'run');
});
