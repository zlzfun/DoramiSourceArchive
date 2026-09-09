"""personal digest: allow the breaking selection lane

Revision ID: c7e1a9d4b2f6
Revises: b34d9f1a72e1
Create Date: 2026-09-09

v3.50(issue #33 §2)个人早报新增「重大事件」通道,条目 selection_lane 多一个枚举值
``breaking``。SQLite 的 CHECK 约束只能随表重建改写,故走 batch 模式。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "c7e1a9d4b2f6"
down_revision: Union[str, Sequence[str], None] = "b34d9f1a72e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_personal_digest_items_selection_lane"


def upgrade() -> None:
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("personal_digest_items", recreate=recreate) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT,
            "selection_lane IN ('interest','quality','breaking')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    # 收窄枚举前先把 breaking 条目物理删除:它们是额外加在 target 之上的头条位,
    # 删除不影响用户自己那份精选;position 留下的空洞只影响排序不破坏唯一键。
    bind.exec_driver_sql(
        "DELETE FROM personal_digest_items WHERE selection_lane = 'breaking'"
    )
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("personal_digest_items", recreate=recreate) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT,
            "selection_lane IN ('interest','quality')",
        )
