"""personal digest: allow the read / listen selection lanes

Revision ID: e5c1f7a2b938
Revises: d2b7c4e19f30
Create Date: 2026-10-12

issue #178 第三波:个人早报新增按内容价值入选的「深度阅读」「播客精选」两个分区,条目 selection_lane
多两个枚举值 ``read`` / ``listen``。SQLite 的 CHECK 约束只能随表重建改写,故走 batch 模式;
``personal_digest_items`` 不参与归档同步、没有同步触发器,重建安全。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "e5c1f7a2b938"
down_revision: Union[str, Sequence[str], None] = "d2b7c4e19f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_personal_digest_items_selection_lane"


def upgrade() -> None:
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("personal_digest_items", recreate=recreate) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT,
            "selection_lane IN ('interest','quality','breaking','read','listen')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    # 收窄枚举前先物理删除两个分区的条目:它们额外于新闻的 target 之上,删除不影响新闻那份精选;
    # position 留下的空洞只影响排序不破坏唯一键。
    bind.exec_driver_sql(
        "DELETE FROM personal_digest_items WHERE selection_lane IN ('read', 'listen')"
    )
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("personal_digest_items", recreate=recreate) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT,
            "selection_lane IN ('interest','quality','breaking')",
        )
