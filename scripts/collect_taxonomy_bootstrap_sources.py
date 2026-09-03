#!/usr/bin/env python3
"""Collect the frozen taxonomy-v1 public sources into an isolated SQLite copy.

This tool intentionally bypasses collection jobs and source cursors.  It uses
the same registered fetchers and ``DatabaseStorage.save`` contract, but refuses
the configured application database so bootstrap expansion cannot move normal
incremental cursors or mutate production state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import settings  # noqa: E402
from fetchers.registry import fetcher_registry  # noqa: E402
from services.taxonomy_bootstrap import TAXONOMY_BOOTSTRAP_V1_SOURCE_IDS  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import ensure_migrated  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect taxonomy bootstrap sources in isolation.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--sources", default="", help="Comma-separated subset of the frozen manifest")
    parser.add_argument("--per-source-limit", type=int, default=15)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--report-out", type=Path)
    return parser.parse_args(argv)


def _database_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url == "sqlite:///:memory:":
        return None
    return Path(database_url[len(prefix) :]).expanduser().resolve()


def assert_isolated_database(database_url: str) -> None:
    target = _database_path(database_url)
    configured = _database_path(settings.storage.database_url)
    if target is None:
        raise ValueError("bootstrap collection requires a file-backed SQLite database")
    if configured is not None and target == configured:
        raise ValueError("refusing to collect bootstrap sources into the configured application database")


def selected_sources(raw: str) -> list[str]:
    frozen = set(TAXONOMY_BOOTSTRAP_V1_SOURCE_IDS)
    requested = [value.strip() for value in raw.split(",") if value.strip()]
    values = requested or list(TAXONOMY_BOOTSTRAP_V1_SOURCE_IDS)
    unknown = [value for value in values if value not in frozen]
    if unknown:
        raise ValueError(f"sources are outside taxonomy-bootstrap-v1: {unknown}")
    return list(dict.fromkeys(values))


async def collect_one(
    storage: DatabaseStorage,
    source_id: str,
    *,
    limit: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    fetcher_class = fetcher_registry.get_class(source_id)
    if fetcher_class is None:
        return {"source_id": source_id, "status": "unregistered", "seen": 0, "saved": 0}
    fetcher = fetcher_class(timeout=min(timeout_seconds, 45), max_retries=1)
    fetcher.dedup_lookup = storage.existing_content_flags
    schema_fields = {row.get("field") for row in fetcher_class.get_parameter_schema()}
    kwargs = {"limit": limit} if "limit" in schema_fields else {}

    async def consume() -> dict[str, Any]:
        seen = 0
        saved = 0
        async for item in fetcher.fetch(**kwargs):
            seen += 1
            saved += int(await storage.save(item))
            if seen >= limit:
                break
        return {"source_id": source_id, "status": "ok", "seen": seen, "saved": saved}

    try:
        return await asyncio.wait_for(consume(), timeout=timeout_seconds)
    except TimeoutError:
        return {
            "source_id": source_id,
            "status": "timeout",
            "seen": 0,
            "saved": 0,
            "error": f"exceeded {timeout_seconds}s",
        }
    except Exception as exc:  # noqa: BLE001 - every source must leave an auditable result
        return {
            "source_id": source_id,
            "status": "failed",
            "seen": 0,
            "saved": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    assert_isolated_database(args.database_url)
    # A production snapshot may predate the analysis/taxonomy migrations.
    # Upgrade only the already-validated isolated target before any fetcher
    # writes, so the following manifest/draft steps see the complete schema.
    ensure_migrated(args.database_url)
    limit = max(1, min(int(args.per_source_limit), 20))
    timeout_seconds = max(10, min(int(args.timeout_seconds), 300))
    storage = DatabaseStorage(args.database_url)
    semaphore = asyncio.Semaphore(max(1, min(int(args.concurrency), 8)))

    async def guarded(source_id: str) -> dict[str, Any]:
        async with semaphore:
            return await collect_one(
                storage,
                source_id,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )

    try:
        results = await asyncio.gather(*(guarded(source_id) for source_id in selected_sources(args.sources)))
    finally:
        storage.engine.dispose()
    return {
        "database_url": args.database_url,
        "per_source_limit": limit,
        "source_count": len(results),
        "ok_source_count": sum(item["status"] == "ok" and item["seen"] > 0 for item in results),
        "seen": sum(int(item["seen"]) for item in results),
        "saved": sum(int(item["saved"]) for item in results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["ok_source_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
