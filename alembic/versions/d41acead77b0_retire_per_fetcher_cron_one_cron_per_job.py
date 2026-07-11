"""单节点 cron 覆盖退役:一任务一 cron(想要不同节奏 = 建新任务)。

防御性拆分(faithful):带非空 per_fetcher_cron 的存量任务,按 distinct cron 把
覆盖节点拆成新任务(命名「{原名} · 独立时刻N」,继承说明/参数/启停/下游策略,
per_fetcher_params 只带走对应节点),并从原任务 fetcher_ids 中移除这些节点——
调度行为不变,只是显式化为多个任务。随后 DROP per_fetcher_cron_json 列
(SQLite 无原生 ALTER,batch 模式重建表)。

Revision ID: d41acead77b0
Revises: 8f6d93196258
Create Date: 2026-07-11
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd41acead77b0'
down_revision: Union[str, Sequence[str], None] = '8f6d93196258'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _loads(raw, fallback):
    try:
        value = json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def upgrade() -> None:
    bind = op.get_bind()
    # 基线采用路径的库由「当前 metadata」create_all 建表,本就没有该列——无需拆分与 drop。
    columns = {c["name"] for c in sa.inspect(bind).get_columns("collection_jobs")}
    if "per_fetcher_cron_json" not in columns:
        return
    rows = bind.execute(sa.text(
        "SELECT id, name, description, fetcher_ids_json, params_json, per_fetcher_params_json, "
        "cron_expr, per_fetcher_cron_json, is_active, downstream_policy_json, created_at, updated_at "
        "FROM collection_jobs"
    )).mappings().all()

    for row in rows:
        overrides = {
            fid: str(cron).strip()
            for fid, cron in _loads(row["per_fetcher_cron_json"], {}).items()
            if str(cron or "").strip()
        }
        if not overrides:
            continue
        fetcher_ids = _loads(row["fetcher_ids_json"], [])
        per_params = _loads(row["per_fetcher_params_json"], {})

        # 按 distinct cron 分组 → 各成一个新任务(faithful 保留原调度行为)
        by_cron: dict = {}
        for fid, cron in overrides.items():
            if fid in fetcher_ids:
                by_cron.setdefault(cron, []).append(fid)

        moved: set = set()
        for idx, (cron, fids) in enumerate(sorted(by_cron.items()), start=1):
            moved.update(fids)
            suffix = f" · 独立时刻{idx if len(by_cron) > 1 else ''}".rstrip()
            bind.execute(
                sa.text(
                    "INSERT INTO collection_jobs (name, description, fetcher_ids_json, params_json, "
                    "per_fetcher_params_json, cron_expr, per_fetcher_cron_json, is_active, "
                    "downstream_policy_json, legacy_task_id, created_at, updated_at) "
                    "VALUES (:name, :description, :fetcher_ids, :params, :per_params, :cron, '{}', "
                    ":is_active, :policy, NULL, :created_at, :updated_at)"
                ),
                {
                    "name": f"{row['name']}{suffix}",
                    "description": row["description"],
                    "fetcher_ids": json.dumps(sorted(fids), ensure_ascii=False),
                    "params": row["params_json"],
                    "per_params": json.dumps(
                        {fid: per_params[fid] for fid in fids if fid in per_params}, ensure_ascii=False
                    ),
                    "cron": cron,
                    "is_active": row["is_active"],
                    "policy": row["downstream_policy_json"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
            )

        remaining = [fid for fid in fetcher_ids if fid not in moved]
        bind.execute(
            sa.text("UPDATE collection_jobs SET fetcher_ids_json = :fids, per_fetcher_params_json = :pp "
                    "WHERE id = :id"),
            {
                "id": row["id"],
                "fids": json.dumps(remaining, ensure_ascii=False),
                "pp": json.dumps(
                    {fid: v for fid, v in per_params.items() if fid in remaining}, ensure_ascii=False
                ),
            },
        )

    with op.batch_alter_table("collection_jobs") as batch_op:
        batch_op.drop_column("per_fetcher_cron_json")


def downgrade() -> None:
    # 拆分不可逆(新任务与原任务已是平级实体),仅恢复列。
    with op.batch_alter_table("collection_jobs") as batch_op:
        batch_op.add_column(sa.Column("per_fetcher_cron_json", sa.VARCHAR(), nullable=False, server_default="{}"))
