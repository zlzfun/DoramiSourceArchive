"""Evidence-based human review with a restart-safe, single daily Feishu digest."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Callable
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config import settings
from models.db import AppSettingRecord, ArticleRecord, CmsTagCandidateRecord, CmsTagRecord, SourceConfigRecord, TagRetagJobRecord
from models.taxonomy_review import (
    ReviewInboxRecord, ReviewNotificationAttemptRecord, ReviewNotificationBatchRecord,
    ReviewNotificationOutboxRecord, ReviewProposalRecord, ReviewProposalRevisionRecord, ReviewProposalMemberRecord,
)
from services import taxonomy
from services.candidate_governance import article_version_fingerprint, public_review_evidence
from services.feishu_notifications import (
    FeishuNotificationConfig, PublicEvidenceLink, ReviewDigestItem, build_daily_digest, send_digest, _public_link_valid,
)
from services.user_sources import source_content_may_leave_deployment

RECIPIENT = 'taxonomy_admin'
PENDING = {'pending', 'deferred'}
MAX_MEMBERS = 20
MAX_ATTEMPTS = 3


class ReviewConflict(ValueError):
    pass


def _now(value=None):
    value = value or dt.datetime.now(dt.timezone.utc)
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _evidence_hash(evidence):
    return _hash({'content_keys': sorted(x['content_key'] for x in evidence['items']),
                  'sources': evidence['sources']})


def _members(proposal):
    return [int(value) for value in json.loads(proposal.member_ids_json)]


def _evidence(session, candidate_ids, now):
    """Current public projection. Never trust an old cached URL or source ACL."""
    seen, sources, items = set(), set(), []
    for candidate_id in candidate_ids:
        for row in public_review_evidence(session, candidate_id, now=now):
            article = session.get(ArticleRecord, row.article_id)
            if article is None or not source_content_may_leave_deployment(session, article.source_id):
                continue
            source = session.get(SourceConfigRecord, article.source_id)
            # Personal sources may supply review evidence inside Dorami, but the
            # shared external digest only contains platform/public source support.
            if source and ((source.owner_username or '').strip() or source.admission_status not in {'admitted','incubating'}):
                continue
            body = ' '.join((article.content or '').split())
            key = _hash(body) if len(body) >= 80 else article.source_url
            if key in seen:
                continue
            seen.add(key)
            origin = (row.source_owner_or_domain or urlsplit(article.source_url).hostname or article.source_id).casefold()
            sources.add(origin)
            items.append({'article_id':article.id, 'source_id':article.source_id, 'origin':origin,
                          'content_key':key, 'published_date':row.published_date or row.created_at,
                          'version_fingerprint':article_version_fingerprint(session,article)})
    items.sort(key=lambda row:(row['published_date'],row['article_id']), reverse=True)
    return {'article_count':len(items), 'source_count':len(sources), 'sources':sorted(sources),
            'items':items[:300], 'first_seen_at':min((r['published_date'] for r in items),default=''),
            'last_seen_at':max((r['published_date'] for r in items),default='')}


def _suggest(candidate, evidence, tags, adviser=None):
    supported = evidence['source_count'] >= 2 and evidence['article_count'] >= 3
    data = {'canonical_name':candidate.label[:120], 'kind':candidate.proposed_kind,
            'definition':'', 'relation':'unknown', 'target_tag_id':None,
            'recommendation':('核验实体' if candidate.proposed_kind == 'entity' else '审核新概念') if supported else '继续观察',
            'reason':f"近 30 天有 {evidence['article_count']} 篇去重公开文章、{evidence['source_count']} 个原始来源",
            'risks':['尚未完成语义复核']}
    if adviser is None or not evidence['items']:
        return data
    # Adviser receives minimum public data only. Its answer is a recommendation,
    # never a mutation, and an unsupported target/relation fails closed.
    try:
        answer = adviser({'label':candidate.label,'kind':candidate.proposed_kind,
                          'evidence':json.loads(_json(evidence)),'formal_tags':tags})
    except Exception:
        return data
    if isinstance(answer,dict) and answer.get('adviser_status')=='unavailable':
        return data
    if not isinstance(answer,dict) or answer.get('relation') not in {'equivalent','broader','narrower','related','new','unknown'}:
        return data
    target = answer.get('target_tag_id')
    if target is not None and target not in {t['id'] for t in tags}:
        return data
    if answer['relation'] in {'equivalent','broader','narrower','related'} and target is None:
        return data
    data.update({key:str(answer.get(key,data[key]))[:limit] for key,limit in
                 [('canonical_name',120),('definition',400),('reason',800)]})
    data['relation'], data['target_tag_id'] = answer['relation'], target
    data['kind'] = answer.get('kind') if answer.get('kind') in {'topic','industry','entity'} else data['kind']
    data['recommendation'] = {'equivalent':'归并已有标签','broader':'核对上位关系','narrower':'核对下位关系',
                              'related':'保留独立概念','new':'建立新概念','unknown':'继续观察'}[data['relation']]
    data['risks'] = [str(x)[:200] for x in answer.get('risks',[])[:5]] if isinstance(answer.get('risks',[]),list) else []
    data['_adviser_version']=str(answer.get('adviser_version','taxonomy-adviser-v1'))[:80]
    return data


def _record_revision(session, proposal, *, action, actor_id='', reason='', now):
    session.add(ReviewProposalRevisionRecord(proposal_id=proposal.id,revision=proposal.revision,
        snapshot_json=_json(proposal.model_dump()),actor_id=actor_id,action=action,reason=reason,created_at=now.isoformat()))
    inbox = session.get(ReviewInboxRecord,(RECIPIENT,proposal.id))
    if inbox is None:
        inbox=ReviewInboxRecord(recipient=RECIPIENT,proposal_id=proposal.id,revision=proposal.revision,updated_at=now.isoformat())
    inbox.revision=proposal.revision
    inbox.status='pending' if proposal.status in PENDING else 'done'
    inbox.updated_at=now.isoformat()
    session.add(inbox)
    # A decided or superseded revision may never be sent as a fresh reminder.
    for old in session.exec(select(ReviewNotificationOutboxRecord).where(
        ReviewNotificationOutboxRecord.proposal_id==proposal.id,
        ReviewNotificationOutboxRecord.status.in_(['pending','failed']))).all():
        old.status='cancelled';old.updated_at=now.isoformat();session.add(old)
    if proposal.status=='pending' and json.loads(proposal.evidence_json).get('article_count',0)>0:
        session.add(ReviewNotificationOutboxRecord(proposal_id=proposal.id,proposal_revision=proposal.revision,
            created_at=now.isoformat(),updated_at=now.isoformat()))


def refresh_review_proposals(session: Session, *, limit=100, now=None, adviser: Callable | None=None, adviser_limit=10, adviser_version="taxonomy-adviser-v1"):
    """Bounded fair refresh; single bad adviser/candidate cannot stop the queue.

    No adviser means explicit ungrouped/unknown proposals, available immediately
    for human review. Repeated refresh/aging alone does not create notifications.
    """
    current=_now(now);count={'created':0,'updated':0,'unchanged':0,'failed':0}
    tags=[{'id':t.id,'name':t.name_zh or t.name_en,'kind':t.kind,'description':t.description[:240]}
          for t in session.exec(select(CmsTagRecord).where(CmsTagRecord.status=='active').order_by(CmsTagRecord.id).limit(1000)).all()]
    # Persisted keyset cursor includes candidates without exportable evidence,
    # so a private-only/low-quality prefix cannot starve later public concepts.
    cursor_key='taxonomy_review_refresh_cursor'
    cursor=session.get(AppSettingRecord,cursor_key)
    try:last_id=int(cursor.value) if cursor else 0
    except ValueError:last_id=0
    query=select(CmsTagCandidateRecord).where(CmsTagCandidateRecord.id>last_id).order_by(CmsTagCandidateRecord.id).limit(min(500,max(1,limit)))
    candidates=session.exec(query).all()
    if not candidates and last_id:
        candidates=session.exec(select(CmsTagCandidateRecord).order_by(CmsTagCandidateRecord.id).limit(min(500,max(1,limit)))).all()
    seen_proposals=set();adviser_calls=0
    for candidate in candidates:
        try:
            identity=f'candidate:{candidate.id}'
            membership=session.get(ReviewProposalMemberRecord,candidate.id)
            p=session.get(ReviewProposalRecord,membership.proposal_id) if membership else session.exec(select(ReviewProposalRecord).where(ReviewProposalRecord.identity==identity)).first()
            if p and p.id in seen_proposals:continue
            if p:seen_proposals.add(p.id)
            if candidate.status not in {'candidate','reviewing'}:
                if p and p.status in PENDING:
                    p.status='resolved';p.revision+=1;p.updated_at=current.isoformat();session.add(p)
                    _record_revision(session,p,action='resolved_elsewhere',now=current);session.commit()
                continue
            # Rejected, grouped/split and approved identities are durable tombstones.
            if p and p.status not in PENDING:
                p.updated_at=current.isoformat();session.add(p);session.commit();count['unchanged']+=1;continue
            members=_members(p) if p else [candidate.id]
            evidence=_evidence(session,members,current)
            if not evidence['items'] and p is None:
                count['unchanged']+=1;continue
            fingerprint=_evidence_hash(evidence)
            prior_evidence=json.loads(p.evidence_json) if p else {}
            evidence_same=p and p.evidence_hash==fingerprint
            adviser_due=bool(adviser and (prior_evidence.get('adviser_version')!=adviser_version or p.taxonomy_revision!=taxonomy.current_taxonomy_sync_revision(session) or [(x['article_id'],x.get('version_fingerprint')) for x in prior_evidence.get('items',[])]!=[(x['article_id'],x.get('version_fingerprint')) for x in evidence['items']]))
            deferred_wait=p and p.status=='deferred' and p.next_review_at>current.isoformat()
            if evidence_same and (deferred_wait or (p.status=='pending' and not adviser_due)):
                p.updated_at=current.isoformat();session.add(p);session.commit();count['unchanged']+=1;continue
            use_adviser=adviser if adviser_calls<max(0,adviser_limit) else None
            # Text excerpts are supplied only after the same public/external AI
            # gate as outgoing evidence. They are untrusted document content.
            evidence['samples']=[]
            for item in evidence['items'][:3]:
                article=session.get(ArticleRecord,item['article_id'])
                if article and source_content_may_leave_deployment(session,article.source_id):
                    evidence['samples'].append({'title':article.title[:240],'excerpt':(article.content or '')[:1200]})
            candidate_id=candidate.id
            proposal_id=p.id if p else None
            expected_revision=p.revision if p else None
            expected_taxonomy=taxonomy.current_taxonomy_sync_revision(session)
            candidate_snapshot=SimpleNamespace(label=p.canonical_name if p and len(members)>1 else candidate.label,proposed_kind=p.kind if p and len(members)>1 else candidate.proposed_kind)
            session.rollback()  # no DB transaction is held during external IO
            suggestion=_suggest(candidate_snapshot,evidence,tags,use_adviser)
            if use_adviser:adviser_calls+=1
            session.expire_all()
            candidate=session.get(CmsTagCandidateRecord,candidate_id)
            p=session.get(ReviewProposalRecord,proposal_id) if proposal_id else None
            membership=session.get(ReviewProposalMemberRecord,candidate_id)
            latest_evidence=_evidence(session,members,current)
            latest_hash=_evidence_hash(latest_evidence)
            versions_same=[(x['article_id'],x.get('version_fingerprint')) for x in latest_evidence['items']]==[(x['article_id'],x.get('version_fingerprint')) for x in evidence['items']]
            if (candidate is None or candidate.status not in {'candidate','reviewing'} or
                latest_hash!=fingerprint or not versions_same or taxonomy.current_taxonomy_sync_revision(session)!=expected_taxonomy or
                (p and (p.revision!=expected_revision or p.status not in PENDING)) or
                (proposal_id and p is None) or (membership and membership.proposal_id!=proposal_id)):
                session.rollback();count['failed']+=1;continue
            completed_version=suggestion.pop('_adviser_version','')
            evidence.pop('samples',None)
            evidence['adviser_version']=completed_version
            advice_same=p and all(getattr(p,'risks_json' if key=='risks' else key)==(_json(value) if key=='risks' else value) for key,value in suggestion.items())
            if evidence_same and p.status=='pending' and (not completed_version or advice_same):
                # Provider unavailable/budget exhausted: preserve earlier advice,
                # do not generate duplicate unknown revisions or reminders.
                p.updated_at=current.isoformat()
                if completed_version:
                    p.evidence_json=_json(evidence);p.taxonomy_revision=taxonomy.current_taxonomy_sync_revision(session)
                session.add(p);session.commit();count['unchanged']+=1;continue
            created=p is None
            if created:
                p=ReviewProposalRecord(identity=identity,member_ids_json=_json(members),canonical_name=candidate.label,
                    kind=candidate.proposed_kind,created_at=current.isoformat(),updated_at=current.isoformat())
            else:
                changed=session.exec(update(ReviewProposalRecord).where(ReviewProposalRecord.id==p.id,
                    ReviewProposalRecord.revision==expected_revision,ReviewProposalRecord.status.in_(PENDING))
                    .values(revision=expected_revision+1))
                if changed.rowcount!=1:
                    session.rollback();count['failed']+=1;continue
                session.expire(p)
                p.revision=expected_revision+1
            for key,value in suggestion.items():
                setattr(p,'risks_json' if key=='risks' else key,_json(value) if key=='risks' else value)
            p.status='pending';p.next_review_at='';p.evidence_json=_json(evidence);p.evidence_hash=fingerprint
            p.taxonomy_revision=taxonomy.current_taxonomy_sync_revision(session)
            p.priority=min(100,evidence['source_count']*10+evidence['article_count'])
            p.updated_at=current.isoformat();session.add(p);session.flush()
            if created:
                session.add(ReviewProposalMemberRecord(candidate_id=candidate.id,proposal_id=p.id))
            _record_revision(session,p,action='created' if created else 'evidence_updated',now=current)
            session.commit();count['created' if created else 'updated']+=1
        except Exception:
            session.rollback();count['failed']+=1
    if candidates:
        cursor=session.get(AppSettingRecord,cursor_key) or AppSettingRecord(key=cursor_key,value='')
        cursor.value=str(candidates[-1].id);session.add(cursor);session.commit()
    return count


def proposal_payload(session, p, *, now=None):
    current=_now(now);result=p.model_dump()
    result['members']=[{'id':c.id,'label':c.label,'kind':c.proposed_kind,'status':c.status} for cid in _members(p)
                       if (c:=session.get(CmsTagCandidateRecord,cid)) is not None]
    result['risks']=json.loads(p.risks_json)
    evidence=_evidence(session,_members(p),current)
    # Re-project current ACL on each GET and send; old snapshots cannot leak.
    result['evidence']={k:v for k,v in evidence.items() if k!='items'}
    result['evidence']['articles']=[{'id':a.id,'title':a.title,'url':a.source_url if _public_link_valid(a.source_url) else '', 'published_date':x['published_date']}
        for x in evidence['items'][:10] if (a:=session.get(ArticleRecord,x['article_id'])) is not None]
    result['current_taxonomy_revision']=taxonomy.current_taxonomy_sync_revision(session)
    from services.sync_consumer_policy import taxonomy_governed_remotely
    result['read_only']=settings.taxonomy.mode=='replica' or taxonomy_governed_remotely(session)
    if p.draft_tag_id:
        tag=session.get(CmsTagRecord,p.draft_tag_id)
        result['publication_status']=tag.status if tag else 'missing'
    else:result['publication_status']='not_applicable'
    if p.retag_job_id:
        job=session.get(TagRetagJobRecord,p.retag_job_id);result['retag_status']=job.status if job else 'missing'
    else:result['retag_status']='not_queued'
    result['notification_statuses']=list(session.exec(select(ReviewNotificationOutboxRecord.status).where(
        ReviewNotificationOutboxRecord.proposal_id==p.id).distinct()).all())
    for key in ('member_ids_json','risks_json','evidence_json','evidence_hash'):
        result.pop(key,None)
    return result


def decide_review_proposal(session: Session, proposal_id: int, *, expected_revision: int,
        expected_taxonomy_revision: int, action: str, actor_id: str, reason: str,
        name: str='',kind: str='',target_tag_id: int | None=None, defer_days: int=7,
        split_candidate_ids: list[int] | None=None,now=None):
    """CAS, decision, catalog draft/alias, audit and outbox cancellation commit once."""
    current=_now(now)
    if action not in {'approve','merge','reject','defer','split'} or not reason.strip():
        raise ValueError('请选择处理方式并填写理由')
    try:
        # First statement takes the write lock. Later catalog reads and writes are
        # in the same transaction; stale or duplicate clicks cannot apply twice.
        changed=session.exec(update(ReviewProposalRecord).where(ReviewProposalRecord.id==proposal_id,
            ReviewProposalRecord.revision==expected_revision,ReviewProposalRecord.status.in_(PENDING))
            .values(revision=expected_revision+1))
        if changed.rowcount!=1:raise ReviewConflict('提案已变化，请刷新后再处理')
        session.expire_all();p=session.get(ReviewProposalRecord,proposal_id)
        if taxonomy.current_taxonomy_sync_revision(session)!=expected_taxonomy_revision:
            raise ReviewConflict('公共标签目录已变化，请刷新后重新选择')
        from services.sync_consumer_policy import taxonomy_governed_remotely
        if settings.taxonomy.mode=="replica" or taxonomy_governed_remotely(session):raise ReviewConflict('本机公共标签只读，请在权威节点处理')
        candidates=[session.get(CmsTagCandidateRecord,cid) for cid in _members(p)]
        if any(c is None or c.status not in {'candidate','reviewing'} for c in candidates):
            raise ReviewConflict('候选已在其它入口处理，请刷新提案')
        evidence=_evidence(session,_members(p),current)
        if action in {'approve','merge'} and not evidence['items']:
            raise ReviewConflict('当前没有可用公开证据，请补充后处理')
        chosen_kind=kind or p.kind
        if chosen_kind not in {'topic','industry','entity'}:raise ValueError('标签类型无效')
        chosen_name=(name or p.canonical_name).strip()[:120]
        if not chosen_name:raise ValueError('概念名称不能为空')
        event_action='change_flags';target=None
        if action=='approve':
            normalized=taxonomy.normalize_label(chosen_name)
            if session.exec(select(CmsTagRecord).where(CmsTagRecord.kind==chosen_kind,CmsTagRecord.normalized_name==normalized)).first():
                raise ReviewConflict('同名规范标签已存在，请选择归并')
            # Approval creates only a draft; controlled publication remains a
            # separate operation and candidates remain reviewing until published.
            target=CmsTagRecord(code=f'review-{p.id}',kind=chosen_kind,name_zh=chosen_name,normalized_name=normalized,
                description=p.definition,status='draft',user_selectable=False,activation_mode='manual',
                created_at=current.isoformat(),updated_at=current.isoformat())
            session.add(target);session.flush();p.draft_tag_id=target.id;p.status='approved'
            for c in candidates:c.status='reviewing';c.updated_at=current.isoformat();session.add(c)
        elif action=='merge':
            target=session.get(CmsTagRecord,target_tag_id or p.target_tag_id)
            if target is None or target.status!='active':raise ValueError('请选择有效的正式标签')
            for c in candidates:
                if taxonomy.normalize_label(c.label)!=target.normalized_name:
                    try:
                        taxonomy.add_alias(session,tag_id=target.id,alias=c.label,now=current)
                    except taxonomy.TaxonomyError:
                        from models.db import CmsTagAliasRecord
                        normalized=taxonomy.normalize_label(c.label)
                        conflict=session.exec(select(CmsTagRecord).where(
                            CmsTagRecord.kind==target.kind,CmsTagRecord.normalized_name==normalized,
                            CmsTagRecord.id!=target.id)).first()
                        if conflict is None:
                            conflict=session.exec(select(CmsTagRecord).join(CmsTagAliasRecord).where(
                                CmsTagAliasRecord.kind==target.kind,CmsTagAliasRecord.normalized_alias==normalized,
                                CmsTagRecord.id!=target.id)).first()
                        label=(conflict.name_zh or conflict.name_en or conflict.code) if conflict else c.label
                        raise ReviewConflict(f'名称或别名与规范标签“{label}”冲突，请刷新目录后重新选择归并目标') from None
                c.status='merged';c.resolution_tag_id=target.id;c.updated_at=current.isoformat();session.add(c)
            p.status='merged';p.target_tag_id=target.id;event_action='merge'
        elif action=='reject':
            p.status='rejected';event_action='reject'
            for c in candidates:c.status='rejected';c.updated_at=current.isoformat();session.add(c)
        elif action=='defer':
            if not 1<=defer_days<=90:raise ValueError('暂缓天数须为 1–90')
            p.status='deferred';p.next_review_at=(current+dt.timedelta(days=defer_days)).isoformat()
        else:
            subset=set(split_candidate_ids or [])
            if not subset or not subset<set(_members(p)):raise ValueError('拆组须选择部分候选')
            left=[c.id for c in candidates if c.id not in subset];right=sorted(subset)
            p.status='split'
            for ids in (left,right):
                child_evidence=_evidence(session,ids,current)
                child=ReviewProposalRecord(identity=f'split:{p.id}:{p.revision}:{_hash(ids)[:12]}',member_ids_json=_json(ids),
                    canonical_name=session.get(CmsTagCandidateRecord,ids[0]).label,kind=p.kind,
                    reason=reason.strip(),evidence_json=_json(child_evidence),evidence_hash=_evidence_hash(child_evidence),
                    taxonomy_revision=expected_taxonomy_revision,created_at=current.isoformat(),updated_at=current.isoformat())
                session.add(child);session.flush()
                for cid in ids:
                    membership=session.get(ReviewProposalMemberRecord,cid)
                    if membership:membership.proposal_id=child.id
                    else:membership=ReviewProposalMemberRecord(candidate_id=cid,proposal_id=child.id)
                    session.add(membership)
                _record_revision(session,child,action='split',actor_id=actor_id,reason=reason,now=current)
        event=taxonomy._event(session,event_action,target_tag_id=target.id if target else None,actor_id=actor_id,
            reason=reason.strip(),payload={'operation':'review_'+action,'proposal_id':p.id,'proposal_revision':p.revision,
                'candidate_ids':_members(p)},now=current)
        if action=='merge' and taxonomy.current_taxonomy_version(session)>0:
            remaining={item['article_id'] for item in evidence['items'][:100]}
            for candidate in candidates:
                ids=[row.article_id for row in public_review_evidence(session,candidate.id,now=current) if row.article_id in remaining]
                if not ids:continue
                remaining.difference_update(ids)
                job=TagRetagJobRecord(event_id=event.id,taxonomy_version=taxonomy.current_taxonomy_version(session),
                    scope_json=_json({'article_ids':ids,'version_fingerprints':{aid:article_version_fingerprint(session,session.get(ArticleRecord,aid)) for aid in ids},
                        'candidate_id':candidate.id,'target_tag_id':target.id,'proposal_id':p.id,'relevance_verification_required':True}),
                    created_at=current.isoformat(),updated_at=current.isoformat())
                session.add(job);session.flush()
                if p.retag_job_id is None:p.retag_job_id=job.id
        p.canonical_name=chosen_name;p.kind=chosen_kind;p.decided_by=actor_id;p.decision_reason=reason.strip()
        p.updated_at=current.isoformat();p.taxonomy_revision=taxonomy.current_taxonomy_sync_revision(session)
        session.add(p);_record_revision(session,p,action=action,actor_id=actor_id,reason=reason,now=current)
        session.commit();session.refresh(p);return p
    except Exception:
        session.rollback();raise


def group_review_proposals(session, selections, *, expected_taxonomy_revision, actor_id, reason, name='', now=None):
    current=_now(now)
    if not 2<=len(selections)<=MAX_MEMBERS or len({s['id'] for s in selections})!=len(selections):
        raise ValueError('请选择 2–20 个不同提案')
    try:
        parents=[];ids=[]
        for chosen in sorted(selections,key=lambda x:x['id']):
            changed=session.exec(update(ReviewProposalRecord).where(ReviewProposalRecord.id==chosen['id'],
                ReviewProposalRecord.revision==chosen['revision'],ReviewProposalRecord.status.in_(PENDING))
                .values(revision=chosen['revision']+1,status='split'))
            if changed.rowcount!=1:raise ReviewConflict('提案已变化，请刷新后重新归组')
            session.expire_all();p=session.get(ReviewProposalRecord,chosen['id']);parents.append(p);ids+=_members(p)
        if taxonomy.current_taxonomy_sync_revision(session)!=expected_taxonomy_revision:raise ReviewConflict('公共目录已变化，请刷新')
        from services.sync_consumer_policy import taxonomy_governed_remotely
        if settings.taxonomy.mode=="replica" or taxonomy_governed_remotely(session):raise ReviewConflict('本机公共标签只读')
        ids=sorted(set(ids))
        if len(ids)>MAX_MEMBERS:raise ValueError('每组最多 20 个候选')
        if any((c:=session.get(CmsTagCandidateRecord,cid)) is None or c.status not in {'candidate','reviewing'} for cid in ids):
            raise ReviewConflict('候选已在其它入口处理')
        evidence=_evidence(session,ids,current)
        group=ReviewProposalRecord(identity='group:'+uuid.uuid4().hex,member_ids_json=_json(ids),
            canonical_name=(name.strip() or parents[0].canonical_name)[:120],kind=parents[0].kind,
            reason=reason.strip(),evidence_json=_json(evidence),evidence_hash=_evidence_hash(evidence),taxonomy_revision=expected_taxonomy_revision,
            created_at=current.isoformat(),updated_at=current.isoformat())
        session.add(group);session.flush()
        for cid in ids:
            membership=session.get(ReviewProposalMemberRecord,cid)
            if membership:membership.proposal_id=group.id
            else:membership=ReviewProposalMemberRecord(candidate_id=cid,proposal_id=group.id)
            session.add(membership)
        for parent in parents:
            parent.updated_at=current.isoformat();parent.decided_by=actor_id;parent.decision_reason=reason.strip();session.add(parent)
            _record_revision(session,parent,action='grouped',actor_id=actor_id,reason=reason,now=current)
        _record_revision(session,group,action='group_created',actor_id=actor_id,reason=reason,now=current)
        taxonomy._event(session,'change_flags',actor_id=actor_id,reason=reason,
            payload={'operation':'review_group','proposal_id':group.id,'parent_proposal_ids':[p.id for p in parents]},now=current)
        session.commit();session.refresh(group);return group
    except Exception:
        session.rollback();raise


def _digest_item(session,p,now):
    evidence=_evidence(session,_members(p),now)
    if not evidence['items']:return None
    links=[]
    for item in evidence['items'][:3]:
        article=session.get(ArticleRecord,item['article_id'])
        if article:links.append(PublicEvidenceLink(article.title,article.source_url,is_public=True))
    return ReviewDigestItem(name=p.canonical_name,kind=p.kind,recommendation=p.recommendation,reason=p.reason,
        article_count=evidence['article_count'],source_count=evidence['source_count'],
        review_path=f'/review/taxonomy/{p.id}',evidence=tuple(links))


async def send_daily_review_digest(engine, config: FeishuNotificationConfig, *, now=None, transport=None):
    """One logical digest/day. Unknown POSTs never retry; known failures <=3.

    Call each minute (or at 18:00 and bounded retry ticks). A restart after an
    unconfirmed send records unknown rather than risking a duplicate POST.
    """
    current=_now(now);stamp=current.isoformat();local=current.astimezone(ZoneInfo(config.timezone))
    if not config.enabled or local.hour<config.digest_hour:return {'status':'skipped'}
    day=local.date().isoformat();dest=_hash(config.webhook_url);token=uuid.uuid4().hex
    with Session(engine) as session:
        for expired in session.exec(select(ReviewNotificationBatchRecord).where(
            ReviewNotificationBatchRecord.status=='sending',ReviewNotificationBatchRecord.lease_expires_at<=stamp)).all():
            expired.status='unknown';expired.error_code='interrupted_send';expired.updated_at=stamp;session.add(expired)
            for row in session.exec(select(ReviewNotificationOutboxRecord).where(ReviewNotificationOutboxRecord.batch_id==expired.id)).all():
                row.status='unknown';row.updated_at=stamp;session.add(row)
            attempt=session.get(ReviewNotificationAttemptRecord,(expired.id,expired.attempt_count))
            if attempt:attempt.status='unknown';attempt.error_code='interrupted_send';attempt.ended_at=stamp;session.add(attempt)
        session.commit()
        batch=session.exec(select(ReviewNotificationBatchRecord).where(
            ReviewNotificationBatchRecord.recipient==RECIPIENT,ReviewNotificationBatchRecord.channel=='feishu',
            ReviewNotificationBatchRecord.digest_date==day)).first()
        if batch and batch.status=='sending':
            if batch.lease_expires_at and batch.lease_expires_at<=stamp:
                batch.status='unknown';batch.error_code='interrupted_send';batch.updated_at=stamp
                for row in session.exec(select(ReviewNotificationOutboxRecord).where(ReviewNotificationOutboxRecord.batch_id==batch.id)).all():
                    row.status='unknown';row.updated_at=stamp;session.add(row)
                attempt=session.get(ReviewNotificationAttemptRecord,(batch.id,batch.attempt_count))
                if attempt:attempt.status='unknown';attempt.error_code='interrupted_send';attempt.ended_at=stamp;session.add(attempt)
                session.add(batch);session.commit()
            return {'status':batch.status}
        if batch and (batch.status in {'accepted','unknown','cancelled'} or batch.attempt_count>=MAX_ATTEMPTS or batch.next_attempt_at>stamp):
            return {'status':batch.status}
        if batch and batch.destination_hash!=dest:return {'status':'blocked','error_code':'destination_changed'}
        if batch is None:
            rows=session.exec(select(ReviewNotificationOutboxRecord).where(
                ReviewNotificationOutboxRecord.status=='pending').order_by(ReviewNotificationOutboxRecord.created_at).limit(500)).all()
        else:rows=[session.get(ReviewNotificationOutboxRecord,i) for i in json.loads(batch.outbox_ids_json)]
        valid=[];items=[]
        for row in rows:
            if row is None:continue
            p=session.get(ReviewProposalRecord,row.proposal_id)
            if p is None or p.status!='pending' or p.revision!=row.proposal_revision or (item:=_digest_item(session,p,current)) is None:
                row.status='cancelled';row.updated_at=stamp;session.add(row);continue
            valid.append(row);items.append(item)
        if not items:
            if batch:batch.status='cancelled';session.add(batch)
            session.commit();return {'status':'empty'}
        payload=build_daily_digest(config,items,digest_date=local.date(),inbox_path='/review/taxonomy',total_count=len(items))
        if batch is None:
            batch=ReviewNotificationBatchRecord(id=uuid.uuid4().hex,digest_date=day,destination_hash=dest,
                outbox_ids_json=_json([r.id for r in valid]),created_at=stamp,updated_at=stamp)
            session.add(batch)
            try:session.flush()
            except IntegrityError:session.rollback();return {'status':'claimed_elsewhere'}
        claimed=session.exec(update(ReviewNotificationBatchRecord).where(ReviewNotificationBatchRecord.id==batch.id,
            ReviewNotificationBatchRecord.status.in_(['pending','failed']),ReviewNotificationBatchRecord.attempt_count==batch.attempt_count)
            .values(status='sending',lease_token=token,lease_expires_at=(current+dt.timedelta(minutes=5)).isoformat(),
                    attempt_count=batch.attempt_count+1,updated_at=stamp))
        if claimed.rowcount!=1:session.rollback();return {'status':'claimed_elsewhere'}
        session.expire_all();batch=session.get(ReviewNotificationBatchRecord,batch.id)
        batch_id,attempt_no=batch.id,batch.attempt_count
        for row in valid:row.status='sending';row.batch_id=batch_id;row.updated_at=stamp;session.add(row)
        session.add(ReviewNotificationAttemptRecord(batch_id=batch_id,attempt=attempt_no,status='sending',started_at=stamp))
        session.commit()
    result=await send_digest(config,payload,transport=transport)
    finished=_now(now).isoformat()
    with Session(engine) as session:
        batch=session.get(ReviewNotificationBatchRecord,batch_id)
        if batch.lease_token!=token or batch.status!='sending':return {'status':'unknown','error_code':'lost_lease'}
        batch.status=result.status;batch.error_code=result.error_code or '';batch.updated_at=finished
        batch.next_attempt_at=(current+dt.timedelta(minutes=5*2**(attempt_no-1))).isoformat() if result.status=='failed' else ''
        session.add(batch)
        attempt=session.get(ReviewNotificationAttemptRecord,(batch_id,attempt_no))
        attempt.status=result.status;attempt.error_code=result.error_code or '';attempt.http_status=result.http_status
        attempt.provider_code=result.provider_code;attempt.ended_at=finished;session.add(attempt)
        for row in session.exec(select(ReviewNotificationOutboxRecord).where(ReviewNotificationOutboxRecord.batch_id==batch_id)).all():
            row.status=result.status;row.updated_at=finished;session.add(row)
            if result.status=='accepted':
                inbox=session.get(ReviewInboxRecord,(RECIPIENT,row.proposal_id))
                if inbox:inbox.last_notified_revision=max(inbox.last_notified_revision,row.proposal_revision);session.add(inbox)
        session.commit()
    return {'status':result.status,'batch_id':batch_id,'attempt_count':attempt_no,'error_code':result.error_code}
