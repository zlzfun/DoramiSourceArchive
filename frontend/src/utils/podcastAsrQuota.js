export const ASR_PROVIDER_HARD_MAX_HOURS = 12;

export const ASR_SOURCE_LABELS = {
  runtime_kv: '运行时配置',
  env: '环境变量',
  ini: '配置文件',
  default: '默认值',
};

export function asrProviderLabel(provider) {
  return provider === 'bailian' ? '阿里云百炼' : '阿里云 ISI';
}

export function parseAsrQuotaDraft(dailyHoursText, episodeHoursText) {
  const dailyHours = Number(dailyHoursText);
  const episodeHours = Number(episodeHoursText);
  if (!Number.isFinite(dailyHours) || dailyHours <= 0) {
    return { error: '每日总额度需大于 0 小时' };
  }
  if (!Number.isFinite(episodeHours) || episodeHours <= 0) {
    return { error: '单集上限需大于 0 小时' };
  }
  if (episodeHours > ASR_PROVIDER_HARD_MAX_HOURS) {
    return { error: `单集上限不能超过供应商硬边界 ${ASR_PROVIDER_HARD_MAX_HOURS} 小时` };
  }
  if (dailyHours < episodeHours) {
    return { error: '每日总额度不能小于单集上限' };
  }
  return {
    dailyAudioSecondsLimit: Math.round(dailyHours * 3600),
    maxAudioSecondsPerFile: Math.round(episodeHours * 3600),
  };
}

export function asrUsageText(quota) {
  if (!quota || !['available', 'frozen'].includes(quota.usage_status)) {
    return `用量未知：${quota?.usage_reason || '尚未读取'}`;
  }
  const values = [
    quota.used_audio_seconds,
    quota.reserved_audio_seconds,
    quota.remaining_audio_seconds,
  ];
  if (!values.every(Number.isFinite)) {
    return `用量未知：${quota.usage_reason || '明细不完整'}`;
  }
  const [used, reserved, remaining] = values.map((seconds) => (seconds / 3600).toFixed(2));
  return `已用 ${used}h · 预占 ${reserved}h · 剩余 ${remaining}h${quota.usage_status === 'frozen' ? ' · 已冻结' : ''}`;
}
