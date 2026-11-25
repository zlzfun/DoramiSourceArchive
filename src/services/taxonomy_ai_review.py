"""Explicit, one-shot AI approval of a new-concept draft. Never publishes."""
from __future__ import annotations

import json
import math
from sqlalchemy import update
from sqlmodel import Session, select
from config import settings
from models.db import ArticleRecord, CmsTagCandidateRecord, CmsTagRecord
from models.taxonomy_review import ReviewProposalRecord
from services import taxonomy_review as review
from services.sync_consumer_policy import taxonomy_governed_remotely


def _authority(session):
    if not settings.taxonomy.review_ai_enabled:
        raise review.ReviewConflict('AI 审核灰度开关未开启')
    if settings.taxonomy.mode == 'replica' or taxonomy_governed_remotely(session):
        raise review.ReviewConflict('本机公共标签只读')


def _signature(evidence):
    return review._hash(evidence['items'])


def run_ai_review(engine, proposal_id, *, expected_revision, expected_taxonomy_revision,
                  actor_id, adviser, now=None):
    """Durable claim precedes IO; a crash/unknown cannot silently replay a call."""
    if adviser is None:
        raise review.ReviewConflict('请先配置审核使用的辅助模型')
    current = review._now(now)
    with Session(engine) as session:
        _authority(session)
        changed = session.exec(update(ReviewProposalRecord).where(
            ReviewProposalRecord.id == proposal_id,
            ReviewProposalRecord.revision == expected_revision,
            ReviewProposalRecord.status == 'pending',
        ).values(revision=expected_revision + 1))
        if changed.rowcount != 1:
            session.rollback()
            raise review.ReviewConflict('提案已变化，请刷新后再处理')
        p = session.get(ReviewProposalRecord, proposal_id)
        if review.taxonomy.current_taxonomy_sync_revision(session) != expected_taxonomy_revision:
            raise review.ReviewConflict('目录已变化，请刷新后再审核')
        previous = json.loads(p.evidence_json)
        if previous.get('ai_review'):
            raise review.ReviewConflict('当前证据已发起过 AI 审核，请查看结果或人工处理')
        members = review._members(p)
        candidates = [session.get(CmsTagCandidateRecord, cid) for cid in members]
        if any(c is None or c.status not in {'candidate', 'reviewing'} for c in candidates):
            raise review.ReviewConflict('候选已变化，请刷新后再审核')
        evidence = review._evidence(session, members, current)
        if not evidence['items']:
            raise review.ReviewConflict('当前没有可用公开证据')
        signature = _signature(evidence)
        if previous.get('adviser_version'):
            evidence['adviser_version'] = previous['adviser_version']
        tags = session.exec(select(CmsTagRecord).where(CmsTagRecord.status == 'active').limit(1001)).all()
        if len(tags) > 1000:
            raise review.ReviewConflict('目录过大，需人工审核')
        payload = {'label': p.canonical_name, 'kind': p.kind,
                   'members': [c.label for c in candidates],
                   'formal_tags': [{'id': t.id, 'kind': t.kind, 'name': t.name_zh or t.name_en,
                                    'definition': t.description} for t in tags],
                   'evidence': {**evidence, 'samples': [
                       {'article_id': item['article_id'], 'title': a.title[:240], 'excerpt': (a.content or '')[:1200]}
                       for item in evidence['items'][:3]
                       if (a := session.get(ArticleRecord, item['article_id'])) is not None]}}
        evidence['ai_review'] = {'state': 'running', 'requested_by': actor_id,
                                 'started_at': current.isoformat(), 'threshold': 0.8}
        p.evidence_json = review._json(evidence)
        p.updated_at = current.isoformat()
        session.add(p)
        review._record_revision(session, p, action='ai_review_started', actor_id=actor_id, now=current)
        session.commit()
    # No database transaction across provider IO. Raw provider errors stay private.
    try:
        answer = adviser(payload)
    except Exception:
        answer = None
    confidence = answer.get('confidence') if isinstance(answer, dict) else None
    valid = type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1
    usable = (valid and isinstance(answer.get('reason'), str) and bool(answer['reason'].strip())
              and isinstance(answer.get('risks'), list) and all(isinstance(x, str) for x in answer['risks']))
    approve = bool(usable and confidence > 0.8 and answer.get('relation') == 'new'
                   and not answer['risks'] and answer.get('target_tag_id') is None
                   and answer.get('kind') in {'topic', 'industry', 'entity'}
                   and isinstance(answer.get('canonical_name'), str) and answer['canonical_name'].strip()
                   and isinstance(answer.get('definition'), str) and answer['definition'].strip())
    result = {'state': 'completed' if usable else 'unknown', 'confidence': confidence if valid else None,
              'threshold': 0.8, 'relation': str((answer or {}).get('relation', 'unknown'))[:40] if isinstance(answer, dict) else 'unknown',
              'reason': answer['reason'][:800] if usable else '审核响应不可用，未自动批准',
              'risks': [risk[:200] for risk in answer['risks'][:5]] if usable else [],
              'adviser_version': 'taxonomy-auto-review-v1',
              'model': str(answer.get('model', ''))[:120] if isinstance(answer, dict) else '',
              'requested_by': actor_id, 'finished_at': review._now(now).isoformat()}
    with Session(engine) as session:
        _authority(session)
        changed = session.exec(update(ReviewProposalRecord).where(
            ReviewProposalRecord.id == proposal_id,
            ReviewProposalRecord.revision == expected_revision + 1,
            ReviewProposalRecord.status == 'pending',
        ).values(revision=expected_revision + 2))
        if changed.rowcount != 1:
            raise review.ReviewConflict('审核期间提案已变化，未应用 AI 结果')
        p = session.get(ReviewProposalRecord, proposal_id)
        fresh = review._evidence(session, members, review._now(now))
        candidates = [session.get(CmsTagCandidateRecord, cid) for cid in members]
        stale = (_signature(fresh) != signature
                 or review.taxonomy.current_taxonomy_sync_revision(session) != expected_taxonomy_revision
                 or any(c is None or c.status not in {'candidate', 'reviewing'} for c in candidates))
        if stale:
            result.update(state='stale', reason='审核期间证据或目录已变化，未自动批准')
            approve = False
        if approve and session.exec(select(CmsTagRecord).where(
                CmsTagRecord.kind == answer['kind'],
                CmsTagRecord.normalized_name == review.taxonomy.normalize_label(answer['canonical_name'][:120]),
        )).first():
            result.update(state='conflict', reason='规范名称已存在，未自动批准；请人工选择归并目标')
            approve = False
        evidence = json.loads(p.evidence_json)
        evidence['ai_review'] = result
        p.evidence_json = review._json(evidence)
        p.updated_at = review._now(now).isoformat()
        session.add(p)
        review._record_revision(session, p, action='ai_review_completed', actor_id=actor_id,
                                reason=result['reason'], now=review._now(now))
        if approve:
            p.definition = answer['definition'][:400]
            session.add(p)
            session.flush()
            p = review.decide_review_proposal(session, proposal_id,
                expected_revision=expected_revision + 2, expected_taxonomy_revision=expected_taxonomy_revision,
                action='approve', actor_id=f'ai-review:{actor_id}',
                reason=f"AI 审核置信度 {confidence:.1%}，严格超过 80%；{result['reason']}",
                name=answer['canonical_name'], kind=answer['kind'], now=now)
        else:
            session.commit()
        return {'proposal_id': proposal_id, 'status': p.status, 'ai_review': result}
