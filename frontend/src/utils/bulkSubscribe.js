const SHAPE_LABELS = Object.freeze({
  article: '文章源',
  podcast: '播客源',
});

export const BULK_SUBSCRIBE_TIMEOUT_MS = 10_000;

export function createBulkSubscribeDeadline(timeoutMs = BULK_SUBSCRIBE_TIMEOUT_MS) {
  const controller = new AbortController();
  let timedOut = false;
  const timerId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    clear: () => clearTimeout(timerId),
  };
}

export async function waitForBulkSubscribeSettlement(
  readStatus,
  { attempts = 60, intervalMs = 1_000, wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) } = {},
) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const status = await readStatus();
    if (!status?.processing) return true;
    if (attempt + 1 < attempts) await wait(intervalMs);
  }
  return false;
}

export function bulkSubscribeModel(shape, sources, subscribedIds, busyShape = null, loading = false) {
  const sourceLabel = SHAPE_LABELS[shape];
  if (!sourceLabel || loading) return null;

  const sourceIds = (sources || [])
    .filter((source) => !source.hidden && (source.shape || 'article') === shape)
    .map((source) => source.source_id);
  if (sourceIds.length === 0) return null;
  const remainingCount = sourceIds.filter((sourceId) => !subscribedIds.has(sourceId)).length;
  const busy = busyShape === shape;
  const locked = busyShape !== null;
  const complete = remainingCount === 0;

  return {
    shape,
    sourceLabel,
    remainingCount,
    busy,
    complete,
    disabled: locked || complete,
    text: busy
      ? '订阅中…'
      : complete
        ? '已全部订阅'
        : `订阅全部${sourceLabel}（剩余 ${remainingCount} 个）`,
  };
}
