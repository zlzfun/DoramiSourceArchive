"""reconcile missing declared indexes

Revision ID: ccae184ca0a1
Revises: 5ee31a7c5393
Create Date: 2026-07-01 09:24:25.143567

对账迁移：修复旧库缺失的、模型里以 index=True 声明的单列索引。

背景：基线之前，schema 增量靠 DatabaseStorage._ensure_compatible_schema 的裸
`ALTER TABLE ADD COLUMN` 完成——它只加列、**不建索引**，故这些列虽在模型上声明了
index=True，旧库里却没有对应索引。全新库经基线全量建表已含这些索引；旧库被 stamp 到
基线（跳过建表），从而物理缺索引。本迁移逐个存在性判断后补齐，对已含索引的新库天然幂等。

清单为发现时（阶段2）的固定快照，不引用会演进的模型 metadata，保证任何时点回放行为一致。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'ccae184ca0a1'
down_revision: Union[str, Sequence[str], None] = '5ee31a7c5393'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表, 列) —— 索引名遵循 SQLModel 约定 ix_<表>_<列>。旧库经裸 ALTER 加列后缺失者。
_MISSING_INDEXES = [
    ("articles", "fetch_run_id"),
    ("articles", "job_id"),
    ("articles", "job_run_id"),
    ("articles", "run_scope"),
    ("articles", "source_group_id"),
    ("fetch_runs", "job_id"),
    ("fetch_runs", "job_run_id"),
    ("fetch_runs", "run_scope"),
    ("fetch_runs", "source_group_id"),
    ("reader_subscriptions", "owner_username"),
    ("source_configs", "fetch_reliability"),
    ("source_configs", "noise_risk"),
    ("source_configs", "provenance_tier"),
    ("source_configs", "signal_strength"),
    ("source_configs", "source_brand"),
    ("source_configs", "source_channel"),
    ("source_configs", "source_owner"),
    ("source_configs", "source_scope"),
    ("users", "ai_beta_enabled"),
]


def upgrade() -> None:
    """补齐旧库缺失的声明索引；对已含者跳过（幂等）。"""
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())
    for table, column in _MISSING_INDEXES:
        if table not in tables:
            continue
        # 列守卫:断代早于基线的老库可能连列都没有(生产实例:users 缺
        # ai_beta_enabled)。收养对齐(_align_legacy_to_baseline)后不会触发,
        # 此处防御保证任何断面回放都不崩——列都不在,索引自然无从谈起。
        if column not in {c["name"] for c in insp.get_columns(table)}:
            continue
        existing = {ix["name"] for ix in insp.get_indexes(table)}
        index_name = f"ix_{table}_{column}"
        if index_name not in existing:
            op.create_index(index_name, table, [column])


def downgrade() -> None:
    """有意为 no-op：这些索引本属基线定义，回退本「修复」不应把它们从新库删掉。"""
    pass
