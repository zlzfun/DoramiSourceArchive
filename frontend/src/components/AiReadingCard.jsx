import { useEffect, useState } from 'react';
import { qualityScoreText, SCORE_DISCLAIMER } from '../utils/analysis';

/**
 * 哆啦美速读卡(issue #13 五轮):AI 渐变 wash 底 + 衬线渐变大数字是卡的身份。
 *
 *   ┌────────┬──────────────────────────────────────┐
 *   │        │  AI 速读                              │
 *   │  8.2   │  一段客观摘要……                        │
 *   │        │                                       │
 *   └────────┴──────────────────────────────────────┘
 *
 * 左栏一个衬线渐变大数字(内容价值分),入场以「里程表」方式滚到分值:每一位数字住在
 * 固定宽度的格子里,格子内 0–9 竖条用 transform 滑到目标——无逐帧重渲染、无布局变化。
 * 渐变落在每个数字/小数点元素自身:挂在外层时 background-clip:text 画不进
 * overflow:hidden 格子里的子元素,实测数字整个消失只剩小数点。
 * 右栏两层内容叠在同一格,格式完全同构(小标 + 正文):「AI 速读」摘要层与「评分依据」
 * 层(一句理由 + 免责小字)。点数字在两层间慢速淡切,卡高由较高者撑住不跳;
 * 再点/Esc/换篇回摘要。无分数时右栏承接骨架与生成入口。桌面与移动壳共用。
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
            <span className="reader-ai-layer-title">AI 速读</span>
            <p className="reader-ai-layer-text">{summary}</p>
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
            <span className="reader-ai-layer-title">评分依据</span>
            {reason && <p className="reader-ai-layer-text">{reason}</p>}
            <span className="reader-ai-layer-note">{SCORE_DISCLAIMER}</span>
          </div>
        )}
      </div>
    </div>
  );
}

const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/** 衬线渐变大数字:里程表式入场(每位数字在定宽格内滑到位),点按在摘要/评分依据间切换。 */
function ScoreFigure({ score, interactive, pressed, onToggle }) {
  const reduced = prefersReducedMotion();
  // 先以 0 挂载,下一帧再落到目标值,让 CSS transition 接管滑动;减少动画时直落。
  const [armed, setArmed] = useState(reduced);
  useEffect(() => {
    if (reduced) return undefined;
    let id = requestAnimationFrame(() => { id = requestAnimationFrame(() => setArmed(true)); });
    return () => cancelAnimationFrame(id);
  }, [reduced]);

  let digitIndex = 0;
  return (
    <button
      type="button"
      className={`reader-ai-score-btn ${interactive ? 'is-interactive' : ''}`}
      aria-label={`内容价值分 ${score}${interactive ? '，点按查看评分依据' : ''}`}
      aria-pressed={interactive ? pressed : undefined}
      disabled={!interactive}
      onClick={interactive ? onToggle : undefined}
    >
      <span className="reader-ai-odo" aria-hidden="true">
        {score.split('').map((ch, i) => {
          if (ch === '.') return <span key={i} className="reader-ai-odo-dot ai-grad-text">.</span>;
          const target = armed ? Number(ch) : 0;
          const order = digitIndex++;
          return (
            <span key={i} className="reader-ai-odo-cell">
              <span
                className="reader-ai-odo-strip"
                style={{ transform: `translateY(${-target * 1.2}em)`, transitionDelay: `${order * 220}ms` }}
              >
                {DIGITS.map((d) => <span key={d} className="reader-ai-odo-digit ai-grad-text">{d}</span>)}
              </span>
            </span>
          );
        })}
      </span>
    </button>
  );
}
