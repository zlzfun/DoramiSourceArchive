import InterestMatchDisclosure from './InterestMatchDisclosure';
import { normalizeInterestMatches } from '../utils/interestMatches';

export default function CurrentInterestMatches({ article, guest = false, mobile = false, capabilities, onOpenInterest, compact = false }) {
  const matches = guest ? [] : normalizeInterestMatches(article, { basis: 'current' });
  if (!matches.length) return null;
  if (compact) return <span className="reader-interest-indicator text-xs">有你感兴趣的内容</span>;
  return <>{matches.map((match) => (
    <InterestMatchDisclosure key={`${article.id}:${match.key}`} matches={[match]} mobile={mobile}
      triggerClassName="reader-tag-chip is-actionable" triggerLabel={match.name} capabilities={capabilities}
      onOpenInterest={onOpenInterest ? (ref) => onOpenInterest(ref, article) : null} />
  ))}</>;
}
