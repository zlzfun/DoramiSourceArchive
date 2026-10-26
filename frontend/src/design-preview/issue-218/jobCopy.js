// 翻译任务的读者面文案(样页):两种显示方式共用。

// 停止态的一行原因 + 动作:失败给「重试」;中断 / 已取消 / 额度恢复后给「继续翻译」;
// 额度用尽只说原因,不给立即重试(重试也突破不了额度)。
export function stoppedLine(job) {
  if (job.status === 'failed') return { text: '翻译失败', action: '重试' };
  if (job.status === 'cancelled') return { text: '翻译已取消', action: '继续翻译' };
  if (job.reason === 'quota') return { text: '今日 AI 翻译次数已用完，明天可继续', action: null };
  if (job.reason === 'interrupted') return { text: '翻译中断', action: '继续翻译' };
  return { text: '翻译已暂停', action: '继续翻译' };
}

// 读屏的一次性播报内容:任务结束时说一句(完成 / 失败 / 额度用尽 / 暂停),不逐片、不逐块;
// 已取消是读者自己点的,不再复述;早已存在的完整缓存不播。
export function announcementOf(job) {
  if (!job?.live) return '';
  if (job.status === 'succeeded') return '中文译文已就绪';
  if (job.status === 'failed') return '翻译失败';
  if (job.status === 'paused') return job.reason === 'quota' ? '今日 AI 翻译次数已用完' : '翻译已暂停';
  return '';
}
