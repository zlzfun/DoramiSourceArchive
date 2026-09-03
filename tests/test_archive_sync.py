import json
import os
import sys

from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _jsonl(*items):
    from api.app import _canonical_json

    return "\n".join(_canonical_json(item) for item in items) + "\n"


def _article_record(**overrides):
    from models.db import ArticleRecord

    data = {
        "id": "sync_article_1",
        "title": "Sync Article",
        "content_type": "rss_article",
        "source_id": "rss_test",
        "source_url": "https://example.test/article",
        "publish_date": "2026-05-20T00:00:00",
        "fetched_date": "2026-05-21T01:02:03",
        "fetch_run_id": 10,
        "job_id": 20,
        "job_run_id": 30,
        "source_group_id": 40,
        "run_scope": "saved_job",
        "has_content": True,
        "content": "Full archive body.",
        "extensions_json": json.dumps({"tag": "sync"}, ensure_ascii=False),
    }
    data.update(overrides)
    return ArticleRecord(**data)


def test_archive_sync_import_is_idempotent_and_preserves_lineage(monkeypatch):
    from api.app import archive_manifest_line, archive_sync_line, import_archive_sync_jsonl
    from models.db import ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)

    source_record = _article_record()
    body = _jsonl(archive_manifest_line(1, {"source_id": "rss_test"}), archive_sync_line(source_record))

    first = import_archive_sync_jsonl(body)
    second = import_archive_sync_jsonl(body)

    assert first["status"] == "success"
    assert first["imported_count"] == 1
    assert first["updated_count"] == 0
    assert second["status"] == "success"
    assert second["skipped_count"] == 1

    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, source_record.id)
        assert record is not None
        assert record.fetch_run_id == 10
        assert record.job_id == 20
        assert record.job_run_id == 30
        assert record.source_group_id == 40
        assert record.run_scope == "saved_job"
        assert record.content == "Full archive body."
        assert json.loads(record.extensions_json) == {"tag": "sync"}


def test_archive_sync_import_backfills_empty_existing_record(monkeypatch):
    from api.app import archive_sync_line, import_archive_sync_jsonl
    from models.db import ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)

    empty = _article_record(has_content=False, content="", extensions_json="{}")
    with Session(sink.engine) as session:
        session.add(empty)
        session.commit()

    incoming = _article_record(content="Backfilled body.", extensions_json=json.dumps({"full": True}))
    result = import_archive_sync_jsonl(_jsonl(archive_sync_line(incoming)))

    assert result["status"] == "success"
    assert result["imported_count"] == 0
    assert result["updated_count"] == 1

    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, incoming.id)
        assert record.has_content is True
        assert record.content == "Backfilled body."
        assert json.loads(record.extensions_json) == {"full": True}


def test_archive_sync_merges_newer_podcast_metadata_and_preserves_derived_fields(monkeypatch):
    from api.app import archive_sync_line, import_archive_sync_jsonl
    from models.db import ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)
    old_extensions = {
        "audio_url": "https://cdn.example.test/old.mp3",
        "duration_seconds": 1200,
        "summary_zh": "读者侧生成的中文摘要",
        "processing_status": "audio_ready",
        "condensed_audio_url": "https://media.example.test/short.mp3",
    }
    existing = _article_record(
        id="podcast_sync_1",
        content_type="podcast_episode",
        source_id="podcast_demo",
        fetched_date="2026-09-01T01:00:00",
        archive_updated_at="2026-09-01T01:00:00",
        extensions_json=json.dumps(old_extensions, ensure_ascii=False),
    )
    with Session(sink.engine) as session:
        session.add(existing)
        session.commit()

    incoming_extensions = {
        "audio_url": "https://cdn.example.test/new.mp3",
        "duration_seconds": 2400,
        "transcripts": [{"url": "https://cdn.example.test/episode.vtt", "type": "text/vtt"}],
    }
    incoming = _article_record(
        id="podcast_sync_1",
        title="Corrected episode title",
        content_type="podcast_episode",
        source_id="podcast_demo",
        fetched_date="2026-09-01T01:00:00",
        archive_updated_at="2026-09-03T01:00:00",
        extensions_json=json.dumps(incoming_extensions, ensure_ascii=False),
    )
    result = import_archive_sync_jsonl(_jsonl(archive_sync_line(incoming)))

    assert result["updated_count"] == 1
    assert result["skipped_count"] == 0
    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, incoming.id)
        extensions = json.loads(record.extensions_json)
        assert record.title == "Corrected episode title"
        assert record.fetched_date == "2026-09-01T01:00:00"
        assert record.archive_updated_at == "2026-09-03T01:00:00"
        assert extensions["audio_url"].endswith("/new.mp3")
        assert extensions["duration_seconds"] == 2400
        assert extensions["summary_zh"] == "读者侧生成的中文摘要"
        assert extensions["processing_status"] == "audio_ready"
        assert extensions["condensed_audio_url"].endswith("/short.mp3")

    # Replaying the boundary row is idempotent, as required by the >= cursor.
    repeated = import_archive_sync_jsonl(_jsonl(archive_sync_line(incoming)))
    assert repeated["updated_count"] == 0
    assert repeated["skipped_count"] == 1


def test_archive_export_incremental_cursor_includes_metadata_refresh(monkeypatch):
    from api.routers.archive_sync import export_archive_articles_jsonl
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)
    with Session(sink.engine) as session:
        session.add(_article_record(
            id="podcast_cursor_1",
            content_type="podcast_episode",
            fetched_date="2026-09-01T01:00:00",
            archive_updated_at="2026-09-03T01:00:00",
        ))
        session.commit()

    response = export_archive_articles_jsonl(
        fetched_date_start="2026-09-02T00:00:00",
    )
    lines = [json.loads(line) for line in response.body.decode("utf-8").splitlines()]
    assert lines[0]["count"] == 1
    assert lines[1]["article"]["id"] == "podcast_cursor_1"
    assert lines[1]["article"]["archive_updated_at"] == "2026-09-03T01:00:00"


def test_archive_sync_rejects_checksum_mismatch(monkeypatch):
    from api.app import archive_sync_line, import_archive_sync_jsonl
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)

    line = archive_sync_line(_article_record())
    line["checksum"] = "bad"
    result = import_archive_sync_jsonl(_jsonl(line))

    assert result["status"] == "partial_success"
    assert result["imported_count"] == 0
    assert result["error_count"] == 1
    assert "checksum mismatch" in result["errors"][0]["error"]


def test_archive_sync_checksum_survives_json_roundtrip(monkeypatch):
    from api.app import _canonical_json, archive_sync_line, import_archive_sync_jsonl
    from models.db import ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)

    source_record = _article_record(
        id="sync_article_unicode",
        title="中文标题",
        content="正文包含中文和 symbols.",
        extensions_json=json.dumps(
            {
                "z": ["后", "先"],
                "nested": {"b": True, "a": 1},
            },
            ensure_ascii=False,
        ),
    )
    exported_line = archive_sync_line(source_record)

    # Simulate a real JSONL producer/consumer boundary.
    reparsed_line = json.loads(_canonical_json(exported_line))
    result = import_archive_sync_jsonl(_jsonl(reparsed_line))

    assert result["status"] == "success"
    assert result["imported_count"] == 1
    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, "sync_article_unicode")
        assert record.title == "中文标题"
        assert json.loads(record.extensions_json) == {
            "z": ["后", "先"],
            "nested": {"b": True, "a": 1},
        }
