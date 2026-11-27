"""Citation transport is lossless; it never asks the model to transcribe text."""
import copy
import json

import pytest

from services import interest_evidence_windows as evidence

AST = {'all_of': [{'any_of': ['robotics']}, {'any_of': ['planning']}], 'exclude': []}


def original(text):
    return {'intent': {'condition': AST}, 'source': {'display_name': 'Example', 'host': 'example.com'},
            **evidence.build_evidence_page(text, AST)}


def raw_core(payload, spans=None):
    spans = spans or payload['evidence_windows'][0]['spans'][:1]
    return {'outcome': 'core', 'scope': 'main', 'reason': 'One substantive discussion',
            'missing_group_indexes': [], 'evidence': [
                {'kind': 'text', 'span_id': span['span_id'], 'group_indexes': [0, 1]} for span in spans]}


@pytest.mark.parametrize('text', [
    'So [the robot planner](https://example.com/planner) executes the requested sequence exactly.',
    'So '+ '严谨的逐字原文' * 42 + '完成。',
    '界' * 301,
    'So lower-case rewriting must not change this initial letter in the citation.',
])
def test_markdown_long_quote_and_case_drift_have_no_transcription_path(text):
    payload = evidence.to_span_evidence_payload(original(text))
    spans = payload['evidence_windows'][0]['spans']
    normalized = evidence.validate_semantic_verdict(payload, raw_core(payload, spans))
    assert ''.join(normalized['quotes']) == text
    assert all(8 <= len(quote) <= 240 for quote in normalized['quotes'])
    assert evidence.validate_semantic_verdict(payload, normalized, normalized=True) == normalized


@pytest.mark.parametrize('length', list(range(1, 9)) + list(range(240, 249)) + [479, 480, 481, 3500])
def test_short_tail_rebalances_without_loss_or_overlong_spans(length):
    text = ('中👩\u200d💻e\u0301\n[link](https://example.com) ' * 150)[:length]
    source = original(text)
    payload = evidence.to_span_evidence_payload(source)
    assert payload['coverage'] == source['coverage']
    for old, window in zip(source['evidence_windows'], payload['evidence_windows']):
        assert 'text' not in window
        assert (old['id'], old['start'], old['end']) == (window['id'], window['start'], window['end'])
        parts = window['spans']
        restored = ''.join(part['text'] for part in parts) if parts else window['uncitable_context']
        assert restored == old['text']
        if length < 8:
            assert parts == [] and len(window['uncitable_context']) < 8
        else:
            assert all(8 <= len(part['text']) <= 240 for part in parts)
            assert parts[0]['start'] == window['start'] and parts[-1]['end'] == window['end']
            assert all(left['end'] == right['start'] for left, right in zip(parts, parts[1:]))
    assert source == original(text)  # transformation never mutates source evidence
    assert payload == evidence.to_span_evidence_payload(source)


def test_raw_model_cannot_switch_to_normalized_mode_or_supply_derived_fields():
    payload = evidence.to_span_evidence_payload(original('Robotics planning evidence from a substantive body.'))
    raw = raw_core(payload)
    canonical = evidence.validate_semantic_verdict(payload, raw)
    for key in ('quote', 'window_id', 'start', 'end'):
        forged = copy.deepcopy(raw)
        forged['normalized'] = True
        forged['evidence'][0][key] = canonical['evidence'][0][key]
        with pytest.raises(ValueError, match='transcribed'):
            evidence.validate_semantic_verdict(payload, forged)
    no_anchor = copy.deepcopy(canonical); no_anchor['evidence'][0].pop('span_id')
    with pytest.raises(ValueError, match='unresolvable'):
        evidence.validate_semantic_verdict(payload, no_anchor, normalized=True)
    for key in ('quote', 'window_id', 'start', 'end'):
        forged = copy.deepcopy(canonical)
        forged['evidence'][0][key] = 'changed' if isinstance(forged['evidence'][0][key], str) else -1
        with pytest.raises(ValueError, match='differs'):
            evidence.validate_semantic_verdict(payload, forged, normalized=True)


def test_ids_resolve_only_in_current_payload_but_any_supplied_window_is_valid():
    payload = evidence.to_span_evidence_payload(original('Robotics planning context. ' * 3000))
    final_span = payload['evidence_windows'][-1]['spans'][-1]
    result = evidence.validate_semantic_verdict(payload, raw_core(payload, [final_span]))
    assert result['evidence'][0]['window_id'] == payload['evidence_windows'][-1]['id']
    other = evidence.to_span_evidence_payload(original('Different content identity and supplied text.'))
    with pytest.raises(ValueError, match='unresolvable'):
        evidence.validate_semantic_verdict(payload, raw_core(payload, other['evidence_windows'][0]['spans']))
    damaged = copy.deepcopy(payload); damaged['evidence_windows'][0]['spans'][0]['text'] = 'changed'
    with pytest.raises(ValueError): evidence.validate_semantic_verdict(damaged, raw_core(damaged))


def test_legacy_quotes_stay_strict_and_cannot_accept_span_evidence():
    payload = original('So [robots](https://example.com) perform planning operations.')
    window = payload['evidence_windows'][0]
    verdict = {'outcome': 'core', 'scope': 'main', 'reason': 'Legacy exact quote', 'missing_group_indexes': [],
               'evidence': [{'kind': 'text', 'group_indexes': [0, 1], 'window_id': window['id'], 'quote': window['text']}]}
    assert evidence.validate_semantic_verdict(payload, verdict)['quotes'] == [window['text']]
    wrong = copy.deepcopy(verdict); wrong['evidence'][0]['span_id'] = 'not-legacy'
    with pytest.raises(ValueError, match='legacy'): evidence.validate_semantic_verdict(payload, wrong)
    wrong = copy.deepcopy(verdict); wrong['evidence'][0]['quote'] = window['text'].lower()
    with pytest.raises(ValueError, match='ungrounded'): evidence.validate_semantic_verdict(payload, wrong)


def test_new_source_evidence_selects_field_and_server_resolves_exact_value():
    payload = evidence.to_span_evidence_payload(original('Planning operations are discussed in this body.'))
    raw = raw_core(payload); raw['evidence'][0]['group_indexes'] = [1]
    raw['evidence'].append({'kind': 'source', 'field': 'host', 'group_indexes': [0]})
    result = evidence.validate_semantic_verdict(payload, raw)
    assert result['evidence'][1]['quote'] == 'example.com'
    assert evidence.validate_semantic_verdict(payload, result, normalized=True) == result
    raw['evidence'][1]['quote'] = 'example.com'
    with pytest.raises(ValueError, match='transcribed'): evidence.validate_semantic_verdict(payload, raw)


def test_actual_canonical_bytes_include_span_overhead_without_duplicate_window_text():
    original_payload = original('中' * 68700)
    payload = evidence.to_span_evidence_payload(original_payload)
    encoded = evidence.encode_evidence_payload(payload)
    assert json.loads(encoded) == payload
    assert sum(len(span['text']) for window in payload['evidence_windows'] for span in window['spans']) == 21000
    assert 21000 * 3 < len(encoded.encode()) < 128000
    assert all('text' not in window for window in payload['evidence_windows'])


def test_authorization_transform_asserts_reconstruction_at_runtime(monkeypatch):
    source = original('Verified source projection that must be preserved exactly.')
    partition = evidence._partition_window
    def broken(window):
        parts = partition(window)
        parts[0]['text'] = parts[0]['text'][1:]
        return parts
    monkeypatch.setattr(evidence, '_partition_window', broken)
    with pytest.raises(ValueError, match='reconstruct'): evidence.to_span_evidence_payload(source)


INTENT = {'version':1,'requirement':'Use AI as a tool for scientific research, not only as the object of research.',
          'negative_examples':['Measuring AI data-center carbon emissions without AI-assisted research.']}


def intent_payload(text='AI methods help scientists design new experiments and test scientific hypotheses.'):
    payload=original(text)
    payload['intent']={'condition':{**AST,'semantic_intent':INTENT}}
    return evidence.to_span_evidence_payload(payload)


def assessed(payload, *, relation='satisfied', ids=None, negatives=None):
    raw=raw_core(payload)
    raw['intent_assessment']={'verdict':relation,'reason':'The method is used as a scientific tool.',
        'evidence_span_ids':ids if ids is not None else [payload['evidence_windows'][0]['spans'][0]['span_id']],
        'negative_example_indexes':negatives or []}
    return raw


def test_intent_relation_core_is_grounded_and_double_validation_stable():
    payload=intent_payload()
    result=evidence.validate_semantic_verdict(payload,assessed(payload))
    assert result['outcome']=='core'
    assert result['semantic_intent_hash']==evidence.semantic_intent_hash(INTENT)
    assert result['semantic_intent_version']==1
    citation=result['intent_assessment']['evidence'][0]
    assert citation['quote']==payload['evidence_windows'][0]['spans'][0]['text']
    assert evidence.validate_semantic_verdict(payload,result,normalized=True)==result


@pytest.mark.parametrize('anomaly',['missing','contradiction','outside_cluster','no_text','uncertain'])
def test_parseable_assessment_anomalies_are_page_unknown_not_sticky(anomaly):
    payload=intent_payload('A substantial independent research discussion. '*3000)
    raw=assessed(payload)
    if anomaly=='missing':raw.pop('intent_assessment')
    elif anomaly=='contradiction':raw['intent_assessment']['negative_example_indexes']=[0]
    elif anomaly=='outside_cluster':
        w=next(w for w in payload['evidence_windows'] if w['start']>payload['evidence_windows'][0]['end'])
        raw['intent_assessment']['evidence_span_ids']=[w['spans'][0]['span_id']]
    elif anomaly=='no_text':raw['intent_assessment']['evidence_span_ids']=[]
    else:raw['intent_assessment']['verdict']='unknown'
    result=evidence.validate_semantic_verdict(payload,raw)
    assert result['outcome']=='unknown' and result['reason']
    assert evidence.validate_semantic_verdict(payload,result,normalized=True)==result


@pytest.mark.parametrize('field,value',[
    ('verdict','maybe'),('verdict',[]),('reason',False),('reason',''),
    ('evidence_span_ids','span'),('evidence_span_ids',['forged-span']),
    ('negative_example_indexes',[1]),('negative_example_indexes',[-1]),
    ('negative_example_indexes',[True]),('negative_example_indexes','0'),
    ('quote','model-made text'),
])
def test_invalid_assessment_structure_stays_a_validator_failure(field,value):
    payload=intent_payload();raw=assessed(payload)
    raw['intent_assessment'][field]=value
    with pytest.raises(ValueError):evidence.validate_semantic_verdict(payload,raw)


def test_assessment_null_is_invalid_and_normalized_citation_cannot_be_forged():
    payload=intent_payload();raw=assessed(payload)
    raw['intent_assessment']=None
    with pytest.raises(ValueError):evidence.validate_semantic_verdict(payload,raw)
    normalized=evidence.validate_semantic_verdict(payload,assessed(payload))
    normalized['intent_assessment']['evidence'][0]['quote']='forged quote'
    with pytest.raises(ValueError,match='differs'):
        evidence.validate_semantic_verdict(payload,normalized,normalized=True)


def test_counterexample_is_grounded_page_negative_not_an_article_veto():
    payload=intent_payload('Researchers measured the energy costs and carbon emissions of AI data centers.')
    raw=assessed(payload,relation='not_satisfied',negatives=[0])
    raw['intent_assessment']['reason']='AI is the measured research object here, not a scientific tool.'
    result=evidence.validate_semantic_verdict(payload,raw)
    assert result['outcome']=='no_match' and result['intent_assessment']['negative_example_indexes']==[0]
    assert evidence.validate_semantic_verdict(payload,result,normalized=True)==result
    mixed=intent_payload('Data-center emissions are background. AI models then guide scientific experiments to discover new materials.')
    assert evidence.validate_semantic_verdict(mixed,assessed(mixed))['outcome']=='core'


def test_relation_span_budget_counts_union_with_group_evidence():
    payload=intent_payload('Substantive research context. '*1000)
    spans=payload['evidence_windows'][0]['spans']
    raw=raw_core(payload,spans[:6])
    raw['intent_assessment']=assessed(payload,ids=[spans[6]['span_id']])['intent_assessment']
    with pytest.raises(ValueError,match='combined'):evidence.validate_semantic_verdict(payload,raw)
    raw['intent_assessment']['evidence_span_ids']=[spans[0]['span_id']]
    assert evidence.validate_semantic_verdict(payload,raw)['outcome']=='core'
    raw['intent_assessment']['evidence_span_ids']*=2
    with pytest.raises(ValueError,match='spans'):evidence.validate_semantic_verdict(payload,raw)
