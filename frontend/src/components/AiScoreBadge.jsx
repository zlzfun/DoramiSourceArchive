import { useEffect, useRef, useState } from 'react';
import { qualityScoreText, SCORE_DISCLAIMER } from '../utils/analysis';

/**
 * 内容价值分徽记(issue #13 二轮):住在「哆啦美速读」卡头部右缘,常态只见分数。
 * 「内容价值分」字样、评分理由、免责说明全部收进悬浮说明——桌面 hover/聚焦即现,
 * 触屏点一下切换;换篇由调用方 key={article.id} 重置。
 */
export default function AiScoreBadge({ article }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const score = article?.quality_score != null ? qualityScoreText(article.quality_score) : '';
  const reason = (article?.score_reason || '').trim();

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

  if (!score) return null;
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
        {reason && <span className="reader-ai-score-pop-reason">{reason}</span>}
        <span className="reader-ai-score-pop-note">{SCORE_DISCLAIMER}</span>
      </span>
    </span>
  );
}
