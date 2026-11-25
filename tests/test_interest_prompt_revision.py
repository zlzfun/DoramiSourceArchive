"""Prompt provenance never changes a paid identity or unlocks old pages."""
import json
from sqlmodel import Session
from services import interest_governance_llm as bridge
from tests.test_custom_interest_semantic_scans import store, seed, answer, run, records, trusted_fake, Scan, Request, Match, service
from tests.test_interest_verifier_policy import INTENT_AST, provider


def legacy(policy):
    return {k: (1 if k == 'version' else v) for k,v in policy.items() if k not in {'prompt_version','prompt_sha256'}}


def test_prompt_variants_bind_hash_but_do_not_add_relations_to_plain_ast(store, monkeypatch):
    cfg = bridge._interest_config(store.engine)
    p = bridge.verifier_policy(cfg)
    assert p['version'] == 2 and bridge.valid_verifier_policy(p)
    assert bridge.valid_verifier_policy(legacy(p)) and legacy(p) != p
    for span in (True, False):
        plain = bridge.build_interest_system_prompt(span_mode=span, semantic_intent=False)
        relation = bridge.build_interest_system_prompt(span_mode=span, semantic_intent=True)
        assert '只有用户明示要求实际部署' not in plain
        for text in ['同一主体', '两个公司的不同案例', '假设场景', '成本或融资压力', '不作整篇否决', '上线验证']:
            assert text in relation
    original = bridge.build_interest_system_prompt
    monkeypatch.setattr(bridge, 'build_interest_system_prompt', lambda **kw: original(**kw) + 'changed')
    assert bridge.verifier_policy(cfg)['prompt_sha256'] != p['prompt_sha256']


def test_partial_prior_policy_pages_block_without_history_rewrite_or_http(store):
    seed(store, body='Real system context and details. ' * 1700)
    calls=[]
    verifier = trusted_fake(store, lambda **kw: calls.append(kw) or answer(kw['payload'], 'no_match'))
    verifier.verifier_policy = legacy(verifier.verifier_policy)
    run(store, verifier)
    assert len(calls) == 1 and records(store, Scan)[0]['status'] == 'partial'
    before_requests, before_scans = records(store, Request), records(store, Scan)
    result = run(store, lambda **kw: calls.append(kw) or answer(kw['payload']), 10)
    assert result['semantic_calls'] == 0 and result['deferred_policy_scans'] == 1
    assert result['error_code'] == 'verifier_policy_changed'
    assert records(store, Request) == before_requests
    assert records(store, Scan) == before_scans
    assert len(calls) == 1


def test_all_current_policy_pages_can_complete(store):
    seed(store, body='Real system context and details. ' * 1700)
    calls=[]
    verifier=lambda **kw: calls.append(kw) or answer(kw['payload'], 'no_match')
    for n in range(8):
        run(store, verifier, n*10)
        if records(store, Scan)[0]['status'] == 'succeeded':break
    assert len(calls)>1 and records(store, Scan)[0]['status']=='succeeded'
    assert json.loads(records(store, Scan)[0]['result_json'])['outcome']=='no_match'
    assert all(json.loads(r['result_json'])['authorization']['verifier_policy']['version']==2 for r in records(store,Request))


def test_policy_upgrade_never_reissues_completed_or_unknown_work(store):
    seed(store)
    calls=[]
    verifier=trusted_fake(store,lambda **kw:calls.append(kw) or answer(kw['payload']))
    verifier.verifier_policy=legacy(verifier.verifier_policy)
    run(store, verifier)
    before=records(store,Request)
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']),10)
    assert len(calls)==1 and records(store,Request)==before


def test_legacy_intent_core_hidden_without_relabel_or_recharge(store, monkeypatch):
    seed(store, condition_ast=INTENT_AST)
    _, seen = provider(store, monkeypatch)
    run(store, bridge.make_interest_verifier(store.engine))
    with Session(store.engine) as session:
        assert len(service.current_matches(session, 'alice')) == 1
        for model, field in [(Request, 'result_json'), (Scan, 'result_json'), (Match, 'evidence_json')]:
            row=session.get(model, records(store,model)[0]['id'])
            value=json.loads(getattr(row,field))
            value['verifier_policy']=legacy(value['verifier_policy'])
            if 'authorization' in value:
                value['authorization']['verifier_policy']=legacy(value['authorization']['verifier_policy'])
            setattr(row,field,json.dumps(value));session.add(row)
        session.commit()
        assert service.current_matches(session,'alice') == []
    before=records(store,Request)
    run(store,bridge.make_interest_verifier(store.engine),10)
    assert records(store,Request)==before and len(seen)==1
    assert json.loads(records(store,Scan)[0]['result_json'])['verifier_policy']['version']==1


def test_prompt_drift_after_factory_creation_blocks_before_post(store, monkeypatch):
    seed(store)
    _, seen = provider(store, monkeypatch)
    verifier=bridge.make_interest_verifier(store.engine)
    monkeypatch.setattr(bridge,'SEMANTIC_PROMPT_VERSION','future-revision')
    assert verifier.available() is False
    run(store,verifier)
    assert seen==[] and records(store,Request)==[]


def test_plain_rule_old_prompt_core_is_hidden_without_recharge(store):
    seed(store)
    calls=[]
    verifier=trusted_fake(store,lambda **kw:calls.append(kw) or answer(kw['payload']))
    old_policy={**verifier.verifier_policy,'prompt_version':'interest-relation-v3-topic','prompt_sha256':'a'*64}
    verifier.verifier_policy=old_policy
    run(store,verifier)
    before=records(store,Request)
    assert records(store,Match)[0]['outcome']=='core'
    with Session(store.engine) as session:
        assert service.current_matches(session,'alice')==[]
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']),10)
    assert records(store,Request)==before and len(calls)==1


def test_all_prompt_modes_require_article_level_centrality():
    variants=[bridge.build_interest_system_prompt(span_mode=span,semantic_intent=intent)
        for span in (False,True) for intent in (False,True)]
    variants.append(bridge.build_interest_system_prompt(span_mode=True,semantic_intent=True,topic_mode=True))
    for prompt in variants:
        assert '核心观点或关键事件' in prompt
        assert 'main指整篇文章而非当前页' in prompt
        assert '删除相关片段后文章主旨仍' in prompt
        assert 'scope须main或substantial_section' not in prompt
