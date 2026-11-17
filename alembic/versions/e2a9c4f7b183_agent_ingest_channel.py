"""agent ingest channel: item provenance, ingest tokens, picks (issue #262 wave 1)

- ``articles`` 加 ``ingest_channel`` / ``ingest_actor``(条目来路;SQLite 原生 ADD COLUMN,
  不重建表,FTS 与归档同步触发器不受影响;存量行为空 = 抓取)与 ``source_url`` 索引(收录的
  跨源同链接去重);
- 新表 ``ingest_tokens``(Agent 收录令牌,只存 HMAC hash)与 ``article_picks``(收录记录)。

两张新表与两列都不随 Archive Sync 导出。downgrade 删表删列。

Revision ID: e2a9c4f7b183
Revises: d7f3b9e2a614
Create Date: 2026-11-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "e2a9c4f7b183"
down_revision: Union[str, Sequence[str], None] = "d7f3b9e2a614"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _article_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("articles")}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _article_columns(bind)
    for name in ("ingest_channel", "ingest_actor"):
        if name not in existing:
            op.add_column(
                "articles",
                sa.Column(
                    name,
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=False,
                    server_default=sa.text("''"),
                ),
            )
    article_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("articles")}
    if "ix_articles_source_url" not in article_indexes:
        # 跨源同链接去重的精确查;一次性建索引,大库上是秒级。
        op.create_index("ix_articles_source_url", "articles", ["source_url"], unique=False)
    tables = set(sa.inspect(bind).get_table_names())
    if "ingest_tokens" not in tables:
        op.create_table(
            "ingest_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("actor", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("token_preview", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("daily_limit", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("last_used_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("revoked_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ingest_tokens_actor", "ingest_tokens", ["actor"], unique=False)
        op.create_index("ix_ingest_tokens_token_hash", "ingest_tokens", ["token_hash"], unique=True)
        op.create_index("ix_ingest_tokens_is_active", "ingest_tokens", ["is_active"], unique=False)
    if "article_picks" not in tables:
        op.create_table(
            "article_picks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("article_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("actor", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("channel", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("picked_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("article_id", "actor", name="uq_article_picks_article_actor"),
        )
        op.create_index("ix_article_picks_article_id", "article_picks", ["article_id"], unique=False)
        op.create_index("ix_article_picks_actor", "article_picks", ["actor"], unique=False)
        op.create_index("ix_article_picks_picked_at", "article_picks", ["picked_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "article_picks" in tables:
        for name in ("ix_article_picks_picked_at", "ix_article_picks_actor", "ix_article_picks_article_id"):
            op.drop_index(name, table_name="article_picks")
        op.drop_table("article_picks")
    if "ingest_tokens" in tables:
        for name in ("ix_ingest_tokens_is_active", "ix_ingest_tokens_token_hash", "ix_ingest_tokens_actor"):
            op.drop_index(name, table_name="ingest_tokens")
        op.drop_table("ingest_tokens")
    article_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("articles")}
    if "ix_articles_source_url" in article_indexes:
        op.drop_index("ix_articles_source_url", table_name="articles")
    existing = _article_columns(bind)
    for name in ("ingest_actor", "ingest_channel"):
        if name in existing:
            if bind.dialect.name == "sqlite":
                op.execute(f"ALTER TABLE articles DROP COLUMN {name}")
            else:
                op.drop_column("articles", name)
