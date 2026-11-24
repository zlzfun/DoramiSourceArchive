"""Real matcher/hold + mocked HTTP policy and budget integration; no live API."""
import json
from dataclasses import replace

import httpx
import pytest
from sqlmodel import Session

import config as config_module
from config import LLMConfig
from services import daily_brief
from services import custom_interests as service
from services import interest_governance_llm as bridge
from tests.test_custom_interest_semantic_scans import (
    store, seed, records, run, answer, INTENT_AST, Request, Scan, Match,
)


def pro():
    return LLMConfig(base_url='https://example.com/v1', api_key='fixture-secret',
        model='test-pro', thinking_mode='high', interest_max_output_tokens=8192)


def provider(store, monkeypatch, config=None, response_mutator=None):
    selected = [config or pro()]
    monkeypatch.setattr(bridge, 'resolve_llm_config', lambda _: selected[0])
    seen = []
    original = httpx.AsyncClient
    def transport(request):
        wire = json.loads(request.content)
        payload = json.loads(wire['messages'][-1]['content'])
        hold = next(row for row in records(store, Request) if row['payload_json'] == wire['messages'][-1]['content'])
        assert hold['status'] == 'authorized'
        assert wire['max_tokens'] == hold['output_token_limit'] == selected[0].interest_max_output_tokens
        policy = bridge.verifier_policy(selected[0])
        assert json.loads(hold['result_json']) == {'authorization': {'verifier_policy': policy}}
        assert not {'authorization', 'verifier_policy', 'reserved_output_token_limit'} & set(payload)
        seen.append({'wire': wire, 'hold': hold})
        raw = answer(payload, normalized=False)
        if payload['intent']['condition'].get('semantic_intent'):
            raw['intent_assessment'] = {'verdict': 'satisfied', 'reason': 'Relation in the original body',
                'evidence_span_ids': [payload['evidence_windows'][0]['spans'][0]['span_id']],
                'negative_example_indexes': []}
        raw['verifier_policy'] = {'model': 'model-supplied-fake'}
        raw['authorization'] = {'verifier_policy': 'model-supplied-fake'}
        if response_mutator:
            response_mutator(raw, hold, selected)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(raw)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    return selected, seen


def test_default_ini_resolver_and_aux_preserve_independent_interest_budget(store, monkeypatch, tmp_path):
    path = tmp_path / 'config.ini'
    path.write_text('[llm]\nmodel = test-pro\nmax_tokens = 7777\n')
    monkeypatch.setenv('DORAMI_CONFIG_FILE', str(path))
    base = config_module.load_config()
    assert base.llm.interest_max_output_tokens == 1800
    path.write_text('[llm]\nmodel = test-pro\nmax_tokens = 7777\ninterest_max_output_tokens = 8192\n')
    base = config_module.load_config()
    monkeypatch.setattr(config_module, 'settings', base)
    with Session(store.engine) as session:
        resolved = daily_brief.resolve_llm_config(session)
    assert resolved.interest_max_output_tokens == 8192 and resolved.max_tokens == 7777
    assert replace(resolved, aux_model='other').for_aux().interest_max_output_tokens == 8192
    path.write_text('[llm]\ninterest_max_output_tokens = 8193\n')
    with pytest.raises(ValueError): config_module.load_config()


@pytest.mark.parametrize('value', [True, False, 0, -1, 8193, 1.2, '8192', None])
def test_invalid_budget_rejected(value):
    with pytest.raises(ValueError): LLMConfig(interest_max_output_tokens=value)


@pytest.mark.parametrize('aux,model,mode', [('', 'test-pro', 'high'), ('test-pro', 'test-pro', 'high'), ('test-aux', 'test-aux', 'omitted')])
def test_effective_aux_policy(aux, model, mode):
    cfg = replace(pro(), aux_model=aux)
    policy = bridge.verifier_policy(cfg)
    assert policy['model'] == model and policy['thinking_mode'] == mode
    assert policy['max_output_tokens'] == 8192


@pytest.mark.parametrize('url', ['https://EXAMPLE.com:443/v1/', 'https://example.com/v1/chat/completions/', 'https://example.com/v1'])
def test_endpoint_hash_matches_actual_endpoint_normalization(url):
    assert bridge.verifier_policy(replace(pro(), base_url=url)) == bridge.verifier_policy(pro())


def test_policy_has_no_secret_or_deadline_and_bad_endpoint_errors_sanitized():
    cfg = replace(pro(), base_url='https://username:password@example.com/v1/chat/completions?token=private#private')
    policy = bridge.verifier_policy(cfg)
    assert not any(secret in json.dumps(policy) for secret in ['username', 'password', 'private', 'fixture-secret', 'example.com'])
    assert bridge.verifier_policy(replace(pro(), api_key='rotated', paid_authorized_until='2000-01-01T00:00:00Z')) == bridge.verifier_policy(pro())
    with pytest.raises(bridge.SemanticPolicyPrecheckError) as error:
        bridge.verifier_policy(replace(pro(), base_url='https://secret:bad-port@bad.example:password/v1'))
    assert 'password' not in str(error.value) and 'bad.example' not in str(error.value)


def test_real_hold_http_and_result_policy_are_equal_and_model_marker_is_stripped(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    selected, seen = provider(store, monkeypatch)
    verifier = bridge.make_interest_verifier(store.engine)
    assert verifier.max_output_tokens == 8192
    run(store, verifier)
    assert len(seen) == 1
    wire = seen[0]['wire']
    assert wire['thinking'] == {'type': 'enabled'} and wire['reasoning_effort'] == 'high'
    policy = bridge.verifier_policy(selected[0])
    request = records(store, Request)[0]
    assert request['status'] == 'succeeded'
    result = json.loads(request['result_json'])
    assert result['authorization'] == {'verifier_policy': policy}
    assert result['verifier_policy'] == policy
    for model, field in [(Scan, 'result_json'), (Match, 'evidence_json')]:
        assert json.loads(records(store, model)[0][field])['verifier_policy'] == policy
    with Session(store.engine) as session:
        assert len(service.current_matches(session, 'alice')) == 1


@pytest.mark.parametrize('change', ['model', 'thinking', 'budget', 'endpoint', 'missing', 'bool_version'])
def test_read_gate_and_success_cache_never_recharge_or_relabel(store, monkeypatch, change):
    seed(store, condition_ast=INTENT_AST)
    selected, seen = provider(store, monkeypatch)
    run(store, bridge.make_interest_verifier(store.engine))
    old = records(store, Request)
    if change in ('missing', 'bool_version'):
        with Session(store.engine) as session:
            match = session.get(Match, records(store, Match)[0]['id'])
            evidence = json.loads(match.evidence_json)
            if change == 'missing': evidence.pop('verifier_policy')
            else: evidence['verifier_policy']['version'] = True
            match.evidence_json = json.dumps(evidence); session.add(match); session.commit()
    else:
        selected[0] = replace(selected[0], **{
            'model': {'model': 'different-pro'}, 'thinking': {'thinking_mode': 'disabled'},
            'budget': {'interest_max_output_tokens': 1800}, 'endpoint': {'base_url': 'https://different.example/v1'},
        }[change])
    with Session(store.engine) as session:
        assert service.current_matches(session, 'alice') == []
    if change not in ('missing', 'bool_version'):
        run(store, bridge.make_interest_verifier(store.engine), 10)
        with Session(store.engine) as session: assert service.current_matches(session, 'alice') == []
    assert len(seen) == 1 and records(store, Request) == old


def test_mid_hold_policy_drift_is_zero_post_sticky_unknown(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    selected, seen = provider(store, monkeypatch)
    initial = selected[0]
    verifier = bridge.make_interest_verifier(store.engine)
    authorize = service._authorize_page
    def drift(*args, **kwargs):
        attempt = authorize(*args, **kwargs)
        selected[0] = replace(initial, thinking_mode='disabled')
        return attempt
    monkeypatch.setattr(service, '_authorize_page', drift)
    run(store, verifier)
    old = records(store, Request)
    assert len(old) == 1 and old[0]['status'] == 'unknown'
    assert old[0]['error_code'] == 'policy_precheck_mismatch' and seen == []
    assert json.loads(old[0]['result_json'])['authorization']['verifier_policy'] == bridge.verifier_policy(initial)
    selected[0] = initial
    monkeypatch.setattr(service, '_authorize_page', authorize)
    run(store, bridge.make_interest_verifier(store.engine), 10)
    assert records(store, Request) == old and seen == []


def test_changed_wrapper_budget_fails_before_hold(store, monkeypatch):
    seed(store)
    _, seen = provider(store, monkeypatch)
    verifier = bridge.make_interest_verifier(store.engine)
    verifier.max_output_tokens = 1800
    assert run(store, verifier)['status'] == 'failed'
    assert records(store, Request) == [] and seen == []


def test_settle_reads_durable_authorization_and_fences_tampering(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    def corrupt(_raw, hold, _selected):
        with Session(store.engine) as session:
            record = session.get(Request, hold['id'])
            record.result_json = json.dumps({'authorization': {'verifier_policy': 'corrupt'}})
            session.add(record); session.commit()
    _, seen = provider(store, monkeypatch, response_mutator=corrupt)
    run(store, bridge.make_interest_verifier(store.engine))
    request = records(store, Request)[0]
    assert len(seen) == 1 and request['status'] == 'unknown'
    assert request['error_code'] == 'policy_precheck_mismatch'
    assert json.loads(request['result_json'])['authorization'] == {'verifier_policy': 'corrupt'}
    with Session(store.engine) as session: assert service.current_matches(session, 'alice') == []


def test_config_change_during_http_preserves_original_policy_but_hides_core(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    def change(_raw, _hold, selected): selected[0] = replace(selected[0], thinking_mode='disabled')
    selected, seen = provider(store, monkeypatch, response_mutator=change)
    run(store, bridge.make_interest_verifier(store.engine))
    result = json.loads(records(store, Request)[0]['result_json'])
    assert result['verifier_policy']['thinking_mode'] == 'high'
    assert records(store, Request)[0]['status'] == 'succeeded' and len(seen) == 1
    with Session(store.engine) as session: assert service.current_matches(session, 'alice') == []


def test_legacy_success_cache_without_marker_stays_unmarked_and_never_recharged(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    selected, seen = provider(store, monkeypatch)
    run(store, bridge.make_interest_verifier(store.engine))
    with Session(store.engine) as session:
        for model, field in [(Request, 'result_json'), (Scan, 'result_json'), (Match, 'evidence_json')]:
            row = session.get(model, records(store, model)[0]['id'])
            value = json.loads(getattr(row, field))
            value.pop('verifier_policy', None); value.pop('authorization', None)
            setattr(row, field, json.dumps(value)); session.add(row)
        session.commit()
    old = records(store, Request)
    selected[0] = replace(selected[0], model='another-pro', interest_max_output_tokens=1800)
    run(store, bridge.make_interest_verifier(store.engine), 10)
    assert records(store, Request) == old and len(seen) == 1
    assert 'verifier_policy' not in json.loads(records(store, Scan)[0]['result_json'])
    assert 'verifier_policy' not in json.loads(records(store, Match)[0]['evidence_json'])
    with Session(store.engine) as session: assert service.current_matches(session, 'alice') == []


@pytest.mark.parametrize('budget,policy_change', [(1800, False), (8192, True), (True, False), (None, False)])
def test_factory_rejects_trusted_precheck_mismatch_without_http(store, monkeypatch, budget, policy_change):
    _, seen = provider(store, monkeypatch)
    verifier = bridge.make_interest_verifier(store.engine)
    policy = {**verifier.verifier_policy, **({'thinking_mode': 'disabled'} if policy_change else {})}
    with pytest.raises(bridge.SemanticPolicyPrecheckError):
        verifier(username='alice', payload={}, reserved_output_token_limit=budget, expected_verifier_policy=policy)
    assert seen == []


@pytest.mark.parametrize('first', ['mention', 'unknown'])
def test_paged_results_wait_for_original_policy_before_completing(store, monkeypatch, first):
    seed(store, condition_ast=INTENT_AST, body='A continuous detailed discussion of machines planning scientific experiments. '*700)
    count = [0]
    def verdict(raw, _hold, selected):
        count[0] += 1
        if count[0] == 1:
            raw.update(outcome=first, scope='aside' if first == 'mention' else 'uncertain')
            selected[0] = replace(selected[0], thinking_mode='disabled')
        elif first == 'mention':
            raw.update(outcome='no_match', scope='uncertain', evidence=[], missing_group_indexes=[0,1])
            raw['intent_assessment'].update(verdict='not_satisfied')
    selected, seen = provider(store, monkeypatch, response_mutator=verdict)
    run(store, bridge.make_interest_verifier(store.engine))
    old = records(store, Request)[0]
    assert records(store, Scan)[0]['status'] == 'partial'
    before_scan = records(store, Scan)
    blocked = run(store, bridge.make_interest_verifier(store.engine), 1)
    assert blocked['error_code'] == 'verifier_policy_changed'
    assert blocked['semantic_calls'] == 0 and len(seen) == 1
    assert records(store, Scan) == before_scan
    assert records(store, Request) == [old]
    selected[0] = replace(selected[0], thinking_mode='high')
    for n in range(2, 12):
        run(store, bridge.make_interest_verifier(store.engine), n)
        if records(store, Scan)[0]['status'] == 'succeeded': break
    scan = records(store, Scan)[0]
    assert scan['status'] == 'succeeded'
    final = json.loads(scan['result_json'])
    assert final['outcome'] == ('mention' if first == 'mention' else 'core')
    assert final['verifier_policy']['thinking_mode'] == 'high'
    assert next(row for row in records(store, Request) if row['id'] == old['id']) == old
    with Session(store.engine) as session:
        assert len(service.current_matches(session, 'alice')) == (0 if first == 'mention' else 1)


@pytest.mark.parametrize('failure', [400, 429, 503, 307, 308, 'length', 'empty'])
def test_pro_http_errors_are_one_post_and_unknown_is_not_retried(store, monkeypatch, failure):
    seed(store, condition_ast=INTENT_AST)
    monkeypatch.setattr(bridge, 'resolve_llm_config', lambda _: pro())
    calls = []; original = httpx.AsyncClient
    def transport(request):
        wire = json.loads(request.content); calls.append(wire)
        assert wire['max_tokens'] == records(store, Request)[0]['output_token_limit'] == 8192
        if type(failure) is int:
            return httpx.Response(failure, headers={'Location': 'https://other.example/redirect'}, json={'error': 'fixture'})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}' if failure == 'length' else ''},
            'finish_reason': 'length' if failure == 'length' else 'stop'}], 'usage': {'completion_tokens': 8192}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    run(store, bridge.make_interest_verifier(store.engine))
    old = records(store, Request)
    assert len(calls) == 1 and old[0]['status'] == 'unknown'
    run(store, bridge.make_interest_verifier(store.engine), 10)
    assert len(calls) == 1 and records(store, Request) == old
