import asyncio
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def test_fetcher_exception_is_marked_failed(monkeypatch, tmp_path):
    import api.app as app_module
    from fetchers.base import BaseFetcher
    from models.db import FetchRunRecord
    from pipeline.core import DataPipeline

    class FailingFetcher(BaseFetcher):
        source_id = "failing_fetcher"
        content_type = "rss_article"
        name = "Failing fetcher"

        async def _run(self, client, **kwargs):
            raise RuntimeError("detail page exploded")
            yield  # pragma: no cover

    sink = _make_sink(tmp_path, "fetch_failures.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "pipeline", DataPipeline(storages=[sink]))
    monkeypatch.setattr(app_module.fetcher_registry, "get_class", lambda fetcher_id: FailingFetcher)

    async def noop_auto_vectorize(content_ids):
        return None

    monkeypatch.setattr(app_module, "auto_vectorize_after_fetch", noop_auto_vectorize)

    with pytest.raises(RuntimeError, match="detail page exploded"):
        asyncio.run(app_module.run_fetcher_with_tracking("failing_fetcher", {}))

    with Session(sink.engine) as session:
        runs = session.exec(select(FetchRunRecord)).all()
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].error_message == "detail page exploded"


def test_collection_result_surfaces_partial_failure(monkeypatch, tmp_path):
    import api.app as app_module

    class FakeFetcher:
        pass

    class SelectivePipeline:
        async def run_task(self, fetcher, lineage=None, **kwargs):
            from types import SimpleNamespace

            marker = kwargs["marker"]
            if marker == "bad":
                raise RuntimeError("bad article failed")
            return SimpleNamespace(
                fetched_count=1,
                saved_count=1,
                skipped_count=0,
                saved_content_ids=[marker],
                latest_content_id=marker,
                latest_content_publish_date="2026-05-28T00:00:00",
                latest_content_source_id=marker,
                latest_content_type="test_content",
            )

    sink = _make_sink(tmp_path, "collection_partial_failures.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module, "pipeline", SelectivePipeline())
    monkeypatch.setattr(app_module.fetcher_registry, "get_class", lambda fetcher_id: FakeFetcher)

    async def noop_auto_vectorize(content_ids):
        return None

    monkeypatch.setattr(app_module, "auto_vectorize_after_fetch", noop_auto_vectorize)

    result = asyncio.run(app_module.run_collection_items(
        [
            {"fetcher_id": "ok_fetcher", "params": {"marker": "ok"}},
            {"fetcher_id": "bad_fetcher", "params": {"marker": "bad"}},
        ],
        name="partial failure",
    ))

    assert result["status"] == "partial_failed"
    assert result["failed_count"] == 1
    assert result["saved_count"] == 1
    assert "bad_fetcher: bad article failed" in result["error_message"]
