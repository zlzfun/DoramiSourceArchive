import { useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { qualityScoreText, SCORE_DISCLAIMER } from '../utils/analysis';

/**
 * 哆啦美速读卡(issue #13 三轮,从零设计):AI 渐变 wash 底是唯一身份标记,不再写字样。
 *
 *   ┌───────┬──────────────────────────────────────┐
 *   │  ✦    │  一段客观摘要……                        │
 *   │ 8.2   │  ……让读者在打开正文前判断讲了什么。       │
 *   └───────┴──────────────────────────────────────┘
 *
 * 左栏是「统计位」:顶上一枚小星(AI 标记),下面一个衬线大数字(内容价值分,与阅读窗
 * 大标题同字系);右栏是摘要正文。数字常态无任何说明文字,悬停/聚焦/点按弹出
 * 「内容价值分 · 一句理由 · 免责」三行浮层。无分数时左栏只余小星,右栏承接
 * 生成态(三行骨架)与生成入口。桌面与移动壳共用。
 */
export default function AiReadingCard({ article, summary, summarizing, canGenerate, onGenerate }) {
  const score = article?.quality_score != null ? qualityScoreText(article.quality_score) : '';
  return (
    <div className="reader-ai-summary">
      <div className="reader-ai-summary-side">
        <Sparkles className="reader-ai-summary-mark" aria-hidden="true" />
        {score && <ScoreFigure key={article.id} score={score} reason={article.score_reason} />}
      </div>
      <div className="reader-ai-summary-main">
        {summary ? (
          <p className="reader-ai-summary-text">{summary}</p>
        ) : summarizing ? (
          <div className="reader-ai-summary-skel" role="status" aria-label="正在生成速读">
            <span className="skeleton" /><span className="skeleton" /><span className="skeleton" />
          </div>
        ) : canGenerate ? (
          <button type="button" onClick={onGenerate} className="reader-ai-summary-generate">
            生成本文要点速读
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ScoreFigure({ score, reason }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const text = (reason || '').trim();

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span ref={rootRef} className={`reader-ai-score ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="reader-ai-score-btn"
        aria-label={`内容价值分 ${score}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {score}
      </button>
      <span className="reader-ai-score-pop" role="tooltip">
        <span className="reader-ai-score-pop-kicker">内容价值分 {score}</span>
        {text && <span className="reader-ai-score-pop-reason">{text}</span>}
        <span className="reader-ai-score-pop-note">{SCORE_DISCLAIMER}</span>
      </span>
    </span>
  );
}
