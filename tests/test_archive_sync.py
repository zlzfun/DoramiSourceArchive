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
        "is_vectorized": True,
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
        assert record.is_vectorized is False


def test_archive_sync_import_backfills_empty_existing_record(monkeypatch):
    from api.app import archive_sync_line, import_archive_sync_jsonl
    from models.db import ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    monkeypatch.setattr("api.app.db_sink", sink)

    empty = _article_record(has_content=False, content="", extensions_json="{}", is_vectorized=True)
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
        assert record.is_vectorized is False


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
