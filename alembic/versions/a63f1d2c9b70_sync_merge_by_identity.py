"""archive sync: merge by identity, forward what a node holds (issue #263)

节点不分角色:拆整机 consumer 模式与按源围栏,导出改按本机时钟分页以转发同步写入的行。

结构:
- ``archive_sync_entity_states`` 加 ``local_revision``(导出修订)与索引
  ``(stream, local_revision, identity)``。``revision`` / ``authority_id`` 语义不变:同步写入的
  行里 ``revision`` 仍是上游修订(接收去重)。

数据(全部确定性、幂等):
- 本机行 ``local_revision = revision``;存在同步写入的行时,它们统一取 ``clock + 1`` 并把时钟
  +1。没有这类行的节点(不拉任何人)时钟不动,导出的 snapshot 不变。
- ``source_configs.collection_authority_id`` 不动:它从围栏降级为簿记(「这个源上游也有」),
  个人早报的 readiness 仍靠它判断该不该等上游的源状态。
- 同步写入的文章清空 ``fetch_run_id`` / ``job_id`` / ``job_run_id`` / ``source_group_id``:
  它们是产生节点的本地整数关联,留着会和本机恢复采集后的运行串号。
- 接收节点(存在 ``remote_sync:v2_consumer_mode``,或迁移前有带归属印记的源)按旧围栏实际挡住的
  范围保持现状:全部公共源 ``ai_analysis_enabled = 0``;含公共源的采集任务置停用(纯私有任务
  不动);``daily_brief_enabled`` 置 false;删 consumer KV。动作摘要记在
  ``archive_sync:issue_263_migration``,供运维核对。

触发器在迁移内先整体 DROP、结束后按当前定义重建(新触发器依赖 ``local_revision`` 列)。

downgrade:只回退结构(删索引与列),不恢复印记、开关与任务状态。按仓内规则,跨迁移回滚靠升级前
备份(docs/ops/release-process.md),不是 downgrade。

Revision ID: a63f1d2c9b70
Revises: f3b8d1c6a925
Create Date: 2026-11-10
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a63f1d2c9b70"
down_revision: Union[str, Sequence[str], None] = "f3b8d1c6a925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX = "ix_archive_sync_entity_states_stream_local_revision_identity"
_CONSUMER_KEY = "remote_sync:v2_consumer_mode"
_SUMMARY_KEY = "archive_sync:issue_263_migration"
_USER_PREFIX = "user_rss_"


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _drop_triggers(bind) -> None:
    if bind.dialect.name != "sqlite":
        return
    from storage.archive_sync_revision import drop_archive_sync_revision_triggers

    drop_archive_sync_revision_triggers(bind)


def _install_triggers(bind) -> None:
    if bind.dialect.name != "sqlite":
        return
    from storage.archive_sync_revision import install_archive_sync_revision_triggers

    install_archive_sync_revision_triggers(
        bind,
        include_podcast_integrity=True,
        include_podcast_audio=True,
    )


def _backfill_local_revision(bind) -> None:
    bind.execute(sa.text(
        "UPDATE archive_sync_entity_states SET local_revision = revision "
        "WHERE authority_id = ''"
    ))
    has_remote = bind.execute(sa.text(
        "SELECT 1 FROM archive_sync_entity_states WHERE authority_id <> '' LIMIT 1"
    )).first()
    if has_remote is None:
        return
    bind.execute(sa.text(
        "INSERT OR IGNORE INTO archive_sync_clock (id, revision) VALUES (1, 0)"
    ))
    clock = int(bind.execute(sa.text(
        "SELECT revision FROM archive_sync_clock WHERE id = 1"
    )).scalar() or 0)
    bind.execute(
        sa.text(
            "UPDATE archive_sync_entity_states SET local_revision = :value "
            "WHERE authority_id <> ''"
        ),
        {"value": clock + 1},
    )
    bind.execute(
        sa.text("UPDATE archive_sync_clock SET revision = :value WHERE id = 1"),
        {"value": clock + 1},
    )


def _quiesce_receiver(bind, tables: set[str]) -> dict:
    """接收节点保持现状:旧围栏挡着的公共源分析、公共采集任务、公共日报继续不跑。"""

    summary: dict = {"sources_analysis_off": 0, "jobs_disabled": [], "daily_brief_disabled": False}
    result = bind.execute(sa.text(
        "UPDATE source_configs SET ai_analysis_enabled = 0 "
        "WHERE owner_username = '' AND ai_analysis_enabled <> 0 "
        "AND source_id NOT LIKE 'user\\_rss\\_%' ESCAPE '\\'"
    ))
    summary["sources_analysis_off"] = max(int(result.rowcount or 0), 0)
    if "collection_jobs" in tables:
        private_ids = {
            str(row[0])
            for row in bind.execute(sa.text(
                "SELECT source_id FROM source_configs WHERE owner_username <> ''"
            )).all()
        }
        for job_id, name, raw in bind.execute(sa.text(
            "SELECT id, name, fetcher_ids_json FROM collection_jobs WHERE is_active = 1"
        )).all():
            try:
                node_ids = json.loads(raw or "[]")
            except (TypeError, ValueError):
                node_ids = []
            if not isinstance(node_ids, list):
                node_ids = []
            has_public = any(
                str(node_id).strip()
                and not str(node_id).startswith(_USER_PREFIX)
                and str(node_id) not in private_ids
                for node_id in node_ids
            )
            if not has_public:
                continue
            bind.execute(
                sa.text("UPDATE collection_jobs SET is_active = 0 WHERE id = :id"),
                {"id": job_id},
            )
            summary["jobs_disabled"].append({"id": int(job_id), "name": str(name or "")})
    result = bind.execute(sa.text(
        "UPDATE app_settings SET value = 'false' "
        "WHERE key = 'daily_brief_enabled' AND lower(value) = 'true'"
    ))
    summary["daily_brief_disabled"] = bool(result.rowcount)
    bind.execute(sa.text("DELETE FROM app_settings WHERE key = :key"), {"key": _CONSUMER_KEY})
    return summary


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "archive_sync_entity_states" not in tables:
        return
    _drop_triggers(bind)
    try:
        if "local_revision" not in _columns(bind, "archive_sync_entity_states"):
            op.add_column(
                "archive_sync_entity_states",
                sa.Column(
                    "local_revision",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )
        existing_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("archive_sync_entity_states")
        }
        if _INDEX not in existing_indexes:
            op.create_index(
                _INDEX,
                "archive_sync_entity_states",
                ["stream", "local_revision", "identity"],
            )
        _backfill_local_revision(bind)

        is_receiver = False
        if "app_settings" in tables:
            is_receiver = bind.execute(
                sa.text("SELECT 1 FROM app_settings WHERE key = :key"),
                {"key": _CONSUMER_KEY},
            ).first() is not None
        if "source_configs" in tables and "collection_authority_id" in _columns(bind, "source_configs"):
            if bind.execute(sa.text(
                "SELECT 1 FROM source_configs WHERE collection_authority_id <> '' LIMIT 1"
            )).first() is not None:
                is_receiver = True
        if "articles" in tables and "analysis_authority_id" in _columns(bind, "articles"):
            bind.execute(sa.text(
                "UPDATE articles SET fetch_run_id = NULL, job_id = NULL, "
                "job_run_id = NULL, source_group_id = NULL "
                "WHERE analysis_authority_id <> '' AND ("
                "fetch_run_id IS NOT NULL OR job_id IS NOT NULL "
                "OR job_run_id IS NOT NULL OR source_group_id IS NOT NULL)"
            ))
        if is_receiver and {"app_settings", "source_configs"} <= tables:
            summary = _quiesce_receiver(bind, tables)
            summary["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            bind.execute(
                sa.text("INSERT OR REPLACE INTO app_settings (key, value) VALUES (:key, :value)"),
                {"key": _SUMMARY_KEY, "value": json.dumps(summary, ensure_ascii=False)},
            )
    finally:
        _install_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "archive_sync_entity_states" not in tables:
        return
    _drop_triggers(bind)
    try:
        existing_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("archive_sync_entity_states")
        }
        if _INDEX in existing_indexes:
            op.drop_index(_INDEX, table_name="archive_sync_entity_states")
        if "local_revision" in _columns(bind, "archive_sync_entity_states"):
            with op.batch_alter_table("archive_sync_entity_states") as batch_op:
                batch_op.drop_column("local_revision")
    finally:
        _install_triggers(bind)
