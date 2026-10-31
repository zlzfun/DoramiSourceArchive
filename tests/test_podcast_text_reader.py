"""Reader contract for current, rights-gated Podcast text publications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from config import PodcastConfig, RuntimeConfig, load_config
from models.db import (
    ArticleRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from storage.impl.db_storage import DatabaseStorage
from tests.conftest import seed_default_accounts


STAMP = "2026-09-05T00:00:00+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"
NORMALIZED_BODY = json.dumps(
    {
        "audio_duration_ms": 2_000,
        "language": "zh-CN",
        "text": "第一段。\n第二段。",
        "segments": [
            {
                "channel": 0,
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "第一段。",
                "words": [],
            },
            {
                "channel": 0,
                "start_ms": 1_000,
                "end_ms": 2_000,
                "text": "第二段。",
                "words": [],
            },
        ],
    },
    ensure_ascii=False,
    separators=(",", ":"),
)


def _artifact(
    episode_id: str,
    kind: str,
    body: str,
    *,
    version: int = 1,
    authority_id: str = "remote\u202e-authority\n",
):
    provenance = {
        "pipeline_version": "podcast\u202e-v1\n",
        "producer_authority_id": "forged-authority",
        "url": "https://publisher.test/transcript?token=never-expose",
    }
    if kind == "publisher_transcript":
        provenance["format"] = "vtt"
    return PodcastTextArtifactRecord(
        id=f"text-{kind}-{version}",
        episode_id=episode_id,
        kind=kind,
        version=version,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
        inline_text=body,
        language="zh-CN" if kind != "publisher_transcript" else "en",
        authority_id=authority_id,
        provenance_json=json.dumps(provenance),
        created_at=STAMP,
    )


def _publication(artifact):
    return PodcastTextPublicationRecord(
        identity=f"{artifact.episode_id}:{artifact.kind}",
        episode_id=artifact.episode_id,
        kind=artifact.kind,
        artifact_id=artifact.id,
        status="published",
        authority_id=artifact.authority_id,
        published_at=STAMP,
        updated_at=STAMP,
    )


def _setup(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'podcast-text-reader.db'}")
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="podcast-reader",
                name="Reader Podcast",
                source_type="podcast",
                url="https://podcasts.test/feed.xml",
                category="podcast",
                fetcher_id="generic_podcast_rss",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.add(
            ArticleRecord(
                id="episode-reader",
                title="Episode",
                content_type="podcast_episode",
                source_id="podcast-reader",
                source_url="https://podcasts.test/e/1",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="show notes",
            )
        )
        session.add(
            ArticleRecord(
                id="not-podcast",
                title="Article",
                content_type="rss_article",
                source_id="podcast-reader",
                source_url="https://podcasts.test/a/1",
                publish_date=STAMP,
                fetched_date=STAMP,
                content="article",
            )
        )
        artifacts = [
            _artifact("episode-reader", "digest_blog_zh", "精华内容第一段，继续阅读。"),
            _artifact(
                "episode-reader", "transcript_zh", "甲乙丙丁搜索词戊己庚辛搜索词壬癸。"
            ),
            _artifact(
                "episode-reader",
                "publisher_transcript",
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello <b>world</b>.\n",
            ),
            _artifact(
                "episode-reader", "normalized_transcript", NORMALIZED_BODY
            ),
            _artifact("episode-reader", "narration_script_zh", "绝不能发给 Reader。"),
        ]
        for artifact in artifacts:
            session.add(artifact)
        session.commit()
        for artifact in artifacts:
            session.add(_publication(artifact))
        session.commit()
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            runtime=RuntimeConfig(role="all"),
            podcast=PodcastConfig(
                reader_text_default_chars=12,
                reader_text_max_chars=20,
                reader_text_query_max_chars=8,
            ),
        ),
    )
    return app_module, sink


def _login(client):
    assert (
        client.post(
            "/api/auth/login", json={"username": "user", "password": "user"}
        ).status_code
        == 200
    )


def test_reader_returns_safe_current_publications_without_enqueuing(
    monkeypatch, tmp_path
):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.get("/api/podcasts/episodes/episode-reader/texts")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert [item["kind"] for item in payload["items"]] == [
            "digest_blog_zh",
            "transcript_zh",
            "publisher_transcript",
            "normalized_transcript",
        ]
        assert "narration" not in response.text
        publisher = payload["items"][2]
        assert publisher["text"] == "Hello world."
        assert "WEBVTT" not in publisher["text"]
        assert "token" not in json.dumps(publisher["provenance"])
        assert publisher["provenance"]["label"] == "来源逐字稿"
        assert publisher["provenance"]["producer_authority_id"] == "remote-authority"
        assert publisher["provenance"]["pipeline_note"] == "podcast-v1"
        assert "forged-authority" not in response.text
        normalized = payload["items"][3]
        assert normalized["text"] == "第一段。\n第二段。"
        assert normalized["text_format"] == "plain"
        assert normalized["provenance"]["label"] == "ASR 逐字稿"
        assert normalized["provenance"]["origin"] == "asr"
    with Session(sink.engine) as session:
        assert session.exec(select(PodcastProcessingRecord)).all() == []
        assert session.exec(select(PodcastStageAttemptRecord)).all() == []


def test_reader_paginates_searches_and_rejects_stale_or_unbounded_requests(
    monkeypatch, tmp_path
):
    app_module, sink = _setup(monkeypatch, tmp_path)
    url = "/api/podcasts/episodes/episode-reader/texts?kind=transcript_zh"
    with TestClient(app_module.app) as client:
        _login(client)
        first = client.get(f"{url}&limit=8").json()["items"][0]
        assert len(first["text"]) == 8
        second_response = client.get(f"{url}&limit=8&cursor={first['next_cursor']}")
        assert second_response.status_code == 200
        second = second_response.json()["items"][0]
        assert second["range_start"] == first["range_end"]

        search = client.get(f"{url}&q=搜索词&limit=8").json()["items"][0]
        assert "搜索词" in search["text"]
        assert search["next_cursor"]
        assert client.get(f"{url}&q=不存在&limit=8").json()["items"] == []
        assert client.get(f"{url}&limit=21").status_code == 400
        assert client.get(f"{url}&q=123456789").status_code == 400
        assert (
            client.get(
                "/api/podcasts/episodes/episode-reader/texts?cursor=bad"
            ).status_code
            == 400
        )

        encoded, signature = first["next_cursor"].split(".")
        forged = f"{encoded[:-1]}A.{signature}"
        forged_response = client.get(f"{url}&limit=8&cursor={forged}")
        assert forged_response.status_code == 400
        assert forged_response.json()["code"] == "podcast_text_bad_request"

        with Session(sink.engine) as session:
            replacement = _artifact(
                "episode-reader", "transcript_zh", "全新发布版本", version=2
            )
            session.add(replacement)
            publication = session.get(
                PodcastTextPublicationRecord, "episode-reader:transcript_zh"
            )
            publication.artifact_id = replacement.id
            publication.updated_at = "2026-09-05T00:01:00+00:00"
            session.add(publication)
            session.commit()
        assert (
            client.get(f"{url}&limit=8&cursor={first['next_cursor']}").status_code
            == 400
        )


def test_reader_unicode_search_uses_original_non_overlapping_spans(
    monkeypatch, tmp_path
):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        artifact = _artifact("episode-reader", "transcript_zh", "İxİy", version=2)
        session.add(artifact)
        publication = session.get(
            PodcastTextPublicationRecord, "episode-reader:transcript_zh"
        )
        publication.artifact_id = artifact.id
        publication.updated_at = "2026-09-05T00:05:00+00:00"
        session.add(publication)
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        endpoint = (
            "/api/podcasts/episodes/episode-reader/texts?kind=transcript_zh&q=i&limit=1"
        )
        first = client.get(endpoint).json()["items"][0]
        second = client.get(f"{endpoint}&cursor={first['next_cursor']}").json()[
            "items"
        ][0]
        assert (first["range_start"], first["range_end"], first["text"]) == (0, 1, "İ")
        assert (second["range_start"], second["range_end"], second["text"]) == (
            2,
            3,
            "İ",
        )
        assert second["next_cursor"] is None


def test_reader_rejects_legacy_text_over_character_or_byte_ceiling(
    monkeypatch, tmp_path
):
    app_module, _ = _setup(monkeypatch, tmp_path)
    app_module.settings = replace(
        app_module.settings,
        podcast=replace(
            app_module.settings.podcast,
            text_artifact_max_chars=5,
            text_artifact_max_bytes=100,
        ),
    )
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.get(
            "/api/podcasts/episodes/episode-reader/texts?kind=digest_blog_zh"
        )
        assert response.status_code == 422
        assert response.json()["code"] == "podcast_text_artifact_invalid"

    app_module.settings = replace(
        app_module.settings,
        podcast=replace(
            app_module.settings.podcast,
            text_artifact_max_chars=100,
            text_artifact_max_bytes=5,
        ),
    )
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.get(
            "/api/podcasts/episodes/episode-reader/texts?kind=digest_blog_zh"
        )
        assert response.status_code == 422


def test_openapi_excludes_narration_script_from_reader_contract(monkeypatch, tmp_path):
    app_module, _ = _setup(monkeypatch, tmp_path)
    schema = app_module.app.openapi()
    operation = schema["paths"]["/api/podcasts/episodes/{episode_id}/texts"]["get"]
    kind = next(item for item in operation["parameters"] if item["name"] == "kind")
    assert "narration_script_zh" not in json.dumps(kind)
    response_schema = schema["components"]["schemas"]["PodcastTextItemResponse"]
    assert "narration_script_zh" not in json.dumps(response_schema)
    assert response_schema["properties"]["kind"]["enum"] == [
        "digest_blog_zh",
        "transcript_zh",
        "publisher_transcript",
        "normalized_transcript",
    ]
    assert {"200", "400", "401", "403", "404", "422"}.issubset(operation["responses"])
    for status in ("400", "401", "403", "404", "422"):
        assert "PodcastReaderErrorResponse" in json.dumps(
            operation["responses"][status]
        )
    assert len(
        {
            operation["operationId"]
            for path in schema["paths"].values()
            for operation in path.values()
            if isinstance(operation, dict) and "operationId" in operation
        }
    ) == sum(
        1
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    )


def test_reader_unauthenticated_error_is_stable_and_not_cacheable(
    monkeypatch, tmp_path
):
    app_module, _ = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        response = client.get("/api/podcasts/episodes/episode-reader/texts")
    assert response.status_code == 401
    assert response.json() == {
        "code": "podcast_auth_required",
        "message": "未登录或登录已过期",
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


def test_reader_validation_error_uses_stable_body(monkeypatch, tmp_path):
    app_module, _ = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = client.get(
            "/api/podcasts/episodes/episode-reader/texts?kind=narration_script_zh"
        )
    assert response.status_code == 422
    assert response.json() == {
        "code": "podcast_text_bad_request",
        "message": "Podcast 文本请求参数无效",
    }
    assert response.headers["cache-control"] == "private, no-store"


def test_reader_text_limits_are_configurable_and_environment_overridable(
    monkeypatch, tmp_path
):
    ini = tmp_path / "podcast-reader.ini"
    ini.write_text(
        "[podcast]\n"
        "reader_text_default_chars = 7\n"
        "reader_text_max_chars = 18\n"
        "reader_text_query_max_chars = 4\n"
        "text_artifact_max_bytes = 41\n"
        "text_artifact_max_chars = 17\n"
        "text_sync_page_max_bytes = 100\n"
        "text_sync_page_max_rows = 3\n"
        "reader_cursor_secret = 0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))
    monkeypatch.setenv("DORAMI_PODCAST_READER_TEXT_MAX_CHARS", "19")
    monkeypatch.setenv("DORAMI_PODCAST_TEXT_ARTIFACT_MAX_BYTES", "42")
    monkeypatch.setenv("DORAMI_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES", "101")
    configured = load_config().podcast
    assert configured.reader_text_default_chars == 7
    assert configured.reader_text_max_chars == 19
    assert configured.reader_text_query_max_chars == 4
    assert configured.text_artifact_max_bytes == 42
    assert configured.text_artifact_max_chars == 17
    assert configured.text_sync_page_max_bytes == 101
    assert configured.text_sync_page_max_rows == 3
    assert configured.reader_cursor_secret == "0123456789abcdef0123456789abcdef"


def test_existing_word_transcript_reads_as_paragraphs_without_changing_ai_input(
    monkeypatch, tmp_path
):
    from services import podcast_text_reader, podcast_transcript_translation

    app_module, sink = _setup(monkeypatch, tmp_path)
    sentence = "I just happened to have some real world experience with this just recently."
    answer = "Yes, that matches."
    words = [("SPEAKER_01", word) for word in sentence.split()]
    words += [("SPEAKER_02", word) for word in answer.split()]
    body = json.dumps({"segments": [
        {"speaker": speaker, "body": word, "startTime": index / 2, "endTime": (index + 1) / 2}
        for index, (speaker, word) in enumerate(words)
    ]})
    artifact = _artifact("episode-reader", "publisher_transcript", body, version=2)
    artifact.provenance_json = json.dumps({"format": "json"})
    with Session(sink.engine) as session:
        session.add(artifact)
        session.commit()
        publication = session.get(PodcastTextPublicationRecord, "episode-reader:publisher_transcript")
        publication.artifact_id = artifact.id
        session.add(publication)
        session.commit()
    original_hash = hashlib.sha256(body.encode()).hexdigest()
    config = replace(app_module.settings.podcast, reader_text_max_chars=100, reader_text_query_max_chars=30)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, podcast=config))
    expected = f"SPEAKER_01: {sentence}\n\nSPEAKER_02: {answer}"
    with TestClient(app_module.app) as client:
        _login(client)
        url = "/api/podcasts/episodes/episode-reader/texts"
        params = {"kind": "publisher_transcript", "limit": 37}
        parts = []
        offset = 0
        while True:
            response = client.get(url, params=params)
            assert response.status_code == 200, response.text
            item = response.json()["items"][0]
            assert item["range_start"] == offset
            assert item["content_hash"] == original_hash
            assert item["total_chars"] == len(expected)
            parts.append(item["text"])
            offset = item["range_end"]
            if not item["next_cursor"]:
                break
            params["cursor"] = item["next_cursor"]
        assert "".join(parts) == expected
        search = client.get(url, params={"kind": "publisher_transcript", "q": "real world experience", "limit": 80})
        assert search.status_code == 200
        assert "real world experience" in search.json()["items"][0]["text"]
    with Session(sink.engine) as session:
        saved = session.get(PodcastTextArtifactRecord, "text-publisher_transcript-2")
        assert saved.inline_text == body
        assert saved.content_hash == original_hash
        assert saved.version == 2
        old_projection = "\n".join(f"{speaker}: {word}" for speaker, word in words)
        assert podcast_text_reader._plain_text(saved, config) == old_projection
        _, source, translation_input = podcast_transcript_translation._load_input(
            session, episode_id="episode-reader", source_kind="publisher_transcript", config=config,
        )
        assert source.id == saved.id
        assert translation_input == old_projection
        assert len(session.exec(select(PodcastTextArtifactRecord)).all()) == 6
        assert session.exec(select(PodcastProcessingRecord)).all() == []
        assert session.exec(select(PodcastStageAttemptRecord)).all() == []


def test_reader_cursor_rejects_changed_projection_and_legacy_offsets():
    import pytest
    from services import podcast_text_reader as reader

    artifact = _artifact("episode-reader", "publisher_transcript", "immutable source")
    args = {"query": "", "limit": 5, "cursor_secret": "test-secret"}
    first = reader._page(artifact, text="one two three", cursor="", **args)
    assert reader._page(artifact, text="one two three", cursor=first["next_cursor"], **args)["text"] == "wo th"
    with pytest.raises(reader.PodcastTextReaderBadRequest, match="失效"):
        reader._page(artifact, text="ONE TWO THREE", cursor=first["next_cursor"], **args)

    # A genuinely signed pre-fix cursor can have an in-range offset while
    # pointing at a different word; deployment must force a first-page refresh.
    legacy_payload = {"v": 2, "a": artifact.id, "h": artifact.content_hash, "k": artifact.kind,
                      "o": 5, "q": hashlib.sha256(b"").hexdigest()}
    encoded = base64.urlsafe_b64encode(json.dumps(legacy_payload).encode()).decode().rstrip("=")
    signature = hmac.new(b"test-secret", f"podcast-text-cursor-v2.{encoded}".encode(), hashlib.sha256).digest()
    legacy = encoded + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")
    with pytest.raises(reader.PodcastTextReaderBadRequest):
        reader._page(artifact, text="one two three", cursor=legacy, **args)
