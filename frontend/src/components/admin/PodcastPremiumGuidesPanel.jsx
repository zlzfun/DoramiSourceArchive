import { useCallback, useEffect, useState } from 'react';
import { Headphones, Loader2, Play, RefreshCw, RotateCcw, Save } from 'lucide-react';

import {
  fetchPodcastPremiumTasks,
  forcePodcastFullAnalysis,
  forcePodcastPremiumTts,
  updatePodcastPremiumThreshold,
} from '../../api';
import Pager from './Pager';
import { podcastTtsStatusMeta } from '../../utils/podcastPremiumGuide';

const PAGE_SIZE = 100;
const FILTERS = [
  ['all', '全部'], ['pending_full', '待全文'], ['processing', '处理中'],
  ['premium', '当前优质'], ['below_threshold', '未达门槛'], ['failed', '失败'],
];
const STAGE_LABELS = {
  not_processed: '尚未初评', not_selected: '未进入全文', awaiting_transcript: '无逐字稿',
  asr_processing: 'ASR 阶段', full_analysis: '全文分析', processing: '处理中',
  full_analyzed: '全文已完成', failed: '处理失败',
};
const score = (value) => (value == null ? '—' : Number(value).toFixed(1));

function Stat({ value, label, sub }) {
  return <div className="kpi"><strong className="kpi-num tabular-nums">{value}</strong><span className="kpi-lbl">{label}</span><span className="kpi-sub">{sub}</span></div>;
}

export default function PodcastPremiumGuidesPanel({ showToast, refreshTick = 0 }) {
  const [state, setState] = useState({ loading: true, items: [], threshold: 8.0, initialThreshold: 5.0, stats: {}, total: 0, totalPages: 0, error: '' });
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState('all');
  const [draft, setDraft] = useState('8.0');
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState({ episodeId: '', action: '' });

  const load = useCallback(async (targetPage = 1, targetFilter = filter) => {
    setState((current) => ({ ...current, loading: true, error: '' }));
    try {
      const data = await fetchPodcastPremiumTasks({ status: targetFilter, page: targetPage, page_size: PAGE_SIZE });
      const threshold = Number(data?.threshold ?? 8.0);
      setState({
        loading: false,
        items: Array.isArray(data?.items) ? data.items : [],
        threshold,
        initialThreshold: Number(data?.initial_processing_threshold ?? 5.0),
        stats: data?.stats || {},
        total: Number(data?.total ?? 0),
        totalPages: Number(data?.total_pages ?? 0),
        error: '',
      });
      setDraft(threshold.toFixed(1));
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error: error.message || '载入失败：请确认后端已启动后重试' }));
    }
  }, [filter]);

  useEffect(() => { load(page, filter); }, [load, page, filter, refreshTick]);
  useEffect(() => { if (state.totalPages > 0 && page > state.totalPages) setPage(state.totalPages); }, [page, state.totalPages]);
  const activeTts = state.items.some((item) => podcastTtsStatusMeta(item).active);
  useEffect(() => {
    if (!activeTts || state.loading) return undefined;
    const timer = window.setTimeout(() => load(page, filter), 3000);
    return () => window.clearTimeout(timer);
  }, [activeTts, filter, load, page, state.loading, state.items]);

  const changeFilter = (next) => { setFilter(next); setPage(1); };

  const saveThreshold = async () => {
    const value = Number(draft);
    if (!/^\d+(?:\.\d)?$/.test(draft.trim()) || value < 1 || value > 10) {
      showToast('门槛需为 1.0–10.0 之间的一位小数', 'error');
      return;
    }
    setSaving(true);
    try {
      const saved = await updatePodcastPremiumThreshold(value);
      const effective = Number(saved?.threshold);
      setDraft(effective.toFixed(1));
      showToast(`已更新优质门槛为 ${effective.toFixed(1)}`, 'success');
      setPage(1);
      await load(1, filter);
    } catch (error) {
      showToast(error.message || '保存失败：请检查门槛后重试', 'error');
    } finally { setSaving(false); }
  };

  const run = async (item) => {
    setRunning({ episodeId: item.episode_id, action: 'full' });
    try {
      await forcePodcastFullAnalysis(item.episode_id, '', {
        processing_id: item.processing_id,
        processing_status: item.processing_status,
        attempt_count: item.attempt_count,
      });
      showToast(item.can_retry ? '已重试全文处理' : '已启动全文处理', 'success');
      await load(page, filter);
    } catch (error) {
      showToast(error.message || '启动失败：请检查逐字稿或原节目音频后重试', 'error');
    } finally { setRunning({ episodeId: '', action: '' }); }
  };

  const forceTts = async (item) => {
    setRunning({ episodeId: item.episode_id, action: 'tts' });
    try {
      const result = await forcePodcastPremiumTts(item.episode_id);
      const resultStatus = result?.status;
      showToast(
        resultStatus === 'ready'
          ? 'TTS 已生成'
          : result?.started === false ? `TTS 当前状态：${resultStatus || '处理中'}` : '已强制启动 TTS，列表会自动更新进度',
        'success',
      );
      await load(page, filter);
    } catch (error) {
      showToast(error.message || '启动强制 TTS 失败，请检查全文分析和 TTS 配置', 'error');
    } finally { setRunning({ episodeId: '', action: '' }); }
  };

  return (
    <>
      <div className="zone-head">
        <span className="zone-title">播客优质门槛与全文任务</span>
        <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => load(page, filter)} disabled={state.loading}>
          {state.loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}刷新任务
        </button>
      </div>

      <section className="surface-card podcast-premium-settings rounded-[var(--r-card)]">
        <div>
          <label className="form-label" htmlFor="podcast-premium-threshold">全文终评优质门槛</label>
          <div className="podcast-premium-threshold-row">
            <input id="podcast-premium-threshold" className="form-input form-input-inline" type="number" min="1" max="10" step="0.1" value={draft} onChange={(event) => setDraft(event.target.value)} />
            <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={saveThreshold} disabled={saving || state.loading}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}{saving ? '保存中…' : '保存门槛'}
            </button>
          </div>
        </div>
        <div className="podcast-premium-readonly">
          <span className="form-label">简介初评处理线</span>
          <strong className="tabular-nums">≥ {state.initialThreshold.toFixed(1)}</strong>
          <span className="tiny-meta">固定规则，只决定是否自动进入全文处理</span>
        </div>
      </section>

      <section className="surface-card kpi-strip" aria-label="播客全文任务概览">
        <Stat value={state.stats.total ?? '—'} label="播客总数" sub="全部归档单集" />
        <Stat value={state.stats.full_analyzed ?? '—'} label="全文分析完成" sub="已有全文终评分" />
        <Stat value={state.stats.premium ?? '—'} label="当前优质" sub={`全文终评 ≥ ${state.threshold.toFixed(1)}`} />
        <Stat value={state.stats.pending_or_failed ?? '—'} label="待处理 / 失败" sub="需继续处理或人工重试" />
      </section>

      <div className="podcast-premium-filterbar">
        <div className="mini-seg" aria-label="全文任务状态筛选">
          {FILTERS.map(([value, label]) => <button key={value} type="button" className={`mini-seg-btn ${filter === value ? 'is-on' : ''}`} aria-pressed={filter === value} onClick={() => changeFilter(value)}>{label}</button>)}
        </div>
      </div>

      <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
        {state.error ? <p className="podcast-assets-state is-error">{state.error}</p> : state.items.length === 0 && !state.loading ? (
          <p className="podcast-assets-state">当前筛选下没有任务，可切换「全部」查看所有播客</p>
        ) : (
          <>
            <div className="acct-scroll"><table className="acct-table is-fixed podcast-premium-table">
              <thead><tr><th className="acct-th">节目</th><th className="acct-th">评分 / 依据</th><th className="acct-th">处理阶段</th><th className="acct-th">判定</th><th className="acct-th">未处理 / 未入选原因</th><th className="acct-th">成品 / TTS 进度</th><th className="acct-th">操作</th></tr></thead>
              <tbody>{state.items.map((item) => {
                const tts = podcastTtsStatusMeta(item);
                return (
                <tr key={item.episode_id} className="acct-row is-static">
                  <td><strong className="podcast-premium-title">{item.title}</strong><span className="tiny-meta block">{item.source_name}</span></td>
                  <td className="tabular-nums"><span className="podcast-premium-scoreline">简介 {score(item.initial_score)} · 全文 {score(item.final_score)}</span><span className="tiny-meta block">当前 {score(item.current_score)} · {item.current_basis}</span></td>
                  <td><span className={`stamp ${item.stage === 'failed' ? 'stamp-bad' : item.stage === 'full_analyzed' ? 'stamp-ok' : ['asr_processing', 'full_analysis', 'processing'].includes(item.stage) ? 'stamp-run' : 'stamp-idle'}`}>{STAGE_LABELS[item.stage] || item.stage}</span></td>
                  <td><span className="tiny-meta block">简介线 {item.initial_eligible ? '已通过' : '未通过'}</span><span className="tiny-meta block">优质线 {item.is_premium ? '已达到' : '未达到'}</span></td>
                  <td><span className="podcast-premium-reason">{item.reason}</span></td>
                  <td><div className="podcast-premium-tts-state">
                    <span className="tiny-meta">博客 {item.blog_ready ? '已生成' : '—'}</span>
                    <span className={`stamp stamp-${tts.tone}`} role={tts.active ? 'status' : undefined}>TTS {tts.label}</span>
                    {tts.forced && <span className="tiny-meta">管理员强制生成 · 不改变优质判定</span>}
                    {tts.error && <span className="podcast-premium-tts-error" title={tts.error}>{tts.error}</span>}
                    {item.historical_generated && !item.is_premium && !tts.forced && <span className="stamp stamp-warn">历史已生成，当前未达门槛</span>}
                  </div></td>
                  <td><div className="podcast-premium-actions">
                    {(item.can_retry || item.can_force) && <button type="button" className="podcast-premium-action" onClick={() => run(item)} disabled={running.episodeId === item.episode_id}>{running.episodeId === item.episode_id && running.action === 'full' ? <Loader2 className="animate-spin" /> : item.can_retry ? <RotateCcw /> : <Play />}{item.can_retry ? '失败重试' : '强制全文'}</button>}
                    {item.can_force_tts && <button type="button" className="podcast-premium-action is-tts" title="跳过自动优质筛选，使用已完成的全文分析生成 TTS 音频" onClick={() => forceTts(item)} disabled={running.episodeId === item.episode_id}>{running.episodeId === item.episode_id && running.action === 'tts' ? <Loader2 className="animate-spin" /> : <Headphones />}强制 TTS</button>}
                  </div></td>
                </tr>
                );
              })}</tbody>
            </table></div>
            {state.totalPages > 1 && <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5"><span className="tiny-meta">共 {state.total} 条 · 第 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, state.total)} 条</span><Pager page={page} totalPages={state.totalPages} onPage={setPage} /></div>}
          </>
        )}
      </section>
    </>
  );
}
