import { cmsTagLabel, displayTagProps } from '../utils/analysis';

export default function AnalysisTagChip({ tag, onTemporarySearch }) {
  const label = cmsTagLabel(tag);
  const extracted = tag?.type === 'extracted';
  const props = displayTagProps(tag);

  if (extracted && onTemporarySearch) {
    return (
      <button
        type="button"
        {...props}
        className={`${props.className} is-actionable`}
        aria-label={`临时检索标签：${label}`}
        onClick={(event) => {
          event.stopPropagation();
          onTemporarySearch(label);
        }}
      >
        {label}
      </button>
    );
  }
  return <span {...props}>{label}</span>;
}
