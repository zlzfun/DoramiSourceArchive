const SHAPE_LABELS = Object.freeze({
  article: '文章源',
  podcast: '播客源',
});

export function bulkSubscribeModel(shape, sources, subscribedIds, busyShape = null) {
  const sourceLabel = SHAPE_LABELS[shape];
  if (!sourceLabel) return null;

  const sourceIds = (sources || [])
    .filter((source) => !source.hidden && (source.shape || 'article') === shape)
    .map((source) => source.source_id);
  const remainingCount = sourceIds.filter((sourceId) => !subscribedIds.has(sourceId)).length;
  const busy = busyShape === shape;
  const complete = remainingCount === 0;

  return {
    shape,
    sourceLabel,
    remainingCount,
    busy,
    complete,
    disabled: busy || complete,
    text: busy
      ? '订阅中…'
      : complete
        ? '已全部订阅'
        : `订阅全部${sourceLabel}（剩余 ${remainingCount} 个）`,
  };
}
