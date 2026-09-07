import { useCallback, useEffect, useRef, useState } from 'react';
import { ArchiveX, CheckCircle2, Download, Loader2, RefreshCw, Trash2, Volume2 } from 'lucide-react';

import {
  deletePodcastArtifact,
  cachePodcastSourceAudio,
  fetchPodcastArtifacts,
  fetchPodcastArtifactStats,
  fetchPodcastStageCapabilities,
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
  podcastArtifactRetentionLabel,
  podcastArtifactStatusMeta,
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
  const [capabilities, setCapabilities] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [gcBusy, setGcBusy] = useState(false);
  const [cacheBusy, setCacheBusy] = useState(false);
  const [cacheEpisodeId, setCacheEpisodeId] = useState('');

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setLoadError('');
    try {
      const [nextStats, nextList, nextCapabilities] = await Promise.all([
        fetchPodcastArtifactStats(),
        fetchPodcastArtifacts({ limit: 100 }),
        fetchPodcastStageCapabilities(),
      ]);
      if (requestGeneration.current !== generation) return;
      setStats(nextStats);
      setItems(Array.isArray(nextList?.items) ? nextList.items : []);
      setCapabilities(nextCapabilities);
    } catch (error) {
      if (requestGeneration.current !== generation) return;
      const message = error.message || '加载失败：后端未响应，请确认服务已启动后重试';
      setLoadError(message);
      showToast(message, 'error');
    } finally {
      if (requestGeneration.current === generation) setLoading(false);
    }
  }, [showToast]);

  const canCacheSourceAudio = capabilities?.installation === 'external'
    && Array.isArray(capabilities?.allowed_stages)
    && capabilities.allowed_stages.includes('fetch');

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
      `确认删除这份已下架或已过期的${kindLabel}登记？登记不可恢复；无引用文件会在宽限期后由安全回收清理。`,
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
      '确认标记已到期且未被处理任务引用的原始音频，并清理过期上传文件及无引用 blob？处理中缓存与中文精简音频不会被删除。',
    ))) return;
    setGcBusy(true);
    try {
      const result = await reconcilePodcastArtifacts();
      const count = Number(result.deleted_orphan_blobs || 0).toLocaleString();
      const bytes = formatPodcastArtifactBytes(result.deleted_bytes);
      const staging = Number(result.deleted_staging_files || 0).toLocaleString();
      const expired = Number(result.expired_source_records || 0).toLocaleString();
      const protectedCount = Number(result.expired_protected || 0).toLocaleString();
      showToast(`已标记 ${expired} 份到期缓存（保护 ${protectedCount} 份处理中缓存），回收 ${count} 个孤儿文件（${bytes}），清理 ${staging} 个上传临时文件`, 'success');
      await load();
    } catch (error) {
      showToast(error.message || '安全回收失败，请刷新后重试', 'error');
    } finally {
      setGcBusy(false);
    }
  };

  const handleCacheSource = async () => {
    const episodeId = cacheEpisodeId.trim();
    if (!episodeId) {
      showToast('请先填写 Podcast 单集 ID', 'error');
      return;
    }
    if (!(await confirm(
      `确认在外网节点抓取并临时缓存单集 ${episodeId} 的发布者原音频？系统会校验地址安全和存储配额。`,
    ))) return;
    setCacheBusy(true);
    try {
      const artifact = await cachePodcastSourceAudio(episodeId);
      showToast(`原音频已缓存，有效期至 ${formatPodcastArtifactTime(artifact.expires_at)}`, 'success');
      setCacheEpisodeId('');
      await load();
    } catch (error) {
      showToast(error.message || '缓存失败，请检查节点能力和 enclosure 地址', 'error');
    } finally {
      setCacheBusy(false);
    }
  };

  return (
    <>
      <div className="zone-head podcast-assets-head">
        <span className="zone-title">播客音频</span>
        <span className="zone-hint">原始音频按 TTL 临时缓存；中文精简音频本地持久保留；文件经宽限期安全回收</span>
        <span className="zone-acts flex flex-wrap gap-2">
          <label className="relative">
            <span className="sr-only">Podcast 单集 ID</span>
            <input
              className="form-input form-input-inline w-52"
              value={cacheEpisodeId}
              onChange={(event) => setCacheEpisodeId(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !cacheBusy) handleCacheSource();
              }}
              placeholder="Podcast 单集 ID"
              disabled={cacheBusy || !canCacheSourceAudio}
              title={canCacheSourceAudio ? undefined : '原音频只能由外网 all 节点缓存'}
            />
          </label>
          <button
            type="button"
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
            onClick={handleCacheSource}
            disabled={cacheBusy || !canCacheSourceAudio || !cacheEpisodeId.trim()}
            title={canCacheSourceAudio ? undefined : '原音频只能由外网 all 节点缓存'}
          >
            {cacheBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            缓存原音频
          </button>
          <button
            type="button"
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
            onClick={handleReconcile}
            disabled={gcBusy || !(
              Number(stats?.reclaimable_orphan_blobs || 0)
              + Number(stats?.stale_staging_files || 0)
              + Math.max(
                Number(stats?.source_audio_due || 0)
                - Number(stats?.expired_protected || 0),
                0,
              )
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

      {capabilities && !canCacheSourceAudio && (
        <div className="podcast-assets-state" role="status">
          当前节点不拥有外网抓取权限；原音频缓存入口已停用，中文音频生成与本地资产管理仍可使用。
        </div>
      )}

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
        <ArtifactKpi value={stats ? Number(stats.ready || 0).toLocaleString() : '—'} label="就绪" sub="可处理 / 待发布" />
        <ArtifactKpi value={stats ? Number(stats.published || 0).toLocaleString() : '—'} label="已发布" sub="读者可用" />
        <ArtifactKpi value={stats ? Number(stats.withdrawn || 0).toLocaleString() : '—'} label="已下架" sub="可物理删除" />
        <ArtifactKpi
          value={stats ? formatPodcastArtifactBytes(stats.source_audio_bytes) : '—'}
          label="原音频临时缓存"
          sub={stats ? `余量 ${formatPodcastArtifactBytes(stats.source_audio_quota_remaining_bytes)} / 下次到期 ${formatPodcastArtifactTime(stats.next_expiry_at)}` : '配置加载中'}
        />
        <ArtifactKpi
          value={stats ? Number(stats.expired_source_audio || 0).toLocaleString() : '—'}
          label="已过期缓存"
          sub={stats ? `处理中保护 ${Number(stats.expired_protected || 0).toLocaleString()} 份 / 最近对账 ${formatPodcastArtifactTime(stats.last_reconciled_at)}` : '等待对账'}
        />
        <ArtifactKpi
          value={stats ? formatPodcastArtifactBytes(stats.quota_remaining_bytes) : '—'}
          label="配额余量"
          sub={stats ? `总额 ${formatPodcastArtifactBytes(stats.quota_bytes)}` : '配置加载中'}
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
          label="下载与上传暂存"
          sub={stats ? `下载预留 ${formatPodcastArtifactBytes(stats.download_reserved_bytes)} / 过期文件 ${Number(stats.stale_staging_files || 0).toLocaleString()} 个` : '等待统计'}
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
            还没有播客音频；抓取原音频或生成中文精简版后，可在这里管理。
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
                  <th className="acct-th">保留策略</th>
                  <th className="acct-th" aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                {items.map((artifact) => {
                  const status = podcastArtifactStatusMeta(artifact.status, artifact.kind);
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
                        <span className="body-text">{podcastArtifactRetentionLabel(artifact.retention_state)}</span>
                        {artifact.kind === 'source_audio' && artifact.expires_at && (
                          <span className="tiny-meta block" title={artifact.expires_at}>
                            到期 {formatPodcastArtifactTime(artifact.expires_at)}
                          </span>
                        )}
                      </td>
                      <td>
                        <span className="rowacts podcast-assets-actions">
                          {artifact.status !== 'expired' && (
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
                          )}
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
                          {['withdrawn', 'expired'].includes(artifact.status) ? (
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
