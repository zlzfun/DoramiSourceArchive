#!/usr/bin/env python3
"""Isolated WP-7 release smoke for live RSS/LLM and persistence boundaries.

The target database must not be the configured application database.  The
script deliberately leaves its isolated database in place so operators can
inspect attempts, editions and SQLite pragmas after a run.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlmodel import Session, func, select  # noqa: E402

from api.routers.personal_briefs import process_pending_edition  # noqa: E402
from config import settings  # noqa: E402
from fetchers.registry import fetcher_registry  # noqa: E402
from models.analysis_contracts import DigestGenerationReason  # noqa: E402
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    UserRecord,
)
from services.article_analysis import (  # noqa: E402
    claim_analysis_tasks,
    get_article_analysis,
    process_claimed_analysis,
    queue_article_analysis,
    recover_expired_leases,
    run_analysis_cycle,
)
from services.personal_digest import SHANGHAI, start_personal_digest_edition  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated analysis release checks without changing production flags."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--rss-source", default="rss_simonwillison")
    parser.add_argument(
        "--skip-live-rss",
        action="store_true",
        help="Use a synthetic public article; intended for deterministic CI only.",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Spend one real configured LLM request instead of the deterministic analyzer.",
    )
    parser.add_argument("--writers", type=int, default=6)
    return parser.parse_args(argv)


def _database_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url == "sqlite:///:memory:":
        return None
    return Path(database_url[len(prefix):]).expanduser().resolve()


def _assert_isolated_database(database_url: str) -> None:
    target = _database_path(database_url)
    configured = _database_path(settings.storage.database_url)
    if target is None:
        raise ValueError("release smoke requires a file-backed SQLite database")
    if configured is not None and target == configured:
        raise ValueError("refusing to run release smoke against the configured application database")


async def _fetch_article(source_id: str, *, live: bool):
    if not live:
        return None
    fetcher_class = fetcher_registry.get_class(source_id)
    if fetcher_class is None:
        raise ValueError(f"unknown RSS source: {source_id}")
    if not source_id.startswith("rss_"):
        raise ValueError("--rss-source must name a registered rss_* fetcher")
    fetcher = fetcher_class(timeout=25, max_retries=1)
    kwargs = (
        {"limit": 1}
        if any(row.get("field") == "limit" for row in fetcher_class.get_parameter_schema())
        else {}
    )
    async for item in fetcher.fetch(**kwargs):
        return item
    raise RuntimeError(f"{source_id} returned no content")


def _synthetic_article(source_id: str, article_id: str, now: dt.datetime) -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title="WP-7 isolated release smoke article",
        content_type="rss_article",
        source_id=source_id,
        source_url="https://example.invalid/wp7-release-smoke",
        publish_date=now.isoformat(),
        fetched_date=now.isoformat(),
        has_content=True,
        content=(
            "A public test article describing an AI product release, its implementation "
            "constraints, measured results, and practical impact for developers."
        ),
    )


def _fake_analysis_payload() -> dict[str, Any]:
    return {
        "quality_score": 8.2,
        "score_reason": "信息完整、边界明确，并包含可复核的实践价值。",
        "one_sentence_summary": "文章说明了一项 AI 产品能力及其使用边界。",
        "summary": "文章介绍了能力、实现约束、验证结果和对开发者的实际影响。",
        "content_genre": "product_update",
        "primary_tag_code": None,
        "tag_assignments": [],
        "tag_candidates": [],
        "content_features": ["implementation_details"],
        "entities": [],
    }


async def _fake_analyzer(*_args):
    return _fake_analysis_payload()


async def _live_analysis_check(
    database_url: str,
    *,
    source_id: str,
    live_rss: bool,
    live_llm: bool,
) -> tuple[dict[str, Any], ArticleRecord]:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    fetched = await _fetch_article(source_id, live=live_rss)
    storage = DatabaseStorage(database_url)
    try:
        if fetched is None:
            article = _synthetic_article(source_id, "wp7-live-analysis", now)
            with Session(storage.engine) as session:
                session.add(article)
                session.commit()
                session.refresh(article)
                session.expunge(article)
            saved = True
        else:
            saved = await storage.save(fetched)
            with Session(storage.engine) as session:
                article = session.get(ArticleRecord, fetched.id)
                if article is None:
                    raise RuntimeError("fetched article was not persisted")
        with Session(storage.engine) as session:
            queued = queue_article_analysis(session, article.id, enabled=True, now=now)

        if live_llm and not settings.llm.configured:
            raise RuntimeError("--live-llm requested but configured LLM is incomplete")
        cycle_kwargs: dict[str, Any] = {}
        if not live_llm:
            cycle_kwargs["analyzer"] = _fake_analyzer
        results = await run_analysis_cycle(
            storage.engine,
            worker_id="wp7-live-smoke",
            llm_config=settings.llm,
            enabled=True,
            candidate_enabled=False,
            batch_size=1,
            scan_limit=1,
            now_fn=lambda: now,
            **cycle_kwargs,
        )
        with Session(storage.engine) as session:
            analysis = get_article_analysis(session, article.id)
            attempt_count = session.exec(
                select(func.count())
                .select_from(ArticleAnalysisAttemptRecord)
                .where(ArticleAnalysisAttemptRecord.article_id == article.id)
            ).one()
        if analysis is None:
            raise RuntimeError("analysis did not persist a succeeded current asset")
        report = {
            "rss_mode": "live" if live_rss else "synthetic",
            "rss_source": article.source_id,
            "rss_title": article.title,
            "content_chars": len(article.content or ""),
            "saved": saved,
            "queue_result": queued,
            "llm_mode": "live" if live_llm else "deterministic",
            "llm_model": settings.llm.model if live_llm else "deterministic-smoke",
            "worker_statuses": [row.status for row in results],
            "quality_score": analysis["quality_score"],
            "content_genre": analysis["content_genre"],
            "summary_chars": len(str(analysis["summary"])),
            "reason_chars": len(str(analysis["score_reason"])),
            "attempt_count": int(attempt_count),
        }
        return report, article
    finally:
        storage.engine.dispose()


async def _restart_recovery_check(
    database_url: str, source_article: ArticleRecord
) -> dict[str, Any]:
    leased_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    restart_id = "wp7-restart-recovery"
    storage = DatabaseStorage(database_url)
    try:
        with Session(storage.engine) as session:
            existing = session.get(ArticleRecord, restart_id)
            if existing is None:
                session.add(
                    ArticleRecord(
                        id=restart_id,
                        title=f"{source_article.title} (restart recovery)",
                        content_type=source_article.content_type,
                        source_id=source_article.source_id,
                        source_url=source_article.source_url,
                        publish_date=leased_at.isoformat(),
                        fetched_date=leased_at.isoformat(),
                        has_content=True,
                        content=source_article.content,
                    )
                )
                session.commit()
            queue_article_analysis(session, restart_id, enabled=True, now=leased_at)
            claimed = claim_analysis_tasks(
                session, worker_id="wp7-dead-worker", limit=1, now=leased_at
            )
        if [row.article_id for row in claimed] != [restart_id]:
            raise RuntimeError("restart fixture was not leased")
    finally:
        storage.engine.dispose()

    recovered_at = leased_at + dt.timedelta(minutes=6)
    storage = DatabaseStorage(database_url)
    try:
        with Session(storage.engine) as session:
            recovered = recover_expired_leases(session, now=recovered_at)
            retry = claim_analysis_tasks(
                session,
                worker_id="wp7-restarted-worker",
                limit=1,
                now=recovered_at + dt.timedelta(seconds=61),
            )
        if recovered != 1 or [row.article_id for row in retry] != [restart_id]:
            raise RuntimeError("expired lease was not reclaimed after reopening the database")
        result = await process_claimed_analysis(
            storage.engine,
            retry[0],
            llm_config=settings.llm,
            analyzer=_fake_analyzer,
            now_fn=lambda: recovered_at + dt.timedelta(seconds=62),
        )
        with Session(storage.engine) as session:
            current = session.get(ArticleAnalysisRecord, restart_id)
            attempts = session.exec(
                select(ArticleAnalysisAttemptRecord)
                .where(ArticleAnalysisAttemptRecord.article_id == restart_id)
                .order_by(ArticleAnalysisAttemptRecord.attempt_no)
            ).all()
        return {
            "recovered_leases": recovered,
            "retry_status": result.status,
            "current_status": current.status if current else None,
            "attempt_statuses": [row.status for row in attempts],
            "reopened_database": True,
        }
    finally:
        storage.engine.dispose()


def _sqlite_concurrency_check(database_url: str, writers: int) -> dict[str, Any]:
    writer_count = max(2, min(32, writers))
    storage = DatabaseStorage(database_url)
    lock_ready = threading.Event()

    def hold_write_lock() -> str | None:
        try:
            with storage.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.exec_driver_sql(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("wp7_concurrency_holder", "held"),
                )
                lock_ready.set()
                time.sleep(0.35)
                connection.commit()
            return None
        except Exception as exc:  # noqa: BLE001 - report exact concurrency failures
            lock_ready.set()
            return f"{type(exc).__name__}: {exc}"

    def write_one(index: int) -> str | None:
        try:
            with Session(storage.engine) as session:
                session.add(
                    AppSettingRecord(key=f"wp7_concurrency_{index}", value=str(index))
                )
                session.commit()
            return None
        except Exception as exc:  # noqa: BLE001 - aggregate without aborting peers
            return f"{type(exc).__name__}: {exc}"

    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=writer_count + 1) as pool:
            holder = pool.submit(hold_write_lock)
            if not lock_ready.wait(timeout=2):
                raise RuntimeError("timed out establishing the SQLite write lock")
            futures = [pool.submit(write_one, index) for index in range(writer_count)]
            errors = [error for error in [holder.result(), *(f.result() for f in futures)] if error]
        with Session(storage.engine) as session:
            persisted = session.exec(
                select(func.count())
                .select_from(AppSettingRecord)
                .where(AppSettingRecord.key.like("wp7_concurrency_%"))
            ).one()
        with storage.engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        return {
            "writers": writer_count,
            "persisted_rows": int(persisted),
            "errors": errors,
            "locked_errors": sum("locked" in error.casefold() for error in errors),
            "journal_mode": journal_mode,
            "busy_timeout_ms": int(busy_timeout),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    finally:
        storage.engine.dispose()


def _deadline_degrade_check(database_url: str) -> dict[str, Any]:
    storage = DatabaseStorage(database_url)
    now = dt.datetime.now(SHANGHAI).replace(microsecond=0)
    username = "wp7-deadline-user"
    source_id = "wp7_release_source"
    article_id = "wp7-deadline-article"
    try:
        with Session(storage.engine) as session:
            session.add(
                UserRecord(
                    username=username,
                    password_hash="release-smoke-not-a-login-secret",
                    role="user",
                    is_active=True,
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
            session.add(
                SourceConfigRecord(
                    source_id=source_id,
                    name="WP-7 deadline source",
                    source_type="rss",
                    is_active=True,
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
            session.add(
                ReaderSubscriptionRecord(
                    owner_username=username,
                    name="WP-7 strict scope",
                    filters_json=json.dumps({"source_ids": source_id}),
                    delivery_policy_json="{}",
                    token_hash="wp7-deadline-token",
                    token_preview="wp7…line",
                    is_active=True,
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
            session.add(
                ArticleRecord(
                    id=article_id,
                    title="Latest subscribed update without completed analysis",
                    content_type="rss_article",
                    source_id=source_id,
                    source_url="https://example.invalid/wp7-deadline",
                    publish_date=now.isoformat(),
                    fetched_date=now.isoformat(),
                    has_content=True,
                    content="This update remains unanalysed until the first-open deadline.",
                )
            )
            session.commit()
            started = start_personal_digest_edition(
                session,
                username,
                now=now,
                generation_reason=DigestGenerationReason.FIRST_OPEN,
                first_open_at=now,
            )
            if started.edition is None:
                raise RuntimeError("deadline edition was not created")
            edition_id = started.edition.id
            before_status = started.edition.status
            # 期限 = max(首开+15min, 当日 08:30+15min):凌晨跑时「now+16min」够不到
            # 08:45,边缘永远 pending——按 edition 自己记的 deadline_at 之后 1 分钟推进,
            # 任何时段跑都是「越过期限」这一个语义。
            deadline_at = dt.datetime.fromisoformat(started.edition.deadline_at)
            completed = process_pending_edition(
                session,
                started.edition,
                now=max(now, deadline_at) + dt.timedelta(minutes=1),
            )
            items = session.exec(
                select(PersonalDigestItemRecord)
                .where(PersonalDigestItemRecord.edition_id == completed.id)
                .order_by(PersonalDigestItemRecord.position)
            ).all()
        snapshots = [json.loads(row.snapshot_json) for row in items]
        return {
            "before_status": before_status,
            "after_status": completed.status,
            "same_edition_id": edition_id == completed.id,
            "degraded_reason": completed.degraded_reason,
            "expected_source_ids": json.loads(completed.expected_source_ids_json),
            "item_count": len(items),
            "outside_scope_items": sum(
                snapshot.get("source_id") != source_id for snapshot in snapshots
            ),
            "deadline_minutes": 15,
        }
    finally:
        storage.engine.dispose()


async def run_release_smoke(args: argparse.Namespace) -> dict[str, Any]:
    _assert_isolated_database(args.database_url)
    live_report, article = await _live_analysis_check(
        args.database_url,
        source_id=args.rss_source,
        live_rss=not args.skip_live_rss,
        live_llm=args.live_llm,
    )
    restart_report = await _restart_recovery_check(args.database_url, article)
    sqlite_report = _sqlite_concurrency_check(args.database_url, args.writers)
    deadline_report = _deadline_degrade_check(args.database_url)

    violations: list[str] = []
    if live_report["worker_statuses"] != ["succeeded"]:
        violations.append("live_analysis_not_succeeded")
    if not 1 <= float(live_report["quality_score"]) <= 10:
        violations.append("live_analysis_score_out_of_range")
    if live_report["summary_chars"] < 1 or live_report["reason_chars"] < 1:
        violations.append("live_analysis_missing_text")
    if restart_report["attempt_statuses"] != ["timeout", "succeeded"]:
        violations.append("restart_attempt_history_invalid")
    if sqlite_report["errors"] or sqlite_report["locked_errors"]:
        violations.append("sqlite_concurrent_write_failed")
    if sqlite_report["persisted_rows"] != sqlite_report["writers"] + 1:
        violations.append("sqlite_concurrent_write_count_mismatch")
    if sqlite_report["journal_mode"].casefold() != "wal":
        violations.append("sqlite_wal_not_enabled")
    if sqlite_report["busy_timeout_ms"] < 5000:
        violations.append("sqlite_busy_timeout_too_low")
    if (
        deadline_report["before_status"] != "pending"
        or deadline_report["after_status"] != "degraded"
        or not deadline_report["same_edition_id"]
        or deadline_report["outside_scope_items"]
    ):
        violations.append("first_open_deadline_contract_failed")

    return {
        "database_url": args.database_url,
        "live_analysis": live_report,
        "restart_recovery": restart_report,
        "sqlite_concurrency": sqlite_report,
        "first_open_deadline": deadline_report,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(run_release_smoke(args))
    except Exception as exc:  # noqa: BLE001 - CLI must return a concise release failure
        report = {
            "database_url": args.database_url,
            "violations": [f"{type(exc).__name__}: {exc}"],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
