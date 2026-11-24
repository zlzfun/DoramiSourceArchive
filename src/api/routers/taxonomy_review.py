"""Authenticated review inbox; GET never applies governance actions."""
from __future__ import annotations
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from sqlmodel import Session,select
from api import deps
from api.routers.taxonomy import _require_local_taxonomy_authority
from models.taxonomy_review import ReviewNotificationBatchRecord,ReviewProposalRecord
from services import taxonomy_review as service

router=APIRouter(prefix='/api/admin/taxonomy',tags=['taxonomy-review'],dependencies=[Depends(deps.require_admin)])


class DecisionBody(BaseModel):
    expected_revision:int=Field(ge=1)
    expected_taxonomy_revision:int=Field(ge=0)
    action:str=Field(pattern='^(approve|merge|reject|defer|split)$')
    reason:str=Field(min_length=1,max_length=1000)
    name:str=Field(default='',max_length=120)
    kind:str=Field(default='',pattern='^(|topic|industry|entity)$')
    target_tag_id:int|None=None
    defer_days:int=Field(default=7,ge=1,le=90)
    split_candidate_ids:list[int]=Field(default_factory=list,max_length=20)


@router.get('/review-proposals')
def list_review_proposals(status:str=Query('pending',pattern='^(all|pending|deferred|rejected|approved|merged|split|resolved)$'),
        cursor:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),session:Session=Depends(deps.get_session)):
    query=select(ReviewProposalRecord).where(ReviewProposalRecord.id>cursor)
    if status!='all':query=query.where(ReviewProposalRecord.status==status)
    rows=session.exec(query.order_by(ReviewProposalRecord.id).limit(limit+1)).all()
    return {'items':[service.proposal_payload(session,p) for p in rows[:limit]],'next_cursor':rows[limit-1].id if len(rows)>limit else None}


class ProposalSelection(BaseModel):
    id:int=Field(ge=1)
    revision:int=Field(ge=1)


class GroupBody(BaseModel):
    proposals:list[ProposalSelection]=Field(min_length=2,max_length=20)
    expected_taxonomy_revision:int=Field(ge=0)
    reason:str=Field(min_length=1,max_length=1000)
    name:str=Field(default='',max_length=120)


@router.post('/review-proposals/group',dependencies=[Depends(_require_local_taxonomy_authority)])
def group_proposals(body:GroupBody,session:Session=Depends(deps.get_session),auth:dict=Depends(deps.require_admin)):
    try:
        p=service.group_review_proposals(session,[x.model_dump() for x in body.proposals],
            expected_taxonomy_revision=body.expected_taxonomy_revision,actor_id=str(auth.get('sub') or auth.get('username') or 'admin'),reason=body.reason,name=body.name)
        return service.proposal_payload(session,p)
    except service.ReviewConflict as exc:raise HTTPException(409,str(exc)) from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get('/review-proposals/{proposal_id}')
def get_review_proposal(proposal_id:int,session:Session=Depends(deps.get_session)):
    proposal=session.get(ReviewProposalRecord,proposal_id)
    if proposal is None:raise HTTPException(404,'提案不存在')
    return service.proposal_payload(session,proposal)


@router.post('/review-proposals/{proposal_id}/decide',dependencies=[Depends(_require_local_taxonomy_authority)])
def decide_review_proposal(proposal_id:int,body:DecisionBody,session:Session=Depends(deps.get_session),auth:dict=Depends(deps.require_admin)):
    try:
        proposal=service.decide_review_proposal(session,proposal_id,actor_id=str(auth.get('sub') or auth.get('username') or 'admin'),**body.model_dump())
        return service.proposal_payload(session,proposal)
    except service.ReviewConflict as exc:raise HTTPException(409,str(exc)) from None
    except service.taxonomy.TaxonomyError:
        raise HTTPException(409,'公共标签目录存在冲突，请刷新目录后重新选择归并目标') from None
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get('/review-notifications')
def list_review_notifications(limit:int=Query(30,ge=1,le=100),session:Session=Depends(deps.get_session)):
    rows=session.exec(select(ReviewNotificationBatchRecord).order_by(ReviewNotificationBatchRecord.created_at.desc()).limit(limit)).all()
    return {'items':[{key:value for key,value in b.model_dump().items() if key not in {'destination_hash','lease_token','outbox_ids_json'}} for b in rows]}
