"""Bounded relevance verification, with model I/O outside database transactions.

A committed ``authorized`` attempt is never automatically submitted again after
an interrupted call. Unknown results pause for operator reconciliation. Identity
resolution, evaluated no-match, and materialized assignments have separate counts.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import math
import uuid

from sqlalchemy import or_, update
from sqlmodel import Session, select

from models.db import (ArticleRecord, ArticleAnalysisRecord, ArticleTagAssignmentRecord,
    CmsTagRecord, CmsTagCandidateRecord, TagRetagJobRecord,
    PodcastTextArtifactRecord, PodcastTextPublicationRecord)
from services import candidate_governance as g, taxonomy, sync_consumer_policy, user_sources
from services.source_visibility import hidden_source_ids


def _scope(job):
    try:
        value=json.loads(job.scope_json or "{}")
        return value if isinstance(value,dict) else {}
    except ValueError:
        return {}


def _body(session,article):
    if article.content_type != "podcast_episode":
        return article.content or ""
    from config import PodcastConfig
    from services.podcast_text_reader import _plain_text, PodcastTextReaderError
    texts=[]
    for pub,artifact in session.exec(select(PodcastTextPublicationRecord,PodcastTextArtifactRecord).join(
        PodcastTextArtifactRecord,
        (PodcastTextArtifactRecord.id==PodcastTextPublicationRecord.artifact_id)
        & (PodcastTextArtifactRecord.episode_id==PodcastTextPublicationRecord.episode_id)
        & (PodcastTextArtifactRecord.kind==PodcastTextPublicationRecord.kind)
        & (PodcastTextArtifactRecord.authority_id==PodcastTextPublicationRecord.authority_id)
    ).where(PodcastTextPublicationRecord.episode_id==article.id,
        PodcastTextPublicationRecord.status=="published",PodcastTextPublicationRecord.published_at.is_not(None),
        PodcastTextPublicationRecord.kind.in_(["publisher_transcript","normalized_transcript"]))).all():
        if hashlib.sha256(artifact.inline_text.encode()).hexdigest()!=artifact.content_hash:
            continue
        try:
            texts.append(_plain_text(artifact,PodcastConfig(),for_reading=True))
        except (PodcastTextReaderError,ValueError,TypeError,UnicodeError):
            continue
    return "\n".join(texts)


def _permitted(session,article):
    if article is None or article.source_id in hidden_source_ids(session):
        return False
    from services.article_analysis import source_allows_analysis
    analysis=session.get(ArticleAnalysisRecord,article.id)
    return bool(source_allows_analysis(session,article.source_id) and analysis and not article.analysis_authority_id and not analysis.authority_id
        and user_sources.source_content_may_leave_deployment(session,article.source_id)
        and sync_consumer_policy.local_source_operation_allowed(session,article.source_id,operation="analysis")
        and taxonomy._analysis_has_readable_result(session,analysis))


def _claim(engine,now,article_ids=None):
    token=uuid.uuid4().hex
    with Session(engine) as session:
        available=or_(TagRetagJobRecord.status=="queued",
            (TagRetagJobRecord.status=="running") & (TagRetagJobRecord.lease_expires_at <= now.isoformat()))
        for job in session.exec(select(TagRetagJobRecord).where(TagRetagJobRecord.operation=="retag_only",available)
                               .order_by(TagRetagJobRecord.id)).all():
            scope = _scope(job)
            if not scope.get("relevance_verification_required"):
                continue
            if article_ids is not None and not any(str(aid) in article_ids and str(aid) not in scope.get('relevance_results', {})
                                                  for aid in scope.get('article_ids', [])):
                continue
            claimed=session.exec(update(TagRetagJobRecord).where(TagRetagJobRecord.id==job.id,available).values(
                status="running",lease_owner=token,lease_expires_at=(now+dt.timedelta(minutes=5)).isoformat()))
            session.commit()
            if claimed.rowcount==1:
                return job.id,token
    return None


def _validated_result(raw,payload):
    if not isinstance(raw,dict):
        return {"status":"unknown","reason_code":"invalid_response"}
    status=raw.get("status",raw.get("outcome"))
    if status not in {"core","mention","no_match","unknown"}:
        return {"status":"unknown","reason_code":"invalid_response"}
    result={"status":status,"reason":str(raw.get("reason", ""))[:800]}
    if status in {"core","mention"}:
        score=raw.get("relevance")
        quotes=raw.get("quotes")
        combined=payload["title"]+"\n"+payload["body"]
        if (type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1
            or not isinstance(quotes,list) or not any(isinstance(q,str) and len(q.strip())>=12 and q in combined for q in quotes)):
            return {"status":"unknown","reason_code":"unsupported_relevance"}
        result["relevance"]=min(score,.59) if status=="mention" else score
    return result


async def run_relevance_retag_cycle(engine,evaluator,*,limit=10,now=None,article_ids=None):
    """Evaluator is async(payload)->status, relevance, reason, verbatim quotes.

    The caller supplies a metered/deadline-gated evaluator. None leaves jobs
    queued without claiming or pretending assignment work completed.
    """
    from services.worker_scope import scoped_ids
    article_ids = scoped_ids(article_ids)
    current=g._now(now)
    counts={"status":"idle","evaluated":0,"assigned":0,"no_match":0,"unknown":0,"skipped":0}
    if evaluator is None:
        return {**counts,"status":"verifier_unavailable"}
    claim=_claim(engine,current,article_ids=article_ids)
    if claim is None:
        return counts
    job_id,token=claim
    counts.update(status="processed",job_id=job_id)
    for _ in range(max(1,min(int(limit),100))):
        with Session(engine) as session:
            job=session.get(TagRetagJobRecord,job_id)
            if not job or job.lease_owner!=token:
                return {**counts,"status":"superseded"}
            scope=_scope(job);results=scope.setdefault("relevance_results",{})
            ids=scope.get("article_ids") or []
            # Persisted authorized entries are uncertain, never implicit retries.
            for aid,prior in results.items():
                if article_ids is None and prior.get("status")=="authorized":
                    results[aid]={"status":"unknown","reason_code":"interrupted_attempt"}
            pending=[str(aid) for aid in ids if str(aid) not in results
                     and (article_ids is None or str(aid) in article_ids)]
            if not pending:
                job.scope_json=json.dumps(scope,ensure_ascii=False,sort_keys=True);session.add(job);session.commit()
                break
            aid=pending[0]
            article=session.get(ArticleRecord,aid)
            target=session.get(CmsTagRecord,scope.get("target_tag_id"))
            candidate=session.get(CmsTagCandidateRecord,scope.get("candidate_id"))
            expected=(scope.get("version_fingerprints") or {}).get(aid)
            valid_target=bool(target and target.status=="active" and candidate and candidate.status in {"merged","activated"}
                              and candidate.resolution_tag_id==target.id)
            current_evidence={row.article_id for row in g.public_review_evidence(session,candidate.id,now=g._now(now))} if candidate else set()
            valid_target=valid_target and aid in current_evidence
            fingerprint=g.article_version_fingerprint(session,article) if article else ""
            body=_body(session,article) if article and _permitted(session,article) else ""
            if not valid_target or not expected or fingerprint!=expected or not _permitted(session,article) or len(body.strip())<80:
                results[aid]={"status":"skipped","reason_code":"stale_or_unavailable_evidence"}
                job.scope_json=json.dumps(scope,ensure_ascii=False,sort_keys=True);session.add(job);session.commit()
                counts["skipped"]+=1
                continue
            revision=taxonomy.current_taxonomy_sync_revision(session)
            payload={"article_id":aid,"title":article.title[:500],"body":body[:24000],"tag":{
                "id":target.id,"code":target.code,"kind":target.kind,"name_zh":target.name_zh,"name_en":target.name_en,
                "description":target.prompt_description},"taxonomy_revision":revision}
            results[aid]={"status":"authorized","fingerprint":fingerprint,"taxonomy_revision":revision}
            job.scope_json=json.dumps(scope,ensure_ascii=False,sort_keys=True)
            job.lease_expires_at=(g._now(now)+dt.timedelta(minutes=5)).isoformat()
            session.add(job);session.commit()
        try:
            result=_validated_result(await asyncio.wait_for(evaluator(payload),timeout=60),payload)
        except Exception:
            result={"status":"unknown","reason_code":"evaluation_uncertain"}
        with Session(engine) as session:
            job=session.get(TagRetagJobRecord,job_id)
            if not job or job.lease_owner!=token or (job.lease_expires_at or "") <= g._now(now).isoformat():
                return {**counts,"status":"superseded"}
            scope=_scope(job);article=session.get(ArticleRecord,aid)
            current_candidate=session.get(CmsTagCandidateRecord,scope.get("candidate_id"))
            if (not _permitted(session,article) or fingerprint!=g.article_version_fingerprint(session,article)
                or revision!=taxonomy.current_taxonomy_sync_revision(session)
                or not current_candidate or current_candidate.status not in {"merged","activated"}
                or current_candidate.resolution_tag_id!=payload["tag"]["id"]
                or aid not in {row.article_id for row in g.public_review_evidence(session,current_candidate.id,now=g._now(now))}):
                result={"status":"skipped","reason_code":"evidence_or_permission_changed"}
            elif result["status"] in {"core","mention"}:
                score=result["relevance"]
                taxonomy.retag_article_from_evidence(session,article,job.taxonomy_version,now=current,
                    relevance_evaluator=lambda _a,tag:score if tag.id==payload["tag"]["id"] else None,
                    relevance_evidence_text=payload["body"])
                assigned=session.exec(select(ArticleTagAssignmentRecord).where(
                    ArticleTagAssignmentRecord.article_id==aid,ArticleTagAssignmentRecord.tag_id==payload["tag"]["id"])).first()
                result["assignment_materialized"]=bool(assigned)
                counts["assigned"]+=int(bool(assigned))
            elif result["status"]=="no_match":
                taxonomy.retag_article_from_evidence(session,article,job.taxonomy_version,now=current,
                    relevance_exclusions={payload["tag"]["id"]}, relevance_evidence_text=payload["body"])
            scope.setdefault("relevance_results",{})[aid]=result
            job.scope_json=json.dumps(scope,ensure_ascii=False,sort_keys=True);session.add(job);session.commit()
            counts["evaluated"]+=1
            if result["status"] in {"no_match","unknown","skipped"}:
                counts[result["status"]]+=1
    with Session(engine) as session:
        job=session.get(TagRetagJobRecord,job_id)
        if not job or job.lease_owner!=token:
            return {**counts,"status":"superseded"}
        scope=_scope(job);results=scope.get("relevance_results",{})
        pending=[aid for aid in scope.get("article_ids",[]) if aid not in results]
        unknown=sum(r.get("status") in {"unknown","authorized"} for r in results.values())
        scope["relevance_result_counts"]={
            "assigned":sum(bool(r.get("assignment_materialized")) for r in results.values()),
            "no_match":sum(r.get("status")=="no_match" for r in results.values()),
            "skipped":sum(r.get("status")=="skipped" for r in results.values()),"unknown":unknown,"pending":len(pending)}
        job.affected_count=len(results)
        job.succeeded_count=scope["relevance_result_counts"]["assigned"]
        job.failed_count=unknown
        # A pause requires explicit reconciliation; neither retries nor a generic
        # retag worker can silently re-submit a possibly billed model request.
        job.status="queued" if pending else ("paused" if unknown else "succeeded")
        job.last_error="relevance_verification_unknown" if unknown else None
        job.lease_owner=job.lease_expires_at=None
        job.scope_json=json.dumps(scope,ensure_ascii=False,sort_keys=True);session.add(job);session.commit()
    return counts
