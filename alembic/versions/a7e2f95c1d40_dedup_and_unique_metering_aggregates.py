"""dedup metering aggregates and add unique constraints

计量聚合表唯一约束(v3.43 审计 M21):ai_usage 与 reader_reads 的写路径此前是
「先查→无则插/有则 Python 递增→提交」,并发请求可各判「无记录」插出重复行、
或同时基于旧值写回丢增量。本迁移分两步:

1. **合并存量重复行**——同聚合键(ai_usage: day×username×purpose×model /
   reader_reads: day×username×source_id)的多行把计数求和写进最小 id 行,
   其余行删除(零信息损失,看板读数只会更准)。
2. **建唯一索引**——约束落库后写侧改 SQLite ON CONFLICT DO UPDATE 原子累加
   (ai_usage.record_usage / reader_activity.record_read),竞态从根上消灭。

Revision ID: a7e2f95c1d40
Revises: b4a1c7e5d2f8
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7e2f95c1d40'
down_revision: Union[str, Sequence[str], None] = 'b4a1c7e5d2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _merge_duplicates(bind, table: str, key_cols: list[str], sum_cols: list[str]) -> None:
    """把同聚合键的重复行求和进最小 id 行并删除其余行(幂等,无重复时零操作)。"""
    keys = ", ".join(key_cols)
    set_clause = ", ".join(
        f"{col} = (SELECT agg.s_{col} FROM ("
        f"  SELECT MIN(id) AS keep_id, SUM({col}) AS s_{col}"
        f"  FROM {table} GROUP BY {keys} HAVING COUNT(*) > 1"
        f") AS agg WHERE agg.keep_id = {table}.id)"
        for col in sum_cols
    )
    # 只有重复组的保留行会被子查询命中;WHERE 限定避免把无关行的列写成 NULL。
    bind.execute(sa.text(
        f"UPDATE {table} SET {set_clause} "
        f"WHERE id IN (SELECT MIN(id) FROM {table} GROUP BY {keys} HAVING COUNT(*) > 1)"
    ))
    bind.execute(sa.text(
        f"DELETE FROM {table} WHERE id NOT IN (SELECT MIN(id) FROM {table} GROUP BY {keys})"
    ))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    _merge_duplicates(
        bind, "ai_usage",
        ["day", "username", "purpose", "model"],
        ["calls", "prompt_tokens", "completion_tokens", "total_tokens"],
    )
    _merge_duplicates(
        bind, "reader_reads",
        ["day", "username", "source_id"],
        ["reads"],
    )

    # 收养回放守卫:create_all() 出生的新库(当前 metadata)已含唯一索引,幂等跳过。
    ai_indexes = {ix["name"] for ix in insp.get_indexes("ai_usage")}
    if "uq_ai_usage_day_user_purpose_model" not in ai_indexes:
        op.create_index(
            "uq_ai_usage_day_user_purpose_model",
            "ai_usage",
            ["day", "username", "purpose", "model"],
            unique=True,
        )
    read_indexes = {ix["name"] for ix in insp.get_indexes("reader_reads")}
    if "uq_reader_reads_day_user_source" not in read_indexes:
        op.create_index(
            "uq_reader_reads_day_user_source",
            "reader_reads",
            ["day", "username", "source_id"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_reader_reads_day_user_source", table_name="reader_reads")
    op.drop_index("uq_ai_usage_day_user_purpose_model", table_name="ai_usage")
