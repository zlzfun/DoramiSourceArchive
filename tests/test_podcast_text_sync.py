"""Archive Sync v3 contract tests for current Podcast text publications."""

import hashlib
import json
import os
import struct
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArticleRecord,
    PodcastArtifactRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
)
from services import archive_sync_v2  # noqa: E402
from services.podcast_artifacts import PodcastArtifactStore  # noqa: E402
from services.podcast_normalized_transcripts import (  # noqa: E402
    canonical_normalized_transcript,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


STAMP = "2026-09-05T12:00:00+00:00"


def _sink(tmp_path, name):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _source():
    return SourceConfigRecord(
        source_id="podcast-show",
        name="Podcast Show",
        source_type="podcast",
        url="https://podcast.example.test/feed.xml",
        owner_username="",
        collection_authority_id="",
        created_at=STAMP,
        updated_at=STAMP,
    )


def _episode(episode_id="episode-1"):
    return ArticleRecord(
        id=episode_id,
        title=f"Episode {episode_id}",
        content_type="podcast_episode",
        source_id="podcast-show",
        source_url=f"https://podcast.example.test/{episode_id}",
        publish_date=STAMP,
        fetched_date=STAMP,
        archive_updated_at=STAMP,
        has_content=True,
        content="Podcast description",
        extensions_json="{}",
    )


def _artifact(
    artifact_id="text-1",
    *,
    episode_id="episode-1",
    kind="transcript_zh",
    version=1,
    text="你好，世界。",
    authority_id="",
):
    return PodcastTextArtifactRecord(
        id=artifact_id,
        episode_id=episode_id,
        kind=kind,
        version=version,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        inline_text=text,
        language="zh-CN",
        authority_id=authority_id,
        source_artifact_id="publisher-source-1",
        source_content_hash="a" * 64,
        provenance_json='{"pipeline":"podcast-v1"}',
        created_at=STAMP,
    )


def _publication(
    artifact_id="text-1",
    *,
    episode_id="episode-1",
    kind="transcript_zh",
    authority_id="",
):
    return PodcastTextPublicationRecord(
        identity=f"{episode_id}:{kind}",
        episode_id=episode_id,
        kind=kind,
        artifact_id=artifact_id,
        status="published",
        authority_id=authority_id,
        published_at=STAMP,
        updated_at=STAMP,
    )


def _local_digest_audio(
    *, episode_id: str, script: PodcastTextArtifactRecord, status: str = "published"
):
    return PodcastArtifactRecord(
        id=f"audio-{episode_id}",
        episode_id=episode_id,
        kind="digest_audio_zh",
        content_hash="f" * 64,
        mime="audio/mpeg",
        ext=".mp3",
        size_bytes=123,
        duration_seconds=10,
        status=status,
        provenance="local-tts",
        authority_id="",
        narration_artifact_id=script.id,
        narration_content_hash=script.content_hash,
        created_at=STAMP,
        updated_at=STAMP,
        published_at=STAMP if status == "published" else None,
    )


def _wav() -> bytes:
    pcm = b"\x00\x00" * 16
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8_000, 16_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def test_published_digest_audio_metadata_and_blob_round_trip(tmp_path):
    producer = _sink(tmp_path, "producer-audio.db")
    consumer = _sink(tmp_path, "consumer-audio.db")
    script = _artifact(
        "script-audio", kind="narration_script_zh", text="用于同步的口播稿。"
    )
    body = _wav()
    content_hash = hashlib.sha256(body).hexdigest()
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.commit()
        session.add(script)
        session.add(_publication(script.id, kind="narration_script_zh"))
        session.commit()
        session.add(
            PodcastArtifactRecord(
                id="digest-audio-1",
                episode_id="episode-1",
                kind="digest_audio_zh",
                content_hash=content_hash,
                mime="audio/wav",
                ext=".wav",
                size_bytes=len(body),
                duration_seconds=1.0,
                status="published",
                provenance="provider-neutral-tts",
                authority_id="",
                narration_artifact_id=script.id,
                narration_content_hash=script.content_hash,
                created_at=STAMP,
                updated_at=STAMP,
                published_at=STAMP,
            )
        )
        session.commit()

    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    _copy(producer, consumer, "podcast_texts")
    raw = archive_sync_v2.export_page(producer.engine, "podcast_audio")
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="podcast_audio")
    assert [row["identity"] for row in rows] == ["digest-audio-1"]
    applied = archive_sync_v2.import_page(
        consumer.engine, raw, expected_stream="podcast_audio"
    )
    assert applied["inserted"] == 1
    store = PodcastArtifactStore(
        consumer.engine,
        tmp_path / "consumer-audio-cas",
        max_bytes=1024 * 1024,
        total_quota_bytes=2 * 1024 * 1024,
        minimum_free_bytes=0,
        staging_ttl_seconds=0,
        allowed_mime_types=("audio/wav",),
    )
    installed = archive_sync_v2.install_podcast_audio_bytes(
        consumer.engine, store, "digest-audio-1", body
    )
    assert installed.status == "published"
    assert installed.authority_id == archive_sync_v2.producer_authority_id(
        producer.engine
    )
    assert store.file_path_for(installed).read_bytes() == body

    with Session(producer.engine) as session:
        audio = session.get(PodcastArtifactRecord, "digest-audio-1")
        audio.status = "withdrawn"
        audio.withdrawn_at = "2026-09-05T13:00:00+00:00"
        audio.updated_at = audio.withdrawn_at
        session.add(audio)
        session.commit()
    tombstone = archive_sync_v2.export_page(
        producer.engine, "podcast_audio", since=manifest["snapshot"]
    )
    _, tombstone_rows = archive_sync_v2.parse_page(
        tombstone, expected_stream="podcast_audio"
    )
    assert tombstone_rows[0]["operation"] == "tombstone"
    archive_sync_v2.import_page(
        consumer.engine, tombstone, expected_stream="podcast_audio"
    )
    with Session(consumer.engine) as session:
        assert (
            session.get(PodcastArtifactRecord, "digest-audio-1").status == "withdrawn"
        )


def _copy(producer, consumer, stream, **kwargs):
    raw = archive_sync_v2.export_page(producer.engine, stream, **kwargs)
    result = archive_sync_v2.import_page(consumer.engine, raw, expected_stream=stream)
    return archive_sync_v2.parse_page(raw, expected_stream=stream), result


def _prepare_two_episodes(producer, consumer):
    with Session(producer.engine) as session:
        session.add(_source())
        for index in (1, 2):
            episode_id = f"episode-{index}"
            artifact_id = f"text-{index}"
            session.add(_episode(episode_id))
            session.add(_artifact(artifact_id, episode_id=episode_id))
            session.add(_publication(artifact_id, episode_id=episode_id))
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")


def test_podcast_text_page_round_trip_keyset_replay_replace_and_withdraw(tmp_path):
    producer = _sink(tmp_path, "producer.db")
    consumer = _sink(tmp_path, "consumer.db")
    _prepare_two_episodes(producer, consumer)

    first_raw = archive_sync_v2.export_page(producer.engine, "podcast_texts", limit=1)
    first_manifest, first_rows = archive_sync_v2.parse_page(
        first_raw, expected_stream="podcast_texts"
    )
    assert first_manifest["complete"] is False
    assert len(first_rows) == 1
    assert set(first_rows[0]["payload"]["artifact"]) >= {
        "content_hash",
        "inline_text",
    }
    archive_sync_v2.import_page(
        consumer.engine, first_raw, expected_stream="podcast_texts"
    )

    second_raw = archive_sync_v2.export_page(
        producer.engine,
        "podcast_texts",
        snapshot=first_manifest["snapshot"],
        after=first_manifest["next_cursor"],
        limit=1,
    )
    second_manifest, second_rows = archive_sync_v2.parse_page(
        second_raw, expected_stream="podcast_texts"
    )
    assert second_manifest["snapshot"] == first_manifest["snapshot"]
    assert second_manifest["complete"] is True
    assert second_rows[0]["identity"] != first_rows[0]["identity"]
    archive_sync_v2.import_page(
        consumer.engine, second_raw, expected_stream="podcast_texts"
    )

    replay = archive_sync_v2.import_page(
        consumer.engine, first_raw, expected_stream="podcast_texts"
    )
    assert replay["inserted"] == replay["updated"] == replay["deleted"] == 0
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        publications = session.exec(select(PodcastTextPublicationRecord)).all()
        artifacts = session.exec(select(PodcastTextArtifactRecord)).all()
        assert len(publications) == len(artifacts) == 2
        assert {row.authority_id for row in publications + artifacts} == {authority}

    with Session(producer.engine) as session:
        session.add(_artifact("text-3", version=2, text="更新后的中文逐字稿。"))
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        publication.artifact_id = "text-3"
        publication.updated_at = "2026-09-05T13:00:00+00:00"
        session.add(publication)
        session.commit()
    (replace_manifest, replace_rows), replace_result = _copy(
        producer,
        consumer,
        "podcast_texts",
        since=first_manifest["snapshot"],
    )
    assert replace_result["updated"] == 1
    assert replace_rows[0]["payload"]["artifact_id"] == "text-3"
    with Session(consumer.engine) as session:
        assert (
            session.get(
                PodcastTextPublicationRecord, "episode-1:transcript_zh"
            ).artifact_id
            == "text-3"
        )
        assert session.get(PodcastTextArtifactRecord, "text-1") is not None

    with Session(producer.engine) as session:
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        publication.status = "unpublished"
        publication.unpublished_at = "2026-09-05T14:00:00+00:00"
        publication.updated_at = publication.unpublished_at
        session.add(publication)
        session.commit()
    (_, withdrawn_rows), withdrawn_result = _copy(
        producer,
        consumer,
        "podcast_texts",
        since=replace_manifest["snapshot"],
    )
    assert withdrawn_rows[0]["operation"] == "tombstone"
    assert withdrawn_result["deleted"] == 1
    with Session(consumer.engine) as session:
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        assert publication.status == "unpublished"
        assert session.get(PodcastTextArtifactRecord, "text-3") is not None


def test_normalized_transcript_publication_round_trips_without_local_attempt(tmp_path):
    producer = _sink(tmp_path, "producer-normalized.db")
    consumer = _sink(tmp_path, "consumer-normalized.db")
    transcript = canonical_normalized_transcript(
        {
            "audio_duration_ms": 1_000,
            "text": "你好",
            "language": "zh-CN",
            "segments": [
                {
                    "text": "你好",
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "channel": 0,
                    "words": [
                        {
                            "text": "你好",
                            "start_ms": 0,
                            "end_ms": 1_000,
                            "channel": 0,
                            "confidence": 0.99,
                        }
                    ],
                }
            ],
        }
    )
    transcript_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.add(
            PodcastProcessingRecord(
                id="normalized-processing",
                episode_id="episode-1",
                input_fingerprint="a" * 64,
                pipeline_version="pipeline-v1",
                requested_target="transcript",
                idempotency_key="normalized-processing-v1",
                input_artifact_id="source-media-1",
                input_artifact_kind="source_media_snapshot",
                input_content_hash="b" * 64,
                input_language="und",
                budget_scope="speech-test",
                budget_period="2026-09",
                budget_limit_minor=100,
                per_run_budget_minor=100,
                eligibility_status="eligible",
                processing_status="ready",
                stage="asr",
                queued_at=STAMP,
                updated_at=STAMP,
                created_at=STAMP,
            )
        )
        session.add(
            PodcastStageAttemptRecord(
                id="normalized-attempt",
                processing_id="normalized-processing",
                stage="asr",
                attempt_no=1,
                fencing_token=1,
                lease_token="lease",
                input_hash="b" * 64,
                output_hash=transcript_hash,
                settings_fingerprint="c" * 64,
                output_artifact_id="normalized-text",
                output_artifact_kind="normalized_transcript",
                provider_request_key="normalized-request",
                execution_kind="provider",
                submission_state="succeeded",
                started_at=STAMP,
                completed_at=STAMP,
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.add(
            PodcastTextArtifactRecord(
                id="normalized-text",
                episode_id="episode-1",
                kind="normalized_transcript",
                version=1,
                content_hash=transcript_hash,
                inline_text=transcript,
                language="zh-cn",
                source_artifact_id="source-media-1",
                source_content_hash="b" * 64,
                processing_id="normalized-processing",
                producing_attempt_id="normalized-attempt",
                provenance_json='{"pipeline":"asr-v1"}',
                created_at=STAMP,
            )
        )
        session.add(
            _publication(
                "normalized-text",
                kind="normalized_transcript",
            )
        )
        session.commit()

    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    raw = archive_sync_v2.export_page(producer.engine, "podcast_texts")
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="podcast_texts")
    wrong_language = json.loads(json.dumps(rows))
    wrong_language[0]["payload"]["artifact"]["language"] = "en-US"
    wrong_language[0]["checksum"] = archive_sync_v2.checksum(
        wrong_language[0]["payload"]
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="language does not match"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(manifest, wrong_language),
            expected_stream="podcast_texts",
        )
    with Session(consumer.engine) as session:
        assert session.exec(select(PodcastTextPublicationRecord)).all() == []
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []
    result = archive_sync_v2.import_page(
        consumer.engine, raw, expected_stream="podcast_texts"
    )
    assert result["inserted"] == 1
    assert rows[0]["payload"]["kind"] == "normalized_transcript"
    authority = archive_sync_v2.producer_authority_id(producer.engine)
    with Session(consumer.engine) as session:
        artifact = session.get(PodcastTextArtifactRecord, "normalized-text")
        publication = session.get(
            PodcastTextPublicationRecord,
            "episode-1:normalized_transcript",
        )
        assert artifact is not None and publication is not None
        assert artifact.authority_id == publication.authority_id == authority
        assert artifact.processing_id is None
        assert artifact.producing_attempt_id is None
        assert session.exec(select(PodcastStageAttemptRecord)).all() == []


def test_podcast_text_validation_is_atomic_and_never_accepts_audio(tmp_path):
    producer = _sink(tmp_path, "producer-invalid.db")
    consumer = _sink(tmp_path, "consumer-invalid.db")
    _prepare_two_episodes(producer, consumer)
    raw = archive_sync_v2.export_page(producer.engine, "podcast_texts")
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="podcast_texts")

    corrupted = json.loads(json.dumps(rows))
    corrupted[1]["payload"]["artifact"]["inline_text"] = "被篡改"
    corrupted[1]["checksum"] = archive_sync_v2.checksum(corrupted[1]["payload"])
    with pytest.raises(archive_sync_v2.SyncV2Error, match="content_hash"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(manifest, corrupted),
            expected_stream="podcast_texts",
        )
    with Session(consumer.engine) as session:
        assert session.exec(select(PodcastTextPublicationRecord)).all() == []
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []

    malformed_payload = json.loads(json.dumps(rows[0]["payload"]))
    malformed_payload["identity"] = "episode-1:normalized_transcript"
    malformed_payload["kind"] = "normalized_transcript"
    malformed_payload["artifact"]["kind"] = "normalized_transcript"
    malformed_payload["artifact"]["inline_text"] = '{"text":"not canonical"}'
    malformed_payload["artifact"]["content_hash"] = hashlib.sha256(
        malformed_payload["artifact"]["inline_text"].encode("utf-8")
    ).hexdigest()
    malformed_row = archive_sync_v2._line(  # noqa: SLF001 - wire fixture
        "podcast_texts",
        malformed_payload,
        revision="998",
        identity=malformed_payload["identity"],
    )
    malformed_manifest = archive_sync_v2._manifest(  # noqa: SLF001 - wire fixture
        "podcast_texts", "external", "998", "", [malformed_row], complete=True
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="not canonical"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(malformed_manifest, [malformed_row]),
            expected_stream="podcast_texts",
        )
    with Session(consumer.engine) as session:
        assert session.exec(select(PodcastTextPublicationRecord)).all() == []
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []

    audio_payload = json.loads(json.dumps(rows[0]["payload"]))
    audio_payload["identity"] = "episode-1:digest_audio_zh"
    audio_payload["kind"] = "digest_audio_zh"
    audio_payload["artifact_id"] = "audio-must-not-sync"
    audio_payload["artifact"].update(id="audio-must-not-sync", kind="digest_audio_zh")
    audio_row = archive_sync_v2._line(  # noqa: SLF001 - wire fixture
        "podcast_texts",
        audio_payload,
        revision="999",
        identity=audio_payload["identity"],
    )
    audio_manifest = archive_sync_v2._manifest(  # noqa: SLF001 - wire fixture
        "podcast_texts", "external", "999", "", [audio_row], complete=True
    )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="not canonical"):
        archive_sync_v2.parse_page(
            archive_sync_v2.encode_page(audio_manifest, [audio_row]),
            expected_stream="podcast_texts",
        )


def test_podcast_text_size_limits_reject_export_and_atomic_import(tmp_path):
    producer = _sink(tmp_path, "producer-size-limits.db")
    consumer = _sink(tmp_path, "consumer-size-limits.db")
    _prepare_two_episodes(producer, consumer)

    with pytest.raises(archive_sync_v2.SyncV2Error, match="字符上限"):
        archive_sync_v2.export_page(
            producer.engine,
            "podcast_texts",
            podcast_text_max_chars=5,
        )

    raw = archive_sync_v2.export_page(producer.engine, "podcast_texts")
    with pytest.raises(archive_sync_v2.SyncV2Error, match="字节上限"):
        archive_sync_v2.parse_page(
            raw,
            expected_stream="podcast_texts",
            podcast_text_max_bytes=5,
        )
    with pytest.raises(archive_sync_v2.SyncV2Error, match="字节上限"):
        archive_sync_v2.import_page(
            consumer.engine,
            raw,
            expected_stream="podcast_texts",
            podcast_text_max_bytes=5,
        )
    with Session(consumer.engine) as session:
        assert session.exec(select(PodcastTextPublicationRecord)).all() == []
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []


def test_podcast_text_sync_page_limits_bound_rows_bytes_and_paginate(tmp_path):
    producer = _sink(tmp_path, "producer-page-limits.db")
    consumer = _sink(tmp_path, "consumer-page-limits.db")
    _prepare_two_episodes(producer, consumer)

    full = archive_sync_v2.export_page(producer.engine, "podcast_texts")
    manifest, rows = archive_sync_v2.parse_page(
        full,
        expected_stream="podcast_texts",
    )
    assert len(rows) == 2
    with pytest.raises(archive_sync_v2.SyncV2Error, match="row limit"):
        archive_sync_v2.parse_page(
            full,
            expected_stream="podcast_texts",
            requested_limit=1,
        )

    first_only = archive_sync_v2.export_page(
        producer.engine,
        "podcast_texts",
        limit=1,
    )
    seed_manifest, _seed_rows = archive_sync_v2.parse_page(
        first_only,
        expected_stream="podcast_texts",
    )
    second_only = archive_sync_v2.export_page(
        producer.engine,
        "podcast_texts",
        snapshot=seed_manifest["snapshot"],
        after=seed_manifest["next_cursor"],
        limit=1,
    )
    byte_cap = (
        max(
            len(first_only.encode("utf-8")),
            len(second_only.encode("utf-8")),
        )
        + 32
    )
    assert len(full.encode("utf-8")) > byte_cap
    bounded = archive_sync_v2.export_page(
        producer.engine,
        "podcast_texts",
        podcast_text_page_max_bytes=len(full.encode("utf-8")) + 1024,
        podcast_text_requested_page_max_bytes=byte_cap,
    )
    first_manifest, first_rows = archive_sync_v2.parse_page(
        bounded,
        expected_stream="podcast_texts",
        podcast_text_page_max_bytes=byte_cap,
    )
    assert len(first_rows) == 1
    assert first_manifest["complete"] is False

    second = archive_sync_v2.export_page(
        producer.engine,
        "podcast_texts",
        snapshot=first_manifest["snapshot"],
        after=first_manifest["next_cursor"],
        podcast_text_page_max_bytes=byte_cap,
    )
    second_manifest, second_rows = archive_sync_v2.parse_page(
        second,
        expected_stream="podcast_texts",
        podcast_text_page_max_bytes=byte_cap,
    )
    assert len(second_rows) == 1
    assert second_manifest["complete"] is True

    with pytest.raises(archive_sync_v2.SyncV2Error, match="aggregate byte limit"):
        archive_sync_v2.import_page(
            consumer.engine,
            full,
            expected_stream="podcast_texts",
            podcast_text_page_max_bytes=len(full.encode("utf-8")) - 1,
        )
    with Session(consumer.engine) as session:
        assert session.exec(select(PodcastTextPublicationRecord)).all() == []
        assert session.exec(select(PodcastTextArtifactRecord)).all() == []


def test_podcast_text_authority_fence_and_presence_pruning(tmp_path):
    producer = _sink(tmp_path, "producer-authority.db")
    consumer = _sink(tmp_path, "consumer-authority.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    authority = archive_sync_v2.producer_authority_id(producer.engine)

    with Session(consumer.engine) as session:
        session.add(_artifact("local-text"))
        session.add(_publication("local-text"))
        session.commit()
    stale = archive_sync_v2.full_authority_stale_identities(
        consumer.engine, "podcast_texts", authority
    )
    assert stale == ["episode-1:transcript_zh"]
    assert (
        archive_sync_v2.authority_present_identities(
            producer.engine, "podcast_texts", stale
        )
        == []
    )
    assert (
        archive_sync_v2.finalize_full_authority_stream(
            consumer.engine,
            "podcast_texts",
            authority,
            absent_identities=stale,
        )
        == 1
    )
    with Session(consumer.engine) as session:
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        assert publication.status == "unpublished"
        assert publication.authority_id == ""

    artifact = _artifact("incoming-text")
    publication_data = archive_sync_v2._podcast_text_payload(  # noqa: SLF001
        _publication("incoming-text"), artifact
    )
    row = archive_sync_v2._line(  # noqa: SLF001
        "podcast_texts",
        publication_data,
        revision="999",
        identity=publication_data["identity"],
    )
    manifest = archive_sync_v2._manifest(  # noqa: SLF001
        "podcast_texts", authority, "999", "", [row], complete=True
    )
    encoded = archive_sync_v2.encode_page(manifest, [row])
    with pytest.raises(
        archive_sync_v2.SyncV2Error,
        match=r"authority mismatch: existing '<local>'",
    ):
        archive_sync_v2.import_page(
            consumer.engine,
            encoded,
            expected_stream="podcast_texts",
        )

    with Session(consumer.engine) as session:
        local_publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        session.delete(local_publication)
        session.commit()
        session.add(
            _artifact(
                "another-authority-text",
                version=2,
                authority_id="another-authority",
            )
        )
        session.add(
            _publication(
                "another-authority-text",
                authority_id="another-authority",
            )
        )
        session.commit()

    with pytest.raises(
        archive_sync_v2.SyncV2Error,
        match=r"authority mismatch: existing 'another-authority'",
    ):
        archive_sync_v2.import_page(
            consumer.engine,
            encoded,
            expected_stream="podcast_texts",
        )

    tombstone_payload = {
        "identity": "episode-1:transcript_zh",
        "tombstone": True,
    }
    tombstone_row = archive_sync_v2._line(  # noqa: SLF001
        "podcast_texts",
        tombstone_payload,
        revision="1000",
        identity=tombstone_payload["identity"],
        operation="tombstone",
    )
    tombstone_manifest = archive_sync_v2._manifest(  # noqa: SLF001
        "podcast_texts", authority, "1000", "", [tombstone_row], complete=True
    )
    with pytest.raises(
        archive_sync_v2.SyncV2Error,
        match=r"authority mismatch: existing 'another-authority'",
    ):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(tombstone_manifest, [tombstone_row]),
            expected_stream="podcast_texts",
        )


def test_podcast_text_presence_prune_preserves_remote_authority(tmp_path):
    producer = _sink(tmp_path, "producer-remote-stale.db")
    consumer = _sink(tmp_path, "consumer-remote-stale.db")
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    authority = archive_sync_v2.producer_authority_id(producer.engine)

    with Session(consumer.engine) as session:
        session.add(_artifact("remote-stale", authority_id=authority))
        session.add(_publication("remote-stale", authority_id=authority))
        session.commit()
    stale = archive_sync_v2.full_authority_stale_identities(
        consumer.engine, "podcast_texts", authority
    )
    assert stale == ["episode-1:transcript_zh"]
    assert (
        archive_sync_v2.finalize_full_authority_stream(
            consumer.engine,
            "podcast_texts",
            authority,
            absent_identities=stale,
        )
        == 1
    )

    with Session(consumer.engine) as session:
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        assert publication.status == "unpublished"
        assert publication.authority_id == authority


def test_narration_sync_replacement_withdraws_only_dependent_local_audio(tmp_path):
    producer = _sink(tmp_path, "producer-script-replace.db")
    consumer = _sink(tmp_path, "consumer-script-replace.db")
    script_v1 = _artifact(
        "script-v1", kind="narration_script_zh", text="第一版口播稿。"
    )
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.commit()
        session.add(script_v1)
        session.add(_publication("script-v1", kind="narration_script_zh"))
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    (initial_manifest, _), _ = _copy(producer, consumer, "podcast_texts")

    with Session(consumer.engine) as session:
        remote_script = session.get(PodcastTextArtifactRecord, "script-v1")
        session.add(_local_digest_audio(episode_id="episode-1", script=remote_script))
        session.commit()

    # An unrelated text publication must not touch the local audio registry.
    with Session(producer.engine) as session:
        blog = _artifact("blog-v1", kind="digest_blog_zh", text="中文博客摘要。")
        session.add(blog)
        session.add(_publication("blog-v1", kind="digest_blog_zh"))
        session.commit()
    (blog_manifest, _), _ = _copy(
        producer,
        consumer,
        "podcast_texts",
        since=initial_manifest["snapshot"],
    )
    with Session(consumer.engine) as session:
        assert (
            session.get(PodcastArtifactRecord, "audio-episode-1").status == "published"
        )

    # Empty subsequent pulls and reopening the same database preserve local audio.
    _copy(producer, consumer, "podcast_texts", since=blog_manifest["snapshot"])
    reopened = _sink(tmp_path, "consumer-script-replace.db")
    with Session(reopened.engine) as session:
        assert (
            session.get(PodcastArtifactRecord, "audio-episode-1").status == "published"
        )

    with Session(producer.engine) as session:
        script_v2 = _artifact(
            "script-v2",
            kind="narration_script_zh",
            version=2,
            text="第二版口播稿。",
        )
        session.add(script_v2)
        publication = session.get(
            PodcastTextPublicationRecord,
            "episode-1:narration_script_zh",
        )
        publication.artifact_id = script_v2.id
        publication.updated_at = "2026-09-05T13:00:00+00:00"
        session.add(publication)
        session.commit()
    _copy(producer, reopened, "podcast_texts", since=blog_manifest["snapshot"])
    with Session(reopened.engine) as session:
        audio = session.get(PodcastArtifactRecord, "audio-episode-1")
        assert audio.status == "withdrawn"
        assert audio.content_hash == "f" * 64
        assert audio.narration_artifact_id == "script-v1"


def test_narration_sync_tombstone_withdraws_audio_without_deleting_registry(tmp_path):
    producer = _sink(tmp_path, "producer-script-tombstone.db")
    consumer = _sink(tmp_path, "consumer-script-tombstone.db")
    script = _artifact(
        "script-tombstone", kind="narration_script_zh", text="即将撤下的口播稿。"
    )
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.commit()
        session.add(script)
        session.add(_publication(script.id, kind="narration_script_zh"))
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    (initial_manifest, _), _ = _copy(producer, consumer, "podcast_texts")
    with Session(consumer.engine) as session:
        remote_script = session.get(PodcastTextArtifactRecord, "script-tombstone")
        session.add(_local_digest_audio(episode_id="episode-1", script=remote_script))
        session.commit()
    with Session(producer.engine) as session:
        publication = session.get(
            PodcastTextPublicationRecord,
            "episode-1:narration_script_zh",
        )
        publication.status = "unpublished"
        publication.unpublished_at = "2026-09-05T14:00:00+00:00"
        publication.updated_at = publication.unpublished_at
        session.add(publication)
        session.commit()
    _copy(producer, consumer, "podcast_texts", since=initial_manifest["snapshot"])
    with Session(consumer.engine) as session:
        audio = session.get(PodcastArtifactRecord, "audio-episode-1")
        assert audio is not None
        assert audio.status == "withdrawn"


def test_corrupt_podcast_text_page_rolls_back_script_audio_withdrawal(tmp_path):
    producer = _sink(tmp_path, "producer-script-atomic.db")
    consumer = _sink(tmp_path, "consumer-script-atomic.db")
    script_v1 = _artifact(
        "script-atomic-v1", kind="narration_script_zh", text="原口播稿。"
    )
    transcript_v1 = _artifact(
        "transcript-atomic-v1", episode_id="episode-2", text="原逐字稿。"
    )
    with Session(producer.engine) as session:
        session.add(_source())
        session.add(_episode("episode-1"))
        session.add(_episode("episode-2"))
        session.commit()
        session.add(script_v1)
        session.add(transcript_v1)
        session.add(_publication(script_v1.id, kind="narration_script_zh"))
        session.add(_publication(transcript_v1.id, episode_id="episode-2"))
        session.commit()
    _copy(producer, consumer, "sources")
    _copy(producer, consumer, "articles")
    (initial_manifest, _), _ = _copy(producer, consumer, "podcast_texts")
    with Session(consumer.engine) as session:
        remote_script = session.get(PodcastTextArtifactRecord, "script-atomic-v1")
        session.add(_local_digest_audio(episode_id="episode-1", script=remote_script))
        session.commit()

    with Session(producer.engine) as session:
        script_v2 = _artifact(
            "script-atomic-v2",
            kind="narration_script_zh",
            version=2,
            text="新口播稿。",
        )
        transcript_v2 = _artifact(
            "transcript-atomic-v2",
            episode_id="episode-2",
            version=2,
            text="新逐字稿。",
        )
        session.add(script_v2)
        session.add(transcript_v2)
        script_publication = session.get(
            PodcastTextPublicationRecord,
            "episode-1:narration_script_zh",
        )
        transcript_publication = session.get(
            PodcastTextPublicationRecord,
            "episode-2:transcript_zh",
        )
        script_publication.artifact_id = script_v2.id
        transcript_publication.artifact_id = transcript_v2.id
        script_publication.updated_at = transcript_publication.updated_at = (
            "2026-09-05T15:00:00+00:00"
        )
        session.add(script_publication)
        session.add(transcript_publication)
        session.commit()
    raw = archive_sync_v2.export_page(
        producer.engine,
        "podcast_texts",
        since=initial_manifest["snapshot"],
    )
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream="podcast_texts")
    assert [row["identity"] for row in rows] == [
        "episode-1:narration_script_zh",
        "episode-2:transcript_zh",
    ]
    corrupted = json.loads(json.dumps(rows))
    corrupted[1]["payload"]["artifact"]["inline_text"] = "被篡改"
    corrupted[1]["checksum"] = archive_sync_v2.checksum(corrupted[1]["payload"])
    with pytest.raises(archive_sync_v2.SyncV2Error, match="content_hash"):
        archive_sync_v2.import_page(
            consumer.engine,
            archive_sync_v2.encode_page(manifest, corrupted),
            expected_stream="podcast_texts",
        )
    with Session(consumer.engine) as session:
        publication = session.get(
            PodcastTextPublicationRecord,
            "episode-1:narration_script_zh",
        )
        assert publication.artifact_id == "script-atomic-v1"
        assert (
            session.get(PodcastArtifactRecord, "audio-episode-1").status == "published"
        )
