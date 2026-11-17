"""Signed reading anchors: real HTTP, SQLite publications and provider transport stub."""
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from api.tokens import AUTH_SECRET
from models.db import ArticleRecord, PodcastTextArtifactRecord, PodcastTextPublicationRecord
from services import qa_reference, source_visibility
from tests.test_qa_contract_api import setup, PARTS
from tests.test_reader_ai import _login, _seed_credentialed_source


def ask(client):
    _login(client)
    response = client.post('/api/reader/ai/ask', json={
        'scope': 'article', 'article_id': 'episode', 'question': '密钥如何处理，还需要什么限制？'})
    assert response.status_code == 200, response.text
    return response.json()


def read(client, token, episode='episode'):
    return client.get(f'/api/podcasts/episodes/{episode}/reference-context', params={'token': token})


@pytest.mark.parametrize('mode', [None, 'partial', 'bad_json'])
def test_qa_to_read_context_is_exact_and_does_not_call_model(monkeypatch, tmp_path, mode):
    body = '😀 Before context. ' * 50 + '\nExternal secrets protect credentials.\n' + 'Separate scope is still needed. ' * 60
    app, sink, calls = setup(monkeypatch, tmp_path, failure=mode, body=body)
    with TestClient(app.app) as client:
        payload = ask(client)
        source = payload['sources'][0]
        location = source['reference_locations'][0]
        assert location['origin'] == ('excerpt_start' if mode == 'bad_json' else 'selected')
        before_calls = len(calls)
        response = read(client, location['token'])
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'private, no-store'
        data = response.json()
        from services.podcast_text_reader import _plain_text
        with Session(sink.engine) as session:
            artifact = session.exec(select(PodcastTextArtifactRecord)).first()
            projected = _plain_text(artifact, app.settings.podcast, for_reading=True)
        assert data['before'] + data['focus'] + data['after'] in projected
        assert len(data['before']) <= 600 and len(data['focus']) <= 800 and len(data['after']) <= 600
        assert len(calls) == before_calls
        expected = [p['question'] for p in PARTS] if mode == 'bad_json' else [PARTS[1]['question']] if mode == 'partial' else []
        assert payload['refinement_questions'] == expected
        # Snapshot text is read from storage again, not from client strings.
        tampered = read(client, location['token'] + '0')
        assert tampered.status_code == 400 and 'focus' not in tampered.json()
        assert read(client, location['token'], 'other').status_code == 400


@pytest.mark.parametrize('change', ['withdraw', 'delete', 'republish', 'hidden', 'credentialed', 'private'])
def test_old_anchor_cannot_bypass_updated_publication_or_rights(monkeypatch, tmp_path, change):
    app, sink, calls = setup(monkeypatch, tmp_path)
    with TestClient(app.app) as client:
        token = ask(client)['sources'][0]['reference_locations'][0]['token']
        with Session(sink.engine) as session:
            article = session.get(ArticleRecord, 'episode')
            publication = session.exec(select(PodcastTextPublicationRecord)).first()
            artifact = session.get(PodcastTextArtifactRecord, publication.artifact_id)
            if change == 'withdraw':
                publication.status = 'unpublished'
                publication.unpublished_at = datetime.now(timezone.utc).isoformat()
                session.add(publication)
            elif change == 'delete':
                session.delete(article)
            elif change == 'republish':
                newer = PodcastTextArtifactRecord(**{**artifact.model_dump(), 'id': 'new-version', 'version': 2})
                session.add(newer); session.flush()
                publication.artifact_id = newer.id; session.add(publication)
            elif change == 'hidden':
                source_visibility.set_source_hidden(session, article.source_id, True)
            elif change == 'private':
                article.source_id = 'user_rss_other_reader'; session.add(article)
            session.commit()
        if change == 'credentialed':
            _seed_credentialed_source(sink.engine, 'podcast-source')
        before = len(calls)
        response = read(client, token)
        assert response.status_code in (404, 409), response.text
        assert 'focus' not in response.json() and 'External secrets' not in response.text
        assert len(calls) == before


def test_guest_can_read_public_anchor_but_anonymous_cannot(monkeypatch, tmp_path):
    app, _, _ = setup(monkeypatch, tmp_path)
    with TestClient(app.app) as client:
        token = ask(client)['sources'][0]['reference_locations'][0]['token']
        client.cookies.clear()
        assert read(client, token).status_code == 401
        assert client.post('/api/auth/guest', json={'entry':'reader'}).status_code == 200
        assert read(client, token).status_code == 200


def test_selected_late_range_not_first_repeated_text_and_no_snapshot_mutation(monkeypatch, tmp_path):
    body = 'Repeated 😀 phrase.\n' * 200 + 'LATE LIMIT.' + ' After.' * 200
    app, sink, _ = setup(monkeypatch, tmp_path, body=body)
    with Session(sink.engine) as session:
        artifact = session.exec(select(PodcastTextArtifactRecord)).first()
        fragment = {'artifact_id': artifact.id, 'content_hash': artifact.content_hash,
                    'kind': artifact.kind, 'range_start': 0, 'range_end': len(body), 'text': body}
    source = {'id': 'episode', 'evidence': [fragment]}
    start = body.index('LATE LIMIT.')
    proof = {**fragment, 'source': 1, 'range_start': start, 'range_end': start + len('LATE LIMIT.')}
    state = SimpleNamespace(checked_parts=[{'evidence': [proof, proof]}])
    result = qa_reference.reference_sources([source], state, secret=AUTH_SECRET, accepted=True)
    assert 'reference_locations' not in source and len(result[0]['reference_locations']) == 1
    with TestClient(app.app) as client:
        _login(client)
        response = read(client, result[0]['reference_locations'][0]['token'])
        assert response.status_code == 200, response.text
        assert response.json()['focus'] == 'LATE LIMIT.'
        assert response.json()['before'] == body[start-600:start]
        assert response.json()['after'] == body[start+len('LATE LIMIT.'):start+len('LATE LIMIT.')+600]


def test_reference_selection_cap_and_rejected_answer_has_no_selected_locations():
    text = 'x' * 10000
    f = {'artifact_id': 'a', 'content_hash': 'f'*64, 'kind': 'publisher_transcript',
         'range_start': 0, 'range_end': len(text), 'text': text}
    proofs = [{**f, 'source': 1, 'range_start': n*800, 'range_end': (n+1)*800} for n in range(10)]
    state = SimpleNamespace(checked_parts=[{'evidence': proofs}])
    normal = qa_reference.reference_sources([{'id': 'e', 'evidence': [f]}], state, secret='test', accepted=True)
    assert len(normal[0]['reference_locations']) == 4
    fallback = qa_reference.reference_sources([{'id': 'e', 'evidence': [f]}], state, secret='test', accepted=False)
    assert len(fallback[0]['reference_locations']) == 1
    assert fallback[0]['reference_locations'][0]['origin'] == 'excerpt_start'
    assert qa_reference.reference_sources([{'id': 'article'}], state, secret='test', accepted=True) == [{'id': 'article'}]


def test_refinements_are_bounded_missing_parts_not_repeated_original():
    state = SimpleNamespace(question='whole', checked_parts=[{'id':'q2', 'status':'partial'}], parts=[
        {'id':'q1','question':'answered'}, {'id':'q2','question':'missing'}, {'id':'q3','question':'whole'},
        {'id':'q4','question':'missing'}, {'id':'q5','question':'x'*1001}])
    assert qa_reference.refinement_questions(state, 'partial') == ['missing']
    assert qa_reference.refinement_questions(state, 'answer') == []
    assert qa_reference.refinement_questions(state, 'references_only') == ['answered', 'missing']


def test_word_json_real_qa_anchor_uses_the_index_reading_projection(monkeypatch, tmp_path):
    import json
    import hashlib
    from services.podcast_text_reader import _plain_text
    app, sink, calls = setup(monkeypatch, tmp_path)
    words = 'External secrets protect credentials but separate scope is still needed 😀 for each team'.split()
    raw = json.dumps({'segments': [{'startTime': i * 0.2, 'endTime': (i + 1) * 0.2,
                                   'speaker': 'Speaker 1', 'body': word} for i, word in enumerate(words)]})
    with Session(sink.engine) as session:
        old = session.exec(select(PodcastTextArtifactRecord)).first()
        artifact = PodcastTextArtifactRecord(**{**old.model_dump(), 'id': 'word-json', 'version': 2,
            'inline_text': raw, 'content_hash': hashlib.sha256(raw.encode()).hexdigest(),
            'provenance_json': '{"format":"json"}'})
        session.add(artifact); session.flush()
        pub = session.exec(select(PodcastTextPublicationRecord)).first()
        pub.artifact_id = artifact.id; session.add(pub); session.commit()
        reading = _plain_text(artifact, app.settings.podcast, for_reading=True)
        assert reading != _plain_text(artifact, app.settings.podcast)
    with TestClient(app.app) as client:
        source = ask(client)['sources'][0]
        evidence = source['evidence'][0]
        assert evidence['artifact_id'] == 'word-json'
        assert evidence['text'] == reading[evidence['range_start']:evidence['range_end']]
        response = read(client, source['reference_locations'][0]['token'])
        assert response.status_code == 200, response.text
        assert response.json()['focus'] == reading.strip()
        assert len(calls) == 2
