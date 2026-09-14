"""add image_insights table (issue #69 图片理解波)

视觉模型对文章配图的结构化文字说明,一行 = 一份图片字节(主键 content_hash 与
media_assets 的内容去重单元同源)。无逐文章状态;失败行是带退避的负缓存。
见 docs/image-understanding-wave-plan.md §1.3。

Revision ID: df8f95fe0439
Revises: c4d8e2f6a1b3
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


# revision identifiers, used by Alembic.
revision: str = 'df8f95fe0439'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2f6a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 收养回放守卫:create_all() 出生的库已有本表(runtime bootstrap == metadata),
    # ensure_migrated 盖基线后回放到这里不得重复建表。
    if "image_insights" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('image_insights',
    sa.Column('content_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('relevant', sa.Boolean(), nullable=False),
    sa.Column('caption', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('details', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('ocr_text', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('prompt_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('fail_count', sa.Integer(), nullable=False),
    sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('next_attempt_at', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.CheckConstraint("status IN ('succeeded','failed')", name='ck_image_insights_status'),
    sa.PrimaryKeyConstraint('content_hash')
    )
    with op.batch_alter_table('image_insights', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_image_insights_prompt_version'), ['prompt_version'], unique=False)
        batch_op.create_index(batch_op.f('ix_image_insights_status'), ['status'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    # 本表自身可逆,但父版本 c4d8e2f6a1b3(v3.56 屏蔽行物理删除)是单向边界:
    # transaction_per_migration 下若先删表再撞父守卫,库会离开 head 却报错。故与链上
    # 其它单向边界之上的迁移同法——在任何 DDL 之前拒绝(tests/test_migrations 守卫)。
    raise RuntimeError(
        "image_insights sits above the one-way v3.56 boundary; restore the "
        "pre-upgrade database to downgrade"
    )
