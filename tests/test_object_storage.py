"""OSS integration contracts, using real stores/API with a fake object service."""
import asyncio
import configparser
import hashlib
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import oss2
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from config_oss import OssConfig, load_oss_config
from models.db import MediaAssetRecord, ObjectBlobRecord
from services.media_store import MediaStore
from services.object_storage import ObjectStorage, ObjectStorageError, hash_file
from storage.impl.db_storage import DatabaseStorage


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.uploads = 0
        self.reads = 0
        self.unavailable = False

    def head_object(self, key):
        if self.unavailable:
            raise RuntimeError("credential-and-signature-must-not-leak")
        if key not in self.objects:
            raise oss2.exceptions.NoSuchKey(404, {}, b"", {})
        body, headers = self.objects[key]
        return SimpleNamespace(content_length=len(body), headers=headers)

    def put_object_from_file(self, key, path, headers):
        if self.unavailable:
            raise RuntimeError("credential-and-signature-must-not-leak")
        self.uploads += 1
        self.objects[key] = (Path(path).read_bytes(), headers)
        return SimpleNamespace(status=200)

    def get_object(self, key):
        self.reads += 1
        if self.unavailable:
            raise RuntimeError("credential-and-signature-must-not-leak")
        return io.BytesIO(self.objects[key][0])

    def delete_object(self, key):
        self.objects.pop(key, None)


def oss_config():
    return OssConfig(media_backend="oss", podcast_backend="oss", bucket="test-dorami",
                     region="ap-southeast-1", endpoint="https://oss-ap-southeast-1.aliyuncs.com",
                     prefix="test/writer", access_key_id="test-id", access_key_secret="test-secret",
                     minimum_free_mb=0)


def remote_store(engine, root, namespace, bucket):
    return ObjectStorage(engine, root, namespace, oss_config(), bucket_factory=lambda _: bucket)


def test_config_defaults_and_secret_environment_only(monkeypatch):
    parser = configparser.ConfigParser()
    parser.read_string("[oss]\naccess_key_secret = ignored\n")
    assert load_oss_config(parser).access_key_secret == ""
    assert not load_oss_config(parser).enabled
    monkeypatch.setenv("DORAMI_OSS_ACCESS_KEY_SECRET", "secret-env")
    assert load_oss_config(parser).access_key_secret == "secret-env"
    assert "secret-env" not in repr(load_oss_config(parser))
    with pytest.raises(ValueError):
        replace(oss_config(), endpoint="http://localhost")
    with pytest.raises(ValueError):
        replace(oss_config(), prefix="../shared")
    with pytest.raises(ValueError):
        replace(oss_config(), region="cn-shanghai")


def test_persist_dedup_restart_restore_and_exact_checksum(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'test.db'}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path, "media", bucket)
    path = tmp_path / "example.png"
    body = b"image-bytes"
    path.write_bytes(body)
    digest, size = hash_file(path)
    store.persist(path, digest, ".png", size, "image/png")
    store.persist(path, digest, ".png", size, "image/png")
    assert bucket.uploads == 1
    with Session(sink.engine) as session:
        row = session.exec(select(ObjectBlobRecord)).one()
    assert row.object_key.endswith(f"/{digest}.png")
    # Inherit the private, public-access-blocked bucket; runtime needs no ACL permission.
    assert "x-oss-object-acl" not in bucket.objects[row.object_key][1]
    path.unlink()
    restarted = remote_store(sink.engine, tmp_path, "media", bucket)
    assert restarted.materialize(path, digest, ".png", size).read_bytes() == body
    path.unlink()
    bucket.objects[row.object_key] = (b"x" * size, {})
    with pytest.raises(ObjectStorageError, match="checksum"):
        restarted.materialize(path, digest, ".png", size)
    assert not path.exists()
    assert not list(tmp_path.glob(".oss-*.part"))


def test_failed_upload_never_registers_and_exceptions_are_sanitized(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'test.db'}")
    bucket = FakeBucket()
    bucket.unavailable = True
    store = remote_store(sink.engine, tmp_path, "media", bucket)
    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    digest, size = hash_file(path)
    with pytest.raises(ObjectStorageError, match="head_failed") as exc:
        store.persist(path, digest, ".png", size, "image/png")
    assert "credential" not in str(exc.value)
    assert store.location(digest, ".png") is None


def test_registered_location_survives_prefix_change(tmp_path):
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'test.db'}")
    bucket = FakeBucket()
    store = remote_store(sink.engine, tmp_path, "media", bucket)
    path = tmp_path / "image.png"
    path.write_bytes(b"original")
    digest, size = hash_file(path)
    store.persist(path, digest, ".png", size, "image/png")
    store.config = replace(store.config, prefix="changed/prefix")
    path.unlink()
    assert store.materialize(path, digest, ".png", size).read_bytes() == b"original"
    path.unlink()
    store.config = replace(store.config, bucket="different-bucket")
    with pytest.raises(ObjectStorageError, match="location_config_mismatch"):
        store.materialize(path, digest, ".png", size)


def test_media_cold_read_does_not_fetch_origin_or_rewrite_archive(monkeypatch, tmp_path):
    from services import media_store as media_module
    from tests.test_media_store import PNG_BYTES, _public_ok
    monkeypatch.setattr(media_module, "_resolve_is_public", _public_ok)
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'test.db'}")
    bucket = FakeBucket()
    remote = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    calls = []
    def origin(request):
        calls.append(request.url)
        return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})
    store = MediaStore(sink.engine, tmp_path / "media", object_storage=remote, transport=httpx.MockTransport(origin))
    async def run():
        record = await store.get_or_fetch("https://example.com/image.png")
        assert record.status == "cached"
        store.file_path_for(record).unlink()
        assert await store.get_or_fetch(record.url)
        assert len(calls) == 1
        store.file_path_for(record).unlink()
        bucket.unavailable = True
        assert await store.get_or_fetch(record.url) is None
        assert len(calls) == 1
        with Session(sink.engine) as session:
            assert session.get(MediaAssetRecord, record.url_hash).status == "cached"
        await store.aclose()
    asyncio.run(run())


def test_audio_cold_range_head_and_withdraw(monkeypatch, tmp_path):
    from tests.test_podcast_artifacts import _setup_app, _login, _publish, WAV
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    bucket = FakeBucket()
    store.object_storage = remote_store(sink.engine, store.root, "podcast", bucket)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # Match premium_guide_tts: local generated audio has empty sync authority.
        # Manual admin imports have a writer authority and are not sync exports.
        generated = store.import_bytes(
            episode_id="episode-1", kind="digest_audio_zh", data=WAV,
            declared_mime="audio/wav", provenance="premium_guide_tts", authority_id="",
            narration_artifact_id="script-episode-1",
            narration_content_hash=hashlib.sha256("episode-1 的中文口播稿。".encode()).hexdigest(),
        )
        artifact = {"id": generated.id, "updated_at": generated.updated_at}
        assert _publish(client, artifact).status_code == 200
        record = store.get(artifact["id"])
        path = store.file_path_for(record)
        path.unlink()
        assert store._durable_blob_bytes() == len(WAV)
        url = f"/api/reader/podcast-artifacts/{record.id}/audio"
        response = client.get(url, headers={"Range": "bytes=0-9"})
        assert response.status_code == 206, response.text
        assert response.content == WAV[:10]
        assert client.head(url).headers["content-length"] == str(len(WAV))
        assert client.get(url, headers={"Range": "bytes=9999-"}).status_code == 416
        path.unlink()
        exported = client.get(f"/api/archive/v2/podcast-audio/{record.id}")
        assert exported.status_code == 200, exported.text
        assert exported.content == WAV
        store.withdraw(record.id)
        assert client.get(url).status_code == 404
        assert client.get(f"/api/archive/v2/podcast-audio/{record.id}").status_code == 404
        assert len(bucket.objects) == 1  # Revocation happens in the API, not in a signed URL.


def test_archive_sync_cold_media_to_local_receiver(monkeypatch, tmp_path):
    from tests.test_podcast_artifacts import _setup_app, _login
    from tests.test_media_store import PNG_BYTES
    from models.db import ArticleRecord
    from services.archive_sync_v2 import install_media_bytes
    app_module, producer, _ = _setup_app(monkeypatch, tmp_path)
    bucket = FakeBucket()
    remote = remote_store(producer.engine, tmp_path / "media", "media", bucket)
    media = MediaStore(producer.engine, remote.root, object_storage=remote)
    monkeypatch.setattr(app_module, "media_store", media)
    url = "https://images.example.test/immutable.png"
    key = hashlib.sha256(url.encode()).hexdigest()
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    with Session(producer.engine) as session:
        article = session.get(ArticleRecord, "episode-1")
        article.content = f"![cover]({url})"
        session.add(article)
        session.add(MediaAssetRecord(url_hash=key, url=url, content_hash=digest, ext=".png",
                    mime="image/png", size_bytes=len(PNG_BYTES), status="pending_sync",
                    created_at="2026-01-01", updated_at="2026-01-01"))
        session.commit()
    record = install_media_bytes(producer.engine, media.root, key, PNG_BYTES, object_storage=remote)
    media.file_path_for(record).unlink()
    with TestClient(app_module.app) as client:
        endpoint = f"/api/archive/v2/media/{key}"
        assert client.get(endpoint).status_code == 401
        assert bucket.reads == 0
        _login(client, "admin", "admin")
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        assert response.content == PNG_BYTES
        consumer = DatabaseStorage(f"sqlite:///{tmp_path / 'receiver.db'}")
        with Session(consumer.engine) as session:
            copy = MediaAssetRecord.model_validate(record.model_dump())
            copy.status = "pending_sync"
            copy.sync_authority_id = "producer"
            session.add(copy)
            session.commit()
        received = install_media_bytes(consumer.engine, tmp_path / "receiver-media", key, response.content)
        assert received.status == "cached"
        assert (tmp_path / "receiver-media" / digest[:2] / f"{digest}.png").read_bytes() == PNG_BYTES
        with Session(consumer.engine) as session:
            assert session.exec(select(ObjectBlobRecord)).all() == []
        media.file_path_for(record).unlink()
        bucket.unavailable = True
        assert client.get(endpoint).status_code == 503


def test_audio_remote_failure_returns_503_without_publishing(monkeypatch, tmp_path):
    from tests.test_podcast_artifacts import _setup_app, _login, _import
    app_module, sink, store = _setup_app(monkeypatch, tmp_path)
    bucket = FakeBucket()
    bucket.unavailable = True
    store.object_storage = remote_store(sink.engine, store.root, "podcast", bucket)
    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        response = _import(client)
        assert response.status_code == 503, response.text
        assert store.count() == 0
        assert "credential" not in response.text


def test_maintenance_dry_run_resume_eviction_and_restore(tmp_path):
    from services.object_storage_maintenance import execute
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'test.db'}")
    bucket = FakeBucket()
    remote = remote_store(sink.engine, tmp_path / "media", "media", bucket)
    body = b"unique-image"
    digest = hashlib.sha256(body).hexdigest()
    path = remote.root / digest[:2] / f"{digest}.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    with Session(sink.engine) as session:
        for key in ("one", "two"):
            session.add(MediaAssetRecord(url_hash=key, url="https://example.test/" + key,
                        content_hash=digest, ext=".png", mime="image/png", size_bytes=len(body),
                        status="cached", created_at="2026-01-01", updated_at="2026-01-01"))
        session.commit()
    stores = {"media": remote}
    plan = execute(sink.engine, stores)
    assert plan["objects"] == 1 and bucket.uploads == 0 and remote.location(digest, ".png") is None
    with pytest.raises(ValueError, match="offline"):
        execute(sink.engine, stores, apply=True)
    for _ in range(2):
        assert execute(sink.engine, stores, apply=True, offline=True)["errors"] == 0
    assert bucket.uploads == 1
    # Corrupt remote content with the same length must prevent eviction.
    record = remote.location(digest, ".png")
    original = bucket.objects[record.object_key]
    bucket.objects[record.object_key] = (b"x" * len(body), original[1])
    assert execute(sink.engine, stores, action="evict", apply=True, offline=True)["errors"] == 1
    assert path.exists()
    bucket.objects[record.object_key] = original
    assert execute(sink.engine, stores, action="evict", apply=True, offline=True)["errors"] == 0
    assert not path.exists()
    assert execute(sink.engine, stores, action="restore", apply=True, offline=True)["errors"] == 0
    assert path.read_bytes() == body
    # Same-size local corruption is repaired explicitly by restore, preserving evidence.
    path.write_bytes(b"z" * len(body))
    assert execute(sink.engine, stores, action="restore", apply=True, offline=True)["errors"] == 0
    assert path.read_bytes() == body
    assert list(path.parent.glob("*.corrupt-*"))


def test_gc_preserves_references_and_requires_backup_cutoff(tmp_path):
    import datetime as dt
    from services.object_storage_maintenance import execute
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'test.db'}")
    bucket = FakeBucket()
    remote = remote_store(sink.engine, tmp_path, "media", bucket)
    path = tmp_path / "orphan.png"
    path.write_bytes(b"orphan")
    digest, size = hash_file(path)
    remote.persist(path, digest, ".png", size, "image/png")
    stores = {"media": remote}
    with pytest.raises(ValueError, match="prune-before"):
        execute(sink.engine, stores, action="gc", apply=True, offline=True)
    result = execute(sink.engine, stores, action="gc", apply=True, offline=True,
                     prune_before=dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    assert result["skipped"] == 1 and bucket.objects
    result = execute(sink.engine, stores, action="gc", apply=True, offline=True,
                     prune_before=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))
    assert result["completed"] == 1 and not bucket.objects
    assert remote.location(digest, ".png") is None


def test_maintenance_cli_dry_run_does_not_create_or_change_database(tmp_path):
    import json
    import os
    import subprocess
    import sys

    database = tmp_path / "inventory.db"
    sink = DatabaseStorage(f"sqlite:///{database}")
    sink.engine.dispose()
    config = tmp_path / "maintenance.ini"
    config.write_text(
        f"[storage]\ndatabase_url = sqlite:///{database}\n"
        f"[media]\nmedia_dir = {tmp_path / 'media'}\n"
        f"[podcast_artifacts]\nroot_dir = {tmp_path / 'audio'}\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("DORAMI_")}
    env["DORAMI_CONFIG_FILE"] = str(config)
    command = [sys.executable, str(Path(__file__).parents[1] / "scripts/migrate_media_oss.py"), "upload"]
    before = database.read_bytes()
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"]["dry_run"] is True
    assert database.read_bytes() == before
    assert not (tmp_path / "media").exists()
    database.unlink()
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert not database.exists()


def test_ecs_credentials_use_token_cache_and_sanitized_failures(monkeypatch):
    import datetime as dt
    from services.oss_credentials import EcsRoleCredentialsProvider
    from services import oss_credentials
    calls = []
    now = 2000000000.0
    monkeypatch.setattr(oss_credentials.time, "time", lambda: now)
    def handler(request):
        calls.append(request)
        assert request.url.host == "100.100.100.200"
        if request.method == "PUT":
            return httpx.Response(200, text="metadata-token")
        assert request.headers["X-aliyun-ecs-metadata-token"] == "metadata-token"
        return httpx.Response(200, json={"Code": "Success", "AccessKeyId": "temporary-id",
            "AccessKeySecret": "temporary-secret", "SecurityToken": "temporary-token",
            "Expiration": dt.datetime.fromtimestamp(now + 900, dt.timezone.utc).isoformat()})
    provider = EcsRoleCredentialsProvider("DoramiRole", transport=httpx.MockTransport(handler))
    assert provider.get_credentials().get_security_token() == "temporary-token"
    provider.get_credentials()
    assert len(calls) == 2
    now += 700
    provider.get_credentials()
    assert len(calls) == 4
    now += 700
    provider.transport = httpx.MockTransport(lambda _: httpx.Response(500, text="secret-material"))
    with pytest.raises(RuntimeError, match="credentials_unavailable") as exc:
        provider.get_credentials()
    assert "secret-material" not in str(exc.value)
    assert replace(oss_config(), credential_provider="ecs_role", ecs_role_name="DoramiRole",
                   access_key_id="", access_key_secret="").enabled
