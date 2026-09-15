import { ArchiveX, CheckCircle2, Loader2, Trash2, Volume2 } from 'lucide-react';

import { podcastArtifactAdminAudioUrl } from '../../api';
import { TableFoot } from './Pager';
import StaleNotice from './StaleNotice';
import { ThFilter, ThSearch, ThSort } from './TableTh';
import { formatStamp } from './adminUtils';
import {
  formatPodcastArtifactBytes,
  podcastArtifactKindLabel,
  podcastArtifactStatusMeta,
} from '../../utils/podcastArtifactAdmin';

export const PODCAST_AUDIO_PAGE_SIZE = 20;
const STATUS_FILTERS = [['', '状态'], ['ready', '待发布'], ['published', '已发布'], ['withdrawn', '已下架']];

// 中文精简音频表(issue #76 样页):导读合成出的音频文件台账,主语是文件——发布 / 下架 /
// 删除 / 安全回收都在这里;节目列显标题(后端补字段),整行可点开同一个单集抽屉。
export default function PodcastAudioTable({
  state,       // { status, data: { items, total }, error }
  stats,       // artifacts stats | null
  filters,     // { q, status, sort, order, page }
  onFilters,
  onOpen,
  onPublish,
  onWithdraw,
  onDelete,
  onReconcile,
  busyId,
  gcBusy,
  onRetryLoad,
}) {
  const data = state.data;
  const items = data?.items ?? [];
  const filtersActive = Boolean(filters.q || filters.status);
  const reclaimable = Number(stats?.reclaimable_orphan_blobs || 0) + Number(stats?.stale_staging_files || 0);
  const handleSort = (k) => {
    if (filters.sort === k) onFilters({ order: filters.order === 'asc' ? 'desc' : 'asc', page: 1 });
    else onFilters({ sort: k, order: 'desc', page: 1 });
  };
  const headMeta = stats
    ? `${Number(stats.artifacts || 0).toLocaleString()} 份 · 已发布 ${Number(stats.published || 0).toLocaleString()} · 待发布 ${Number(stats.ready || 0).toLocaleString()} · 已下架 ${Number(stats.withdrawn || 0).toLocaleString()} · ${formatPodcastArtifactBytes(stats.disk_bytes)}`
    : '';

  return (
    <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
      <div className="tbl-head">
        <span className="tools-title">中文精简音频</span>
        {headMeta && <span className="tiny-meta">{headMeta}</span>}
        {data && <StaleNotice status={state.status} error={state.error} onRetry={onRetryLoad} />}
        <span className="zone-acts">
          <button
            type="button"
            className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
            onClick={onReconcile}
            disabled={gcBusy || reclaimable === 0}
            title="清理过期临时校验文件与无引用音频文件；数据库仍引用的音频不会被删除"
          >
            {gcBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            安全回收 <span className="acct-mono">{reclaimable}</span>
          </button>
        </span>
      </div>
      {(state.status === 'error' || state.status === 'unavailable') && !data ? (
        <p className="acct-empty"><StaleNotice status={state.status} error={state.status === 'unavailable' ? '当前后端版本没有该端点' : state.error} onRetry={state.status === 'error' ? onRetryLoad : undefined} label="中文精简音频" /></p>
      ) : state.status === 'loading' && !data ? (
        <p className="acct-empty tiny-meta" aria-busy="true"><Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin" />正在加载音频…</p>
      ) : (
        <>
          <div className="acct-scroll">
            <table className="acct-table is-fixed">
              <thead>
                <tr>
                  <ThSearch label="节目" value={filters.q} onChange={(q) => onFilters({ q, page: 1 })} placeholder="搜索节目 / 节目 ID" active={Boolean(filters.q.trim())} width="36%" inputWidth={200} />
                  <ThFilter label="状态" value={filters.status} onChange={(status) => onFilters({ status, page: 1 })} options={STATUS_FILTERS} width={110} />
                  <th className="acct-th" style={{ width: 120 }}>格式</th>
                  <ThSort label="大小" k="size" sort={filters.sort} order={filters.order} onSort={handleSort} num width={96} />
                  <ThSort label="创建" k="created" sort={filters.sort} order={filters.order} onSort={handleSort} width={130} />
                  <th className="acct-th" aria-label="操作" style={{ width: 96 }} />
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="acct-empty tiny-meta">
                      {filtersActive ? (
                        <>没有匹配当前筛选的音频。<button type="button" className="kpi-sub-link" onClick={() => onFilters({ q: '', status: '', page: 1 })}>清除筛选</button></>
                      ) : '还没有中文精简音频'}
                    </td>
                  </tr>
                ) : items.map((artifact) => {
                  const status = podcastArtifactStatusMeta(artifact.status);
                  const kindLabel = podcastArtifactKindLabel(artifact.kind);
                  const busy = busyId === artifact.id;
                  const refs = Number(artifact.active_processing_refs || 0);
                  const open = () => onOpen(artifact.episode_id);
                  return (
                    <tr
                      key={artifact.id}
                      className="acct-row"
                      tabIndex={0}
                      onClick={open}
                      onKeyDown={(e) => { if (e.key === 'Enter') open(); }}
                    >
                      <td>
                        <span className="acct-name" title={artifact.episode_title || artifact.episode_id}>{artifact.episode_title || artifact.episode_id || '—'}</span>
                        <span className="acct-sub">{artifact.source_name ? `${artifact.source_name} · ` : ''}<span className="acct-mono">{artifact.episode_id}</span></span>
                      </td>
                      <td><span className={`stamp stamp-${status.tone}`}>{status.label}</span></td>
                      <td><span className="acct-mono">{artifact.mime || '—'}</span></td>
                      <td className="acct-n">{formatPodcastArtifactBytes(artifact.size_bytes)}</td>
                      <td><time className="acct-mono" dateTime={artifact.created_at || undefined}>{formatStamp(artifact.created_at)}</time></td>
                      <td>
                        <span className="rowacts" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                          <a
                            className="rowact-btn"
                            href={podcastArtifactAdminAudioUrl(artifact.id)}
                            target="_blank"
                            rel="noreferrer"
                            title={`试听${kindLabel}`}
                            aria-label={`试听${kindLabel}`}
                          >
                            <Volume2 />
                          </a>
                          {busy ? (
                            <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span>
                          ) : (
                            <>
                              {artifact.status === 'ready' && (
                                <button type="button" className="rowact-btn" onClick={() => onPublish(artifact)} title="发布（读者可见；需确认）" aria-label="发布中文精简版">
                                  <CheckCircle2 />
                                </button>
                              )}
                              {artifact.status === 'published' && (
                                <button type="button" className="rowact-btn" onClick={() => onWithdraw(artifact)} title="下架（读者不可见，文件保留；需确认）" aria-label="下架中文精简版">
                                  <ArchiveX />
                                </button>
                              )}
                              {artifact.status === 'withdrawn' && (
                                <button
                                  type="button"
                                  className="rowact-btn is-danger"
                                  onClick={() => onDelete(artifact)}
                                  disabled={refs > 0}
                                  title={refs > 0 ? `仍被 ${refs} 个处理中任务引用，引用归零后可删除` : '永久删除登记与文件（需确认）'}
                                  aria-label="永久删除中文精简版"
                                >
                                  <Trash2 />
                                </button>
                              )}
                            </>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <TableFoot total={data?.total ?? 0} page={filters.page} pageSize={PODCAST_AUDIO_PAGE_SIZE} onPage={(page) => onFilters({ page })} />
        </>
      )}
    </section>
  );
}
