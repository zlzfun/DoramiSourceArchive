// 采集侧（节点管理 / 任务与运行）共享的小工具，收敛两个 Tab 里曾各写一份且已分叉的实现。

// 测试运行时每个来源只抓 1 条，用于快速验证节点连通性。
export const TEST_RUN_LIMIT = 1;

// 去重 + 去空：节点 id 列表规整。
export function normalizeIds(ids) {
  return Array.from(new Set((ids || []).filter(Boolean)));
}

// 采集/批量运行结果的统一提示文案。
// successCount 省略时不含「完成 N 个节点」前缀（供 FetchRunsTab 的采集任务运行复用，
// 输出与其原实现逐字一致）；传入时（FetchTab 批量抓取）补上节点完成数。
export function collectionRunMessage(prefix, result, successCount = null) {
  const failed = result?.failed_count || 0;
  const saved = result?.saved_count || 0;
  const okText = successCount === null ? '' : `完成 ${successCount} 个节点，`;
  const failureText = failed ? `，失败 ${failed} 个${result.error_message ? `：${result.error_message}` : ''}` : '';
  return `${prefix}：${okText}新增 ${saved} 条${failureText}`;
}
