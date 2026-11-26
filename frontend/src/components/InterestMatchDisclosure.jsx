import { useState } from 'react';
import { X } from 'lucide-react';
import Modal from './Modal';
import Sheet from './layer/Sheet';
import { interestDrilldownAvailable, interestEvidenceLabel, interestMatchLabel } from '../utils/interestMatches';
import './interestMatchDisclosure.css';

const stop = (event) => event.stopPropagation();
const STATUS = { updated: '已更新', paused: '已停用', deleted: '已移除', unavailable: '暂不可用' };

// Render beside the article's open button, never inside it. Both overlays portal
// outside the card; stop touch/context-menu bubbling into its long-press action.
export default function InterestMatchDisclosure({
  matches = [], selectionLabel = '', selectionReason = '', mobile = false,
  capabilities = null, onOpenInterest = null, expandedNames = false, triggerLabel = '', triggerClassName = 'interest-match-trigger',
}) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  if (!matches.length) return null;
  const named = expandedNames ? matches.filter((match) => match.ref.kind === 'custom') : [];
  const compact = expandedNames ? matches.filter((match) => match.ref.kind !== 'custom') : matches;
  const content = (
    <div className="interest-match-body">
      {selectionLabel && (
        <div className="interest-match-selection">
          <p className="micro-label">{selectionLabel}</p>
          {selectionReason && selectionReason !== selectionLabel && <p>{selectionReason}</p>}
        </div>
      )}
      <ul className="interest-match-list">
        {matches.map((match) => {
          const available = interestDrilldownAvailable(match, capabilities, onOpenInterest);
          return (
            <li key={match.key}>
              <div className="interest-match-heading">
                <h3 className="card-title">{match.name}</h3>
                {match.selected && <span className="tiny-meta">入选兴趣</span>}
                {!match.selected && match.contribution?.admission === 'interest_union'
                  && <span className="tiny-meta">候选依据</span>}
                {STATUS[match.drilldown.reason] && <span className="tiny-meta">{STATUS[match.drilldown.reason]}</span>}
              </div>
              <p className="tiny-meta">{interestEvidenceLabel(match)}</p>
              {match.evidence.reason && <p>{match.evidence.reason}</p>}
              {match.evidence.quotes.map((quote, index) => (
                <blockquote key={index}>
                  <p>{quote.text}</p>
                  <span className="tiny-meta">
                    {quote.availability === 'historical' ? '当时依据 · ' : ''}
                    {quote.sourceKind === 'podcast_transcript' ? '节目原文' : quote.sourceKind === 'title' ? '标题' : '正文'}
                  </span>
                </blockquote>
              ))}
              <button type="button" className="action-button action-button-quiet interest-match-drilldown"
                disabled={!available}
                aria-label={`查看同兴趣：${match.name}${available ? '' : '（暂不可用）'}`}
                onClick={() => {
                  if (!available) return;
                  close();
                  onOpenInterest(match.ref);
                }}>
                查看同兴趣{!available && ' · 暂不可用'}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
  return (
    <>
      {named.map((match) => (
        <button key={match.key} type="button" className="interest-match-trigger" aria-haspopup="dialog" aria-expanded={open}
          aria-label={`查看自定义兴趣「${match.name}」的匹配依据`}
          onTouchStart={stop} onContextMenu={stop}
          onClick={(event) => { event.stopPropagation(); setOpen(true); }}>
          自定义兴趣 · {match.name} · {interestEvidenceLabel(match)}
        </button>
      ))}
      {compact.length > 0 && <button type="button" className={triggerClassName} aria-haspopup="dialog" aria-expanded={open}
        aria-label={triggerLabel ? `${triggerLabel}，查看匹配原因` : `查看 ${matches.length} 个兴趣的匹配依据`}
        onTouchStart={stop} onContextMenu={stop}
        onClick={(event) => { event.stopPropagation(); setOpen(true); }}>
        {triggerLabel || interestMatchLabel(compact)}
      </button>}
      {mobile ? (
        <Sheet open={open} onClose={close} label="兴趣依据" grabLabel="关闭兴趣依据" className="interest-match-sheet"
          panelProps={{ onTouchStart: stop, onContextMenu: stop }}
          head={<h2 className="section-title">兴趣依据</h2>}>
          {content}
        </Sheet>
      ) : (
        <Modal open={open} onClose={close} closeOnOverlay portal size="lg" ariaLabel="兴趣依据" panelClassName="interest-match-modal"
          panelProps={{ onTouchStart: stop, onContextMenu: stop }}>
          <div className="interest-match-heading">
            <h2 className="card-title">兴趣依据</h2>
            <button type="button" className="icon-button" aria-label="关闭兴趣依据" onClick={close}><X aria-hidden="true" /></button>
          </div>
          {content}
        </Modal>
      )}
    </>
  );
}
