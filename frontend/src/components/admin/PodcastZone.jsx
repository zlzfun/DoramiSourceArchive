import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';

import {
  deletePodcastArtifact,
  fetchPodcastArtifactStats,
  fetchPodcastArtifacts,
  fetchPodcastAsrQuota,
  fetchPodcastPremiumTasks,
  forcePodcastFullAnalysis,
  forcePodcastPremiumTts,
  publishPodcastArtifact,
  reconcilePodcastArtifacts,
  updatePodcastPremiumThreshold,
  withdrawPodcastArtifact,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { usePolling } from '../../hooks/usePolling';
import { formatPodcastArtifactBytes, podcastArtifactKindLabel } from '../../utils/podcastArtifactAdmin';
import {
  podcastForceTtsResult,
  podcastProcessingSuccessMessage,
  podcastScoreText,
  podcastTaskMeta,
} from '../../utils/podcastProcessing';
import { Kpi, KpiState } from './Kpi';
import PodcastAudioTable, { PODCAST_AUDIO_PAGE_SIZE } from './PodcastAudioTable';
import PodcastEpisodeDrawer from './PodcastEpisodeDrawer';
import PodcastTasksTable, { PODCAST_TASKS_PAGE_SIZE } from './PodcastTasksTable';
import StaleNotice from './StaleNotice';

const TASK_FILTERS_INIT = { q: '', stage: '', verdict: '', tts: '', sort: 'publish', order: 'desc', page: 1 };
const AUDIO_FILTERS_INIT = { q: '', status: '', sort: 'created', order: 'desc', page: 1 };
const IDLE = { status: 'idle', data: null, error: '' };

// 每组 loader 一个代次 ref:只允许最新请求落数据 / 错误 / loading(归并稿 P1 #5)。
function useLoader(fetcher) {
  const genRef = useRef(0);
  const [state, setState] = useState(IDLE);
  const load = useCallback(async (args, { quiet = false } = {}) => {
    const gen = ++genRef.current;
    if (!quiet) setState((prev) => ({ status: 'loading', data: prev.data, error: '' }));
    try {
      const data = await fetcher(args);
      if (gen === genRef.current) setState({ status: 'ok', data, error: '' });
      return data;
    } catch (error) {
      // 404 = 当前后端没有该端点(未接入),与普通失败分形;两者都保留旧快照(codex R3)
      if (gen === genRef.current) {
        setState((prev) => ({ status: error.status === 404 ? 'unavailable' : 'error', data: prev.data, error: error.message }));
      }
      return undefined;
    }
  }, [fetcher]);
  return [state, load];
}

const UNAVAILABLE_TEXT = '当前后端版本没有该端点';
const stateError = (state) => (state.status === 'error' ? state.error : state.status === 'unavailable' ? UNAVAILABLE_TEXT : '');

const fetchTasks = (filters) => fetchPodcastPremiumTasks({
  q: filters.q, stage: filters.stage, verdict: filters.verdict, tts: filters.tts,
  sort: filters.sort, order: filters.order, page: filters.page, page_size: PODCAST_TASKS_PAGE_SIZE,
});
const fetchAudio = (filters) => fetchPodcastArtifacts({
  kind: 'digest_audio_zh', q: filters.q, status: filters.status, sort: filters.sort, order: filters.order,
  offset: (filters.page - 1) * PODCAST_AUDIO_PAGE_SIZE, limit: PODCAST_AUDIO_PAGE_SIZE,
});
const fetchStats = () => fetchPodcastArtifactStats();
const fetchQuota = () => fetchPodcastAsrQuota();

/**
 * 运维管理 → 内容 → 「播客」分区(issue #76,样页 docs/design/dorami-admin-podcast-taxonomy-quiet.html)。
 * 区头(quiet 刷新)→ KPI 六格 → 处理参数开关板(优质门槛 + 固定付费 ASR 线 + ASR 配额 chip)→
 * 单集处理表 → 中文精简音频表;三处整行可点开同一个单集抽屉。四组数据各自 loading/error/data,
 * 一组失败不挡其它;活动态行存在时 3s 轮询任务表(静默,不闪 loading)。
 */
export default function PodcastZone({ showToast, refreshTick = 0, onOpenCredentials }) {
  const confirm = useConfirm();
  const [taskFilters, setTaskFilters] = useState(TASK_FILTERS_INIT);
  const [audioFilters, setAudioFilters] = useState(AUDIO_FILTERS_INIT);
  const taskQ = useDebouncedValue(taskFilters.q, 300);
  const audioQ = useDebouncedValue(audioFilters.q, 300);
  const taskQuery = useMemo(() => ({ ...taskFilters, q: taskQ.trim() }), [taskFilters, taskQ]);
  const audioQuery = useMemo(() => ({ ...audioFilters, q: audioQ.trim() }), [audioFilters, audioQ]);
  const [tasks, loadTasks] = useLoader(fetchTasks);
  const [audio, loadAudio] = useLoader(fetchAudio);
  const [stats, loadStats] = useLoader(fetchStats);
  const [quota, loadQuota] = useLoader(fetchQuota);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState({ episodeId: '', action: '' });
  const [busyId, setBusyId] = useState(null);
  const [gcBusy, setGcBusy] = useState(false);
  const [drawerId, setDrawerId] = useState(null);
  const [drawerTick, setDrawerTick] = useState(0);

  useEffect(() => { loadTasks(taskQuery); }, [loadTasks, taskQuery]);
  useEffect(() => { loadAudio(audioQuery); }, [loadAudio, audioQuery]);
  useEffect(() => { loadStats(); loadQuota(); }, [loadStats, loadQuota]);
  useEffect(() => {
    if (refreshTick > 0) { loadTasks(taskQuery, { quiet: true }); loadAudio(audioQuery, { quiet: true }); loadStats(); loadQuota(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应「切回 Tab / 切子页」刷新脉冲
  }, [refreshTick]);
  useEffect(() => {
    const threshold = tasks.data?.threshold;
    if (threshold != null) setDraft(Number(threshold).toFixed(1));
  }, [tasks.data?.threshold]);

  const thresholds = useMemo(() => ({
    initial: tasks.data?.initial_processing_threshold ?? 6,
    premium: tasks.data?.threshold ?? 7.5,
  }), [tasks.data?.initial_processing_threshold, tasks.data?.threshold]);

  // 活动态(处理中 / TTS 生成中)存在时静默轮询;抽屉开着也同步刷新。
  const anyActive = useMemo(
    () => (tasks.data?.items ?? []).some((item) => podcastTaskMeta(item, thresholds).active),
    [tasks.data?.items, thresholds],
  );
  const poll = useCallback(async () => {
    await loadTasks(taskQuery, { quiet: true });
    if (drawerId) setDrawerTick((t) => t + 1);
  }, [loadTasks, taskQuery, drawerId]);
  usePolling(poll, 3000, { immediate: false, enabled: anyActive });

  const refreshAll = useCallback(async () => {
    await Promise.all([loadTasks(taskQuery, { quiet: true }), loadAudio(audioQuery, { quiet: true }), loadStats()]);
    if (drawerId) setDrawerTick((t) => t + 1);
  }, [loadTasks, taskQuery, loadAudio, audioQuery, loadStats, drawerId]);

  const patchTaskFilters = (patch) => setTaskFilters((prev) => ({ ...prev, ...patch }));
  const patchAudioFilters = (patch) => setAudioFilters((prev) => ({ ...prev, ...patch }));

  const thresholdDirty = tasks.data != null && draft !== Number(tasks.data.threshold).toFixed(1);
  const saveThreshold = async () => {
    const value = Number(draft);
    if (!/^\d+(?:\.\d)?$/.test(draft.trim()) || value < 1 || value > 10) {
      showToast('门槛需为 1.0–10.0 之间的一位小数', 'error');
      return;
    }
    setSaving(true);
    try {
      const saved = await updatePodcastPremiumThreshold(value);
      showToast(`已更新优质门槛为 ${Number(saved?.threshold).toFixed(1)}`, 'success');
      patchTaskFilters({ page: 1 });
      await loadTasks({ ...taskQuery, page: 1 }, { quiet: true });
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setSaving(false); }
  };

  const runProcessing = async (item) => {
    setRunning({ episodeId: item.episode_id, action: 'full' });
    try {
      await forcePodcastFullAnalysis(item.episode_id, '', {
        processing_id: item.processing_id,
        processing_status: item.processing_status,
        processing_stage: item.processing_stage,
        attempt_count: item.attempt_count,
      });
      showToast(podcastProcessingSuccessMessage(item), 'success');
      await refreshAll();
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setRunning({ episodeId: '', action: '' }); }
  };

  const handleRetry = (item) => runProcessing(item);

  const handleForceFull = async (item) => {
    const initial = item.initial_score == null ? '尚未初评' : `简介初评 ${podcastScoreText(item.initial_score)}${item.initial_eligible ? '，尚未进入全文处理' : `，未过付费 ASR 线 ${podcastScoreText(thresholds.initial)}`}`;
    const inputCost = item.publisher_transcript_available
      ? '将优先获取发布方逐字稿并进行全文分析；若逐字稿不可用，可能下载原节目音频并提交 ASR，消耗 ASR 日配额。'
      : '将准备原节目音频、提交 ASR 转录并进行全文分析；消耗 ASR 日配额。';
    if (!(await confirm({
      title: '强制全文处理',
      message: `「${item.title}」${initial}。\n${inputCost}`,
      confirmText: '开始处理',
      tone: 'primary',
    }))) return;
    await runProcessing(item);
  };

  const handleForceTts = async (item) => {
    const verdict = item.is_premium ? '已达优质门槛' : `未达优质门槛 ${podcastScoreText(thresholds.premium)}`;
    if (!(await confirm({
      title: '强制生成 TTS',
      message: `「${item.title}」全文终评 ${podcastScoreText(item.final_score)}，${verdict}。\n跳过自动优质筛选，按已完成的全文分析合成中文精简音频；消耗 TTS 额度，不改变优质判定。`,
      confirmText: '生成 TTS',
      tone: 'primary',
    }))) return;
    setRunning({ episodeId: item.episode_id, action: 'tts' });
    try {
      const result = await forcePodcastPremiumTts(item.episode_id);
      const toast = podcastForceTtsResult(result);
      showToast(toast.message, toast.tone);
      await refreshAll();
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setRunning({ episodeId: '', action: '' }); }
  };

  const artifactAction = async (artifact, { confirmOptions, run, done }) => {
    if (!(await confirm(confirmOptions))) return;
    setBusyId(artifact.id);
    try {
      const result = await run();
      showToast(done(result), 'success');
      await refreshAll();
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setBusyId(null); }
  };

  const handlePublish = (artifact) => artifactAction(artifact, {
    confirmOptions: { title: '发布中文精简版', message: `发布「${artifact.episode_title || artifact.episode_id}」的中文精简音频，读者可听；系统会再次校验文件和并发版本。`, confirmText: '发布', tone: 'primary' },
    run: () => publishPodcastArtifact(artifact.id, artifact.updated_at),
    done: () => '已发布中文精简版',
  });
  const handleWithdraw = (artifact) => artifactAction(artifact, {
    confirmOptions: { title: '下架中文精简版', message: `下架「${artifact.episode_title || artifact.episode_id}」的中文精简音频，读者将无法继续访问，文件会保留以便核查。`, confirmText: '下架' },
    run: () => withdrawPodcastArtifact(artifact.id),
    done: () => `已下架${podcastArtifactKindLabel(artifact.kind)}`,
  });
  const handleDelete = (artifact) => artifactAction(artifact, {
    confirmOptions: { title: '永久删除', message: `删除「${artifact.episode_title || artifact.episode_id}」这份已下架的中文精简音频登记？登记不可恢复；无引用文件会在宽限期后由安全回收清理。`, confirmText: '删除' },
    run: () => deletePodcastArtifact(artifact.id),
    done: (result) => (result?.blob_deleted ? '已删除中文精简版及本地文件' : '已删除中文精简版记录'),
  });

  const handleReconcile = async () => {
    if (!(await confirm({
      title: '安全回收',
      message: '清理过期的临时校验文件、失效预留标记和无引用音频文件；数据库仍引用的中文精简音频不会被删除。',
      confirmText: '回收',
      tone: 'primary',
    }))) return;
    setGcBusy(true);
    try {
      const result = await reconcilePodcastArtifacts();
      showToast(`已回收 ${Number(result.deleted_orphan_blobs || 0).toLocaleString()} 个孤儿文件（${formatPodcastArtifactBytes(result.deleted_bytes)}），清理 ${Number(result.deleted_staging_files || 0).toLocaleString()} 个临时校验文件`, 'success');
      await refreshAll();
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setGcBusy(false); }
  };

  const stageFilterLink = (stage, label) => (
    <button type="button" className={`kpi-sub-link ${taskFilters.stage === stage ? 'is-on' : ''}`} onClick={() => patchTaskFilters({ stage: taskFilters.stage === stage ? '' : stage, page: 1 })}>
      {label}
    </button>
  );

  const taskData = tasks.data;
  const statsData = stats.data;
  const breakdown = taskData?.breakdown ?? {};
  const stageCounts = breakdown.stage ?? {};
  const refreshing = tasks.status === 'loading' || audio.status === 'loading' || stats.status === 'loading';
  const quotaData = quota.data;

  return (
    <>
      <div className="zone-head">
        <span className="zone-title">播客</span>
        {statsData?.storage_pressure && <span className="stamp stamp-bad">存储容量保护中</span>}
        {statsData && <StaleNotice status={stats.status} error={stats.error} onRetry={() => loadStats()} label="音频统计" />}
        {quotaData && <StaleNotice status={quota.status} error={quota.error} onRetry={() => loadQuota()} label="ASR 配额" />}
        <span className="zone-acts">
          <button
            type="button"
            className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
            onClick={() => { refreshAll(); loadQuota(); }}
            disabled={refreshing}
          >
            {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} 刷新
          </button>
        </span>
      </div>

      <div className="grid gap-3">
        <section className="surface-card kpi-strip" aria-label="播客概览">
          {taskData ? (
            <>
              <Kpi num={Number(taskData.stats?.total ?? 0).toLocaleString()} label="播客单集" sub={`${Number(breakdown.shows ?? 0).toLocaleString()} 个节目`} />
              <Kpi num={Number(taskData.stats?.full_analyzed ?? 0).toLocaleString()} label="全文分析完成" sub={stageFilterLink('awaiting_transcript', `待全文 ${stageCounts.awaiting_transcript ?? 0}`)} />
              <Kpi num={Number(taskData.stats?.premium ?? 0).toLocaleString()} label="当前优质" sub={`≥ ${podcastScoreText(taskData.threshold)}`} />
              <Kpi
                num={Number(taskData.stats?.pending_or_failed ?? 0).toLocaleString()}
                label="待处理 / 失败"
                tone={(stageCounts.failed ?? 0) + (stageCounts.reconciliation ?? 0) > 0 ? 'is-warn' : undefined}
                sub={<>{stageFilterLink('failed', `失败 ${stageCounts.failed ?? 0}`)} · {stageFilterLink('reconciliation', `待对账 ${stageCounts.reconciliation ?? 0}`)}</>}
              />
            </>
          ) : (
            <KpiState label="单集统计" error={stateError(tasks)} onRetry={() => loadTasks(taskQuery)} />
          )}
          {statsData ? (
            <>
              <Kpi num={Number(statsData.published || 0).toLocaleString()} label="已发布音频" sub={`待发布 ${Number(statsData.ready || 0).toLocaleString()} · 已下架 ${Number(statsData.withdrawn || 0).toLocaleString()}`} />
              <Kpi
                num={formatPodcastArtifactBytes(statsData.disk_bytes)}
                label="本地占用"
                tone={statsData.storage_pressure ? 'is-bad' : undefined}
                sub={Number(statsData.quota_bytes) > 0
                  ? `配额余 ${formatPodcastArtifactBytes(statsData.quota_remaining_bytes)} · 磁盘余 ${formatPodcastArtifactBytes(statsData.disk_free_bytes)}`
                  : `不设配额 · 磁盘余 ${formatPodcastArtifactBytes(statsData.disk_free_bytes)}`}
              />
            </>
          ) : (
            <KpiState label="音频统计" error={stateError(stats)} onRetry={() => loadStats()} />
          )}
        </section>

        <section className="surface-card ai-switchboard is-wrap rounded-[var(--r-card)]" aria-label="播客处理参数">
          <span className="ai-switch-lbl">优质门槛</span>
          <label className="knob">
            全文终评 ≥
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && thresholdDirty) saveThreshold(); }}
              inputMode="decimal"
              aria-label="全文终评优质门槛"
              disabled={!taskData}
            />
          </label>
          <span className="ai-divider" />
          <span className="knob is-fixed">简介付费 ASR 线 <strong>≥ {podcastScoreText(thresholds.initial)}</strong></span>
          <span className="ai-divider" />
          <button
            type="button"
            className="model-chip"
            title="前往设置 → 凭据 编辑播客 ASR 配额"
            onClick={() => onOpenCredentials?.()}
          >
            <i className={quotaData ? '' : 'is-off'} />
            ASR 日配额{' '}
            <b>{quotaData ? `${Number(quotaData.daily_audio_hours_limit || 0).toFixed(1)}h` : (quota.status === 'error' ? '未读取' : '…')}</b>
            {quotaData && <> · 单集 ≤ <b>{Number(quotaData.max_audio_hours_per_file || 0).toFixed(1)}h</b></>}
          </button>
          <button
            type="button"
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs ml-auto"
            onClick={saveThreshold}
            disabled={saving || !thresholdDirty}
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}保存
          </button>
        </section>

        <PodcastTasksTable
          state={tasks}
          filters={taskFilters}
          onFilters={patchTaskFilters}
          thresholds={thresholds}
          onOpen={setDrawerId}
          onRetry={handleRetry}
          onForceFull={handleForceFull}
          onForceTts={handleForceTts}
          running={running}
          onRetryLoad={() => loadTasks(taskQuery)}
        />

        <PodcastAudioTable
          state={audio}
          stats={statsData}
          filters={audioFilters}
          onFilters={patchAudioFilters}
          onOpen={setDrawerId}
          onPublish={handlePublish}
          onWithdraw={handleWithdraw}
          onDelete={handleDelete}
          onReconcile={handleReconcile}
          busyId={busyId}
          gcBusy={gcBusy}
          onRetryLoad={() => loadAudio(audioQuery)}
        />
      </div>

      <PodcastEpisodeDrawer
        episodeId={drawerId}
        onClose={() => setDrawerId(null)}
        refreshTick={drawerTick}
        running={running}
        onRetry={handleRetry}
        onForceFull={handleForceFull}
        onForceTts={handleForceTts}
      />
    </>
  );
}
