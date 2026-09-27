import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchStorageStatus } from '../../api';
import { formatPodcastArtifactBytes, formatPodcastArtifactTime } from '../../utils/podcastArtifactAdmin';
import { hasStorageOperations, storageBackupMeta, storageCacheMeta, storageHealthMeta } from '../../utils/storageStatus';
import StaleNotice from './StaleNotice';

const bytes = (value) => value == null ? '—' : formatPodcastArtifactBytes(value);
const time = formatPodcastArtifactTime;

function Stamp({ meta }) {
  return <span className={`stamp stamp-${meta.tone}`}>{meta.label}</span>;
}

function StorageRow({ name, data }) {
  const cache = data.cache ?? {};
  const health = data.storage_health ?? {};
  return (
    <div className="grid gap-4 py-4 sm:grid-cols-3">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="section-title">{name}</h3>
          <Stamp meta={storageHealthMeta(health)} />
        </div>
        <p className="tiny-meta mt-2">云端持久存储 <span className="tabular-nums text-slate-800">{bytes(data.remote_bytes)}</span></p>
        <p className="tiny-meta mt-1 tabular-nums">已登记 {data.remote_objects == null ? '—' : Number(data.remote_objects).toLocaleString()} 个对象</p>
        {health.last_error ? (
          <p className="tiny-meta mt-2" role="alert">云端读写失败，请检查存储连接与访问权限</p>
        ) : health.last_success_at ? (
          <p className="tiny-meta mt-2 tabular-nums">最近读写 {time(health.last_success_at)}</p>
        ) : null}
      </div>
      <dl>
        <dt className="tiny-meta">本地缓存</dt>
        <dd className="body-text tabular-nums mt-1">{bytes(cache.local_bytes)}</dd>
        {cache.enabled && <dd className="tiny-meta mt-1 tabular-nums">缓存目标 {bytes(cache.max_bytes)}</dd>}
      </dl>
      <div>
        <Stamp meta={storageCacheMeta(cache)} />
        {cache.last_run_at && <p className="tiny-meta mt-2 tabular-nums">最近检查 {time(cache.last_run_at)}</p>}
        {cache.last_run_at && <p className="tiny-meta mt-1 tabular-nums">本次回收 {Number(cache.evicted_files || 0).toLocaleString()} 个文件 / {bytes(cache.evicted_bytes)}</p>}
        {cache.last_error && <p className="tiny-meta mt-2" role="alert">缓存回收失败，请检查后台维护日志</p>}
      </div>
    </div>
  );
}

export default function StorageStatusPanel({ refreshTick = 0 }) {
  const [state, setState] = useState({ data: null, status: 'loading' });
  const sequence = useRef(0);
  const request = useRef(null);
  const load = useCallback(async () => {
    const id = ++sequence.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState((previous) => ({ ...previous, status: 'loading' }));
    try {
      const data = await fetchStorageStatus({ signal: controller.signal });
      if (id === sequence.current) setState({ data, status: 'ready' });
    } catch (error) {
      if (id !== sequence.current || controller.signal.aborted) return;
      setState((previous) => ({ ...previous, status: error.status === 404 ? 'unavailable' : 'error' }));
    }
  }, []);

  useEffect(() => {
    load();
    return () => { sequence.current += 1; request.current?.abort(); };
  }, [load, refreshTick]);

  const { data, status } = state;
  const visible = hasStorageOperations(data);
  // Old/local deployments retain the original page. Once enabled, keep the last
  // snapshot visible through failures instead of making an outage look disabled.
  if (!visible && (data || status !== 'error')) return null;
  const backup = data?.backup ?? {};
  const remote = ['media', 'podcast'].filter((kind) => data?.[kind]?.storage_backend === 'oss');
  const refreshing = status === 'loading';

  return (
    <section className="surface-card card-pad rounded-[var(--r-card)] mb-4" aria-label="存储与备份">
      <div className="card-head">
        <h2 className="card-title">存储与备份</h2>
        <StaleNotice
          status={status}
          label="存储状态"
          error={status === 'unavailable' ? '当前后端版本未提供存储状态' : '无法读取最新状态，请确认服务连接后重试'}
          onRetry={load}
        />
        <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs ml-auto" onClick={load} disabled={refreshing}>
          {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
          刷新
        </button>
      </div>
      {data && (
        <>
          <div className="divide-y divide-[var(--dorami-border)]">
            {remote.map((kind) => <StorageRow key={kind} name={kind === 'media' ? '图片' : '生成音频'} data={data[kind]} />)}
          </div>
          {remote.length > 0 && <p className="tiny-meta mb-4">云端用量按本项目已登记对象统计，实际计费以云平台账单为准</p>}
          <div className={`flex flex-wrap items-baseline gap-x-4 gap-y-2${remote.length > 0 ? ' border-t border-[var(--dorami-border)] pt-4' : ' pt-3'}`}>
            <h3 className="section-title">自动备份</h3>
            <Stamp meta={storageBackupMeta(backup)} />
            {backup.enabled && <span className="tiny-meta">{backup.destination === 'oss' ? '保存至云端' : backup.destination === 'local' ? '保存至本地' : '已配置备份位置'}</span>}
            {backup.last_success_at && <span className="tiny-meta tabular-nums">最近完成 {time(backup.last_success_at)}</span>}
            {backup.last_size_bytes != null && <span className="tiny-meta tabular-nums">{bytes(backup.last_size_bytes)}</span>}
            {backup.last_error && <p className="tiny-meta w-full" role="alert">最近备份未完成，请检查备份位置与后台维护日志{backup.last_attempt_at ? `（${time(backup.last_attempt_at)}）` : ''}</p>}
          </div>
        </>
      )}
    </section>
  );
}
