"""AI confidence is advice, guarded by evidence/revision and explicit opt-in."""
from dataclasses import replace
import json
import pytest
from sqlmodel import Session, select
from models.db import ArticleRecord, CmsTagRecord
from models.taxonomy_review import ReviewProposalRevisionRecord
from services import taxonomy_ai_review as ai, taxonomy_review as review
from tests.test_taxonomy_review import env, candidate, first


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    config = replace(ai.settings, taxonomy=replace(ai.settings.taxonomy, mode='manual', review_ai_enabled=True))
    monkeypatch.setattr(ai, 'settings', config)
    monkeypatch.setattr(review, 'settings', config)


def answer(confidence=.81, **extra):
    return dict(canonical_name='Memory agents', kind='topic', definition='Persistent memory agents',
                relation='new', target_tag_id=None, reason='Evidence supports a distinct concept',
                risks=[], confidence=confidence, **extra)


def run(env, response, mutate=None):
    engine, s, now = env
    candidate(s, now)
    review.refresh_review_proposals(s, now=now)
    p = first(s)
    pid, rev = p.id, p.revision
    s.rollback()
    calls=[]
    def adviser(payload):
        calls.append(payload)
        if mutate: mutate(engine, pid)
        return response
    result=ai.run_ai_review(engine, pid, expected_revision=rev, expected_taxonomy_revision=0,
                            actor_id='admin', adviser=adviser, now=now)
    s.expire_all()
    return result,calls


@pytest.mark.parametrize('confidence,expected', [(.79,'pending'),(.8,'pending'),(.80001,'approved'),(.95,'approved'),
    (None,'pending'),('0.99','pending'),(True,'pending'),(float('nan'),'pending'),(float('inf'),'pending'),(1.1,'pending')])
def test_strict_threshold_and_invalid_numbers(env, confidence, expected):
    result,calls=run(env, answer(confidence))
    assert result['status']==expected and len(calls)==1
    _,s,_=env
    tags=s.exec(select(CmsTagRecord)).all()
    assert len(tags)==(expected=='approved')
    if tags: assert tags[0].status=='draft' and not tags[0].user_selectable
    audit=s.exec(select(ReviewProposalRevisionRecord)).all()
    assert any(r.action=='ai_review_started' for r in audit)
    assert any(r.action=='ai_review_completed' for r in audit)


@pytest.mark.parametrize('change', [{'relation':'unknown'},{'relation':'equivalent'}, {'risks':['ambiguous']},
                                   {'definition':''},{'target_tag_id':123}, {'kind':'invalid'}])
def test_unresolved_or_non_new_advice_cannot_auto_approve(env, change):
    value=answer(.99);value.update(change)
    result,_=run(env,value)
    assert result['status']=='pending'


def test_body_change_during_provider_io_does_not_apply(env):
    def mutate(engine,pid):
        with Session(engine) as s:
            a=s.get(ArticleRecord,'a-1');a.content+=' changed body';s.add(a);s.commit()
    result,_=run(env,answer(),mutate)
    assert result['ai_review']['state']=='stale' and result['status']=='pending'


def test_repeat_does_not_call_provider_and_unknown_does_not_retry(env):
    result,_=run(env,None)
    engine,s,now=env;p=first(s);rev=p.revision;s.rollback()
    with pytest.raises(review.ReviewConflict,match='已发起'):
        ai.run_ai_review(engine,p.id,expected_revision=rev,expected_taxonomy_revision=0,
                         actor_id='admin',adviser=lambda _:pytest.fail('must not call'),now=now)
    s.expire_all()
    assert first(s).revision==rev
    assert result['ai_review']['state']=='unknown'


def test_concurrent_human_decision_wins(env):
    def mutate(engine,pid):
        with Session(engine) as s:
            p=s.get(review.ReviewProposalRecord,pid)
            review.decide_review_proposal(s,pid,expected_revision=p.revision,expected_taxonomy_revision=0,
                actor_id='human',action='reject',reason='Human review')
    with pytest.raises(review.ReviewConflict,match='提案已变化'):
        run(env,answer(),mutate)
    env[1].expire_all()
    assert first(env[1]).status=='rejected'


def test_closed_switch_and_replica_fail_before_provider(env,monkeypatch):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s);s.rollback()
    for changes in [{'review_ai_enabled':False},{'mode':'replica','review_ai_enabled':True}]:
        monkeypatch.setattr(ai,'settings',replace(ai.settings,taxonomy=replace(ai.settings.taxonomy,**changes)))
        with pytest.raises(review.ReviewConflict):
            ai.run_ai_review(engine,p.id,expected_revision=1,expected_taxonomy_revision=0,
                actor_id='admin',adviser=lambda _:pytest.fail('must not call'),now=now)


def test_revoked_source_during_review_blocks_approval(env):
    from services.source_visibility import set_source_hidden
    def mutate(engine,pid):
        with Session(engine) as s: set_source_hidden(s,'public',True)
    result,_=run(env,answer(.99),mutate)
    assert result['ai_review']['state']=='stale'
    assert result['status']=='pending'


def test_duplicate_name_leaves_reviewable_result(env):
    engine,s,now=env
    s.add(CmsTagRecord(code='existing',kind='topic',name_zh='Memory agents',normalized_name='memory agents',
                      status='draft',created_at=now.isoformat(),updated_at=now.isoformat()))
    s.commit()
    result,_=run(env,answer(.99))
    assert result['status']=='pending' and result['ai_review']['state']=='conflict'


def test_duplicate_request_while_provider_running_cannot_send(env):
    def mutate(engine,pid):
        with pytest.raises(review.ReviewConflict):
            ai.run_ai_review(engine,pid,expected_revision=2,expected_taxonomy_revision=0,
                actor_id='second',adviser=lambda _:pytest.fail('duplicate provider call'))
    result,calls=run(env,answer(),mutate)
    assert len(calls)==1 and result['status']=='approved'


def test_provider_exception_keeps_private_error_out_of_audit(env):
    def fail(engine,pid): raise RuntimeError('private-key-provider-error')
    result,_=run(env,answer(),fail)
    assert result['ai_review']['state']=='unknown'
    assert 'private-key' not in json.dumps(result)


def test_ai_route_requires_admin_and_switch(env):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from api import deps
    from api.routers.taxonomy_review import router
    app=FastAPI();app.include_router(router)
    def deny(): raise HTTPException(403,'admin required')
    app.dependency_overrides[deps.require_admin]=deny
    with TestClient(app) as client:
        response=client.post('/api/admin/taxonomy/review-proposals/1/ai-review',json={
            'expected_revision':1,'expected_taxonomy_revision':0})
    assert response.status_code==403


def test_admin_endpoint_launches_durable_job_and_applies_draft(env,monkeypatch):
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import deps
    from api.routers.taxonomy_review import router
    from services import jobs, interest_governance_llm
    import datetime as dt
    engine,s,_=env;now=dt.datetime.now(dt.timezone.utc)
    candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s);pid=p.id;s.rollback()
    app=FastAPI();app.include_router(router)
    def session_dep():
        with Session(engine) as scoped: yield scoped
    app.dependency_overrides[deps.get_session]=session_dep
    app.dependency_overrides[deps.require_admin]=lambda:{'role':'admin','sub':'admin'}
    calls=[]
    def factory(engine,**kw):
        assert kw=={'limit':1,'auto_review':True}
        return lambda payload:(calls.append(payload) or answer(.95))
    monkeypatch.setattr(interest_governance_llm,'make_taxonomy_adviser',factory)
    with TestClient(app) as client:
        response=client.post(f'/api/admin/taxonomy/review-proposals/{pid}/ai-review',json={
            'expected_revision':1,'expected_taxonomy_revision':0})
        assert response.status_code==200,response.text
        for _ in range(100):
            job=jobs.get_job(engine,response.json()['job_id'])
            if job['status'] in {'failed','succeeded'}:break
            time.sleep(.01)
        assert job['status']=='succeeded',job
        assert job['result']['status']=='approved'
    assert len(calls)==1
    s.expire_all();assert first(s).status=='approved'


def test_proposal_refresh_cannot_erase_unknown_claim(env):
    run(env,None)
    _,s,now=env
    review.refresh_review_proposals(s,now=now,adviser=lambda _: {'relation':'new','reason':'new advice'})
    assert json.loads(first(s).evidence_json)['ai_review']['state']=='unknown'


def test_adviser_has_explicit_confidence_contract_and_no_transport_retry(monkeypatch):
    from types import SimpleNamespace
    from services import interest_governance_llm as bridge
    calls=[]
    monkeypatch.setattr(bridge,'_config',lambda _:SimpleNamespace(model='test-aux'))
    async def completion(**kw):
        calls.append(kw);return json.dumps(answer())
    monkeypatch.setattr(bridge,'chat_completion',completion)
    adviser=bridge.make_taxonomy_adviser(None,limit=1,auto_review=True)
    result=adviser({'label':'memory agents','evidence':{}})
    assert result['model']=='test-aux' and result['adviser_version']=='taxonomy-auto-review-v1'
    assert calls[0]['max_retries']==0
    assert 'confidence' in calls[0]['messages'][0].content
    with pytest.raises(RuntimeError,match='limit'):adviser({})
    assert len(calls)==1
