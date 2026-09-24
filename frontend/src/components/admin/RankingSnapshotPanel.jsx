import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchAdminRankingStatus, refreshAdminRankings } from '../../api';
import { formatPodcastArtifactTime } from '../../utils/podcastArtifactAdmin';
import { rankingCoverageText, rankingSnapshotStatusMeta } from '../../utils/rankingAdmin';
import StaleNotice from './StaleNotice';

export default function RankingSnapshotPanel({ showToast, refreshTick = 0 }) {
  const [state, setState] = useState({ data: null, status: 'loading' });
  const [refreshing, setRefreshing] = useState(false);
  const sequence = useRef(0);
  const request = useRef(null);

  const load = useCallback(async () => {
    const id = ++sequence.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState((previous) => ({ ...previous, status: 'loading' }));
    try {
      const data = await fetchAdminRankingStatus({ signal: controller.signal });
      if (id === sequence.current) setState({ data, status: 'ready' });
    } catch {
      if (id !== sequence.current || controller.signal.aborted) return;
      setState((previous) => ({ ...previous, status: 'error' }));
    }
  }, []);

  useEffect(() => {
    load();
    return () => { sequence.current += 1; request.current?.abort(); };
  }, [load, refreshTick]);

  const handleRefresh = async () => {
    if (refreshing || state.data?.refresh_running) return;
    setRefreshing(true);
    try {
      const data = await refreshAdminRankings();
      setState({ data, status: 'ready' });
      showToast('已刷新榜单快照', 'success');
    } catch (error) {
      showToast(error.message || '刷新榜单失败，请稍后再试', 'error');
      await load();
    } finally {
      setRefreshing(false);
    }
  };

  const { data, status } = state;
  const snapshot = data?.snapshot;
  const meta = rankingSnapshotStatusMeta({ ...data, refresh_running: refreshing || data?.refresh_running });
  const busy = refreshing || data?.refresh_running;

  return (
    <section className="surface-card card-pad rounded-[var(--r-card)] mb-4" aria-label="榜单快照">
      <div className="card-head">
        <h2 className="card-title">榜单快照</h2>
        <span className={`stamp stamp-${meta.tone}`}>{meta.label}</span>
        <StaleNotice
          status={status}
          label="榜单状态"
          error="无法读取榜单状态，请确认服务连接后重试"
          onRetry={load}
        />
        <button
          type="button"
          className="action-button action-button-primary min-h-[32px] px-3 text-xs ml-auto"
          onClick={handleRefresh}
          disabled={busy || status === 'loading'}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
          {busy ? '刷新中…' : '刷新榜单'}
        </button>
      </div>
      {snapshot ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <p className="body-text">最近生成 <span className="tabular-nums">{formatPodcastArtifactTime(snapshot.generated_at)}</span></p>
            <p className="tiny-meta mt-1">数据窗口截至每日 07:00，全站公共内容，不受个人订阅限制</p>
          </div>
          <div>
            <p className="body-text">{rankingCoverageText(snapshot)}</p>
            <p className="tiny-meta mt-1 tabular-nums">下次自动刷新 {formatPodcastArtifactTime(data.next_refresh_at)}</p>
          </div>
        </div>
      ) : (
        <p className="tiny-meta">还没有榜单快照。读者首次打开榜单时会自动生成，也可以现在手动刷新。</p>
      )}
    </section>
  );
}
