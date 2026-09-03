#!/usr/bin/env python3
"""Run a bounded full-analysis backfill against an isolated database copy.

The command refuses the configured application database.  Pass ``--live-llm``
only after inspecting the estimate; without it the command is a read-only dry
run.  Output contains counts and versions, never article titles, bodies or URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlmodel import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import settings  # noqa: E402
from models.db import TagRetagJobRecord  # noqa: E402
from services import analysis_backfill, article_analysis, daily_brief  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke a bounded full_analysis backfill.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--max-articles", type=int, default=5)
    parser.add_argument("--live-llm", action="store_true")
    return parser.parse_args()


def _sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


async def run(args: argparse.Namespace) -> int:
    target_path = _sqlite_path(args.database_url)
    configured_path = _sqlite_path(settings.storage.database_url)
    if target_path is not None and configured_path is not None and target_path == configured_path:
        raise ValueError("refusing to run full-analysis smoke against the configured application database")
    if args.max_articles < 1:
        raise ValueError("max-articles must be positive")

    storage = DatabaseStorage(db_url=args.database_url)
    try:
        with Session(storage.engine) as session:
            estimate = analysis_backfill.estimate_full_analysis_backfill(
                session,
                days=None,
                selection="all",
                source_ids=[args.source_id],
            )
            print(json.dumps({"phase": "estimate", **estimate}, ensure_ascii=False, sort_keys=True))
            if not estimate["ready"]:
                raise ValueError("full-analysis estimate is not ready")
            if estimate["article_count"] > args.max_articles:
                raise ValueError(
                    f"estimate has {estimate['article_count']} articles; max is {args.max_articles}"
                )
            if not args.live_llm:
                return 0
            llm_config = daily_brief.resolve_llm_config(session)
            if not llm_config.configured:
                raise ValueError("LLM is not configured")
            candidate_enabled = article_analysis.read_feature_flag(
                session,
                article_analysis.TAXONOMY_CANDIDATE_ENABLED_KEY,
                default=False,
            )
            job = analysis_backfill.create_full_analysis_backfill(
                session,
                days=None,
                selection="all",
                source_ids=[args.source_id],
                actor_id="smoke-full-analysis",
                confirmation=analysis_backfill.FULL_ANALYSIS_CONFIRMATION,
            )
            job_id = int(job.id)

        for cycle in range(max(3, args.max_articles * 3)):
            results = await article_analysis.run_analysis_cycle(
                storage.engine,
                worker_id="smoke-full-analysis",
                llm_config=llm_config,
                enabled=True,
                candidate_enabled=candidate_enabled,
                batch_size=min(2, args.max_articles),
            )
            with Session(storage.engine) as session:
                job = session.get(TagRetagJobRecord, job_id)
                state = analysis_backfill.serialize_full_analysis_backfill(
                    session,
                    job,
                    include_failures=True,
                )
            print(
                json.dumps(
                    {
                        "phase": "cycle",
                        "cycle": cycle + 1,
                        "processed": len(results),
                        "job": state,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            if state["status"] in {
                "succeeded",
                "partial_failed",
                "failed",
                "cancelled",
            }:
                return 0 if state["status"] == "succeeded" else 1
            if not results and state["counts"]["queued"]:
                break
        raise RuntimeError("full-analysis smoke did not reach a terminal state")
    finally:
        storage.engine.dispose()


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
