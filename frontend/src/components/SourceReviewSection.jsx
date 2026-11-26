import { reviewOf } from '../utils/sourceReview';

export default function SourceReviewSection({ source }) {
  const review = reviewOf(source);
  if (!review.length) return null;

  return (
    <div className="inspector-section">
      <h3 className="tools-title">源审查</h3>
      <div className="inspector-review-grid">
        {review.map((item) => (
          <div key={item.key}>
            <span className="tiny-meta">{item.name}</span>
            <div className="inspector-review-val" title={item.raw || undefined}>{item.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
