// 首登引导完成后的早报重编状态 → Toast 文案(issue #56,codex 检视 P2「有 edition ≠ 重编成功」)。
// status 契约同后端 brief_rebuild_status:null=未触发;ready/degraded=已完成;pending/generating=在途;
// failed=触发失败;empty_subscriptions=无可编排内容(不提示——早报页自会说明)。
export function rebuildToast(status) {
  if (status === 'ready' || status === 'degraded') return { text: '早报已按你的兴趣重新编排', kind: 'success' };
  if (status === 'pending' || status === 'generating') return { text: '早报正在按你的兴趣重新编排', kind: 'success' };
  if (status === 'failed') return { text: '兴趣已保存，但早报重编失败', kind: 'error' };
  return null;
}
