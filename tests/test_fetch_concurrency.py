import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def test_collection_fetch_items_run_with_limited_concurrency(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "fetch_concurrency.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(app_module.fetcher_registry, "get_class", lambda fetcher_id: FakeFetcher)

    fake_pipeline = FakePipeline()
    monkeypatch.setattr(app_module, "pipeline", fake_pipeline)

    async def noop_auto_vectorize(content_ids):
        return None

    monkeypatch.setattr(app_module, "auto_vectorize_after_fetch", noop_auto_vectorize)

    items = [
        {"fetcher_id": f"fetcher_{index}", "params": {"marker": f"item_{index}"}}
        for index in range(4)
    ]

    result = asyncio.run(
        app_module.run_collection_items(
            items,
            name="并发测试",
            max_concurrency=2,
        )
    )

    assert result["status"] == "success"
    assert result["saved_count"] == 4
    assert [item["fetcher_id"] for item in result["results"]] == [item["fetcher_id"] for item in items]
    assert fake_pipeline.max_active == 2


class FakeFetcher:
    pass


class FakePipeline:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def run_task(self, fetcher, lineage=None, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        marker = kwargs["marker"]
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
