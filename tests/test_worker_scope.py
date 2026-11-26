"""Operator-only selectors do not lease or mutate outside queued work."""
import asyncio
import datetime as dt
import json
import pytest
from sqlmodel import Session, select
from storage.impl.db_storage import DatabaseStorage
from models.db import ArticleRecord, ArticleAnalysisRecord, SourceConfigRecord, TaxonomyVersionRecord, TagRetagJobRecord, EventAssignmentLedgerRecord
from services import article_analysis as aa, analysis_backfill as ab, event_assignment as ev, candidate_retag as cr
from services.worker_scope import scoped_ids

@pytest.fixture
def db(tmp_path):
    store = DatabaseStorage(f'sqlite:///{tmp_path / "scope.db"}')
    now = dt.datetime.now(dt.timezone.utc)
    with Session(store.engine) as session:
        session.add(SourceConfigRecord(source_id='s', name='S', ai_analysis_enabled=True, created_at=now.isoformat(), updated_at=now.isoformat()))
        session.add(TaxonomyVersionRecord(version=1, status='active', created_at=now.isoformat()))
        session.commit()
        for aid in ['inside', 'outside']:
            article = ArticleRecord(id=aid, source_id='s', title=aid, content='body', content_type='article',
                source_url='https://example.test/'+aid, publish_date=now.isoformat(), fetched_date=now.isoformat(), has_content=True)
            session.add(article)
            session.commit()
            aa.queue_article_analysis(session, article.id, enabled=True, now=now)
        session.commit()
    yield store.engine, now
    store.engine.dispose()

@pytest.mark.parametrize('value', ['id', {'x':1}, [None], [' x'], [1]])
def test_ids_reject_ambiguous(value):
    with pytest.raises(ValueError): scoped_ids(value)

@pytest.mark.parametrize('value', [[True], [0], [-1], ['1']])
def test_integer_ids_reject(value):
    with pytest.raises(ValueError): scoped_ids(value, integer=True)


def test_empty_is_deny_all_not_default(db):
    engine, now = db
    with Session(engine) as session:
        assert aa.claim_analysis_tasks(session, worker_id='w', now=now, article_ids=[]) == []
        assert session.get(ArticleAnalysisRecord, 'outside').status == 'pending'
        tasks = aa.claim_analysis_tasks(session, worker_id='w', now=now, article_ids=['inside'])
        assert [t.article_id for t in tasks] == ['inside']
        assert session.get(ArticleAnalysisRecord, 'outside').status == 'pending'


def test_recovery_preserves_outside_full_row(db):
    engine, now = db
    with Session(engine) as session:
        aa.claim_analysis_tasks(session, worker_id='w', now=now, limit=2, lease_seconds=1)
        prior = session.get(ArticleAnalysisRecord, 'outside').model_dump()
        aa.recover_expired_leases(session, now=now+dt.timedelta(seconds=2), article_ids=['inside'])
        assert session.get(ArticleAnalysisRecord, 'outside').model_dump() == prior
        assert session.get(ArticleAnalysisRecord, 'inside').status != 'running'


def test_scoped_cycle_requires_live_scan_off_and_explicit_jobs(db):
    engine, now = db
    from config import LLMConfig
    with pytest.raises(ValueError): asyncio.run(aa.run_analysis_cycle(engine, llm_config=LLMConfig(), worker_id="w", article_ids=['inside']))
    with pytest.raises(ValueError): asyncio.run(aa.run_analysis_cycle(engine, llm_config=LLMConfig(), worker_id="w", article_ids=['inside'], scan_live=False))


def test_job_selector_does_not_lease_outside(db):
    engine, now = db
    with Session(engine) as session:
        job = TagRetagJobRecord(taxonomy_version=1, operation='full_analysis', status='queued', created_at=now.isoformat(), updated_at=now.isoformat())
        session.add(job); session.commit(); session.refresh(job)
        before = job.model_dump()
        assert ab.claim_full_analysis_backfill(session, lease_owner='w', now=now, job_ids=[]) is None
        session.refresh(job)
        assert job.model_dump() == before
        assert ab.claim_full_analysis_backfill(session, lease_owner='w', now=now, job_ids=[job.id]).id == job.id


def test_retag_selector_ignores_outside_job(db):
    engine, now = db
    with Session(engine) as session:
        job = TagRetagJobRecord(taxonomy_version=1, operation='retag_only', status='queued',
            scope_json=json.dumps({'relevance_verification_required':True,'article_ids':['outside']}),
            created_at=now.isoformat(), updated_at=now.isoformat())
        session.add(job); session.commit(); session.refresh(job)
        prior = job.model_dump()
    assert cr._claim(engine, now, article_ids=frozenset({'inside'})) is None
    with Session(engine) as session:
        assert session.get(TagRetagJobRecord, job.id).model_dump() == prior


def test_event_due_selector_preserves_outside(db):
    engine, now = db
    with Session(engine) as session:
        for aid in ['inside', 'outside']:
            session.add(EventAssignmentLedgerRecord(article_id=aid,status='pending',created_at=now.isoformat(),updated_at=now.isoformat()))
        session.commit()
        prior = session.get(EventAssignmentLedgerRecord, 'outside').model_dump()
        rows=ev._load_due_batch(session, now=now, batch_size=10, article_ids=['inside'])
        assert all(row[0].article_id == 'inside' for row in rows)
        assert session.get(EventAssignmentLedgerRecord, 'outside').model_dump() == prior


def test_event_model_boundary_allows_separate_writer(tmp_path, monkeypatch):
    # Use the ordinary full service, including its intake and sweep commits.
    import sqlite3
    from tests.test_event_assignment import _sink, _seed, NOW, LLM, _reply
    store=_sink(tmp_path); engine=store.engine; observed=[]
    _seed(engine,'a','Anthropic ships Claude Opus 5',entities=('entity.anthropic',),source_id='rss_a')
    _seed(engine,'b','Claude Opus 5 发布:上下文翻倍',entities=('entity.anthropic',),source_id='rss_b')
    def call(*args):
        with sqlite3.connect(engine.url.database, timeout=0) as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO app_settings(key,value) VALUES (?,?)',('test.boundary','1'))
            con.commit()
        observed.append(True)
        return '{"decisions":[]}'  # Response validity is not the subject of this boundary check.
    monkeypatch.setattr(ev,'_call_llm',call)
    ev.assign_pending(engine,LLM,now=NOW,article_ids=['a','b'])
    assert observed==[True]
    engine.dispose()


def test_title_pick_scope_preserves_outside_job(tmp_path,monkeypatch):
    from tests.test_auto_title_translation import article,seed,CONFIG
    from services import title_translation as tt
    from models.db import TitleTranslationJobRecord as Job
    store=DatabaseStorage(f'sqlite:///{tmp_path/"titles.db"}')
    monkeypatch.setattr(tt.daily_brief,'resolve_llm_config',lambda s:CONFIG)
    seed(store,article('outside'),article('inside'))
    with Session(store.engine) as s:before=s.get(Job,'outside').model_dump()
    assert tt._pick(store.engine,article_ids=[]) is None
    picked=tt._pick(store.engine,article_ids=['inside'])
    assert picked[0]=='inside'
    with Session(store.engine) as s:assert s.get(Job,'outside').model_dump()==before
    assert tt._pick(store.engine) is not None
    store.engine.dispose()
