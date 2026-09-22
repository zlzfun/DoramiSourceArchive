// 播客处理状态归一层(issue #76,归并稿 P2 #11):面板行 / 台账 / 抽屉 / 命令组装
// 都从这里取「阶段 / 判定 / TTS」的规范化谓词、章文案与 tone、可用动作与成功提示,
// 展示层不再各自拼 stage/status 三元;审计 reason 也由同一谓词组装。
//
// 输入形状两种都可喂:
//   · 管理面任务行(/api/admin/podcast-premium-tasks 的 item):stage_code / verdict /
//     processing_status / processing_stage / tts_status / can_* …
//   · 台账文章的 podcast 投影(articles_view):processing_status|status / stage …
// 阶段以后端 stage_code 为准;缺失时按 processing_status + stage 就地推导(旧响应兼容)。

const ACTIVE_PROCESSING = new Set(['queued', 'running', 'awaiting_review']);
const FAILED_PROCESSING = new Set(['failed', 'retry_wait', 'reconciliation_required']);

export const PODCAST_INITIAL_THRESHOLD = 6.0;

export const PODCAST_STAGE_META = Object.freeze({
  not_processed: { label: '未初评', tone: 'idle' },
  not_selected: { label: '未入选', tone: 'idle' },
  awaiting_transcript: { label: '待全文', tone: 'idle' },
  processing: { label: '处理中', tone: 'run' },
  full_analyzed: { label: '全文完成', tone: 'ok' },
  reconciliation: { label: '待对账', tone: 'warn' },
  failed: { label: '失败', tone: 'bad' },
});

export const PODCAST_STAGE_FILTERS = Object.freeze([
  ['', '阶段'],
  ['not_processed', '未初评'],
  ['not_selected', '未入选'],
  ['awaiting_transcript', '待全文'],
  ['processing', '处理中'],
  ['full_analyzed', '全文完成'],
  ['reconciliation', '待对账'],
  ['failed', '失败'],
]);

export const PODCAST_VERDICT_META = Object.freeze({
  premium: { label: '优质', tone: 'ok' },
  below_threshold: { label: '未达门槛', tone: 'idle' },
  unscored: { label: '未终评', tone: 'idle' },
});

export const PODCAST_VERDICT_FILTERS = Object.freeze([
  ['', '判定'],
  ['premium', '优质'],
  ['below_threshold', '未达门槛'],
  ['unscored', '未终评'],
]);

export const PODCAST_TTS_META = Object.freeze({
  not_started: { label: '未生成', tone: 'idle', active: false },
  queued: { label: '已排队', tone: 'run', active: true },
  summarizing: { label: '生成导读中', tone: 'run', active: true },
  synthesizing: { label: '合成音频中', tone: 'run', active: true },
  ready: { label: '已生成', tone: 'ok', active: false },
  failed: { label: '失败', tone: 'bad', active: false },
});

export const PODCAST_TTS_FILTERS = Object.freeze([
  ['', 'TTS'],
  ['not_started', '未生成'],
  ['active', '生成中'],
  ['ready', '已生成'],
  ['failed', '失败'],
]);

export const PODCAST_BASIS_LABELS = Object.freeze({
  podcast_show_notes: '节目简介',
  publisher_transcript: '发布方逐字稿',
  asr_transcript: 'ASR 逐字稿',
});

export const PODCAST_TIMELINE_TONES = Object.freeze({
  done: 'ok', run: 'run', fail: 'bad', warn: 'warn', pending: 'idle', skipped: 'idle',
});

const lower = (value) => String(value || '').trim().toLowerCase();

export function podcastScoreText(value) {
  if (value == null || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : '—';
}

function processingStatusOf(podcast = {}) {
  return lower(podcast.processing_status || podcast.status);
}

function processingStageOf(podcast = {}) {
  return lower(podcast.processing_stage || podcast.stage);
}

/**
 * 重试 / 恢复动作的种类:reconcile(待对账,走 /reconcile)/ asr / analyze / full(走 /retry)。
 * 空串 = 当前不可重试。命令层(podcastFullAnalysisCommand)与展示层(按钮 title、成功 toast)
 * 共用这一个谓词,不再各写一遍 isAsr/isAnalyze。
 */
export function podcastRetryKind(podcast = {}) {
  const status = processingStatusOf(podcast);
  if (!FAILED_PROCESSING.has(status)) return '';
  if (status === 'reconciliation_required') return 'reconcile';
  const stage = processingStageOf(podcast);
  if (stage === 'asr' || stage === 'fetch') return 'asr';
  if (stage === 'analyze') return 'analyze';
  return 'full';
}

function isAsrStage(podcast = {}) {
  const stage = processingStageOf(podcast);
  return stage === 'asr' || stage === 'fetch';
}

/**
 * 审计 reason(命令 body):由重试种类机械推导,展示层不持有 API 字符串。
 * kind 缺省按 processing_status 推导;命令层走 /retry 路径时显式传 'retry'(not_required 也走该路径,
 * 措辞按阶段而非按状态)。
 */
export function podcastProcessingReason(podcast = {}, kind = podcastRetryKind(podcast)) {
  const asr = isAsrStage(podcast);
  const analyze = processingStageOf(podcast) === 'analyze';
  if (kind === 'reconcile') {
    return asr ? '管理员核对并重试 ASR 转录' : analyze ? '管理员核对并重试全文分析' : '管理员核对并重试全文处理';
  }
  if (kind === 'retry' || kind === 'asr' || kind === 'analyze' || kind === 'full') {
    return asr ? '管理员重试 ASR 转录' : analyze ? '管理员重试全文分析' : '管理员重试全文处理';
  }
  return '管理员强制全文处理';
}

/** 行内动作钮的 title / 抽屉脚按钮文案。 */
export function podcastRetryLabel(podcast = {}) {
  const kind = podcastRetryKind(podcast);
  if (kind === 'reconcile') return isAsrStage(podcast) ? '对账恢复 ASR' : '对账恢复';
  if (kind === 'asr') return '重试 ASR 转录';
  if (kind === 'analyze') return '重试全文分析';
  if (kind === 'full') return '重试全文处理';
  return '';
}

/** 命令成功后的 toast(台账 / 面板 / 抽屉同一句)。 */
export function podcastProcessingSuccessMessage(podcast = {}) {
  const kind = podcastRetryKind(podcast);
  if (kind === 'reconcile') return isAsrStage(podcast) ? '已启动 ASR 对账恢复' : '已启动对账恢复';
  if (kind === 'asr') return '已重试 ASR 转录';
  if (kind === 'analyze') return '已重试全文分析';
  if (kind === 'full') return '已重试全文处理';
  return '已启动全文处理';
}

/** 强制 TTS 的结果 toast:幂等命中(started=false)不是成功,按状态如实说。 */
export function podcastForceTtsResult(result = {}) {
  const status = lower(result.status);
  if (status === 'ready') return { message: '已生成 TTS 音频', tone: 'success' };
  if (result.started === false) {
    const meta = PODCAST_TTS_META[status];
    return { message: `TTS 当前${meta ? meta.label : '处理中'}，未重复启动`, tone: 'info' };
  }
  return { message: '已启动强制 TTS，列表会自动更新进度', tone: 'success' };
}

function deriveStageCode(item = {}) {
  if (item.stage_code && PODCAST_STAGE_META[item.stage_code]) return item.stage_code;
  if (item.final_score != null) return 'full_analyzed';
  const status = processingStatusOf(item);
  if (status === 'reconciliation_required') return 'reconciliation';
  if (FAILED_PROCESSING.has(status)) return 'failed';
  if (ACTIVE_PROCESSING.has(status)) return 'processing';
  if (item.initial_score == null) return 'not_processed';
  return Number(item.initial_score) < PODCAST_INITIAL_THRESHOLD ? 'not_selected' : 'awaiting_transcript';
}

function deriveVerdict(item = {}) {
  if (item.verdict && PODCAST_VERDICT_META[item.verdict]) return item.verdict;
  if (item.final_score == null) return 'unscored';
  return item.is_premium ? 'premium' : 'below_threshold';
}

/**
 * 阶段章下的一行原因(原「未处理 / 未入选原因」列的内容,压成一句短语);后端 reason
 * 全文进 title。thresholds 由面板传入(简介付费 ASR 线固定、优质门槛可调)。
 */
function stageReason(item, stageCode, thresholds) {
  const initialLine = podcastScoreText(thresholds.initial ?? PODCAST_INITIAL_THRESHOLD);
  const premiumLine = podcastScoreText(thresholds.premium);
  const error = String(item.processing_error || '').trim();
  const asr = isAsrStage(item);
  const analyze = processingStageOf(item) === 'analyze';
  switch (stageCode) {
    case 'not_processed':
      return '等待简介初评';
    case 'not_selected':
      return `简介初评 ${podcastScoreText(item.initial_score)} < 付费 ASR 线 ${initialLine}`;
    case 'awaiting_transcript':
      return `已过付费 ASR 线 ${initialLine} · 等待逐字稿`;
    case 'processing': {
      const status = processingStatusOf(item);
      const queued = status === 'queued';
      if (asr) return processingStageOf(item) === 'fetch' ? '准备原节目音频' : (queued ? 'ASR 排队中' : 'ASR 转录中');
      if (analyze) return queued ? '全文分析排队中' : '全文分析中';
      return '全文处理中';
    }
    case 'full_analyzed': {
      const basis = PODCAST_BASIS_LABELS[item.analysis_basis] || item.current_basis || '全文';
      const score = podcastScoreText(item.final_score);
      const sign = item.is_premium ? '≥' : '<';
      const tail = item.historical_generated && !item.is_premium ? ' · 历史成品' : '';
      return `${basis} · ${score} ${sign} 门槛 ${premiumLine}${tail}`;
    }
    case 'reconciliation':
      return asr ? 'ASR 结果待对账 · 对账后重试 ASR' : '结果待对账 · 对账后重试';
    case 'failed': {
      const head = asr ? 'ASR 转录失败' : analyze ? '全文分析失败' : '全文处理失败';
      const retry = processingStatusOf(item) === 'retry_wait' ? '等待自动重试' : '可重试';
      return error ? `${head} · ${error}` : `${head} · ${retry}`;
    }
    default:
      return String(item.reason || '');
  }
}

/**
 * 任务行的完整投影:面板表格、单集抽屉与 KPI 下钻都吃这一份。
 * thresholds: { initial, premium }。
 */
export function podcastTaskMeta(item = {}, thresholds = {}) {
  const stageCode = deriveStageCode(item);
  const stage = { code: stageCode, ...PODCAST_STAGE_META[stageCode] };
  const verdictCode = deriveVerdict(item);
  const verdict = { code: verdictCode, ...PODCAST_VERDICT_META[verdictCode] };
  const ttsStatus = lower(item.tts_status) || (item.audio_ready ? 'ready' : 'not_started');
  const ttsBase = PODCAST_TTS_META[ttsStatus] || PODCAST_TTS_META.not_started;
  const tts = {
    status: ttsStatus,
    label: ttsBase.label,
    tone: ttsBase.tone,
    active: ttsBase.active,
    error: String(item.tts_error || ''),
    forced: Boolean(item.tts_forced),
    shown: verdictCode !== 'unscored' || ttsStatus !== 'not_started',
  };
  const retryKind = podcastRetryKind(item);
  return {
    stage,
    reason: stageReason(item, stageCode, thresholds),
    reasonFull: String(item.reason || ''),
    verdict,
    tts,
    active: stageCode === 'processing' || tts.active,
    scores: {
      initial: item.initial_score,
      final: item.final_score,
      current: item.current_score,
      basis: PODCAST_BASIS_LABELS[item.analysis_basis] || item.current_basis || '',
    },
    actions: {
      retry: item.can_retry ? retryKind : '',
      retryLabel: item.can_retry ? podcastRetryLabel(item) : '',
      force: Boolean(item.can_force),
      forceTts: Boolean(item.can_force_tts),
    },
  };
}

/** 评分格:主数字 + 依据小字(全文分优先;只有简介分时标「简介」)。 */
export function podcastScoreCell(item = {}) {
  if (item.final_score != null) {
    return {
      main: podcastScoreText(item.final_score),
      sub: item.initial_score != null ? `全文 · 简介 ${podcastScoreText(item.initial_score)}` : '全文',
    };
  }
  if (item.initial_score != null) return { main: podcastScoreText(item.initial_score), sub: '简介' };
  return { main: '—', sub: '' };
}
