"""Read-only personal interest projection, full-set pagination and frozen evidence."""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from sqlalchemy import event

from tests.test_custom_interests import store, save, add_article, run_core_cycle, NOW, AST
from tests.test_reader_interests import _setup, _login
from models.db import ArticleRecord, AppSettingRecord, ReaderSubscriptionRecord
from services import custom_interests as custom
from services import reader_interests as projection
from services import personal_digest as digest
from api.routers import interests, personal_briefs


def seed_match(session, condition, article, *, fingerprint=None, outcome='core'):
    from services.interest_governance_llm import current_verifier_policy
    # Projection tests seed already-verified main-topic evidence, not title hints.
    flag = session.get(AppSettingRecord, 'ai_beta_global_enabled')
    if flag is None: flag = AppSettingRecord(key='ai_beta_global_enabled')
    flag.value='true'; session.add(flag)
    from models.db import UserRecord
    user = session.get(UserRecord, condition.owner_username)
    user.ai_beta_enabled = True; session.add(user); session.commit()
    fingerprint = fingerprint or custom.evidence_snapshot(session, article)[0]
    m = custom.Match(owner_username=condition.owner_username, condition_id=condition.id,
        condition_revision=condition.revision, article_id=article.id, fingerprint=fingerprint,
        matcher_version=custom.MATCHER_VERSION, outcome=outcome,
        evidence_json=json.dumps({'method':'semantic', 'scope':'main', 'verifier_policy':current_verifier_policy(session), 'evidence':[{'kind':'text','quote':article.content}], 'reason':'Article core topic is enterprise deployment.'}),
        updated_at=NOW.isoformat())
    session.add(m)
    session.commit()
    return m


def test_full_validation_before_limit_and_batched_snapshot(store, monkeypatch):
    with Session(store.engine) as session:
        from services.interest_governance_llm import current_verifier_policy
        c = save(session)
        session.add(AppSettingRecord(key='ai_beta_global_enabled',value='true'));session.commit()
        proof=json.dumps({'method':'semantic','scope':'main','verifier_policy':current_verifier_policy(session)})
        for n in range(2005):
            a = ArticleRecord(id=f'x{n:04}', title='Enterprise on-premise Agent', source_id='rss_public',
                source_url='https://example.test/a', content_type='rss_article', content='body',
                publish_date=NOW.isoformat(), fetched_date=NOW.isoformat())
            session.add(a)
            session.flush()
            session.add(custom.Match(owner_username='alice', condition_id=c.id, condition_revision=1,
                article_id=a.id, fingerprint='current' if n >= 2000 else 'stale',
                matcher_version=custom.MATCHER_VERSION, outcome='core', evidence_json=proof, updated_at=NOW.isoformat()))
        session.commit()
        calls = []
        monkeypatch.setattr(custom, 'evidence_snapshot', lambda s, a, **kw: (calls.append(a.id) or 'current', a.content, None))
        statements = []
        listener = lambda conn, cursor, statement, params, context, many: statements.append(statement)
        event.listen(store.engine, 'before_cursor_execute', listener)
        try:
            rows = custom.current_matches(session, 'alice', limit=2, order_by_id=True)
        finally:
            event.remove(store.engine, 'before_cursor_execute', listener)
        assert [a.id for _, _, a in rows] == ['x2000', 'x2001']
        assert len(calls) == 2005 and len(set(calls)) == 2005
        assert len(statements) < 60  # chunks, not article * interest
        assert len(custom.current_matches(session, 'alice', limit=None)) == 5


@pytest.mark.parametrize('kw', [
    {'scope':'only', 'kinds':''}, {'scope':'only', 'kinds':'bogus'},
    {'scope':'off','custom_id':1,'kinds':'custom'}, {'scope':'only','custom_id':1},
    {'scope':'off','tag_id':1,'kinds':'tag'}, {'scope':'only','tag_id':1,'kinds':'custom'},
    {'scope':'only','tag_id':1,'custom_id':2,'kinds':'tag,custom'},
])
def test_explicit_parameter_matrix(kw):
    with pytest.raises(HTTPException) as exc:
        projection.parse_interest_query(**kw)
    assert exc.value.status_code == 400


def test_legacy_parameter_and_typed_identity():
    assert projection.parse_interest_query(scope='only') == {'tag'}
    assert projection.parse_interest_query(scope=' ONLY ') == {'tag'}
    assert projection.parse_interest_query(scope='legacy-ignored') == {'tag'}
    assert projection.parse_interest_query(tag_id=1) == {'tag'}
    assert projection.parse_interest_query(scope='only', kinds='tag,custom') == {'tag','custom'}


def test_custom_drilldown_current_token_and_legacy_cursor(store):
    with Session(store.engine) as session:
        c = save(session)
        for n in range(53):
            seed_match(session, c, add_article(session, f'a{n:02}'))
        first = interests.custom_interest_matches(c.id, cursor=0, match_contract=1,
            auth={'sub':'alice'}, session=session)
        assert len(first['items']) == 50 and first['total'] == 53
        next_page = interests.custom_interest_matches(c.id, cursor=first['next_cursor'],
            pagination_token=first['pagination_token'], match_contract=1, auth={'sub':'alice'}, session=session)
        assert len(next_page['items']) == 3 and next_page['next_cursor'] is None
        assert not {x['article_id'] for x in first['items']} & {x['article_id'] for x in next_page['items']}
        assert first['items'][0]['interest_matches'][0]['method'] == 'semantic'
        # Legacy cursor does not opt in to a cross-page snapshot guarantee.
        assert len(interests.custom_interest_matches(c.id, cursor=first['next_cursor'],
            auth={'sub':'alice'}, session=session)['items']) == 3
        with pytest.raises(HTTPException) as exc:
            interests.custom_interest_matches(c.id, cursor=first['next_cursor'], match_contract=1,
                auth={'sub':'alice'}, session=session)
        assert exc.value.status_code == 400
        c.revision += 1; session.add(c); session.commit()
        with pytest.raises(HTTPException) as exc:
            interests.custom_interest_matches(c.id, cursor=first['next_cursor'],
                pagination_token=first['pagination_token'], auth={'sub':'alice'}, session=session)
        assert exc.value.status_code == 409
        for target in (c.id, 999999):
            with pytest.raises(HTTPException) as exc:
                interests.custom_interest_matches(target, cursor=0, auth={'sub':'bob'}, session=session)
            assert exc.value.status_code == 404


def test_safe_evidence_historical_revocation_and_no_raw_leak():
    a = ArticleRecord(id='x', title='literal title', source_id='s', content_type='podcast_episode')
    raw = {'method':'semantic', 'reason':'secret raw prose', 'authorization':{'secret':'credential'},
           'evidence':[{'kind':'text','quote':'published words','span_id':'private anchor'},
                       {'kind':'source','quote':'secret hostname'}]}
    method, evidence = projection.safe_evidence(raw, a, ('current', 'published words', None),
        historical=True, expected_fingerprint='current')
    assert method == 'semantic' and evidence['quotes'][0]['source_kind'] == 'podcast_transcript'
    assert evidence['quotes'][0]['availability'] == 'historical'
    assert 'secret' not in json.dumps(evidence) and 'private anchor' not in json.dumps(evidence)
    _, revoked = projection.safe_evidence(raw, a, ('withdrawn', '', None), historical=True,
        expected_fingerprint='current')
    assert revoked == {'reason':'','quotes':[],'redaction':'evidence_unavailable'}
    nested = {'snapshot': {'custom_interest_evidence': raw}, 'interest_evidence': raw}
    assert projection.strip_private_evidence(nested) == {'snapshot':{}}
    assert nested['interest_evidence'] is raw  # stored/source dict never rewritten


def test_frozen_brief_name_revision_lane_and_no_current_fallback(store):
    with Session(store.engine) as session:
        c = save(session, name='Frozen name')
        a = add_article(session)
        cid = c.id
    run_core_cycle(store)
    with Session(store.engine) as session:
        generated = digest.generate_personal_digest(session,'alice',now=NOW)
        edition = generated.edition
        stored = edition.interest_snapshot_json
        row = next(r for r in generated.items if r.article_id == 'a')
        stored_item = row.ranking_features_json
        c = session.get(custom.Condition, cid)
        c.name = 'New name'; c.revision = 2; c.status = 'paused'; session.add(c); session.commit()
        payload = personal_briefs.serialize_edition(session, edition)
        match = next(i for i in payload['items'] if i['article_id'] == 'a')['interest_matches'][0]
        assert (match['name'], match['revision'], match['basis']) == ('Frozen name',1,'edition_snapshot')
        assert match['drilldown'] == {'available':False,'reason':'paused'}
        receipt = payload['custom_interest_summary'][0]
        assert receipt['name'] == 'Frozen name' and receipt['revision'] == 1
        assert receipt['matched_item_count'] == 1
        assert receipt['selected_item_count'] == (1 if row.selection_lane == 'interest' else 0)
        assert receipt['drilldown'] == {'available': False, 'reason': 'paused'}
        assert 'condition' not in receipt
        assert match['selection_role'] == ('selected_by_interest' if row.selection_lane == 'interest' else 'also_matches')
        assert edition.interest_snapshot_json == stored and row.ranking_features_json == stored_item
        item = {'snapshot':{}, 'selection_lane':'quality', 'matched_interest_codes':[]}
        features = json.loads(stored_item)
        unknown = projection.frozen_matches(session,'alice',item,features,[],session.get(ArticleRecord,'a'))[0]
        assert unknown['name'] == '兴趣入选' and unknown['selection_role'] == 'also_matches'


def test_value_selected_custom_match_keeps_topic_without_claiming_interest_promotion(store):
    with Session(store.engine) as session:
        condition = save(session, name='Enterprise deployment')
        article = add_article(session)
        seed_match(session, condition, article)
        session.add(ReaderSubscriptionRecord(
            owner_username='alice', name='Public source', token_hash='fixture-subscription',
            filters_json=json.dumps({'source_ids': 'rss_public'}), is_active=True,
            created_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        ))
        session.commit()
        result = digest.generate_personal_digest(session, 'alice', now=NOW)
        payload = personal_briefs.serialize_edition(session, result.edition)
        item = next(item for item in payload['items'] if item['article_id'] == article.id)
        assert payload['status'] == 'ready'
        assert payload['selection_stats']['interest_hits'] == 1
        assert payload['selection_stats']['interest_promoted_count'] == 0
        assert item['selection_lane'] == 'quality'
        match = item['interest_matches'][0]
        assert match['name'] == 'Enterprise deployment'
        assert match['selection_role'] == 'also_matches'
        assert match['selection_contribution']['ranking'] == 'none'
        assert payload['custom_interest_summary'][0]['matched_item_count'] == 1
        assert payload['custom_interest_summary'][0]['selected_item_count'] == 0


def test_custom_receipt_zero_acl_visibility_exact_revision_and_deduplication(store):
    with Session(store.engine) as session:
        c = save(session, name='Frozen coding')
        cid = c.id
        frozen = [{'custom_interest_id': cid, 'revision': 1, 'name': 'Frozen coding', 'condition': AST}]
        context = projection.frozen_context(session, 'alice')
        match = {'ref': {'kind': 'custom', 'id': str(cid)}, 'revision': 1,
                 'basis': 'edition_snapshot', 'outcome': 'core', 'selection_role': 'selected_by_interest'}
        item = {'article_id': 'a', 'selection_lane': 'interest', 'interest_matches': [match, match]}
        counted = projection.frozen_custom_summary(frozen, [item, item], context)[0]
        assert (counted['matched_item_count'], counted['selected_item_count']) == (1, 1)
        # The serializer supplies only readable edition items: an ACL removal
        # must not leak the hidden count via this independent summary field.
        assert projection.frozen_custom_summary(frozen, [], context)[0]['matched_item_count'] == 0
        for patch in ({'revision': 2}, {'outcome': 'mention'}, {'basis': 'current'}, {'ref': {'kind': 'tag', 'id': str(cid)}}):
            row = {**item, 'interest_matches': [{**match, **patch}]}
            assert projection.frozen_custom_summary(frozen, [row], context)[0]['matched_item_count'] == 0
        assert projection.frozen_custom_summary([], [item], context) == []
        other_owner = projection.frozen_context(session, 'bob')
        assert projection.frozen_custom_summary(frozen, [], other_owner)[0]['drilldown']['available'] is False


def test_custom_receipt_serializer_omits_guest_and_list_without_mutation(store):
    from models.db import PersonalDigestEditionRecord
    with Session(store.engine) as session:
        c = save(session)
        frozen = json.dumps([{'custom_interest_id': c.id, 'revision': 1, 'name': 'Private label'}])
        edition = PersonalDigestEditionRecord(owner_username='alice', report_date=NOW.date().isoformat(),
            revision=1, status='ready', interest_snapshot_json=frozen,
            check_after=NOW.isoformat(), cutoff_at=NOW.isoformat(),
            created_at=NOW.isoformat(), updated_at=NOW.isoformat())
        session.add(edition); session.commit(); session.refresh(edition)
        payload = personal_briefs.serialize_edition(session, edition)
        assert payload['custom_interest_summary'][0]['matched_item_count'] == 0
        assert 'custom_interest_summary' not in personal_briefs.serialize_edition(session, edition, include_items=False)
        edition.owner_username = 'guest:shared'
        with session.no_autoflush:
            assert 'custom_interest_summary' not in personal_briefs.serialize_edition(session, edition)
        assert edition.interest_snapshot_json == frozen


def test_list_detail_union_exact_and_get_pure(monkeypatch, tmp_path):
    app, sink = _setup(monkeypatch, tmp_path)
    from config import LLMConfig
    from services import interest_governance_llm as bridge
    monkeypatch.setattr(bridge, 'resolve_llm_config', lambda _: LLMConfig(base_url='https://example.com/v1',api_key='fixture',model='aux'))
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key='personal_digest_enabled',value='true'))
        c = custom.save_condition(session,'user',custom.ConditionInput(name='Same name',condition=AST))
        session.commit()
        a = session.get(ArticleRecord,'sub_plain')
        seed_match(session,c,a)
        cid = c.id
    with TestClient(app.app) as client:
        _login(client)
        # All following GETs must not mutate persisted state or invoke the verifier.
        from services import interest_governance_llm
        monkeypatch.setattr(interest_governance_llm,'make_interest_verifier',lambda *a,**kw: pytest.fail('GET requested verifier'))
        writes = []
        listener = lambda conn, cursor, statement, params, context, many: writes.append(statement) if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) else None
        event.listen(sink.engine,'before_cursor_execute',listener)
        try:
            capabilities=client.get('/api/reader/interests').json()['capabilities']
            assert capabilities == {'interest_match_projection':1, 'custom_interest_filter':True, 'match_contract':1}
            base={'interest_scope':'only','include_total':'true','with_interest':'true'}
            legacy=client.get('/api/articles',params=base).json()
            assert legacy['total']==3
            assert client.get('/api/articles',params={**base,'interest_scope':' ONLY '}).json()['total']==3
            union=client.get('/api/articles',params={**base,'interest_kinds':'tag,custom','kind_facets':'true'})
            assert union.status_code==200 and union.json()['total']==4
            assert union.headers['cache-control']=='private, no-store'
            first=client.get('/api/articles',params={**base,'interest_kinds':'custom','limit':1}).json()
            assert first['total']==1 and first['items'][0]['id']=='sub_plain'
            assert first['items'][0]['interest_matches'][0]['ref']=={'kind':'custom','id':str(cid)}
            detail=client.get('/api/articles/sub_plain',params={'with_interest':'true'}).json()
            assert detail['interest_matches']==first['items'][0]['interest_matches']
            q={**base,'interest_kinds':'tag,custom','skip':1,'limit':1}
            assert client.get('/api/articles',params=q).status_code==400
            q['pagination_token']=union.json()['pagination_token']
            # Different kind_facets is a different query identity; replay rejected.
            assert client.get('/api/articles',params=q).status_code==409
            q['kind_facets']='true'
            assert client.get('/api/articles',params=q).status_code==200
            assert not writes
        finally:
            event.remove(sink.engine,'before_cursor_execute',listener)
        assert client.post('/api/reader/mark-scope-read',params={'interest_kinds':'custom'}).status_code==400


def test_podcast_batch_fingerprint_and_response_only_withdrawal(store):
    from tests.test_podcast_text_reader import _artifact, _publication
    with Session(store.engine) as session:
        c = save(session)
        add_article(session)
        cid = c.id
    run_core_cycle(store)
    with Session(store.engine) as session:
        generated = digest.generate_personal_digest(session,'alice',now=NOW)
        row = next(r for r in generated.items if r.article_id == 'a')
        a = session.get(ArticleRecord,'a')
        a.content_type = 'podcast_episode'; session.add(a); session.commit()
        quote = 'This is published transcript.'
        artifact = _artifact('a','publisher_transcript','WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n'+quote+'\n')
        session.add(artifact); session.flush()
        publication = _publication(artifact); session.add(publication); session.commit()
        original = custom.evidence_snapshot(session,a)
        batched = custom.evidence_snapshot(session,a,assets=custom.evidence_assets(session,[a]))
        assert original == batched and quote in original[1]
        features = json.loads(row.ranking_features_json)
        features['custom_interest_evidence'][str(cid)].update(method='semantic',fingerprint=original[0],evidence_binding_version=1,
            evidence=[{'kind':'text','quote':quote}],reason='SECRET MODEL PROSE')
        row.ranking_features_json=json.dumps(features)
        snapshot=json.loads(row.snapshot_json)
        snapshot['nested']={'interest_evidence':{'quote':quote}}
        row.snapshot_json=json.dumps(snapshot)
        session.add(row);session.commit()
        before=personal_briefs.serialize_edition(session,generated.edition)
        assert quote in json.dumps(before) and 'SECRET MODEL PROSE' not in json.dumps(before)
        preserved=row.ranking_features_json
        publication.status='unpublished';publication.unpublished_at=NOW.isoformat()
        session.add(publication);session.commit()
        after=personal_briefs.serialize_edition(session,generated.edition)
        assert after['items'] and quote not in json.dumps(after)
        assert after['items'][0]['interest_matches'][0]['evidence']['redaction']=='evidence_unavailable'
        assert row.ranking_features_json == preserved


def test_cached_snapshot_exact_for_multiple_conditions(store, monkeypatch):
    with Session(store.engine) as session:
        first=save(session,'same'); second=save(session,'same')
        a=add_article(session)
        seed_match(session,first,a);seed_match(session,second,a)
        real=custom.evidence_snapshot; calls=[]
        def counted(s,a,**kw):
            calls.append(a.id);return real(s,a,**kw)
        monkeypatch.setattr(custom,'evidence_snapshot',counted)
        cache={}
        rows=custom.current_matches(session,'alice',limit=None,snapshot_cache=cache)
        items=[{'id':'a'}]
        projection.annotate_current(session,'alice',items,{},projection.InterestMap(),cache=cache)
        assert len(rows)==2 and calls==['a']
        assert len(items[0]['interest_matches'])==2
        assert len({m['ref']['id'] for m in items[0]['interest_matches']})==2
