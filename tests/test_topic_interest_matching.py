"""Topic meanings use original body proof without changing legacy rule semantics."""
import copy
import json

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from services import custom_interests as service
from services import interest_evidence_windows as windows
from services import interest_governance_llm as bridge
from tests.test_custom_interest_semantic_scans import store, seed, run, records, Match, Request, Condition

TOPIC = {'match_mode': 'topic', 'all_of': [], 'exclude': [], 'semantic_intent': {
    'version': 1, 'requirement': 'AI 产品的新功能、使用体验和实际工作流程。',
    'negative_examples': ['只提及公司融资、股价，未讨论产品功能或使用体验。']}}
BODY = '工作台为四种职业预置工具与数据连接器，实测展示报告生成、图像编辑和教案编写的操作流程、耗时及失败边界。'


def payload():
    return windows.to_span_evidence_payload({'intent': {'condition': TOPIC},
        'source': {'display_name': 'Publisher'}, **windows.build_evidence_page(BODY, TOPIC)})


def answer(data, outcome='core'):
    sid = data['evidence_windows'][0]['spans'][0]['span_id']
    return {'outcome': outcome, 'scope': 'main' if outcome == 'core' else 'aside',
        'reason': '正文实测产品工作流程', 'missing_group_indexes': [],
        'evidence': [{'kind': 'text', 'span_id': sid, 'group_indexes': []}],
        'intent_assessment': {'verdict': 'satisfied' if outcome in ('core', 'mention') else 'not_satisfied',
            'reason': '正文提供具体使用过程', 'evidence_span_ids': [sid], 'negative_example_indexes': []}}


def test_topic_needs_meaning_and_never_becomes_title_positive_without_verification():
    ast = service.ConditionAST.model_validate(TOPIC)
    assert service.evaluate(ast, 'AI 产品与交互', BODY)[0] == 'unknown'
    for patch in [{'semantic_intent': None}, {'all_of': [{'any_of': ['AI']}]}, {'match_mode': None}]:
        with pytest.raises(ValidationError): service.ConditionAST.model_validate({**TOPIC, **patch})
    old = service.ConditionAST(all_of=[{'any_of': ['AI']}])
    assert 'match_mode' not in old.model_dump(exclude_none=True)
    assert service.evaluate(old, 'AI 产品发布', BODY)[0] == 'mention'


def test_topic_span_proof_is_body_bound_and_repeatably_normalized():
    data = payload(); result = windows.validate_semantic_verdict(data, answer(data))
    assert result['outcome'] == 'core' and result['quotes'] == [BODY]
    assert windows.validate_semantic_verdict(data, result, normalized=True) == result


@pytest.mark.parametrize('change', ['source', 'fake_span', 'title_only', 'aside_core', 'group_index'])
def test_topic_rejects_ungrounded_or_incidental_core(change):
    data = payload(); verdict = answer(data)
    if change == 'source': verdict['evidence'] = [{'kind':'source','field':'display_name','group_indexes':[]}]
    elif change == 'fake_span': verdict['evidence'][0]['span_id'] = 'fabricated'
    elif change == 'title_only': verdict['evidence'] = []
    elif change == 'aside_core': verdict['scope'] = 'aside'
    else: verdict['evidence'][0]['group_indexes'] = [0]
    with pytest.raises(ValueError): windows.validate_semantic_verdict(data, verdict)


def test_topic_unknown_and_negative_examples_do_not_promote_to_core():
    data = payload(); verdict = answer(data)
    for change in [{'verdict': 'unknown'}, {'evidence_span_ids': []}, {'negative_example_indexes': [0]}]:
        changed = copy.deepcopy(verdict); changed['intent_assessment'].update(change)
        assert windows.validate_semantic_verdict(data, changed)['outcome'] == 'unknown'
    assert windows.validate_semantic_verdict(data, answer(data, 'mention'))['outcome'] == 'mention'
    assert windows.validate_semantic_verdict(data, answer(data, 'no_match'))['outcome'] == 'no_match'


def test_normal_worker_promotes_body_topic_with_zero_title_overlap_and_reuses_work(store):
    cid = seed(store, condition_ast=TOPIC, title='工作台体验记', body=BODY)
    calls = []
    def verify(**kw):
        calls.append(kw['payload'])
        return windows.validate_semantic_verdict(kw['payload'], answer(kw['payload']))
    assert run(store, verify)['semantic_calls'] == 1
    assert records(store, Match)[0]['outcome'] == 'core'
    with Session(store.engine) as s:
        assert s.get(Condition, cid).schema_version == 3
        assert len(service.current_matches(s, 'alice')) == 1
        assert service.current_matches(s, 'bob') == []
    before = records(store, Request)
    run(store, verify, n=10)
    assert len(calls) == 1 and records(store, Request) == before


def test_topic_edit_is_idempotent_and_older_editor_cannot_silently_downgrade(store):
    cid = seed(store, condition_ast=TOPIC, body=BODY)
    with Session(store.engine) as s:
        row=s.get(Condition,cid)
        service.save_condition(s,'alice',service.ConditionInput(name=row.name,condition=TOPIC,expected_revision=1),cid)
        assert row.revision == 1
        with pytest.raises(ValueError, match='编辑器'):
            service.save_condition(s,'alice',service.ConditionInput(name=row.name,
                condition={'all_of':[{'any_of':['AI']}]},expected_revision=1),cid)
        changed=service.save_condition(s,'alice',service.ConditionInput(name=row.name,
            condition={'match_mode':None,'all_of':[{'any_of':['AI']}],'semantic_intent':None},expected_revision=1),cid)
        assert changed.revision == 2 and changed.schema_version == 1
        assert 'match_mode' not in json.loads(changed.condition_json)


def test_topic_prompt_uses_meaning_and_preserves_the_rules_prompt():
    topic=bridge.build_interest_system_prompt(span_mode=True,semantic_intent=True,topic_mode=True)
    assert '无需出现固定关键词' in topic and 'group_indexes:[]' in topic
    rules=bridge.build_interest_system_prompt(span_mode=True,semantic_intent=True)
    assert '不能替代或放宽all_of' in rules


def test_a_substantial_related_section_is_not_an_article_level_core():
    data = payload()
    verdict = answer(data)
    verdict['scope'] = 'substantial_section'
    with pytest.raises(ValueError, match='core requires article main topic'):
        windows.validate_semantic_verdict(data, verdict)
    verdict['outcome'] = 'mention'
    assert windows.validate_semantic_verdict(data, verdict)['outcome'] == 'mention'


def test_title_overlap_cannot_prove_the_body_is_about_the_interest():
    ast = service.ConditionAST(all_of=[{'any_of': ['医疗']}])
    outcome, _ = service.evaluate(ast, '产品发布：医疗只是一个例子', '正文介绍通用办公工作台，医疗仅是场景列表中的一项。')
    assert outcome == 'mention'


@pytest.mark.parametrize('patch', [
    {'scope': 'substantial_section'}, {'method': 'deterministic_title'},
    {'verifier_policy': {}},
])
def test_current_read_rejects_legacy_or_section_only_core_without_rewriting_it(store, patch):
    cid = seed(store, condition_ast=TOPIC, title='工作台体验记', body=BODY)
    run(store, lambda **kw: windows.validate_semantic_verdict(kw['payload'], answer(kw['payload'])))
    before_requests = records(store, Request)
    with Session(store.engine) as s:
        assert len(service.current_matches(s, 'alice')) == 1
        row = s.get(Match, records(store, Match)[0]['id'])
        row.evidence_json = json.dumps({**json.loads(row.evidence_json), **patch})
        s.add(row); s.commit()
        saved = row.evidence_json
        assert service.current_matches(s, 'alice') == []
        s.refresh(row)
        assert row.evidence_json == saved
    assert records(store, Request) == before_requests
