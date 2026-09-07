import { useCallback, useEffect, useState } from 'react';
import { Loader2, Play, RefreshCw } from 'lucide-react';

import { fetchPodcastPremiumGuides, runPodcastPremiumGuide } from '../../api';
import Pager from './Pager';

const PAGE_SIZE = 100;

const STATUS_LABELS = {
  not_started: '未开始',
  summarizing: '生成中文博客',
  synthesizing: '生成导读音频',
  ready: '已完成',
  not_required: '非精品',
  failed: '失败',
};

export default function PodcastPremiumGuidesPanel({ showToast, refreshTick = 0 }) {
  const [state, setState] = useState({ loading: true, items: [], threshold: 8.5, total: 0, totalPages: 0, error: '' });
  const [page, setPage] = useState(1);
  const [running, setRunning] = useState('');

  const load = useCallback(async (targetPage) => {
    setState((current) => ({ ...current, loading: true, error: '' }));
    try {
      const data = await fetchPodcastPremiumGuides({ page: targetPage, page_size: PAGE_SIZE });
      setState({
        loading: false,
        items: Array.isArray(data?.items) ? data.items : [],
        threshold: Number(data?.threshold ?? 8.5),
        total: Number(data?.total ?? 0),
        totalPages: Number(data?.total_pages ?? 0),
        error: '',
      });
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error: error.message || '载入失败' }));
    }
  }, []);

  useEffect(() => { load(page); }, [load, page, refreshTick]);

  useEffect(() => {
    if (state.totalPages > 0 && page > state.totalPages) setPage(state.totalPages);
  }, [page, state.totalPages]);

  const run = async (item) => {
    setRunning(item.episode_id);
    try {
      await runPodcastPremiumGuide(item.episode_id);
      showToast('精品导读任务已启动', 'success');
      await load(page);
    } catch (error) {
      showToast(error.message || '启动失败', 'error');
    } finally {
      setRunning('');
    }
  };

  return (
    <>
      <div className="zone-head">
        <span className="zone-title">精品导读任务</span>
        <span className="zone-hint">只展示评分高于 {state.threshold} 分的精品播客；当前只生成最长 15 分钟的单人速览</span>
        <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => load(page)} disabled={state.loading}>
          {state.loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}刷新任务
        </button>
      </div>
      <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
        {state.error ? <p className="podcast-assets-state is-error">{state.error}</p> : state.items.length === 0 && !state.loading ? (
          <p className="podcast-assets-state">暂无评分高于 {state.threshold} 分的精品播客</p>
        ) : (
          <>
          <div className="acct-scroll">
            <table className="acct-table is-fixed podcast-premium-table">
              <thead><tr><th className="acct-th">播客</th><th className="acct-th">评分</th><th className="acct-th">中文博客</th><th className="acct-th">导读音频</th><th className="acct-th">状态</th><th className="acct-th">操作</th></tr></thead>
              <tbody>{state.items.map((item) => (
                <tr key={item.episode_id} className="acct-row">
                  <td><strong className="podcast-premium-title">{item.title}</strong><span className="tiny-meta block">{item.source_id}</span></td>
                  <td>{item.quality_score == null ? '—' : Number(item.quality_score).toFixed(1)} {item.is_premium && <span className="podcast-premium-badge">优质播客</span>}</td>
                  <td>{item.blog_ready ? '已完成' : '未完成'}</td>
                  <td>{item.audio_ready ? '已完成' : '未完成'}</td>
                  <td><span className={`stamp ${item.status === 'failed' ? 'stamp-bad' : item.status === 'ready' ? 'stamp-ok' : 'stamp-idle'}`}>{STATUS_LABELS[item.status] || item.status}</span>{item.error && <span className="tiny-meta block">{item.error}</span>}</td>
                  <td><button type="button" className="action-button action-button-secondary min-h-[30px] px-2 text-xs" onClick={() => run(item)} disabled={running === item.episode_id || ['summarizing', 'synthesizing'].includes(item.status)}>{running === item.episode_id ? <Loader2 className="animate-spin" /> : <Play />}运行</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {state.totalPages > 1 && (
            <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5">
              <span className="tiny-meta">
                共 {state.total} 条 · 第 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, state.total)} 条
              </span>
              <Pager page={page} totalPages={state.totalPages} onPage={setPage} />
            </div>
          )}
          </>
        )}
      </section>
    </>
  );
}
