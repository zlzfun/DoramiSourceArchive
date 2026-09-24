export function rankingSnapshotStatusMeta(data) {
  if (data?.refresh_running) return { label: '刷新中', tone: 'run' };
  if (!data?.snapshot) return { label: '待生成', tone: 'idle' };
  if (data.snapshot.status === 'degraded') return { label: '覆盖不足', tone: 'warn' };
  if (data.snapshot.status === 'complete') return { label: '已就绪', tone: 'ok' };
  return { label: '状态未知', tone: 'idle' };
}

export function rankingCoverageText(snapshot) {
  if (!snapshot?.coverage) return '';
  const article = snapshot.coverage.article ?? {};
  const podcast = snapshot.coverage.podcast ?? {};
  return [
    `文章：可入榜 ${Number(article.eligible || 0).toLocaleString()}，已分析 ${Number(article.analyzed || 0).toLocaleString()}，已打标签 ${Number(article.tagged || 0).toLocaleString()}`,
    `播客：可入榜 ${Number(podcast.eligible || 0).toLocaleString()}，已分析 ${Number(podcast.analyzed || 0).toLocaleString()}，已打标签 ${Number(podcast.tagged || 0).toLocaleString()}`,
  ].join(' · ');
}
