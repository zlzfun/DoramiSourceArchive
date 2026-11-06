"""article fingerprints for same-source content dedupe on push (issue #262)

新表 ``article_fingerprints``:条目 id 为主键,存规范化正文 sha1、64 位 SimHash、字数与计算时的
条目版本(``archive_updated_at``)。不回填:推送到某个已有源时对该源现场补齐。只在本机用,
不随 Archive Sync 导出,与混版本窗口无关。downgrade 删表。

Revision ID: f3b8d1c6a925
Revises: e2a9c4f7b183
Create Date: 2026-11-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "f3b8d1c6a925"
down_revision: Union[str, Sequence[str], None] = "e2a9c4f7b183"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "article_fingerprints" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "article_fingerprints",
        sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("simhash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("length", sa.Integer(), nullable=False),
        sa.Column("revision", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_index("ix_article_fingerprints_source_id", "article_fingerprints", ["source_id"], unique=False)
    op.create_index(
        "ix_article_fingerprints_source_hash",
        "article_fingerprints",
        ["source_id", "content_hash"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "article_fingerprints" not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index("ix_article_fingerprints_source_hash", table_name="article_fingerprints")
    op.drop_index("ix_article_fingerprints_source_id", table_name="article_fingerprints")
    op.drop_table("article_fingerprints")
