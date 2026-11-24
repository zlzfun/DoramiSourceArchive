"""Private compound-interest boundaries and brief integration (issue #298)."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.db import ArticleRecord, ArticleAnalysisRecord, UserRecord, AppSettingRecord, CmsTagRecord, CmsTagAliasRecord
from models.custom_interests import InterestArticleMatchRecord, UserInterestConditionRecord
from services import custom_interests as service
from services import personal_digest as digest
from storage.impl.db_storage import DatabaseStorage

NOW = dt.datetime.now(dt.timezone.utc)
AST = {'all_of':[{'any_of':['enterprise','企业']},{'any_of':['agent','智能体']},{'any_of':['on-premise','内网']}], 'exclude':['gaming']}


@pytest.fixture(autouse=True)
def fresh_case_clock(monkeypatch):
    # A CI shard may queue this module for minutes after collection. Keep the
    # test cutoff relative to its rows, not earlier than newly enqueued work.
    monkeypatch.setattr(sys.modules[__name__], 'NOW', dt.datetime.now(dt.timezone.utc))


@pytest.fixture
def store(tmp_path, monkeypatch):
    from config import LLMConfig
    from services import interest_governance_llm as bridge
    monkeypatch.setattr(bridge, "resolve_llm_config", lambda _: LLMConfig(base_url="https://example.com/v1", api_key="fixture", model="aux"))
    store = DatabaseStorage(db_url=f'sqlite:///{tmp_path / "custom.db"}')
    with Session(store.engine) as session:
        for name in ('alice','bob'):
            session.add(UserRecord(username=name,password_hash='x',role='user',is_active=True,
                ai_beta_enabled=True,created_at=NOW.isoformat(),updated_at=NOW.isoformat()))
        session.add(AppSettingRecord(key='personal_digest_enabled',value='true'))
        session.commit()
    yield store
    store.engine.dispose()


def trusted_fake(store, verifier):
    from services import interest_governance_llm as bridge
    def adapted(*, username, payload, reserved_output_token_limit, expected_verifier_policy):
        assert reserved_output_token_limit == adapted.max_output_tokens
        assert expected_verifier_policy == adapted.verifier_policy
        return verifier(username=username, payload=payload)
    adapted.verifier_policy = bridge.verifier_policy(bridge._interest_config(store.engine))
    adapted.max_output_tokens = adapted.verifier_policy['max_output_tokens']
    return adapted


def add_article(session, aid='a', *, title='Enterprise on-premise Agent', source='rss_public', body='Enterprise on-premise Agent deployment is the central topic.', age=1):
    article = ArticleRecord(id=aid,title=title,source_id=source,source_url='https://example.com/'+aid,
        content_type='rss_article',publish_date=(NOW-dt.timedelta(hours=age)).isoformat(),
        fetched_date=(NOW-dt.timedelta(hours=age)).isoformat(),content=body,has_content=True)
    session.add(article)
    session.flush()
    session.add(ArticleAnalysisRecord(article_id=aid,status='succeeded',tagging_status='succeeded',
        quality_score=8,score_reason='Detailed deployment evidence',content_genre='industry_news',analyzed_at=NOW.isoformat(),created_at=NOW.isoformat(),updated_at=NOW.isoformat()))
    session.commit()
    return article


def save(session, name='Enterprise agent', **kw):
    row=service.save_condition(session,'alice',service.ConditionInput(name=name,condition=AST,**kw))
    session.commit()
    return row


def test_compound_does_not_collapse_to_or_and_mentions_are_not_core():
    condition=service.ConditionAST.model_validate(AST)
    assert service.evaluate(condition,'Enterprise on-premise Agent','full evidence')[0]=='core'
    assert service.evaluate(condition,'Enterprise news','on-premise Agent appears once')[0]=='mention'
    assert service.evaluate(condition,'Enterprise Agent','deployment')[0]=='no_match'
    assert service.evaluate(condition,'Enterprise on-premise Agent gaming','')[0]=='no_match'
    ai=service.ConditionAST(all_of=[{'any_of':['AI']}])
    assert service.evaluate(ai,'said something','')[0]=='no_match'
    assert service.evaluate(condition,'Enterprise on-premise Agent','',usable=False)[0]=='unknown'
    with pytest.raises(ValidationError):
        service.ConditionAST(all_of=[{'any_of':['agent']}],sql='SELECT *')


def test_private_revision_noop_owner_and_delete(store):
    with Session(store.engine) as session:
        row=save(session)
        assert service.state_revision(session,'alice')==1
        assert digest._interest_version(session,'alice')==-2
        body=service.ConditionInput(name=row.name,condition=AST,expected_revision=1)
        service.save_condition(session,'alice',body,row.id)
        session.commit()
        assert service.state_revision(session,'alice')==1
        with pytest.raises(LookupError): service.save_condition(session,'bob',body,row.id)
        with pytest.raises(service.RevisionConflict):
            service.save_condition(session,'alice',body.model_copy(update={'expected_revision':2}),row.id)
        service.delete_condition(session,'alice',row.id,1)
        session.commit()
        assert service.state_revision(session,'alice')==2
        assert service.list_conditions(session,'alice')==[]


def test_worker_paging_resume_fingerprint_and_acl(store):
    with Session(store.engine) as session:
        row=save(session)
        cid=row.id
        add_article(session,'a')
        add_article(session,'b')
        add_article(session,'c',source='user_rss_secret')
        add_article(session,'future',age=-2)
    first=service.run_match_cycle(store.engine,limit=1)
    assert first['processed']==1
    assert service.run_match_cycle(store.engine,limit=1)['processed']==1
    with Session(store.engine) as session:
        assert {a.id for _,_,a in service.current_matches(session,'alice')}=={'a','b'}
        assert service.current_matches(session,'bob')==[]
        article=session.get(ArticleRecord,'a');article.content+=' Changed content';session.add(article);session.commit()
        assert {a.id for _,_,a in service.current_matches(session,'alice')}=={'b'}
        session.add(AppSettingRecord(key='reader_hidden_source_ids',value='["rss_public"]'));session.commit()
        assert service.current_matches(session,'alice')==[]
    # Resume the same keyset; forbidden and future content cannot be installed.
    for _ in range(4): service.run_match_cycle(store.engine,limit=1)
    with Session(store.engine) as session:
        matches=session.exec(select(InterestArticleMatchRecord)).all()
        assert {m.article_id for m in matches}=={'a','b'}
        condition=session.get(UserInterestConditionRecord,cid)
        service.delete_condition(session,'alice',cid,condition.revision);session.commit()
        assert service.current_matches(session,'alice')==[]


def test_custom_only_brief_selects_existing_lane_and_typed_refs(store):
    with Session(store.engine) as session:
        save(session)
        add_article(session)
    assert service.run_match_cycle(store.engine)['processed']==1
    with Session(store.engine) as session:
        interests=digest._load_interests(session,'alice')
        assert interests[0].tag_code is None
        result=digest.generate_personal_digest(session,'alice',now=NOW+dt.timedelta(minutes=1))
        assert result.status!='empty_subscriptions'
        assert any(item.article_id=='a' for item in result.items)
        item=next(item for item in result.items if item.article_id=='a')
        assert json.loads(item.matched_interest_codes_json)==[]
        assert json.loads(item.ranking_features_json)['custom_interest_refs'][0]['kind']=='custom'
        assert json.loads(result.edition.interest_snapshot_json)[0]['condition']==AST


def test_catalog_alias_search_and_formal_put_preserves_custom(store):
    from api.routers.interests import catalog, replace_interests, InterestReplace
    with Session(store.engine) as session:
        save(session)
        tag=CmsTagRecord(code='topic:test298',kind='topic',name_zh='内网系统',name_en='Private Systems',
            normalized_name='private systems',status='active',user_selectable=True,created_at=NOW.isoformat(),updated_at=NOW.isoformat())
        session.add(tag);session.flush()
        session.add(CmsTagAliasRecord(tag_id=tag.id,kind='topic',alias='onprem',normalized_alias='onprem',created_at=NOW.isoformat(),updated_at=NOW.isoformat()))
        session.commit()
        result=catalog(q='onprem',cursor=0,limit=20,auth={'sub':'alice'},session=session)
        assert [item['id'] for item in result['items']]==[tag.id]
        result=replace_interests(InterestReplace(items=[{'tag_id':tag.id}]),auth={'sub':'alice'},session=session)
        assert len(result['custom_items'])==1
        revision=result['revision']
        result=replace_interests(InterestReplace(items=[{'tag_id':tag.id}]),auth={'sub':'alice'},session=session)
        assert result['revision']==revision


def test_semantic_permission_before_call_and_after_cas(store):
    calls=[]
    def verifier(**kwargs):
        calls.append(kwargs)
        with Session(store.engine) as session:
            article=session.get(ArticleRecord,'a');article.content='New unrelated content';session.add(article);session.commit()
        return {'outcome':'core','reason':'Central topic','quotes':['Enterprise on-premise Agent deployment is the central topic.']}
    with Session(store.engine) as session:
        save(session)
        add_article(session,title='Deployment notes')
    # Global AI disabled: text must not reach the verifier.
    with Session(store.engine) as session:
        session.add(AppSettingRecord(key='ai_beta_global_enabled',value='false'));session.commit()
    verifier = trusted_fake(store, verifier)
    service.run_match_cycle(store.engine,semantic_verifier=verifier)
    assert calls==[]
    from services import accounts
    with Session(store.engine) as session:
        row=session.get(AppSettingRecord,accounts.AI_BETA_GLOBAL_KEY);row.value='true';session.add(row);session.commit()
    service.run_match_cycle(store.engine,now=NOW+dt.timedelta(minutes=10),semantic_verifier=verifier)
    assert len(calls)==1
    with Session(store.engine) as session:
        assert service.current_matches(session,'alice')==[]


@pytest.mark.parametrize('semantic_edit',[False,True])
@pytest.mark.parametrize('edit_stage', ['selection', 'localization'])
def test_inflight_edit_fences_completion_and_preserves_durable_pending(store, monkeypatch, semantic_edit, edit_stage):
    with Session(store.engine) as session:
        row=save(session)
        cid=row.id
        add_article(session)
    service.run_match_cycle(store.engine)
    with Session(store.engine) as session:
        pending=digest.start_personal_digest_edition(session,'alice',now=NOW+dt.timedelta(minutes=1))
        pending_id=pending.edition.id
    original=digest.select_digest_articles
    edited=False
    def edit_interest():
        nonlocal edited
        if not edited:
            with Session(store.engine) as session:
                service.save_condition(session,'alice',service.ConditionInput(
                    name='Updated interest',condition={**AST,'semantic_intent':INTENT} if semantic_edit else AST,
                    expected_revision=1),cid)
                session.commit()
            edited=True
    def edit_during_selection(*args,**kwargs):
        edit_interest()
        return original(*args,**kwargs)
    def edit_during_localization(*args,**kwargs):
        edit_interest()
        return {}
    if edit_stage == 'selection':
        monkeypatch.setattr(digest,'select_digest_articles',edit_during_selection)
    else:
        from services import personal_digest_titles
        monkeypatch.setattr(personal_digest_titles, 'localize_edition_titles', edit_during_localization)
    with Session(store.engine) as session:
        result=digest.generate_personal_digest(
            session,'alice',now=NOW+dt.timedelta(minutes=1),pending_edition_id=pending_id,
            localize_before_publish=edit_stage == 'localization')
        assert edited
        assert result.status=='pending'
        assert result.items==()
        assert result.edition.generation_token is None
        assert json.loads(result.edition.interest_snapshot_json)[0]['revision']==2
        if semantic_edit:
            assert json.loads(result.edition.interest_snapshot_json)[0]['condition']['semantic_intent']==INTENT
    def verify(**kw):
        from services.interest_evidence_windows import validate_semantic_verdict
        payload=kw['payload'];sid=payload['evidence_windows'][0]['spans'][0]['span_id']
        return validate_semantic_verdict(payload,{'outcome':'core','scope':'main','reason':'Grounded fixture relation',
            'missing_group_indexes':[], 'evidence':[{'kind':'text','span_id':sid,'group_indexes':[0,1,2]}],
            'intent_assessment':{'verdict':'satisfied','reason':'Grounded fixture relation',
                'evidence_span_ids':[sid],'negative_example_indexes':[]}})
    service.run_match_cycle(store.engine,semantic_verifier=trusted_fake(store, verify) if semantic_edit else None)
    with Session(store.engine) as session:
        result=digest.generate_personal_digest(session,'alice',now=NOW+dt.timedelta(minutes=2),pending_edition_id=pending_id)
        assert result.status in {'ready','degraded'}
        assert result.items


def test_historical_edition_filters_revoked_source_snapshot(store):
    from api.routers.personal_briefs import serialize_edition
    with Session(store.engine) as session:
        save(session)
        add_article(session)
    service.run_match_cycle(store.engine)
    with Session(store.engine) as session:
        result=digest.generate_personal_digest(session,'alice',now=NOW+dt.timedelta(minutes=1))
        assert serialize_edition(session,result.edition)['items']
        session.add(AppSettingRecord(key='reader_hidden_source_ids',value='["rss_public"]'));session.commit()
        assert serialize_edition(session,result.edition)['items']==[]


def test_worker_recovers_expired_lease_and_bad_condition_does_not_starve(store):
    from models.custom_interests import InterestMatchJobRecord
    with Session(store.engine) as session:
        first=save(session,name='Broken')
        first.condition_json='{}';session.add(first);session.commit()
        bad_id=first.id
        second=save(session,name='Good')
        add_article(session)
        job=session.get(InterestMatchJobRecord,second.id)
        job.lease_token='old-process';job.lease_expires_at=(NOW-dt.timedelta(hours=1)).isoformat()
        session.add(job);session.commit()
    assert service.run_match_cycle(store.engine)['status']=='failed'
    result=service.run_match_cycle(store.engine)
    assert result['status']=='processed' and result['processed']==1
    with Session(store.engine) as session:
        assert session.get(InterestMatchJobRecord,bad_id).last_error=='matching_failed'


INTENT = {'version':1,'requirement':'AI acts as a tool for scientific research.',
          'negative_examples':['Only studying AI carbon costs does not qualify.']}


@pytest.mark.parametrize('patch',[
    {'version':True},{'version':2},{'requirement':''},{'requirement':' '*4},
    {'requirement':'a'*1001},{'requirement':'hidden\x00command'},
    {'negative_examples':['x']*7},{'negative_examples':['a'*501]},
    {'negative_examples':['']},{'negative_examples':['bad\ttab']},
    {'negative_examples':[{'scenario':'old shape'}]},{'extra':'no'},
])
def test_semantic_intent_rejects_unsupported_or_unbounded_input(patch):
    with pytest.raises(ValidationError):service.SemanticIntent.model_validate({**INTENT,**patch})


def test_semantic_prose_keeps_case_and_newlines_but_trims_edges():
    intent=service.SemanticIntent.model_validate({**INTENT,'requirement':'  AI as Tool\n研究方法  ',
        'negative_examples':['  AI as Object  ']})
    assert intent.requirement=='AI as Tool\n研究方法'
    assert intent.negative_examples==['AI as Object']
    with pytest.raises(ValidationError):service.ConditionAST.model_validate({**AST,'semantic_intent':{}})


def test_legacy_bytes_noop_identity_and_intent_omission_versus_null(store):
    with Session(store.engine) as session:
        row=save(session);add_article(session)
        article=session.get(ArticleRecord,'a');_,text,_=service.evidence_snapshot(session,article)
        source=service._source_metadata(session,article)
        original_json=row.condition_json;original_identity=service._paid_identity(row,article,text,source)
        assert original_json==service._json(AST)
        row=service.save_condition(session,'alice',service.ConditionInput(name=row.name,
            condition={**AST,'semantic_intent':None},expected_revision=1),row.id)
        session.commit()
        assert row.revision==1 and row.condition_json==original_json
        assert service._paid_identity(row,article,text,source)==original_identity
        row=service.save_condition(session,'alice',service.ConditionInput(name=row.name,
            condition={**AST,'semantic_intent':INTENT},expected_revision=1),row.id)
        session.commit();assert row.revision==2 and row.schema_version==2
        assert service._paid_identity(row,article,text,source)!=original_identity
        intent_json=row.condition_json
        row=service.save_condition(session,'alice',service.ConditionInput(name=row.name,
            condition=AST,expected_revision=2),row.id)
        session.commit()
        assert row.revision==2 and row.condition_json==intent_json
        row=service.save_condition(session,'alice',service.ConditionInput(name=row.name,
            condition={**AST,'semantic_intent':None},expected_revision=2),row.id)
        session.commit()
        assert row.revision==3 and row.schema_version==1 and row.condition_json==original_json


def test_intent_snapshot_roundtrip_preview_and_no_local_core(store):
    condition=service.ConditionAST.model_validate({**AST,'semantic_intent':INTENT})
    with Session(store.engine) as session:
        row=service.save_condition(session,'alice',service.ConditionInput(name='Science',condition=condition))
        session.commit();add_article(session)
        frozen=digest._serialize_interests(digest._load_interests(session,'alice'))
        assert json.loads(frozen)[0]['condition']['semantic_intent']==INTENT
        assert digest._serialize_interests(digest._deserialize_interests(frozen))==frozen
        legacy='[{"custom_interest_id":1,"revision":1,"name":"Old","condition":'+json.dumps(AST)+'}]'
        assert digest._deserialize_interests(legacy)[0].condition==AST
        before=session.exec(select(service.Job)).one().model_dump()
        preview=service.preview(session,'alice',condition)
        assert preview['status']=='semantic_verification_required' and preview['items']==[]
        assert len(preview['lexical_candidates'])==1 and preview['lexical_candidates'][0]['outcome']=='unknown'
        assert session.exec(select(service.Job)).one().model_dump()==before
        assert session.exec(select(service.Request)).all()==[]
    service.run_match_cycle(store.engine)
    with Session(store.engine) as session:
        assert service.current_matches(session,'alice')==[]
        assert session.exec(select(InterestArticleMatchRecord)).one().outcome=='unknown'
    assert service.evaluate(condition,'Enterprise on-premise Agent gaming','')[0]=='no_match'
    assert service.evaluate(condition,'Enterprise on-premise Agent',INTENT['negative_examples'][0])[0]=='unknown'


def test_ready_edition_is_immutable_after_intent_edit(store):
    from models.db import PersonalDigestEditionRecord
    with Session(store.engine) as session:
        condition=save(session);cid=condition.id;add_article(session)
    service.run_match_cycle(store.engine)
    with Session(store.engine) as session:
        generated=digest.generate_personal_digest(session,'alice',now=NOW+dt.timedelta(minutes=1))
        assert generated.status in {'ready','degraded'}
        frozen=generated.edition.model_dump()
        service.save_condition(session,'alice',service.ConditionInput(name=session.get(UserInterestConditionRecord,cid).name,
            condition={**AST,'semantic_intent':INTENT},expected_revision=1),cid)
        session.commit()
        assert session.get(PersonalDigestEditionRecord,generated.edition.id).model_dump()==frozen
        assert json.loads(frozen['interest_snapshot_json'])[0]['condition']==AST


def test_older_ast_parser_rejection_backs_off_without_starving_legacy_job(store,monkeypatch):
    from pydantic import BaseModel, ConfigDict, Field
    class LegacyAST(BaseModel):
        model_config=ConfigDict(extra='forbid')
        all_of:list[service.ConceptGroup]
        exclude:list[str]=Field(default_factory=list)
    current=service.ConditionAST
    class OldParser:
        @staticmethod
        def model_validate_json(raw):
            LegacyAST.model_validate_json(raw)  # The old worker's exact extra=forbid boundary.
            return current.model_validate_json(raw)
    with Session(store.engine) as session:
        first=service.save_condition(session,'alice',service.ConditionInput(name='New intent',
            condition={**AST,'semantic_intent':INTENT}))
        session.commit();bad_id=first.id
        save(session,name='Legacy');add_article(session)
    monkeypatch.setattr(service,'ConditionAST',OldParser)
    now=NOW+dt.timedelta(minutes=1)
    assert service.run_match_cycle(store.engine,now=now)['status']=='failed'
    assert service.run_match_cycle(store.engine,now=now)['status']=='processed'
    with Session(store.engine) as session:
        job=session.get(service.Job,bad_id)
        assert job.last_error=='matching_failed'
        assert dt.datetime.fromisoformat(job.next_attempt_at)>=now+dt.timedelta(minutes=5)
        assert session.exec(select(service.Request)).all()==[]
        assert len(service.current_matches(session,'alice'))==1


def test_router_preview_is_free_and_roundtrips_nested_intent(store):
    from api.routers.interests import preview_custom_interest, create_custom_interest, update_custom_interest
    body=service.ConditionInput(name='Science',condition={**AST,'semantic_intent':INTENT})
    with Session(store.engine) as session:
        add_article(session)
        preview=preview_custom_interest(body,auth={'sub':'alice'},session=session)
        assert preview['status']=='semantic_verification_required' and not preview['items']
        assert service.list_conditions(session,'alice')==[]
        assert session.exec(select(service.Request)).all()==[]
        created=create_custom_interest(body,auth={'sub':'alice'},session=session)['item']
        assert created['condition']['semantic_intent']==INTENT and created['schema_version']==2
        updated=update_custom_interest(created['id'],service.ConditionInput(name='Science',condition=AST,
            expected_revision=1),auth={'sub':'alice'},session=session)['item']
        assert updated['revision']==1 and updated['condition']['semantic_intent']==INTENT


def test_policy_drift_at_final_brief_fence_resets_pending_and_snapshot_preserves_origin(store, monkeypatch):
    from config import LLMConfig
    from services import interest_governance_llm as bridge
    from services.interest_evidence_windows import validate_semantic_verdict
    selected = [LLMConfig(base_url='https://example.com/v1', api_key='fixture', model='aux')]
    monkeypatch.setattr(bridge, 'resolve_llm_config', lambda _: selected[0])
    with Session(store.engine) as session:
        service.save_condition(session, 'alice', service.ConditionInput(name='Relation interest',
            condition={**AST, 'semantic_intent': INTENT}))
        session.add(AppSettingRecord(key='ai_beta_global_enabled', value='true'))
        session.commit(); add_article(session)
    def verify(**kw):
        payload = kw['payload']; sid = payload['evidence_windows'][0]['spans'][0]['span_id']
        return validate_semantic_verdict(payload, {'outcome': 'core', 'scope': 'main', 'reason': 'Grounded fixture',
            'missing_group_indexes': [], 'evidence': [{'kind': 'text', 'span_id': sid, 'group_indexes': [0,1,2]}],
            'intent_assessment': {'verdict': 'satisfied', 'reason': 'Grounded relation',
                'evidence_span_ids': [sid], 'negative_example_indexes': []}})
    service.run_match_cycle(store.engine, semantic_verifier=trusted_fake(store, verify))
    with Session(store.engine) as session:
        assert len(service.current_matches(session, 'alice')) == 1
        pending = digest.start_personal_digest_edition(session, 'alice', now=NOW+dt.timedelta(minutes=1))
        pending_id = pending.edition.id
    original = digest.select_digest_articles
    def drift_during_selection(*args, **kwargs):
        selected[0] = LLMConfig(base_url='https://example.com/v1', api_key='fixture', model='new-pro', thinking_mode='high')
        return original(*args, **kwargs)
    monkeypatch.setattr(digest, 'select_digest_articles', drift_during_selection)
    with Session(store.engine) as session:
        result = digest.generate_personal_digest(session, 'alice', now=NOW+dt.timedelta(minutes=1), pending_edition_id=pending_id, policy=digest.DigestSelectionPolicy(target_items=1, interest_max_ratio=1))
        assert result.status == 'pending' and result.items == ()
        assert result.edition.generation_token is None
    selected[0] = LLMConfig(base_url='https://example.com/v1', api_key='fixture', model='aux')
    monkeypatch.setattr(digest, 'select_digest_articles', original)
    with Session(store.engine) as session:
        result = digest.generate_personal_digest(session, 'alice', now=NOW+dt.timedelta(minutes=2), pending_edition_id=pending_id, policy=digest.DigestSelectionPolicy(target_items=1, interest_max_ratio=1))
        assert result.status in {'ready', 'degraded'} and result.items
        policy = bridge.verifier_policy(selected[0])
        # The existing final selection snapshot copies the complete custom evidence.
        encoded = json.dumps([item.model_dump() for item in result.items], default=str)
        assert policy['provider_endpoint_sha256'] in encoded
        frozen = result.edition.model_dump()
        selected[0] = LLMConfig(base_url='https://example.com/v1', api_key='fixture', model='another-pro')
        session.refresh(result.edition)
        assert result.edition.model_dump() == frozen


@pytest.mark.parametrize('change', ['none', 'content', 'visibility'])
def test_runtime_publication_rechecks_custom_evidence_after_titles(store, monkeypatch, change):
    """M2M title work can overlap archive edits; only current evidence publishes."""
    from models.db import PersonalDigestItemRecord
    from services import personal_digest_titles

    with Session(store.engine) as session:
        save(session)
        add_article(session)
    service.run_match_cycle(store.engine)
    stamp = NOW + dt.timedelta(minutes=1)
    with Session(store.engine) as session:
        pending = digest.start_personal_digest_edition(session, 'alice', now=stamp)
        edition_id = pending.edition.id

    localized = []
    def localize(session, edition, **kwargs):
        localized.append(edition.id)
        assert kwargs['persist'] is False
        assert kwargs['items']
        # A separate connection also proves title work does not hold a write lock.
        with Session(store.engine) as writer:
            if change == 'content':
                article = writer.get(ArticleRecord, 'a')
                article.content = 'Unrelated replacement body'
                writer.add(article)
            elif change == 'visibility':
                writer.add(AppSettingRecord(key='reader_hidden_source_ids', value='["rss_public"]'))
            writer.commit()
        return {}

    monkeypatch.setattr(personal_digest_titles, 'localize_edition_titles', localize)
    with Session(store.engine) as session:
        result = digest.generate_personal_digest(
            session, 'alice', now=stamp, pending_edition_id=edition_id,
            localize_before_publish=True, execution_clock=lambda: stamp,
        )
        assert localized == [edition_id]
        if change == 'none':
            assert result.status in {'ready', 'degraded'}
            assert [item.article_id for item in result.items] == ['a']
            assert json.loads(result.edition.briefs_payload_json)['items']
        else:
            assert result.status == 'pending'
            assert result.edition.generation_token is None
            assert result.edition.briefs_payload_json is None
            assert session.exec(select(PersonalDigestItemRecord).where(
                PersonalDigestItemRecord.edition_id == edition_id
            )).all() == []
