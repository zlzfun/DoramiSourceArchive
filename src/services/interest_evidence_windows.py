"""Reproducible, bounded evidence pages and grounded semantic verdicts.

Offsets refer to the exact projected Unicode text, not HTML or normalized search
tokens. The caller persists successful pages and owns authorization and retries.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

CONTRACT_VERSION = 'interest-semantic-scan-v2'
PROJECTION_VERSION = 'literal-window-v1'
CHUNK_CHARS = 3500
CHUNK_OVERLAP = 240
MAX_WINDOWS = 6
MAX_TEXT_CHARS = 21000
WIRE_EVIDENCE_FORMAT = 'span-id-v1'


def encode_evidence_payload(payload: object) -> str:
    """Exactly the user-message bytes used for storage, identity and transport."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def semantic_intent_hash(intent: dict) -> str:
    return hashlib.sha256(encode_evidence_payload(intent).encode()).hexdigest()


def _intent_assessment(payload, verdict, spans, group_evidence, clusters, normalized):
    """Separate malformed responses from received but insufficient relation proof."""
    intent = payload['intent']['condition']['semantic_intent']
    raw = verdict.get('intent_assessment')
    if 'intent_assessment' not in verdict:
        raw = {'verdict':'unknown', 'reason':'语义意图尚未获得关系判断',
               'evidence_span_ids':[], 'negative_example_indexes':[]}
        if normalized:
            raw['evidence'] = []
    keys = {'verdict','reason','evidence_span_ids','negative_example_indexes'}
    if not isinstance(raw, dict) or set(raw) != (keys | {'evidence'} if normalized and 'evidence' in raw else keys):
        raise ValueError('invalid intent assessment fields')
    if not isinstance(raw['verdict'],str) or raw['verdict'] not in {'satisfied','not_satisfied','unknown'}:
        raise ValueError('invalid intent assessment verdict')
    if not isinstance(raw['reason'], str) or not raw['reason'].strip():
        raise ValueError('invalid intent assessment reason')
    ids, negatives = raw['evidence_span_ids'], raw['negative_example_indexes']
    if (not isinstance(ids, list) or len(ids)>6
            or any(not isinstance(sid,str) or sid not in spans for sid in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('invalid intent assessment spans')
    if (not isinstance(negatives, list) or len(negatives)>6
            or any(type(i) is not int or not 0 <= i < len(intent['negative_examples']) for i in negatives)
            or len(set(negatives)) != len(negatives)):
        raise ValueError('invalid intent negative indexes')
    combined = set(ids) | {item['span_id'] for item in group_evidence if item['kind']=='text'}
    if len(combined)>6:
        raise ValueError('too many combined intent spans')
    citations = []
    for sid in ids:
        window, part = spans[sid]
        citations.append({'span_id':sid,'window_id':window['id'],'start':part['start'],
                          'end':part['end'],'quote':part['text']})
    if normalized and raw.get('evidence') != citations:
        raise ValueError('normalized intent evidence differs from its server span')
    assessment = {**raw,'reason':raw['reason'][:1000],'evidence':citations}
    outcome, reason = verdict['outcome'], verdict['reason']
    if raw['verdict']=='unknown':
        outcome, reason = 'unknown', assessment['reason']
    elif not ids:
        outcome, reason = 'unknown', '语义意图缺少正文关系证据'
    elif raw['verdict']=='satisfied' and negatives:
        outcome, reason = 'unknown', '语义意图判断与排除示例冲突'
    elif raw['verdict']=='not_satisfied':
        outcome, reason = 'no_match', assessment['reason']
    elif outcome=='core' and not any(all(
            start <= item['start'] and item['end'] <= end for item in citations)
            for start,end in clusters):
        outcome, reason = 'unknown', '语义关系证据不在支持全部条件组的同一讨论中'
    return outcome, reason, assessment


def _span_id(window_id, start, end, text):
    identity = encode_evidence_payload([WIRE_EVIDENCE_FORMAT, window_id, start, end, text])
    return 'span-' + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _partition_window(window):
    text, base = window['text'], window['start']
    spans, at = [], 0
    while at < len(text):
        end = min(len(text), at + 240)
        if end < len(text):
            # Prefer a sentence/paragraph boundary without stripping a single
            # character. Long sentences/markdown use a deterministic hard cut.
            boundaries = [i + 1 for i in range(at + 119, end)
                          if text[i] in '\n。！？!?;；' or (text[i] == '.' and i + 1 < len(text) and text[i + 1].isspace())]
            if boundaries:
                end = boundaries[-1]
            if 0 < len(text) - end < 8:
                end = len(text) - 8
        piece = text[at:end]
        if not 8 <= len(piece) <= 240:
            raise ValueError('invalid citation span length')
        start_offset, end_offset = base + at, base + end
        spans.append({'span_id': _span_id(window['id'], start_offset, end_offset, piece),
                      'start': start_offset, 'end': end_offset, 'text': piece})
        at = end
    return spans


def to_span_evidence_payload(payload: dict) -> dict:
    """Transform already-verified windows; preserve their exact geometry.

    This runs inside request authorization. Reconstruction is checked here at
    runtime, before any hold exists. Existing contextual window overlap remains
    included in the 21k bound; no full-window text is duplicated beside spans.
    """
    if 'wire_evidence_format' in payload:
        raise ValueError('evidence payload already has a wire format')
    original = payload['evidence_windows']
    if len(original) > MAX_WINDOWS or sum(len(w['text']) for w in original) > MAX_TEXT_CHARS:
        raise ValueError('evidence window bounds exceeded')
    windows = []
    for window in original:
        if type(window['start']) is not int or type(window['end']) is not int or len(window['text']) != window['end'] - window['start']:
            raise ValueError('invalid original window offsets')
        wire = {key: value for key, value in window.items() if key != 'text'}
        wire['spans'] = _partition_window(window) if len(window['text']) >= 8 else []
        if len(window['text']) < 8:
            wire['uncitable_context'] = window['text']
        restored = ''.join(span['text'] for span in wire['spans']) if wire['spans'] else wire.get('uncitable_context', '')
        if restored != window['text']:
            raise ValueError('citation spans do not reconstruct verified projection')
        windows.append(wire)
    result = {**payload, 'wire_evidence_format': WIRE_EVIDENCE_FORMAT, 'evidence_windows': windows}
    _validated_spans(result)
    return result


def _validated_spans(payload):
    """Validate the complete submitted table before resolving any selected ID."""
    spans = {}
    for window in payload['evidence_windows']:
        if 'text' in window or type(window.get('start')) is not int or type(window.get('end')) is not int or window['end'] < window['start']:
            raise ValueError('invalid span window')
        parts = window.get('spans')
        if not isinstance(parts, list):
            raise ValueError('missing span table')
        if not parts:
            context = window.get('uncitable_context')
            if not isinstance(context, str) or not 0 <= len(context) < 8 or len(context) != window['end'] - window['start']:
                raise ValueError('invalid uncitable context')
            continue
        if 'uncitable_context' in window:
            raise ValueError('context duplicated beside citation spans')
        position = window['start']
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get('text'), str):
                raise ValueError('invalid citation span')
            text = part['text']
            if (type(part.get('start')) is not int or type(part.get('end')) is not int
                    or part['start'] != position or part['end'] - part['start'] != len(text)
                    or not 8 <= len(text) <= 240):
                raise ValueError('noncontiguous citation spans')
            expected = _span_id(window['id'], part['start'], part['end'], text)
            if part.get('span_id') != expected or expected in spans:
                raise ValueError('invalid or duplicate span identity')
            spans[expected] = (window, part)
            position = part['end']
        if position != window['end']:
            raise ValueError('citation spans leave a coverage gap')
    return spans


def _union_size(ranges):
    total, right = 0, 0
    for start, end in sorted(ranges):
        total += max(0, end - max(right, start))
        right = max(right, end)
    return total


def build_evidence_page(text: str, ast_dict: dict, *, reviewed_chunk_ids=()) -> dict:
    """Return the next deterministic page; old-projection chunk IDs are invalid."""
    if not isinstance(text, str) or not isinstance(ast_dict, dict):
        raise ValueError('invalid evidence projection')
    projection_hash = hashlib.sha256(text.encode()).hexdigest()
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        chunks.append({'id': f'chunk-{projection_hash[:16]}-{len(chunks):05d}',
                       'start': start, 'end': end, 'text': text[start:end],
                       'section_kind': 'body'})
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
    by_id = {chunk['id']: chunk for chunk in chunks}
    reviewed = set(reviewed_chunk_ids)
    if not all(isinstance(value, str) for value in reviewed) or not reviewed <= by_id.keys():
        raise ValueError('reviewed windows belong to another projection')
    count = len(chunks)
    uniform = sorted({round(i * (count - 1) / (MAX_WINDOWS - 1))
                      for i in range(min(count, MAX_WINDOWS))}) if count > MAX_WINDOWS else list(range(count))
    groups = ast_dict.get('all_of', [])
    def anchor_score(index):
        normalized = unicodedata.normalize('NFKC', chunks[index]['text']).casefold()
        return sum(any(isinstance(term, str) and term.strip()
                       and unicodedata.normalize('NFKC', term).casefold() in normalized
                       for term in group.get('any_of', [])) for group in groups)
    # Keep the first and last uniform windows. At most two interior slots become
    # generic AST anchors; no product/sample-specific rules influence the order.
    first = list(uniform)
    reasons = {index: 'uniform' for index in first}
    if count > MAX_WINDOWS:
        anchors = sorted((i for i in range(count) if i not in first and anchor_score(i)),
                         key=lambda i: (-anchor_score(i), i))[:2]
        replaceable = [i for i in first if i not in {0, count - 1}]
        for anchor, replaced in zip(anchors, reversed(replaceable)):
            first[first.index(replaced)] = anchor
            reasons.pop(replaced, None)
            reasons[anchor] = 'ast_anchor'
    order = sorted(first) + [i for i in range(count) if i not in first]
    selected = [i for i in order if chunks[i]['id'] not in reviewed][:MAX_WINDOWS]
    windows = [{**chunks[i], 'selection_reason': reasons.get(i, 'remaining')} for i in selected]
    supplied = {window['id'] for window in windows}
    reviewed_ranges = [(by_id[key]['start'], by_id[key]['end']) for key in reviewed]
    supplied_ranges = [(window['start'], window['end']) for window in windows]
    after = reviewed | supplied
    coverage = {
        'projection_hash': projection_hash, 'projection_version': PROJECTION_VERSION,
        'full_chars': len(text), 'supplied_unique_chars': _union_size(supplied_ranges),
        'previously_reviewed_unique_chars': _union_size(reviewed_ranges),
        'covered_after_this_page_chars': _union_size(reviewed_ranges + supplied_ranges),
        'complete_after_this_page': bool(text) and _union_size(reviewed_ranges + supplied_ranges) == len(text),
        'reviewed_chunk_ids': sorted(reviewed),
        'remaining_chunk_ids': [chunk['id'] for chunk in chunks if chunk['id'] not in after],
        'all_chunk_ids': [chunk['id'] for chunk in chunks],
    }
    page_id = hashlib.sha256(json.dumps([CONTRACT_VERSION, projection_hash,
        [window['id'] for window in windows]], separators=(',', ':')).encode()).hexdigest()
    return {'page_id': page_id, 'chunk_ids': [window['id'] for window in windows],
            'evidence_windows': windows, 'coverage': coverage}


def validate_semantic_verdict(payload: dict, verdict: dict, *, normalized: bool = False) -> dict:
    """Validate supplied evidence, not model confidence or invented offsets.

    Structural grounding cannot prove semantic entailment. The independent
    acceptance sample remains necessary for false-positive/negative assessment.
    """
    if type(normalized) is not bool:
        raise ValueError('normalized mode must be selected by the caller')
    if not isinstance(verdict, dict) or verdict.get('outcome') not in {'core', 'mention', 'no_match', 'unknown'}:
        raise ValueError('invalid semantic outcome')
    scope = verdict.get('scope')
    if scope not in {'main', 'substantial_section', 'aside', 'advertisement', 'uncertain'}:
        raise ValueError('invalid semantic scope')
    condition = payload['intent']['condition']
    topic_mode = condition.get('match_mode') == 'topic'
    groups = condition['all_of']
    if topic_mode and (groups or not condition.get('semantic_intent')):
        raise ValueError('invalid topic condition')
    if not topic_mode and not groups:
        raise ValueError('missing condition groups')
    indexes = set(range(len(groups)))
    missing = verdict.get('missing_group_indexes')
    if not isinstance(missing, list) or any(type(i) is not int or i not in indexes for i in missing):
        raise ValueError('invalid missing groups')
    evidence = verdict.get('evidence')
    if not isinstance(evidence, list) or len(evidence) > 8:
        raise ValueError('invalid semantic evidence')
    windows = {window['id']: window for window in payload['evidence_windows']}
    if len(windows) != len(payload['evidence_windows']):
        raise ValueError('duplicate evidence window')
    wire_format = payload.get('wire_evidence_format')
    if wire_format not in {None, WIRE_EVIDENCE_FORMAT}:
        raise ValueError('unsupported evidence wire format')
    span_mode = wire_format == WIRE_EVIDENCE_FORMAT
    if topic_mode and not span_mode:
        raise ValueError('topic matching requires span evidence')
    spans = _validated_spans(payload) if span_mode else {}
    normalized_items, text_groups, source_groups, quote_windows = [], {}, set(), {}
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError('invalid evidence item')
        claimed = item.get('group_indexes')
        if (not isinstance(claimed, list) or (not topic_mode and not claimed)
                or any(type(i) is not int or i not in indexes for i in claimed)):
            raise ValueError('invalid evidence groups')
        quote = item.get('quote')
        if span_mode and not normalized and any(key in item for key in ('quote', 'window_id', 'start', 'end')):
            raise ValueError('transcribed evidence is forbidden in span mode')
        if not span_mode and 'span_id' in item:
            raise ValueError('span evidence is forbidden in legacy mode')
        if item.get('kind') == 'text':
            if span_mode:
                anchor = item.get('span_id')
                if not isinstance(anchor, str) or anchor not in spans:
                    raise ValueError('unresolvable citation span')
                window, part = spans[anchor]
                quote, offset = part['text'], part['start']
                canonical = {'kind': 'text', 'span_id': anchor, 'group_indexes': sorted(set(claimed)),
                    'window_id': window['id'], 'quote': quote, 'start': offset, 'end': part['end']}
                if normalized and any(item.get(key) != canonical[key] for key in ('quote', 'window_id', 'start', 'end')):
                    raise ValueError('normalized evidence differs from its server span')
            else:
                window = windows.get(item.get('window_id'))
                if not isinstance(quote, str) or not window or not 8 <= len(quote) <= 240 or quote not in window['text']:
                    raise ValueError('ungrounded text evidence')
                if len(window['text']) != window['end'] - window['start']:
                    raise ValueError('invalid window offsets')
                offset = window['start'] + window['text'].index(quote)
                canonical = {'kind': 'text', 'group_indexes': sorted(set(claimed)),
                    'window_id': window['id'], 'quote': quote, 'start': offset, 'end': offset + len(quote)}
            normalized_items.append(canonical)
            text_groups.setdefault(window['id'], set()).update(claimed)
            quote_windows[window['id']] = window
        elif item.get('kind') == 'source':
            if topic_mode:
                raise ValueError('topic matching requires body evidence')
            field = item.get('field')
            canonical_quote = payload.get('source', {}).get(field) if field in {'display_name', 'host'} else None
            if not isinstance(canonical_quote, str) or not canonical_quote:
                raise ValueError('ungrounded source evidence')
            if (not span_mode or normalized) and quote != canonical_quote:
                raise ValueError('ungrounded source evidence')
            if 'span_id' in item:
                raise ValueError('source evidence cannot select a text span')
            normalized_items.append({'kind': 'source', 'group_indexes': sorted(set(claimed)), 'field': field, 'quote': canonical_quote})
            source_groups.update(claimed)
        else:
            raise ValueError('invalid evidence kind')
    if sum(item['kind'] == 'text' for item in normalized_items) > 6 or sum(item['kind'] == 'source' for item in normalized_items) > 2:
        raise ValueError('too many evidence quotes')
    satisfying_clusters = []
    if verdict['outcome'] == 'core':
        if scope not in {'main', 'substantial_section'} or missing or not text_groups:
            raise ValueError('core lacks substantive text evidence')
        if not topic_mode and source_groups == indexes:
            raise ValueError('core requires a condition group supported only by text')
        # An all-of claim cannot stitch unrelated distant windows together.
        clusters = []
        for window in sorted(quote_windows.values(), key=lambda value: value['start']):
            if clusters and window['start'] <= clusters[-1][1]:
                clusters[-1][1] = max(clusters[-1][1], window['end'])
                clusters[-1][2].update(text_groups[window['id']])
            else:
                clusters.append([window['start'], window['end'], set(text_groups[window['id']])])
        satisfying_clusters = [(start,end) for start,end,covered in clusters if (covered | source_groups) == indexes]
        if not satisfying_clusters:
            raise ValueError('core groups lack one continuous discussion')
    reason = verdict.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('missing semantic reason')
    outcome, extension = verdict['outcome'], {}
    intent = payload['intent']['condition'].get('semantic_intent')
    if intent is not None:
        if not span_mode or intent.get('version') != 1:
            raise ValueError('unsupported semantic intent evidence protocol')
        outcome, reason, assessment = _intent_assessment(
            payload,verdict,spans,normalized_items,satisfying_clusters,normalized)
        extension = {'intent_assessment':assessment, 'semantic_intent_version':intent['version'],
                     'semantic_intent_hash':semantic_intent_hash(intent)}
    return {'outcome': outcome, 'scope': scope, 'reason': reason[:1000],
            'evidence': normalized_items, 'quotes': [item['quote'] for item in normalized_items if item['kind'] == 'text'],
            'missing_group_indexes': sorted(set(missing)), 'matcher_version': CONTRACT_VERSION,
            'coverage': dict(payload['coverage']), **extension}
