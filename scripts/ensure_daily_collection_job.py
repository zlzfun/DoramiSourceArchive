#!/usr/bin/env python3
"""Create or update the default daily full collection job.

Run from the repository root:

    PYTHONPATH=src uv run python scripts/ensure_daily_collection_job.py

The job targets all built-in concrete source nodes by default. Generic advanced
fetchers are excluded unless --include-advanced is set, because they require
per-source parameters and would fail as unattended daily jobs without them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlmodel import Session, select  # noqa: E402

from config import settings  # noqa: E402
from fetchers.registry import fetcher_registry  # noqa: E402
from models.db import CollectionJobRecord  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


DEFAULT_JOB_NAME = "每日全量采集"
DEFAULT_CRON_EXPR = "10 7 * * *"
# incubating = 新节点观察期(curation_policy「Incubation」节):质量验收转正前
# 不进每日自动采集,只手动触发、集中观察抓取结果。
DEFAULT_EXCLUDED_CATEGORIES = frozenset({"advanced", "workflow", "incubating"})


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def validate_cron_expr(value: str) -> str:
    cron_expr = value.strip()
    if len(cron_expr.split()) != 5:
        raise argparse.ArgumentTypeError("cron 表达式必须是 5 段，例如：10 7 * * *")
    return cron_expr


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="创建或更新默认每日全量采集任务。",
    )
    parser.add_argument("--name", default=DEFAULT_JOB_NAME, help=f"任务名称，默认：{DEFAULT_JOB_NAME}")
    parser.add_argument("--cron", default=DEFAULT_CRON_EXPR, type=validate_cron_expr, help=f"5 段 cron，默认：{DEFAULT_CRON_EXPR}")
    parser.add_argument(
        "--description",
        default="每天采集当前注册表中的内置具体数据源节点，用于归档、日报和订阅分发。",
        help="任务说明。",
    )
    parser.add_argument(
        "--database-url",
        default=settings.storage.database_url,
        help="数据库 URL；默认读取当前 Dorami 配置。",
    )
    parser.add_argument(
        "--include-advanced",
        action="store_true",
        help="包含 advanced/workflow 等通用能力节点。默认排除，因为它们通常需要额外参数。",
    )
    parser.add_argument(
        "--exclude-category",
        action="append",
        default=[],
        help="额外排除的节点 category，可重复传入。",
    )
    parser.add_argument(
        "--exclude-fetcher",
        action="append",
        default=[],
        help="额外排除的 fetcher_id，可重复传入。",
    )
    parser.add_argument(
        "--inactive",
        action="store_true",
        help="创建/更新为停用状态。默认启用。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出将要写入的任务内容，不修改数据库。",
    )
    return parser.parse_args()


def selected_fetcher_ids(
    *,
    include_advanced: bool = False,
    exclude_categories: set[str] | None = None,
    exclude_fetchers: set[str] | None = None,
) -> list[str]:
    excluded_categories = set(exclude_categories or set())
    if not include_advanced:
        excluded_categories.update(DEFAULT_EXCLUDED_CATEGORIES)
    excluded_fetchers = set(exclude_fetchers or set())

    fetcher_ids: list[str] = []
    for item in fetcher_registry.get_all_metadata():
        fetcher_id = str(item["id"]).strip()
        category = str(item.get("category") or "").strip()
        if not fetcher_id:
            continue
        if category in excluded_categories:
            continue
        if fetcher_id in excluded_fetchers:
            continue
        fetcher_ids.append(fetcher_id)
    return fetcher_ids


def ensure_collection_job(
    *,
    engine,
    name: str,
    description: str,
    cron_expr: str,
    fetcher_ids: list[str],
    is_active: bool,
) -> dict[str, Any]:
    now = datetime.now().isoformat()
    downstream_policy = {
        "purpose": "daily_full_archive",
        "delivery_targets": ["archive", "daily_brief", "reader_feed", "rag"],
        "source_scope": "all_builtin_concrete_fetchers",
    }

    with Session(engine) as session:
        record = session.exec(
            select(CollectionJobRecord).where(CollectionJobRecord.name == name)
        ).first()
        action = "updated" if record else "created"
        if record is None:
            record = CollectionJobRecord(
                name=name,
                created_at=now,
                updated_at=now,
            )

        record.description = description
        record.fetcher_ids_json = json_dumps(fetcher_ids)
        record.params_json = "{}"
        record.per_fetcher_params_json = "{}"
        record.cron_expr = cron_expr
        record.is_active = is_active
        record.downstream_policy_json = json_dumps(downstream_policy)
        record.updated_at = now

        session.add(record)
        session.commit()
        session.refresh(record)

        return {
            "action": action,
            "job_id": record.id,
            "name": record.name,
            "cron_expr": record.cron_expr,
            "is_active": record.is_active,
            "fetcher_count": len(fetcher_ids),
            "fetcher_ids": fetcher_ids,
        }


def main() -> int:
    args = parse_args()
    name = args.name.strip()
    if not name:
        raise SystemExit("任务名称不能为空")

    fetcher_ids = selected_fetcher_ids(
        include_advanced=args.include_advanced,
        exclude_categories={item.strip() for item in args.exclude_category if item.strip()},
        exclude_fetchers={item.strip() for item in args.exclude_fetcher if item.strip()},
    )
    if not fetcher_ids:
        raise SystemExit("没有可加入任务的采集节点")

    if args.dry_run:
        result = {
            "action": "dry_run",
            "name": name,
            "cron_expr": args.cron,
            "is_active": not args.inactive,
            "fetcher_count": len(fetcher_ids),
            "fetcher_ids": fetcher_ids,
            "database_url": args.database_url,
        }
    else:
        db = DatabaseStorage(args.database_url)
        result = ensure_collection_job(
            engine=db.engine,
            name=name,
            description=args.description.strip(),
            cron_expr=args.cron,
            fetcher_ids=fetcher_ids,
            is_active=not args.inactive,
        )
        result["database_url"] = args.database_url

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
