"""Durable paid semantic scans: real matcher paths, mocked provider transport."""
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config import LLMConfig
from models.db import ArticleRecord, ArticleAnalysisRecord, AppSettingRecord, SourceConfigRecord, UserRecord
from models.custom_interests import (
    InterestSemanticScanRecord as Scan, InterestSemanticRequestRecord as Request,
    InterestArticleMatchRecord as Match, InterestMatchJobRecord as Job,
    UserInterestConditionRecord as Condition,
)
from services import custom_interests as service
from services import interest_evidence_windows as windows
from services import interest_governance_llm as bridge
from storage.impl.db_storage import DatabaseStorage

NOW = None  # Assigned by store at test setup, never at collection time.
AST = {'all_of':[{'any_of':['robotics']},{'any_of':['planning']}], 'exclude':['sponsored']}
BODY = 'Machines compose multi-step routes, coordinate their actions, and revise their next moves.'


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Seed timestamps and explicit run(n) offsets share one per-test clock.
    # Collection/worker queue delays must not put new jobs after the first run.
    monkeypatch.setattr(bridge, "resolve_llm_config", lambda _: LLMConfig(base_url="https://example.com/v1", api_key="fixture", model="aux"))
    current = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setitem(globals(), 'NOW', current)
    monkeypatch.setattr(service, '_now', lambda: current)
    store = DatabaseStorage(db_url=f'sqlite:///{tmp_path / "semantic.db"}')
    with Session(store.engine) as session:
        for name in ('alice','bob'):
            session.add(UserRecord(username=name,password_hash='x',role='user',is_active=True,
                ai_beta_enabled=True,created_at=NOW.isoformat(),updated_at=NOW.isoformat()))
        session.add(AppSettingRecord(key='ai_beta_global_enabled',value='true'))
        session.add(SourceConfigRecord(source_id='rss_public',name='Controlled Research',source_type='rss',
            url='https://research.example/feed?private=never-export',category='tech',fetcher_id='generic_rss',
            created_at=NOW.isoformat(),updated_at=NOW.isoformat()))
        session.commit()
    yield store
    store.engine.dispose()


def seed(store, *, count=1, body=BODY, username='alice', title='Lab report', condition_ast=AST):
    with Session(store.engine) as session:
        condition = service.save_condition(session,username,service.ConditionInput(
            name='Research interest',condition=condition_ast))
        cid=condition.id; session.commit()
        for i in range(count):
            aid=f'a{i:02}'
            if session.get(ArticleRecord,aid):continue
            session.add(ArticleRecord(id=aid,title=title,source_id='rss_public',source_url='https://untrusted.example/article',
                content_type='rss_article',publish_date=(NOW-dt.timedelta(hours=1)).isoformat(),
                fetched_date=NOW.isoformat(),content=body,has_content=True))
            session.flush()
            session.add(ArticleAnalysisRecord(article_id=aid,status='succeeded',tagging_status='succeeded',
                analyzed_at=NOW.isoformat(),created_at=NOW.isoformat(),updated_at=NOW.isoformat()))
        session.commit()
    return cid


def answer(payload, outcome='core', *, normalized=True):
    evidence=[]
    if outcome in {'core','mention'}:
        w=payload['evidence_windows'][0]
        evidence=([{'kind':'text','group_indexes':[0,1],'span_id':w['spans'][0]['span_id']}]
            if payload.get('wire_evidence_format') == windows.WIRE_EVIDENCE_FORMAT else
            [{'kind':'text','group_indexes':[0,1],'window_id':w['id'],'quote':w['text'][:100]}])
    result = {'outcome':outcome,'scope':'main' if outcome=='core' else 'aside' if outcome=='mention' else 'uncertain',
            'reason':'Grounded test response','evidence':evidence,
            'missing_group_indexes':[] if outcome=='core' else [0,1]}
    return windows.validate_semantic_verdict(payload,result) if normalized else result


def trusted_fake(store, verifier):
    if verifier is None or hasattr(verifier, 'verifier_policy'):
        return verifier
    # Test-only adapter: no provider, same trusted server metadata contract.
    def adapted(*, username, payload, reserved_output_token_limit, expected_verifier_policy):
        assert reserved_output_token_limit == adapted.max_output_tokens
        assert expected_verifier_policy == adapted.verifier_policy
        return verifier(username=username, payload=payload)
    adapted.verifier_policy = bridge.verifier_policy(bridge._interest_config(store.engine))
    adapted.max_output_tokens = adapted.verifier_policy['max_output_tokens']
    adapted.available = getattr(verifier, 'available', lambda: True)
    return adapted


def run(store, verifier, n=0, **kw):
    verifier = trusted_fake(store, verifier)
    return service.run_match_cycle(store.engine,now=NOW+dt.timedelta(minutes=n+1),semantic_verifier=verifier,**kw)


def records(store, model):
    with Session(store.engine) as session:return [r.model_dump() for r in session.exec(select(model)).all()]


def test_zero_literal_hits_queue_and_full_body_exclusion_never_calls(store):
    seed(store,count=2)
    with Session(store.engine) as session:
        a=session.get(ArticleRecord,'a01');a.content='Unrelated context. '*2000+' sponsored';session.add(a)
        # A usable original body does not need an analysis bookkeeping row.
        session.delete(session.get(ArticleAnalysisRecord,'a00'));session.commit()
    calls=[]
    def verify(*,username,payload):calls.append(payload);return answer(payload)
    assert run(store,verify)['semantic_calls']==1
    assert len(calls)==1 and calls[0]['article']['id']=='a00'
    assert calls[0]['source']['host']=='research.example'
    assert 'never-export' not in json.dumps(calls)
    outcomes={r['article_id']:r['outcome'] for r in records(store,Match)}
    assert outcomes=={'a00':'core','a01':'no_match'}


def test_six_http_cap_deferred_seventh_and_unknown_not_recharged(store,monkeypatch):
    seed(store,count=7,title='Robotics planning')
    config=LLMConfig(base_url='https://example.com/v1',api_key='fixture',model='aux')
    monkeypatch.setattr(bridge,'_interest_config',lambda _:config)
    calls=[]; original=httpx.AsyncClient
    def transport(req):calls.append(req);return httpx.Response(400,json={'error':'response_format unsupported'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(transport),**kw))
    verifier=bridge.make_interest_verifier(store.engine)
    assert run(store,verifier,semantic_limit=999)['semantic_calls']==6
    assert len(calls)==6 and len(records(store,Request))==6
    assert all(r['outcome']=='unknown' for r in records(store,Match))
    assert len([r for r in records(store,Scan) if r['status']=='deferred'])==1
    assert run(store,verifier,1)['semantic_calls']==1
    assert len(calls)==7 and len(records(store,Request))==7
    run(store,verifier,10)
    assert len(calls)==7


def test_unserved_pair_precedes_partial_and_each_pair_gets_one_page(store):
    seed(store,count=7,body='Contextual document without literal groups. '*1500)
    calls=[]
    def verify(*,username,payload):calls.append(payload['article']['id']);return answer(payload,'no_match')
    assert run(store,verify)['semantic_calls']==6
    assert calls==[f'a{i:02}' for i in range(6)]
    assert all(r['outcome']=='unknown' for r in records(store,Match))
    assert run(store,verify,1)['semantic_calls']==6
    assert calls[6]=='a06' and len(set(calls[6:]))==6
    with Session(store.engine) as session:
        assert service.current_matches(session,'alice')==[]


@pytest.mark.parametrize('first_outcome,final_outcome',[('no_match','no_match'),('mention','mention')])
def test_paginated_negative_requires_full_durable_union(store,first_outcome,final_outcome):
    text='A coherent long research discussion. '*1800
    seed(store,body=text)
    seen=[]
    def verify(*,username,payload):
        seen.append(payload)
        return answer(payload,first_outcome if len(seen)==1 else 'no_match')
    run(store,verify)
    assert records(store,Scan)[0]['status']=='partial'
    assert records(store,Match)[0]['outcome']=='unknown'
    for n in range(1,8):
        run(store,verify,n)
        if records(store,Scan)[0]['status']=='succeeded':break
    scan=records(store,Scan)[0]
    assert scan['status']=='succeeded'
    assert json.loads(scan['reviewed_ranges_json'])==[[0,len(text)]]
    ids=[w['id'] for p in seen for w in p['evidence_windows']]
    assert len(set(ids))==len(ids)
    assert records(store,Match)[0]['outcome']==final_outcome
    assert scan['page_count']==len(seen)>1


def test_tail_core_can_finish_early_with_partial_coverage(store):
    marker='Machines compose multi-step routes and revise actions after feedback.'
    body=('Plain research context. '*3000)[:51585]
    body=body[:42000]+marker+body[42000:]
    seed(store,body=body)
    def verify(*,username,payload):
        for w in payload['evidence_windows']:
            selected = next((span for span in w['spans'] if 'Machines compose' in span['text']), None)
            if selected:
                result=answer(payload,normalized=False)
                result['evidence']=[{'kind':'text','group_indexes':[0,1],'span_id':selected['span_id']}]
                return windows.validate_semantic_verdict(payload,result)
        return answer(payload,'no_match')
    run(store,verify)
    match=records(store,Match)[0]
    assert match['outcome']=='core'
    evidence=json.loads(match['evidence_json'])
    assert not evidence['coverage']['complete'] and evidence['evidence'][0]['end']>42000


def test_missing_tail_cannot_be_declared_negative(store,monkeypatch):
    seed(store,body='Long unrelated document. '*3000)
    verifier=lambda **kw:answer(kw['payload'],'no_match')
    run(store,verifier)
    real=windows.build_evidence_page
    def gap(text,ast_dict,*,reviewed_chunk_ids=()):
        page=real(text,ast_dict,reviewed_chunk_ids=reviewed_chunk_ids)
        return {**page,'evidence_windows':[],'chunk_ids':[],
                'coverage':{**page['coverage'],'complete_after_this_page':True,'remaining_chunk_ids':[]}}
    monkeypatch.setattr(windows,'build_evidence_page',gap)
    run(store,verifier,1)
    scan=records(store,Scan)[0]
    assert scan['status']=='unknown' and scan['error_code']=='coverage_gap'
    assert records(store,Match)[0]['outcome']=='unknown' and len(records(store,Request))==1


def test_unknown_survives_analysis_matcher_contract_changes_but_new_body_can_run(store,monkeypatch):
    seed(store)
    calls=[]
    def verify(**kw):
        calls.append(kw['payload'])
        raise TimeoutError('provider outcome not known')
    run(store,verify)
    original=records(store,Request)[0]
    with Session(store.engine) as session:
        analysis=session.get(ArticleAnalysisRecord,'a00');analysis.updated_at='changed bookkeeping';session.add(analysis);session.commit()
    monkeypatch.setattr(service,'MATCHER_VERSION','new-matcher')
    monkeypatch.setattr(windows,'CONTRACT_VERSION','explicit-contract-v3')
    run(store,verify,10)
    assert len(calls)==1 and records(store,Request)[0]==original
    with Session(store.engine) as session:
        a=session.get(ArticleRecord,'a00');a.content+=' New substantive text.';session.add(a);session.commit()
    run(store,verify,20)
    assert len(calls)==2


def test_success_reuses_paid_content_after_analysis_or_model_config_churn(store,monkeypatch):
    seed(store)
    calls=[]
    def verify(**kw):calls.append(kw);return answer(kw['payload'])
    run(store,verify)
    oldfingerprint=records(store,Match)[0]['fingerprint']
    with Session(store.engine) as session:
        analysis=session.get(ArticleAnalysisRecord,'a00');analysis.updated_at='bookkeeping';session.add(analysis);session.commit()
    def changed_model(**kw):return verify(**kw)
    run(store,changed_model,10)
    assert len(calls)==1 and records(store,Match)[0]['fingerprint']!=oldfingerprint
    monkeypatch.setattr(windows,'CONTRACT_VERSION','operator-approved-contract-v3')
    run(store,changed_model,20)
    assert len(calls)==2


@pytest.mark.parametrize('after_http',[False,True])
def test_authorized_crash_before_or_after_http_never_retries(store,after_http):
    seed(store)
    class Crash(BaseException):pass
    submitted=[]
    def crash(**kw):
        if after_http:submitted.append(kw)
        raise Crash()
    with pytest.raises(Crash):run(store,crash)
    assert records(store,Request)[0]['status']=='authorized'
    called=[]
    run(store,lambda **kw:called.append(kw) or answer(kw['payload']),10)
    assert called==[] and len(submitted)==int(after_http)
    assert records(store,Request)[0]['status']=='unknown'
    assert records(store,Request)[0]['reserved_calls']==1
    assert records(store,Scan)[0]['status']=='unknown'


@pytest.mark.parametrize('mutation',['body','source','acl','condition'])
@pytest.mark.parametrize('outcome',['core','unknown'])
def test_stale_or_revoked_response_settles_only(store,mutation,outcome):
    cid=seed(store)
    def verify(**kw):
        with Session(store.engine) as session:
            if mutation=='body':
                row=session.get(ArticleRecord,'a00');row.content+=' Changed';session.add(row)
            elif mutation=='source':
                row=session.get(SourceConfigRecord,'rss_public');row.name='New Publisher';session.add(row)
            elif mutation=='acl':
                session.add(AppSettingRecord(key='reader_hidden_source_ids',value='["rss_public"]'))
            else:
                row=session.get(Condition,cid)
                service.save_condition(session,'alice',service.ConditionInput(name='Edited',condition=AST,expected_revision=row.revision),cid)
            session.commit()
        return answer(kw['payload'],outcome)
    run(store,verify)
    assert records(store,Request)[0]['status']=='succeeded'
    assert records(store,Scan)[0]['status']=='unknown'
    assert all(r['outcome']!='core' for r in records(store,Match))
    with Session(store.engine) as session:assert service.current_matches(session,'alice')==[]


def test_owner_isolation_and_paid_request_unique_constraint(store):
    seed(store);seed(store,username='bob')
    calls=[]
    def verify(**kw):calls.append(kw['username']);return answer(kw['payload'])
    run(store,verify);run(store,verify,1)
    assert calls==['alice','bob']
    requests=records(store,Request)
    assert len(requests)==2 and requests[0]['content_identity']!=requests[1]['content_identity']
    with Session(store.engine) as session:
        duplicate=Request(**{**requests[0],'id':'different-row-id'})
        session.add(duplicate)
        with pytest.raises(IntegrityError):session.commit()


def test_paused_condition_drops_backlog_and_denied_semantic_preserves_lexical(store):
    cid=seed(store,title='Robotics planning')
    verifier=lambda **kw:answer(kw['payload'])
    run(store,verifier,semantic_limit=0)
    assert records(store,Match)[0]['outcome']=='unknown'
    with Session(store.engine) as session:
        row=session.get(Condition,cid)
        service.save_condition(session,'alice',service.ConditionInput(name=row.name,condition=AST,status='paused',expected_revision=row.revision),cid)
        session.commit()
    assert records(store,Scan)[0]['status']=='superseded'
    assert run(store,verifier,1)['status']=='idle'
    assert not records(store,Request)
    with Session(store.engine) as session:
        row=session.get(Condition,cid)
        service.save_condition(session,'alice',service.ConditionInput(name=row.name,condition=AST,status='active',expected_revision=row.revision),cid)
        user=session.get(UserRecord,'alice');user.ai_beta_enabled=False;session.add(user);session.commit()
    calls=[]
    run(store,lambda **kw:calls.append(kw),2)
    assert calls==[] and records(store,Match)[0]['outcome']=='core'


def test_concurrent_worker_cannot_reauthorize_same_page(store):
    seed(store)
    entered,release=Event(),Event();calls=[]
    def verify(**kw):
        calls.append(kw);entered.set();assert release.wait(10)
        return answer(kw['payload'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(run,store,verify)
        assert entered.wait(10)
        second=run(store,verify)
        release.set();assert first.result()['semantic_calls']==1
    assert second['status']=='idle' and len(calls)==1 and len(records(store,Request))==1


def test_legacy_semantic_unknown_is_anchored_without_version_recharge(store,monkeypatch):
    cid=seed(store)
    with Session(store.engine) as session:
        session.add(Match(owner_username='alice',condition_id=cid,condition_revision=1,article_id='a00',
            fingerprint='old-incompatible-fingerprint',matcher_version='compound-title-v1',outcome='unknown',
            evidence_json=json.dumps({'method':'semantic','reason':'old timeout'}),updated_at=NOW.isoformat()))
        session.commit()
    calls=[]
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']))
    assert calls==[] and records(store,Scan)[0]['error_code']=='legacy_semantic_unknown'
    with Session(store.engine) as session:
        a=session.get(ArticleRecord,'a00');a.content+=' Changed body';session.add(a);session.commit()
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']),10)
    assert len(calls)==1


def test_operator_filter_only_narrows_and_seventh_is_eventually_served(store):
    cid=seed(store,count=9)
    other=seed(store,username='bob')
    selected={f'a{i:02}' for i in range(1,8)}
    calls=[]
    def verify(**kw):calls.append((kw['username'],kw['payload']['article']['id']));return answer(kw['payload'])
    assert run(store,verify,candidate_pairs={})['status']=='idle'
    assert not records(store,Scan)
    assert run(store,verify,candidate_pairs={cid:selected})['semantic_calls']==6
    assert run(store,verify,1,candidate_pairs={cid:selected})['semantic_calls']==1
    assert set(calls)=={('alice',aid) for aid in selected}
    assert not any(r['condition_id']==other for r in records(store,Scan))
    # Resuming normal scheduling restores lower IDs excluded by the cohort.
    for n in range(10,15):run(store,verify,n)
    assert ('alice','a00') in calls and ('alice','a08') in calls


def test_scoped_keyset_exhaustion_resets_for_unscoped_low_ids(store):
    cid=seed(store,count=3)
    calls=[]
    def verify(**kw):calls.append(kw['payload']['article']['id']);return answer(kw['payload'])
    run(store,verify,candidate_pairs={cid:{'a02'}},limit=1)
    assert calls==['a02']
    # The shared cursor is intentionally unchanged; normal exhaustion resets it.
    run(store,verify,1,limit=1)
    for n in range(10,14):run(store,verify,n,limit=1)
    assert set(calls)=={'a00','a01','a02'} and len(calls)==3


def test_analysis_change_during_response_is_cached_but_cas_not_published(store):
    seed(store)
    calls=[]
    def verify(**kw):
        calls.append(kw)
        with Session(store.engine) as session:
            row=session.get(ArticleAnalysisRecord,'a00');row.updated_at='concurrent bookkeeping';session.add(row);session.commit()
        return answer(kw['payload'])
    run(store,verify)
    assert records(store,Scan)[0]['status']=='succeeded'
    assert records(store,Match)[0]['outcome']=='unknown'
    run(store,verify,10)
    assert len(calls)==1 and records(store,Match)[0]['outcome']=='core'


@pytest.mark.parametrize('gate',['global','account','inactive'])
def test_read_time_semantic_results_hide_immediately_after_revocation(store,gate):
    seed(store)
    run(store,lambda **kw:answer(kw['payload']))
    with Session(store.engine) as session:
        assert len(service.current_matches(session,'alice'))==1
        if gate=='global':
            row=session.get(AppSettingRecord,'ai_beta_global_enabled');row.value='false'
        else:
            row=session.get(UserRecord,'alice')
            if gate=='account':row.ai_beta_enabled=False
            else:row.is_active=False
        session.add(row);session.commit()
        assert service.current_matches(session,'alice')==[]


@pytest.mark.parametrize('first_outcome', ['core', 'unknown'])
def test_wire_format_upgrade_does_not_recharge_legacy_success_or_unknown(store,monkeypatch,first_outcome):
    seed(store)
    convert = windows.to_span_evidence_payload
    monkeypatch.setattr(windows,'to_span_evidence_payload',lambda payload:payload)
    calls=[]
    def verify(**kw):calls.append(kw['payload']);return answer(kw['payload'],first_outcome)
    run(store,verify)
    original=records(store,Request)[0]
    assert 'wire_evidence_format' not in json.loads(original['payload_json'])
    monkeypatch.setattr(windows,'to_span_evidence_payload',convert)
    run(store,verify,10)
    assert len(calls)==1 and records(store,Request)==[original]


def test_wire_upgrade_continues_partial_without_repaying_completed_chunks(store,monkeypatch):
    seed(store,body='A long coherent article with independent evidence. '*1800)
    convert = windows.to_span_evidence_payload
    monkeypatch.setattr(windows,'to_span_evidence_payload',lambda payload:payload)
    calls=[]
    def verify(**kw):calls.append(kw['payload']);return answer(kw['payload'],'no_match')
    run(store,verify)
    original=records(store,Request)[0]
    old_ids={window['id'] for window in calls[0]['evidence_windows']}
    assert records(store,Scan)[0]['status']=='partial'
    monkeypatch.setattr(windows,'to_span_evidence_payload',convert)
    run(store,verify,1)
    assert calls[1]['wire_evidence_format']==windows.WIRE_EVIDENCE_FORMAT
    assert not old_ids & {window['id'] for window in calls[1]['evidence_windows']}
    assert next(row for row in records(store,Request) if row['id']==original['id'])==original


def test_persisted_canonical_payload_bytes_are_exact_actual_http_user_content(store,monkeypatch):
    cid=seed(store,body='So [robotics planning](https://example.com) is developed in the substantive article.')
    with Session(store.engine) as session:
        condition=session.get(Condition,cid)
        condition.name='播客专属误导名称'
        condition.description='只看播客的误导描述'
        session.add(condition);session.commit()
    config=LLMConfig(base_url='https://example.com/v1',api_key='fixture',model='aux')
    monkeypatch.setattr(bridge,'_interest_config',lambda _:config)
    actual=[]; client=httpx.AsyncClient
    def transport(req):
        user_content=json.loads(req.content)['messages'][-1]['content']
        actual.append(user_content)
        payload=json.loads(user_content)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer(payload,normalized=False))},'finish_reason':'stop'}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(transport),**kw))
    run(store,bridge.make_interest_verifier(store.engine))
    request=records(store,Request)[0]
    assert actual==[request['payload_json']]
    assert request['input_bytes']==len(actual[0].encode())
    assert windows.encode_evidence_payload(json.loads(actual[0]))==actual[0]
    assert json.loads(actual[0])['intent']=={'condition':AST}
    assert '误导' not in actual[0]
    assert records(store,Match)[0]['outcome']=='core'


def test_wire_byte_gate_uses_transformed_payload_before_hold(store,monkeypatch):
    seed(store)
    convert=windows.to_span_evidence_payload
    def oversized(payload):return {**convert(payload),'test_wire_overhead':'中'*50000}
    monkeypatch.setattr(windows,'to_span_evidence_payload',oversized)
    calls=[]
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']))
    assert calls==[] and records(store,Request)==[]


@pytest.mark.parametrize('outcomes', [
    ('unknown','no_match'), ('unknown','mention','no_match'),
    ('mention','unknown','no_match'), ('no_match','unknown'), ('unknown',),
])
def test_valid_ambiguity_pages_finish_unknown_and_cache_without_recharge(store,outcomes):
    body='A substantive research discussion with unresolved meaning. '*1200
    seed(store,body=body)
    seen=[]
    def verify(**kw):
        payload=kw['payload'];seen.append(payload)
        result=answer(payload,outcomes[min(len(seen)-1,len(outcomes)-1)])
        result['reason']=f'Page {len(seen)} remains ambiguous' if result['outcome']=='unknown' else 'Other page verdict'
        return result
    for n in range(12):
        run(store,verify,n)
        scan=records(store,Scan)[0]
        if scan['status']=='succeeded':break
        assert scan['status']=='partial'
        assert records(store,Match)[0]['outcome']=='unknown'
    assert scan['status']=='succeeded' and scan['page_count']==len(seen)>1
    assert json.loads(scan['reviewed_ranges_json'])==[[0,len(body)]]
    requests=records(store,Request)
    assert all(r['status']=='succeeded' and r['error_code']=='' for r in requests)
    unknown_requests=[r for r in requests if json.loads(r['result_json'])['outcome']=='unknown']
    result=json.loads(scan['result_json'])
    assert result['outcome']=='unknown' and result['ambiguous_page_count']==len(unknown_requests)
    assert {p['request_id'] for p in result['ambiguous_pages']}=={r['id'] for r in unknown_requests}
    assert result['reason'].startswith('Page ') and 'ambiguous' in result['reason']
    evidence=json.loads(records(store,Match)[0]['evidence_json'])
    assert evidence['coverage']['complete'] and evidence['outcome']=='unknown'
    assert evidence['reason']==result['reason']
    ids=[w['id'] for p in seen for w in p['evidence_windows']]
    assert len(ids)==len(set(ids))
    with Session(store.engine) as session:
        assert service.current_matches(session,'alice')==[]
        assert not service._unresolved(session,scan['content_identity'])
    calls=len(seen)
    run(store,verify,30)
    assert len(seen)==calls and records(store,Request)==requests
    # Existing local fallback may replace Match, never the paid cache. Restoring
    # the semantic path must recover the finished uncertainty with no HTTP.
    run(store,None,40)
    assert records(store,Request)==requests and records(store,Scan)==[scan]
    run(store,verify,50)
    assert len(seen)==calls and records(store,Match)[0]['outcome']=='unknown'


def test_valid_unknown_next_unseen_page_can_core(store):
    seed(store,body='A lengthy context with several distinct research sections. '*2000)
    seen=[]
    def verify(**kw):
        seen.append(kw['payload'])
        return answer(kw['payload'],'unknown' if len(seen)==1 else 'core')
    run(store,verify)
    first=records(store,Request)[0]
    assert first['status']=='succeeded'
    assert records(store,Scan)[0]['status']=='partial'
    assert records(store,Scan)[0]['page_count']==1
    run(store,verify,1)
    scan=records(store,Scan)[0]
    assert scan['status']=='succeeded' and scan['page_count']==2
    assert records(store,Match)[0]['outcome']=='core'
    assert not json.loads(records(store,Match)[0]['evidence_json'])['coverage']['complete']
    assert not ({w['id'] for w in seen[0]['evidence_windows']} & {w['id'] for w in seen[1]['evidence_windows']})
    run(store,verify,10)
    assert len(seen)==2
    assert next(r for r in records(store,Request) if r['id']==first['id'])==first


@pytest.mark.parametrize('failure',['transport','schema'])
def test_valid_unknown_then_failed_request_remains_paid_unresolved(store,failure):
    seed(store,body='Long independent source context. '*2500)
    calls=[]
    def verify(**kw):
        calls.append(kw)
        if len(calls)==1:return answer(kw['payload'],'unknown')
        if failure=='transport':raise TimeoutError('uncertain HTTP')
        return {'outcome':'not-a-valid-verdict'}
    run(store,verify);run(store,verify,1)
    requests=records(store,Request)
    assert [r['status'] for r in requests]==['succeeded','unknown']
    assert records(store,Scan)[0]['status']=='unknown'
    assert records(store,Scan)[0]['page_count']==1
    run(store,verify,10)
    assert len(calls)==2 and records(store,Request)==requests


def test_preexisting_model_unknown_rows_stay_immutable_and_blocked(store,monkeypatch):
    seed(store,body='Historical ambiguous paid source context. '*2000)
    run(store,lambda **kw:answer(kw['payload'],'unknown'))
    # Explicit old-state fixture: new settlement must never manufacture a
    # historical paid-unknown row or retrospectively convert one.
    with Session(store.engine) as session:
        request=session.exec(select(Request)).one()
        request.status='unknown';request.error_code='model_unknown'
        scan=session.exec(select(Scan)).one()
        scan.status='unknown';scan.error_code='model_unknown'
        scan.page_count=0;scan.completed_chunk_ids_json='[]';scan.reviewed_ranges_json='[]'
        session.add(request);session.add(scan);session.commit()
    original=records(store,Request)
    calls=[]
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']),10)
    monkeypatch.setattr(windows,'CONTRACT_VERSION','future-contract-for-fence-test')
    run(store,lambda **kw:calls.append(kw) or answer(kw['payload']),20)
    assert calls==[] and records(store,Request)==original
    assert all(r['status']=='unknown' for r in records(store,Scan))


def test_partial_semantic_unknown_preserves_fairness_and_six_hold_cap(store):
    seed(store,count=7,body='Extended independent research context. '*2000)
    calls=[]
    def verify(**kw):
        calls.append(kw['payload']['article']['id'])
        return answer(kw['payload'],'unknown')
    assert run(store,verify,semantic_limit=999)['semantic_calls']==6
    assert all(r['status']=='succeeded' for r in records(store,Request))
    assert run(store,verify,1,semantic_limit=999)['semantic_calls']==6
    assert calls[6]=='a06' and len(set(calls[6:]))==6
    assert all(r['status'] in {'partial','deferred'} for r in records(store,Scan))


def test_final_page_ambiguity_is_included_and_audit_reasons_are_bounded(store):
    seed(store,body='A lengthy document containing uncertain evidence. '*1700)
    calls=[]
    def verify(**kw):
        payload=kw['payload'];calls.append(payload)
        final=payload['coverage']['complete_after_this_page']
        result=answer(payload,'unknown' if final else 'mention')
        if final:result['reason']='uncertainty '*40
        return result
    for n in range(12):
        run(store,verify,n)
        if records(store,Scan)[0]['status']=='succeeded':break
    result=json.loads(records(store,Scan)[0]['result_json'])
    assert result['outcome']=='unknown' and result['ambiguous_page_count']==1
    assert len(result['reason'])==240
    assert len(result['ambiguous_pages'][0]['reason'])==240
    assert result['ambiguous_pages'][0]['request_id']==records(store,Request)[-1]['id']


def test_ambiguity_aggregation_is_scan_local_and_audit_pages_are_bounded(store):
    seed(store,body='A large body with ambiguous research claims. '*5500)
    seed(store,username='bob')
    calls={'alice':0,'bob':0}
    def verify(**kw):
        calls[kw['username']]+=1
        return answer(kw['payload'],'unknown' if kw['username']=='alice' else 'no_match')
    for n in range(35):
        run(store,verify,n)
        if len(records(store,Scan))==2 and all(s['status']=='succeeded' for s in records(store,Scan)):break
    scans={s['owner_username']:s for s in records(store,Scan)}
    result=json.loads(scans['alice']['result_json'])
    assert scans['alice']['status']=='succeeded' and scans['alice']['page_count']>8
    assert result['ambiguous_page_count']==calls['alice']
    assert len(result['ambiguous_pages'])==8
    assert json.loads(scans['bob']['result_json'])['outcome']=='no_match'
    assert calls['bob']==calls['alice']  # Same public body, independent owner scans.
    requests={r['id']:r for r in records(store,Request)}
    assert all(requests[p['request_id']]['scan_id']==scans['alice']['id'] for p in result['ambiguous_pages'])


def test_ui_fields_stay_in_paid_identity_but_not_model_intent(store):
    cid=seed(store)
    with Session(store.engine) as session:
        condition=session.get(Condition,cid);article=session.get(ArticleRecord,'a00')
        _,text,_=service.evidence_snapshot(session,article)
        source=service._source_metadata(session,article)
        before=service._paid_identity(condition,article,text,source)
        condition.description='Only the UI description changes'
        assert service._paid_identity(condition,article,text,source)!=before
        condition.description='';condition.name='A different UI name'
        assert service._paid_identity(condition,article,text,source)!=before


INTENT = {'version':1,'requirement':'Use the technology as a scientific tool, not as the research object.',
          'negative_examples':['Measuring carbon costs of the technology alone is outside this interest.']}
INTENT_AST = {**AST,'semantic_intent':INTENT}


def intent_answer(payload,outcome='core',relation='satisfied',negatives=None):
    raw=answer(payload,outcome,normalized=False)
    raw['intent_assessment']={'verdict':relation,'reason':'Grounded relation assessment',
        'evidence_span_ids':[payload['evidence_windows'][0]['spans'][0]['span_id']],
        'negative_example_indexes':negatives or []}
    return windows.validate_semantic_verdict(payload,raw)


@pytest.mark.parametrize('gate',['none','unavailable','expired','global','account','source'])
def test_intent_all_fallback_paths_never_promote_title_core(store,monkeypatch,gate):
    seed(store,title='Robotics planning',condition_ast=INTENT_AST)
    calls=[]
    def verifier(**kw):calls.append(kw);return intent_answer(kw['payload'])
    if gate=='none':verifier=None
    elif gate=='unavailable':verifier.available=lambda:False
    elif gate=='expired':
        config=LLMConfig(base_url='https://example.com',api_key='fixture',model='aux',
            paid_authorized_until=(NOW-dt.timedelta(hours=1)).isoformat())
        monkeypatch.setattr(bridge,'_interest_config',lambda _:config)
        verifier=bridge.make_interest_verifier(store.engine)
        async def forbidden(**kw):raise AssertionError('No HTTP while expired')
        monkeypatch.setattr(bridge,'chat_completion',forbidden)
    elif gate=='source':monkeypatch.setattr(service.user_sources,'external_ai_allowed_source_ids',lambda *args:[])
    else:
        with Session(store.engine) as session:
            if gate=='global':row=session.get(AppSettingRecord,'ai_beta_global_enabled');row.value='false'
            else:row=session.get(UserRecord,'alice');row.ai_beta_enabled=False
            session.add(row);session.commit()
    run(store,verifier)
    assert calls==[] and records(store,Request)==[]
    assert records(store,Match)[0]['outcome']=='unknown'
    assert json.loads(records(store,Match)[0]['evidence_json'])['reason']=='semantic_intent_requires_verification'
    with Session(store.engine) as session:assert service.current_matches(session,'alice')==[]


@pytest.mark.parametrize('first',['missing','counterexample','uncertain'])
def test_intent_ambiguous_or_counterexample_page_can_reach_later_core(store,first):
    seed(store,body='A lengthy discussion that contains multiple independent sections. '*1800,
        condition_ast=INTENT_AST)
    seen=[]
    def verify(**kw):
        p=kw['payload'];seen.append(p)
        if len(seen)>1:return intent_answer(p)
        if first=='missing':return answer(p)  # Missing assessment normalizes to page-unknown.
        if first=='counterexample':return intent_answer(p,'no_match','not_satisfied',[0])
        return intent_answer(p,'unknown','unknown')
    run(store,verify)
    assert records(store,Scan)[0]['status']=='partial'
    assert records(store,Request)[0]['status']=='succeeded'
    run(store,verify,1)
    assert records(store,Match)[0]['outcome']=='core'
    assert not ({w['id'] for w in seen[0]['evidence_windows']} & {w['id'] for w in seen[1]['evidence_windows']})
    with Session(store.engine) as session:assert len(service.current_matches(session,'alice'))==1


def test_complete_intent_ambiguity_cached_and_proof_required_at_read_time(store):
    seed(store,condition_ast=INTENT_AST)
    calls=[]
    def verify(**kw):calls.append(kw);return answer(kw['payload'])
    run(store,verify)
    assert records(store,Request)[0]['status']=='succeeded'
    assert records(store,Scan)[0]['status']=='succeeded'
    assert records(store,Match)[0]['outcome']=='unknown'
    run(store,verify,10)
    assert len(calls)==1
    with Session(store.engine) as session:
        match=session.exec(select(Match)).one();match.outcome='core'
        session.add(match);session.commit()
        assert service.current_matches(session,'alice')==[]


def test_intent_core_read_guard_fallback_cache_and_revision_invalidation(store):
    cid=seed(store,condition_ast=INTENT_AST)
    calls=[]
    def verify(**kw):calls.append(kw);return intent_answer(kw['payload'])
    run(store,verify)
    requests=records(store,Request)
    with Session(store.engine) as session:
        assert len(service.current_matches(session,'alice'))==1
        match=session.exec(select(Match)).one();proof=json.loads(match.evidence_json)
        for patch in ({'method':'deterministic_title'},{'semantic_intent_hash':'stale'},
                      {'intent_assessment':None},{'semantic_intent_version':0}):
            match.evidence_json=json.dumps({**proof,**patch});session.add(match);session.commit()
            assert service.current_matches(session,'alice')==[]
        match.evidence_json=json.dumps(proof);session.add(match);session.commit()
    run(store,None,10)
    assert records(store,Match)[0]['outcome']=='unknown' and records(store,Request)==requests
    run(store,verify,20)
    assert len(calls)==1 and records(store,Match)[0]['outcome']=='core'
    with Session(store.engine) as session:
        condition=session.get(Condition,cid)
        service.save_condition(session,'alice',service.ConditionInput(name=condition.name,
            condition={**AST,'semantic_intent':{**INTENT,'requirement':'A new explicitly chosen relationship.'}},
            expected_revision=condition.revision),cid)
        session.commit()
        assert service.current_matches(session,'alice')==[]
    run(store,verify,30)
    assert len(calls)==2 and records(store,Request)[0]==requests[0]
    assert records(store,Request)[0]['content_identity']!=records(store,Request)[1]['content_identity']


def test_intent_edit_supersedes_partial_and_fences_inflight_result(store):
    cid=seed(store,body='A long article with unfinished semantic verification. '*1800,condition_ast=INTENT_AST)
    run(store,lambda **kw:intent_answer(kw['payload'],'unknown','unknown'))
    old=records(store,Request)[0]
    with Session(store.engine) as session:
        condition=session.get(Condition,cid)
        service.save_condition(session,'alice',service.ConditionInput(name=condition.name,
            condition={**AST,'semantic_intent':{**INTENT,'requirement':'Changed relationship'}},expected_revision=1),cid)
        session.commit()
    assert records(store,Scan)[0]['status']=='superseded'
    assert records(store,Request)==[old]
    def edit_during_request(**kw):
        with Session(store.engine) as session:
            condition=session.get(Condition,cid)
            service.save_condition(session,'alice',service.ConditionInput(name=condition.name,
                condition={**AST,'semantic_intent':{**INTENT,'requirement':'Changed during request'}},expected_revision=2),cid)
            session.commit()
        return intent_answer(kw['payload'])
    run(store,edit_during_request,10)
    assert records(store,Request)[0]==old
    assert records(store,Scan)[-1]['error_code']=='stale_or_revoked_result'
    with Session(store.engine) as session:assert service.current_matches(session,'alice')==[]


def test_actual_intent_http_payload_and_prompt_keep_ui_fields_out(store,monkeypatch):
    cid=seed(store,condition_ast=INTENT_AST)
    with Session(store.engine) as session:
        row=session.get(Condition,cid);row.name='UI podcast only';row.description='UI secret extra restriction'
        session.add(row);session.commit()
    monkeypatch.setattr(bridge,'_interest_config',lambda _:LLMConfig(base_url='https://example.com/v1',api_key='fixture',model='aux'))
    wire=[];original=httpx.AsyncClient
    def transport(req):
        messages=json.loads(req.content)['messages'];wire.append(messages)
        p=json.loads(messages[-1]['content']);raw=answer(p,normalized=False)
        raw['intent_assessment']={'verdict':'satisfied','reason':'Explicit grounded relationship',
            'evidence_span_ids':[p['evidence_windows'][0]['spans'][0]['span_id']],'negative_example_indexes':[]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(raw)},'finish_reason':'stop'}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(transport),**kw))
    run(store,bridge.make_interest_verifier(store.engine))
    stored=records(store,Request)[0]
    assert wire[0][-1]['content']==stored['payload_json']
    assert json.loads(stored['payload_json'])['intent']=={'condition':INTENT_AST}
    assert 'UI podcast only' not in str(wire) and 'UI secret' not in str(wire)
    assert 'intent_assessment' in wire[0][0]['content'] and '不是关键词排除或整篇否决' in wire[0][0]['content']
    assert records(store,Match)[0]['outcome']=='core'
