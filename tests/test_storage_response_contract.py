"""Real HTTP routes must survive eviction before deferred response construction."""
from dataclasses import replace
import hashlib

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session

from models.db import ArticleRecord, MediaAssetRecord, ObjectBlobRecord
from services.media_store import MediaStore
from services.object_storage import ObjectStorage
from tests.test_object_storage import FakeBucket, oss_config
from tests.test_podcast_artifacts import _login, _setup_app


@pytest.fixture
def endpoint_media(monkeypatch, tmp_path):
    app, sink, _ = _setup_app(monkeypatch, tmp_path)
    bucket = FakeBucket()
    remote = ObjectStorage(sink.engine, tmp_path / "media", "media", replace(
        oss_config(), media_cache_max_mb=0, cache_min_age_seconds=0,
    ), bucket_factory=lambda _: bucket)
    store = MediaStore(sink.engine, remote.root, object_storage=remote)
    url = "https://images.example.test/route-race.png"
    body = b"immutable archived image" * 64
    digest = hashlib.sha256(body).hexdigest()
    record = MediaAssetRecord(url_hash=hashlib.sha256(url.encode()).hexdigest(), url=url,
                              content_hash=digest, ext=".png", mime="image/png", size_bytes=len(body),
                              status="cached", created_at="2026-09-16", updated_at="2026-09-16")
    path = store.file_path_for(record)
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    remote.persist(path, digest, ".png", len(body), "image/png")
    with Session(sink.engine) as session:
        article = session.get(ArticleRecord, "episode-1")
        article.content = f"![cover]({url})"
        session.add(article)
        session.add(record)
        session.commit()
        session.refresh(record)
        session.expunge(record)
    monkeypatch.setattr(app, "media_store", store)
    return app, store, remote, record, path, body, bucket


def evict_before_response(monkeypatch, store, remote, route, *, after_evict=lambda: None):
    if route == "archive":
        original = store.file_path_for
        calls = 0

        def file_path_for(record):
            nonlocal calls
            path = original(record)
            calls += 1
            if calls == 1:
                assert remote.evict_cache()["evicted_files"] == 1
                after_evict()
            return path

        monkeypatch.setattr(store, "file_path_for", file_path_for)
    else:
        original = store.get_or_fetch

        async def get_or_fetch(url, **kwargs):
            record = await original(url, **kwargs)
            assert record is not None
            assert remote.evict_cache()["evicted_files"] == 1
            after_evict()
            return record

        monkeypatch.setattr(store, "get_or_fetch", get_or_fetch)


def request(client, record, route, **kwargs):
    if route == "archive":
        return client.get(f"/api/archive/v2/media/{record.url_hash}", **kwargs)
    if route == "share":
        shared = client.post("/api/reader/articles/episode-1/share", json={"expires_in_days": 7})
        assert shared.status_code == 200, shared.text
        return client.get(f"/api/public/share/{shared.json()['token']}/media", params={"url": record.url}, **kwargs)
    return client.get("/api/media/proxy", params={"url": record.url}, **kwargs)


@pytest.mark.parametrize("route", ["archive", "proxy", "share"])
@pytest.mark.parametrize("range_header,status", [(None, 200), ("bytes=4-19", 206)])
def test_route_restores_cache_evicted_before_response(monkeypatch, endpoint_media, route, range_header, status):
    app, store, remote, record, path, body, _ = endpoint_media
    evict_before_response(monkeypatch, store, remote, route)
    with TestClient(app.app) as client:
        _login(client, "admin", "admin")
        response = request(client, record, route, headers={"Range": range_header} if range_header else {},
                           follow_redirects=False)
    assert response.status_code == status, response.text
    assert response.content == (body[4:20] if range_header else body)
    assert path.read_bytes() == body


@pytest.mark.parametrize("route", ["archive", "proxy", "share"])
def test_deferred_cloud_failure_is_retryable_and_releases_pin(monkeypatch, endpoint_media, route):
    app, store, remote, record, path, _, bucket = endpoint_media
    evict_before_response(monkeypatch, store, remote, route,
                          after_evict=lambda: setattr(bucket, "unavailable", True))
    with TestClient(app.app) as client:
        _login(client, "admin", "admin")
        response = request(client, record, route, follow_redirects=False)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert response.headers["cache-control"] == "no-store"
    assert "credential-and-signature" not in response.text
    assert not path.exists()
    with remote.pin(record.content_hash, exclusive=True, blocking=False) as acquired:
        assert acquired


@pytest.mark.parametrize("route", ["archive", "proxy", "share"])
def test_local_store_without_object_adapter_still_serves_bytes(endpoint_media, route):
    app, store, _, record, _, body, _ = endpoint_media
    store.object_storage = None
    with TestClient(app.app) as client:
        _login(client, "admin", "admin")
        response = request(client, record, route, follow_redirects=False)
    assert response.status_code == 200
    assert response.content == body


def _forget_remote_location(remote, record):
    with Session(remote.engine) as session:
        location = session.get(ObjectBlobRecord, remote.identity(record.content_hash, record.ext))
        session.delete(location)
        session.commit()


@pytest.mark.parametrize("route,status", [("archive", 404), ("proxy", 302), ("share", 302)])
def test_missing_unregistered_file_preserves_each_route_fallback(monkeypatch, endpoint_media, route, status):
    app, store, remote, record, path, _, bucket = endpoint_media
    _forget_remote_location(remote, record)
    if route == "archive":
        path.unlink()
    else:
        original = store.get_or_fetch

        async def lose_after_lookup(url, **kwargs):
            cached = await original(url, **kwargs)
            assert cached is not None
            path.unlink()  # Lost after route lookup, before deferred FileResponse.
            return cached

        monkeypatch.setattr(store, "get_or_fetch", lose_after_lookup)
    with TestClient(app.app) as client:
        _login(client, "admin", "admin")
        response = request(client, record, route, follow_redirects=False)
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "retry-after" not in response.headers
    if status == 302:
        assert response.headers["location"] == record.url
    else:
        assert response.json()["detail"] == "media file missing"
    assert bucket.reads == 0
    with remote.pin(record.content_hash, exclusive=True, blocking=False) as acquired:
        assert acquired


def test_archive_missing_file_does_not_bypass_permissions(endpoint_media):
    app, _, remote, record, path, _, bucket = endpoint_media
    _forget_remote_location(remote, record)
    path.unlink()
    endpoint = f"/api/archive/v2/media/{record.url_hash}"
    with TestClient(app.app) as client:
        assert client.get(endpoint).status_code == 401
        _login(client, "user", "user")
        assert client.get(endpoint).status_code == 403
        _login(client, "admin", "admin")
        assert client.get(endpoint).status_code == 404
        # Removing the public reference must remain a route-level 404, without
        # reaching deferred storage or its fallback.
        with Session(remote.engine) as session:
            article = session.get(ArticleRecord, "episode-1")
            article.content = "No public image references"
            session.add(article)
            session.commit()
        assert client.get(endpoint).json()["detail"] == "media asset not found"
    assert bucket.reads == 0


def test_share_missing_file_fallback_requires_live_token_and_article_url(monkeypatch, endpoint_media):
    app, store, remote, record, path, _, bucket = endpoint_media
    _forget_remote_location(remote, record)
    path.unlink()

    async def forbidden_lookup(*_args, **_kwargs):
        pytest.fail("Unauthorised share reached storage lookup")

    monkeypatch.setattr(store, "get_or_fetch", forbidden_lookup)
    with TestClient(app.app) as client:
        _login(client, "admin", "admin")
        created = client.post("/api/reader/articles/episode-1/share", json={"expires_in_days": 7}).json()
        token, share_id = created["token"], created["id"]
        endpoint = f"/api/public/share/{token}/media"
        assert client.get(endpoint, params={"url": "https://other.example/image.png"}).status_code == 404
        assert client.delete(f"/api/reader/shares/{share_id}").status_code == 200
        assert client.get(endpoint, params={"url": record.url}).status_code == 404
    assert bucket.reads == 0
