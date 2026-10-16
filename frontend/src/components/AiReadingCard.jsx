import { useEffect, useId, useRef, useState } from 'react';
import { Headphones, Loader2 } from 'lucide-react';
import {
  podcastAnalysisBasis,
  podcastAssessmentMeta,
  scoreDimensions,
  scoreDisclaimer,
  scoreTierClass,
} from '../utils/analysis';
import { motionReduced } from '../motion';
import { requestArticleOndemand } from '../api';

const LISTEN_ACTIVE = new Set(['queued', 'narrating', 'synthesizing']);
const LISTEN_POLL_MS = 4000;

/**
 * 哆啦美速读卡(issue #13 五轮;issue #184 改细线卡):外框色底 + 最弱细线,卡头渐变 ✦ 与分数是卡的身份。
 *
 *   ┌───────────────────────────────────────────────┐
 *   │ ✦ AI 速读                       新闻价值  8.2 │
 *   │ 一段客观摘要……                                 │
 *   └───────────────────────────────────────────────┘
 *
 * 卡头右侧一个宋体渐变数字(主分),入场以「里程表」方式滚到分值:每一位数字住在
 * 固定宽度的格子里,格子内 0–9 竖条用 transform 滑到目标——无逐帧重渲染、无布局变化。
 * 渐变落在每个数字/小数点元素自身:挂在外层时 background-clip:text 画不进
 * overflow:hidden 格子里的子元素,实测数字整个消失只剩小数点。
 *
 * 双评分(issue #178 第二波):数字左侧小字标出当前维度(新闻价值 / 内容价值)。有两个分时点数字
 * 在两个维度间切换,数字沿里程表滑到另一个值,AI 速读不变;换篇回到主分。悬停或键盘聚焦数字浮出
 * 该维度的评分依据(一句理由 + 免责小字),鼠标可以移进浮层停留;Escape 收起且不移动焦点,等悬停与焦点
 * 都离开、或有新的悬停 / 聚焦进来时复位(codex R1-6)。触屏没有悬停,依据改为卡内常显的一行小字。此前「点数字
 * 翻面看评分依据」的交互随之退役。正文区承接速读、骨架与生成入口。桌面与移动壳共用。
 *
 * 文章点播(issue #124):非播客时，可点播/生成中/失败重试用卡内小胶囊（耳机图标 + 文案）；
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
  ondemandEnabled = false,   // 文章点播能力位(issue #137):总闸 ∧ 本部署真能跑
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
  const dims = scoreDimensions(article);
  const [dimKey, setDimKey] = useState(null);   // null = 主分
  const current = dims.find((d) => d.key === dimKey) || dims[0] || null;
  const other = dims.length > 1 ? dims.find((d) => d.key !== current.key) : null;
  const score = current?.text || '';
  const scoreTier = scoreTierClass(current?.value);   // issue #54:档位挂在每位数字自身(渐变落在元素自身)
  const reason = (article?.score_reason || '').trim();
  const tipId = useId();
  const sideRef = useRef(null);
  const [tipDismissed, setTipDismissed] = useState(false);
  const tipTitle = podcast ? podcastReasonTitle : `评分依据 · ${current?.label || ''}`;
  const tipReason = current?.reason || '';
  const tipNote = podcast ? podcastBasisNote : scoreDisclaimer(current?.key);
  const [listenBusy, setListenBusy] = useState(false);

  const guide = !podcast && article?.listen_guide && typeof article.listen_guide === 'object'
    ? article.listen_guide
    : null;
  const listenStatus = String(guide?.status || '').trim().toLowerCase();
  const listenReady = Boolean(guide?.audio_ready && guide?.audio_url);
  const listenActive = !listenReady && LISTEN_ACTIVE.has(listenStatus);
  const listenFailed = !listenReady && listenStatus === 'failed';
  const canRequestListen = Boolean(
    !podcast && aiEnabled && ondemandEnabled
      && article?.id && !listenReady && !listenActive && !listenBusy,
  );
  // 不可点播时不画胶囊;已在生成 / 刚失败的仍给状态与重试,不让读者的请求凭空消失。
  const showListenAction = Boolean(
    !podcast && (
      canRequestListen || listenActive || listenBusy
      || (listenFailed && aiEnabled && ondemandEnabled)
    ),
  );

  useEffect(() => { setDimKey(null); setTipDismissed(false); }, [article?.id]);
  useEffect(() => {
    // Escape 收起评分依据:只在数字区处于悬停或焦点在其中时生效,不移动焦点。
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      const side = sideRef.current;
      if (side && (side.contains(document.activeElement) || side.matches(':hover'))) setTipDismissed(true);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => { setListenBusy(false); }, [article?.id]);
  useEffect(() => {
    if (!listenActive || !onArticleRefresh) return undefined;
    const timer = window.setInterval(() => {
      onArticleRefresh().catch(() => {});
    }, LISTEN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [listenActive, article?.id, onArticleRefresh]);

  const handleListenOndemand = async () => {
    if (!canRequestListen && !(listenFailed && aiEnabled && ondemandEnabled && !listenBusy)) return;
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

  // 同步来的权威初评理论上包含摘要，但即使旧/异常数据只有分数理由，也不能把理由藏掉。
  const primaryText = summary || (podcast ? reason : '');
  const primaryTitle = summary
    ? (podcast ? podcastSummaryTitle : 'AI 速读')
    : (podcast ? podcastReasonTitle : 'AI 速读');
  // 播客无摘要时理由已作正文,不再重复成依据行。
  const inlineReason = tipReason && !(podcast && !summary) ? tipReason : '';
  return (
    <div className="reader-ai-summary">
      {/* 卡头恒在常流里(codex R1-P2-03):左「✦ 标题」,右统计位「维度 分数」——只有分数没有正文时卡也撑得住 */}
      <div className="reader-ai-card-head">
        <span className="reader-ai-layer-title">{primaryTitle}</span>
        {score && (
          <div
            ref={sideRef}
            className={`reader-ai-summary-side ${tipDismissed ? 'is-tip-dismissed' : ''}`}
            onMouseEnter={() => setTipDismissed(false)}
            onFocus={() => setTipDismissed(false)}
            onMouseLeave={(event) => {
              if (!event.currentTarget.contains(document.activeElement)) setTipDismissed(false);
            }}
            onBlur={(event) => {
              const side = event.currentTarget;
              if (!side.contains(event.relatedTarget) && !side.matches(':hover')) setTipDismissed(false);
            }}
          >
            <ScoreFigure
              key={article.id}
              score={score}
              tierClass={scoreTier}
              interactive={Boolean(other)}
              label={other ? `${current.label} ${score}，点按切换到${other.label}` : `${current.label} ${score}`}
              describedBy={tipReason || tipNote ? tipId : undefined}
              onToggle={() => { if (other) setDimKey(other.key); }}
            />
            <span className="reader-ai-score-dim" aria-hidden="true">{current.label}</span>
            {(tipReason || tipNote) && (
              <div className="reader-ai-score-tip" role="tooltip" id={tipId}>
                <span className="reader-ai-layer-title">{tipTitle}</span>
                {tipReason && <p className="reader-ai-layer-text">{tipReason}</p>}
                <span className="reader-ai-layer-note">{tipNote}</span>
              </div>
            )}
          </div>
        )}
      </div>
      <div className="reader-ai-summary-main">
        {primaryText ? (
          <div className="reader-ai-layer reader-ai-layer-summary">
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
        {score && inlineReason && (
          <p className="reader-ai-reason-inline">
            <span className="reader-ai-reason-inline-label">{current.label}依据</span>
            {inlineReason}
          </p>
        )}
        {showListenAction && (
          <div className="reader-ai-listen-action">
            {listenActive || listenBusy ? (
              <span className="reader-ai-listen-pill is-busy" role="status" aria-live="polite">
                <Loader2 className="reader-ai-listen-icon animate-spin" aria-hidden="true" />
                {listenBusy ? '提交中…' : '生成中…'}
              </span>
            ) : listenFailed ? (
              <>
                <span className="reader-ai-listen-status" role="status">
                  {guide?.error || '上次点播失败'}
                </span>
                <button
                  type="button"
                  className="reader-ai-listen-pill"
                  onClick={handleListenOndemand}
                  disabled={listenBusy}
                >
                  <Headphones className="reader-ai-listen-icon" aria-hidden="true" />
                  重新点播
                </button>
              </>
            ) : (
              <button
                type="button"
                className="reader-ai-listen-pill"
                onClick={handleListenOndemand}
              >
                <Headphones className="reader-ai-listen-icon" aria-hidden="true" />
                听导读
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];


/** 衬线渐变大数字:里程表式入场(每位数字在定宽格内滑到位),有两个分时点按切换维度(数字滑到另一个值)。 */
function ScoreFigure({ score, tierClass = '', interactive, label, describedBy, onToggle }) {
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
      aria-label={label}
      aria-describedby={describedBy}
      onClick={() => { if (interactive) onToggle?.(); }}
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
    </button>
  );
}
