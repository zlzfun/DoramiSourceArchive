import { useCallback, useEffect, useRef, useState } from 'react';
import { ArchiveX, CheckCircle2, Loader2, RefreshCw, Trash2, Volume2 } from 'lucide-react';

import {
  deletePodcastArtifact,
  fetchPodcastArtifacts,
  fetchPodcastArtifactStats,
  podcastArtifactAdminAudioUrl,
  publishPodcastArtifact,
  reconcilePodcastArtifacts,
  withdrawPodcastArtifact,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import {
  formatPodcastArtifactBytes,
  formatPodcastArtifactTime,
  podcastArtifactKindLabel,
  podcastArtifactStatusMeta,
  podcastArtifactTotalStorageMeta,
} from '../../utils/podcastArtifactAdmin';

function ArtifactKpi({ value, label, sub }) {
  return (
    <div className="kpi">
      <span className="kpi-num">{value}</span>
      <span className="kpi-lbl">{label}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

export default function PodcastArtifactsPanel({ showToast, refreshTick = 0 }) {
  const confirm = useConfirm();
  const requestGeneration = useRef(0);
  const [stats, setStats] = useState(null);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [gcBusy, setGcBusy] = useState(false);

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setLoadError('');
    try {
      const [nextStats, nextList] = await Promise.all([
        fetchPodcastArtifactStats(),
        fetchPodcastArtifacts({ kind: 'digest_audio_zh', limit: 100 }),
      ]);
      if (requestGeneration.current !== generation) return;
      setStats(nextStats);
      setItems(Array.isArray(nextList?.items) ? nextList.items : []);
    } catch (error) {
      if (requestGeneration.current !== generation) return;
      const message = error.message || '加载失败：后端未响应，请确认服务已启动后重试';
      setLoadError(message);
      showToast(message, 'error');
    } finally {
      if (requestGeneration.current === generation) setLoading(false);
    }
  }, [showToast]);

  const totalStorageMeta = stats ? podcastArtifactTotalStorageMeta(stats) : null;

  useEffect(() => { load(); }, [load, refreshTick]);

  const handleWithdraw = async (artifact) => {
    const kindLabel = podcastArtifactKindLabel(artifact.kind);
    if (!(await confirm(
      `确认下架这份${kindLabel}？读者将无法继续访问，文件会保留以便核查。`,
    ))) return;
    setBusyId(artifact.id);
    try {
      await withdrawPodcastArtifact(artifact.id);
      showToast(`已下架${kindLabel}`, 'success');
      await load();
    } catch (error) {
      showToast(error.message || '下架失败，请刷新后重试', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handlePublish = async (artifact) => {
    if (!(await confirm(
      '确认发布这份中文精简版？系统会再次校验文件和并发版本。',
    ))) return;
    setBusyId(artifact.id);
    try {
      await publishPodcastArtifact(artifact.id, artifact.updated_at);
      showToast('中文精简版已发布', 'success');
      await load();
    } catch (error) {
      showToast(error.message || '发布失败，请刷新后重试', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (artifact) => {
    const kindLabel = podcastArtifactKindLabel(artifact.kind);
    if (!(await confirm(
      `确认删除这份已下架的${kindLabel}登记？登记不可恢复；无引用文件会在宽限期后由安全回收清理。`,
    ))) return;
    setBusyId(artifact.id);
    try {
      const result = await deletePodcastArtifact(artifact.id);
      showToast(result.blob_deleted ? `已删除${kindLabel}及本地文件` : `已删除${kindLabel}记录`, 'success');
      await load();
    } catch (error) {
      showToast(error.message || '删除失败，请刷新后重试', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleReconcile = async () => {
    if (!(await confirm(
      '确认清理过期的临时校验文件、失效预留标记和无引用音频文件？数据库仍引用的中文精简音频不会被删除。',
    ))) return;
    setGcBusy(true);
    try {
      const result = await reconcilePodcastArtifacts();
      const count = Number(result.deleted_orphan_blobs || 0).toLocaleString();
      const bytes = formatPodcastArtifactBytes(result.deleted_bytes);
      const staging = Number(result.deleted_staging_files || 0).toLocaleString();
      showToast(`已回收 ${count} 个孤儿文件（${bytes}），清理 ${staging} 个临时校验文件`, 'success');
      await load();
    } catch (error) {
      showToast(error.message || '安全回收失败，请刷新后重试', 'error');
    } finally {
      setGcBusy(false);
    }
  };

  return (
    <>
      <div className="zone-head podcast-assets-head">
        <span className="zone-title">播客音频</span>
        <span className="zone-hint">仅持久保留生成的中文精简音频；原节目继续使用发布者链接</span>
        <span className="zone-acts flex flex-wrap gap-2">
          <button
            type="button"
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
            onClick={handleReconcile}
            disabled={gcBusy || !(
              Number(stats?.reclaimable_orphan_blobs || 0)
              + Number(stats?.stale_staging_files || 0)
            )}
          >
            {gcBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            安全回收
          </button>
          <button
            type="button"
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
            onClick={load}
            disabled={loading}
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            刷新资产
          </button>
        </span>
      </div>

      {stats?.storage_pressure && (
        <div className="podcast-assets-state is-error" role="alert">
          播客存储已触发容量保护；请先扩容、调整已审核的配额配置，或安全回收无引用文件。
        </div>
      )}

      <section className="surface-card kpi-strip" aria-label="播客音频存储概览">
        <ArtifactKpi value={stats ? Number(stats.artifacts || 0).toLocaleString() : '—'} label="资产记录" sub="最近 100 条见下表" />
        <ArtifactKpi
          value={stats ? formatPodcastArtifactBytes(stats.disk_bytes) : '—'}
          label="本地占用"
          sub={stats ? `孤儿 ${Number(stats.orphan_blobs || 0).toLocaleString()} 个 / ${formatPodcastArtifactBytes(stats.orphan_bytes)}` : '按内容去重'}
        />
        <ArtifactKpi value={stats ? Number(stats.ready || 0).toLocaleString() : '—'} label="就绪" sub="待发布" />
        <ArtifactKpi value={stats ? Number(stats.published || 0).toLocaleString() : '—'} label="已发布" sub="读者可用" />
        <ArtifactKpi value={stats ? Number(stats.withdrawn || 0).toLocaleString() : '—'} label="已下架" sub="可物理删除" />
        <ArtifactKpi
          value={totalStorageMeta?.value || '—'}
          label={totalStorageMeta?.label || '配额余量'}
          sub={totalStorageMeta?.sub || '配置加载中'}
        />
        <ArtifactKpi
          value={stats ? formatPodcastArtifactBytes(stats.disk_free_bytes) : '—'}
          label="磁盘可用"
          sub={stats ? `最低保留 ${formatPodcastArtifactBytes(stats.minimum_free_bytes)}` : '配置加载中'}
        />
        <ArtifactKpi
          value={stats ? (
            Number(stats.staging_files || 0) + Number(stats.download_reservations || 0)
          ).toLocaleString() : '—'}
          label="临时校验暂存"
          sub={stats ? `预留 ${formatPodcastArtifactBytes(stats.download_reserved_bytes)} / 过期文件 ${Number(stats.stale_staging_files || 0).toLocaleString()} 个` : '等待统计'}
        />
      </section>

      <section className="surface-card rounded-[var(--r-card)] overflow-hidden podcast-assets-card">
        {loadError ? (
          <div className="podcast-assets-state is-error" role="alert" aria-live="polite">
            <span>{loadError}</span>
            <button type="button" className="kpi-sub-link" onClick={load}>重新加载</button>
          </div>
        ) : loading && !stats ? (
          <p className="podcast-assets-state tiny-meta">
            <Loader2 className="h-4 w-4 animate-spin" /> 正在加载播客音频…
          </p>
        ) : items.length === 0 ? (
          <p className="podcast-assets-state tiny-meta">
            还没有中文精简音频；生成并上传后，可在这里管理。
          </p>
        ) : (
          <div className="acct-scroll">
            <table className="acct-table is-fixed podcast-assets-table">
              <thead>
                <tr>
                  <th className="acct-th">节目 ID</th>
                  <th className="acct-th">类型</th>
                  <th className="acct-th">状态</th>
                  <th className="acct-th">格式</th>
                  <th className="acct-th is-num">大小</th>
                  <th className="acct-th">创建时间</th>
                  <th className="acct-th" aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                {items.map((artifact) => {
                  const status = podcastArtifactStatusMeta(artifact.status);
                  const busy = busyId === artifact.id;
                  return (
                    <tr key={artifact.id} className="acct-row is-static">
                      <td>
                        <span className="acct-name podcast-assets-episode" title={artifact.episode_id}>
                          {artifact.episode_id || '—'}
                        </span>
                      </td>
                      <td><span className="body-text podcast-assets-kind">{podcastArtifactKindLabel(artifact.kind)}</span></td>
                      <td><span className={`stamp stamp-${status.tone}`}>{status.label}</span></td>
                      <td><span className="acct-mono">{artifact.mime || '—'}</span></td>
                      <td className="acct-n">{formatPodcastArtifactBytes(artifact.size_bytes)}</td>
                      <td><time className="acct-mono" dateTime={artifact.created_at || undefined}>{formatPodcastArtifactTime(artifact.created_at)}</time></td>
                      <td>
                        <span className="rowacts podcast-assets-actions">
                          <a
                            className="rowact-btn"
                            href={podcastArtifactAdminAudioUrl(artifact.id)}
                            target="_blank"
                            rel="noreferrer"
                            title={`试听${podcastArtifactKindLabel(artifact.kind)}`}
                            aria-label={`试听${podcastArtifactKindLabel(artifact.kind)}`}
                          >
                            <Volume2 />
                          </a>
                          {artifact.status === 'ready' && artifact.kind === 'digest_audio_zh' && (
                            <button
                              type="button"
                              className="rowact-btn"
                              onClick={() => handlePublish(artifact)}
                              disabled={busy}
                              title="发布中文精简音频"
                              aria-label="发布中文精简版"
                            >
                              {busy ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
                            </button>
                          )}
                          {artifact.status === 'withdrawn' ? (
                            <button
                              type="button"
                              className="rowact-btn is-danger"
                              onClick={() => handleDelete(artifact)}
                              disabled={busy}
                              title="永久删除资产"
                              aria-label="永久删除资产"
                            >
                              {busy ? <Loader2 className="animate-spin" /> : <Trash2 />}
                            </button>
                          ) : artifact.kind === 'digest_audio_zh' ? (
                            <button
                              type="button"
                              className="rowact-btn"
                              onClick={() => handleWithdraw(artifact)}
                              disabled={busy}
                              title="下架资产"
                              aria-label="下架资产"
                            >
                              {busy ? <Loader2 className="animate-spin" /> : <ArchiveX />}
                            </button>
                          ) : null}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
