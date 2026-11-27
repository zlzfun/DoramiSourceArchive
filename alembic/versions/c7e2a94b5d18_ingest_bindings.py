"""ingest bindings: which ingest token may push into which source (issue #344)

新表 ``ingest_bindings``:源 × 收录令牌,唯一约束 ``(source_id, token_id)``;``last_pushed_at`` 记这个令牌
最近一次有条目被这个源接受的时间。推送绑定是源的一种获取方式,与源行上的抓取绑定并列。

不回填:升级后现有令牌都没有绑定,推送会被逐条拒收,直到管理员在运维「自定源与收录」里绑定来源
(方案 docs/archive/issue-344-push-binding.md §7)。只在收录入口所在的节点用,不随 Archive Sync 导出,
与混版本窗口无关。downgrade 删表。

Revision ID: c7e2a94b5d18
Revises: c298f6100001
Create Date: 2026-11-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "c7e2a94b5d18"
down_revision: Union[str, Sequence[str], None] = "c298f6100001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "ingest_bindings" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "ingest_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_pushed_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "token_id", name="uq_ingest_bindings_source_token"),
    )
    op.create_index("ix_ingest_bindings_source_id", "ingest_bindings", ["source_id"], unique=False)
    op.create_index("ix_ingest_bindings_token_id", "ingest_bindings", ["token_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "ingest_bindings" not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index("ix_ingest_bindings_token_id", table_name="ingest_bindings")
    op.drop_index("ix_ingest_bindings_source_id", table_name="ingest_bindings")
    op.drop_table("ingest_bindings")
