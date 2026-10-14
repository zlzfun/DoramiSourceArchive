"""add content value columns to article_analyses (issue #178 wave 2)

Revision ID: d2b7c4e19f30
Revises: c6413eda8931
Create Date: 2026-10-12 20:00:00

第二波给每条内容加第二个分「内容价值」(值不值得花时间读完 / 听完),与新闻价值 quality_score
相互独立,有独立的尺子版本 content_value_version。另加 pending_scope,标记一次待处理任务是
完整分析还是只补内容价值(历史回填「补内容价值」策略用,不动已有新闻分、摘要与标签)。

upgrade:只加列(SQLite 原生 ADD COLUMN,不重建表——article_analyses 上有归档同步触发器,
重建表会把它们丢掉)。存量行全部为空,即「没有内容价值」,由回填补齐。
downgrade:SQLite 原生 DROP COLUMN(新列无索引、不被触发器引用)。
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "d2b7c4e19f30"
down_revision = "c6413eda8931"
branch_labels = None
depends_on = None

_TABLE = "article_analyses"


def _columns() -> list[sa.Column]:
    text_type = sqlmodel.sql.sqltypes.AutoString
    return [
        sa.Column("content_kind", text_type(), nullable=True),
        sa.Column("content_value", sa.Float(), nullable=True),
        sa.Column("content_value_reason", text_type(), nullable=False, server_default=sa.text("''")),
        sa.Column("content_value_version", text_type(), nullable=False, server_default=sa.text("''")),
        sa.Column("pending_scope", text_type(), nullable=False, server_default=sa.text("''")),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}
    for column in _columns():
        if column.name not in existing:
            op.add_column(_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}
    for column in reversed(_columns()):
        if column.name not in existing:
            continue
        if bind.dialect.name == "sqlite":
            op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {column.name}")
        else:
            op.drop_column(_TABLE, column.name)
