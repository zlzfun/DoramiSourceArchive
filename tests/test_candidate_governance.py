"""Issue #298: persisted queue fairness, exact identity and evidence boundaries."""
import asyncio
import datetime as dt
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (AppSettingRecord, ArticleRecord, ArticleAnalysisRecord,
    ArticleTagAssignmentRecord, CmsTagCandidateRecord, CmsTagCandidateEvidenceRecord,
    CmsTagEventRecord, CmsTagRecord, EventAssignmentLedgerRecord, SourceConfigRecord,
    TagRetagJobRecord, TaxonomyVersionRecord)
from models.taxonomy_governance import CandidateGovernanceActionRecord, CandidateGovernanceStateRecord
from services import candidate_governance as g, taxonomy
from storage.impl.db_storage import DatabaseStorage


@pytest.fixture
def storage(tmp_path):
    store = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'governance.db'}")
    yield store
    store.engine.dispose()


@pytest.fixture
def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def seed_candidate(session, now, *, label="Novel Concept", kind="topic", age=0):
    stamp = (now - dt.timedelta(days=age)).isoformat()
    candidate = CmsTagCandidateRecord(label=label, normalized_label=taxonomy.normalize_label(label),
        proposed_kind=kind, first_seen_at=stamp,last_seen_at=stamp,created_at=stamp,updated_at=stamp)
    session.add(candidate); session.flush()
    return candidate


def add_evidence(session, candidate, now, aid, *, published=None, source_id="source", body=None):
    article = ArticleRecord(id=aid,title=aid,content_type="article",source_id=source_id,
        source_url=f"https://{source_id}.example/{aid}",publish_date=published or now.isoformat(),
        fetched_date=now.isoformat(),content=body or (aid + " detailed independent article text. ") * 6)
    session.add(article);session.flush()
    row = CmsTagCandidateEvidenceRecord(candidate_id=candidate.id, article_id=aid,
        source_id=source_id,source_owner_or_domain=source_id,published_date=article.publish_date,
        confidence=0.99,raw_label=candidate.label,created_at=now.isoformat())
    session.add(row); session.flush()
    return article


def exact_target(session, candidate, now):
    return taxonomy.create_tag(session,code="topic.existing",kind=candidate.proposed_kind,
        name_en=candidate.label,status="active",now=now)


def test_300_candidates_entities_and_a_failure_do_not_starve_tail(storage, now, monkeypatch):
    with Session(storage.engine) as session:
        candidates = [seed_candidate(session,now,label=f"Candidate {i}",kind="entity" if i < 40 else "topic",age=60)
                      for i in range(300)]
        session.commit()
        bad_id = candidates[50].id
        original = taxonomy.aggregate_candidate
        def with_one_fault(session, candidate_id, **kw):
            if candidate_id == bad_id:
                raise ValueError("deliberate test failure")
            return original(session,candidate_id,**kw)
        monkeypatch.setattr(taxonomy,"aggregate_candidate",with_one_fault)
        results = [g.run_governance_cycle(session,limit=80,now=now) for _ in range(5)]
        states = session.exec(select(CandidateGovernanceStateRecord)).all()
        assert len(states) == 300
        assert sum(bool(s.last_evaluated_at) for s in states) == 299
        failed = session.get(CandidateGovernanceStateRecord,bad_id)
        assert failed.failure_count == 1 and failed.last_error_code == "ValueError"
        assert all(s.last_evaluated_at for s in states if s.candidate_id != bad_id)
        assert sum(r["entities"] for r in results) == 40


def test_one_item_batches_alternate_new_and_history(storage, now):
    with Session(storage.engine) as session:
        old = seed_candidate(session,now,label="Old",age=60)
        new = seed_candidate(session,now,label="New")
        session.commit()
        first = g.run_governance_cycle(session,limit=1,now=now)
        late = seed_candidate(session,now,label="Latest");session.commit()
        second = g.run_governance_cycle(session,limit=1,now=now)
        assert first["candidate_ids"] == [new.id]
        assert second["candidate_ids"] == [old.id]
        assert session.get(CandidateGovernanceStateRecord,late.id).last_evaluated_at == ""


def test_expired_lease_recovers_after_engine_restart(storage, now):
    with Session(storage.engine) as session:
        c = seed_candidate(session,now)
        g.wake_candidate(session,c.id,now=now)
        state = session.get(CandidateGovernanceStateRecord,c.id)
        state.lease_token="crashed"; state.lease_until=(now+dt.timedelta(minutes=1)).isoformat()
        session.add(state); session.commit(); cid=c.id
    replacement = DatabaseStorage(db_url=str(storage.engine.url))
    try:
        with Session(replacement.engine) as session:
            assert g.run_governance_cycle(session,now=now)["evaluated"] == 0
            assert g.run_governance_cycle(session,now=now+dt.timedelta(minutes=2))["evaluated"] == 1
            assert session.get(CandidateGovernanceStateRecord,cid).lease_token == ""
    finally:
        replacement.engine.dispose()


def test_window_statistics_are_shared_and_exclude_bad_future_derived_and_private(storage,now):
    from services.article_analysis import _refresh_candidate_stats
    with Session(storage.engine) as session:
        c = seed_candidate(session,now)
        for aid,published in [("current",now.isoformat()),("older",(now-dt.timedelta(days=12)).isoformat()),
            ("expired",(now-dt.timedelta(days=40)).isoformat()),("bad","not-a-date"),
            ("future",(now+dt.timedelta(hours=1)).isoformat())]:
            add_evidence(session,c,now,aid,published=published)
        add_evidence(session,c,now,"derived",source_id="dorami_daily_brief")
        add_evidence(session,c,now,"private",source_id="private")
        session.add(SourceConfigRecord(source_id="private",name="private",params_json='{"credentialed_private":true}',
            created_at=now.isoformat(),updated_at=now.isoformat()))
        session.commit()
        _refresh_candidate_stats(session,c,now)
        assert (c.support_article_count_7d,c.support_article_count_30d)==(1,2)
        assert c.mean_confidence == pytest.approx(.99)
        # Aggregate never commits a caller's unrelated work.
        session.add(AppSettingRecord(key="must-rollback",value="x"))
        taxonomy.aggregate_candidate(session,c.id,now=now)
        session.rollback()
        assert session.get(AppSettingRecord,"must-rollback") is None
        result=g.run_governance_cycle(session,now=now+dt.timedelta(days=31))
        assert result["evaluated"]==1
        session.refresh(c)
        assert c.support_article_count_30d==0


def test_independent_projection_deduplicates_content_and_marks_unknown_events(storage,now):
    with Session(storage.engine) as session:
        c=seed_candidate(session,now)
        same_body="The same syndicated article content. "*10
        for aid,source in [("a","one"),("b","two"),("c","three")]:
            add_evidence(session,c,now,aid,source_id=source,body=same_body if aid in {"a","b"} else None)
            if aid != "c":
                session.add(EventAssignmentLedgerRecord(article_id=aid,status="skipped",created_at=now.isoformat(),updated_at=now.isoformat()))
        session.commit()
        projection,_=g.evidence_projection(session,c.id,now=now)
        assert projection["review"]["7d"]["article_count"]==3
        assert projection["independent"]["7d"]["article_count"]==1
        assert projection["independent"]["event_assignment_complete"] is False
        assert projection["independent"]["unknown_event_article_ids"]==["c"]


def test_exact_preview_fences_changes_and_applies_audit_action_job_in_one_transaction(storage,now):
    with Session(storage.engine) as session:
        c=seed_candidate(session,now)
        a=add_evidence(session,c,now,"a")
        session.add(TaxonomyVersionRecord(version=1,status="active",created_at=now.isoformat()))
        session.commit();target=exact_target(session,c,now)
        preview=g.preview_exact_merge(session,c.id,now=now)
        a.content += " changed";session.add(a);session.commit()
        with pytest.raises(taxonomy.TaxonomyError,match="stale"):
            g.apply_exact_merge(session,preview,now=now)
        preview=g.preview_exact_merge(session,c.id,now=now)
        action=g.apply_exact_merge(session,preview,now=now)
        assert action.retag_job_id is not None
        session.rollback()
        assert session.get(CmsTagCandidateRecord,c.id).status=="candidate"
        assert session.exec(select(CandidateGovernanceActionRecord)).all()==[]
        assert session.exec(select(CmsTagEventRecord)).all()==[]
        assert session.exec(select(TagRetagJobRecord)).all()==[]
        action=g.apply_exact_merge(session,preview,now=now); session.commit()
        replay=g.apply_exact_merge(session,preview,now=now)
        assert replay.id==action.id
        assert len(session.exec(select(TagRetagJobRecord)).all())==1
        assert session.get(CmsTagCandidateRecord,c.id).resolution_tag_id==target.id


def test_rejected_candidate_and_new_concept_never_auto_promote(storage,now):
    with Session(storage.engine) as session:
        rejected=seed_candidate(session,now,label="Rejected")
        rejected.status="rejected";session.add(rejected);session.commit()
        exact_target(session,rejected,now)
        assert g.preview_exact_merge(session,rejected.id,now=now) is None
        novel=seed_candidate(session,now,label="New concept")
        session.commit()
        assert g.preview_exact_merge(session,novel.id,now=now) is None
        with pytest.raises(taxonomy.TaxonomyError,match="human review"):
            taxonomy.activate_candidate(session,novel.id,automatic=True,now=now)
        assert g.run_governance_cycle(session,now=now,apply_exact=True)["merged"]==0
        assert session.get(CmsTagCandidateRecord,rejected.id).status=="rejected"


def test_stale_bounded_retag_skips_changed_article(storage,now):
    with Session(storage.engine) as session:
        c=seed_candidate(session,now); a=add_evidence(session,c,now,"a")
        session.add(TaxonomyVersionRecord(version=1,status="active",created_at=now.isoformat()))
        session.commit();exact_target(session,c,now)
        action=g.apply_exact_merge(session,g.preview_exact_merge(session,c.id,now=now),now=now)
        session.commit()
        a.content+=" changed";session.add(a);session.commit()
        assert taxonomy.claim_retag_job(session,lease_owner="worker",now=now) is None
    from services.candidate_retag import run_relevance_retag_cycle
    async def verifier(payload):
        raise AssertionError("stale article must not leave deployment")
    result=asyncio.run(run_relevance_retag_cycle(storage.engine,verifier,now=now))
    assert result["skipped"]==1 and result["evaluated"]==0
    with Session(storage.engine) as session:
        job=session.exec(select(TagRetagJobRecord)).one()
        assert json.loads(job.scope_json)["relevance_results"]["a"]["status"]=="skipped"
        assert job.succeeded_count==0


def test_relevance_requires_independent_result_never_confidence(storage,now):
    with Session(storage.engine) as session:
        c=seed_candidate(session,now);a=add_evidence(session,c,now,"a")
        session.add(ArticleAnalysisRecord(article_id=a.id,status="succeeded",quality_score=8,
            created_at=now.isoformat(),updated_at=now.isoformat()))
        session.add(TaxonomyVersionRecord(version=1,status="active",created_at=now.isoformat()))
        session.commit();tag=exact_target(session,c,now)
        g.apply_exact_merge(session,g.preview_exact_merge(session,c.id,now=now),now=now);session.commit()
        taxonomy.retag_article_from_evidence(session,a,1,now=now)
        assert session.exec(select(ArticleTagAssignmentRecord)).all()==[]
        taxonomy.retag_article_from_evidence(session,a,1,now=now,relevance_evaluator=lambda article,tag:.42)
        assignment=session.exec(select(ArticleTagAssignmentRecord)).one()
        assert assignment.relevance==.42 and assignment.relevance != .99


def test_realtime_known_alias_uses_complete_directory_without_candidate_or_relevance(storage,now):
    from services.article_analysis import _persist_candidates, AnalysisInput
    from models.analysis_contracts import ArticleTagCandidateDTO
    with Session(storage.engine) as session:
        c=seed_candidate(session,now,label="Long Canonical")
        target=exact_target(session,c,now)
        taxonomy.add_alias(session,tag_id=target.id,alias="LC",now=now)
        a=add_evidence(session,c,now,"a");session.commit()
        from services.article_analysis import analysis_input_from_article
        source_input=analysis_input_from_article(a,None,active_tags=[],session=session)
        result=_persist_candidates(session,article=source_input,
            candidates=[ArticleTagCandidateDTO(label="LC",proposed_kind="topic",confidence=.99,evidence="mention")],
            prompt_version="test",now=now)
        assert ("topic","lc") not in result
        assert session.exec(select(CmsTagCandidateRecord).where(CmsTagCandidateRecord.normalized_label=="lc")).first() is None
        assert session.exec(select(ArticleTagAssignmentRecord)).all()==[]


def test_hidden_source_change_wakes_sleeping_projection_without_new_evidence(storage,now):
    with Session(storage.engine) as session:
        c=seed_candidate(session,now);add_evidence(session,c,now,"a")
        session.commit()
        assert g.run_governance_cycle(session,now=now)["evaluated"]==1
        assert session.get(CandidateGovernanceStateRecord,c.id).next_evaluation_at > now.isoformat()
        session.add(AppSettingRecord(key="reader_hidden_source_ids",value='["source"]'));session.commit()
        assert g.run_governance_cycle(session,now=now+dt.timedelta(minutes=1))["evaluated"]==1
        session.refresh(c)
        assert c.support_article_count_30d==0


def test_retag_failure_rolls_back_action_and_releases_lease_for_other_candidates(storage,now,monkeypatch):
    with Session(storage.engine) as session:
        c=seed_candidate(session,now);add_evidence(session,c,now,"a")
        session.add(TaxonomyVersionRecord(version=1,status="active",created_at=now.isoformat()))
        session.commit();exact_target(session,c,now)
        other=seed_candidate(session,now,label="unresolved");session.commit()
        original=g.apply_exact_merge
        def fail_after_write(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError("failure before transaction commit")
        monkeypatch.setattr(g,"apply_exact_merge",fail_after_write)
        result=g.run_governance_cycle(session,now=now,apply_exact=True)
        assert result["failed"]==1 and result["merged"]==0 and result["evaluated"]==1
        assert session.get(CmsTagCandidateRecord,c.id).status=="candidate"
        assert session.get(CandidateGovernanceStateRecord,other.id).last_evaluated_at
        assert session.exec(select(CandidateGovernanceActionRecord)).all()==[]
        assert session.exec(select(CmsTagEventRecord)).all()==[]
        assert session.exec(select(TagRetagJobRecord)).all()==[]


def prepare_relevance_job(session,now):
    c=seed_candidate(session,now);a=add_evidence(session,c,now,"a")
    session.add(ArticleAnalysisRecord(article_id=a.id,status="succeeded",quality_score=8,
        created_at=now.isoformat(),updated_at=now.isoformat()))
    session.add(TaxonomyVersionRecord(version=1,status="active",created_at=now.isoformat()))
    session.commit();tag=exact_target(session,c,now)
    action=g.apply_exact_merge(session,g.preview_exact_merge(session,c.id,now=now),now=now);session.commit()
    return action.retag_job_id


def test_detached_relevance_worker_materializes_verified_score_and_counts(storage,now):
    from services.candidate_retag import run_relevance_retag_cycle
    with Session(storage.engine) as session:
        job_id=prepare_relevance_job(session,now)
    async def evaluator(payload):
        # A concurrent writer can commit: model I/O holds no DB write transaction.
        with Session(storage.engine) as other:
            other.add(AppSettingRecord(key="during-model",value="committed"));other.commit()
        return {"status":"core","relevance":.81,"quotes":[payload["body"][:25]],"reason":"core evidence"}
    result=asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now))
    assert result["assigned"]==1
    with Session(storage.engine) as session:
        assignment=session.exec(select(ArticleTagAssignmentRecord)).one()
        assert assignment.relevance==.81
        job=session.get(TagRetagJobRecord,job_id)
        assert job.status=="succeeded" and job.succeeded_count==1
        assert json.loads(job.scope_json)["relevance_result_counts"]["assigned"]==1


@pytest.mark.parametrize("mode",["timeout","interrupted","permission_changed","unquoted"])
def test_uncertain_or_stale_relevance_is_never_retried_or_installed(storage,now,mode):
    from services.candidate_retag import run_relevance_retag_cycle
    with Session(storage.engine) as session:
        job_id=prepare_relevance_job(session,now)
        if mode=="interrupted":
            job=session.get(TagRetagJobRecord,job_id);scope=json.loads(job.scope_json)
            scope["relevance_results"]={"a":{"status":"authorized"}}
            job.scope_json=json.dumps(scope);session.add(job);session.commit()
    calls=[]
    async def evaluator(payload):
        calls.append(payload["article_id"])
        if mode=="timeout":
            raise TimeoutError()
        if mode=="permission_changed":
            with Session(storage.engine) as other:
                other.add(AppSettingRecord(key="reader_hidden_source_ids",value='["source"]'));other.commit()
        return {"status":"core","relevance":.99,"quotes":[] if mode=="unquoted" else [payload["body"][:25]]}
    asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now))
    asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now+dt.timedelta(hours=1)))
    assert len(calls)==(0 if mode=="interrupted" else 1)
    with Session(storage.engine) as session:
        assert session.exec(select(ArticleTagAssignmentRecord)).all()==[]
        job=session.get(TagRetagJobRecord,job_id)
        assert job.status==("succeeded" if mode=="permission_changed" else "paused")
        assert job.succeeded_count==0
        assert json.loads(job.scope_json)["relevance_result_counts"]["assigned"]==0


def test_relevance_no_match_finishes_without_claiming_an_assignment(storage,now):
    from services.candidate_retag import run_relevance_retag_cycle
    with Session(storage.engine) as session:
        job_id=prepare_relevance_job(session,now)
    async def evaluator(payload):
        return {"status":"no_match","reason":"only an unrelated passing reference"}
    result=asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now))
    assert result["no_match"]==1 and result["assigned"]==0
    with Session(storage.engine) as session:
        job=session.get(TagRetagJobRecord,job_id)
        assert job.status=="succeeded" and job.succeeded_count==0
        assert json.loads(job.scope_json)["relevance_result_counts"]["no_match"]==1


def test_concurrent_relevance_worker_cannot_submit_same_attempt(storage,now):
    from services.candidate_retag import run_relevance_retag_cycle
    with Session(storage.engine) as session:
        prepare_relevance_job(session,now)
    calls=[]
    async def evaluator(payload):
        calls.append(True)
        contender=await run_relevance_retag_cycle(storage.engine,evaluator,now=now)
        assert contender["status"]=="idle"
        return {"status":"no_match"}
    asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now))
    assert calls==[True]


def test_podcast_description_cannot_substitute_for_published_transcript(storage,now):
    from services.candidate_retag import run_relevance_retag_cycle
    with Session(storage.engine) as session:
        job_id=prepare_relevance_job(session,now)
        a=session.get(ArticleRecord,"a");a.content_type="podcast_episode";session.add(a);session.flush()
        job=session.get(TagRetagJobRecord,job_id);scope=json.loads(job.scope_json)
        scope["version_fingerprints"]["a"]=g.article_version_fingerprint(session,a)
        job.scope_json=json.dumps(scope);session.add(job);session.commit()
    async def evaluator(payload):
        raise AssertionError("description is not transcript")
    result=asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now))
    assert result["skipped"]==1 and result["evaluated"]==0


@pytest.mark.parametrize("outcome",["mention","no_match"])
def test_verified_result_replaces_old_machine_relevance_instead_of_maxing_confidence(storage,now,outcome):
    from services.candidate_retag import run_relevance_retag_cycle
    with Session(storage.engine) as session:
        job_id=prepare_relevance_job(session,now)
        target=session.exec(select(CmsTagRecord)).one()
        session.add(ArticleTagAssignmentRecord(article_id="a",tag_id=target.id,tag_kind="topic",relevance=.99,
            assignment_source="llm",prompt_version="legacy",taxonomy_version=1,created_at=now.isoformat(),updated_at=now.isoformat()))
        session.commit()
    async def evaluator(payload):
        return {"status":outcome,"relevance":.3,"quotes":[payload["body"][:25]]}
    asyncio.run(run_relevance_retag_cycle(storage.engine,evaluator,now=now))
    with Session(storage.engine) as session:
        assignments=session.exec(select(ArticleTagAssignmentRecord)).all()
        if outcome=="no_match":
            assert assignments==[]
        else:
            assert len(assignments)==1 and assignments[0].relevance==.3


def test_window_statistics_exclude_every_system_source(storage, now, second_system_source):
    """issue #72:候选的取证不算系统源的条目——名单里的每一个,不只是公共日报。"""
    from services.article_analysis import _refresh_candidate_stats
    with Session(storage.engine) as session:
        c = seed_candidate(session, now)
        add_evidence(session, c, now, "current", published=now.isoformat())
        add_evidence(session, c, now, "from-brief", source_id="dorami_daily_brief")
        add_evidence(session, c, now, "from-other-flow", source_id=second_system_source)
        session.commit()
        _refresh_candidate_stats(session, c, now)
        assert (c.support_article_count_7d, c.support_article_count_30d) == (1, 1)


def test_window_statistics_count_an_unlisted_dorami_id(storage, now):
    """反向对照:名单里没有它时,同一个 id 的条目照常算取证。"""
    from services.article_analysis import _refresh_candidate_stats
    with Session(storage.engine) as session:
        c = seed_candidate(session, now)
        add_evidence(session, c, now, "current", published=now.isoformat())
        add_evidence(session, c, now, "from-other-flow", source_id="dorami_test_flow")
        session.commit()
        _refresh_candidate_stats(session, c, now)
        assert (c.support_article_count_7d, c.support_article_count_30d) == (2, 2)
