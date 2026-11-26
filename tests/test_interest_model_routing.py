"""Interest-only routing: real durable matcher and HTTP serialization, no network."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from sqlmodel import Session

import config as config_module
from config import LLMConfig
from services import daily_brief, credentials
from services import custom_interests as service
from services import interest_governance_llm as bridge
from tests.test_custom_interest_semantic_scans import store, seed, records, run, INTENT_AST, Request, Scan
from tests.test_interest_verifier_policy import pro, provider


def dedicated():
    return replace(pro(), model='test-flash', thinking_mode='disabled',
                   interest_model='test-pro', interest_thinking_mode='high')


@pytest.mark.parametrize('model,aux,mode', [
    ('main', '', ''), ('main', '', 'disabled'), ('main', '', 'high'),
    ('main', 'main', 'high'), ('main', 'aux', 'high'), ('', 'aux', 'disabled'),
])
def test_empty_interest_overrides_exactly_preserve_legacy_route(model, aux, mode):
    cfg = LLMConfig(model=model, aux_model=aux, thinking_mode=mode)
    assert cfg.for_interest() == cfg.for_aux()
    assert cfg.for_interest().for_interest() == cfg.for_interest()
    assert cfg.model == model and cfg.aux_model == aux and cfg.thinking_mode == mode


@pytest.mark.parametrize('aux,override_model,override_mode,expected_model,expected_mode', [
    ('', 'pro', '', 'pro', 'disabled'),
    ('aux', 'pro', '', 'pro', ''),
    ('aux', '', 'high', 'aux', 'high'),
    ('aux', 'pro', 'disabled', 'pro', 'disabled'),
    ('aux', 'pro', ' HIGH ', 'pro', 'high'),
])
def test_partial_override_inherits_effective_route_and_is_idempotent(
        aux, override_model, override_mode, expected_model, expected_mode):
    cfg = LLMConfig(model='main', thinking_mode='disabled', aux_model=aux,
                    interest_model=override_model, interest_thinking_mode=override_mode)
    effective = cfg.for_interest()
    assert (effective.model, effective.thinking_mode) == (expected_model, expected_mode)
    assert effective.for_interest() == effective.for_aux() == effective
    assert cfg.model == 'main' and cfg.aux_model == aux


@pytest.mark.parametrize('fields', [
    {'interest_thinking_mode': 'omitted'}, {'interest_thinking_mode': 'unsupported'},
    {'interest_thinking_mode': None}, {'interest_model': 123},
])
def test_invalid_interest_overrides_fail_closed(fields):
    with pytest.raises(ValueError): LLMConfig(**fields)


def test_full_policy_equality_has_no_routing_origin():
    assert bridge.verifier_policy(pro()) == bridge.verifier_policy(dedicated())
    cfg = replace(dedicated(), aux_model='legacy-aux')
    assert bridge.verifier_policy(cfg) == bridge.verifier_policy(cfg.for_interest())
    assert bridge.verifier_policy(cfg) == bridge.verifier_policy(pro())


def test_ini_only_fields_survive_runtime_main_aux_overrides(store, monkeypatch, tmp_path):
    path = tmp_path / 'routing.ini'
    path.write_text('[llm]\nbase_url=https://example.com/v1\napi_key=fixture\n'
        'model=main\nthinking_mode=disabled\ninterest_model= test-pro \n'
        'interest_thinking_mode= HIGH \ninterest_max_output_tokens=8192\n')
    monkeypatch.setenv('DORAMI_CONFIG_FILE', str(path))
    base = config_module.load_config()
    monkeypatch.setattr(config_module, 'settings', base)
    # Exercise the normal credential namespace overlay without extending its fields.
    original = credentials.resolve_values
    def overrides(session, namespace, base):
        return {**original(session, namespace, base), 'model': 'runtime-main', 'aux_model': 'runtime-aux'}
    monkeypatch.setattr(credentials, 'resolve_values', overrides)
    with Session(store.engine) as session: cfg = daily_brief.resolve_llm_config(session)
    assert (cfg.model, cfg.for_aux().model, cfg.for_aux().thinking_mode) == ('runtime-main', 'runtime-aux', '')
    assert (cfg.for_interest().model, cfg.for_interest().thinking_mode) == ('test-pro', 'high')
    assert cfg.for_interest().interest_max_output_tokens == 8192


@pytest.mark.parametrize('outcome', ['core', 'transport_unknown'])
def test_route_migration_reuses_success_and_fences_unknown_without_http(store, monkeypatch, outcome):
    seed(store, condition_ast=INTENT_AST)
    def fail(_raw, _hold, _selected):
        if outcome == 'transport_unknown': raise httpx.ReadTimeout('fixture timeout')
    selected, seen = provider(store, monkeypatch, response_mutator=fail)
    run(store, bridge.make_interest_verifier(store.engine))
    old_requests = records(store, Request)
    old_scans = records(store, Scan)
    assert len(seen) == 1
    assert old_requests[0]['status'] == ('succeeded' if outcome == 'core' else 'unknown')
    selected[0] = dedicated()
    verifier = bridge.make_interest_verifier(store.engine)
    assert verifier.verifier_policy == bridge.verifier_policy(pro())
    run(store, verifier, 10)
    assert len(seen) == 1 and records(store, Request) == old_requests
    assert records(store, Scan) == old_scans
    with Session(store.engine) as session:
        assert len(service.current_matches(session, 'alice')) == (1 if outcome == 'core' else 0)


def test_dedicated_route_real_hold_and_http_match_8192_pro_high(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    _, seen = provider(store, monkeypatch, config=dedicated())
    run(store, bridge.make_interest_verifier(store.engine))
    wire = seen[0]['wire']
    assert wire['model'] == 'test-pro' and wire['max_tokens'] == 8192
    assert wire['thinking'] == {'type': 'enabled'} and wire['reasoning_effort'] == 'high'
    assert records(store, Request)[0]['status'] == 'succeeded'


@pytest.mark.parametrize('aux', ['', 'legacy-aux'])
def test_other_purposes_keep_original_model_effort_and_short_budgets(store, monkeypatch, aux):
    from services.article_analysis import analyze_article_with_llm
    from services.personal_digest_titles import _translate_batch
    from services.article_listen_guides import OpenAiCompatibleArticleListenTextProvider
    from tests.test_article_analysis import _analysis_input, _payload
    cfg = replace(dedicated(), aux_model=aux)
    monkeypatch.setattr(bridge, 'resolve_llm_config', lambda _: cfg)
    seen = []
    responses = [json.dumps({'relation': 'unknown'}), json.dumps({'status': 'unknown'}),
                 json.dumps(_payload()), '机器人的科研新进展', '这是文章的口播稿。']
    original = httpx.AsyncClient
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': responses[len(seen)-1]},
                                                     'finish_reason': 'stop'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    bridge.make_taxonomy_adviser(store.engine)({'evidence': 'fixture'})
    asyncio.run(bridge.make_retag_evaluator(store.engine)({'evidence': 'fixture'}))
    asyncio.run(analyze_article_with_llm(_analysis_input(), [], cfg))
    assert asyncio.run(_translate_batch({'article': 'Scientific advances'}, cfg,
                                       username='alice', budget_seconds=5))
    asyncio.run(OpenAiCompatibleArticleListenTextProvider(cfg).create_narration(title='Title', body='Body', max_chars=100))
    assert [wire['max_tokens'] for wire in seen] == [1200, 900, 4096, 512, 4000]
    assert [wire['model'] for wire in seen] == [aux or 'test-flash'] * 4 + ['test-flash']
    for index, wire in enumerate(seen):
        assert 'reasoning_effort' not in wire
        assert wire.get('thinking') == (None if aux and index < 3 else {'type': 'disabled'})
