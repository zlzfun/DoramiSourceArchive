"""event member backlog (issue #121, review R1 #4)

Archive Sync 接收端节点本地表 `event_member_backlog`:事件流到达时成员文章尚未到本地的成员
暂存于此,每次 v2 拉取在 articles 流之后重放。不进同步协议,无触发器。

Revision ID: 7c3d9e1f5a24
Revises: 0919431adfa8
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


revision: str = "7c3d9e1f5a24"
down_revision: Union[str, Sequence[str], None] = "0919431adfa8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 老库经 create_all 启动再被 Alembic 收养时本表已存在(与 ed530f8224f9 同姿态)。
    if "event_member_backlog" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "event_member_backlog",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "payload_json",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "article_id"),
    )


def downgrade() -> None:
    if "event_member_backlog" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("event_member_backlog")
