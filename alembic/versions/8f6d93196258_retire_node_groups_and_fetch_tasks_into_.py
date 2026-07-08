"""retire node_groups and fetch_tasks into collection jobs

实体简化阶段 2（docs/analysis/entity-simplification-plan.md）：退役「采集范围
（node_groups）」与「旧版单节点定时任务（fetch_tasks）」两个实体，DROP 前先把
存量数据合并进采集任务（collection_jobs），保证行为不丢：

1. 引用了采集范围的采集任务 → 内联（忠实复刻旧 build_collection_job_items 的
   合并优先级：group.params < job.params < group.per_fetcher_params <
   job.per_fetcher_params；job 自身 fetcher_ids 非空时无视 group 节点列表；
   group 停用则任务同步停用以保持「不产出」行为）。
2. 未被引用、或自带 cron 调度的采集范围 → 转换为独立采集任务（保留
   cron_expr / per_fetcher_cron / is_active，调度行为不变）。
3. fetch_tasks → 单节点采集任务（复刻旧 migrate-legacy-tasks 端点，跳过已按
   legacy_task_id 迁移过的；is_active 沿用原任务——旧调度路径已随本阶段移除，
   置 False 会静默停掉仍在跑的定时）。

downgrade 仅恢复表结构，数据不可逆（内联/转换不回拆）。

Revision ID: 8f6d93196258
Revises: 8bba6f81b240
Create Date: 2026-07-08 23:13:02.602394

"""
import datetime
import json
from typing import Any, Dict, List, Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '8f6d93196258'
down_revision: Union[str, Sequence[str], None] = '8bba6f81b240'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _loads(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _rows(conn, table: str) -> List[Dict[str, Any]]:
    """SELECT *，以 dict 返回（旧库可能缺 additive 列，用 .get 兜底读取）。"""
    return [dict(row._mapping) for row in conn.execute(sa.text(f"SELECT * FROM {table}"))]


def _merge_group_data_into_jobs(conn, now: str) -> None:
    groups = {row["id"]: row for row in _rows(conn, "node_groups")}
    jobs = _rows(conn, "collection_jobs")
    referenced_group_ids = set()

    for job in jobs:
        group_id = job.get("group_id")
        if group_id is None:
            continue
        group = groups.get(group_id)
        if group is None:
            continue  # 悬空引用：schema 阶段 drop_column 即等效清空
        referenced_group_ids.add(group_id)

        job_fetcher_ids = _loads(job.get("fetcher_ids_json"), [])
        fetcher_ids = job_fetcher_ids or _loads(group.get("fetcher_ids_json"), [])
        params = {**_loads(group.get("params_json"), {}), **_loads(job.get("params_json"), {})}
        group_pf = _loads(group.get("per_fetcher_params_json"), {})
        job_pf = _loads(job.get("per_fetcher_params_json"), {})
        per_fetcher_params = {
            fid: {**(group_pf.get(fid) or {}), **(job_pf.get(fid) or {})}
            for fid in set(group_pf) | set(job_pf)
        }
        is_active = bool(job.get("is_active")) and bool(group.get("is_active"))

        conn.execute(
            sa.text(
                "UPDATE collection_jobs SET fetcher_ids_json=:fids, params_json=:params, "
                "per_fetcher_params_json=:pf, is_active=:active, updated_at=:now WHERE id=:id"
            ),
            {
                "fids": json.dumps(fetcher_ids, ensure_ascii=False),
                "params": json.dumps(params, ensure_ascii=False),
                "pf": json.dumps(per_fetcher_params, ensure_ascii=False),
                "active": is_active,
                "now": now,
                "id": job["id"],
            },
        )

    # 未被引用、或自带 cron 调度的采集范围 → 独立采集任务（保调度/保配置）。
    for group_id, group in groups.items():
        has_own_schedule = bool((group.get("cron_expr") or "").strip()) or any(
            (expr or "").strip() for expr in _loads(group.get("per_fetcher_cron_json"), {}).values()
        )
        if group_id in referenced_group_ids and not has_own_schedule:
            continue
        description = (group.get("description") or "").strip()
        description = f"{description}（由采集范围迁移生成）" if description else "由采集范围迁移生成。"
        conn.execute(
            sa.text(
                "INSERT INTO collection_jobs (name, description, fetcher_ids_json, params_json, "
                "per_fetcher_params_json, cron_expr, per_fetcher_cron_json, is_active, "
                "downstream_policy_json, created_at, updated_at) "
                "VALUES (:name, :desc, :fids, :params, :pf, :cron, :pfc, :active, '{}', :now, :now)"
            ),
            {
                "name": group.get("name") or f"采集范围 #{group_id}",
                "desc": description,
                "fids": group.get("fetcher_ids_json") or "[]",
                "params": group.get("params_json") or "{}",
                "pf": group.get("per_fetcher_params_json") or "{}",
                "cron": (group.get("cron_expr") or "").strip(),
                "pfc": group.get("per_fetcher_cron_json") or "{}",
                "active": bool(group.get("is_active")),
                "now": now,
            },
        )


def _convert_fetch_tasks_to_jobs(conn, now: str) -> None:
    migrated_task_ids = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT legacy_task_id FROM collection_jobs WHERE legacy_task_id IS NOT NULL")
        )
    }
    for task in _rows(conn, "fetch_tasks"):
        if task["id"] in migrated_task_ids:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO collection_jobs (name, description, fetcher_ids_json, params_json, "
                "per_fetcher_params_json, cron_expr, per_fetcher_cron_json, is_active, "
                "downstream_policy_json, legacy_task_id, created_at, updated_at) "
                "VALUES (:name, :desc, :fids, :params, '{}', :cron, '{}', :active, '{}', :task_id, :now, :now)"
            ),
            {
                "name": f"{task['fetcher_id']} 定时采集",
                "desc": "由旧版单节点定时任务迁移生成。",
                "fids": json.dumps([task["fetcher_id"]], ensure_ascii=False),
                "params": task.get("params_json") or "{}",
                "cron": (task.get("cron_expr") or "").strip(),
                "active": bool(task.get("is_active")),
                "task_id": task["id"],
                "now": now,
            },
        )


def upgrade() -> None:
    """先内联/转换存量数据，再 DROP 两张退役表与 collection_jobs.group_id 列。"""
    conn = op.get_bind()
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    now = datetime.datetime.now().isoformat()

    if "node_groups" in tables and "collection_jobs" in tables:
        _merge_group_data_into_jobs(conn, now)
    if "fetch_tasks" in tables and "collection_jobs" in tables:
        _convert_fetch_tasks_to_jobs(conn, now)

    if "node_groups" in tables:
        node_group_indexes = {i["name"] for i in insp.get_indexes("node_groups")}
        with op.batch_alter_table('node_groups', schema=None) as batch_op:
            if 'ix_node_groups_is_active' in node_group_indexes:
                batch_op.drop_index(batch_op.f('ix_node_groups_is_active'))
            if 'ix_node_groups_name' in node_group_indexes:
                batch_op.drop_index(batch_op.f('ix_node_groups_name'))
        op.drop_table('node_groups')

    if "fetch_tasks" in tables:
        fetch_task_indexes = {i["name"] for i in insp.get_indexes("fetch_tasks")}
        with op.batch_alter_table('fetch_tasks', schema=None) as batch_op:
            if 'ix_fetch_tasks_fetcher_id' in fetch_task_indexes:
                batch_op.drop_index(batch_op.f('ix_fetch_tasks_fetcher_id'))
        op.drop_table('fetch_tasks')

    job_columns = {c["name"] for c in insp.get_columns("collection_jobs")}
    if "group_id" in job_columns:
        job_indexes = {i["name"] for i in insp.get_indexes("collection_jobs")}
        with op.batch_alter_table('collection_jobs', schema=None) as batch_op:
            if 'ix_collection_jobs_group_id' in job_indexes:
                batch_op.drop_index(batch_op.f('ix_collection_jobs_group_id'))
            batch_op.drop_column('group_id')


def downgrade() -> None:
    """仅恢复表结构；内联/转换的数据不回拆（单向迁移）。"""
    with op.batch_alter_table('collection_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.INTEGER(), nullable=True))
        batch_op.create_index(batch_op.f('ix_collection_jobs_group_id'), ['group_id'], unique=False)

    op.create_table('fetch_tasks',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('fetcher_id', sa.VARCHAR(), nullable=False),
    sa.Column('cron_expr', sa.VARCHAR(), nullable=False),
    sa.Column('params_json', sa.VARCHAR(), nullable=False),
    sa.Column('is_active', sa.BOOLEAN(), nullable=False),
    sa.Column('created_at', sa.VARCHAR(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fetch_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fetch_tasks_fetcher_id'), ['fetcher_id'], unique=False)

    op.create_table('node_groups',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('name', sa.VARCHAR(), nullable=False),
    sa.Column('description', sa.VARCHAR(), nullable=False),
    sa.Column('fetcher_ids_json', sa.VARCHAR(), nullable=False),
    sa.Column('params_json', sa.VARCHAR(), nullable=False),
    sa.Column('per_fetcher_params_json', sa.VARCHAR(), nullable=False),
    sa.Column('cron_expr', sa.VARCHAR(), nullable=False),
    sa.Column('per_fetcher_cron_json', sa.VARCHAR(), nullable=False),
    sa.Column('is_active', sa.BOOLEAN(), nullable=False),
    sa.Column('created_at', sa.VARCHAR(), nullable=False),
    sa.Column('updated_at', sa.VARCHAR(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('node_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_node_groups_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_node_groups_is_active'), ['is_active'], unique=False)
