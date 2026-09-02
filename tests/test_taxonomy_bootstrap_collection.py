"""Safety and bounded-fetch tests for isolated taxonomy source expansion."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import collect_taxonomy_bootstrap_sources as collection  # noqa: E402


def test_source_selection_is_frozen_and_rejects_outside_nodes():
    assert collection.selected_sources("rss_openai_news,rss_openai_news") == ["rss_openai_news"]
    with pytest.raises(ValueError, match="outside taxonomy-bootstrap-v1"):
        collection.selected_sources("generic_rss")


def test_collection_refuses_the_configured_application_database():
    with pytest.raises(ValueError, match="configured application database"):
        collection.assert_isolated_database(collection.settings.storage.database_url)


def test_collect_one_honors_limit_and_uses_normal_storage_contract(monkeypatch):
    class FakeFetcher:
        def __init__(self, **_kwargs):
            self.dedup_lookup = None

        @classmethod
        def get_parameter_schema(cls):
            return [{"field": "limit"}]

        async def fetch(self, **kwargs):
            assert kwargs == {"limit": 2}
            for index in range(4):
                yield SimpleNamespace(id=f"item-{index}")

    class FakeStorage:
        def __init__(self):
            self.saved = []

        async def existing_content_flags(self, _ids):
            return {}

        async def save(self, item):
            self.saved.append(item.id)
            return True

    monkeypatch.setattr(collection.fetcher_registry, "get_class", lambda _source_id: FakeFetcher)
    storage = FakeStorage()
    result = asyncio.run(
        collection.collect_one(storage, "rss_openai_news", limit=2, timeout_seconds=10)
    )
    assert result == {"source_id": "rss_openai_news", "status": "ok", "seen": 2, "saved": 2}
    assert storage.saved == ["item-0", "item-1"]
