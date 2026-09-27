"""rebuild articles FTS5 with Unicode normalization

Revision ID: 344ba9944fe9
Revises: b715a91c4e02
Create Date: 2026-09-22 16:02:45.626162

为解决搜索时 Unicode 特殊排版符号（如 U+2011 非破坏性连字符）无法命中的问题，
本迁移在 SQLite 下重建 FTS5 虚拟表与同步 Trigger，引入两端一致的 Unicode 归一化，
并将存量文章批量重新计算并回填至 FTS5 索引中。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '344ba9944fe9'
down_revision: Union[str, Sequence[str], None] = 'b715a91c4e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：重建带有 Unicode 归一化 Trigger 的 FTS 虚拟表并回填存量。"""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    from storage.fts import rebuild_fts_normalized
    rebuild_fts_normalized(bind)


def downgrade() -> None:
    # Like the parent revision, reject before any DDL above the one-way boundary.
    raise RuntimeError(
        "FTS normalization sits above a one-way boundary; restore the pre-upgrade database to downgrade"
    )
