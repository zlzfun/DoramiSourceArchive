"""add article_shares for reader article sharing

Revision ID: 9548caa15eea
Revises: 263e8e8b6c3d
Create Date: 2026-07-29 15:27:36.003942

只建 ``article_shares`` 一张表（公开分享链接，见 models.db.ArticleShareRecord）。

注：autogenerate 当时还带出了 source_configs / users / articles / fetch_runs /
reader_subscriptions 上一批 nullable→NOT NULL 的 alter——那是开发机存量库遗留的漂移
（旧手写 ``_ensure_compatible_schema()`` 的 ALTER 路径造成），与本次分享功能无关，
已剔除，留给专门的修复迁移，免得一个功能迁移顺手改了五张无关表的约束。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


# revision identifiers, used by Alembic.
revision: str = '9548caa15eea'
down_revision: Union[str, Sequence[str], None] = '263e8e8b6c3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 运行时 bootstrap 仍是 create_all()（见 CLAUDE.md），故本表可能已由 metadata 建好；
    # 与既有建表迁移同款守卫，避免「table already exists」打断 legacy DB 的 upgrade head。
    if "article_shares" in inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        'article_shares',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('article_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('owner_username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('expires_at', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('revoked_at', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('view_count', sa.Integer(), nullable=False),
        sa.Column('last_viewed_at', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('article_shares', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_article_shares_article_id'), ['article_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_article_shares_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_article_shares_owner_username'), ['owner_username'], unique=False)
        # 令牌唯一：按令牌解析分享是单行查找，重复即是歧义。
        batch_op.create_index(batch_op.f('ix_article_shares_token'), ['token'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('article_shares', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_article_shares_token'))
        batch_op.drop_index(batch_op.f('ix_article_shares_owner_username'))
        batch_op.drop_index(batch_op.f('ix_article_shares_created_at'))
        batch_op.drop_index(batch_op.f('ix_article_shares_article_id'))

    op.drop_table('article_shares')
