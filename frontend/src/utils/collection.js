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

// 把一个参数覆盖对象翻译成可读 chips：`[{ key, label, value }]`。
// 键名优先用 fetcher 参数 schema 的中文 label（fetcher.parameters: [{field,label,type}]），
// 未命中回落原字段名；布尔值渲染成 ✓/✕，其余原样。空对象返回空数组（供调用方决定是否渲染）。
export function paramChips(paramsObj, fetcher) {
  const entries = Object.entries(paramsObj || {});
  if (!entries.length) return [];
  const schema = Object.fromEntries((fetcher?.parameters || []).map(p => [p.field, p]));
  return entries.map(([key, value]) => {
    const label = schema[key]?.label || key;
    let display;
    if (typeof value === 'boolean') display = value ? '✓' : '✕';
    else if (value === null || value === undefined || value === '') display = '—';
    else display = String(value);
    return { key, label, value: display };
  });
}
