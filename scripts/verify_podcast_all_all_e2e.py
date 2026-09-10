#!/usr/bin/env python3
"""Verify Issue #7's Podcast text/audio slice across two real ``role=all`` nodes.

This operator smoke test deliberately does not bill real providers. It seeds the
published text and derived-audio outputs that an external provider run produces,
caches one publisher-audio fixture through the external HTTP API, transfers the
published guide through the real Archive Sync v3 HTTP contract, then checks that
the internal Reader can serve both text and audio. Source audio remains
external-only across sync and process restarts.

Every database, config, media directory, and Podcast CAS directory is created
under a fresh OS temporary directory.  The safety guard is public so pytest can
prove that a repository/production path cannot be substituted accidentally.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy import text as sql_text
from sqlmodel import Session, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleRecord,
    JobRecord,
    MediaAssetRecord,
    PodcastArtifactRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
    TaxonomyVersionRecord,
)
from services import archive_sync_v2  # noqa: E402
from services import accounts as accounts_service  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import ensure_migrated  # noqa: E402


STAMP = "2026-09-05T12:00:00+00:00"
EXTERNAL_AUTHORITY = "podcast-text-e2e-external"
INTERNAL_AUTHORITY = "podcast-text-e2e-internal"
SOURCE_ID = "podcast-text-e2e-show"
EPISODE_ID = "podcast-text-e2e-episode"
AUDIO_URL = "https://audio.example.test/podcast-text-e2e-original.mp3"
DIGEST_AUDIO_ID = "podcast-text-e2e-digest-audio"
SOURCE_AUDIO_TTL_SECONDS = 604800
EXPECTED_STREAMS = (
    "sources",
    "taxonomy",
    "articles",
    "analyses",
    "media",
    "podcast_texts",
    "podcast_audio",
    "source_states",
)
TEXT_FIXTURES = {
    "publisher_transcript": "Welcome to the source transcript.",
    "transcript_zh": "欢迎阅读完整的中文逐字稿。",
    "digest_blog_zh": "这是一份保留出处和事实边界的中文精华博客。",
    "narration_script_zh": "欢迎收听本期中文精华版播客。",
}
_CHILD_ENV_PASSTHROUGH = frozenset(
    {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "WINDIR",
    }
)


def _source_audio_fixture(samples: int = 80) -> bytes:
    """Return a tiny valid PCM WAV without requiring a fixture download."""

    pcm = b"\x00\x00" * samples
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8_000, 16_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


SOURCE_AUDIO_FIXTURE = _source_audio_fixture()


_FIXTURE_SERVER_BOOTSTRAP = r"""
import os
import runpy
from pathlib import Path

import httpx

from api.routers import podcasts
from services import http_safety

payload = Path(os.environ["DORAMI_E2E_SOURCE_AUDIO_FIXTURE"]).read_bytes()
request_log = Path(os.environ["DORAMI_E2E_SOURCE_AUDIO_REQUEST_LOG"])
real_async_client = httpx.AsyncClient
real_cache_source_audio = podcasts.cache_source_audio


async def fixture_resolver(host):
    assert host == "audio.example.test", host
    return ["93.184.216.34"]


def fixture_handler(request):
    assert request.method == "GET"
    # The downloader validates the logical hostname through the patched public
    # resolver, while httpx keeps that hostname on the request URL.  Pinning the
    # transport-visible URL to the resolver's IP made this fixture depend on an
    # implementation detail that the production downloader does not promise.
    assert request.url.host == "audio.example.test", request.url
    assert request.headers["Host"] == "audio.example.test"
    with request_log.open("a", encoding="ascii") as handle:
        handle.write("request\n")
    return httpx.Response(
        200,
        headers={"Content-Type": "audio/wav", "Content-Length": str(len(payload))},
        stream=httpx.ByteStream(payload),
    )


def fixture_client(*args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(fixture_handler)
    kwargs["follow_redirects"] = False
    return real_async_client(*args, **kwargs)


async def fixture_cache_source_audio(*args, **kwargs):
    kwargs["client_factory"] = fixture_client
    return await real_cache_source_audio(*args, **kwargs)


http_safety._default_public_resolver = fixture_resolver
podcasts.cache_source_audio = fixture_cache_source_audio
runpy.run_path(os.environ["DORAMI_E2E_MAIN"], run_name="__main__")
"""


def assert_isolated_e2e_paths(root: Path, *paths: Path) -> None:
    """Reject repository, symlinked, or out-of-root storage targets.

    The verifier never accepts a database URL from the application config.  This
    additional boundary protects future CLI refactors from pointing it at a real
    Dorami database or media/CAS directory.
    """

    resolved_root = root.resolve()
    resolved_project = PROJECT_ROOT.resolve()
    if root.is_symlink():
        raise RuntimeError("Podcast E2E root must not be a symlink")
    if resolved_root == resolved_project or resolved_project in resolved_root.parents:
        raise RuntimeError("Podcast E2E root must be outside the repository")
    for path in paths:
        resolved = path.resolve()
        if path.is_symlink():
            raise RuntimeError(f"Podcast E2E path must not be a symlink: {path}")
        if resolved == resolved_root or resolved_root not in resolved.parents:
            raise RuntimeError(f"Podcast E2E path escapes its temporary root: {path}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(
    path: Path,
    *,
    db_path: Path,
    media_root: Path,
    artifact_root: Path,
    port: int,
    installation: str,
    authority_id: str,
    stages: tuple[str, ...],
    ffprobe_binary: Path,
) -> None:
    path.write_text(
        "\n".join(
            (
                "[server]",
                "host = 127.0.0.1",
                f"port = {port}",
                "reload = false",
                "",
                "[runtime]",
                "role = all",
                "",
                "[taxonomy]",
                "deployment = manual",
                "",
                "[network]",
                "disable_ca_bundle = false",
                "hf_endpoint =",
                "",
                "[proxy]",
                "http_proxy =",
                "https_proxy =",
                "no_proxy = 127.0.0.1,localhost",
                "",
                "[auth]",
                f"cookie_name = dorami_podcast_text_e2e_{port}",
                f"secret = podcast-text-e2e-only-secret-{port}",
                "cookie_secure = false",
                "",
                "[storage]",
                f"database_url = sqlite:///{db_path}",
                "",
                "[cors]",
                "allow_origins = *",
                "allow_credentials = true",
                "allow_methods = *",
                "allow_headers = *",
                "",
                "[media]",
                "enabled = true",
                f"media_dir = {media_root}",
                "max_file_mb = 20",
                "timeout_seconds = 5",
                "prefetch_concurrency = 1",
                "",
                "[podcast]",
                f"installation = {installation}",
                f"authority_id = {authority_id}",
                f"allowed_stages = {','.join(stages)}",
                "feed_max_bytes = 1048576",
                "feed_timeout_seconds = 5",
                "",
                "[podcast_artifacts]",
                f"root_dir = {artifact_root}",
                "max_audio_mb = 16",
                "total_quota_bytes = 67108864",
                "minimum_free_bytes = 0",
                "upload_timeout_seconds = 5",
                "download_timeout_seconds = 5",
                "download_max_redirects = 2",
                f"source_audio_ttl_seconds = {SOURCE_AUDIO_TTL_SECONDS}",
                "source_audio_quota_bytes = 16777216",
                f"ffprobe_binary = {ffprobe_binary}",
                "probe_timeout_seconds = 5",
                "orphan_grace_seconds = 0",
                "staging_ttl_seconds = 0",
                "",
                # No provider credential is present. A successful Reader request
                # therefore proves that reading synchronized content is not a
                # hidden trigger for an LLM/ASR/TTS call.
                "[llm]",
                "base_url =",
                "api_key =",
                "model =",
                "",
            )
        ),
        encoding="utf-8",
    )


def _storage(path: Path) -> DatabaseStorage:
    url = f"sqlite:///{path}"
    ensure_migrated(url)
    return DatabaseStorage(db_url=url)


def _source() -> SourceConfigRecord:
    return SourceConfigRecord(
        source_id=SOURCE_ID,
        name="Podcast Text E2E Show",
        source_type="podcast",
        url="https://feeds.example.test/podcast-text-e2e.xml",
        category="podcast",
        fetcher_id="generic_podcast_rss",
        owner_username="",
        ai_analysis_enabled=True,
        is_active=True,
        params_json="{}",
        created_at=STAMP,
        updated_at=STAMP,
    )


def _episode() -> ArticleRecord:
    return ArticleRecord(
        id=EPISODE_ID,
        title="Podcast Text E2E Episode",
        content_type="podcast_episode",
        source_id=SOURCE_ID,
        source_url="https://podcast.example.test/episodes/text-e2e",
        publish_date=STAMP,
        fetched_date=STAMP,
        archive_updated_at=STAMP,
        has_content=True,
        content="Publisher show notes; reading this must not call a provider.",
        extensions_json=json.dumps(
            {
                "audio_url": AUDIO_URL,
                "enclosure_url": AUDIO_URL,
                "audio_mime": "audio/wav",
                "audio_bytes": len(SOURCE_AUDIO_FIXTURE),
                "duration_seconds": 3600,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _seed_external(db_path: Path, artifact_root: Path) -> None:
    storage = _storage(db_path)
    try:
        with Session(storage.engine) as session:
            session.add(_source())
            session.add(_episode())
            session.add(
                TaxonomyVersionRecord(
                    version=1,
                    status="active",
                    change_summary="Podcast text E2E empty catalog",
                    activated_by="podcast-text-e2e",
                    activated_at=STAMP,
                    created_at=STAMP,
                )
            )
            session.add(AppSettingRecord(key="taxonomy:sync_revision", value="1"))
            session.flush()
            previous_id: str | None = None
            previous_hash: str | None = None
            for version, (kind, value) in enumerate(TEXT_FIXTURES.items(), start=1):
                artifact_id = f"podcast-text-e2e-{kind}"
                content_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
                artifact = PodcastTextArtifactRecord(
                    id=artifact_id,
                    episode_id=EPISODE_ID,
                    kind=kind,
                    version=1,
                    content_hash=content_hash,
                    inline_text=value,
                    language="en" if kind == "publisher_transcript" else "zh-CN",
                    authority_id="",
                    source_artifact_id=previous_id,
                    source_content_hash=previous_hash,
                    provenance_json=json.dumps(
                        {
                            "fixture": True,
                            "pipeline": "podcast-text-e2e-v1",
                            "sequence": version,
                            "provider": "none",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    created_at=STAMP,
                )
                session.add(artifact)
                session.flush()
                session.add(
                    PodcastTextPublicationRecord(
                        identity=f"{EPISODE_ID}:{kind}",
                        episode_id=EPISODE_ID,
                        kind=kind,
                        artifact_id=artifact_id,
                        status="published",
                        authority_id="",
                        published_at=STAMP,
                        updated_at=STAMP,
                    )
                )
                previous_id = artifact_id
                previous_hash = content_hash
            session.flush()
            digest_hash = hashlib.sha256(SOURCE_AUDIO_FIXTURE).hexdigest()
            session.add(
                PodcastArtifactRecord(
                    id=DIGEST_AUDIO_ID,
                    episode_id=EPISODE_ID,
                    kind="digest_audio_zh",
                    content_hash=digest_hash,
                    mime="audio/wav",
                    ext=".wav",
                    size_bytes=len(SOURCE_AUDIO_FIXTURE),
                    duration_seconds=0.01,
                    status="published",
                    provenance="premium_guide_tts",
                    authority_id="",
                    narration_artifact_id=previous_id,
                    narration_content_hash=previous_hash,
                    created_at=STAMP,
                    updated_at=STAMP,
                    published_at=STAMP,
                )
            )
            session.commit()
        digest_path = (
            artifact_root
            / hashlib.sha256(SOURCE_AUDIO_FIXTURE).hexdigest()[:2]
            / f"{hashlib.sha256(SOURCE_AUDIO_FIXTURE).hexdigest()}.wav"
        )
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        digest_path.write_bytes(SOURCE_AUDIO_FIXTURE)
    finally:
        storage.engine.dispose()


def _seed_internal_reader(db_path: Path) -> None:
    storage = _storage(db_path)
    try:
        assert accounts_service.seed_root_admin_if_empty(storage.engine) is True
        with Session(storage.engine) as session:
            accounts_service.create_user(
                session,
                "podcast-text-reader",
                "podcast-text-reader-password",
                "user",
            )
            session.commit()
    finally:
        storage.engine.dispose()


def _child_environment(
    *,
    config: Path,
    archive_authority: str,
    installation: str,
    stages: tuple[str, ...],
    artifact_root: Path,
    source_audio_fixture: Path | None = None,
    source_audio_request_log: Path | None = None,
) -> dict[str, str]:
    # Start from a small runtime allowlist. A denylist is unsafe here because a
    # newly added provider credential could silently flow into this zero-call
    # E2E child and turn a future regression into a real paid request.
    env = {
        key: value for key, value in os.environ.items() if key in _CHILD_ENV_PASSTHROUGH
    }
    env.update(
        {
            "DORAMI_CONFIG_FILE": str(config),
            "DORAMI_ARCHIVE_AUTHORITY_ID": archive_authority,
            "DORAMI_RUNTIME_ROLE": "all",
            "DORAMI_MEDIA_ENABLED": "true",
            "DORAMI_PODCAST_INSTALLATION": installation,
            "DORAMI_PODCAST_AUTHORITY_ID": archive_authority,
            "DORAMI_PODCAST_ALLOWED_STAGES": ",".join(stages),
            "DORAMI_PODCAST_ARTIFACT_ROOT_DIR": str(artifact_root),
            "DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_BYTES": "67108864",
            "DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_BYTES": "0",
            "PYTHONPATH": str(SRC_DIR),
        }
    )
    if source_audio_fixture is not None:
        if source_audio_request_log is None:
            raise ValueError("source audio fixture requires a request log")
        env.update(
            {
                "DORAMI_E2E_SOURCE_AUDIO_FIXTURE": str(source_audio_fixture),
                "DORAMI_E2E_SOURCE_AUDIO_REQUEST_LOG": str(source_audio_request_log),
                "DORAMI_E2E_MAIN": str(SRC_DIR / "main.py"),
            }
        )
    return env


def _start_server(
    root: Path,
    *,
    name: str,
    config: Path,
    base_url: str,
    archive_authority: str,
    installation: str,
    stages: tuple[str, ...],
    artifact_root: Path,
    source_audio_fixture: Path | None = None,
    source_audio_request_log: Path | None = None,
    attempt: int = 1,
) -> subprocess.Popen:
    log_path = root / f"{name}-{attempt}.log"
    with log_path.open("w", encoding="utf-8") as log:
        command = [sys.executable, str(SRC_DIR / "main.py")]
        if source_audio_fixture is not None:
            command = [sys.executable, "-c", _FIXTURE_SERVER_BOOTSTRAP]
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=_child_environment(
                config=config,
                archive_authority=archive_authority,
                installation=installation,
                stages=stages,
                artifact_root=artifact_root,
                source_audio_fixture=source_audio_fixture,
                source_audio_request_log=source_audio_request_log,
            ),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"{name} exited ({process.returncode}):\n"
                f"{log_path.read_text(errors='replace')}"
            )
        try:
            response = httpx.get(f"{base_url}/api/auth/session", timeout=1)
            if response.status_code == 200:
                ready_client = _login(base_url)
                try:
                    runtime_response = ready_client.get("/api/runtime")
                    runtime_response.raise_for_status()
                    runtime = runtime_response.json()
                    assert runtime["role"] == "all"
                    assert runtime["collector_enabled"] is True
                    assert runtime["reader_enabled"] is True
                    assert runtime["llm_configured"] is False
                finally:
                    ready_client.close()
                return process
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(
        f"{name} did not become ready:\n{log_path.read_text(errors='replace')}"
    )


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _login(base_url: str) -> httpx.Client:
    client = httpx.Client(base_url=base_url, timeout=10)
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    response.raise_for_status()
    return client


def _login_reader(base_url: str) -> httpx.Client:
    client = httpx.Client(base_url=base_url, timeout=10)
    response = client.post(
        "/api/auth/login",
        json={
            "username": "podcast-text-reader",
            "password": "podcast-text-reader-password",
        },
    )
    response.raise_for_status()
    return client


def _run_sync(client: httpx.Client, producer_url: str) -> dict:
    response = client.post(
        "/api/admin/remote-sync/start",
        json={
            "base_url": producer_url,
            "username": "admin",
            "password": "admin",
            "page_size": 1,
            "protocol": "v2",
        },
    )
    response.raise_for_status()
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job_response = client.get(f"/api/jobs/{job_id}")
        job_response.raise_for_status()
        job = job_response.json()
        if job["status"] == "succeeded":
            return job["result"]
        if job["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"remote sync job {job_id} failed: {job.get('error')}")
        time.sleep(0.2)
    raise RuntimeError(f"remote sync job {job_id} did not finish")


def _checkpoint_identity(
    status: dict, producer_url: str
) -> dict[str, tuple[str, str, str]]:
    streams = status["state"]["targets"][producer_url]["v2_streams"]
    return {
        stream: (
            str(checkpoint["authority_id"]),
            str(checkpoint["snapshot"]),
            str(checkpoint["cursor"]),
        )
        for stream, checkpoint in streams.items()
    }


def _cache_external_source_audio(
    client: httpx.Client,
    *,
    expected_artifact_id: str | None = None,
) -> dict:
    response = client.post(
        f"/api/admin/podcast-episodes/{EPISODE_ID}/cache-source-audio"
    )
    response.raise_for_status()
    artifact = response.json()
    assert artifact["kind"] == "source_audio"
    assert artifact["status"] == "ready"
    assert artifact["retention_state"] == "temporary"
    assert (
        artifact["source_locator_hash"]
        == hashlib.sha256(AUDIO_URL.encode("utf-8")).hexdigest()
    )
    assert artifact["content_hash"] == hashlib.sha256(SOURCE_AUDIO_FIXTURE).hexdigest()
    created_at = dt.datetime.fromisoformat(artifact["created_at"])
    expires_at = dt.datetime.fromisoformat(artifact["expires_at"])
    assert expires_at > dt.datetime.now(dt.timezone.utc)
    assert (
        SOURCE_AUDIO_TTL_SECONDS
        <= (expires_at - created_at).total_seconds()
        <= (SOURCE_AUDIO_TTL_SECONDS + 1)
    )
    assert artifact["expired_at"] is None
    if expected_artifact_id is not None:
        assert artifact["id"] == expected_artifact_id

    audio = client.get(f"/api/admin/podcast-artifacts/{artifact['id']}/audio")
    audio.raise_for_status()
    assert audio.content == SOURCE_AUDIO_FIXTURE
    assert audio.headers["etag"] == f'"{artifact["content_hash"]}"'

    stats = client.get("/api/admin/podcast-artifacts/stats")
    stats.raise_for_status()
    storage_status = stats.json()
    assert storage_status["source_audio_bytes"] == len(SOURCE_AUDIO_FIXTURE)
    assert storage_status["expired_source_audio"] == 0
    assert storage_status["source_audio_due"] == 0
    return artifact


def _assert_external_source_audio(
    db_path: Path,
    artifact_root: Path,
    *,
    artifact_id: str,
) -> None:
    expected_hash = hashlib.sha256(SOURCE_AUDIO_FIXTURE).hexdigest()
    storage = DatabaseStorage(db_url=f"sqlite:///{db_path}")
    try:
        with Session(storage.engine) as session:
            artifacts = session.exec(
                select(PodcastArtifactRecord).where(
                    PodcastArtifactRecord.kind == "source_audio"
                )
            ).all()
            assert len(artifacts) == 1
            artifact = artifacts[0]
            assert artifact.id == artifact_id
            assert artifact.episode_id == EPISODE_ID
            assert artifact.kind == "source_audio"
            assert artifact.status == "ready"
            assert artifact.content_hash == expected_hash
            assert artifact.mime == "audio/wav"
            assert artifact.size_bytes == len(SOURCE_AUDIO_FIXTURE)
            assert (
                artifact.source_locator_hash
                == hashlib.sha256(AUDIO_URL.encode("utf-8")).hexdigest()
            )
            assert artifact.expires_at is not None
            assert dt.datetime.fromisoformat(artifact.expires_at) > dt.datetime.now(
                dt.timezone.utc
            )
            assert artifact.expired_at is None
            assert artifact.provenance == "publisher_enclosure_cache"
            assert artifact.authority_id == EXTERNAL_AUTHORITY
        blob = artifact_root / expected_hash[:2] / f"{expected_hash}.wav"
        assert blob.read_bytes() == SOURCE_AUDIO_FIXTURE
    finally:
        storage.engine.dispose()


def _assert_internal_database(db_path: Path, artifact_root: Path) -> None:
    storage = DatabaseStorage(db_url=f"sqlite:///{db_path}")
    try:
        with Session(storage.engine) as session:
            episode = session.get(ArticleRecord, EPISODE_ID)
            assert episode is not None
            extensions = json.loads(episode.extensions_json)
            assert extensions["audio_url"] == AUDIO_URL
            assert extensions["enclosure_url"] == AUDIO_URL
            publications = session.exec(
                select(PodcastTextPublicationRecord).where(
                    PodcastTextPublicationRecord.episode_id == EPISODE_ID
                )
            ).all()
            artifacts = session.exec(
                select(PodcastTextArtifactRecord).where(
                    PodcastTextArtifactRecord.episode_id == EPISODE_ID
                )
            ).all()
            assert {row.kind for row in publications} == set(TEXT_FIXTURES)
            assert {row.kind for row in artifacts} == set(TEXT_FIXTURES)
            by_id = {row.id: row for row in artifacts}
            for publication in publications:
                artifact = by_id[publication.artifact_id]
                expected = TEXT_FIXTURES[publication.kind]
                assert publication.status == "published"
                assert publication.authority_id == EXTERNAL_AUTHORITY
                assert artifact.authority_id == EXTERNAL_AUTHORITY
                assert artifact.inline_text == expected
                assert (
                    artifact.content_hash
                    == hashlib.sha256(expected.encode("utf-8")).hexdigest()
                )
                provenance = json.loads(artifact.provenance_json)
                assert provenance["fixture"] is True
                assert provenance["pipeline"] == "podcast-text-e2e-v1"
                assert provenance["provider"] == "none"

            # Publisher source audio remains an external-only temporary cache;
            # the published Chinese guide audio is replicated into internal CAS.
            audio = session.get(PodcastArtifactRecord, DIGEST_AUDIO_ID)
            assert audio is not None
            assert audio.kind == "digest_audio_zh"
            assert audio.status == "published"
            assert audio.authority_id == EXTERNAL_AUTHORITY
            assert (
                audio.content_hash == hashlib.sha256(SOURCE_AUDIO_FIXTURE).hexdigest()
            )
            assert (
                session.exec(
                    select(PodcastArtifactRecord).where(
                        PodcastArtifactRecord.kind == "source_audio"
                    )
                ).all()
                == []
            )
            assert (
                session.exec(
                    select(MediaAssetRecord).where(MediaAssetRecord.url == AUDIO_URL)
                ).first()
                is None
            )
            digest_path = (
                artifact_root
                / audio.content_hash[:2]
                / f"{audio.content_hash}{audio.ext}"
            )
            assert digest_path.read_bytes() == SOURCE_AUDIO_FIXTURE

            table_names = {
                row[0]
                for row in session.exec(
                    sql_text("SELECT name FROM sqlite_master WHERE type='table'")
                ).all()
            }
            provider_attempts = 0
            if "podcast_stage_attempts" in table_names:
                provider_attempts = int(
                    session.exec(
                        sql_text("SELECT COUNT(*) FROM podcast_stage_attempts")
                    ).one()[0]
                )
            assert provider_attempts == 0
            remote_jobs = session.exec(
                select(JobRecord).where(JobRecord.type == "remote_archive_sync")
            ).all()
            assert remote_jobs and all(job.status == "succeeded" for job in remote_jobs)
    finally:
        storage.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    if not __debug__:
        raise RuntimeError("do not run the Podcast E2E verifier with python -O")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep", action="store_true", help="Keep the temporary environment"
    )
    args = parser.parse_args(argv)

    root = Path(tempfile.mkdtemp(prefix="dorami-podcast-text-e2e-"))
    external_db = root / "external.db"
    internal_db = root / "internal.db"
    external_media = root / "external-media"
    internal_media = root / "internal-media"
    external_artifacts = root / "external-podcast-artifacts"
    internal_artifacts = root / "internal-podcast-artifacts"
    external_config = root / "external.ini"
    internal_config = root / "internal.ini"
    source_audio_fixture = root / "publisher-source-audio.wav"
    source_audio_request_log = root / "publisher-source-audio.requests"
    ffprobe_binary = root / "ffprobe-fixture"
    assert_isolated_e2e_paths(
        root,
        external_db,
        internal_db,
        external_media,
        internal_media,
        external_artifacts,
        internal_artifacts,
        external_config,
        internal_config,
        source_audio_fixture,
        source_audio_request_log,
        ffprobe_binary,
    )

    external_port = _free_port()
    internal_port = _free_port()
    while internal_port == external_port:
        internal_port = _free_port()
    external_url = f"http://127.0.0.1:{external_port}"
    internal_url = f"http://127.0.0.1:{internal_port}"
    external_stages = (
        "fetch",
        "asr",
        "translate",
        "analyze",
        "digest",
        "script",
        "tts",
        "audio_qa",
        "local_publish",
    )
    internal_stages: tuple[str, ...] = ()
    processes: list[subprocess.Popen] = []
    passed = False
    try:
        source_audio_fixture.write_bytes(SOURCE_AUDIO_FIXTURE)
        ffprobe_binary.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "print(json.dumps({'streams': [{'codec_type': 'audio', "
            "'duration': '0.010'}], 'format': {'duration': '0.010'}}))\n",
            encoding="utf-8",
        )
        ffprobe_binary.chmod(0o700)
        _write_config(
            external_config,
            db_path=external_db,
            media_root=external_media,
            artifact_root=external_artifacts,
            port=external_port,
            installation="external",
            authority_id=EXTERNAL_AUTHORITY,
            stages=external_stages,
            ffprobe_binary=ffprobe_binary,
        )
        _write_config(
            internal_config,
            db_path=internal_db,
            media_root=internal_media,
            artifact_root=internal_artifacts,
            port=internal_port,
            installation="internal",
            authority_id=INTERNAL_AUTHORITY,
            stages=internal_stages,
            ffprobe_binary=ffprobe_binary,
        )
        _seed_external(external_db, external_artifacts)
        _seed_internal_reader(internal_db)

        external_process = _start_server(
            root,
            name="external",
            config=external_config,
            base_url=external_url,
            archive_authority=EXTERNAL_AUTHORITY,
            installation="external",
            stages=external_stages,
            artifact_root=external_artifacts,
            source_audio_fixture=source_audio_fixture,
            source_audio_request_log=source_audio_request_log,
        )
        processes.append(external_process)
        internal_process = _start_server(
            root,
            name="internal",
            config=internal_config,
            base_url=internal_url,
            archive_authority=INTERNAL_AUTHORITY,
            installation="internal",
            stages=internal_stages,
            artifact_root=internal_artifacts,
        )
        processes.append(internal_process)

        external_admin = _login(external_url)
        try:
            source_artifact = _cache_external_source_audio(external_admin)
            # A second request must reuse the live locator without another
            # publisher transfer.
            replay = _cache_external_source_audio(
                external_admin, expected_artifact_id=source_artifact["id"]
            )
            assert replay["id"] == source_artifact["id"]
            assert source_audio_request_log.read_text(
                encoding="ascii"
            ).splitlines() == ["request"]
        finally:
            external_admin.close()
        _assert_external_source_audio(
            external_db,
            external_artifacts,
            artifact_id=source_artifact["id"],
        )

        internal_admin = _login(internal_url)
        try:
            probe = internal_admin.post(
                "/api/admin/remote-sync/test",
                json={
                    "base_url": external_url,
                    "username": "admin",
                    "password": "admin",
                    "protocol": "v2",
                },
            )
            probe.raise_for_status()
            probe_data = probe.json()
            assert probe_data["schema_version"] == archive_sync_v2.SCHEMA_VERSION
            assert probe_data["authority_id"] == EXTERNAL_AUTHORITY
            assert (
                archive_sync_v2.PODCAST_TEXT_PUBLICATIONS_CAPABILITY
                in probe_data["capabilities"]
            )
            assert (
                archive_sync_v2.PODCAST_AUDIO_PUBLICATIONS_CAPABILITY
                in probe_data["capabilities"]
            )

            first = _run_sync(internal_admin, external_url)
            assert tuple(first["streams"]) == EXPECTED_STREAMS
            assert first["streams"]["podcast_texts"]["count"] == len(TEXT_FIXTURES)
            assert first["streams"]["podcast_texts"]["pages"] == len(TEXT_FIXTURES)
            assert first["streams"]["podcast_audio"]["count"] == 1
            assert first["streams"]["podcast_audio"]["podcast_audio_downloaded"] == 1

            reader = _login_reader(internal_url)
            try:
                listing = reader.get(
                    "/api/articles",
                    params={
                        "shape": "podcast",
                        "source_id": SOURCE_ID,
                        "include_content": "true",
                    },
                )
                listing.raise_for_status()
                assert [row["id"] for row in listing.json()] == [EPISODE_ID]
                detail = reader.get(f"/api/articles/{EPISODE_ID}")
                detail.raise_for_status()
                assert detail.json()["content"].startswith("Publisher show notes")
                audio = reader.get(
                    f"/api/reader/podcast-artifacts/{DIGEST_AUDIO_ID}/audio"
                )
                audio.raise_for_status()
                assert audio.content == SOURCE_AUDIO_FIXTURE
            finally:
                reader.close()

            second = _run_sync(internal_admin, external_url)
            assert tuple(second["streams"]) == EXPECTED_STREAMS
            assert all(value["count"] == 0 for value in second["streams"].values())
            _assert_internal_database(internal_db, internal_artifacts)
            before_restart = _checkpoint_identity(
                internal_admin.get("/api/admin/remote-sync/status").json(), external_url
            )

            internal_admin.close()
            _stop(internal_process)
            processes.remove(internal_process)
            _stop(external_process)
            processes.remove(external_process)
            external_process = _start_server(
                root,
                name="external",
                config=external_config,
                base_url=external_url,
                archive_authority=EXTERNAL_AUTHORITY,
                installation="external",
                stages=external_stages,
                artifact_root=external_artifacts,
                source_audio_fixture=source_audio_fixture,
                source_audio_request_log=source_audio_request_log,
                attempt=2,
            )
            processes.append(external_process)
            internal_process = _start_server(
                root,
                name="internal",
                config=internal_config,
                base_url=internal_url,
                archive_authority=INTERNAL_AUTHORITY,
                installation="internal",
                stages=internal_stages,
                artifact_root=internal_artifacts,
                attempt=2,
            )
            processes.append(internal_process)
            external_admin = _login(external_url)
            try:
                replay_after_restart = _cache_external_source_audio(
                    external_admin,
                    expected_artifact_id=source_artifact["id"],
                )
                assert replay_after_restart["id"] == source_artifact["id"]
                assert source_audio_request_log.read_text(
                    encoding="ascii"
                ).splitlines() == ["request"]
            finally:
                external_admin.close()
            _assert_external_source_audio(
                external_db,
                external_artifacts,
                artifact_id=source_artifact["id"],
            )
            internal_admin = _login(internal_url)
            after_restart = _checkpoint_identity(
                internal_admin.get("/api/admin/remote-sync/status").json(), external_url
            )
            assert after_restart == before_restart
            third = _run_sync(internal_admin, external_url)
            # Startup reconciles built-in source definitions and can therefore
            # advance only the sources stream.  Podcast publications must stay
            # zero-delta; one follow-up clears its page cursor and the next proves
            # the settled checkpoint is stable.
            assert all(
                stats["count"] == 0
                for stream, stats in third["streams"].items()
                if stream != "sources"
            )
            fourth = _run_sync(internal_admin, external_url)
            assert all(value["count"] == 0 for value in fourth["streams"].values())
            settled_checkpoint = _checkpoint_identity(
                internal_admin.get("/api/admin/remote-sync/status").json(), external_url
            )
            fifth = _run_sync(internal_admin, external_url)
            assert all(value["count"] == 0 for value in fifth["streams"].values())
            final_checkpoint = _checkpoint_identity(
                internal_admin.get("/api/admin/remote-sync/status").json(), external_url
            )
            assert final_checkpoint == settled_checkpoint
            _assert_internal_database(internal_db, internal_artifacts)
        finally:
            internal_admin.close()

        print(
            json.dumps(
                {
                    "status": "passed",
                    "runtime_roles": {"external": "all", "internal": "all"},
                    "podcast_stages": {
                        "external": list(external_stages),
                        "internal": list(internal_stages),
                    },
                    "capabilities": [
                        archive_sync_v2.PODCAST_TEXT_PUBLICATIONS_CAPABILITY,
                        archive_sync_v2.PODCAST_AUDIO_PUBLICATIONS_CAPABILITY,
                    ],
                    "streams": list(EXPECTED_STREAMS),
                    "first_counts": {
                        stream: stats["count"]
                        for stream, stats in first["streams"].items()
                    },
                    "second_counts": {
                        stream: stats["count"]
                        for stream, stats in second["streams"].items()
                    },
                    "restart_counts": {
                        stream: stats["count"]
                        for stream, stats in third["streams"].items()
                    },
                    "podcast_text_pages": first["streams"]["podcast_texts"]["pages"],
                    "reader_requests": [
                        "/api/articles?shape=podcast",
                        f"/api/articles/{EPISODE_ID}",
                    ],
                    "provider_attempts": 0,
                    "original_audio": "external-temporary-cache-only",
                    "source_audio": {
                        "artifact_id": source_artifact["id"],
                        "status": "ready",
                        "retention": "temporary",
                        "ttl_seconds": SOURCE_AUDIO_TTL_SECONDS,
                        "external_cached": True,
                        "internal_synchronized": False,
                        "publisher_requests": 1,
                        "stable_after_restart": True,
                    },
                    "digest_audio_zh": "external-generated-and-synchronized",
                    "checkpoint_stable_after_restart": True,
                    "temp_root": str(root) if args.keep else "removed",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        passed = True
        return 0
    finally:
        for process in reversed(processes):
            _stop(process)
        if not args.keep and passed:
            shutil.rmtree(root)
            if root.exists():
                raise RuntimeError(
                    f"failed to remove successful E2E environment: {root}"
                )
        elif not passed:
            print(
                f"Podcast text E2E environment retained for diagnosis: {root}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
