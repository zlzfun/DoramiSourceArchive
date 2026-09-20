import { useEffect, useState } from 'react';
import {
  podcastAnalysisBasis,
  podcastAssessmentMeta,
  qualityScoreText,
  scoreTierClass,
  SCORE_DISCLAIMER,
} from '../utils/analysis';
import { motionReduced } from '../motion';
import { requestArticleOndemand } from '../api';

const LISTEN_ACTIVE = new Set(['queued', 'narrating', 'synthesizing']);
const LISTEN_POLL_MS = 4000;

/**
 * 哆啦美速读卡(issue #13 五轮):AI 渐变 wash 底 + 衬线渐变大数字是卡的身份。
 *
 *   ┌────────┬──────────────────────────────────────┐
 *   │        │  AI 速读                              │
 *   │  8.2   │  一段客观摘要……                        │
 *   │        │                                       │
 *   └────────┴──────────────────────────────────────┘
 *
 * 左栏一个衬线渐变大数字(新闻价值分),入场以「里程表」方式滚到分值:每一位数字住在
 * 固定宽度的格子里,格子内 0–9 竖条用 transform 滑到目标——无逐帧重渲染、无布局变化。
 * 渐变落在每个数字/小数点元素自身:挂在外层时 background-clip:text 画不进
 * overflow:hidden 格子里的子元素,实测数字整个消失只剩小数点。
 * 右栏两层内容叠在同一格,格式完全同构(小标 + 正文):「AI 速读」摘要层与「评分依据」
 * 层(一句理由 + 免责小字)。点数字在两层间慢速淡切,卡高由较高者撑住不跳;
 * 再点/Esc/换篇回摘要。无分数时右栏承接骨架与生成入口。桌面与移动壳共用。
 *
 * 文章点播(issue #124):非播客时，可点播/生成中/失败重试作为卡内轻量文字动作；
 * 音频就绪后的播放条由 ArticleListenBar 承接，不挤进本卡。
 */
export default function AiReadingCard({
  article,
  summary,
  summarizing,
  canGenerate,
  onGenerate,
  podcast = false,
  aiEnabled = false,
  showToast,
  onArticleRefresh,
}) {
  // 新分析记录是依据的事实源；旧数据仍由 podcast projection 回退 show_notes。
  const assessment = podcast ? podcastAssessmentMeta(article) : null;
  const analysisBasis = podcast ? podcastAnalysisBasis(article) : '';
  const transcriptBacked = analysisBasis === 'publisher_transcript' || analysisBasis === 'asr_transcript';
  const podcastSummaryTitle = transcriptBacked ? '全文导读' : '简介导读';
  const podcastReasonTitle = assessment?.label || (transcriptBacked ? '全文深度分析' : '简介初评');
  const podcastBasisNote = assessment?.note || (transcriptBacked
    ? 'AI 基于完整逐字稿分析，关键结论可回到原节目时间码核验'
    : 'AI 基于节目简介的初步评估，尚未分析完整音频，仅用于辅助筛选');
  const score = article?.quality_score != null ? qualityScoreText(article.quality_score) : '';
  const scoreTier = scoreTierClass(article?.quality_score);   // issue #54:档位挂在每位数字自身(渐变落在元素自身)
  const reason = (article?.score_reason || '').trim();
  const [showReason, setShowReason] = useState(false);
  const [listenBusy, setListenBusy] = useState(false);

  const guide = !podcast && article?.listen_guide && typeof article.listen_guide === 'object'
    ? article.listen_guide
    : null;
  const listenStatus = String(guide?.status || '').trim().toLowerCase();
  const listenReady = Boolean(guide?.audio_ready && guide?.audio_url);
  const listenActive = !listenReady && LISTEN_ACTIVE.has(listenStatus);
  const listenFailed = !listenReady && listenStatus === 'failed';
  const canRequestListen = Boolean(
    !podcast && aiEnabled && article?.id && !listenReady && !listenActive && !listenBusy,
  );
  const showListenAction = Boolean(
    !podcast && (canRequestListen || listenActive || listenBusy || (listenFailed && aiEnabled)),
  );

  useEffect(() => { setShowReason(false); }, [article?.id]);
  useEffect(() => { setListenBusy(false); }, [article?.id]);
  useEffect(() => {
    if (!showReason) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setShowReason(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showReason]);
  useEffect(() => {
    if (!listenActive || !onArticleRefresh) return undefined;
    const timer = window.setInterval(() => {
      onArticleRefresh().catch(() => {});
    }, LISTEN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [listenActive, article?.id, onArticleRefresh]);

  const handleListenOndemand = async () => {
    if (!canRequestListen && !(listenFailed && aiEnabled && !listenBusy)) return;
    setListenBusy(true);
    try {
      const result = await requestArticleOndemand(article.id);
      const outcome = String(result?.outcome || '');
      if (outcome === 'ready') {
        showToast?.('导读音频已就绪', 'success');
      } else if (outcome === 'in_progress') {
        showToast?.('导读音频正在生成中', 'info');
      } else {
        showToast?.('已开始生成导读音频', 'success');
      }
      await onArticleRefresh?.();
    } catch (error) {
      showToast?.(error?.message || '点播失败，请稍后重试', 'error');
    } finally {
      setListenBusy(false);
    }
  };

  const canFlip = Boolean(score && summary && reason);
  // 同步来的权威初评理论上包含摘要，但即使旧/异常数据只有分数理由，也不能把理由藏掉。
  const primaryText = summary || (podcast ? reason : '');
  const primaryTitle = summary
    ? (podcast ? podcastSummaryTitle : 'AI 速读')
    : (podcast ? podcastReasonTitle : 'AI 速读');
  return (
    <div className={`reader-ai-summary ${showReason ? 'is-reason' : ''}`}>
      {score && (
        <div className="reader-ai-summary-side">
          <ScoreFigure
            key={article.id}
            score={score}
            tierClass={scoreTier}
            interactive={canFlip}
            pressed={showReason}
            onToggle={() => setShowReason((v) => !v)}
          />
        </div>
      )}
      <div className="reader-ai-summary-main">
        {primaryText ? (
          <div className="reader-ai-layer reader-ai-layer-summary" aria-hidden={showReason}>
            <span className="reader-ai-layer-title">{primaryTitle}</span>
            <p className="reader-ai-layer-text">{primaryText}</p>
            {podcast && (
              <span className="reader-ai-layer-note">
                {summary ? `${podcastReasonTitle} · ${podcastBasisNote}` : podcastBasisNote}
              </span>
            )}
          </div>
        ) : summarizing ? (
          <div className="reader-ai-summary-skel" role="status" aria-label="正在生成速读">
            <span className="skeleton" /><span className="skeleton" /><span className="skeleton" />
          </div>
        ) : canGenerate ? (
          <button type="button" onClick={onGenerate} className="reader-ai-summary-generate">
            {podcast ? '生成节目简介导读' : '生成本文要点速读'}
          </button>
        ) : null}
        {canFlip && (
          <div className="reader-ai-layer reader-ai-layer-reason" aria-hidden={!showReason}>
            <span className="reader-ai-layer-title">{podcast ? podcastReasonTitle : '评分依据'}</span>
            {reason && <p className="reader-ai-layer-text">{reason}</p>}
            <span className="reader-ai-layer-note">
              {podcast
                ? podcastBasisNote
                : SCORE_DISCLAIMER}
            </span>
          </div>
        )}
        {showListenAction && (
          <div className="reader-ai-listen-action">
            {listenActive || listenBusy ? (
              <span className="reader-ai-listen-status" role="status">
                {listenBusy ? '提交中…' : '导读音频生成中…'}
              </span>
            ) : listenFailed ? (
              <>
                <span className="reader-ai-listen-status" role="status">
                  {guide?.error || '上次点播失败'}
                </span>
                <button
                  type="button"
                  className="reader-ai-summary-generate"
                  onClick={handleListenOndemand}
                  disabled={listenBusy}
                >
                  重新点播
                </button>
              </>
            ) : (
              <button
                type="button"
                className="reader-ai-summary-generate"
                onClick={handleListenOndemand}
              >
                点播听导读
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];


/** 衬线渐变大数字:里程表式入场(每位数字在定宽格内滑到位),点按在摘要/评分依据间切换。 */
function ScoreFigure({ score, tierClass = '', interactive, pressed, onToggle }) {
  const reduced = motionReduced();
  // 先以 0 挂载,下一帧再落到目标值,让 CSS transition 接管滑动;减少动画时直落。
  const [armed, setArmed] = useState(reduced);
  useEffect(() => {
    if (reduced) return undefined;
    let id = requestAnimationFrame(() => { id = requestAnimationFrame(() => setArmed(true)); });
    return () => cancelAnimationFrame(id);
  }, [reduced]);

  const chars = String(score);
  return (
    <button
      type="button"
      className={`reader-ai-score-btn ${interactive ? 'is-interactive' : ''}`}
      aria-pressed={pressed}
      disabled={!interactive}
      onClick={() => { if (interactive) onToggle?.(); }}
      title={interactive ? (pressed ? '返回摘要' : '查看评分依据') : undefined}
    >
      <span className="reader-ai-odo" aria-hidden="true">
        {chars.split('').map((ch, i) => {
          if (ch === '.') return <span key={i} className={`reader-ai-odo-dot ai-grad-text ${tierClass}`}>.</span>;
          const digit = Number(ch);
          if (!Number.isFinite(digit)) return <span key={i}>{ch}</span>;
          return (
            <span key={i} className="reader-ai-odo-cell">
              <span
                className="reader-ai-odo-strip"
                style={{ transform: `translateY(${-(armed ? digit : 0) * 10}%)` }}
              >
                {DIGITS.map((d) => <span key={d} className={`reader-ai-odo-digit ai-grad-text ${tierClass}`}>{d}</span>)}
              </span>
            </span>
          );
        })}
      </span>
      <span className="sr-only">{score}</span>
    </button>
  );
}
