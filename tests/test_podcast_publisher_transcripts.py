"""Publisher transcript parsing, governance, publication, and API tests."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import threading
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from config import PodcastConfig, RuntimeConfig, load_config
from models.db import (
    ArticleRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services import podcast_publisher_transcripts as transcripts
from services.podcast_stage_policy import PodcastStagePolicy
from storage.impl.db_storage import DatabaseStorage
from tests.conftest import seed_default_accounts


STAMP = "2026-09-05T00:00:00+00:00"
VTT = b"""WEBVTT

00:00:00.000 --> 00:00:02.000
Hello <b>world</b>.

00:00:02.000 --> 00:00:04.500
Second line.
"""


def _config(**changes) -> PodcastConfig:
    values = {
        "installation": "external",
        "authority_id": "podcast-external-test",
        "allowed_stages": ("fetch", "asr"),
        "transcript_max_bytes": 4096,
        "transcript_timeout_seconds": 7,
        "transcript_max_segments": 10,
        "transcript_max_text_chars": 1000,
    }
    values.update(changes)
    return PodcastConfig(**values)


def _sink(tmp_path, *, approved: bool = True, rights: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'publisher-transcript.db'}")
    with Session(sink.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="podcast-publisher",
                name="Publisher Podcast",
                source_type="podcast",
                url="https://publisher.example/feed.xml",
                category="podcast",
                fetcher_id="generic_podcast_rss",
                is_active=approved,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.commit()
        session.add(
            ArticleRecord(
                id="episode-publisher",
                title="Publisher episode",
                content_type="podcast_episode",
                source_id="podcast-publisher",
                source_url="https://publisher.example/episodes/1",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
                extensions_json=json.dumps(
                    {
                        "transcripts": [
                            {
                                "url": "https://cdn.publisher.example/episode.txt",
                                "type": "text/plain",
                                "language": "en",
                            },
                            {
                                "url": "https://cdn.publisher.example/episode.vtt?token=secret",
                                "type": "text/vtt; charset=utf-8",
                                "language": "en-US",
                            },
                        ]
                    }
                ),
            )
        )
        session.commit()
    return sink


def _run_ingest(sink, monkeypatch, body=VTT, *, calls=None, config=None, fetcher=None):
    calls = calls if calls is not None else []

    async def fake_fetch(_client, url, *, max_bytes, timeout_seconds, **_kwargs):
        calls.append((url, max_bytes, timeout_seconds))
        return body

    monkeypatch.setattr(
        transcripts.http_safety,
        "fetch_public_bytes_limited",
        fetcher or fake_fetch,
    )

    async def run():
        async with httpx.AsyncClient() as client:
            return await transcripts.ingest_publisher_transcript(
                sink.engine,
                episode_id="episode-publisher",
                config=config or _config(),
                client=client,
            )

    return asyncio.run(run())


def test_candidate_policy_prefers_timed_and_does_not_rescue_unsupported_mime():
    selected = transcripts.select_candidate(
        [
            {"url": "https://example.test/first.vtt", "type": "text/html"},
            {"url": "https://example.test/plain.txt", "type": "text/plain"},
            {"url": "https://example.test/timed.json", "type": "application/json"},
        ]
    )
    assert selected.format == "json"
    assert selected.metadata_index == 2
    assert (
        transcripts.select_candidate(
            [{"url": "https://example.test/no-content-type.srt"}]
        ).format
        == "srt"
    )
    with pytest.raises(transcripts.PublisherTranscriptNotFound):
        transcripts.select_candidate(
            [{"url": "https://example.test/conflict.srt", "type": "text/vtt"}]
        )


@pytest.mark.parametrize(
    ("format", "body", "expected"),
    [
        ("vtt", VTT, "Hello world.\nSecond line."),
        (
            "srt",
            b"1\n00:00:00,000 --> 00:00:01,500\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nWorld\n",
            "Hello\nWorld",
        ),
        ("text", b"  Hello world  \n\n Second line \n", "Hello world\nSecond line"),
        (
            "json",
            json.dumps(
                {
                    "segments": [
                        {
                            "speaker": "Host",
                            "startTime": 0,
                            "endTime": 1.5,
                            "body": "Hello",
                        },
                        {"startTime": 1.5, "endTime": 3, "body": "World"},
                    ]
                }
            ).encode(),
            "Host: Hello\nWorld",
        ),
    ],
)
def test_supported_formats_are_normalized(format, body, expected):
    parsed = transcripts.parse_transcript(
        body, format, max_segments=10, max_text_chars=1000
    )
    assert parsed.text == expected
    assert parsed.segment_count == 2
    if format in {"vtt", "srt"}:
        assert "-->" in parsed.source_text
    if format == "json":
        assert '"speaker": "Host"' in parsed.source_text


@pytest.mark.parametrize(
    "body",
    [
        b"WEBVTT\n\n00:00:03.000 --> 00:00:02.000\nbackwards\n",
        b"WEBVTT\n\n00:broken --> 00:00:02.000\nbroken\n",
        b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n\n",
    ],
)
def test_malformed_vtt_is_rejected(body):
    with pytest.raises(transcripts.PublisherTranscriptMalformed):
        transcripts.parse_transcript(body, "vtt", max_segments=10, max_text_chars=1000)


def test_vtt_header_and_plain_html_must_not_be_misclassified():
    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="WEBVTT"):
        transcripts.parse_transcript(
            b"WEBVTTjunk\n\n00:00:00.000 --> 00:00:01.000\nwrong\n",
            "vtt",
            max_segments=10,
            max_text_chars=1000,
        )


def test_nonfinite_timestamps_and_extreme_json_are_domain_errors():
    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="时间码"):
        transcripts.parse_transcript(
            b"WEBVTT\n\n00:00:nan --> 00:00:nan\ninvalid\n",
            "vtt",
            max_segments=10,
            max_text_chars=1000,
        )
    oversized_integer = b'{"segments":[{"body":' + (b"1" * 5000) + b"}]}"
    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="JSON"):
        transcripts.parse_transcript(
            oversized_integer,
            "json",
            max_segments=10,
            max_text_chars=10_000,
        )
    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="HTML"):
        transcripts.parse_transcript(
            b"<!doctype html><html><body>error page</body></html>",
            "text",
            max_segments=10,
            max_text_chars=1000,
        )


def test_segment_and_text_limits_are_enforced():
    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="segment"):
        transcripts.parse_transcript(
            b"one\ntwo\n", "text", max_segments=1, max_text_chars=100
        )
    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="字符"):
        transcripts.parse_transcript(
            b"long text", "text", max_segments=2, max_text_chars=4
        )


def test_ingest_is_bounded_idempotent_and_moves_immutable_pointer(
    monkeypatch, tmp_path
):
    sink = _sink(tmp_path)
    boundaries = []
    original = PodcastStagePolicy.require_artifact_writer

    def capture(self, kind, *, boundary="commit"):
        boundaries.append(boundary)
        return original(self, kind, boundary=boundary)

    monkeypatch.setattr(PodcastStagePolicy, "require_artifact_writer", capture)
    calls = []
    first = _run_ingest(sink, monkeypatch, calls=calls)
    second = _run_ingest(sink, monkeypatch, calls=calls)
    changed = _run_ingest(
        sink,
        monkeypatch,
        body=VTT.replace(b"Second line.", b"Changed line."),
        calls=calls,
    )

    assert first["created"] is True
    assert second["created"] is False
    assert changed["created"] is True
    assert first["artifact"]["id"] == second["artifact"]["id"]
    assert changed["artifact"]["version"] == 2
    assert all(result["provider_calls"] == 0 for result in (first, second, changed))
    assert calls == [
        ("https://cdn.publisher.example/episode.vtt?token=secret", 4096, 7),
        ("https://cdn.publisher.example/episode.vtt?token=secret", 4096, 7),
        ("https://cdn.publisher.example/episode.vtt?token=secret", 4096, 7),
    ]
    assert boundaries == ["enqueue", "provider_submit", "commit"] * 3
    with Session(sink.engine) as session:
        artifacts = session.exec(
            select(PodcastTextArtifactRecord).order_by(
                PodcastTextArtifactRecord.version
            )
        ).all()
        publication = session.get(
            PodcastTextPublicationRecord,
            "episode-publisher:publisher_transcript",
        )
        assert [row.version for row in artifacts] == [1, 2]
        assert publication.artifact_id == artifacts[1].id
        assert all(row.authority_id == "" for row in artifacts)
        provenance = json.loads(artifacts[0].provenance_json)
        assert "00:00:00.000 --> 00:00:02.000" in artifacts[0].inline_text
        assert provenance["producer_authority_id"] == "podcast-external-test"
        assert provenance["mime"] == "text/vtt"
        assert provenance["raw_sha256"] == artifacts[0].source_content_hash
        assert provenance["plain_text_chars"] == len("Hello world.\nSecond line.")
        assert "url" not in provenance
        assert "token=secret" not in artifacts[0].provenance_json


def test_same_content_at_a_new_locator_rebinds_with_a_new_immutable_version(
    monkeypatch, tmp_path
):
    sink = _sink(tmp_path)
    first = _run_ingest(sink, monkeypatch)
    new_url = "https://cdn.publisher.example/reissued.vtt"
    with Session(sink.engine) as session:
        episode = session.get(ArticleRecord, "episode-publisher")
        episode.extensions_json = json.dumps(
            {
                "transcripts": [
                    {
                        "url": new_url,
                        "type": "text/vtt",
                        "language": "en-US",
                    }
                ]
            }
        )
        session.add(episode)
        session.commit()

    rebound = _run_ingest(sink, monkeypatch)

    assert rebound["created"] is True
    assert rebound["artifact"]["id"] != first["artifact"]["id"]
    assert rebound["artifact"]["version"] == 2
    assert (
        transcripts.publisher_transcript_refresh_revision(
            sink.engine, episode_id="episode-publisher"
        )
        == ""
    )
    with Session(sink.engine) as session:
        artifacts = session.exec(
            select(PodcastTextArtifactRecord).order_by(
                PodcastTextArtifactRecord.version
            )
        ).all()
        assert len(artifacts) == 2
        assert artifacts[0].content_hash == artifacts[1].content_hash
        first_provenance = json.loads(artifacts[0].provenance_json)
        rebound_provenance = json.loads(artifacts[1].provenance_json)
        assert first_provenance["url_sha256"] != rebound_provenance["url_sha256"]
        assert rebound_provenance["url_sha256"] == hashlib.sha256(
            new_url.encode()
        ).hexdigest()


@pytest.mark.parametrize(
    "limit_changes",
    [
        {"text_artifact_max_chars": 20},
        {"text_artifact_max_bytes": 20},
    ],
)
def test_ingest_enforces_canonical_text_artifact_limits(
    monkeypatch, tmp_path, limit_changes
):
    sink = _sink(tmp_path)
    calls = []
    with pytest.raises(transcripts.PublisherTranscriptMalformed):
        _run_ingest(
            sink,
            monkeypatch,
            calls=calls,
            config=_config(**limit_changes),
        )
    assert calls[0][1] == min(
        4096,
        limit_changes.get("text_artifact_max_bytes", 8 * 1024 * 1024),
    )
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []


def test_commit_rejects_transcript_metadata_changed_during_download(
    monkeypatch, tmp_path
):
    sink = _sink(tmp_path)

    async def change_locator_then_return(_client, _url, **_kwargs):
        with Session(sink.engine) as session:
            episode = session.get(ArticleRecord, "episode-publisher")
            episode.extensions_json = json.dumps(
                {
                    "transcripts": [
                        {
                            "url": "https://cdn.publisher.example/replaced.vtt",
                            "type": "text/vtt",
                            "language": "en-US",
                        }
                    ]
                }
            )
            session.add(episode)
            session.commit()
        return VTT

    with pytest.raises(transcripts.PublisherTranscriptConflict, match="变化"):
        _run_ingest(sink, monkeypatch, fetcher=change_locator_then_return)
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []


def test_concurrent_same_content_creates_one_immutable_version(monkeypatch, tmp_path):
    sink = _sink(tmp_path)
    worker_count = 4
    barrier = threading.Barrier(worker_count)

    async def synchronized_fetch(_client, _url, **_kwargs):
        barrier.wait(timeout=5)
        return VTT

    monkeypatch.setattr(
        transcripts.http_safety, "fetch_public_bytes_limited", synchronized_fetch
    )

    def worker():
        async def run():
            async with httpx.AsyncClient() as client:
                return await transcripts.ingest_publisher_transcript(
                    sink.engine,
                    episode_id="episode-publisher",
                    config=_config(),
                    client=client,
                )

        return asyncio.run(run())

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(lambda _index: worker(), range(worker_count)))
    assert sum(result["created"] for result in results) == 1
    assert len({result["artifact"]["id"] for result in results}) == 1
    with Session(sink.engine) as session:
        assert len(session.exec(select(PodcastTextArtifactRecord)).all()) == 1


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (
            ValueError("RSS 响应超过大小上限"),
            transcripts.PublisherTranscriptTooLarge,
        ),
        (
            httpx.TimeoutException("timed out"),
            transcripts.PublisherTranscriptTimeout,
        ),
    ],
)
def test_download_size_and_timeout_are_safely_mapped(
    monkeypatch, tmp_path, error, expected_error
):
    sink = _sink(tmp_path)
    observed = []

    async def fail(_client, _url, *, max_bytes, timeout_seconds, **_kwargs):
        observed.append((max_bytes, timeout_seconds))
        raise error

    with pytest.raises(expected_error):
        _run_ingest(sink, monkeypatch, fetcher=fail)
    assert observed == [(4096, 7)]


def test_outer_deadline_also_bounds_safety_resolution(monkeypatch, tmp_path):
    sink = _sink(tmp_path)

    async def stalled_safety_resolution(_client, _url, **_kwargs):
        await asyncio.sleep(1)
        return VTT

    with pytest.raises(transcripts.PublisherTranscriptTimeout):
        _run_ingest(
            sink,
            monkeypatch,
            config=_config(transcript_timeout_seconds=0.01),
            fetcher=stalled_safety_resolution,
        )


def test_ssrf_redirect_is_rechecked_and_private_target_is_never_requested(
    monkeypatch, tmp_path
):
    sink = _sink(tmp_path)
    requested = []

    async def public_host(hostname):
        if hostname == "127.0.0.1":
            raise ValueError("private address")

    async def handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    from services import media_store

    monkeypatch.setattr(media_store, "ensure_public_host", public_host)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await transcripts.ingest_publisher_transcript(
                sink.engine,
                episode_id="episode-publisher",
                config=_config(),
                client=client,
            )

    with pytest.raises(transcripts.PublisherTranscriptMalformed, match="下载失败"):
        asyncio.run(run())
    assert requested == ["https://cdn.publisher.example/episode.vtt?token=secret"]


def test_config_ini_and_env_override_transcript_limits(monkeypatch, tmp_path):
    ini = tmp_path / "podcast.ini"
    ini.write_text(
        "[storage]\ndatabase_url = sqlite:///:memory:\n"
        "[podcast]\ntranscript_max_bytes = 1234\n"
        "transcript_timeout_seconds = 9\ntranscript_max_segments = 33\n"
        "transcript_max_text_chars = 4444\n"
        "transcript_duration_tolerance_seconds = 6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.setenv("DORAMI_PODCAST_TRANSCRIPT_MAX_BYTES", "2345")
    monkeypatch.setenv("DORAMI_PODCAST_TRANSCRIPT_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("DORAMI_PODCAST_TRANSCRIPT_MAX_SEGMENTS", "44")
    monkeypatch.setenv("DORAMI_PODCAST_TRANSCRIPT_MAX_TEXT_CHARS", "5555")
    monkeypatch.setenv("DORAMI_PODCAST_TRANSCRIPT_DURATION_TOLERANCE_SECONDS", "7")
    config = load_config().podcast
    assert (
        config.transcript_max_bytes,
        config.transcript_timeout_seconds,
        config.transcript_max_segments,
        config.transcript_max_text_chars,
        config.transcript_duration_tolerance_seconds,
    ) == (2345, 10, 44, 5555, 7)


def test_config_rejects_negative_transcript_duration_tolerance():
    with pytest.raises(ValueError, match="Podcast transcript limits are invalid"):
        replace(_config(), transcript_duration_tolerance_seconds=-1)


def test_admin_api_is_explicit_and_reader_get_never_fetches(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _sink(tmp_path)
    seed_default_accounts(sink.engine)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=_config(),
        ),
    )
    fetches = []

    async def fake_fetch(_client, _url, **_kwargs):
        fetches.append(1)
        return VTT

    monkeypatch.setattr(
        transcripts.http_safety, "fetch_public_bytes_limited", fake_fetch
    )
    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "user", "password": "user"}
            ).status_code
            == 200
        )
        assert client.get("/api/articles/episode-publisher").status_code == 200
        assert fetches == []
        assert (
            client.post(
                "/api/admin/podcast-transcripts/episode-publisher/ingest-publisher"
            ).status_code
            == 403
        )

    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/admin/podcast-transcripts/episode-publisher/ingest-publisher"
        )
        assert response.status_code == 200
        assert response.json()["provider_calls"] == 0
        assert fetches == [1]


def test_admin_api_rebinds_same_content_from_a_changed_locator(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _sink(tmp_path)
    seed_default_accounts(sink.engine)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=_config(),
        ),
    )
    requested_urls = []

    async def fake_fetch(_client, url, **_kwargs):
        requested_urls.append(url)
        return VTT

    monkeypatch.setattr(
        transcripts.http_safety, "fetch_public_bytes_limited", fake_fetch
    )
    with TestClient(app_module.app) as client:
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).status_code == 200
        first = client.post(
            "/api/admin/podcast-transcripts/episode-publisher/ingest-publisher"
        )
        assert first.status_code == 200

        new_url = "https://cdn.publisher.example/reissued.vtt"
        with Session(sink.engine) as session:
            episode = session.get(ArticleRecord, "episode-publisher")
            episode.extensions_json = json.dumps(
                {
                    "transcripts": [
                        {
                            "url": new_url,
                            "type": "text/vtt",
                            "language": "en-US",
                        }
                    ]
                }
            )
            session.add(episode)
            session.commit()

        rebound = client.post(
            "/api/admin/podcast-transcripts/episode-publisher/ingest-publisher"
        )

    assert rebound.status_code == 200
    assert rebound.json()["created"] is True
    assert rebound.json()["artifact"]["version"] == 2
    assert requested_urls == [
        "https://cdn.publisher.example/episode.vtt?token=secret",
        new_url,
    ]


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ValueError("RSS 响应超过大小上限"), 413),
        (httpx.TimeoutException("timed out"), 504),
        (httpx.ConnectError("connection failed"), 502),
    ],
)
def test_admin_api_distinguishes_size_timeout_and_upstream_failure(
    monkeypatch, tmp_path, error, status_code
):
    import api.app as app_module

    sink = _sink(tmp_path)
    seed_default_accounts(sink.engine)
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=_config(),
        ),
    )

    async def fail(_client, _url, **_kwargs):
        raise error

    monkeypatch.setattr(transcripts.http_safety, "fetch_public_bytes_limited", fail)
    with TestClient(app_module.app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/admin/podcast-transcripts/episode-publisher/ingest-publisher"
        )
        assert response.status_code == status_code
