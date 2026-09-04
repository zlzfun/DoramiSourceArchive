import { useEffect, useRef, useState } from 'react';
import { qualityScoreText, SCORE_DISCLAIMER } from '../utils/analysis';

/**
 * 哆啦美速读卡(issue #13 四轮):AI 渐变 wash 底是卡的身份,渐变大数字是卡头。
 *
 *   ┌────────┬──────────────────────────────────────┐
 *   │        │  一段客观摘要……                        │
 *   │  8.2   │  ……让读者在打开正文前判断讲了什么。       │
 *   │        │                                       │
 *   └────────┴──────────────────────────────────────┘
 *
 * 左栏只有一个衬线渐变大数字(内容价值分),入场从 0 滚到分值;右栏两层内容叠在同一格:
 * 摘要层与「评分依据」层(内容价值分 · 一句理由 · 免责)。点数字在两层间慢速淡切,
 * 卡高由较高的一层撑住不跳;再点/Esc/换篇回到摘要。无分数时右栏承接骨架与生成入口。
 * 桌面与移动壳共用。
 */
export default function AiReadingCard({ article, summary, summarizing, canGenerate, onGenerate }) {
  const score = article?.quality_score != null ? qualityScoreText(article.quality_score) : '';
  const reason = (article?.score_reason || '').trim();
  const [showReason, setShowReason] = useState(false);

  useEffect(() => { setShowReason(false); }, [article?.id]);
  useEffect(() => {
    if (!showReason) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setShowReason(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showReason]);

  const canFlip = Boolean(score && summary);
  return (
    <div className={`reader-ai-summary ${showReason ? 'is-reason' : ''}`}>
      {score && (
        <div className="reader-ai-summary-side">
          <ScoreFigure
            key={article.id}
            score={score}
            interactive={canFlip}
            pressed={showReason}
            onToggle={() => setShowReason((v) => !v)}
          />
        </div>
      )}
      <div className="reader-ai-summary-main">
        {summary ? (
          <div className="reader-ai-layer reader-ai-layer-summary" aria-hidden={showReason}>
            <p className="reader-ai-summary-text">{summary}</p>
          </div>
        ) : summarizing ? (
          <div className="reader-ai-summary-skel" role="status" aria-label="正在生成速读">
            <span className="skeleton" /><span className="skeleton" /><span className="skeleton" />
          </div>
        ) : canGenerate ? (
          <button type="button" onClick={onGenerate} className="reader-ai-summary-generate">
            生成本文要点速读
          </button>
        ) : null}
        {canFlip && (
          <div className="reader-ai-layer reader-ai-layer-reason" aria-hidden={!showReason}>
            <span className="reader-ai-reason-kicker">内容价值分 {score}</span>
            {reason && <p className="reader-ai-reason-text">{reason}</p>}
            <span className="reader-ai-reason-note">{SCORE_DISCLAIMER}</span>
          </div>
        )}
      </div>
    </div>
  );
}

const COUNT_UP_MS = 1100;
const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

/** 衬线渐变大数字:入场从 0 慢滚到分值(尊重减少动画),点按在摘要/评分依据间切换。 */
function ScoreFigure({ score, interactive, pressed, onToggle }) {
  const target = Number(score);
  const decimals = score.includes('.') ? 1 : 0;
  const reduced = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const [shown, setShown] = useState(reduced ? target : 0);
  const rafRef = useRef(0);

  useEffect(() => {
    if (reduced) { setShown(target); return undefined; }
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / COUNT_UP_MS);
      setShown(target * easeOutExpo(t));
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, reduced]);

  const text = shown.toFixed(decimals);
  return (
    <button
      type="button"
      className={`reader-ai-score-btn ${interactive ? 'is-interactive' : ''}`}
      aria-label={`内容价值分 ${score}${interactive ? '，点按查看评分依据' : ''}`}
      aria-pressed={interactive ? pressed : undefined}
      disabled={!interactive}
      onClick={interactive ? onToggle : undefined}
    >
      <span className="reader-ai-score-num ai-grad-text">{text}</span>
    </button>
  );
}
