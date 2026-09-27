export function hasStorageOperations(status) {
  return status?.media?.storage_backend === 'oss'
    || status?.podcast?.storage_backend === 'oss'
    || status?.backup?.enabled === true;
}

// Missing observations are pending, never a successful health check.
export function storageHealthMeta(health) {
  if (health?.last_error) return { tone: 'bad', label: '读写异常' };
  if (health?.last_success_at) return { tone: 'ok', label: '最近读写正常' };
  return { tone: 'idle', label: '等待首次读写' };
}

export function storageCacheMeta(cache) {
  if (!cache?.enabled) return { tone: 'idle', label: '自动回收未启用' };
  if (cache.last_error) return { tone: 'warn', label: '回收待检查' };
  if (cache.last_run_at) return { tone: 'ok', label: '自动回收正常' };
  return { tone: 'idle', label: '等待首次回收' };
}

export function storageBackupMeta(backup) {
  if (!backup?.enabled) return { tone: 'idle', label: '未启用' };
  if (backup.running) return { tone: 'run', label: '备份中…' };
  if (backup.last_error) return { tone: 'bad', label: '最近备份失败' };
  if (backup.last_success_at) return { tone: 'ok', label: '最近备份完成' };
  return { tone: 'idle', label: '等待首次备份' };
}
