"""Private fixed-publication batches; real matcher, isolated DB, fake verifier."""
import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from config import LLMConfig
from models.db import ArticleRecord, ArticleAnalysisRecord, AppSettingRecord, SourceConfigRecord, UserRecord
from models.custom_interests import (
    UserInterestConditionRecord as Condition, InterestMatchJobRecord as Job,
    InterestArticleMatchRecord as Match, InterestSemanticScanRecord as Scan,
    InterestSemanticRequestRecord as Request,
)
from services import custom_interests as service
from services import interest_evidence_windows as windows
from services import interest_governance_llm as bridge
from services.interest_match_scope import (
    OperatorMatchEligibilityScope as Scope, MatchScopeGrant as Grant,
    MatchScopeError, database_binding,
)
from storage.impl.db_storage import DatabaseStorage

AST = {'all_of': [{'any_of': ['robotics']}, {'any_of': ['planning']}], 'exclude': ['sponsored']}
BODY = 'Machines compose multi-step routes and revise plans from observations.'


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    tick = {'seconds': 0.0}
    monkeypatch.setattr(service, '_now', lambda: now)
    monkeypatch.setattr(service, 'time', SimpleNamespace(monotonic=lambda: tick['seconds']))
    store = DatabaseStorage(db_url=f'sqlite:///{tmp_path / "scope.db"}')
    with Session(store.engine) as session:
        for name in ('alice', 'bob'):
            session.add(UserRecord(username=name, password_hash='fixture', role='user', is_active=True,
                ai_beta_enabled=True, created_at=now.isoformat(), updated_at=now.isoformat()))
        session.add(AppSettingRecord(key='ai_beta_global_enabled', value='true'))
        session.add(SourceConfigRecord(source_id='rss_public', name='Research', source_type='rss',
            url='https://research.example/feed', fetcher_id='generic_rss', created_at=now.isoformat(), updated_at=now.isoformat()))
        session.commit()
    yield SimpleNamespace(store=store, now=now, tick=tick)
    store.engine.dispose()


def seed(env, count=1, body=BODY, username='alice'):
    with Session(env.store.engine) as session:
        condition = service.save_condition(session, username, service.ConditionInput(name='Research', condition=AST))
        cid = condition.id
        session.commit()
        for n in range(count):
            aid = f'a{n:03}'
            if session.get(ArticleRecord, aid):
                continue
            session.add(ArticleRecord(id=aid, title='Lab report', source_id='rss_public',
                source_url='https://research.example/article', content_type='rss_article',
                publish_date=(env.now - dt.timedelta(days=7, hours=12)).isoformat(),
                fetched_date=env.now.isoformat(), content=body, has_content=True))
            session.flush()
            session.add(ArticleAnalysisRecord(article_id=aid, status='succeeded', tagging_status='succeeded',
                analyzed_at=env.now.isoformat(), created_at=env.now.isoformat(), updated_at=env.now.isoformat()))
        job = session.get(Job, cid)
        job.cursor = 'zzzz'
        job.cycle_cutoff_at = (env.now - dt.timedelta(days=30)).isoformat()
        job.next_attempt_at = (env.now + dt.timedelta(days=2)).isoformat()
        job.last_error = 'normal-workflow-note'
        session.add(job); session.commit()
    return cid


def scope_for(env, cid, ids=None, **overrides):
    with Session(env.store.engine) as session:
        condition = session.get(Condition, cid)
        rows = session.exec(select(ArticleRecord).order_by(ArticleRecord.id)).all()
        grants = []
        for row in rows:
            if ids is not None and row.id not in ids:
                continue
            _, text, _ = service.evidence_snapshot(session, row)
            grants.append(Grant(cid, row.id, condition.owner_username, condition.revision,
                row.source_id, row.publish_date,
                service._paid_identity(condition, row, text, service._source_metadata(session, row))))
    raw = Scope('0' * 64, database_binding(env.store.engine), env.now - dt.timedelta(seconds=1),
        env.now + dt.timedelta(hours=2), env.now - dt.timedelta(days=8), env.now - dt.timedelta(days=1), tuple(grants))
    raw = replace(raw, **overrides)
    return replace(raw, manifest_sha256=raw.manifest_digest())


def fake_verifier(callback=None, outcome='core'):
    calls = []
    def verify(**kw):
        payload = kw['payload']; calls.append(payload)
        if callback: callback(payload)
        evidence = []
        if outcome in ('core', 'mention'):
            evidence = [{'kind': 'text', 'group_indexes': [0, 1],
                         'span_id': payload['evidence_windows'][0]['spans'][0]['span_id']}]
        return windows.validate_semantic_verdict(payload, {
            'outcome': outcome, 'scope': 'main' if outcome == 'core' else 'uncertain',
            'reason': 'Grounded fixture', 'evidence': evidence,
            'missing_group_indexes': [] if outcome == 'core' else [0, 1]})
    verify.verifier_policy = bridge.verifier_policy(LLMConfig(
        base_url='https://example.com/v1', api_key='fixture', model='aux'))
    verify.max_output_tokens = verify.verifier_policy['max_output_tokens']
    verify.calls = calls
    return verify


def run(env, cid, scope, verifier=None, ids=None, **kwargs):
    ids = ids if ids is not None else [g.article_id for g in scope.grants if g.condition_id == cid]
    return service.run_match_cycle(env.store.engine, candidate_pairs={cid: ids},
        eligibility_scope=scope, semantic_verifier=verifier, **kwargs)


def rows(env, model):
    with Session(env.store.engine) as session:
        return [r.model_dump() for r in session.exec(select(model)).all()]


def job_state(env, cid):
    with Session(env.store.engine) as session:
        return session.get(Job, cid).model_dump()


@pytest.mark.parametrize('semantic', [True, False])
def test_fixed_window_ignores_old_cursor_cutoff_due_without_mutating_them(env, semantic):
    cid = seed(env, 2); scope = scope_for(env, cid); original = job_state(env, cid)
    verify = fake_verifier() if semantic else None
    result = run(env, cid, scope, verify)
    assert result['discovery_complete'] and set(result['items']) == {'a000', 'a001'}
    assert result['batch_terminal']
    assert job_state(env, cid) == original
    assert len(rows(env, Match)) == 2
    if semantic:
        assert len(verify.calls) == 2 and len(rows(env, Request)) == 2
        assert all(r['authorized_at'] >= env.now.isoformat() for r in rows(env, Request))
    else:
        assert not rows(env, Request)


@pytest.mark.parametrize('kind', ['now', 'multi', 'duplicate', 'oversize', 'limit', 'empty', 'not_granted'])
def test_invalid_batch_fails_before_lease_or_scan(env, kind):
    cid = seed(env, 201); scope = scope_for(env, cid, ['a000']); before = job_state(env, cid)
    pairs, kwargs = {cid: ['a000']}, {}
    if kind == 'now': kwargs['now'] = env.now - dt.timedelta(days=1)
    elif kind == 'multi': pairs[cid + 1] = ['a000']
    elif kind == 'duplicate': pairs[cid] = ['a000', 'a000']
    elif kind == 'oversize': pairs[cid] = [f'a{n:03}' for n in range(201)]
    elif kind == 'limit': kwargs['limit'] = 0
    elif kind == 'empty': pairs[cid] = []
    else: pairs[cid] = ['a001']
    with pytest.raises(MatchScopeError):
        service.run_match_cycle(env.store.engine, eligibility_scope=scope, candidate_pairs=pairs, **kwargs)
    assert job_state(env, cid) == before and not rows(env, Scan) and not rows(env, Request)


@pytest.mark.parametrize('kind', ['manifest', 'db', 'expired', 'future', 'ttl', 'window', 'owner', 'revision', 'source', 'identity', 'publish'])
def test_invalid_authority_scope_and_grant_fail_before_write(env, kind):
    cid = seed(env); scope = scope_for(env, cid); original = job_state(env, cid)
    if kind == 'manifest': scope = replace(scope, manifest_sha256='f' * 64)
    elif kind == 'db': scope = replace(scope, database_binding='f' * 64)
    elif kind == 'expired': scope = replace(scope, expires_at=env.now)
    elif kind == 'future': scope = replace(scope, issued_at=env.now + dt.timedelta(seconds=1))
    elif kind == 'ttl': scope = replace(scope, expires_at=env.now + dt.timedelta(hours=7))
    elif kind == 'window': scope = replace(scope, publish_after=env.now - dt.timedelta(days=9))
    else:
        grant = scope.grants[0]
        change = {'owner': {'owner_username': 'bob'}, 'revision': {'condition_revision': 2},
                  'source': {'source_id': 'other'}, 'identity': {'paid_content_identity': 'f' * 64},
                  'publish': {'publish_date': env.now.isoformat()}}[kind]
        scope = replace(scope, grants=(replace(grant, **change),))
    if kind != 'manifest': scope = replace(scope, manifest_sha256=scope.manifest_digest())
    with pytest.raises(MatchScopeError): run(env, cid, scope, fake_verifier())
    assert job_state(env, cid) == original and not rows(env, Scan) and not rows(env, Request)


def test_competing_lease_is_not_stolen_or_restored(env):
    cid = seed(env); scope = scope_for(env, cid)
    with Session(env.store.engine) as session:
        job = session.get(Job, cid); job.lease_token = 'other-worker'
        job.lease_expires_at = (env.now + dt.timedelta(minutes=3)).isoformat()
        session.add(job); session.commit()
    before = job_state(env, cid)
    assert run(env, cid, scope, fake_verifier())['status'] == 'busy'
    assert job_state(env, cid) == before and not rows(env, Request)


def test_exactly200_partial_long_body_does_not_starve(env):
    cid = seed(env, 200, body='Long unrelated context. ' * 1800)
    scope = scope_for(env, cid); verify = fake_verifier(outcome='no_match'); before = job_state(env, cid)
    # Seed one reviewed page for every pair through the real scope path. A 200
    # item discovery must still admit partials when all page_count values > 0.
    for _ in range(34):
        result = run(env, cid, scope, verify)
    scan_rows = rows(env, Scan)
    assert len(scan_rows) == 200 and min(r['page_count'] for r in scan_rows) >= 1
    calls = len(verify.calls)
    result = run(env, cid, scope, verify)
    assert len(verify.calls) > calls
    assert result['discovery_complete'] and len(result['items']) == 200
    assert max(r['page_count'] for r in rows(env, Scan)) >= 2
    for _ in range(100):
        if result['batch_terminal']:
            break
        result = run(env, cid, scope, verify)
    assert result['batch_terminal']
    assert all(row['status'] == 'succeeded' for row in rows(env, Scan))
    assert all(json.loads(row['reviewed_ranges_json']) == [[0, row['full_chars']]] for row in rows(env, Scan))
    assert job_state(env, cid) == before


def test_reuse_success_after_analysis_churn_and_restart_does_not_pay(env):
    cid = seed(env); scope = scope_for(env, cid); verify = fake_verifier()
    run(env, cid, scope, verify); old = rows(env, Request)
    with Session(env.store.engine) as session:
        row = session.get(ArticleAnalysisRecord, 'a000'); row.updated_at = 'changed bookkeeping'
        session.add(row); session.commit()
    result = run(env, cid, scope, verify)
    assert len(verify.calls) == 1 and rows(env, Request) == old and result['batch_terminal']
    assert result['items']['a000']['status'] == 'reused_verified'
    with Session(env.store.engine) as session:
        article = session.get(ArticleRecord, 'a000'); match = session.exec(select(Match)).one()
        assert match.fingerprint == service.bound_evidence_snapshot(session, article, json.loads(match.evidence_json))[0]


def test_unknown_stays_fenced_and_changed_input_cannot_wash_scope(env):
    cid = seed(env); scope = scope_for(env, cid)
    def timeout(_payload): raise TimeoutError('unknown fixture')
    verify = fake_verifier(timeout)
    run(env, cid, scope, verify); old = rows(env, Request)
    result = run(env, cid, scope, verify)
    assert len(verify.calls) == 1 and rows(env, Request) == old
    assert result['items']['a000']['status'] == 'unknown'
    with Session(env.store.engine) as session:
        row = session.get(ArticleRecord, 'a000'); row.content += ' actual change'
        session.add(row); session.commit()
    with pytest.raises(MatchScopeError, match='scope_input_changed'):
        run(env, cid, scope, verify)
    assert rows(env, Request) == old


def test_expiry_between_pages_stops_next_post_preserves_job(env):
    cid = seed(env, 2); scope = scope_for(env, cid, expires_at=env.now + dt.timedelta(seconds=2))
    before = job_state(env, cid)
    verify = fake_verifier(lambda _: env.tick.update(seconds=3.0))
    result = run(env, cid, scope, verify)
    assert len(verify.calls) == 1 and len(rows(env, Request)) == 1
    assert result['status'] == 'expired' and job_state(env, cid) == before
    assert result['items']['a001']['status'] == 'expired_unprocessed'
    # A call submitted while valid may settle successfully after scope expiry.
    assert rows(env, Request)[0]['status'] == 'succeeded'


def test_expiry_after_hold_before_invoke_retains_unknown(env, monkeypatch):
    cid = seed(env); scope = scope_for(env, cid, expires_at=env.now + dt.timedelta(seconds=2))
    before = job_state(env, cid); original = service._authorize_page
    def authorize(*args, **kwargs):
        attempt = original(*args, **kwargs)
        env.tick['seconds'] = 3.0
        return attempt
    monkeypatch.setattr(service, '_authorize_page', authorize)
    verify = fake_verifier(); result = run(env, cid, scope, verify)
    assert not verify.calls and result['verifier_invocations'] == 0
    request = rows(env, Request)[0]
    assert request['status'] == 'unknown' and request['error_code'] == 'scope_expired_preinvoke'
    assert job_state(env, cid) == before


def test_exception_only_releases_own_lease_and_never_sweeps_other_scans(env, monkeypatch):
    cid = seed(env); other = seed(env, username='bob'); scope = scope_for(env, cid)
    with Session(env.store.engine) as session:
        condition = session.get(Condition, other); condition.status = 'paused'; session.add(condition)
        session.add(Scan(id='untouched', content_identity='other', contract_version='old',
            owner_username='bob', condition_id=other, condition_revision=1, article_id='a000',
            projection_hash='p', source_metadata_hash='s', full_chars=1,
            created_at=env.now.isoformat(), updated_at=env.now.isoformat()))
        session.commit()
    before = job_state(env, cid); original = rows(env, Scan)
    monkeypatch.setattr(service, '_ensure_scan', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('fixture')))
    assert run(env, cid, scope, fake_verifier())['status'] == 'failed'
    assert job_state(env, cid) == before and rows(env, Scan) == original


@pytest.mark.parametrize('mutation', ['ai', 'matching', 'lease', 'body'])
def test_current_grant_and_permission_rechecked_after_hold(env, monkeypatch, mutation):
    cid = seed(env); scope = scope_for(env, cid); before = job_state(env, cid)
    original = service._authorize_page
    def authorize(*args, **kwargs):
        attempt = original(*args, **kwargs)
        with Session(env.store.engine) as session:
            if mutation == 'ai':
                record = session.get(UserRecord, 'alice'); record.ai_beta_enabled = False
            elif mutation == 'matching':
                record = AppSettingRecord(key=service.MATCHING_ENABLED_KEY, value='false')
            elif mutation == 'lease':
                record = session.get(Job, cid); record.lease_token = 'replacement-worker'
            else:
                record = session.get(ArticleRecord, 'a000'); record.content += ' changed input'
            session.add(record); session.commit()
        return attempt
    monkeypatch.setattr(service, '_authorize_page', authorize)
    verify = fake_verifier(); run(env, cid, scope, verify)
    assert not verify.calls
    assert rows(env, Request)[0]['status'] == 'unknown'
    after = job_state(env, cid)
    if mutation == 'lease':
        assert after['lease_token'] == 'replacement-worker'  # Do not erase another owner's lease.
        assert {k:v for k,v in after.items() if not k.startswith('lease_')} == {k:v for k,v in before.items() if not k.startswith('lease_')}
    else: assert after == before


def test_lexical_expiry_before_commit_writes_no_matches(env, monkeypatch):
    cid = seed(env); scope = scope_for(env, cid, expires_at=env.now + dt.timedelta(seconds=2))
    before = job_state(env, cid); original = service.evaluate
    def evaluate(*args, **kwargs):
        result = original(*args, **kwargs); env.tick['seconds'] = 3.0; return result
    monkeypatch.setattr(service, 'evaluate', evaluate)
    result = run(env, cid, scope)
    assert result['status'] == 'expired' and not rows(env, Match) and not rows(env, Request)
    assert job_state(env, cid) == before


def test_legacy_semantic_unknown_remains_fenced(env):
    cid = seed(env); scope = scope_for(env, cid)
    with Session(env.store.engine) as session:
        row = session.get(ArticleRecord, 'a000')
        session.add(Match(owner_username='alice', condition_id=cid, article_id=row.id,
            condition_revision=1, fingerprint=service.evidence_snapshot(session, row)[0],
            matcher_version=service.MATCHER_VERSION, outcome='unknown',
            evidence_json=json.dumps({'method':'semantic'}), updated_at=env.now.isoformat()))
        session.commit()
    verify = fake_verifier(); result = run(env, cid, scope, verify)
    assert not verify.calls and not rows(env, Request)
    assert rows(env, Scan)[0]['error_code'] == 'legacy_semantic_unknown'
    assert result['items']['a000']['status'] == 'unknown'


@pytest.mark.parametrize('position,seconds,allowed', [('lower',-1,False),('lower',0,True),('upper',0,True),('upper',1,False)])
def test_publication_inclusive_exact_boundaries(env, position, seconds, allowed):
    cid = seed(env)
    bound = env.now - dt.timedelta(days=8 if position == 'lower' else 1)
    with Session(env.store.engine) as session:
        article = session.get(ArticleRecord,'a000'); article.publish_date = (bound+dt.timedelta(seconds=seconds)).isoformat()
        session.add(article);session.commit()
    scope = scope_for(env,cid);verify=fake_verifier()
    if allowed:
        assert run(env,cid,scope,verify)['batch_terminal'] and len(verify.calls)==1
    else:
        with pytest.raises(MatchScopeError,match='scope_publication_changed'):run(env,cid,scope,verify)
        assert not rows(env,Request)


def test_partial_page_progress_is_preserved_when_scope_expires(env, monkeypatch):
    cid=seed(env,body='Long original evidence. '*1800)
    scope=scope_for(env,cid,expires_at=env.now+dt.timedelta(seconds=2));verify=fake_verifier(outcome='no_match')
    first=run(env,cid,scope,verify)
    assert not first['batch_terminal'] and rows(env,Scan)[0]['status']=='partial'
    requests_before=rows(env,Request);scan_before=rows(env,Scan)
    env.tick['seconds']=3.0
    # Entry checks use the real wall-clock source, independently from the prior cycle.
    # The scope is now past its actual boundary on the next invocation.
    monkeypatch.setattr(service, '_now', lambda: env.now+dt.timedelta(seconds=3))
    with pytest.raises(MatchScopeError,match='scope_expired'):run(env,cid,scope,verify)
    assert rows(env,Request)==requests_before and rows(env,Scan)==scan_before


@pytest.mark.parametrize('kind', ['other_article', 'old_identity'])
def test_same_condition_stray_scan_is_never_submitted(env, kind):
    cid=seed(env,2);scope=scope_for(env,cid)
    stray='a001' if kind=='other_article' else 'a000'
    run(env,cid,scope,fake_verifier(),ids=[stray],semantic_limit=0)
    original=rows(env,Scan)[0]
    if kind=='old_identity':
        with Session(env.store.engine) as session:
            article=session.get(ArticleRecord,'a000');article.content+=' newly approved original input'
            session.add(article);session.commit()
        scope=scope_for(env,cid)  # A separately approved fixture manifest, no old paid request exists.
    verify=fake_verifier();run(env,cid,scope,verify,ids=['a000'])
    assert [x['article']['id'] for x in verify.calls]==['a000']
    assert next(row for row in rows(env,Scan) if row['id']==original['id'])==original
    assert all(row['scan_id']!=original['id'] for row in rows(env,Request))


@pytest.mark.parametrize('semantic',[True,False])
@pytest.mark.parametrize('early',['disabled','superseded'])
def test_scoped_early_returns_keep_batch_shape(env, monkeypatch, semantic, early):
    cid=seed(env);scope=scope_for(env,cid)
    with Session(env.store.engine) as session:
        if early=='disabled':session.add(AppSettingRecord(key=service.MATCHING_ENABLED_KEY,value='false'))
        else:
            job=session.get(Job,cid);job.condition_revision+=1;session.add(job)
        session.commit()
    before=job_state(env,cid)
    result=run(env,cid,scope,fake_verifier() if semantic else None)
    assert result['status']==early and result['scope_hash']==scope.manifest_sha256
    assert set(result['items'])=={'a000'} and not result['batch_terminal']
    assert job_state(env,cid)==before and not rows(env,Request)
