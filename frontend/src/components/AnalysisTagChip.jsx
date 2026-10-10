import { analysisTagSearch, cmsTagLabel, displayTagProps } from '../utils/analysis';

export default function AnalysisTagChip({ tag, onSearch }) {
  const label = cmsTagLabel(tag);
  const props = displayTagProps(tag);

  if (onSearch && analysisTagSearch(tag)) {
    return (
      <button
        type="button"
        {...props}
        className={`${props.className} is-actionable`}
        aria-label={`检索「${label}」`}
        onClick={(event) => {
          event.stopPropagation();
          onSearch(tag);
        }}
      >
        {label}
      </button>
    );
  }
  return <span {...props}>{label}</span>;
}
