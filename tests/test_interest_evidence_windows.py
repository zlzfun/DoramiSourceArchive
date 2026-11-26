"""Bounded scan pages cover the whole projection without fabricated evidence."""
import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session

from config import LLMConfig
from llm.client import LLMError
from models.db import SourceConfigRecord
from services import interest_governance_llm as bridge
from services.interest_evidence_windows import build_evidence_page, validate_semantic_verdict
from storage.impl.db_storage import DatabaseStorage

AST = {'all_of': [{'any_of': ['browser']}, {'any_of': ['permission']}], 'exclude': []}


def payload(text='A browser must ask permission before performing important actions.'):
    return {'intent': {'condition': AST}, 'source': {'display_name': 'Example', 'host': 'example.com'},
            **build_evidence_page(text, AST)}


def core(data):
    window = data['evidence_windows'][0]
    return {'outcome': 'core', 'scope': 'main', 'reason': 'Same substantive discussion',
            'missing_group_indexes': [], 'evidence': [{'kind': 'text', 'group_indexes': [0, 1],
            'window_id': window['id'], 'quote': window['text'][:80]}]}


def test_deterministic_pages_reach_tail_and_prove_union_coverage():
    text = ('无关键词的证据正文。' * 6000)[:51585]
    text = text[:42000] + 'browser permission is explained here' + text[42036:]
    reviewed, ranges = [], []
    first = build_evidence_page(text, AST)
    assert first == build_evidence_page(text, AST)
    assert any('explained here' in window['text'] for window in first['evidence_windows'])
    assert sum(window['selection_reason'] == 'ast_anchor' for window in first['evidence_windows']) <= 2
    assert not first['coverage']['complete_after_this_page']
    while True:
        page = build_evidence_page(text, AST, reviewed_chunk_ids=reviewed)
        assert len(page['evidence_windows']) <= 6
        assert sum(len(window['text']) for window in page['evidence_windows']) <= 21000
        assert not set(reviewed) & set(page['chunk_ids'])
        ranges.extend((window['start'], window['end']) for window in page['evidence_windows'])
        reviewed += page['chunk_ids']
        if page['coverage']['complete_after_this_page']:
            assert page['coverage']['covered_after_this_page_chars'] == len(text)
            assert page['coverage']['remaining_chunk_ids'] == []
            break
    assert sum(end - start for start, end in ranges) > len(text)  # overlaps never double-counted
    assert sorted({position for start, end in ranges for position in range(start, end)}) == list(range(len(text)))
    complete = build_evidence_page(text, AST, reviewed_chunk_ids=reviewed)
    assert complete['chunk_ids'] == []
    assert complete['coverage']['previously_reviewed_unique_chars'] == len(text)
    with pytest.raises(ValueError, match='another projection'):
        build_evidence_page(text + 'changed', AST, reviewed_chunk_ids=reviewed)


def test_zero_keyword_long_text_still_gets_uniform_first_middle_and_tail():
    text = 'unrelated research context\n' * 3000
    page = build_evidence_page(text, AST)
    assert len(page['evidence_windows']) == 6
    assert page['evidence_windows'][0]['start'] == 0
    assert page['evidence_windows'][-1]['end'] == len(text)
    assert page['coverage']['covered_after_this_page_chars'] < len(text)


def test_exact_supplied_quote_offsets_and_caller_coverage_cannot_be_forged():
    data = payload()
    result = validate_semantic_verdict(data, {**core(data), 'coverage': {'complete_after_this_page': False}})
    assert result['coverage'] == data['coverage']
    quote = result['evidence'][0]
    assert data['evidence_windows'][0]['text'][quote['start']:quote['end']] == quote['quote']
    assert validate_semantic_verdict(data, result) == result


@pytest.mark.parametrize('mutation', ['fake_quote', 'missing_group', 'source_only', 'advertisement', 'bad_window', 'bad_source', 'bad_index'])
def test_invalid_core_evidence_fails_closed(mutation):
    data = payload(); verdict = core(data)
    if mutation == 'fake_quote': verdict['evidence'][0]['quote'] = 'fabricated evidence never supplied'
    elif mutation == 'missing_group': verdict['evidence'][0]['group_indexes'] = [0]
    elif mutation == 'source_only': verdict['evidence'] = [{'kind': 'source', 'field': 'host', 'quote': 'example.com', 'group_indexes': [0, 1]}]
    elif mutation == 'advertisement': verdict['scope'] = 'advertisement'
    elif mutation == 'bad_window': verdict['evidence'][0]['window_id'] = 'not-supplied'
    elif mutation == 'bad_source': verdict['evidence'].append({'kind': 'source', 'field': 'url', 'quote': 'example.com', 'group_indexes': [1]})
    else: verdict['evidence'][0]['group_indexes'] = [True, 1]
    with pytest.raises(ValueError): validate_semantic_verdict(data, verdict)


def test_distant_windows_cannot_be_stitched_into_all_of_core():
    data = payload('First topic with browser context.\n' * 2000)
    first, last = data['evidence_windows'][0], data['evidence_windows'][-1]
    verdict = core(data)
    verdict['evidence'] = [{'kind': 'text', 'window_id': window['id'], 'quote': window['text'][:40], 'group_indexes': [group]}
                           for window, group in ((first, 0), (last, 1))]
    with pytest.raises(ValueError, match='continuous'): validate_semantic_verdict(data, verdict)


def test_source_identity_can_complement_real_text_but_not_replace_it():
    data = payload(); verdict = core(data)
    data['intent']['condition'] = {'all_of': [{'any_of': ['Example']}, {'any_of': ['permission']}], 'exclude': []}
    verdict['evidence'][0]['group_indexes'] = [1]
    verdict['evidence'].append({'kind': 'source', 'field': 'display_name', 'quote': 'Example', 'group_indexes': [0]})
    assert validate_semantic_verdict(data, verdict)['outcome'] == 'core'


def test_source_cannot_claim_every_group_and_attach_an_irrelevant_quote():
    data = payload(); verdict = core(data)
    verdict['evidence'][0]['group_indexes'] = [0]
    verdict['evidence'].append({'kind': 'source', 'field': 'host',
                                'quote': 'example.com', 'group_indexes': [0, 1]})
    with pytest.raises(ValueError, match='supported only by text'):
        validate_semantic_verdict(data, verdict)
    data['intent']['condition'] = {'all_of': [{'any_of': ['Example']}], 'exclude': []}
    verdict['evidence'][1]['group_indexes'] = [0]
    with pytest.raises(ValueError, match='supported only by text'):
        validate_semantic_verdict(data, verdict)


def test_partial_negative_keeps_actual_coverage_and_requires_valid_schema():
    data = payload('正文与主题无关。' * 10000)
    verdict = {'outcome': 'no_match', 'scope': 'uncertain', 'reason': 'No evidence in this page',
               'evidence': [], 'missing_group_indexes': [0, 1]}
    result = validate_semantic_verdict(data, verdict)
    assert not result['coverage']['complete_after_this_page']
    with pytest.raises(ValueError): validate_semantic_verdict(data, {'outcome': 'no_match'})


def test_source_metadata_uses_directory_and_host_only_and_ignores_bookkeeping(tmp_path):
    store = DatabaseStorage(db_url=f'sqlite:///{tmp_path / "sources.db"}')
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with Session(store.engine) as session:
            source = SourceConfigRecord(source_id='public', name='Configured publisher',
                url='https://user:secret@ml.example.com/feed?token=secret', created_at=stamp, updated_at=stamp)
            session.add(source); session.commit()
            article = SimpleNamespace(source_id='public', source_url='https://impostor.invalid', title='I claim another publisher')
            first = bridge.trusted_source_metadata(session, article)
            assert first['host'] == 'ml.example.com'
            assert 'secret' not in json.dumps(first) and 'impostor' not in json.dumps(first)
            source.updated_at = 'bookkeeping'; session.add(source); session.commit()
            assert bridge.trusted_source_metadata(session, article) == first
            source.name = 'Renamed publisher'; session.add(source); session.commit()
            assert bridge.trusted_source_metadata(session, article)['metadata_hash'] != first['metadata_hash']
    finally: store.engine.dispose()


@pytest.mark.parametrize('thinking,status', [('', 400), ('high', 400), ('', 307), ('', 308)])
def test_interest_bridge_protocol_errors_are_exactly_one_http(monkeypatch, thinking, status):
    config = LLMConfig(base_url='https://example.com/v1', api_key='fixture', model='aux', thinking_mode=thinking)
    monkeypatch.setattr(bridge, '_interest_config', lambda _engine: config)
    calls = []
    original = httpx.AsyncClient
    def transport(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'https://example.com/redirect'}, json={'error': 'unsupported protocol'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    verify = bridge.make_interest_verifier(object())
    assert verify.available()
    with pytest.raises(LLMError): verify(username='alice', payload=payload(), reserved_output_token_limit=verify.max_output_tokens, expected_verifier_policy=verify.verifier_policy)
    assert len(calls) == 1


def test_interest_bridge_availability_checks_cutoff_without_http(monkeypatch):
    config = LLMConfig(base_url='https://example.com', api_key='fixture', model='aux',
                       paid_authorized_until=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat())
    monkeypatch.setattr(bridge, '_interest_config', lambda _engine: config)
    verify = bridge.make_interest_verifier(object())
    assert not verify.available()
    monkeypatch.setattr(bridge, '_interest_config', lambda _engine: replace(config, paid_authorized_until=''))
    assert verify.available()
