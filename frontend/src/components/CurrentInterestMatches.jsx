import InterestMatchDisclosure from './InterestMatchDisclosure';
import { normalizeInterestMatches } from '../utils/interestMatches';

export default function CurrentInterestMatches({ article, guest = false, mobile = false, capabilities, onOpenInterest }) {
  const matches = guest ? [] : normalizeInterestMatches(article, { basis: 'current' });
  if (!matches.length) return null;
  return <div className="reader-current-interests"><InterestMatchDisclosure matches={matches} mobile={mobile}
    capabilities={capabilities} onOpenInterest={onOpenInterest ? (ref) => onOpenInterest(ref, article) : null} /></div>;
}
