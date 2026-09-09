import {
  labelFrom,
  NOISE_LABELS,
  RELIABILITY_LABELS,
  SIGNAL_LABELS,
} from '../sourceTaxonomy';

// v3.48 之前的 Podcast 目录使用短枚举。新写入已与文章节点统一，展示层继续
// 接住旧库，避免要求管理员重新导入目录才能看到中文审查结果。
const LEGACY_REVIEW_VALUES = {
  signal_strength: {
    high: 'high_signal',
    medium: 'medium_signal',
    low: 'low_signal',
  },
  noise_risk: {
    low: 'low_noise',
    medium: 'medium_noise',
    high: 'high_noise',
  },
  fetch_reliability: {
    high: 'stable_public',
    blocked: 'blocked_or_fragile',
  },
};

function normalizedReviewValue(field, value) {
  return LEGACY_REVIEW_VALUES[field]?.[value] || value || '';
}

export default function SourceReviewSection({ source }) {
  const signal = normalizedReviewValue('signal_strength', source?.signal_strength);
  const noise = normalizedReviewValue('noise_risk', source?.noise_risk);
  const reliability = normalizedReviewValue('fetch_reliability', source?.fetch_reliability);
  const contentTags = (Array.isArray(source?.content_tags) ? source.content_tags : []).slice(0, 6);
  const hasReview = Boolean(signal || noise || reliability || contentTags.length);

  if (!hasReview) return null;

  return (
    <div className="inspector-section">
      <h3 className="micro-label">源审查</h3>
      <div className="inspector-review-grid">
        {signal && <div><span className="tiny-meta">信号</span><div className="inspector-review-val">{labelFrom(SIGNAL_LABELS, signal)}</div></div>}
        {noise && <div><span className="tiny-meta">噪声</span><div className="inspector-review-val">{labelFrom(NOISE_LABELS, noise)}</div></div>}
        {reliability && <div><span className="tiny-meta">稳定性</span><div className="inspector-review-val">{labelFrom(RELIABILITY_LABELS, reliability)}</div></div>}
      </div>
      {contentTags.length > 0 && (
        <div className="inspector-tags">
          {contentTags.map((tag, index) => <span key={`${tag}-${index}`}>{tag}</span>)}
        </div>
      )}
    </div>
  );
}
