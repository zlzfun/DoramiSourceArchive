"""Review transaction, privacy, fairness and real-wire protocol under MockTransport."""
import asyncio
import datetime as dt
import json
import os
import sys
from dataclasses import replace
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','src'))
import httpx
import pytest
from sqlalchemy import create_engine
from sqlmodel import Session,SQLModel,select
from models.db import AppSettingRecord,ArticleRecord,CmsTagCandidateRecord,CmsTagCandidateEvidenceRecord,CmsTagRecord,CmsTagAliasRecord
from models.taxonomy_review import ReviewProposalRecord,ReviewProposalRevisionRecord,ReviewNotificationOutboxRecord,ReviewNotificationBatchRecord,ReviewNotificationAttemptRecord,ReviewProposalMemberRecord
from services import taxonomy_review as review
from services.feishu_notifications import FeishuNotificationConfig
from services.source_visibility import set_source_hidden


@pytest.fixture
def env(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "review.db"}')
    SQLModel.metadata.create_all(engine)
    now=dt.datetime.now(dt.timezone.utc).replace(hour=10,minute=0,second=0,microsecond=0)
    with Session(engine) as session:
        yield engine,session,now
    engine.dispose()


def candidate(session,now,label='memory agents',source='public',index=1):
    c=CmsTagCandidateRecord(label=label,normalized_label=label,proposed_kind='topic',
        created_at=now.isoformat(),updated_at=now.isoformat(),first_seen_at=now.isoformat(),last_seen_at=now.isoformat())
    session.add(c);session.flush()
    article=ArticleRecord(id=f'a-{index}',title=label,content_type='article',source_id=source,
        source_url=f'https://example.com/article-{index}',content=label+' evidence',
        publish_date=now.isoformat(),fetched_date=now.isoformat())
    session.add(article);session.flush()
    session.add(CmsTagCandidateEvidenceRecord(candidate_id=c.id,article_id=article.id,source_id=source,
        source_owner_or_domain=source,published_date=now.isoformat(),confidence=.9,raw_label=label,created_at=now.isoformat()))
    session.commit();return c


def first(session):return session.exec(select(ReviewProposalRecord).order_by(ReviewProposalRecord.id)).first()


def decision(session,p,action,**kw):
    return review.decide_review_proposal(session,p.id,expected_revision=p.revision,
        expected_taxonomy_revision=0,action=action,actor_id='reviewer',reason='evidence reviewed',**kw)


@pytest.fixture
def config():
    return FeishuNotificationConfig(enabled=True,webhook_url='https://open.feishu.cn/open-apis/bot/v2/hook/fixture-hook-review-test',
        signing_secret='test-secret',review_base_url='https://dorami.example.com')


def test_refresh_idempotent_and_adviser_only_on_change(env):
    _,s,now=env;candidate(s,now);calls=[]
    def advise(p):
        calls.append(p);return {'relation':'new','reason':'distinct meaning','adviser_version':'taxonomy-adviser-v1'}
    assert review.refresh_review_proposals(s,now=now,adviser=advise)['created']==1
    assert review.refresh_review_proposals(s,now=now,adviser=advise)['unchanged']==1
    assert len(calls)==1 and calls[0]['evidence']['samples'][0]['title']=='memory agents'
    assert len(s.exec(select(ReviewNotificationOutboxRecord)).all())==1
    assert first(s).revision==1


def test_unavailable_adviser_does_not_hide_proposals_or_spam(env):
    _,s,now=env;candidate(s,now)
    def fail(_):raise RuntimeError('private provider detail')
    assert review.refresh_review_proposals(s,now=now,adviser=fail)['created']==1
    review.refresh_review_proposals(s,now=now,adviser=fail)
    assert first(s).revision==1
    assert 'private provider' not in first(s).model_dump_json()


def test_cursor_covers_private_prefix_and_caps_adviser_calls(env):
    _,s,now=env
    for i in range(1,8):candidate(s,now,f'label {i}',source='hidden' if i<5 else 'public',index=i)
    set_source_hidden(s,'hidden',True);calls=[]
    for _ in range(4):review.refresh_review_proposals(s,limit=2,now=now,adviser=lambda p:(calls.append(p) or {'relation':'new'}),adviser_limit=1)
    assert len(s.exec(select(ReviewProposalRecord)).all())==3
    assert len(calls)<=4


def test_approval_is_draft_and_duplicate_decision_conflicts(env):
    _,s,now=env;c=candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s);pid=p.id
    approved=decision(s,p,'approve',now=now)
    assert approved.status=='approved'
    assert s.get(CmsTagRecord,approved.draft_tag_id).status=='draft'
    assert s.get(CmsTagCandidateRecord,c.id).status=='reviewing'
    with pytest.raises(review.ReviewConflict):review.decide_review_proposal(s,pid,expected_revision=1,expected_taxonomy_revision=0,action='approve',actor_id='other',reason='twice',now=now)
    assert len(s.exec(select(CmsTagRecord)).all())==1
    assert s.exec(select(ReviewNotificationOutboxRecord)).first().status=='cancelled'


def test_failed_merge_rolls_back_revision_and_outbox(env):
    _,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s)
    with pytest.raises(ValueError):decision(s,p,'merge',target_tag_id=999,now=now)
    assert first(s).revision==1 and first(s).status=='pending'
    assert s.exec(select(ReviewNotificationOutboxRecord)).first().status=='pending'


def test_taxonomy_conflict_rolls_back_all_changes(env):
    _,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s)
    s.add(AppSettingRecord(key='taxonomy:sync_revision',value='9'));s.commit()
    with pytest.raises(review.ReviewConflict):decision(s,p,'reject',now=now)
    assert first(s).revision==1


def test_merge_creates_alias_once_and_reject_stays_rejected(env):
    _,s,now=env;c=candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s)
    t=CmsTagRecord(code='long-term-memory',kind='topic',name_en='Long term memory',normalized_name='long term memory',status='active',created_at=now.isoformat(),updated_at=now.isoformat())
    s.add(t);s.commit();decision(s,p,'merge',target_tag_id=t.id,now=now)
    assert s.get(CmsTagCandidateRecord,c.id).status=='merged'
    assert len(s.exec(select(CmsTagAliasRecord)).all())==1
    c2=candidate(s,now,'other concept',index=2);review.refresh_review_proposals(s,now=now)
    p2=s.exec(select(ReviewProposalRecord).where(ReviewProposalRecord.identity==f'candidate:{c2.id}')).first()
    decision(s,p2,'reject',now=now);review.refresh_review_proposals(s,now=now+dt.timedelta(days=1))
    assert s.get(ReviewProposalRecord,p2.id).status=='rejected'


def test_defer_silent_until_due_or_new_evidence(env):
    _,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s)
    decision(s,p,'defer',defer_days=3,now=now)
    review.refresh_review_proposals(s,now=now+dt.timedelta(days=1));assert first(s).status=='deferred'
    review.refresh_review_proposals(s,now=now+dt.timedelta(days=4));assert first(s).status=='pending'


def test_group_split_membership_and_old_tombstones(env):
    _,s,now=env
    for i in (1,2):candidate(s,now,f'concept {i}',index=i)
    review.refresh_review_proposals(s,now=now)
    parents=s.exec(select(ReviewProposalRecord)).all()
    group=review.group_review_proposals(s,[{'id':p.id,'revision':p.revision} for p in parents],expected_taxonomy_revision=0,actor_id='admin',reason='same concept',now=now)
    assert len(json.loads(group.member_ids_json))==2
    decision(s,group,'split',split_candidate_ids=[1],now=now)
    assert len({m.proposal_id for m in s.exec(select(ReviewProposalMemberRecord)).all()})==2
    review.refresh_review_proposals(s,now=now)
    assert all(s.get(ReviewProposalRecord,p.id).status=='split' for p in parents)


def test_daily_once_and_next_day_only_new_revision(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);calls=[]
    transport=httpx.MockTransport(lambda req:(calls.append(req) or httpx.Response(200,json={'code':0})))
    assert asyncio.run(review.send_daily_review_digest(engine,config,now=now,transport=transport))['status']=='accepted'
    assert asyncio.run(review.send_daily_review_digest(engine,config,now=now+dt.timedelta(minutes=1),transport=transport))['status']=='accepted'
    assert asyncio.run(review.send_daily_review_digest(engine,config,now=now+dt.timedelta(days=1),transport=transport))['status']=='empty'
    assert len(calls)==1
    assert b'/review/taxonomy/1' in calls[0].content


def test_timeout_never_retries_even_next_day(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);calls=[]
    def timeout(req):calls.append(req);raise httpx.ReadTimeout('uncertain',request=req)
    transport=httpx.MockTransport(timeout)
    for delta in (0,10,1440):asyncio.run(review.send_daily_review_digest(engine,config,now=now+dt.timedelta(minutes=delta),transport=transport))
    assert len(calls)==1
    s.expire_all();assert s.exec(select(ReviewNotificationOutboxRecord)).first().status=='unknown'


def test_failed_bounded_retry_then_dead_letter(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);calls=[]
    transport=httpx.MockTransport(lambda req:(calls.append(req) or httpx.Response(429,json={'code':1})))
    for delta in (0,1,5,15,40,1440):asyncio.run(review.send_daily_review_digest(engine,config,now=now+dt.timedelta(minutes=delta),transport=transport))
    assert len(calls)==3
    s.expire_all();assert s.exec(select(ReviewNotificationBatchRecord)).first().attempt_count==3


def test_current_acl_blocks_old_outbox_and_read_projection(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now);p=first(s)
    set_source_hidden(s,'public',True)
    assert review.proposal_payload(s,p,now=now)['evidence']['articles']==[]
    def forbidden(_):raise AssertionError('must not send')
    assert asyncio.run(review.send_daily_review_digest(engine,config,now=now,transport=httpx.MockTransport(forbidden)))['status']=='empty'


def test_before_schedule_and_disabled_no_requests(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now)
    assert asyncio.run(review.send_daily_review_digest(engine,config,now=now-dt.timedelta(hours=1)))['status']=='skipped'
    assert asyncio.run(review.send_daily_review_digest(engine,replace(config,enabled=False),now=now))['status']=='skipped'


def test_slow_adviser_permission_change_is_rechecked_without_write_lock(env):
    engine,s,now=env;candidate(s,now)
    def adviser(_):
        with Session(engine) as other:set_source_hidden(other,'public',True)
        return {'relation':'new','reason':'stale suggestion'}
    result=review.refresh_review_proposals(s,now=now,adviser=adviser)
    assert result['failed']==1
    assert s.exec(select(ReviewProposalRecord)).first() is None


def test_adviser_cannot_auto_activate_or_reference_invented_target(env):
    _,s,now=env;candidate(s,now)
    review.refresh_review_proposals(s,now=now,adviser=lambda _: {'relation':'equivalent','target_tag_id':999,'reason':'untrusted'})
    assert first(s).relation=='unknown' and first(s).target_tag_id is None
    assert s.exec(select(CmsTagRecord)).first() is None


def test_parallel_digest_claim_sends_once(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now)
    async def run():
        started=asyncio.Event();release=asyncio.Event();calls=[]
        async def handler(req):calls.append(req);started.set();await release.wait();return httpx.Response(200,json={'code':0})
        transport=httpx.MockTransport(handler)
        task=asyncio.create_task(review.send_daily_review_digest(engine,config,now=now,transport=transport))
        await started.wait()
        second=await review.send_daily_review_digest(engine,config,now=now,transport=transport)
        release.set();first_result=await task
        assert second['status']=='sending' and first_result['status']=='accepted'
        assert len(calls)==1
    asyncio.run(run())


def test_interrupted_prior_day_send_becomes_unknown_without_resend(env,config):
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now)
    row=s.exec(select(ReviewNotificationOutboxRecord)).first();row.status='sending';row.batch_id='interrupted';s.add(row)
    old=now-dt.timedelta(days=1)
    s.add(ReviewNotificationBatchRecord(id='interrupted',digest_date=old.date().isoformat(),destination_hash=review._hash(config.webhook_url),status='sending',attempt_count=1,
        outbox_ids_json=json.dumps([row.id]),lease_token='old',lease_expires_at=(old+dt.timedelta(minutes=5)).isoformat(),created_at=old.isoformat(),updated_at=old.isoformat()))
    s.add(ReviewNotificationAttemptRecord(batch_id='interrupted',attempt=1,status='sending',started_at=old.isoformat()));s.commit()
    assert asyncio.run(review.send_daily_review_digest(engine,config,now=now))['status']=='empty'
    s.expire_all();assert s.get(ReviewNotificationBatchRecord,'interrupted').status=='unknown'
    assert s.get(ReviewNotificationAttemptRecord,('interrupted',1)).error_code=='interrupted_send'


def test_http_routes_require_admin_and_get_does_not_mutate(env,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import deps
    from api.routers.taxonomy_review import router
    engine,s,now=env;candidate(s,now);review.refresh_review_proposals(s,now=now)
    app=FastAPI();app.include_router(router)
    def session_dep():
        with Session(engine) as scoped:yield scoped
    app.dependency_overrides[deps.get_session]=session_dep
    who={'role':'user','sub':'reader'}
    monkeypatch.setattr(deps,'get_current_session',lambda _request:who)
    with TestClient(app) as client:
        assert client.get('/api/admin/taxonomy/review-proposals').status_code==403
        who['role']='admin'
        assert client.get('/api/admin/taxonomy/review-proposals/1').status_code==200
        assert client.get('/api/admin/taxonomy/review-proposals/1/decide').status_code==405
    s.expire_all();assert first(s).revision==1 and first(s).status=='pending'


def test_group_and_split_keep_revision_and_outbox_stable_on_unchanged_refresh(env):
    _, s, now = env
    for i in (1, 2):
        candidate(s, now, f'stable concept {i}', index=i)
    review.refresh_review_proposals(s, now=now)
    parents = s.exec(select(ReviewProposalRecord)).all()
    group = review.group_review_proposals(s, [{'id': p.id, 'revision': p.revision} for p in parents],
        expected_taxonomy_revision=0, actor_id='admin', reason='reviewed same concept', name='Reviewed group', now=now)
    before = len(s.exec(select(ReviewNotificationOutboxRecord)).all())
    result = review.refresh_review_proposals(s, now=now + dt.timedelta(minutes=5))
    assert result['updated'] == 0 and result['unchanged'] == 1
    s.refresh(group)
    assert group.revision == 1 and group.canonical_name == 'Reviewed group'
    assert len(s.exec(select(ReviewNotificationOutboxRecord)).all()) == before
    decision(s, group, 'split', split_candidate_ids=[1], now=now + dt.timedelta(minutes=6))
    children = s.exec(select(ReviewProposalRecord).where(ReviewProposalRecord.status == 'pending')).all()
    assert len(children) == 2
    before = len(s.exec(select(ReviewNotificationOutboxRecord)).all())
    result = review.refresh_review_proposals(s, now=now + dt.timedelta(minutes=10))
    assert result['updated'] == 0 and result['unchanged'] == 2
    assert all(s.get(ReviewProposalRecord, child.id).revision == 1 for child in children)
    assert len(s.exec(select(ReviewNotificationOutboxRecord)).all()) == before


@pytest.mark.parametrize('conflict_type', ['canonical', 'alias'])
def test_merge_alias_conflict_returns_named_409_and_rolls_back_group(env, monkeypatch, conflict_type):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import deps
    from api.routers.taxonomy_review import router
    from models.db import CmsTagEventRecord

    engine, s, _ = env
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    for i, label in enumerate(('new alias', 'conflicting alias'), start=1):
        candidate(s, now, label, index=i)
    review.refresh_review_proposals(s, now=now)
    parents = s.exec(select(ReviewProposalRecord)).all()
    group = review.group_review_proposals(s, [{'id': p.id, 'revision': p.revision} for p in parents],
        expected_taxonomy_revision=0, actor_id='admin', reason='reviewed same concept', now=now)
    target = CmsTagRecord(code='target', kind='topic', name_en='Target', normalized_name='target', status='active',
        created_at=now.isoformat(), updated_at=now.isoformat())
    conflict_name = 'Conflicting Alias' if conflict_type == 'canonical' else 'Conflicting Formal Tag'
    conflict = CmsTagRecord(code='conflict', kind='topic', name_en=conflict_name,
        normalized_name='conflicting alias' if conflict_type == 'canonical' else 'conflicting formal tag', status='active',
        created_at=now.isoformat(), updated_at=now.isoformat())
    s.add(target); s.add(conflict); s.flush()
    if conflict_type == 'alias':
        s.add(CmsTagAliasRecord(tag_id=conflict.id, kind='topic', alias='conflicting alias', normalized_alias='conflicting alias',
            created_at=now.isoformat(), updated_at=now.isoformat()))
    s.commit()
    before_aliases = len(s.exec(select(CmsTagAliasRecord)).all())
    before_events = len(s.exec(select(CmsTagEventRecord)).all())
    before_revisions = len(s.exec(select(ReviewProposalRevisionRecord)).all())
    before_outbox = [(r.id, r.status) for r in s.exec(select(ReviewNotificationOutboxRecord)).all()]
    app = FastAPI(); app.include_router(router)
    def session_dep():
        with Session(engine) as scoped:
            yield scoped
    app.dependency_overrides[deps.get_session] = session_dep
    monkeypatch.setattr(deps, 'get_current_session', lambda _request: {'role': 'admin', 'sub': 'admin'})
    with TestClient(app) as client:
        response = client.post(f'/api/admin/taxonomy/review-proposals/{group.id}/decide', json={
            'expected_revision': group.revision, 'expected_taxonomy_revision': 0, 'action': 'merge',
            'reason': 'reviewed both', 'target_tag_id': target.id})
    assert response.status_code == 409
    assert conflict_name in response.json()['detail']
    assert '刷新' in response.json()['detail']
    s.expire_all()
    assert s.get(ReviewProposalRecord, group.id).revision == 1
    assert s.get(ReviewProposalRecord, group.id).status == 'pending'
    assert all(c.status == 'candidate' for c in s.exec(select(CmsTagCandidateRecord)).all())
    assert len(s.exec(select(CmsTagAliasRecord)).all()) == before_aliases
    assert len(s.exec(select(CmsTagEventRecord)).all()) == before_events
    assert len(s.exec(select(ReviewProposalRevisionRecord)).all()) == before_revisions
    assert [(r.id, r.status) for r in s.exec(select(ReviewNotificationOutboxRecord)).all()] == before_outbox
