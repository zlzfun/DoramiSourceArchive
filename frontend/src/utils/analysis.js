export function cmsTagLabel(tag) {
  return tag?.name_zh || tag?.name_en || tag?.label || tag?.code || '';
}

export function displayAnalysisTags(article) {
  const tags = Array.isArray(article?.display_tags)
    ? article.display_tags
    : (Array.isArray(article?.tags) ? article.tags : []);
  // Older snapshots can interleave the two kinds. Keep their within-kind order,
  // but give canonical tags priority before applying the shared display limit.
  return [
    ...tags.filter((tag) => tag?.type !== 'extracted'),
    ...tags.filter((tag) => tag?.type === 'extracted'),
  ].slice(0, 6);
}

/** Temporary retrieval only: canonical identity is independent of interests. */
export function analysisTagSearch(tag) {
  const label = cmsTagLabel(tag).trim();
  if (!label) return null;
  if (tag?.type === 'extracted') return { label, filters: { display_tag: label } };
  const id = Number(tag?.id);
  if (!Number.isSafeInteger(id) || id <= 0) return null;
  return { label, filters: { display_tag_id: String(id) } };
}

export function displayTagProps(tag) {
  const extracted = tag?.type === 'extracted';
  // 不挂 title:原生 tooltip 是一块黑框,「规范标签」说不出任何读者关心的事,
  // 长句更把内部术语漏到读者面(issue #23 验收:读者面不得出现「个性化日报」)。
  // 小签可点检索的语义由 AnalysisTagChip 的 aria-label + 悬停提墨承担。
  return {
    className: `reader-tag-chip${extracted ? ' is-extracted' : ''}`,
  };
}

// 条目分类签:主标签优先,没有主标签时用事件类型(issue #178)。体裁的中文名由后端词表下发
// (content_genre_label),这里不再维护一份;教程 / 观点等由内容类型签表达,不在这里重复。
export function primaryAnalysisLabel(article) {
  return cmsTagLabel(article?.primary_tag) || article?.event_label || '';
}

export function qualityScoreText(value) {
  // issue #12:Number(null) === 0 会把「未分析」画成 0 分;分数只可能是 null 或 [1,10]。
  if (value == null || (typeof value === 'string' && value.trim() === '')) return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return '';
  return number.toFixed(number % 1 ? 1 : 0);
}

// 分数分档着色(issue #54,样页 docs/design/dorami-score-tiers-quiet.html):
// 灰线 6.0 取自评分尺子自身的档界(提示词:1.0–5.9 泛泛/宣传/重复,6.0 起「有用或深入」)——固定视觉刻度,
// 随尺子版本走、不随运营旋钮 daily_brief_min_score 联动(台账 7+/8+/9+ 同口径);灰线以下与旁边元信息同灰、不画身份色,
// 其上按整数分一档(6.x/7.x/8.x/9+ → 1..4),越高渐变越鲜明;9+ 是完整渐变。档位只改颜色,
// 字号/字重/字体/位置一律不动;无分数返回空串(缺分是「缺席」,不是 0 分——与 qualityScoreText 同口径)。
export const SCORE_GRAY_LINE = 6;
export function scoreTierClass(value) {
  if (!qualityScoreText(value)) return '';
  const number = Number(value);
  if (number < SCORE_GRAY_LINE) return 'is-score-below';
  return `is-score-${Math.min(4, Math.floor(number - SCORE_GRAY_LINE) + 1)}`;
}

const TRANSCRIPT_ANALYSIS_BASES = new Set(['publisher_transcript', 'asr_transcript']);
const PODCAST_PROCESSING_ACTIVE = new Set([
  'not_started',
  'queued',
  'running',
  'retry_wait',
  'reconciliation_required',
]);

const PODCAST_TRANSCRIPT_SOURCE_LABELS = {
  publisher_transcript: '发布方完整逐字稿',
  asr_transcript: 'ASR 逐字稿',
};

function podcastProjection(article) {
  return article?.podcast && typeof article.podcast === 'object' ? article.podcast : {};
}

/**
 * 全文依据优先于简介依据。正常响应的两处 basis 相同；这里的优先级让滚动列表、
 * 详情缓存交错刷新时也不会短暂把已经完成的全文分重新标成简介初评。
 */
export function podcastAnalysisBasis(article) {
  const podcast = podcastProjection(article);
  const candidates = [article?.analysis_basis, podcast.analysis_basis]
    .map((value) => String(value || '').trim());
  return candidates.find((value) => TRANSCRIPT_ANALYSIS_BASES.has(value))
    || candidates.find(Boolean)
    || '';
}

export function podcastTranscriptSourceLabel(value) {
  return PODCAST_TRANSCRIPT_SOURCE_LABELS[String(value || '').trim()] || '';
}

function podcastTranscriptBasisText(value) {
  const label = podcastTranscriptSourceLabel(value);
  return label.startsWith('ASR') ? `基于 ${label}` : `基于${label}`;
}

/** 播客唯一分数的输入依据标签；不返回任何第二套分值。 */
export function podcastAssessmentMeta(article) {
  if (article?.content_type !== 'podcast_episode') return null;
  if (!qualityScoreText(article?.quality_score) && !String(article?.score_reason || '').trim()) {
    return null;
  }
  const basis = podcastAnalysisBasis(article);
  if (podcastProjection(article).full_analysis_ready === true
      || TRANSCRIPT_ANALYSIS_BASES.has(basis)) {
    return {
      label: '全文深度分析',
      note: 'AI 已分析整期内容，关键结论可回到原节目核验',
    };
  }
  return {
    label: '简介初评',
    note: 'AI 基于节目简介的初步评估，尚未分析完整音频，仅用于辅助筛选',
  };
}

/**
 * Issue #44 的读者态投影。后端只给事实字段，文案与视觉 tone 由前端统一解释。
 * 返回 null 表示仍是普通「简介初评」或旧版精品导读状态，调用方应保留原显示。
 */
export function podcastFullProcessingMeta(article) {
  if (article?.content_type !== 'podcast_episode') return null;
  const podcast = podcastProjection(article);
  const status = String(podcast.processing_status || podcast.status || '').trim().toLowerCase();
  const stage = String(podcast.stage || podcast.processing_stage || '').trim().toLowerCase();
  const basis = podcastAnalysisBasis(article);
  const transcriptSource = String(
    podcast.transcript_source || (TRANSCRIPT_ANALYSIS_BASES.has(basis) ? basis : ''),
  ).trim();
  const sourceLabel = podcastTranscriptSourceLabel(transcriptSource);
  const candidate = podcast.full_analysis_candidate === true;
  const finalPremium = typeof podcast.final_premium === 'boolean'
    ? podcast.final_premium
    : null;
  const error = String(podcast.error || podcast.processing_error || '').trim();
  const retryable = podcast.retryable === true || podcast.processing_retryable === true;
  const hasFullProjection = candidate
    || Boolean(stage)
    || Boolean(sourceLabel)
    || TRANSCRIPT_ANALYSIS_BASES.has(basis)
    || finalPremium !== null
    || Boolean(podcast.id || podcast.processing_id)
    || Boolean(error);

  if (!hasFullProjection) return null;

  const result = (label, tone, detail = '') => ({
    label,
    tone,
    detail,
    sourceLabel,
    retryable,
  });
  const sourceDetail = sourceLabel
    ? `${podcastTranscriptBasisText(transcriptSource)}，当前分已替换简介初评`
    : '全文评分已替换简介初评';

  if (status === 'ready' || (TRANSCRIPT_ANALYSIS_BASES.has(basis) && finalPremium !== null)) {
    if (finalPremium === false) {
      return result(
        '全文评分未达优质门槛',
        'warn',
        sourceLabel ? `已${podcastTranscriptBasisText(transcriptSource)}完成全文评分` : '已完成全文评分',
      );
    }
    return result('全文评分完成', 'ok', sourceDetail);
  }

  const isAsrStage = stage === 'asr' || stage === 'fetch';
  const isAnalyzeStage = stage === 'analyze';

  if (status === 'failed') {
    const retryHint = retryable ? '，可重试' : '';
    const label = isAsrStage ? 'ASR 转录失败' : (isAnalyzeStage ? '全文分析失败' : '全文处理失败');
    const defaultDetail = isAsrStage
      ? `ASR 语音转录未完成${retryHint}，请稍后重试 ASR`
      : (isAnalyzeStage ? `全文分析未完成${retryHint}，请稍后再试` : `全文处理未完成${retryHint}，请稍后再试`);
    return result(
      label,
      'bad',
      error ? `${isAsrStage ? 'ASR 转录失败：' : ''}${error}${retryHint}` : defaultDetail,
    );
  }

  if (status === 'retry_wait') {
    const label = isAsrStage ? 'ASR 等待重试' : (isAnalyzeStage ? '全文分析等待重试' : '全文处理等待重试');
    const defaultDetail = isAsrStage
      ? 'ASR 转录暂未完成，系统将自动重试 ASR'
      : (isAnalyzeStage ? '全文分析暂未完成，系统将自动重试' : '暂未完成，系统将自动重试');
    return result(
      label,
      'warn',
      error ? `${isAsrStage ? 'ASR 转录异常：' : ''}${error}，系统将重试${isAsrStage ? ' ASR' : ''}` : defaultDetail,
    );
  }

  if (status === 'reconciliation_required') {
    const label = isAsrStage ? 'ASR 待对账恢复' : (isAnalyzeStage ? '全文分析待对账恢复' : '全文处理恢复中…');
    const defaultDetail = isAsrStage
      ? '正在确认 ASR 云端转录结果，确认后将重试 ASR'
      : '正在确认上次处理结果，确认后将继续';
    return result(
      label,
      'warn',
      error ? `${isAsrStage ? 'ASR 待对账：' : ''}${error}（确认后重试 ASR）` : defaultDetail,
    );
  }

  if (stage === 'analyze') {
    if (status === 'running') {
      return result(
        '全文分析中…',
        'run',
        sourceLabel ? `正在${podcastTranscriptBasisText(transcriptSource)}分析整期内容` : '正在覆盖整期内容进行分析',
      );
    }
    return result(
      sourceLabel === PODCAST_TRANSCRIPT_SOURCE_LABELS.asr_transcript
        ? '转录完成，等待全文分析'
        : '等待全文分析',
      'idle',
      sourceLabel ? `已采用${sourceLabel}` : '完整逐字稿已就绪',
    );
  }

  if (stage === 'asr' || stage === 'fetch') {
    if (status === 'running') {
      return result(
        stage === 'fetch' ? '正在准备音频…' : 'ASR 转录中…',
        'run',
        stage === 'fetch' ? '正在准备原节目音频' : '正在生成完整逐字稿',
      );
    }
    return result(
      'ASR 排队中…',
      'idle',
      '未发现发布方完整逐字稿，正在等待转录',
    );
  }

  if (sourceLabel) {
    return result('转录完成，等待全文分析', 'idle', `已采用${sourceLabel}`);
  }

  if (candidate && (status === 'not_started' || status === 'queued' || !status)) {
    return result(
      '等待完整逐字稿',
      'idle',
      '正在查找发布方完整逐字稿；没有时将通过 ASR 获取全文',
    );
  }

  if (status === 'not_required') {
    return result(
      '暂未进入全文处理',
      'idle',
      error || '简介初评未达到自动处理线，管理员仍可强制全文处理',
    );
  }

  return result('全文处理状态待确认', 'idle', error);
}

/** 管理台账使用的端到端播客状态：TTS 阶段优先，否则沿用全文处理投影。 */
export function podcastLedgerProcessingMeta(article) {
  if (article?.content_type !== 'podcast_episode') return null;
  const podcast = podcastProjection(article);
  const guide = podcast.premium_guide && typeof podcast.premium_guide === 'object'
    ? podcast.premium_guide
    : {};
  const status = String(guide.status || '').trim().toLowerCase();
  const failedStage = String(guide.failed_stage || '').trim().toLowerCase();
  const error = String(guide.error || '').trim();
  const result = (label, tone, detail = '') => ({ label, tone, detail });

  if (status === 'synthesizing') {
    return result('TTS 合成中…', 'run', '全文分析已完成，正在生成精品导读音频');
  }
  if (status === 'summarizing') {
    return result('精品导读生成中…', 'run', '正在根据完整逐字稿生成精品导读与口播稿');
  }
  if (status === 'failed') {
    const ttsFailed = failedStage === 'synthesizing';
    return result(
      ttsFailed ? 'TTS 合成失败' : '精品导读生成失败',
      'bad',
      `${error || (ttsFailed ? '语音合成未完成' : '精品导读未生成')}；可在播客任务中重试`,
    );
  }
  if (status === 'ready' && (guide.audio_ready || podcast.condensed_audio_url)) {
    return result('TTS 音频已就绪', 'ok', '精品导读音频已发布');
  }
  return podcastFullProcessingMeta(article);
}

/** 已落库分析不依赖本部署是否配置 LLM；aiEnabled 只决定能否现场生成。 */
export function shouldShowAiReadingCard(article, { summary, aiEnabled, body } = {}) {
  return Boolean(
    summary
    || qualityScoreText(article?.quality_score)
    || String(article?.score_reason || '').trim()
    || (aiEnabled && body)
  );
}

export function hasReadableAnalysis(article) {
  if (typeof article?.analysis_has_result === 'boolean') return article.analysis_has_result;
  const machineTag = displayAnalysisTags(article).some((tag) => (
    tag?.type === 'extracted' || tag?.assignment_source === 'llm'
  ));
  return Boolean(
    qualityScoreText(article?.quality_score)
    || article?.content_genre
    || machineTag
  );
}

const PODCAST_GUIDE_ACTIVE = new Set(['queued', 'summarizing', 'synthesizing']);

export function analysisNeedsPolling(article) {
  const status = article?.analysis_status;
  if (status === 'pending' || status === 'running' || (
    (status === 'failed' || status === 'timeout')
    && Boolean(article?.analysis_next_attempt_at)
  )) return true;
  const guideStatus = String(
    article?.podcast?.premium_guide?.status || '',
  ).trim().toLowerCase();
  if (PODCAST_GUIDE_ACTIVE.has(guideStatus) && !article?.podcast?.condensed_audio_url) {
    return true;
  }
  return PODCAST_PROCESSING_ACTIVE.has(
    String(article?.podcast?.processing_status || article?.podcast?.status || '').trim().toLowerCase(),
  );
}

export function preferredAnalysisSummary(cachedSummary, incomingSummary) {
  return cachedSummary ?? incomingSummary ?? null;
}

export function analysisItemsFromResponse(response) {
  if (Array.isArray(response)) return response;
  return Array.isArray(response?.items) ? response.items : [];
}

/**
 * Human-facing analysis state. Terminal errors stay hidden from readers by
 * default; admin surfaces opt in with includeTerminal so internal enum values
 * never leak into UI copy. A pending/running row that still carries a readable
 * result is a forced refresh and therefore says “更新中” rather than pretending
 * the old score disappeared.
 */
export function analysisStatusMeta(article, { podcast = false, includeTerminal = false } = {}) {
  const status = article?.analysis_status;
  const refreshing = hasReadableAnalysis(article);
  if (status === 'pending') {
    return {
      label: refreshing ? (podcast ? '简介更新中…' : '更新中…') : (podcast ? '简介分析中…' : '正在分析…'),
      cls: 'stamp-run',
    };
  }
  if (status === 'running') {
    return {
      label: refreshing ? (podcast ? '简介更新中…' : '更新中…') : (podcast ? '简介分析中…' : '正在分析…'),
      cls: 'stamp-run',
    };
  }
  if (!includeTerminal) return null;
  if (status === 'failed') return { label: '分析失败', cls: 'stamp-bad' };
  if (status === 'timeout') return { label: '分析超时', cls: 'stamp-warn' };
  if (status === 'skipped') return { label: '未执行分析', cls: 'stamp-idle' };
  if (!status) return { label: '尚未分析', cls: 'stamp-idle' };
  return null;
}

// 分数语义 v3.48 起是「新闻价值」(这件事有多重要),不是阅读质量。issue #178 第二波起每条最多两个分:
// 新闻价值 + 内容价值(值不值得花时间读完 / 听完);个人早报与日报仍只用新闻价值。
export const SCORE_DISCLAIMER = 'AI 新闻价值评估，用于辅助筛选，不代表事实保证或你的个人评分';
export const SCORE_DIMENSION_LABELS = { news: '新闻价值', content: '内容价值' };

export function scoreDisclaimer(dimension) {
  const label = SCORE_DIMENSION_LABELS[dimension] || SCORE_DIMENSION_LABELS.news;
  return `AI ${label}评估，用于辅助筛选，不代表事实保证或你的个人评分`;
}

/**
 * 条目可显示的分数维度,主分在前:[{ key, label, value, text, reason }](issue #178 第二波)。
 * 后端按内容类型与例外线算好 primary_dimension;缺失的维度不出现(缺失即缺席)。
 * 没有新字段的旧响应(部署交替期的缓存列表)回落为「quality_score = 新闻价值」。
 */
export function scoreDimensions(article) {
  if (!article) return [];
  // 新协议的判据是投影键「有值或显式 null」;旧响应经轮询合并后这些键会以 undefined 存在,不能当作新协议
  // (codex R1-5)——显式 null 才表示该维度缺席,不回落旧分。
  const projected = ['primary_dimension', 'news_value', 'content_value'].some((key) => article[key] !== undefined);
  const entries = [
    ['news', projected ? article.news_value : article.quality_score,
      projected ? article.news_value_reason : article.score_reason],
    ['content', projected ? article.content_value : null, projected ? article.content_value_reason : ''],
  ];
  const dims = entries
    .filter(([, value]) => qualityScoreText(value))
    .map(([key, value, reason]) => ({
      key,
      label: SCORE_DIMENSION_LABELS[key],
      value: Number(value),
      text: qualityScoreText(value),
      reason: String(reason || '').trim(),
    }));
  if (dims.length > 1 && article.primary_dimension && dims[0].key !== article.primary_dimension) dims.reverse();
  return dims;
}

/** 列表与卡片的主分:scoreDimensions 的第一项,没有分数时 null。 */
export function primaryScoreOf(article) {
  return scoreDimensions(article)[0] || null;
}
